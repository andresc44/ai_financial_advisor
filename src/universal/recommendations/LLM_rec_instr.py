"""
LLM Recommendation Instructions & Schema Descriptions.
Centralized repository for prompts, field descriptions, and system instructions 
used in standardized stock evaluation.
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
    "Rating from 0.0 to 30.0 evaluating chart patterns, short-term moving average alignment (9/21 EMAs), "
    "and price positioning within Bollinger Bands."
)

RISK_REWARD_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 30.0 evaluating entry proximity to key support/resistance levels, "
    "stop-loss distance, and upside potential toward analyst price targets."
)

MOMENTUM_VOLUME_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 20.0 evaluating RSI levels and recovery trends, Parabolic SAR direction, "
    "and volume relative to 10-day/3-month averages."
)

CATALYST_SENTIMENT_SCORE_DESCRIPTION = (
    "Rating from 0.0 to 20.0 evaluating upcoming earnings catalysts, historical earnings surprise rates, "
    "analyst upgrades/downgrades, and recent headline sentiment."
)

# Claude-authored (2026-10-16): written fields shown to the reader in the digest, so a pick says why it was made
LONG_TERM_SENTIMENT_DESCRIPTION = (
    "Overall outlook for owning the stock over the next 1-3+ years, weighing all the data: "
    "Bullish, Neutral, or Bearish."
)
LONG_TERM_THESIS_DESCRIPTION = (
    "1-2 sentences for the reader on why the stock does or doesn't suit a long-term portfolio, citing the 2-3 "
    "figures from the data that drive the long-term scores."
)
KEY_RISK_DESCRIPTION = (
    "One sentence on the single biggest risk to the long-term thesis, citing the supporting figure or headline "
    "where the data has one."
)
SWING_SENTIMENT_DESCRIPTION = (
    "Outlook for the share price over the next 2-8 weeks, from the technicals, catalysts, and headlines: "
    "Bullish, Neutral, or Bearish."
)
SWING_THESIS_DESCRIPTION = (
    "1-2 sentences for the reader on the swing setup: what the chart and catalysts show and what would confirm it, "
    "citing the indicator values behind it."
)
ENTRY_PRICE_DESCRIPTION = (
    "Suggested entry price for a swing long, in the stock's trading currency: at or near the current price, or at "
    "a nearby support level from the data (EMA, middle/lower Bollinger Band)."
)
STOP_LOSS_DESCRIPTION = (
    "Stop-loss price below the entry, from a level in the data (Parabolic SAR, lower Bollinger Band, recent swing "
    "low), where the setup is invalidated."
)
PRICE_TARGET_DESCRIPTION = (
    "Price target above the entry for the next 2-8 weeks, from a level in the data (upper Bollinger Band, recent "
    "high, 52-week high, analyst target), whichever is the first realistic resistance."
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
- Technical Indicators: Base technical assessments on supplied series including daily RSI (14-day), short-term exponential moving averages (ema_9, ema_21), 20-day Bollinger Bands, Parabolic SAR, and volume relative to 10-day/3-month averages.

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
1. technical_setup_score (0.0 - 30.0):
   - 25.0 - 30.0: Strong bullish structure; price above key short-term EMAs (9/21), near middle/lower Bollinger Band support bouncing upward, or breaking out.
   - 12.0 - 24.0: Neutral chart setup, consolidating sideways near mid-ranges or moving averages.
   - 0.0 - 11.0: Clear technical breakdown; price below key EMAs, trailing toward lower bands, bearish Parabolic SAR alignment.

2. risk_reward_ratio_score (0.0 - 30.0):
   - 25.0 - 30.0: Entry right at major support with a tight stop-loss and >= 3:1 upside potential toward analyst price targets or 52-week highs.
   - 12.0 - 24.0: Acceptable 1.5:1 to 2:1 risk-reward setup.
   - 0.0 - 11.0: Poor risk-reward (< 1:1); buying near extended resistance levels with substantial downside risk to support.

3. momentum_volume_score (0.0 - 20.0):
   - 16.0 - 20.0: RSI recovering from oversold (40-60 zone trending upward), trading volume above 10-day/3-month averages on green days.
   - 8.0 - 15.0: Neutral RSI (45-55), average volume.
   - 0.0 - 7.0: Overbought or deteriorating RSI (< 40 and falling), heavy sell volume, trailing Parabolic SAR overhead.

4. catalyst_sentiment_score (0.0 - 20.0):
   - 16.0 - 20.0: Strong historical earnings surprise track record, upcoming catalysts in 1-4 weeks, positive Wall Street analyst upgrades/price target revisions.
   - 8.0 - 15.0: Low news volatility, neutral insider sentiment, mixed analyst coverage.
   - 0.0 - 7.0: Recent analyst downgrades, heavy insider selling, imminent regulatory or macro headwinds.

### WRITTEN FIELDS (shown to the reader in their daily digest)
- sentiment (in each section): your overall read for that horizon. Long-term weighs the business and valuation over 1-3+ years; swing weighs the next 2-8 weeks of price action. They can differ (e.g. a strong business in a short-term downtrend).
- thesis, key_risk: plain language for an individual investor. Cite the specific figures from the data behind each point (e.g. "forward P/E of 18 against 25% revenue growth"). Never invent numbers, events, or news that are not in the data, and say so plainly when the data argues against the stock: these fields are recorded for every candidate, not only the ones picked.
- entry_price, stop_loss, price_target (swing): derive each from levels in the data (current price, EMAs, Bollinger Bands, Parabolic SAR, 52-week range, analyst targets), in the stock's trading currency, with stop_loss < entry_price < price_target. Mention the level each one comes from in the swing thesis when it helps the reader.
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