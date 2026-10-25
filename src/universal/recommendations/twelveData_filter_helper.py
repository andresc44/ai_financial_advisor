import operator
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.ops.run_logging import debug
from src.config.parameters import params_dict

if TYPE_CHECKING:
    from filter_tickers import DataFetcher

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
    val: Any,
    metric_key: str,
    op_str: str,
    threshold: float | int,
    symbol: str,
) -> tuple[bool, Any]:
    """Evaluates a raw numerical value against a threshold operator."""
    comp_fn = OPERATORS.get(op_str)
    if not comp_fn:
        raise ValueError(f"Unsupported comparison operator: '{op_str}'")

    if val is None:
        return False, None

    passed = comp_fn(val, threshold)
    if not passed:
        debug(f"Alert: {symbol} has {metric_key} = {val} (Failed condition: '{op_str} {threshold}'). Skipping.")
        return False, val

    return True, val


def filter_twelvedata_tickers(
    fetcher: "DataFetcher",
    tickers: Optional[List[str]] = None,
    rules: Optional[List[tuple]] = None,
) -> List[str]:
    """Filters tickers using Twelve Data technical indicators in batch mode."""
    input_tickers = tickers if tickers is not None else fetcher.all_tickers
    if not input_tickers:
        debug("No tickers received for Twelve Data technical filtering. Skipping API calls.")
        return []

    filter_rules = rules if rules is not None else params_dict.get("TWELVEDATA_FILTERS", [])
    filtered_twelvedata_tickers = []

    # Collect indicators and query params safely
    needed_indicators = []
    for rule in filter_rules:
        indicator = rule[0]
        extra_params = rule[4] if len(rule) > 4 else {}
        needed_indicators.append((indicator, extra_params))

    # Batch API Call
    debug(f"\nExecuting batch Twelve Data API request for {len(input_tickers)} tickers...")
    batch_data = fetcher.fetch_twelvedata_batch_data(
        symbols=input_tickers, 
        indicators=needed_indicators
    )

    if not isinstance(batch_data, dict) or not batch_data:
        # Claude-authored (2026-10-16): no data (rate limit, used-up credits, outage) now keeps every ticker instead
        # of dropping them all, which left nothing for Claude to score. The cause is already a universal issue
        # (data_source_guard.py), and the completeness check flags the missing indicators.
        print("[WARNING] Batch Twelve Data response was invalid or empty. Keeping all tickers unfiltered by technicals.")
        debug(f"[DEBUG] Raw batch_data returned: {batch_data}")
        return list(input_tickers)

    debug(f"[DEBUG] Top-level symbols returned in batch_data: {list(batch_data.keys())}")

    # Evaluate each ticker against the rule set
    for symbol in input_tickers:
        # ALWAYS extract by symbol key regardless of ticker count
        symbol_data = batch_data.get(symbol, {})
        
        if not symbol_data:
            debug(f"[DEBUG] No data dict found for symbol '{symbol}'; kept unfiltered by technicals.")
            filtered_twelvedata_tickers.append(symbol)  # Claude-authored (2026-10-16): missing data isn't a failed check
            continue

        debug(f"\n[DEBUG] Indicator keys available for '{symbol}': {list(symbol_data.keys())}")
        
        passed_all = True
        evaluated_values = {}

        for rule in filter_rules:
            indicator = rule[0]
            interval = rule[1]
            op = rule[2]
            threshold = rule[3]
            extra_params = rule[4] if len(rule) > 4 else {}

            # Construct key matching fetcher (e.g., 'rsi' or 'rsi_9')
            param_suffix = f"_{extra_params.get('time_period')}" if "time_period" in extra_params else ""
            indicator_key = f"{indicator}{param_suffix}"

            indicator_payload = symbol_data.get(indicator_key, {})
            values_list = indicator_payload.get("values", [])
            
            if not values_list:
                # Claude-authored (2026-10-16): missing data (e.g. an error for this symbol in the batch) skips this
                # rule instead of dropping the ticker
                debug(f"Alert: Missing {indicator_key} data for {symbol}; rule skipped.")
                debug(f"[DEBUG] Full raw payload received for {symbol} -> {indicator_key}:")
                debug(f"       {indicator_payload}")
                continue

            latest_val = float(values_list[0].get(indicator, 0))
            metric_label = f"{indicator_key}_{interval}"

            passed, val = evaluate_filter(
                val=latest_val,
                metric_key=metric_label,
                op_str=op,
                threshold=threshold,
                symbol=symbol,
            )

            if not passed:
                passed_all = False
                break

            evaluated_values[metric_label] = val

        if passed_all:
            filtered_twelvedata_tickers.append(symbol)
            debug(f"✓ {symbol} passed Twelve Data technical filters: {evaluated_values}")

    debug(f"\nFinal pipeline tickers remaining: {filtered_twelvedata_tickers}")
    return filtered_twelvedata_tickers

def fetch_twelvedata_context_data(
    fetcher: "DataFetcher",
    tickers: Optional[List[str]] = None,
    rules: Optional[List[tuple]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Fetches non-filtered Twelve Data indicator context for a list of tickers in batch mode.
    
    Returns a dictionary mapping symbol -> { indicator_label: indicator_payload }
    """
    input_tickers = tickers if tickers is not None else fetcher.all_tickers
    if not input_tickers:
        return {}

    context_rules = rules if rules is not None else params_dict.get("TWELVEDATA_CONTEXT", [])
    if not context_rules:
        return {symbol: {} for symbol in input_tickers}

    # Group indicator requests by interval to execute efficient batch calls
    grouped_by_interval: Dict[str, List[tuple[str, dict]]] = {}
    for rule in context_rules:
        indicator = rule[0]
        interval = rule[1]
        extra_params = rule[2] if len(rule) > 2 else {}
        grouped_by_interval.setdefault(interval, []).append((indicator, extra_params))

    context_results: Dict[str, Dict[str, Any]] = {symbol: {} for symbol in input_tickers}

    # Fetch batch data per interval group
    for interval, indicator_list in grouped_by_interval.items():
        debug(f"Executing batch Twelve Data context fetch for {len(input_tickers)} tickers ({interval})...")
        batch_data = fetcher.fetch_twelvedata_batch_data(
            symbols=input_tickers,
            indicators=indicator_list,
            interval=interval
        )

        for symbol in input_tickers:
            symbol_payload = batch_data.get(symbol, {})
            if isinstance(symbol_payload, dict):
                for indicator_key, data in symbol_payload.items():
                    metric_label = f"{indicator_key}_{interval}"
                    context_results[symbol][metric_label] = data

    return context_results