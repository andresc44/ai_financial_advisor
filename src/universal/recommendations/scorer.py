import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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

# Claude-authored (2026-10-17): params_dict["PICK_SECTOR_RULES"] values -> whether one more pick from a sector already
# in the list is allowed, given (distinct sectors, picks that repeat a sector) so far
SECTOR_RULES: Dict[str, Callable[[int, int], bool]] = {
    "all_different": lambda distinct, repeats: False,
    # After one more repeat there are distinct + repeats + 1 picks, and distinct must stay more than half of them
    "majority_different": lambda distinct, repeats: repeats + 1 < distinct,
    "any": lambda distinct, repeats: True,
}


def candidate_sector(ticker: str, sanitized_inputs: Dict[str, Dict[str, Any]]) -> str:
    """Yahoo's sector for a candidate. A stock without one counts as its own sector, so missing data never blocks it."""
    sector = ((sanitized_inputs.get(ticker) or {}).get("profile") or {}).get("sector")
    return sector.strip() if isinstance(sector, str) and sector.strip() else f"(no sector: {ticker})"


def pick_with_sector_rule(
    ranked: List[Tuple[str, Dict[str, Any]]], n: int, sector_of: Callable[[str], str], rule: str, label: str
) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Claude-authored (2026-10-17): up to n of `ranked` ((ticker, scores), best first) whose sectors follow `rule`
    (SECTOR_RULES). Highest scores win wherever the rule allows: a stock from a sector already in the list waits,
    and joins as soon as enough other sectors have joined to allow it ("majority_different"). Returns the picks
    best first; stocks left out are logged.
    """
    if rule not in SECTOR_RULES:
        logger.warning(f"Unknown PICK_SECTOR_RULES value {rule!r} for {label} picks; using 'any'.")
        rule = "any"
    allow_repeat = SECTOR_RULES[rule]
    picked: List[Tuple[str, Dict[str, Any]]] = []
    waiting: List[Tuple[str, Dict[str, Any]]] = []
    sectors: Counter = Counter()

    def room_for_repeat() -> bool:
        return len(picked) < n and allow_repeat(len(sectors), len(picked) - len(sectors))

    for item in ranked:
        if len(picked) >= n:
            break
        sector = sector_of(item[0])
        if sectors[sector] == 0:
            picked.append(item)
            sectors[sector] += 1
            while waiting and room_for_repeat():  # A new sector can make room for better stocks that waited
                earlier = waiting.pop(0)
                picked.append(earlier)
                sectors[sector_of(earlier[0])] += 1
        elif room_for_repeat():
            picked.append(item)
            sectors[sector] += 1
        else:
            waiting.append(item)
    for ticker, _ in waiting:
        logger.info(f"[{ticker}] Left out of the {label} picks: another pick is already from {sector_of(ticker)} "
                    f"(PICK_SECTOR_RULES {rule!r}).")
    rank = {ticker: position for position, (ticker, _) in enumerate(ranked)}
    return sorted(picked, key=lambda item: rank[item[0]])


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
    stats: Optional[Dict[str, int]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Evaluates candidate tickers and selects:
    1. Top long_n tickers with longScore >= min_long_score.
    2. Top swing_n remaining tickers with swingScore >= min_swing_score (excluding longScore picks).
    2026-10-17: within each list, the sectors follow params_dict["PICK_SECTOR_RULES"] (pick_with_sector_rule).

    Args:
        sanitized_inputs: Dict mapping ticker symbols to their sanitized market payloads.
        long_n: Maximum number of top candidates to select for long-term holding.
        swing_n: Maximum number of top candidates to select for swing trading.
        min_long_score: Minimum longScore cutoff. Defaults to params_dict['MIN_LONG_SCORE'].
        min_swing_score: Minimum swingScore cutoff. Defaults to params_dict['MIN_SWING_SCORE'].
        config: Optional custom configuration dictionary for the LLM call.
        stats: Claude-authored (2026-10-17), filled in when given: "sent" (candidates sent to the LLM), "scored"
            (evaluations that came back valid), and "long_qualified" / "swing_qualified" (scored tickers at or above
            each minimum score, before the picks are capped at long_n / swing_n; a ticker can clear both).

    Returns:
        Dict with keys 'top_long_term' and 'top_swing', containing lists of selected ticker objects.
    """
    stats = stats if stats is not None else {}
    stats.update(sent=len(sanitized_inputs or {}), scored=0, long_qualified=0, swing_qualified=0)
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

    stats["scored"] = len(scores_map)
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
    
    stats["long_qualified"] = len(eligible_long)
    stats["swing_qualified"] = sum(1 for s in scores_map.values() if s.get("swingScore", 0.0) >= min_swing_score)
    # Claude-authored (2026-10-17): the sector rule decides among the stocks that cleared the bar
    sector_rules = params_dict.get("PICK_SECTOR_RULES") or {}
    sector_of = lambda ticker: candidate_sector(ticker, sanitized_inputs)  # noqa: E731
    top_long_tuples = pick_with_sector_rule(
        sorted_by_long, long_n, sector_of, sector_rules.get("LONG_TERM", "any"), "long-term")
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
    
    top_swing_tuples = pick_with_sector_rule(sorted_by_swing, swing_n, sector_of, sector_rules.get("SWING", "any"), "swing")

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