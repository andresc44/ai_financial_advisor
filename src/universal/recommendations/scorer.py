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

# Claude-authored (2026-10-16): the parts of Claude's evaluation that go with a pick to the digest. Before, only the
# two totals were kept, so a pick never said why it was made.
LONG_TERM_SCORES = ("valuation_score", "financial_health_score", "growth_momentum_score", "competitive_moat_score")
SWING_SCORES = ("technical_setup_score", "risk_reward_ratio_score", "momentum_volume_score", "catalyst_sentiment_score")


def long_term_reasoning(scores: Dict[str, Any]) -> Dict[str, Any]:
    """Sentiment, thesis, key risk, and sub-scores from the long-term half of a ticker's evaluation."""
    section = (scores.get("sub_scores") or {}).get("long_term") or {}
    return {
        "sentiment": section.get("sentiment"),
        "thesis": section.get("thesis"),
        "key_risk": section.get("key_risk"),
        "sub_scores": {name: section.get(name) for name in LONG_TERM_SCORES},
    }


def swing_reasoning(ticker: str, scores: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sentiment, thesis, sub-scores, and trade levels from the swing half of a ticker's evaluation. The levels are
    only kept when they make sense together (0 < stop < entry < target); then risk_reward is the upside to the
    target per unit of downside to the stop, e.g. 2.5 for 2.5:1.
    """
    section = (scores.get("sub_scores") or {}).get("swing") or {}
    entry, stop, target = (section.get(key) for key in ("entry_price", "stop_loss", "price_target"))
    levels_ok = all(isinstance(v, (int, float)) for v in (entry, stop, target)) and 0 < stop < entry < target
    if not levels_ok:
        logger.warning(f"[{ticker}] Swing levels dropped (need 0 < stop < entry < target): "
                       f"entry={entry}, stop={stop}, target={target}")
    return {
        "sentiment": section.get("sentiment"),
        "thesis": section.get("thesis"),
        "entry_price": entry if levels_ok else None,
        "stop_loss": stop if levels_ok else None,
        "price_target": target if levels_ok else None,
        "risk_reward": round((target - entry) / (entry - stop), 1) if levels_ok else None,
        "sub_scores": {name: section.get(name) for name in SWING_SCORES},
    }


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
            "swingScore": scores.get("swingScore", 0.0),
            "model": scores.get("model"),  # Claude-authored (2026-10-16): the LLM that scored it
            **long_term_reasoning(scores),  # Claude-authored (2026-10-16)
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
            "longScore": scores.get("longScore", 0.0),
            "model": scores.get("model"),  # Claude-authored (2026-10-16): the LLM that scored it
            **swing_reasoning(ticker, scores),  # Claude-authored (2026-10-16)
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