# File to pick which long-term candidates go to the LLM: the ones that already look best against its rubric.
"""
Authorship: Written by Claude (Claude Sonnet 5.5 via Claude Code) on 2026-10-25, at Andres's request.

Why: the quality screens (YAHOO_FILTERS, FINNHUB_FILTERS, TWELVEDATA_FILTERS) let ~100 stocks through on a typical
day, and every one went to the LLM (99 of 99 on 2026-10-25, for 7 that cleared MIN_LONG_SCORE), so scoring cost about
$0.18 of that run's $0.24. Andres asked for 30-40.

How: each candidate gets up to 100 "screen points" from the figures the rubric (LLM_rec_instr.LONG_TERM_SYSTEM_PROMPT)
says earn its top band, four parts of 25 like its four sub-scores, and the best LONG_TERM_SCREEN["MAX_CANDIDATES"] go
on. A figure that is missing counts as the middle of its range, so a gap neither helps nor sinks a stock.
- valuation: PEG at or under 1 is full marks, 3 or over none; forward P/E 10 or under is full, 35 or over none
- financial health: debt-to-equity 30% or under is full, 175% or over none; current ratio 1.5+ is full, under 0.8
  none; positive free cash flow earns a third of the part
- growth momentum: revenue and earnings growth 30%+ is full, 5% or under none; operating margin 25%+ is full, 5% none
- moat (no figure measures it directly, so what usually goes with one): gross margin 60%+ full, 20% none; return on
  equity 25%+ full, 8% none; operating margin as above
"""
from typing import Any, Dict, List, Optional

from src.config.parameters import params_dict

MIDDLE = 0.5


def _ramp(value: Any, worst: float, best: float) -> Optional[float]:
    """0 at `worst` up to 1 at `best` (either direction); None when the figure is missing."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value != value:
        return None
    return min(1.0, max(0.0, (value - worst) / (best - worst)))


def _part(*scores: Optional[float]) -> float:
    """The average of the figures that exist, as a share of the part's 25 points; the middle when none do."""
    known = [s for s in scores if s is not None]
    return 25 * (sum(known) / len(known) if known else MIDDLE)


def screen_points(sanitized: Dict[str, Any]) -> float:
    """Up to 100 points for how much of the long-term rubric's top bands the stock's figures already show."""
    f = sanitized.get("valuation_and_fundamentals") or {}
    cash_flow = f.get("free_cash_flow")
    cash = None if not isinstance(cash_flow, (int, float)) else (1.0 if cash_flow > 0 else 0.0)
    health = [_ramp(f.get("debt_to_equity"), 175, 30), _ramp(f.get("current_ratio"), 0.8, 1.5)]
    health_known = [s for s in health if s is not None]
    health_part = _part(*(health_known + ([cash] if cash is not None else [])))
    return round(
        _part(_ramp(f.get("peg_ratio"), 3, 1), _ramp(f.get("pe_forward"), 35, 10))
        + health_part
        + _part(_ramp(f.get("revenue_growth_yoy"), 0.05, 0.30), _ramp(f.get("earnings_growth_yoy"), 0.05, 0.30),
                _ramp(f.get("operating_margin"), 0.05, 0.25))
        + _part(_ramp(f.get("gross_margin"), 0.20, 0.60), _ramp(f.get("return_on_equity"), 0.08, 0.25),
                _ramp(f.get("operating_margin"), 0.05, 0.25)), 1)


def best_long_term_candidates(tickers: List[str], sanitized_inputs: Dict[str, Dict[str, Any]]) -> List[str]:
    """
    The best LONG_TERM_SCREEN["MAX_CANDIDATES"] of `tickers` by screen_points (ties in alphabetical order, so a run
    repeats); all of them when there are fewer, or the setting is 0.
    """
    limit = int((params_dict.get("LONG_TERM_SCREEN") or {}).get("MAX_CANDIDATES", 0) or 0)
    if limit <= 0 or len(tickers) <= limit:
        return list(tickers)
    ranked = sorted(tickers, key=lambda t: (-screen_points(sanitized_inputs.get(t) or {}), t))
    return ranked[:limit]
