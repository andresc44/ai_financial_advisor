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
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

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
    "Rating from 0.0 to 30.0 of how good an entry the chart offers now, by the SWING RUBRIC's bands: high for a "
    "pullback that has reached the support its stop sits under in an intact trend, or a turn up only a few days old."
)

RISK_REWARD_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 30.0 of the trade plan's reward-to-risk, (target - entry) / (entry - stop), computed in code "
    "from the swing screen's plan (LLM_recommendation.plan_risk_reward_score)."
)

MOMENTUM_VOLUME_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 20.0 of how many signs of a turn the data shows, by the SWING RUBRIC's checklist: RSI rising, "
    "volume heavier on up days, a higher low or bounce off support, a close above EMA 9 or the SAR flipped below."
)

CATALYST_SENTIMENT_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 20.0 of whether the dip looks temporary and something can lift the price in 2-8 weeks: "
    "an overreaction that doesn't change the business, upcoming earnings with a record of beats, analyst targets "
    "well above the price, insider buying; low for news that breaks the thesis."
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
- `swing_setup.facts` were computed from the daily bars. Distances are in ATRs, a normal day's range (`atr`): `pullback_atr` / `pullback_pct` from the recent high close, `run_from_low_atr` from the 10-day low, `pct_vs_sma_200`, `rsi` and `rsi_5_days_ago`, `up_vs_down_volume_10d` (volume on up days over volume on down days, last 10 sessions), `ema_9_crossed_above_ema_21_days_ago`. Use them as given; don't recompute them.
- `swing_setup.plan` is the trade plan, set by fixed rules for every stock: the entry is the latest close, the stop sits below the nearest support under the price (`stop_basis`), and the target is the nearest overhead level (`target_basis`). Its reward-to-risk is scored separately in code, worth up to 30 points; you don't set or score the levels.
- `daily_bars` (most recent first, unadjusted; the newest may be a session still in progress) and `technical_indicators` (daily RSI 14, EMA 9/21, Bollinger Bands 20, Parabolic SAR; weekly beta to the S&P 500) show the price path the facts summarize. `key_fundamentals`, analyst data, earnings, and headlines show whether the dip changes the business.

{DATA_GUIDELINES}

### SWING RUBRIC (your three sub-scores, 70 points; with the plan's reward-to-risk, 100)
1. technical_setup_score (0.0 - 30.0): is this the ideal entry right now?
   - 25.0 - 30.0: The ideal entry. A pullback in an intact trend (above its 200-day average with the 50-day above the 200-day), down an orderly 1.5-6 ATRs from its recent high (no gap through support on heavy volume), now within about 1 ATR of the support the stop sits under. Or a new uptrend whose cross is at most 3 sessions old, closing above EMA 21, off a higher low or a base, still within about 2 ATRs of its 10-day low.
   - 18.0 - 24.0: A valid setup with one weakness: a mixed trend (below the 200-day average but the 50-day still above it, or the reverse), the price 1-2 ATRs above the support, or a cross 3-4 sessions old.
   - 12.0 - 17.0: A valid setup that is stretched or messy: 2 or more ATRs above the support, wide erratic swings, or below both the 50- and 200-day averages.
   - 0.0 - 11.0: The data contradicts the setup: breaking below the support the plan relies on, a gap down on heavy volume through support, or new lows with no sign of a floor.

2. momentum_volume_score (0.0 - 20.0): count the signs of a turn:
   (a) RSI higher than five sessions ago (`rsi` > `rsi_5_days_ago`) and below about 55;
   (b) volume heavier on up days than down days (`up_vs_down_volume_10d` of 1.2 or more);
   (c) a higher low or a bounce off support in the latest `daily_bars`;
   (d) a close above EMA 9, or the Parabolic SAR flipped below the price.
   - 16.0 - 20.0: three or four signs.
   - 11.0 - 15.0: two signs.
   - 6.0 - 10.0: one sign.
   - 0.0 - 5.0: none, or RSI still falling to new lows.

3. catalyst_sentiment_score (0.0 - 20.0): does the dip look temporary, and can something lift the price soon?
   - 16.0 - 20.0: Both: the dip looks like market-wide selling or an overreaction to news that doesn't change the business, AND something can lift it soon (earnings 1-4 weeks out with a record of beats, analyst targets well above the price, insider buying, a product event).
   - 11.0 - 15.0: One of the two, or both but weaker.
   - 6.0 - 10.0: Nothing clear either way; mixed news or coverage.
   - 0.0 - 5.0: News that breaks the thesis (guidance cut, downgrades, regulatory trouble), heavy insider selling, or an imminent risk that could extend the drop.
   - Earnings within the next 5 trading days (`catalysts_and_earnings.upcoming_earnings`) are event risk, not a catalyst: score this at most 10.0, whatever else the data shows.

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
