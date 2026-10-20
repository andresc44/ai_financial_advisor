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

# Swing Trade Sub-score Descriptions
TECHNICAL_SETUP_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 30.0 of how good an entry the chart offers now: high for a pullback to support within an "
    "intact longer-term trend, low for a stock stretched after a multi-week run or breaking down with no support."
)

RISK_REWARD_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 30.0 of the reward-to-risk of your own entry_price, stop_loss, and price_target: "
    "(price_target - entry_price) / (entry_price - stop_loss), with the stop at least a normal day's range below the entry."
)

MOMENTUM_VOLUME_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 20.0 of signs the selling is running out and buyers are returning: RSI turning up from "
    "30-45, price holding or reclaiming support, the Parabolic SAR flipping below price, and volume on up vs down days."
)

CATALYST_SENTIMENT_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 20.0 of whether the drop looks temporary and something can lift the price in 2-8 weeks: "
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
ENTRY_PRICE_DESCRIPTION = (
    "Suggested entry price for a swing long, in the stock's trading currency: at or near the current price, or at "
    "the support just below (EMA 21, lower Bollinger Band, 50- or 200-day average) if the price is still falling."
)
STOP_LOSS_DESCRIPTION = (
    "Stop-loss price just below the support the trade relies on (lower Bollinger Band, recent swing low, 50- or "
    "200-day average), where a break means the bounce failed."
)
PRICE_TARGET_DESCRIPTION = (
    "Price target above the entry for the next 2-8 weeks: where the rebound should reach (EMA 21 or the 50-day "
    "average from below, the middle or upper Bollinger Band, the last swing high, or the analyst target if that close)."
)

# Tool specification configuration
EVALUATION_TOOL_NAME = "record_stock_evaluation_breakdown"
EVALUATION_TOOL_DESCRIPTION = (
    "Record standardized sub-score evaluations across long-term fundamentals and swing trading technicals "
    "to compute deterministic equity rating scores, with the sentiment, thesis, and swing price levels shown "
    "to the reader."
)

# System level instructions for LLM with explicit scoring rubrics
# Claude-authored (2026-10-13): made an f-string and added the tool-call instruction. Why: the request now uses
# tool_choice "auto" (forced tool_choice returns a 400 on Opus 5.5 / Sonnet 5.5 / Fable 5.1), so the prompt
# itself must tell the model to record its scores with the tool.
SYSTEM_PROMPT = f"""
You are an expert quantitative equity strategist and technical analyst.
Analyze the provided stock market context and evaluate the candidate stock by rating specific sub-criteria using strict, standardized rubrics.
Record your sub-scores by calling the `{EVALUATION_TOOL_NAME}` tool exactly once, keeping every score within the range its rubric states.

DATA PARSING & METRIC GUIDELINES:
- Financial Ratios: Ratios such as `debt_to_equity` in the payload are scaled as percentages (e.g., 34.804 represents a 34.8% debt-to-equity ratio or ~0.35 D/E).
- Daily Bars: `daily_bars` lists the most recent trading days (date, open, high, low, close, volume), most recent first, unadjusted like the indicators; the newest bar may be a session still in progress. Use them for the price path the indicators summarize: pullbacks into support, higher or lower lows, bounces, gaps, and whether volume is heavier on up days or down days.
- Technical Indicators: Base technical assessments on supplied series including daily RSI (14-day), short-term exponential moving averages (ema_9, ema_21), 20-day Bollinger Bands, Parabolic SAR, and volume relative to 10-day/3-month averages. `beta_52_1week` is the stock's beta to the S&P 500 (SPY) from 52 weeks of weekly returns; `price_action.beta` is Yahoo's longer-term beta.

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

### SWING TRADE EVALUATION RUBRIC (Total: 100 Points)
A good swing trade buys a stock early, while it is temporarily cheap and likely to go back up within 2-8 weeks: a pullback or overreaction in a stock whose longer-term trend and business are intact, where the selling is running out, or the first few days of a new upward trend (a higher low, a first close back above EMA 9/21, the Parabolic SAR just flipped below the price). It is not a stock that has already jumped or risen for weeks: buying far along a run leaves little upside and a long way down. Score every candidate on this one idea. A candidate already far along its run (more than about 3 normal days' ranges above its 10-day low, RSI above 70, or above its upper Bollinger Band) is not picked however it scores.

1. technical_setup_score (0.0 - 30.0): is it cheap right now, within a healthy longer trend?
   - 25.0 - 30.0: Pulled back to clear support (lower Bollinger Band, EMA 21, the 50- or 200-day average, a prior low or the lower part of the 52-week range) while the longer-term trend is intact (price near or above the 200-day average, or the 50-day average still above the 200-day); or just turning up into a new upward trend off a base or support, still close to the turn (within a few days and a few normal daily ranges of the low).
   - 12.0 - 24.0: Mid-range: near the middle band or the EMAs, neither cheap nor stretched; or a pullback where the longer trend is unclear.
   - 0.0 - 11.0: Stretched after a run or a sharp jump (above the upper band, well above EMA 9/21, or already several normal daily ranges above its recent low), so buying here chases the move; or a breakdown with no support in sight (falling through the 200-day average or a 52-week low).

2. risk_reward_ratio_score (0.0 - 30.0): the reward-to-risk of YOUR OWN levels, (price_target - entry_price) / (entry_price - stop_loss).
   - 25.0 - 30.0: 2.5:1 or better, with the stop just below a clear support level.
   - 12.0 - 24.0: 1.5:1 to 2.5:1.
   - 0.0 - 11.0: Below 1.5:1, or no sensible stop because there is no support nearby.
   - Score the ratio your three levels actually give; don't score an analyst target you didn't use as your price_target. This score is recomputed from your levels after you answer, and a stop closer to the entry than a normal day's range (the average true range of the recent `daily_bars`) is counted as that far, since ordinary noise would hit it. So set a realistic stop rather than a tight one.

3. momentum_volume_score (0.0 - 20.0): is the selling running out and are buyers returning?
   - 16.0 - 20.0: Clear signs of a turn: RSI rising from roughly 30-45 (or making a higher low while the price made a lower low), a higher low or a bounce off support in `daily_bars`, the price holding or reclaiming support or EMA 9, the Parabolic SAR flipped or about to flip below the price, volume heavier on the latest up days than on the down days.
   - 8.0 - 15.0: Early or mixed: RSI flat in the 40s to mid-50s, no clear turn yet.
   - 0.0 - 7.0: Still falling hard with no floor (RSI falling, new lows, the SAR above the price and pulling away), or RSI above ~70 after a run, leaving little room to rise.

4. catalyst_sentiment_score (0.0 - 20.0): does the drop look temporary, and can something lift the price soon?
   - 16.0 - 20.0: The drop looks like an overreaction or market-wide selling that doesn't change the business, and a catalyst is 1-4 weeks out (earnings with a record of beats, a product event), with analyst targets well above the price or insider buying.
   - 8.0 - 15.0: No clear catalyst either way; mixed news or analyst coverage.
   - 0.0 - 7.0: News that breaks the thesis (guidance cuts, downgrades, regulatory trouble), heavy insider selling, or an imminent risk that could extend the drop.

### WRITTEN FIELDS (shown to the reader in their daily digest)
- sentiment (in each section): your overall read for that horizon. Long-term weighs the business and valuation over 1-3+ years; swing weighs the next 2-8 weeks of price action. They can differ (e.g. a strong business stretched after a run, where the next few weeks look flat or down).
- thesis, key_risk: read on a phone by an individual investor, not an analyst. Keep the thesis to at most 2 short sentences and the key risk to one. Say what the data means and how to read it (e.g. "cheap for how fast it's growing", "a fortress balance sheet", "pulled back to its long-term trend line with the selling fading") rather than listing figures: the card already shows the price levels, key figures, and score breakdown beside the text. Use a figure or an indicator name sparingly, only when it is the clearest way to make the point, and never string several together (not "forward P/E of 13.6, a PEG of 0.26, 106% revenue growth and a 66% operating margin"). Never invent numbers, events, or news that are not in the data, and say so plainly when the data argues against the stock: these fields are recorded for every candidate, not only the ones picked.
- entry_price, stop_loss, price_target (swing): derive each from levels in the data, in the stock's trading currency, with stop_loss < entry_price < price_target. Enter at or near the current price, or at the support just below if the price is still falling. Put the stop just below the support the trade relies on (the level whose break means the bounce failed), and the target where the rebound should reach within 2-8 weeks (EMA 21 or the 50-day average from below, the middle or upper Bollinger Band, the last swing high, or the analyst target if it is that close). The card shows the three levels, so the swing thesis needn't repeat them.
"""

# User prompt template
USER_PROMPT_TEMPLATE = (
    "Evaluate {symbol} against the standardized sub-criteria rubrics using the provided market data context:\n\n{sanitized_prompt_data}"
)


def format_user_prompt(symbol: str, sanitized_prompt_data: str) -> str:
    """Formats the user prompt with the stock symbol and json context."""
    return USER_PROMPT_TEMPLATE.format(
        symbol=symbol, 
        sanitized_prompt_data=sanitized_prompt_data
    )