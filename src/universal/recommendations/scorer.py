import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict
from src.universal.recommendations.LLM_recommendation import daily_atr, run_llm_evaluation
from src.universal.recommendations.sanitizer import horizon_payload

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


def _first(series: Any, field: str) -> Optional[float]:
    """The newest value of an indicator series (newest first), or None."""
    point = series[0] if isinstance(series, list) and series and isinstance(series[0], dict) else {}
    value = point.get(field)
    return float(value) if isinstance(value, (int, float)) else None


def too_far_along(context: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    Claude-authored (2026-10-18): why a swing candidate is already too far along its current run to be bought early,
    e.g. "up 4.0 days' ranges (20%) from its 10-day low", or None. Andres wants swing picks caught early in a new
    upward move, never after a jump; the model alone scored TRU, up 20% in 10 days, the best swing setup. Checks
    params_dict["SWING_EARLY_ENTRY"] against the daily bars and indicators the model saw. Missing data proves
    nothing, so it doesn't count against a stock.
    """
    rules = params_dict.get("SWING_EARLY_ENTRY") or {}
    context = context or {}
    bars = [bar for bar in context.get("daily_bars") or [] if isinstance(bar, dict)]
    indicators = context.get("technical_indicators") or {}
    price = bars[0].get("close") if bars else None
    if not isinstance(price, (int, float)):
        price = (context.get("price_action") or {}).get("current_price")
    price = float(price) if isinstance(price, (int, float)) and price > 0 else None
    reasons = []

    lookback = int(rules.get("LOOKBACK_DAYS", 10))
    lows = [bar["low"] for bar in bars[:lookback] if isinstance(bar.get("low"), (int, float))]
    atr = daily_atr(bars)
    if price and atr and lows and "MAX_RUN_ATR" in rules:
        run = (price - min(lows)) / atr
        if run > rules["MAX_RUN_ATR"]:
            reasons.append(f"up {run:.1f} days' ranges ({(price / min(lows) - 1) * 100:.0f}%) from its {lookback}-day low")
    rsi = _first(indicators.get("rsi_14_1day"), "rsi")
    if rsi is not None and "MAX_RSI" in rules and rsi > rules["MAX_RSI"]:
        reasons.append(f"RSI {rsi:.0f}")
    upper = _first(indicators.get("bbands_20_1day"), "upper_band")
    if price and upper and price > upper:
        reasons.append("above its upper Bollinger Band")
    return "; ".join(reasons) or None


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


def with_non_tech_picks(
    picked: List[Tuple[str, Dict[str, Any]]], n: int, candidates: List[Tuple[str, Dict[str, Any]]],
    sector_of: Callable[[str], str],
) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Claude-authored (2026-10-22, Andres's rule): long-term picks with at least params_dict["LONG_TERM_MIN_NON_TECH_PICKS"]
    stocks outside TECH_SECTORS. While there are fewer, the best non-tech candidate from a sector not already picked (so
    the sector rule still holds) joins the picks: added when there are fewer than `n`, otherwise in place of the
    lowest-scoring tech pick. `candidates` are best first and may score below the usual minimum (from
    LONG_TERM_NON_TECH_MIN_SCORE). Only applies when something was picked; with no such candidate the picks stay as they
    are, and the log says so.
    """
    minimum = int(params_dict.get("LONG_TERM_MIN_NON_TECH_PICKS", 0) or 0)
    tech = set(params_dict.get("TECH_SECTORS") or [])
    is_tech = lambda ticker: sector_of(ticker) in tech  # noqa: E731
    result = list(picked)
    while result and sum(not is_tech(t) for t, _ in result) < minimum:
        taken = {sector_of(t) for t, _ in result}
        joining = next((item for item in candidates if item[0] not in {t for t, _ in result} and not is_tech(item[0])
                        and sector_of(item[0]) not in taken), None)
        tech_picks = [item for item in result if is_tech(item[0])]
        if joining is None or (len(result) >= n and not tech_picks):
            logger.info(f"Long-term picks {[t for t, _ in result]}: no non-tech candidate scored high enough, so they stay "
                        f"as they are (LONG_TERM_MIN_NON_TECH_PICKS {minimum}).")
            break
        if len(result) < n:
            logger.info(f"[{joining[0]}] Added to the long-term picks ({sector_of(joining[0])}): at least {minimum} "
                        f"pick(s) must be outside {sorted(tech)} (LONG_TERM_MIN_NON_TECH_PICKS).")
            result.append(joining)
        else:
            leaving = tech_picks[-1]  # The lowest-scoring tech pick (picks are best first)
            logger.info(f"[{leaving[0]}] Left out of the long-term picks for {joining[0]} ({sector_of(joining[0])}): at "
                        f"least {minimum} pick(s) must be outside {sorted(tech)} (LONG_TERM_MIN_NON_TECH_PICKS).")
            result[result.index(leaving)] = joining
    rank = {ticker: position for position, (ticker, _) in enumerate(candidates)}
    return sorted(result, key=lambda item: rank.get(item[0], len(rank)))


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
        # Claude-authored (2026-10-20): the swing screen's setup ("pullback" / "new_uptrend") and what its plan's stop
        # and target sit at
        "setup": section.get("setup"),
        "stop_basis": section.get("stop_basis"),
        "target_basis": section.get("target_basis"),
        "sub_scores": {name: section.get(name) for name in SWING_SCORES},
    }


def rescore_borderline(
    scores: Dict[str, Dict[str, Any]],
    key: str,
    picks: int,
    min_score: float,
    rescore: Callable[[List[str]], Dict[str, Dict[str, Any]]],
    label: str,
) -> Dict[str, Dict[str, Any]]:
    """
    Claude-authored (2026-10-22, at Andres's request): scores that could go either way, scored again
    (params_dict["IDEA_RESCORE"]). Unless the list already has `picks` candidates above `min_score` + ABOVE_MIN, each
    candidate whose `key` score is from `min_score` - BELOW_MIN up to `min_score` + ABOVE_MIN is scored EXTRA_RUNS more
    times by
    `rescore(tickers)` ({ticker: scores}, like run_llm_evaluation), and keeps the run with the middle score (the
    lower of the two middle ones when a re-score failed), so its sub-scores and thesis match the score it shows.
    """
    settings = params_dict.get("IDEA_RESCORE") or {}
    # 2026-10-22: the band is relative to each list's minimum (swing's went to 75); it was up to an absolute 85
    clear = min_score + settings.get("ABOVE_MIN", 5.0)
    below, runs = settings.get("BELOW_MIN", 5.0), settings.get("EXTRA_RUNS", 2)
    if not scores or runs <= 0:
        return scores
    clear_count = sum(1 for s in scores.values() if (s.get(key) or 0.0) > clear)
    if clear_count >= picks:
        logger.info(f"{clear_count} {label} candidates score above {clear:g} (the list shows {picks}); no re-scoring.")
        return scores
    borderline = sorted(t for t, s in scores.items() if min_score - below <= (s.get(key) or 0.0) <= clear)
    if not borderline:
        return scores
    logger.info(f"Re-scoring {len(borderline)} borderline {label} candidates {runs} more times: {borderline}")
    extra = [rescore(borderline) for _ in range(runs)]
    result = dict(scores)
    for ticker in borderline:
        draws = sorted([scores[ticker]] + [run[ticker] for run in extra if ticker in run], key=lambda s: s.get(key) or 0.0)
        middle = draws[(len(draws) - 1) // 2]
        result[ticker] = middle
        logger.info(f"[{ticker}] {label} scores {[round(s.get(key) or 0.0, 1) for s in draws]} -> "
                    f"{(middle.get(key) or 0.0):.1f}")
    return result


def select_top_candidates(
    sanitized_inputs: Dict[str, Dict[str, Any]],
    long_n: int = 2,
    swing_n: int = 5,
    min_long_score: Optional[float] = None,
    min_swing_score: Optional[float] = None,
    config: Optional[Dict[str, Any]] = None,
    issues: Optional[List[Dict[str, Any]]] = None,
    stats: Optional[Dict[str, int]] = None,
    long_tickers: Optional[List[str]] = None,
    swing_setups: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Evaluates candidate tickers and selects:
    1. Top long_n tickers with longScore >= min_long_score.
    2. Top swing_n tickers with swingScore >= min_swing_score.
    2026-10-17: within each list, the sectors follow params_dict["PICK_SECTOR_RULES"] (pick_with_sector_rule).
    2026-10-20 (Claude-authored, at Andres's request): the two lists come from two pipelines side by side. The
    long-term candidates (`long_tickers`, by default every input) get the long-term call only, and the swing
    candidates (the keys of `swing_setups`, from swing_screen.screen: {"setup", "facts", "plan"}) the swing call
    only, each with just the data its rubric uses (sanitizer.horizon_payload). A stock can be in both lists: the
    long-term picks are no longer left out of the swing picks. A pick shows its other score only when it was scored
    on both.

    Args:
        sanitized_inputs: Dict mapping ticker symbols to their full sanitized market payloads (every candidate).
        long_n: Maximum number of top candidates to select for long-term holding.
        swing_n: Maximum number of top candidates to select for swing trading.
        min_long_score: Minimum longScore cutoff. Defaults to params_dict['MIN_LONG_SCORE'].
        min_swing_score: Minimum swingScore cutoff. Defaults to params_dict['MIN_SWING_SCORE'].
        config: Optional custom configuration dictionary for the LLM call.
        stats: Claude-authored (2026-10-17), filled in when given: "sent" / "scored" (long-term candidates sent to the
            LLM / valid evaluations), "swing_sent" / "swing_scored" (2026-10-20: the same for swing), "long_qualified" /
            "swing_qualified" (at or above each minimum score, before the picks are capped), and "swing_early"
            (2026-10-18: swing-qualified and not too_far_along).
        long_tickers / swing_setups: Claude-authored (2026-10-20), as above.

    Returns:
        Dict with keys 'top_long_term' and 'top_swing', containing lists of selected ticker objects.
    """
    stats = stats if stats is not None else {}
    sanitized_inputs = sanitized_inputs or {}
    long_tickers = [t for t in (long_tickers if long_tickers is not None else sanitized_inputs) if t in sanitized_inputs]
    swing_setups = {t: setup for t, setup in (swing_setups or {}).items() if t in sanitized_inputs}
    stats.update(sent=len(long_tickers), scored=0, long_qualified=0, swing_sent=len(swing_setups), swing_scored=0,
                 swing_qualified=0, swing_early=0)
    if not long_tickers and not swing_setups:
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

    # 1. Run each horizon's LLM evaluation over its own candidates
    issues_before = len(issues) if issues is not None else 0
    long_scores: Dict[str, Dict[str, Any]] = run_llm_evaluation(
        sanitized_inputs={t: horizon_payload(sanitized_inputs[t], "long_term") for t in long_tickers},
        config=config,
        issues=issues,  # Claude-authored (2026-10-16): collects a shared Anthropic key failure
        horizon="long_term",
    ) if long_tickers else {}
    key_failed = issues is not None and len(issues) > issues_before  # The same key would fail the swing calls too
    swing_scores: Dict[str, Dict[str, Any]] = run_llm_evaluation(
        sanitized_inputs={t: horizon_payload(sanitized_inputs[t], "swing", setup) for t, setup in swing_setups.items()},
        config=config,
        issues=issues,
        horizon="swing",
    ) if swing_setups and not key_failed else {}

    # Claude-authored (2026-10-22): scores that could go either way are scored twice more, keeping the middle one
    long_scores = rescore_borderline(
        long_scores, "longScore", long_n, min_long_score,
        lambda tickers: run_llm_evaluation(
            sanitized_inputs={t: horizon_payload(sanitized_inputs[t], "long_term") for t in tickers},
            config=config, horizon="long_term"),
        "long-term")
    swing_scores = rescore_borderline(
        swing_scores, "swingScore", swing_n, min_swing_score,
        lambda tickers: run_llm_evaluation(
            sanitized_inputs={t: horizon_payload(sanitized_inputs[t], "swing", swing_setups[t]) for t in tickers},
            config=config, horizon="swing"),
        "swing")

    stats["scored"] = len(long_scores)
    stats["swing_scored"] = len(swing_scores)
    if not long_scores and not swing_scores:
        logger.error("LLM evaluation returned empty results.")
        return {"top_long_term": [], "top_swing": []}

    # 2. Filter candidates meeting the min_long_score threshold
    eligible_long = []

    for ticker, scores in long_scores.items():
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
    stats["swing_qualified"] = sum(1 for s in swing_scores.values() if s.get("swingScore", 0.0) >= min_swing_score)
    # Claude-authored (2026-10-18): a swing pick must still be early in its move (too_far_along)
    too_far = {ticker: too_far_along(sanitized_inputs.get(ticker)) for ticker, s in swing_scores.items()
               if s.get("swingScore", 0.0) >= min_swing_score}
    stats["swing_early"] = sum(1 for reason in too_far.values() if reason is None)
    # Claude-authored (2026-10-17): the sector rule decides among the stocks that cleared the bar
    sector_rules = params_dict.get("PICK_SECTOR_RULES") or {}
    sector_of = lambda ticker: candidate_sector(ticker, sanitized_inputs)  # noqa: E731
    top_long_tuples = pick_with_sector_rule(
        sorted_by_long, long_n, sector_of, sector_rules.get("LONG_TERM", "any"), "long-term")
    # 2026-10-22: at least one pick outside tech, which may score from LONG_TERM_NON_TECH_MIN_SCORE
    non_tech_floor = params_dict.get("LONG_TERM_NON_TECH_MIN_SCORE", min_long_score)
    non_tech_pool = sorted(((t, s) for t, s in long_scores.items() if s.get("longScore", 0.0) >= min(non_tech_floor, min_long_score)),
                           key=lambda item: item[1].get("longScore", 0.0), reverse=True)
    top_long_tuples = with_non_tech_picks(top_long_tuples, long_n, non_tech_pool, sector_of)

    def other_score(scores_map: Dict[str, Dict[str, Any]], ticker: str, key: str) -> Optional[float]:
        return (scores_map.get(ticker) or {}).get(key)

    top_long_term = [
        {
            "ticker": ticker,
            "longScore": scores.get("longScore", 0.0),
            "swingScore": other_score(swing_scores, ticker, "swingScore"),  # 2026-10-20: None unless swing-scored
            "model": scores.get("model"),  # Claude-authored (2026-10-16): the LLM that scored it
            **long_term_reasoning(scores),  # Claude-authored (2026-10-16)
        }
        for ticker, scores in top_long_tuples
    ]

    # 3. Filter the swing candidates meeting the min_swing_score threshold
    eligible_swing = []

    for ticker, scores in swing_scores.items():
        swing_score = scores.get("swingScore", 0.0)
        if swing_score >= min_swing_score and too_far.get(ticker):
            logger.info(f"[{ticker}] Swing score {swing_score:.1f}, but left out: already too far along its run "
                        f"({too_far[ticker]}).")
        elif swing_score >= min_swing_score:
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
            "longScore": other_score(long_scores, ticker, "longScore"),  # 2026-10-20: None unless long-term-scored
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
