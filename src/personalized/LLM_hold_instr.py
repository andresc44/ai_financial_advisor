"""
LLM Holding Evaluation Instructions & Schema Descriptions.
Centralized repository for prompts, field descriptions, and system instructions
used to decide whether to add to an existing position, hold it, watch it, take profits, or sell it.

Authorship: Written by Claude (Claude Opus 5.5 via Claude Code), at Andres's request.
- 2026-10-13: created, mirroring src/universal/recommendations/LLM_rec_instr.py, so the sell-side
  prompt lives apart from the evaluation code. The rubric reserves 'urgent_sell' for immediate,
  time-sensitive action because it pushes a phone alert and the check repeats on a schedule.
- 2026-10-13: added the MARKET SESSION guidance and the `market_session` prompt field. Why: the watchdog
  can now run in pre-market, after-hours, overnight, and on weekends, where prices are thin or stale.
- 2026-10-14: added the `sentiment` output and the SENTIMENT guidance. Why: the digest's holding cards show a
  Bullish / Neutral / Bearish badge beside the headlines, and Claude reads the news and full data, whereas
  analyst ratings alone rate almost every large company a buy.
- 2026-10-18: new labels at Andres's request: buy_more (a rebounding stock that is cheap now), hold (healthy;
  let it reach its next swing peak), risky (needs close watching; not necessarily a good time to sell),
  profit_sell (a recent riser whose run looks to be tiring), and urgent_sell (unchanged). They replace hold /
  trim / risk_alert / urgent_sell; current_recommendation translates the old ones still in saved state. A good
  add is a stock that is cheap now and likely to recover, not one that has already run for weeks.
- 2026-10-19: the rationale is at most 2 plain sentences on what the trend is doing and what it suggests (plus
  any important news), with indicator names used sparingly, at Andres's request. Why: rationales had grown to ~900 characters of indicator names and
  values, hard to read on a phone and a tenth of the email's size.
- 2026-10-22: the rubric works from facts code works out for each holding (`checks`, src/personalized/holding_checks.py),
  and the labels are checked in a fixed order with stated thresholds, at Andres's request. Why: in that day's model
  comparison every model but Opus gave Opus's label for only ~7 holdings in 10, mostly with extra 'risky' labels that
  cited the price under its short-term averages or an upcoming report: judgment calls the rubric left open. His
  choices: 'risky' means genuinely risky, needing a close eye; leveraged ETFs are judged on their trend. Later the
  same day, after the comparison (Opus 5.5 and the cheaper models then agreed on 91-95% of the labels), he asked for
  'risky' to be a little more reserved: an upcoming report is now a lesser warning, so it needs another one.
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Claude-authored (2026-10-22): the rubric's numbers, shared with holding_checks so the prompt and the code agree.
# The four signs of a turn are the swing rubric's.
from src.universal.recommendations.LLM_rec_instr import (  # noqa: E402
    TURN_SIGN_LOW_HELD, TURN_SIGN_OFF_LOW_ATR, TURN_SIGN_RSI_BELOW, TURN_SIGN_UP_VOLUME,
)

PULLBACK_ATR = 1.5        # A pullback: down at least this many ATRs from the 20-day high close
RECENT_RUN_PCT = 15.0     # A real recent run: up at least this much from the 20-day low...
RECENT_RUN_ATR = 4.0      # ...or this many ATRs
BUY_MORE_TURN_SIGNS = 2   # Signs of a turn 'buy_more' needs
STRETCHED_RSI = 70        # Stretched: RSI at least this, or a close above the upper Bollinger Band
RSI_HIGH, RSI_FALL_POINTS = 65, 5  # Tiring: RSI down this many points from a high of at least RSI_HIGH (5 sessions)

# The recommendations, most urgent first (the digest's order)
RECOMMENDATIONS = ("urgent_sell", "risky", "profit_sell", "buy_more", "hold")
# Labels used before 2026-10-18, still in saved watchdog state, older payloads, and the track record
LEGACY_RECOMMENDATIONS = {"risk_alert": "risky", "trim": "profit_sell"}


def current_recommendation(value):
    """A recommendation in today's labels: the older 'risk_alert' and 'trim' become 'risky' and 'profit_sell'."""
    return LEGACY_RECOMMENDATIONS.get(value, value) if isinstance(value, str) else value

# Field descriptions for the Pydantic evaluation schema
RECOMMENDATION_DESCRIPTION = (
    "One of 'buy_more', 'hold', 'risky', 'profit_sell', or 'urgent_sell'. Reserve 'urgent_sell' for "
    "situations where immediate, time-sensitive action is required."
)

SENTIMENT_DESCRIPTION = (
    "Your overall outlook on the stock itself over the coming weeks to months: 'Bullish', 'Neutral', or "
    "'Bearish', judged from all of the provided data with the most weight on recent news and catalysts. "
    "Independent of the user's cost basis, gain, and position size."
)

RATIONALE_DESCRIPTION = (
    "At most 2 short sentences in plain language (see RATIONALE in the instructions): what the price trend is "
    "doing and what it suggests for the position, plus the effect of any important news."
)

# Tool specification configuration
EVALUATION_TOOL_NAME = "record_holding_recommendation"
EVALUATION_TOOL_DESCRIPTION = (
    "Record the buy_more / hold / risky / profit_sell / urgent_sell recommendation for a currently held position, "
    "your Bullish / Neutral / Bearish sentiment on the stock, and a short plain-language rationale."
)

# System level instructions for LLM with explicit recommendation rubric
SYSTEM_PROMPT = f"""
You are an expert portfolio manager and technical analyst reviewing a stock the user already owns.
Decide what the user should do with the position now (add to it, hold it, watch it closely, sell into strength, or sell immediately), and give your overall sentiment on the stock, using the position details and market context provided.
Record your decision by calling the `{EVALUATION_TOOL_NAME}` tool exactly once.

HOW THE OUTPUT IS USED:
- 'buy_more', 'hold', 'risky', and 'profit_sell' appear in a twice-daily email digest the user reads at leisure.
- The sentiment appears as a badge beside the stock's news headlines in that digest.
- 'urgent_sell' immediately pushes a phone alert, and this evaluation re-runs every hour. A false 'urgent_sell' erodes trust in every future alert, so reserve it for situations that genuinely cannot wait for the next digest.

DATA PARSING & METRIC GUIDELINES:
- `positions` lists every lot of this stock across the user's accounts. `unrealized_pnl_pct`, `daily_pnl_pct`, `natr` (14-day ATR as a % of price), and `annualized_vol` are already in percent (e.g. 12.5 means 12.5%). `rsi` is the 14-day RSI from daily closes.
- Money amounts (`market_value`, `total_cost_basis`, `unrealized_pnl`, `daily_pnl`) are in USD, converted at today's rate (`fx_rate_to_usd`), so unrealized P/L excludes currency moves since purchase. Per-share prices (`current_price`, `valuation_price`, `avg_cost_basis`, `last_close`, `session_price`) are in the listing's `currency`, the same currency as `market_context`.
- `market_value`, `unrealized_pnl`, and `daily_pnl` are measured at `valuation_price`: the last regular session's official close, or the live price while the session is open. Outside regular hours `current_price` and `session_price` can be extended-hours quotes, so they may differ from it.
- `daily_pnl` / `daily_pnl_pct` cover one regular trading session, dated `daily_pnl_session`: today's session (live) once the market has opened, otherwise the last completed session (before the open, overnight, weekends, and holidays it is the previous trading day's move, not today's). Extended-hours moves are not included; see `session_price` for those.
- `portfolio_weight_pct` is the stock's share of total portfolio market value (approximate; ignores currency differences).
- `market_context` uses the same structure as new-stock analysis: profile, price action, valuation and fundamentals, analyst sentiment and insider activity, upcoming earnings and past surprises, quarterly financial trends, technical indicator series (daily RSI 14, EMA 9/21, Bollinger Bands 20, Parabolic SAR; weekly 52-week beta to the S&P 500), the most recent daily bars (`daily_bars`: date, open, high, low, close, volume, unadjusted; the newest may be a session in progress), and recent headlines. Time series are ordered most recent first. Use the daily bars for the price path the indicators summarize: pullbacks into support, higher or lower lows, bounces, gaps, and whether volume is heavier on up or down days.
- Financial ratios such as `debt_to_equity` are scaled as percentages (e.g. 34.804 represents ~0.35 D/E).
- `market_context` may be null or contain error entries when a data source failed. Decide from the data that is present, and never escalate to 'urgent_sell' because data is missing.
- Judge the stock on its forward outlook. Cost basis and unrealized gain can inform 'profit_sell' and risk tolerance, but a loss alone is not a reason to sell and a gain alone is not a reason to hold.
- `opened_from_finnbot_idea`, when present: the user bought this stock after Finnbot suggested it, as a 'swing' idea (with its plan: `entry_price`, `stop_loss`, `price_target`, and `reached` when the price got to one of them) or a 'long_term' idea (with its `score`). For a swing idea, weigh the plan: at or near `price_target`, consider 'profit_sell' unless the setup clearly has more room; below `stop_loss`, the setup has failed, so consider 'risky' (or 'urgent_sell' only if the rubric's urgency test is met). Re-judge the stock on today's data rather than following the plan blindly.

MARKET SESSION:
- The request states the current `market_session`: pre_market, regular, after_hours, overnight, or closed (weekend/holiday).
- When present, `session_price` is the latest price for that session, and `session_price_as_of` / `session_price_age_minutes` show how fresh it is. Prefer it over `current_price` when judging recent moves.
- Outside regular hours, trading is thin and prices may be stale or not yet reflect news. 'urgent_sell' then means acting at the next trading opportunity (e.g. a pre-market order): a material event can still justify it, but a thin after-hours price move on its own should not.

### CHECKS WORKED OUT FOR YOU
`checks` holds facts computed in code from `market_context`, as of `checks.as_of` (its latest daily bar). Apply them as given rather than re-judging them, and judge yourself only what they can't settle: news, fundamentals, analyst and insider activity, and how serious something is. A null check means the data was missing.
- `trend`: "intact" (the price above its 200-day average with the 50-day above the 200-day), "mixed" (one of the two), or "weak" (neither).
- `pullback`: down at least {PULLBACK_ATR:g} ATRs from its 20-day high close (`pullback_from_20d_high_atr`, `_pct`). `recent_run`: up at least {RECENT_RUN_PCT:g}% or {RECENT_RUN_ATR:g} ATRs from its 20-day low (`run_from_20d_low_pct`, `_atr`). `atr` is a normal day's range.
- `turn_signs`, the signs the selling is ending, and `turn_sign_count`: `rsi_rising` (RSI higher than 5 sessions ago and below {TURN_SIGN_RSI_BELOW}); `volume_on_up_days` (10 sessions' up-day volume at least {TURN_SIGN_UP_VOLUME:g} times the down-day volume); `higher_low` (no new 10-day low for {TURN_SIGN_LOW_HELD} sessions, and at least {TURN_SIGN_OFF_LOW_ATR:g} ATR above it); `above_ema_9_or_sar` (a close above EMA 9, or the Parabolic SAR below the price).
- `below_ema_21`, `sar_above_price`, `new_low_in_last_2_sessions`; `stretched` (a close above the upper Bollinger Band, or RSI {STRETCHED_RSI} or more); `rsi_falling_from_high` (RSI down {RSI_FALL_POINTS} or more points from a high of {RSI_HIGH}+ within 5 sessions).
- `sessions_to_earnings` (trading days to the next report) and `next_earnings_date`; `fund` (an ETF or other fund) and `leveraged_fund`.

### RECOMMENDATION RUBRIC
Go through the labels in this order and use the first one that applies.

1. urgent_sell:
   - Immediate, time-sensitive action is required, and waiting for the next digest would likely cost the user materially.
   - Requires a specific, recent, verifiable trigger in the provided data: a thesis-breaking event (fraud or accounting allegations, going-concern or bankruptcy risk, delisting, major regulatory action, guidance collapse, failed key product or trial, a dilutive or unfavorable deal) or a decisive, accelerating technical breakdown (gap down through support or the SAR stop on heavy volume).
   - Never use for slow deterioration, valuation alone, general macro worries, or ordinary volatility within the stock's normal range (compare moves against `natr`).
   - When torn between 'risky' and 'urgent_sell', choose 'risky'.

2. profit_sell: the stock has made a real recent run (`checks.recent_run`) and at least two signs say the run is tiring: `checks.stretched`; `checks.rsi_falling_from_high`; the price at or beyond the analysts' mean target or a prior high; momentum fading (RSI making a lower high while the price makes a higher one); or valuation stretched after the run. It is an opportunistic moment to sell (all or part) and lock in the gain. A stock without a recent run is never 'profit_sell'.

3. risky: the position is genuinely at risk and needs a close eye. Selling to be safe may make sense, though it is not necessarily a good moment to sell (e.g. the price is already depressed). It needs one serious warning or two lesser ones:
   - Serious: consecutive earnings misses or a guidance cut; two or more analyst downgrades, or one with a sharply cut target; news that damages the thesis without meeting the urgent_sell test; or a broken trend that is still falling (`trend` "weak" and `turn_sign_count` 0).
   - Lesser: a short-term downtrend with no sign of a turn (`below_ema_21` and `sar_above_price` with `turn_sign_count` 0); an earnings report within ~5 trading days (`sessions_to_earnings` 5 or less) on a position with meaningful downside exposure; a single analyst downgrade; insider selling well beyond routine sales; contracting margins; an unexplained move larger than twice the stock's normal daily range (`natr`).
   - Not 'risky' on its own: an upcoming earnings report, a dip within an intact trend, any other single lesser warning, or a large daily move within the stock's normal range.

4. buy_more: a pullback (`checks.pullback`) while the long-term `trend` is "intact" or "mixed", with at least {BUY_MORE_TURN_SIGNS} of the `turn_signs`, not `stretched`, no warning from step 3, and an intact thesis (no thesis-breaking news, stable fundamentals): the stock is temporarily cheap and the selling is ending, so now is a good time to add.

5. hold: everything else. The position is healthy or recovering normally, or the evidence is mixed and nothing above applies; give it time to reach its next swing peak.

FUNDS (`checks.fund`, e.g. index and sector ETFs): judge them on their price trend alone; they have no earnings, guidance, or analyst ratings of their own. For a fund, a broken trend that is still falling (`trend` "weak" with `turn_sign_count` 0) or a short-term downtrend with no sign of a turn (`below_ema_21` and `sar_above_price`, `turn_sign_count` 0) is enough for 'risky'.
LEVERAGED ETFs (`checks.leveraged_fund`): they move 2-3 times their index each day by design, so the size of their moves and the leverage itself are never a warning. Apply the fund rule above to their trend.

### SENTIMENT
Separately from the recommendation, rate your overall outlook on the stock itself as 'Bullish', 'Neutral', or 'Bearish'.
- Weigh all of the data: recent headlines and catalysts first, then analyst revisions and insider activity, price trend and technicals, and fundamentals and earnings trends.
- Judge the stock, not the position: ignore the user's cost basis, unrealized gain, and position size.
- It may differ from the recommendation, e.g. 'Bullish' with 'profit_sell' for a stretched winner that should cool off, or 'Bearish' with 'hold' when the concern is still minor.
- Most large companies carry a buy consensus, so analyst ratings alone are not enough for 'Bullish'. Use 'Neutral' when the evidence is mixed or thin.

### RATIONALE
The rationale is read on a phone by an investor, not an analyst. Write at most 2 short sentences:
- Say what the price trend is doing and what that suggests, in plain words: e.g. "falling steadily for two weeks with no sign of a floor yet", "bouncing off its recent low as selling fades", "stretched after a fast run and starting to stall".
- Add the effect of important news or an upcoming event when it matters to the decision (an earnings date, a deal, a downgrade).
- Use indicator names sparingly: one is fine when it's the clearest way to make the point (e.g. "RSI near 30, so it's oversold"), but don't string several together with their values (not "below EMA 9 (290.0) and EMA 21 (289.5) with the PSAR at 301.64"). Use numbers sparingly too, such as a big move ("down 15% since Oct 6") or a date.
- For 'buy_more', say why the decline looks to be ending; for 'profit_sell', why the run looks tired; for 'urgent_sell', what happened and why it can't wait for the next digest.
"""

# User prompt template
USER_PROMPT_TEMPLATE = (
    "Evaluate the held position in {symbol} as of {evaluated_at} (US Eastern, market_session: {market_session}) "
    "and record your recommendation using the provided position and market data:\n\n{holding_prompt_data}"
)


def format_user_prompt(
    symbol: str, evaluated_at: str, holding_prompt_data: str, market_session: str = "unknown"
) -> str:
    """Formats the user prompt with the stock symbol, evaluation time, market session, and json context."""
    return USER_PROMPT_TEMPLATE.format(
        symbol=symbol,
        evaluated_at=evaluated_at,
        market_session=market_session,
        holding_prompt_data=holding_prompt_data,
    )
