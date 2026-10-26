# File with a short note on what each setting in parameters.py does, for the "What it does" column of the settings sheet.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-23, at Andres's request.

src/config/remote_settings.py writes these into the settings sheet next to each setting. Keys are the setting's name as the
sheet shows it (its keys joined by dots); a key with "*" covers a group (fnmatch). A setting without a note here gets
the comment written next to it in parameters.py instead, so a new setting is never left blank; tests/test_remote_settings.py
checks that every setting has a note of its own. parameters.py's comments keep the history and the reasons.
"""
import ast
import io
import re
import tokenize
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path
from typing import Optional

PARAMETERS_PY = Path(__file__).resolve().parent / "parameters.py"
SIGNALS = "buy_more, hold, risky, profit_sell, urgent_sell"

NOTES: dict[str, str] = {
    "ACTIVE_USERS": "The users the digest and the watchdog run for. Set by the sign-up sync from the form's answers, so "
                    "it can't be changed here.",

    # ---------------------------------------------------------------- New-stock data (stage 1 and Finnhub)
    "COMPANY_NEWS_LOOKBACK_DAYS": "Days of company news (Finnhub) fetched for each long-term candidate.",
    "EARNINGS_FUTURE_LOOKAHEAD_DAYS": "How many days ahead to look for each candidate's upcoming earnings date.",
    "FUNDAMENTAL_MONTHS_LOOKBACK": "Months of fundamental data (Finnhub) fetched for each candidate.",
    "EARNINGS_SURPRISE.LIMIT": "How many past quarters of earnings surprises (beat or miss) each candidate gets.",
    "INSIDER_SENTIMENT.DAYS_BACK": "Days of insider buying and selling (Finnhub) fetched for each candidate.",
    "FINNHUB_RATE_LIMIT_SLEEP": "Seconds to wait between Finnhub calls, to spread them within each minute.",
    "FINNHUB_CALLS_PER_MINUTE": "Finnhub calls allowed per minute. 60 is the free plan's limit; raise it with a paid "
                                "plan. Takes effect from the next run.",
    "MKTCAP_MIN": "Stage 1 screen: smallest market cap, in millions of USD (8000 = $8B).",
    "MKTCAP_MAX": "Stage 1 screen: largest market cap, in millions of USD. None = no limit.",
    "VOLUME_MIN": "Stage 1 screen: smallest daily trading volume, in millions of shares.",
    "VOLUME_MAX": "Stage 1 screen: largest daily trading volume, in millions of shares. None = no limit.",
    "LASTSALE_MIN": "Stage 1 screen: lowest share price, in USD.",
    "LASTSALE_MAX": "Stage 1 screen: highest share price, in USD. None = no limit.",
    "REGION": "Stage 1 screen: only stocks from this region. None = any. Takes priority over COUNTRY.",
    "COUNTRY": "Stage 1 screen: only stocks from this country. None = any. Ignored when REGION is set.",
    "SECTOR": "Stage 1 screen: only stocks in this sector. None = any. Takes priority over INDUSTRY.",
    "INDUSTRY": "Stage 1 screen: only stocks in this industry. None = any. Ignored when SECTOR is set.",
    "CLEAR_EXISTING_DATA": "Debugging scripts only: delete earlier CSV exports before writing new ones.",
    "MAX_NEWS_ARTICLES": "Most recent headlines sent to the LLM with each candidate.",
    "YAHOO_FETCH_CONCURRENCY": "Yahoo requests made at once in the stage 2 screen.",
    "YAHOO_FILTERS": "Stage 2 long-term screen (Yahoo): rules a stock must pass, as (field, operator, threshold). "
                     "Margins and growth are fractions (0.07 = 7%).",
    "FINNHUB_FILTERS": "Stage 3 long-term screen (Finnhub): rules as (section, field, operator, threshold); "
                       "percentages are in percent (9 = 9%).",
    "TECHNICAL_INDICATOR_SOURCE": "Where technical indicators come from: yahoo (computed from Yahoo's daily bars: free, no "
                                  "rate limit) or twelvedata (Twelve Data's API, 8 calls a minute on the free plan).",
    "DAILY_BARS_DAYS": "How many recent daily price bars go to the LLM with each stock (ideas and holdings). 0 leaves them out.",
    "TWELVEDATA_FILTERS": "Indicator rules a long-term candidate must pass, as (indicator, interval, operator, threshold, "
                          "options), e.g. ADX at most 45.",
    "TWELVEDATA_CONTEXT": "Indicators sent to the LLM with each candidate (Bollinger Bands, EMA 9 and 21, Parabolic SAR, "
                          "RSI, beta), as (indicator, interval, options).",
    "COMPANY_BASIC_FINANCIALS.METRIC": "Which group of Finnhub's basic financials to fetch: all, margin, valuation, price...",

    # ---------------------------------------------------------------- Gemini
    "GEMINI_MODELS.HOLDING_NEWS": "Gemini model for each holding's news bullets (paid by the user's Gemini key).",
    "GEMINI_MODELS.MACRO_NEWS": "Gemini model for the top macro stories (paid by the admin's shared Gemini key).",
    "GEMINI_CONFIG.effort": "How much Gemini thinks before answering, for every Gemini call: minimal, low, medium, high, "
                            "or default (the model decides). Thinking is most of Gemini's cost.",
    "GEMINI_CONFIG.max_output_tokens": "Most tokens a Gemini answer may use, thinking included: a safety cap on cost. "
                                       "Holding news cut off by it is asked for again one stock at a time.",

    # ---------------------------------------------------------------- LLMs
    "LLM.UNIVERSAL.model": "Model that scores the long-term and swing ideas (admin's shared key). Any model in "
                           "ALLOWED_CLAUDE_MODELS or ALLOWED_DEEPSEEK_MODELS.",
    "LLM.UNIVERSAL.effort": "Thinking effort for scoring the ideas; must be one the model accepts (see ALLOWED_*_MODELS).",
    "LLM.HOLDINGS.model": "Model that reviews every user's holdings and writes the portfolio review (each user's own key).",
    "LLM.HOLDINGS.effort": "Thinking effort for the holdings reviews and the portfolio review; must be one the model accepts.",
    "DEEPSEEK_CONFIG.base_url": "DeepSeek's API address.",
    "DEEPSEEK_CONFIG.effort": "Effort used when a DeepSeek model is chosen without one: none, low, high, or max.",
    "DEEPSEEK_CONFIG.max_tokens": "Most tokens a DeepSeek answer may use, thinking included. None = DeepSeek's default.",
    "DEEPSEEK_CONFIG.max_concurrency": "DeepSeek requests sent at once.",
    "DEEPSEEK_CONFIG.timeout_seconds": "Seconds to wait for a DeepSeek answer (thinking can take minutes).",
    "CLAUDE_CONFIG.max_tokens": "Most tokens a Claude answer may use, thinking included.",
    "CLAUDE_CONFIG.effort": "Effort used when a Claude model is chosen without one: low, medium, high, xhigh, or max.",
    "CLAUDE_CONFIG.max_concurrency": "Claude requests sent at once.",

    # ---------------------------------------------------------------- Picks
    "LONG_TERM_N_AMOUNT": "How many long-term ideas the digest shows (at most).",
    "SWING_TERM_N_AMOUNT": "How many swing ideas the digest shows (at most).",
    "IDEA_RESCORE.ABOVE_MIN": "An idea scoring up to this many points above its list's minimum is scored again before "
                              "it's picked (a close call).",
    "IDEA_RESCORE.BELOW_MIN": "An idea scoring up to this many points below its list's minimum is scored again too.",
    "IDEA_RESCORE.EXTRA_RUNS": "Extra scores for each close call; the middle score is kept. 0 = no rescoring.",
    "HOLDINGS_RECHECK.SIGNALS": f"Holding signals that get a second look before they're shown or alerted (any of {SIGNALS}).",
    "HOLDINGS_RECHECK.EXTRA_REVIEWS": "Extra reviews for those signals; the majority's label wins (a tie goes to the milder). "
                                      "0 = no second look.",
    "MIN_LONG_SCORE": "Lowest score (out of 100) a long-term idea needs to be picked.",
    "MIN_SWING_SCORE": "Lowest score (out of 100) a swing idea needs to be picked.",
    "PICK_SECTOR_RULES.LONG_TERM": "Sector mix of the long-term picks: all_different (no two share a sector), "
                                   "majority_different (more than half from different sectors), or any.",
    "PICK_SECTOR_RULES.SWING": "Sector mix of the swing picks: all_different, majority_different, or any.",
    "PICK_GROUP_BY": "What the sector mix rules compare picks by: industry (finer, about 140) or sector (11).",
    "SWING_MIN_NON_TECH": "Non-tech swing picks (see TECH_SECTORS) a list needs, as [[ideas, non-tech picks], ...]: "
                          "[[4, 1], [8, 2]] means 1 for 4-7 ideas and 2 for 8 or more, if one cleared MIN_SWING_SCORE.",
    "LONG_TERM_MIN_NON_TECH_SHARE": "At least this share of the long-term picks, rounded up, are not tech stocks (see "
                                    "TECH_SECTORS): 0.33 means 1 of 3, 2 of 4-6, 3 of 7-9. None for a list of 1. The "
                                    "rest may be tech.",
    "LONG_TERM_NON_TECH_MIN_SCORE": "Lowest score a non-tech stock needs to be picked under that rule.",
    "TECH_SECTORS": "Yahoo sectors that count as tech for the non-tech rule.",
    "SWING_EARLY_ENTRY.LOOKBACK_DAYS": "Swing picks must be early in their move: their low over this many days is the "
                                       "starting point.",
    "SWING_EARLY_ENTRY.MAX_RUN_ATR": "A swing idea more than this many normal days' ranges (ATR) above that low is "
                                     "left out as too far along.",
    "SWING_EARLY_ENTRY.MAX_RSI": "A swing idea with an RSI above this is left out as stretched.",

    "LONG_TERM_SCREEN.MAX_CANDIDATES": "Most long-term candidates sent to the LLM: the best by the rubric's own figures "
                                       "(valuation, financial health, growth, margins). 0 = send every one the screens "
                                       "let through.",

    # ---------------------------------------------------------------- Swing screen
    "SWING_SCREEN.ENABLED": "Run the swing screen at all (False = no swing ideas).",
    "SWING_SCREEN.YAHOO_FILTERS": "Swing screen's business-quality rules (Yahoo), looser than the long-term ones, as "
                                  "(field, operator, threshold).",
    "SWING_SCREEN.SETUP.MIN_PRICE": "Lowest share price for a swing idea, in USD.",
    "SWING_SCREEN.SETUP.MIN_DOLLAR_VOLUME_M": "Lowest average daily dollar volume (20 days), in millions of USD.",
    "SWING_SCREEN.SETUP.MIN_RSI": "Lowest RSI for a swing setup (lower is in free fall).",
    "SWING_SCREEN.SETUP.MAX_RSI": "Highest RSI for a swing setup (higher is stretched).",
    "SWING_SCREEN.SETUP.MIN_TURN_SIGNS": "Signs of a turn up (out of 4) a setup needs. 0 = none required (setups are "
                                         "ranked by them instead).",
    "SWING_SCREEN.SETUP.EXCLUDE_FALLING_KNIVES": "Leave out a dip that's still falling (RSI also at a 10-day low).",
    "SWING_SCREEN.SETUP.EARNINGS_BLACKOUT_SESSIONS": "Leave out a stock reporting earnings within this many trading days.",
    "SWING_SCREEN.SETUP.PULLBACK.ENABLED": "Look for pullbacks: a dip in a healthy longer trend.",
    "SWING_SCREEN.SETUP.PULLBACK.MAX_BELOW_SMA200_PCT": "A pullback may be at most this % below its 200-day average.",
    "SWING_SCREEN.SETUP.PULLBACK.OR_SMA50_ABOVE_SMA200": "Also allow any depth while the 50-day average is above the 200-day.",
    "SWING_SCREEN.SETUP.PULLBACK.LOOKBACK_DAYS": "The recent high a pullback is measured from: the highest close of these days.",
    "SWING_SCREEN.SETUP.PULLBACK.MIN_PULLBACK_ATR": "A pullback must be at least this many normal days' ranges (ATR) "
                                                    "below that high.",
    "SWING_SCREEN.SETUP.PULLBACK.MAX_PULLBACK_PCT": "...but at most this % below it (more has collapsed).",
    "SWING_SCREEN.SETUP.NEW_UPTREND.ENABLED": "Look for new uptrends: the first days of a turn up.",
    "SWING_SCREEN.SETUP.NEW_UPTREND.CROSS_WITHIN_DAYS": "The 9-day average must have crossed above the 21-day within this "
                                                        "many sessions.",
    "SWING_SCREEN.LEVELS.SUPPORT_LOOKBACK_DAYS": "The trade plan's supports include the low of this many days.",
    "SWING_SCREEN.LEVELS.STOP_BUFFER_ATR": "The stop goes this many ATRs below the nearest support under the price...",
    "SWING_SCREEN.LEVELS.MIN_RISK_ATR": "...and at least this many ATRs below the entry.",
    "SWING_SCREEN.LEVELS.TARGET_LOOKBACK_DAYS": "Targets include the highest close of this many days.",
    "SWING_SCREEN.LEVELS.MIN_TARGET_ATR": "The target is the nearest one at least this many ATRs up...",
    "SWING_SCREEN.LEVELS.MAX_TARGET_ATR": "...capped at this many ATRs up (reachable in 2-8 weeks).",
    "SWING_SCREEN.RISK_REWARD_POINTS": "Risk/reward points (out of 30) at each reward-to-risk ratio, as [ratio, points], "
                                       "with straight lines between them.",
    "SWING_SCREEN.MIN_PLAN_RR": "Plans with less reward per unit of risk than this aren't sent to the LLM.",
    "SWING_SCREEN.MAX_ABOVE_SUPPORT_ATR": "Leave out a stock more than this many ATRs above the support its stop sits under.",
    "SWING_SCREEN.MAX_CANDIDATES": "Most swing plans sent to the LLM (the best by the code's own checks).",

    # ---------------------------------------------------------------- What the LLM and the digest get
    "LLM_PAYLOAD.LONG_TERM": "Sections of each candidate's data sent to the LLM for long-term scoring.",
    "LLM_PAYLOAD.SWING": "Sections of each candidate's data sent to the LLM for swing scoring.",
    "LLM_PAYLOAD.SWING_FUNDAMENTALS": "The few fundamentals a swing call gets, to judge whether a dip changes the business.",
    "RECOMMENDATION_FIELDS.all.*": "Field shown with every idea in the digest: where it comes from in the scored data "
                                   "(section.field).",
    "RECOMMENDATION_FIELDS.top_long_term.*": "Field shown with each long-term idea: where it comes from in the scored "
                                             "data (section.field).",
    "RECOMMENDATION_FIELDS.top_swing.*": "Field shown with each swing idea: where it comes from in the scored data "
                                         "(section.field; .0 is the latest value).",

    # ---------------------------------------------------------------- Holdings, data quality, extras
    "HOLDINGS.STALE_SYNC_HOURS": "Tell the user when their brokerage hasn't synced their holdings for this many hours.",
    "HOLDINGS.HOME_CURRENCIES": "Currencies a user can choose as their home currency.",
    "HOLDINGS.DEFAULT_HOME_CURRENCY": "Home currency for a user who hasn't set a valid one.",
    "UNIVERSAL_DATA_QUALITY.MAX_MISSING_PCT": "Alert the admin when a data source is missing for more than this % of "
                                              "the screened stocks.",
    "UNIVERSAL_EXTRAS.MACRO_NEWS": "Include the top macro stories (Gemini, admin's shared key).",
    "UNIVERSAL_EXTRAS.MACRO_NEWS_STORIES": "How many macro stories.",
    "UNIVERSAL_EXTRAS.MARKET_DASHBOARD": "Include the markets dashboard (indexes, VIX, yields, Fear & Greed, breadth, "
                                         "put/call).",

    # ---------------------------------------------------------------- Market signals (green / yellow / red)
    "MARKET_SIGNALS.KEY_SIGNALS": "Signals shown first in Markets & macro, in this order; the rest are under All indicators.",
    "MARKET_SIGNALS.RELATIVE_WINDOW": "Window for the index comparisons (e.g. Nasdaq-100 vs Dow): 1D, 5D, or 1M.",
    "MARKET_SIGNALS.RELATIVE_GAP_PTS": "A lead of at least this many percentage points counts in those comparisons.",
    "MARKET_SIGNALS.VIX_CALM_BELOW": "VIX under this is calm.",
    "MARKET_SIGNALS.VIX_PANIC_ABOVE": "VIX over this is stress (between the two: elevated).",
    "MARKET_SIGNALS.PUT_CALL_LOW_BELOW": "Put/call ratio under this is over-exuberance (yellow).",
    "MARKET_SIGNALS.PUT_CALL_HIGH_ABOVE": "Put/call ratio over this is fear (red); between the two is normal (green).",
    "MARKET_SIGNALS.FEAR_GREED_EXTREME_FEAR_BELOW": "Fear & Greed under this is extreme fear (red).",
    "MARKET_SIGNALS.FEAR_GREED_FEAR_BELOW": "Fear & Greed under this is fear (yellow).",
    "MARKET_SIGNALS.FEAR_GREED_GREED_ABOVE": "Fear & Greed over this is greed (green).",
    "MARKET_SIGNALS.FEAR_GREED_EXTREME_GREED_ABOVE": "Fear & Greed over this is extreme greed (yellow: froth).",
    "MARKET_SIGNALS.BREADTH_WEAK_BELOW": "% of S&P 500 stocks above their 50-day average: under this is a narrow market (red).",
    "MARKET_SIGNALS.BREADTH_STRONG_ABOVE": "Over this is broad strength (green); between the two is yellow.",
    "MARKET_SIGNALS.DOLLAR_FALL_PCT": "US Dollar Index down this % over 5 days is a tailwind (green).",
    "MARKET_SIGNALS.DOLLAR_FAST_RISE_PCT": "US Dollar Index up this % over 5 days is a headwind (red).",
    "MARKET_SIGNALS.YIELD_MOVE_BPS": "10-year yield up this many basis points over a month is red; down as much is green.",
    "MARKET_SIGNALS.CURVE_RECENT_DAYS": "10Y-2Y spread: yellow when it turned positive within this many days after inverting.",
    "MARKET_SIGNALS.OIL_BREAKOUT_PCT": "Crude oil up this % over 5 days is red (inflation); down as much is yellow.",
    "MARKET_SIGNALS.MOOD_MATRIX.*": "Market mood label for this VIX state and Fear & Greed state, as (label, colour: "
                                    "pos / warn / neg, what it means).",

    # ---------------------------------------------------------------- Costs
    "API_PRICES.AS_OF": "Date the API prices below were last checked.",
    "API_PRICES.SOURCES": "Where the API prices come from.",
    "API_PRICES.ANTHROPIC_CACHE_WRITE_MULTIPLIER": "Anthropic cache writes cost this many times the input price.",
    "API_PRICES.GEMINI_FREE_SEARCH_REQUESTS_PER_MONTH": "Free Google Search requests per Gemini key each month.",
    "API_PRICES.anthropic.*": "This Claude model's prices in USD per million tokens, from each 'from' date, for the "
                              "cost estimates in the digest.",
    "API_PRICES.gemini.*": "This Gemini model's prices in USD per million tokens (search per 1,000), from each 'from' date.",
    "API_PRICES.deepseek.*": "This DeepSeek model's prices in USD per million tokens, peak and off-peak.",
    "API_PRICES.DEEPSEEK_PEAK_HOURS_UTC": "DeepSeek's peak-price hours (UTC, weekdays), as [start, end] pairs.",

    # ---------------------------------------------------------------- Operations
    "OPS.ALERT_USERS": "Users told on Telegram when a run fails (the admins).",
    "OPS.ALERT_COOLDOWN_HOURS": "The same failure is reported at most once per this many hours.",
    "OPS.HOLIDAY_NOTICE": "Tell the admins on Telegram when the US market is closed for a holiday.",
    "OPS.LOG_MAX_MB": "Each run log rotates at this size (MB). Takes effect from the next run.",
    "OPS.LOG_BACKUPS": "Old run logs kept. Takes effect from the next run.",

    # ---------------------------------------------------------------- Watchdog
    "WATCHDOG.SESSION": "When the watchdog checks holdings: regular (market hours), extended (4:00-20:00 ET), or 24_7. "
                        "For every watchdog user, unless <NAME>_WATCHDOG_SESSION is set in .env.",
    "WATCHDOG.TIMEZONE": "The market's timezone, for trading dates and times. Takes effect from the next run.",
    "WATCHDOG.EXCHANGE_CALENDARS": "Exchange calendars; a day is a trading day if any of them trades (XNYS = NYSE, "
                                   "XTSE = Toronto).",
    "WATCHDOG.SESSIONS.*": "The watchdog's session windows: minutes before the open and after the close, and whether "
                           "only trading days count.",
    "WATCHDOG.DEFAULT_INTERVAL_HOURS": "Hours between the watchdog's scheduled checks, for every user who chose the "
                                       "watchdog on the form, unless <NAME>_WATCHDOG_INTERVAL_HOURS is set in .env.",
    "WATCHDOG.MAX_INTERVAL_HOURS": "Longest interval a user may choose.",
    "WATCHDOG.TRIPWIRE_DEFAULT": "Hourly price check between scheduled checks, for every watchdog user, unless "
                                 "<NAME>_WATCHDOG_TRIPWIRE is set in .env.",
    "WATCHDOG.REEVAL_NATR_MULTIPLE": "On a scheduled check, review again a stock that moved at least this many normal "
                                     "days' ranges (NATR).",
    "WATCHDOG.TRIPWIRE_NATR_MULTIPLE": "Between checks, review early a stock that moved at least this many NATRs.",
    "WATCHDOG.MAX_EVALUATION_AGE_HOURS": "Review again anything older than this many hours, even if it hasn't moved.",
    "WATCHDOG.EARNINGS_WINDOW_DAYS": "Review again, at every check, a stock reporting earnings within this many trading "
                                     "days (0 = only on the day itself).",
    "WATCHDOG.EARNINGS_AFTER_TRADING_DAYS": "Review again, at every check, a stock for this many trading days after "
                                            "it reports earnings (0 = off).",
    "WATCHDOG.ALERT_RECOMMENDATIONS": f"Signals that send a Telegram alert (any of {SIGNALS}).",
    "WATCHDOG.ALERT_COOLDOWN_HOURS": "The same alert (user, stock, signal) is sent at most once per this many hours. "
                                     "Takes effect from the next run.",
    "WATCHDOG.ALERT_LOG_RETENTION_DAYS": "Days of alert history kept. Takes effect from the next run.",
    "WATCHDOG.EARNINGS_REMINDER_HOURS": "Telegram reminder when a held stock reports within this many hours (0 = off).",
    "WATCHDOG.MAX_CLAUDE_CALLS_PER_USER_DAY": "Most watchdog LLM reviews per user per day (the digest's aren't counted).",
    "WATCHDOG.FRESH_CONTEXT_MAX_AGE_MINUTES": "Cached quotes and news older than this many minutes are fetched again.",
    "WATCHDOG.CONTEXT_CACHE_RETENTION_DAYS": "Cached market data for a stock not refreshed in this many days is dropped.",
    "WATCHDOG.MAX_PARALLEL_USERS": "Users the watchdog checks at the same time.",

    # ---------------------------------------------------------------- Digest
    "DIGEST.CATALYST_WINDOW_DAYS": "Holding cards list earnings dates within this many days.",
    "DIGEST.SYMBOL_NEWS_BULLETS": "Most news stories on each holding card.",
    "DIGEST.SYMBOL_NEWS_MIN_PRIORITY": "Lowest news priority shown: HIGH, MEDIUM, or LOW.",
    "DIGEST.SYMBOL_NEWS_LOOKBACK_DAYS": "How many days back Gemini searches for each holding's news.",
    "DIGEST.NEWS_FULL_UP_TO_HOLDINGS": "With more holdings than this, cards show NEWS_LARGE_PORTFOLIO_BULLETS stories "
                                       "each, to keep the email under Gmail's size limit.",
    "DIGEST.NEWS_LARGE_PORTFOLIO_BULLETS": "Stories per card in a large portfolio.",
    "DIGEST.NEWS_FULL_SIGNALS": "Holdings with these signals keep all their stories in a large portfolio.",
    "DIGEST.PORTFOLIO_REVIEW_BULLETS": "Most notes in the whole-portfolio review.",
    "DIGEST.PORTFOLIO_REVIEW_MAX_TOKENS": "Most tokens the portfolio review's answer may use, thinking included.",
    "DIGEST.SYMBOL_NEWS_BATCH_SIZE": "Holdings covered by one Gemini news request (1 = a request per holding).",
    "DIGEST.NEWS_REUSE.ENABLED": "Show news fetched earlier the same day (the morning digest's) again instead of asking "
                                 "Gemini, unless the stock moved, its signal changed, or it reported.",
    "DIGEST.NEWS_REUSE.MAX_MOVE_NATR": "A holding that moved more than this many normal days' ranges (NATR) since gets "
                                       "fresh news.",
    "DIGEST.SYMBOL_NEWS_CONCURRENCY": "Gemini news requests sent at once per user.",
    "DIGEST.MAX_PARALLEL_USERS": "Users whose digests are built at the same time.",
    "DIGEST.REUSE_EVALUATION_MAX_AGE_MINUTES": "Reuse a watchdog review this recent instead of reviewing again (0 = off).",
    "DIGEST.PRE_MARKET_TIME_ET": "When the Pre-Market digest goes out (US Eastern, HH:MM).",
    "DIGEST.POST_MARKET_DELAY_MINUTES": "The Post-Market digest goes out this many minutes after the close.",
    "DIGEST.POST_MARKET_SEND_WINDOW_MINUTES": "A missed Post-Market digest is still sent for this many minutes after it was due.",
    "DIGEST.AMP_EMAIL": "Also send the AMP version, whose dropdowns work in the Gmail app.",
    "DIGEST.HEAT_MAP_URL": "Link of the heat map button under the greeting.",
    "DIGEST.HEAT_MAP_LABEL": "Text of the heat map button.",
    "DIGEST.CHART_DIRECT_USERS": "Users whose email links the chart picture's own address (no redirects), in case "
                                 "charts don't show in Gmail. '*' = everyone. About 200 bytes more per chart.",
    "DIGEST.GLOSSARY_URL": "Web page of the glossary. When set, the email links to it instead of carrying it (~10 KB).",
    "DIGEST.INDICATOR_LINKS.*": "Where this market signal's name links in the email.",
    "DIGEST.OPEN_SECTIONS": "Email sections that start open (any of risk, markets, holdings, swing, long_term); the rest "
                            "start collapsed.",

    # ---------------------------------------------------------------- Models
    "CLAUDE_REFUSAL_FALLBACK": "When Claude declines a request, let the API retry it on a fallback model.",
    "CLAUDE_PROMPT_CACHE": "Reuse the Claude evaluations' shared instructions from Anthropic's prompt cache (cheaper).",
    "CLAUDE_FALLBACK_DEFAULT_MODELS": "Claude models that use that fallback.",
    "ALLOWED_CLAUDE_MODELS.*": "A Claude model that LLM may use, with the efforts it accepts.",
    "ALLOWED_DEEPSEEK_MODELS.*": "A DeepSeek model that LLM may use, with the efforts it accepts (none = no thinking).",
}

TAG = re.compile(r"\(?Claude-authored(?: \([^)]*\))?[,:]?\s*")


def own_note(name: str) -> Optional[str]:
    """The note written for `name` here (its own, else the first group pattern that matches), or None."""
    if name in NOTES:
        return NOTES[name]
    return next((note for pattern, note in NOTES.items() if "*" in pattern and fnmatchcase(name, pattern)), None)


@lru_cache(maxsize=1)
def _comments() -> dict[str, str]:
    """{setting name: the comment next to it in parameters.py}, for settings without a note here."""
    source = PARAMETERS_PY.read_text(encoding="utf-8")
    lines = source.splitlines()
    comments = {}  # line number -> (text, whether code comes before it on the line)
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            line, column = token.start
            comments[line] = (token.string.lstrip("#").strip(), bool(lines[line - 1][:column].strip()))
    tree = ast.parse(source)
    root = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", "") == "params_dict")
    found = {}

    def walk(node: ast.Dict, prefix: str) -> None:
        for key, value in zip(node.keys, node.values):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                continue
            name = f"{prefix}{key.value}"
            above, line = [], key.lineno - 1
            while line in comments and not comments[line][1]:
                above.insert(0, comments[line][0])
                line -= 1
            beside = [comments[n][0] for n in range(key.lineno, value.end_lineno + 1) if n in comments and comments[n][1]]
            found[name] = re.sub(r"\s+", " ", TAG.sub("", " ".join(above + beside))).strip()
            if isinstance(value, ast.Dict):
                walk(value, f"{name}.")

    walk(root, "")
    return found


def note_for(name: str) -> str:
    """What the setting does: its note here, else its parameters.py comment (or its section's), cut to 400 characters."""
    note = own_note(name)
    if note:
        return note
    parts = name.split(".")
    for end in range(len(parts), 0, -1):
        comment = _comments().get(".".join(parts[:end]))
        if comment:
            return comment if len(comment) <= 400 else comment[:397].rsplit(" ", 1)[0] + "..."
    return ""
