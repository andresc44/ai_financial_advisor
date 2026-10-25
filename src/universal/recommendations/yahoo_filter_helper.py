import operator
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

# Set up project root path resolution dynamically
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.ops.run_logging import debug
from src.config.parameters import params_dict

if TYPE_CHECKING:
    from filter_tickers import DataFetcher

# Map comparison string operators to Python operator functions
OPERATORS: Dict[str, Callable[[Any, Any], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "=": operator.eq,
    "!=": operator.ne,
}


def evaluate_filter(
    metrics_data: dict,
    metric_key: str,
    op_str: str,
    threshold: float | int,
    symbol: str,
    get_metric_fn: Callable,
    default_val: Any = None,
) -> tuple[bool, Any]:
    """Retrieves a metric and evaluates it against a threshold using a comparison operator.

    Returns (passed_filter: bool, metric_value: Any)
    """
    comp_fn = OPERATORS.get(op_str)
    if not comp_fn:
        raise ValueError(f"Unsupported comparison operator: '{op_str}'")

    val = get_metric_fn(metrics_data, metric_key, symbol, default_val)
    if val is None:
        return False, None

    # Evaluate comparison condition
    passed = comp_fn(val, threshold)
    if not passed:
        debug(f"Alert: {symbol} has {metric_key} = {val} (Failed: condition '{op_str} {threshold}'). Skipping.")
        return False, val

    return True, val


def filter_yahoo_tickers(
    client: "DataFetcher", 
    tickers: Optional[List[str]] = None,
    rules: Optional[List[tuple]] = None,
) -> List[str]:
    """Filters tickers based on configurable Yahoo parameters defined in params_dict."""
    input_tickers = tickers if tickers is not None else client.all_tickers
    
    # Use supplied rules or default to YAHOO_FILTERS from params_dict
    filter_rules = rules if rules is not None else params_dict.get("YAHOO_FILTERS", [])
    filtered_yahoo_tickers = []

    # Access private/public metric retriever method dynamically
    get_metric = getattr(
        client,
        f"_{client.__class__.__name__}__get_metric_with_alert",
        getattr(client, "_get_metric_with_alert", None) or getattr(client, "get_metric_with_alert", None)
    )

    # Claude-authored (2026-10-16): fetched YAHOO_FETCH_CONCURRENCY at a time (each takes ~2 s, so a few hundred
    # tickers one by one took minutes), then checked in order. fetch_yahoo_data keeps each result, so the survivors
    # aren't fetched again when their full data is gathered.
    def _fetch(symbol: str) -> Any:
        try:
            return client.fetch_yahoo_data(symbol=symbol, fetch_news=False)
        except Exception as e:  # fetch_yahoo_data already catches its own errors; this is only a safety net
            return {"error": str(e)}

    workers = max(1, int(params_dict.get("YAHOO_FETCH_CONCURRENCY", 4)))
    debug(f"Fetching Yahoo data for {len(input_tickers)} tickers, {workers} at a time...")
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="yahoo") as pool:
        fetched = dict(zip(input_tickers, pool.map(_fetch, input_tickers)))
    # The first parallel requests of a run can race for Yahoo's session token and get a 401 "Invalid Crumb" (ALB was
    # lost that way on 2026-10-16), so each failed fetch is retried once, one at a time
    failed = [s for s, data in fetched.items() if not isinstance(data, dict) or "error" in data]
    for symbol in failed:
        fetched[symbol] = _fetch(symbol)
    if failed:
        still = sum(1 for s in failed if not isinstance(fetched[s], dict) or "error" in fetched[s])
        print(f"Retried {len(failed)} failed Yahoo fetches one at a time; {still} still failed.")

    for symbol in input_tickers:
        yahoo_metrics = fetched[symbol]

        if not isinstance(yahoo_metrics, dict):
            debug(f"Alert: Yahoo data for '{symbol}' is not a dictionary. Skipping.")
            continue

        passed_all = True
        evaluated_values = {}

        # Loop through rules dynamically retrieved from params_dict["YAHOO_FILTERS"]
        for key, op, threshold in filter_rules:
            passed, val = evaluate_filter(
                metrics_data=yahoo_metrics,
                metric_key=key,
                op_str=op,
                threshold=threshold,
                symbol=symbol,
                get_metric_fn=get_metric,
            )
            if not passed:
                passed_all = False
                break
            evaluated_values[key] = val

        if passed_all:
            filtered_yahoo_tickers.append(symbol)
            debug(f"✓ {symbol} passed Yahoo filters: {evaluated_values}")

    return one_listing_per_company(filtered_yahoo_tickers, fetched)


def one_listing_per_company(symbols: List[str], metrics: Dict[str, Any]) -> List[str]:
    """
    Claude-authored (2026-10-16): keeps the most traded listing of each company (Yahoo longName), in order. Share
    classes like GOOG / GOOGL are one business: both reaching Claude would double its cost for that company and could
    fill two pick slots with it.
    """
    best: Dict[str, tuple[str, float]] = {}
    for symbol in symbols:
        data = metrics.get(symbol) if isinstance(metrics.get(symbol), dict) else {}
        company = str(data.get("longName") or data.get("shortName") or symbol).strip().lower()
        volume = data.get("averageVolume") or 0
        if company not in best or volume > best[company][1]:
            best[company] = (symbol, volume)
    kept = {symbol for symbol, _ in best.values()}
    for symbol in symbols:
        if symbol not in kept:
            debug(f"Alert: {symbol} is another listing of a company already kept. Skipping.")
    return [symbol for symbol in symbols if symbol in kept]