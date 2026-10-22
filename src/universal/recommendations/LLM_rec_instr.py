"""
LLM Recommendation Instructions & Schema Descriptions.
Centralized repository for prompts, field descriptions, and system instructions
used in standardized stock evaluation.

2026-10-18 (Claude-authored, at Andres's request): the swing rubric now rewards a stock that is cheap now and likely
to bounce (a pullback to support in an intact trend, with the selling running out), not one that has already run
for weeks. Its bands are one coherent setup instead of three that rarely co-occur (breakout, at support, bouncing
from oversold), and risk/reward is scored from the model's own entry, stop, and target.

2026-10-20 (Claude-authored, at Andres's request): the theses are at most 2 short plain sentences on what the data
means, with figures and indicator names used sparingly (they had become lists of 8-10 figures the card already
shows), and the key risk is one short sentence, like the holdings rationale (LLM_hold_instr.RATIONALE).

2026-10-20 (later, Claude-authored, at Andres's request): one prompt per horizon instead of one that scored both.
Long-term candidates (the long-term screens) get LONG_TERM_SYSTEM_PROMPT, with the same rubric as before; swing
candidates (src/universal/recommendations/swing_screen.py) get SWING_SYSTEM_PROMPT. Each call is shorter (one rubric,
half the answer, only the data that rubric uses), which pays for scoring the swing screen's candidates too. The
swing rubric now works from the screen's facts and fixed trade plan: the model no longer sets the entry, stop, and
target (the noisiest part of the swing score, up to 9 of 30 points between identical runs), and each band names
the facts it rests on, so the same stock scores the same way more often. The bar for a pick is unchanged, and
earnings within 5 trading days cap the catalyst score at 10: the first run credited CSX and UNP, reporting 2 and 3
days later, with a catalyst.

2026-10-22 (Claude-authored, at Andres's request): less left to the model's judgment, without raising the bar's
reach. The checks a fact can settle are worked out in code (swing_screen.rubric_checks) and given with the stock:
the technical score starts from 30 less WEAKNESS_POINTS per weakness in the facts, and the model only takes off up
to CHART_POINTS for how the chart looks; the four signs of a turn are checked in code and their count sets the
momentum band (TURN_SIGN_BANDS); and the catalyst score is three parts with named evidence (a temporary dip 0-8,
something to lift it 0-8 at LIFT_POINTS per sign, no red flags 0-4). Each new band sits where the old band for the
same case did (e.g. one weakness: 24 less the chart, against 18-24 before), so scores aren't raised. The old bands
also contradicted each other: "below the 200-day average but the 50-day still above it" was one weakness (18-24),
while "below both the 50- and 200-day averages", the same chart, was stretched (12-17); the trend is now one fact.
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Claude-authored (2026-10-22): the swing rubric's numbers, shared by the prompt below and the checks the swing screen
# works out in code (swing_screen.rubric_checks), so the two can't drift apart
WEAKNESS_POINTS = 6.0       # Off the technical score per weakness the facts show (twice that for a double one)
CHART_POINTS = 5.0          # The most the model takes off on top for how the chart looks (orderly or erratic)
ORDERLY_PULLBACK_ATR = (1.5, 6.0)   # A pullback this deep from its recent high is orderly
FRESH_CROSS_SESSIONS = 4    # A new uptrend whose cross is this many sessions old or more has a weakness
MAX_RUN_FROM_LOW_ATR = 2.0  # ...as has one this many ATRs above its 10-day low
SUPPORT_NEAR_ATR, SUPPORT_FAR_ATR = 1.0, 2.0  # Above the support the stop sits under: one weakness past near, two past far
TURN_SIGN_RSI_BELOW = 55    # Sign (a): RSI higher than 5 sessions ago and below this
TURN_SIGN_UP_VOLUME = 1.2   # Sign (b): volume on up days over volume on down days, 10 sessions, at least this
TURN_SIGN_LOW_HELD = 2      # Sign (c): no new 10-day low for this many sessions...
TURN_SIGN_OFF_LOW_ATR = 0.5 # ...and the price at least this many ATRs above it
TURN_SIGN_BANDS = {4: (19.0, 20.0), 3: (16.0, 18.0), 2: (11.0, 15.0), 1: (6.0, 10.0), 0: (0.0, 5.0)}  # Momentum band by signs
LIFT_POINTS = 2.0           # Catalyst points per sign that something can lift the price within 2-8 weeks
EARNINGS_LIFT_SESSIONS = (6, 40)  # Earnings this many trading days away (after the 5-day event risk, within ~8 weeks)...
EARNINGS_LIFT_BEATS = 3     # ...after beating the estimate in at least this many of the last 4 reports
ANALYST_LIFT_UPSIDE_PCT = 15.0  # The analysts' mean target at least this far above the price

# Field descriptions for Pydantic sub-score schema fields
# Long-Term Sub-score Descriptions
VALUATION_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 25.0 evaluating valuation multiples (P/E, forward P/E, PEG, P/S, EV/EBITDA) "
    "relative to growth and sector benchmarks."
)

FINANCIAL_HEALTH_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 25.0 evaluating balance sheet stability, debt load (debt-to-equity), "
    "liquidity (current/quick ratios), and free cash flow generation."
)

GROWTH_MOMENTUM_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 25.0 evaluating YoY revenue/earnings growth, 3-5 year CAGRs, "
    "and profit margin trajectories (operating, net, FCF margins)."
)

COMPETITIVE_MOAT_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 25.0 evaluating market leadership, pricing power, intellectual property, "
    "barriers to entry, and strategic ecosystem partnerships."
)

# Swing Trade Sub-score Descriptions (the model scores three; risk/reward is scored in code from the fixed plan)
TECHNICAL_SETUP_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 30.0 of how good an entry the chart offers now, by the SWING RUBRIC: `checks.technical_start` "
    "(30 less the weaknesses in the facts), less up to 5 for how the chart looks."
)

RISK_REWARD_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 30.0 of the trade plan's reward-to-risk, (target - entry) / (entry - stop), computed in code "
    "from the swing screen's plan (LLM_recommendation.plan_risk_reward_score)."
)

MOMENTUM_VOLUME_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 20.0 within the SWING RUBRIC's band for how many of the four `checks.turn_signs` hold."
)

CATALYST_SENTIMENT_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 20.0, the SWING RUBRIC's three parts: the dip looks temporary (0-8), something can lift the "
    "price within 2-8 weeks (0-8), and no red flags (0-4)."
)

# Claude-authored (2026-10-16): written fields shown to the reader in the digest, so a pick says why it was made
LONG_TERM_SENTIMENT_DESCRIPTION = (
    "Overall outlook for owning the stock over the next 1-3+ years, weighing all the data: "
    "Bullish, Neutral, or Bearish."
)
LONG_TERM_THESIS_DESCRIPTION = (
    "At most 2 short sentences in plain language on why the business does or doesn't suit a long-term portfolio: "
    "what the data says about it (e.g. cheap for how fast it's growing, a fortress balance sheet), not a list of "
    "figures (see WRITTEN FIELDS)."
)
KEY_RISK_DESCRIPTION = (
    "One short sentence (at most about 25 words) on the single biggest risk to the long-term thesis and what it "
    "would mean for the stock."
)
SWING_SENTIMENT_DESCRIPTION = (
    "Outlook for the share price over the next 2-8 weeks, from the technicals, catalysts, and headlines: "
    "Bullish, Neutral, or Bearish."
)
SWING_THESIS_DESCRIPTION = (
    "At most 2 short sentences in plain language on the swing setup: why the stock looks cheap now, what should "
    "lift it, and what would confirm the bounce, describing what the chart is doing rather than listing "
    "indicator values (see WRITTEN FIELDS)."
)

# Tool specification configuration
LONG_TERM_TOOL_NAME = "record_long_term_evaluation"
LONG_TERM_TOOL_DESCRIPTION = (
    "Record the long-term sub-scores (valuation, financial health, growth momentum, competitive moat) with the "
    "sentiment, thesis, and key risk shown to the reader."
)
SWING_TOOL_NAME = "record_swing_evaluation"
SWING_TOOL_DESCRIPTION = (
    "Record the swing sub-scores (technical setup, momentum and volume, catalysts and sentiment) with the "
    "sentiment and thesis shown to the reader."
)

DATA_GUIDELINES = """DATA PARSING & METRIC GUIDELINES:
- Financial Ratios: Ratios such as `debt_to_equity` in the payload are scaled as percentages (e.g., 34.804 represents a 34.8% debt-to-equity ratio or ~0.35 D/E).
- Fields that are null or missing were unavailable; judge from the data that is present."""

# System level instructions for LLM with explicit scoring rubrics
# Claude-authored (2026-10-13): made an f-string and added the tool-call instruction. Why: the request now uses
# tool_choice "auto" (forced tool_choice returns a 400 on Opus 5.5 / Sonnet 5.5 / Fable 5.1), so the prompt
# itself must tell the model to record its scores with the tool.
LONG_TERM_SYSTEM_PROMPT = f"""
You are an expert equity analyst evaluating a stock as a long-term holding (1-3+ years).
Rate the candidate's sub-criteria using the strict, standardized rubric below.
Record your sub-scores by calling the `{LONG_TERM_TOOL_NAME}` tool exactly once, keeping every score within the range its rubric states.

{DATA_GUIDELINES}

### LONG-TERM EVALUATION RUBRIC (Total: 100 Points)
1. valuation_score (0.0 - 25.0):
   - 20.0 - 25.0: Undervalued relative to historical multiples & peers; low PEG ratio (< 1.5); attractive forward P/E compared to earnings growth.
   - 10.0 - 19.0: Fairly valued; trading near sector averages or premium justified by high revenue/EPS growth.
   - 0.0 - 9.0: Severely overvalued; stretched P/E or high price-to-sales without supporting growth metrics.

2. financial_health_score (0.0 - 25.0):
   - 20.0 - 25.0: Pristine balance sheet, strong free cash flow, low/manageable debt-to-equity (< 50%), healthy liquidity (current/quick ratio > 1.0).
   - 10.0 - 19.0: Stable balance sheet, manageable debt servicing, positive cash flows.
   - 0.0 - 9.0: High debt burden, negative free cash flow, liquidity or refinancing risks.

3. growth_momentum_score (0.0 - 25.0):
   - 20.0 - 25.0: Strong double-digit YoY revenue/EPS growth (> 20%), expanding operating/net margins, strong 3-5yr CAGRs.
   - 10.0 - 19.0: Moderate single-digit to low double-digit growth, steady profit margins.
   - 0.0 - 9.0: Stagnant or declining revenue/earnings, contracting profit margins.

4. competitive_moat_score (0.0 - 25.0):
   - 20.0 - 25.0: Clear pricing power, high switching costs, industry leader, crucial strategic partnerships (e.g., major foundry/hardware integrations).
   - 10.0 - 19.0: Moderate moat, strong brand recognition, faceable competition.
   - 0.0 - 9.0: Commodity business, low entry barriers, losing market share or facing technological disruption risks.

### WRITTEN FIELDS (shown to the reader in their daily digest)
- sentiment: your overall read on owning the stock for 1-3+ years, weighing the business and its valuation.
- thesis, key_risk: read on a phone by an individual investor, not an analyst. Say what the data means and how to read it (e.g. "cheap for how fast it's growing", "a fortress balance sheet") rather than listing figures: the card already shows the key figures and score breakdown beside the text. Keep the thesis to at most 2 short sentences and the key risk to one. Use a figure sparingly, only when it is the clearest way to make the point, and never string several together (not "forward P/E of 13.6, a PEG of 0.26, 106% revenue growth and a 66% operating margin"). Never invent numbers, events, or news that are not in the data, and say so plainly when the data argues against the stock: these fields are recorded for every candidate, not only the ones picked.
"""

SWING_SYSTEM_PROMPT = f"""
You are an expert swing trader evaluating a stock for a trade of 2-8 weeks.
Rate the candidate's sub-criteria using the strict, standardized rubric below.
Record your sub-scores by calling the `{SWING_TOOL_NAME}` tool exactly once, keeping every score within the range its rubric states.

THE IDEAL SWING TRADE buys a decent business early, while it is temporarily cheap and likely to go back up within 2-8 weeks: a pullback to support in a stock whose longer-term trend and business are intact, where the selling is running out, or the first few days of a new upward trend. It is never a stock that has already jumped or risen for weeks: buying far along a run leaves little upside and a long way down. Nor is it a bet on an earnings report a few days away, which can gap the price either way.

WHAT YOU ARE GIVEN:
- The stock passed a swing screen: a profitable, liquid, analyst-covered business in one of two setups, `swing_setup.setup`: "pullback" (a dip in a healthy longer trend) or "new_uptrend" (its 9-day average crossed above its 21-day average within the last few sessions).
- `swing_setup.facts` were computed from the daily bars. Distances are in ATRs, a normal day's range (`atr`): `pullback_atr` / `pullback_pct` from the recent high close, `run_from_low_atr` from the 10-day low and `days_since_low_10d` since it (0 = a new low today), `pct_vs_sma_200` and `trend`, `rsi` and `rsi_5_days_ago`, `up_vs_down_volume_10d` (volume on up days over volume on down days, last 10 sessions), `ema_9_crossed_above_ema_21_days_ago`, and `spy_change_since_high_pct` (the S&P 500's move over the same days as the stock's drop from its recent high). Use them as given; don't recompute them.
- `swing_setup.catalyst_facts`: the next earnings (`next_earnings_date`, `sessions_to_earnings` trading days away), `earnings_beats_last_4` (how many of the last 4 reports beat the estimate), `analyst_target_upside_pct` (the analysts' mean target against the price), and `insider_mspr_3m` (insiders' net buying over the last 3 months, from -100 all selling to 100 all buying). Null means unknown.
- `swing_setup.checks` are this rubric's checks, worked out in code from those facts: `technical_start` with the `weaknesses` behind it, the four `turn_signs`, and the `lifts` the data shows. Apply them as given; don't re-judge them.
- `swing_setup.plan` is the trade plan, set by fixed rules for every stock: the entry is the latest close, the stop sits below the nearest support under the price (`stop_basis`; the price is `above_support_atr` above that support), and the target is the nearest overhead level (`target_basis`). Its reward-to-risk is scored separately in code, worth up to 30 points; you don't set or score the levels.
- `daily_bars` (most recent first, unadjusted; the newest may be a session still in progress) and `technical_indicators` (daily RSI 14, EMA 9/21, Bollinger Bands 20, Parabolic SAR; weekly beta to the S&P 500) show the price path the facts summarize. `key_fundamentals`, analyst data, earnings, and headlines show whether the dip changes the business.

{DATA_GUIDELINES}

### SWING RUBRIC (your three sub-scores, 70 points; with the plan's reward-to-risk, 100)
1. technical_setup_score (0.0 - 30.0): is this the ideal entry right now?
   - Start from `checks.technical_start`: 30, less {WEAKNESS_POINTS:g} for each weakness the facts show (listed in `checks.weaknesses`):
     - the trend (`facts.trend`): "intact" (above its 200-day average, with the 50-day above the 200-day) loses nothing, "mixed" (one of the two) loses {WEAKNESS_POINTS:g}, and "weak" (neither) loses {2 * WEAKNESS_POINTS:g};
     - the price {SUPPORT_NEAR_ATR:g}-{SUPPORT_FAR_ATR:g} ATRs above the support the stop sits under (`plan.above_support_atr`) loses {WEAKNESS_POINTS:g}, and more than {SUPPORT_FAR_ATR:g} loses {2 * WEAKNESS_POINTS:g};
     - a pullback of less than {ORDERLY_PULLBACK_ATR[0]:g} or more than {ORDERLY_PULLBACK_ATR[1]:g} ATRs from its recent high loses {WEAKNESS_POINTS:g};
     - a new uptrend loses {WEAKNESS_POINTS:g} for each of: a cross {FRESH_CROSS_SESSIONS} or more sessions old, a close below EMA 21, and a price more than {MAX_RUN_FROM_LOW_ATR:g} ATRs above its 10-day low.
   - Then take off up to {CHART_POINTS:g} for how the chart looks in `daily_bars`: nothing for an orderly move (a pullback drifting down on lighter volume with small candles, or a steady climb off a higher low or a base), up to {CHART_POINTS:g} for wide erratic swings, big down days on heavy volume, or gaps.
   - Score at most 11.0, whatever the start, when the bars contradict the setup: a gap down through a support (EMA 21, the 50- or 200-day average) on heavy volume in the last 5 sessions, or new lows day after day with no sign of a floor.

2. momentum_volume_score (0.0 - 20.0): the signs of a turn, checked in code (`checks.turn_signs`):
   (a) `rsi_rising`: RSI higher than five sessions ago, and below {TURN_SIGN_RSI_BELOW};
   (b) `volume_on_up_days`: volume heavier on up days than down days (`up_vs_down_volume_10d` of {TURN_SIGN_UP_VOLUME:g} or more);
   (c) `higher_low`: no new 10-day low for {TURN_SIGN_LOW_HELD} sessions, and the price at least {TURN_SIGN_OFF_LOW_ATR:g} ATR above it;
   (d) `above_ema_9_or_sar`: a close above EMA 9, or the Parabolic SAR below the price.
   - How many hold sets the band: four {TURN_SIGN_BANDS[4][0]:.1f} - {TURN_SIGN_BANDS[4][1]:.1f}, three {TURN_SIGN_BANDS[3][0]:.1f} - {TURN_SIGN_BANDS[3][1]:.1f}, two {TURN_SIGN_BANDS[2][0]:.1f} - {TURN_SIGN_BANDS[2][1]:.1f}, one {TURN_SIGN_BANDS[1][0]:.1f} - {TURN_SIGN_BANDS[1][1]:.1f}, none {TURN_SIGN_BANDS[0][0]:.1f} - {TURN_SIGN_BANDS[0][1]:.1f}.
   - Within the band, score higher the clearer the signs (RSI up 5 or more points, up/down volume of 1.5 or more, a close well above EMA 9) and lower when they only just qualify.
   - Score at most 5.0 when RSI is at its lowest in 10 sessions (`facts.rsi_at_10_day_low`): it is still falling.

3. catalyst_sentiment_score (0.0 - 20.0): the sum of three parts.
   (a) Does the dip look temporary? 0 - 8 (for a new uptrend, the decline it is turning up from):
       - 8: the market fell with it (the S&P 500's drop over the same days, `facts.spy_change_since_high_pct`, is at least a third of the stock's own, `facts.pullback_pct`), or the headlines show an overreaction to news that doesn't change the business;
       - 4: nothing in the data explains the drop, and nothing suggests the business changed;
       - 0: the drop has a business cause (a guidance cut, a lost customer, falling sales or margins);
       - 2 or 6 when the evidence is partial.
   (b) Can something lift it within 2-8 weeks? 0 - 8: {LIFT_POINTS:g} for each of `checks.lifts` that holds (`earnings_after_beats`: earnings {EARNINGS_LIFT_SESSIONS[0]}-{EARNINGS_LIFT_SESSIONS[1]} trading days away, after beating the estimate in at least {EARNINGS_LIFT_BEATS} of the last 4 reports; `analyst_upside`: the mean target at least {ANALYST_LIFT_UPSIDE_PCT:g}% above the price; `insider_buying`: net insider buying over the last 3 months), and {LIFT_POINTS:g} more for a dated event within the window in the headlines (a product launch, investor day, approval decision, or index inclusion).
   (c) No red flags? 0 - 4: 4 when the headlines and data show none; 2 for a minor one (a single downgrade amid mostly positive coverage); 0 for a guidance cut, several downgrades, regulatory or legal trouble, a share offering, or an accounting issue.
   - News that breaks the thesis caps this score at 5.0.
   - Earnings within the next 5 trading days (`catalyst_facts.sessions_to_earnings` of 5 or less, or `catalysts_and_earnings.upcoming_earnings`) are event risk, not a catalyst: score this at most 10.0, whatever else the data shows.

### WRITTEN FIELDS (shown to the reader in their daily digest)
- sentiment: your read on the share price over the next 2-8 weeks.
- thesis: read on a phone by an individual investor, not an analyst. Say what the data means and how to read it (e.g. "pulled back to its long-term trend line with the selling fading") rather than listing figures: the card already shows the trade plan and figures beside the text. At most 2 short sentences: why it looks cheap now, what should lift it, and what would confirm the bounce. Use a figure or an indicator name sparingly, and never string several together. Never invent numbers, events, or news that are not in the data, and say so plainly when the data argues against the trade: this field is recorded for every candidate, not only the ones picked.
"""

# User prompt templates
USER_PROMPT_TEMPLATES = {
    "long_term": "Evaluate {symbol} as a long-term holding against the rubric, using the provided market data context:\n\n{sanitized_prompt_data}",
    "swing": "Evaluate {symbol} as a swing trade against the rubric, using the provided setup and market data:\n\n{sanitized_prompt_data}",
}


def format_user_prompt(symbol: str, sanitized_prompt_data: str, horizon: str = "long_term") -> str:
    """Formats the user prompt for one horizon ("long_term" or "swing") with the stock symbol and json context."""
    return USER_PROMPT_TEMPLATES[horizon].format(symbol=symbol, sanitized_prompt_data=sanitized_prompt_data)
