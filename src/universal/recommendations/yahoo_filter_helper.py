import operator
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

# Set up project root path resolution dynamically
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict

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
        print(f"Alert: {symbol} has {metric_key} = {val} (Failed: condition '{op_str} {threshold}'). Skipping.")
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

    for symbol in input_tickers:
        print(f"Fetching Yahoo data for {symbol}...")
        yahoo_metrics = client.fetch_yahoo_data(symbol=symbol, fetch_news=False)

        if not isinstance(yahoo_metrics, dict):
            print(f"Alert: Yahoo data for '{symbol}' is not a dictionary. Skipping.")
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
            print(f"✓ {symbol} passed Yahoo filters: {evaluated_values}")

    return filtered_yahoo_tickers