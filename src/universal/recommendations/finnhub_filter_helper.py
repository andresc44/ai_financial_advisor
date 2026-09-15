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
    """Retrieves a metric and evaluates it against a threshold using a comparison operator."""
    comp_fn = OPERATORS.get(op_str)
    if not comp_fn:
        raise ValueError(f"Unsupported comparison operator: '{op_str}'")

    val = get_metric_fn(metrics_data, metric_key, symbol, default_val)
    if val is None:
        return False, None

    # Evaluate comparison condition
    passed = comp_fn(val, threshold)
    if not passed:
        print(f"Alert: {symbol} has {metric_key} = {val} (Failed condition: '{op_str} {threshold}'). Skipping.")
        return False, val

    return True, val


def filter_finnhub_tickers(
    fetcher: "DataFetcher", 
    tickers: Optional[List[str]] = None,
    rules: Optional[List[tuple]] = None,
) -> List[str]:
    """Filters tickers based on configurable Finnhub parameters defined in params_dict."""
    input_tickers = tickers if tickers is not None else fetcher.all_tickers
    
    # Default to FINNHUB_FILTERS from params_dict
    filter_rules = rules if rules is not None else params_dict.get("FINNHUB_FILTERS", [])
    filtered_finnhub_tickers = []

    # Safely retrieve private __get_metric_with_alert or fallback
    get_metric = getattr(
        fetcher, 
        f"_{fetcher.__class__.__name__}__get_metric_with_alert", 
        getattr(fetcher, "_get_metric_with_alert", None) or getattr(fetcher, "get_metric_with_alert", None)
    )

    for symbol in input_tickers:
        print(f"Fetching Finnhub data for {symbol}...")
        finnhub_data = fetcher.fetch_finnhub_data(symbol, fetch_news=False)
        
        if not isinstance(finnhub_data, dict):
            print(f"Alert: Finnhub data for '{symbol}' is not a dictionary. Skipping.")
            continue

        passed_all = True
        evaluated_values = {}

        # Loop through rules dynamically: (category, key, op, threshold)
        for category, key, op, threshold in filter_rules:
            # Extract target payload dict safely (e.g., 'profile', 'quote', 'basic_financials')
            payload_section = finnhub_data.get(category, {})

            # Set a default value for peTTM if key check demands it
            default_val = float("inf") if key == "peTTM" else None

            passed, val = evaluate_filter(
                metrics_data=payload_section,
                metric_key=key,
                op_str=op,
                threshold=threshold,
                symbol=symbol,
                get_metric_fn=get_metric,
                default_val=default_val,
            )
            
            if not passed:
                passed_all = False
                break
                
            evaluated_values[key] = val

        if passed_all:
            filtered_finnhub_tickers.append(symbol)
            print(f"✓ {symbol} passed Finnhub filters: {evaluated_values}")

    print(f"Filtered final tickers: {filtered_finnhub_tickers}")
    return filtered_finnhub_tickers