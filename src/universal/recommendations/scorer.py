import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict
from src.universal.recommendations.LLM_recommendation import run_llm_evaluation

logger = logging.getLogger("Finnbot.CandidateSelector")


def select_top_candidates(
    sanitized_inputs: Dict[str, Dict[str, Any]], 
    long_n: int = 2,
    swing_n: int = 5,
    min_long_score: Optional[float] = None,
    min_swing_score: Optional[float] = None,
    config: Optional[Dict[str, Any]] = None,
    issues: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Evaluates candidate tickers and selects:
    1. Top long_n tickers with longScore >= min_long_score.
    2. Top swing_n remaining tickers with swingScore >= min_swing_score (excluding longScore picks).

    Args:
        sanitized_inputs: Dict mapping ticker symbols to their sanitized market payloads.
        long_n: Maximum number of top candidates to select for long-term holding.
        swing_n: Maximum number of top candidates to select for swing trading.
        min_long_score: Minimum longScore cutoff. Defaults to params_dict['MIN_LONG_SCORE'].
        min_swing_score: Minimum swingScore cutoff. Defaults to params_dict['MIN_SWING_SCORE'].
        config: Optional custom configuration dictionary for the LLM call.

    Returns:
        Dict with keys 'top_long_term' and 'top_swing', containing lists of selected ticker objects.
    """
    if not sanitized_inputs:
        logger.warning("No sanitized inputs provided for selection.")
        return {"top_long_term": [], "top_swing": []}

    # Fetch thresholds from params_dict if not explicitly passed as arguments
    if min_long_score is None:
        min_long_score = params_dict.get("MIN_LONG_SCORE", 60.0)
    if min_swing_score is None:
        min_swing_score = params_dict.get("MIN_SWING_SCORE", 60.0)

    # Standardize non-negative bounds for N
    long_n = max(0, long_n)
    swing_n = max(0, swing_n)

    # 1. Run LLM evaluation pipeline across all tickers
    scores_map: Dict[str, Dict[str, float]] = run_llm_evaluation(
        sanitized_inputs=sanitized_inputs, 
        config=config,
        issues=issues,  # Claude-authored (2026-10-16): collects a shared Anthropic key failure
    )

    if not scores_map:
        logger.error("LLM evaluation returned empty results.")
        return {"top_long_term": [], "top_swing": []}

    # 2. Filter candidates meeting the min_long_score threshold
    eligible_long = []

    for ticker, scores in scores_map.items():
        long_score = scores.get("longScore", 0.0)
        if long_score >= min_long_score:
            eligible_long.append((ticker, scores))
        else:
            logger.info(
                f"[{ticker}] Long-Term score ({long_score:.1f}) did not meet "
                f"minimum threshold ({min_long_score:.1f}). Skipping."
            )

    sorted_by_long = sorted(
        eligible_long,
        key=lambda item: item[1].get("longScore", 0.0),
        reverse=True
    )
    
    top_long_tuples = sorted_by_long[:long_n]
    selected_long_tickers = {ticker for ticker, _ in top_long_tuples}

    top_long_term = [
        {
            "ticker": ticker,
            "longScore": scores.get("longScore", 0.0),
            "swingScore": scores.get("swingScore", 0.0)
        }
        for ticker, scores in top_long_tuples
    ]

    # 3. Filter remaining candidates meeting the min_swing_score threshold
    eligible_swing = []

    for ticker, scores in scores_map.items():
        if ticker in selected_long_tickers:
            logger.info(
                f"[{ticker}] Excluded from Swing evaluation: Already selected in Long-Term portfolio."
            )
            continue

        swing_score = scores.get("swingScore", 0.0)
        if swing_score >= min_swing_score:
            eligible_swing.append((ticker, scores))
        else:
            logger.info(
                f"[{ticker}] Swing score ({swing_score:.1f}) did not meet "
                f"minimum threshold ({min_swing_score:.1f}). Skipping."
            )

    sorted_by_swing = sorted(
        eligible_swing,
        key=lambda item: item[1].get("swingScore", 0.0),
        reverse=True
    )
    
    top_swing_tuples = sorted_by_swing[:swing_n]

    top_swing = [
        {
            "ticker": ticker,
            "swingScore": scores.get("swingScore", 0.0),
            "longScore": scores.get("longScore", 0.0)
        }
        for ticker, scores in top_swing_tuples
    ]

    logger.info(
        f"Selected top {len(top_long_term)} Long-Term candidates "
        f"(requested {long_n}, min_score {min_long_score}): {[x['ticker'] for x in top_long_term]}"
    )
    logger.info(
        f"Selected top {len(top_swing)} Swing candidates "
        f"(requested {swing_n}, min_score {min_swing_score}): {[x['ticker'] for x in top_swing]}"
    )

    return {
        "top_long_term": top_long_term,
        "top_swing": top_swing
    }