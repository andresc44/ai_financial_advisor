# Holdings watchdog and per-user model selection

The watchdog re-evaluates each user's holdings on that user's own schedule and sends a Telegram alert when Claude recommends `urgent_sell`. Each user also picks which Claude model and effort level evaluates their holdings.

**Holdings signals** (changed 2026-10-18; `src/personalized/LLM_hold_instr.py`):

| Signal | Meaning |
|---|---|
| `buy_more` | The stock is rebounding: it has pulled back, looks cheap, and the data suggests it will go back up, so now is a good time to add. Not for a stock that has already run for weeks, or one still falling with no floor. |
| `hold` | Healthy. Give it time to reach its next swing peak before selling. |
| `risky` | Something looks suspicious and needs close watching. Selling to be safe may make sense, but now isn't necessarily a good time to sell. |
| `profit_sell` | It has risen recently, but the indicators say the run may turn down soon: an opportunistic time to sell. |
| `urgent_sell` | Unchanged: a specific, time-sensitive problem. The only signal that sends a Telegram alert. |

They replace `hold` / `trim` / `risk_alert` / `urgent_sell`. Labels saved before the change are read as today's (`risk_alert` as `risky`, `trim` as `profit_sell`), so the watchdog state, reused evaluations, and older payloads keep working. The track record keeps each label as it was given.

**Since 2026-10-22 the labels are decided from facts worked out in code** (`src/personalized/holding_checks.py`, added to each holding as `checks`): the long-term trend, a pullback from the 20-day high or a run from the 20-day low, the four signs of a turn, whether the price is under EMA 21 and its trend stop, whether it is stretched, trading days to the next report, and whether it is a fund or leveraged ETF. The model checks the labels in a fixed order (urgent sell, profit sell, risky, buy more, then hold) with stated thresholds: `risky` needs one serious warning or two lesser ones, and an upcoming report is a lesser one; funds, leveraged ETFs included, are judged on their trend alone. Every user's holdings are reviewed by Claude Haiku 5.5 at low effort (`LLM.HOLDINGS`): the sign-up form no longer asks for a model, and the old `<NAME>_HOLDINGS_LLM_MODEL` / `_EFFORT` settings are ignored. Why and how it was tested: `docs/model_selection.md`.

In the email, Buy more is light blue, Hold a deeper blue, and Profit sell green (since 2026-10-25); Buy more comes after the sell-side signals. It gets a "Needs attention" chip, but isn't counted as Flagged, which means a reason to sell or watch.

> **Authorship:** this feature was designed with and written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-13, at Andres's request. [Authorship](#authorship) lists every file Claude wrote or changed and why. In code, Claude's work is marked with an `Authorship:` docstring (new files) or a `Claude-authored (2026-10-13)` comment (changes to existing files), so `grep -rn "Claude-authored\|Authorship:" src/` finds all of it.

## How one run works

Cron starts `src/telegram_alert/hourly_watchdog.py` every hour at :45. For each user in `ACTIVE_USERS` (`src/config/parameters.py`, the same list the digest uses) with a holdings source (`SNAPTRADE` or `SHEETS`) and a Telegram chat ID, it checks the gates below in order, cheapest first, and stops at the first one that says skip:

1. **Watchdog on?** `watchdog_interval_hours` is above 0.
2. **In session?** The time falls inside the user's session (`regular`, `extended` or `24_7`), using real exchange calendars, so holidays and 13:00 half-days are handled.
3. **Due or tripwire?** The user is *due* N hours after their last successful scheduled check or digest (see [Schedules](#schedules)). If they aren't due and the tripwire is off, nothing happens this hour.
4. **Cheap snapshot.** Holdings, P&L, RSI and NATR, plus a price suited to the market phase (see [Prices outside regular hours](#prices-outside-regular-hours)). No Finnhub, Twelve Data or Claude calls. If the brokerage returns nothing, the run stops without marking the check as done, because an outage looks the same as an empty portfolio.
5. **Choose stocks.** Only stocks that changed get a new evaluation (see [When Claude is called](#when-claude-is-called)). Every other stock keeps its last recommendation.
6. **Daily cap.** At most `MAX_CLAUDE_CALLS_PER_USER_DAY` watchdog evaluations per user per day. When the cap binds, the stocks whose last recommendation was most severe go first.
7. **Evaluate.** Market context (from the daily cache) plus Claude, run only for the chosen stocks.
8. **Alert.** One Telegram message covers every held stock whose latest recommendation is an alert level and isn't in cooldown.

Errors are contained per user: one user's failure is printed with a traceback, and the other users still run.

### Parallel users (watchdog and digest)

Both the watchdog and the digest process several users at once (`MAX_PARALLEL_USERS`), and the digest also builds the universal content at the same time. Inside one user's digest, the Gemini news (several holdings per request, several requests at a time; since 2026-10-23 news fetched earlier the same day is reused, see [Holding news](#holding-news-and-the-portfolio-review-2026-10-23)) runs while market data and Claude run, and the portfolio review waits for both. What keeps this safe:

- **Shared data keys:** Finnhub and Twelve Data use one key each, so fetching from them is serialized by one lock (`src/ops/shared_locks.py`). The request rate stays the same as a single-user run, and a user who waited finds symbols another user just fetched in the market-context cache.
- **Shared files:** `data/watchdog_state.json` updates take an in-process lock, and the market-context cache is only read and written under the lock above. The crontab `flock` still keeps the watchdog and digest processes apart.
- **Per-user keys:** Claude, Gemini, SnapTrade, and Telegram calls use each user's own credentials, so they run fully in parallel.
- Log lines from different users interleave; watchdog lines start with `[user]`.

## Settings

Since 2026-10-23 every setting in `src/config/parameters.py` can also be changed from the sign-up spreadsheet, without editing the file: see [Changing settings from the spreadsheet](#changing-settings-from-the-spreadsheet-2026-10-23).

### Global (`src/config/parameters.py`)

| Key | Default | Meaning |
|---|---|---|
| `ACTIVE_USERS` | Synced from the sign-up sheet (`src/users/import_signup.py`, 2026-10-22) | Users the digest **and** the watchdog run for. Each needs a profile in `user_profiles.py`; unknown names are skipped with a warning. Their `.env` settings still decide which digests they get and whether the watchdog runs for them. |
| `WATCHDOG.SESSION` | `"extended"` | Default session for all users (2026-10-26; was `"regular"`). Since that day the sign-up form only asks whether a user wants the watchdog: the interval, session, and tripwire are these defaults unless `<NAME>_WATCHDOG_INTERVAL_HOURS` / `_SESSION` / `_TRIPWIRE` are set in `.env`, which the sign-up sync leaves alone. |
| `WATCHDOG.SESSIONS` | see file | `pre_open_minutes` / `post_close_minutes` are measured from each day's actual open and close. `extended` = 330 / 240, which is 4:00–20:00 ET on a full day. Set `pre_open_minutes` to 150 to start at 7:00. |
| `WATCHDOG.EXCHANGE_CALENDARS` | `["XNYS", "XTSE"]` | A day counts as a trading day if **any** listed exchange trades, so US Thanksgiving is a trading day because the TSX is open. |
| `WATCHDOG.DEFAULT_INTERVAL_HOURS` | `4` | Hours between scheduled checks. |
| `WATCHDOG.TRIPWIRE_DEFAULT` | `True` | Hourly price check between scheduled checks. |
| `WATCHDOG.REEVAL_NATR_MULTIPLE` | `1.0` | On a scheduled check, re-evaluate a stock that moved at least this many NATRs since its last evaluation. |
| `WATCHDOG.TRIPWIRE_NATR_MULTIPLE` | `2.0` | Between scheduled checks, force an evaluation at this many NATRs. |
| `WATCHDOG.MAX_EVALUATION_AGE_HOURS` | `24` | On a scheduled check, re-evaluate anything older than this. Also the maximum age of an evaluation that can still trigger an alert. |
| `WATCHDOG.EARNINGS_WINDOW_DAYS` | `7` | On a scheduled check, re-evaluate when earnings fall within this many days. |
| `WATCHDOG.EARNINGS_AFTER_TRADING_DAYS` | `1` | And for this many trading days after a report (2026-10-26; 0 = off). |
| `WATCHDOG.ALERT_RECOMMENDATIONS` | `["urgent_sell"]` | Recommendations that send Telegram alerts. |
| `WATCHDOG.ALERT_COOLDOWN_HOURS` | `24` | No repeat alert for the same (user, symbol, recommendation) within this window. |
| `WATCHDOG.MAX_CLAUDE_CALLS_PER_USER_DAY` | `40` | Cap on watchdog Claude calls per user per market day. Digest calls don't count. |
| `WATCHDOG.FRESH_CONTEXT_MAX_AGE_MINUTES` | `30` | Cached quote and news older than this are re-fetched. |
| `WATCHDOG.MAX_PARALLEL_USERS` | `4` | Users checked at the same time each hour. |
| `DIGEST.MAX_PARALLEL_USERS` | `4` | Users whose digest content is built at the same time, alongside the universal content. |
| `DIGEST.SYMBOL_NEWS_BULLETS` | `2` | Gemini news stories per holding card (3 before 2026-10-20). |
| `DIGEST.SYMBOL_NEWS_MIN_PRIORITY` | `"MEDIUM"` | Lowest story priority kept (added 2026-10-20): LOW stories, such as routine commentary and recaps, are left out. |
| `DIGEST.SYMBOL_NEWS_CONCURRENCY` | `4` | Gemini news requests in flight at once per user. Lower it if a free-tier Gemini key keeps hitting per-minute limits. |
| `DIGEST.SYMBOL_NEWS_BATCH_SIZE` | `3` | Holdings covered by one Gemini news request (2026-10-23; was one request each, then 5). |
| `DIGEST.NEWS_REUSE` | `{"ENABLED": True, "MAX_MOVE_NATR": 1.0}` | Show news fetched earlier the same US Eastern day again instead of asking Gemini (2026-10-23). |
| `DIGEST.PORTFOLIO_REVIEW_MAX_TOKENS` | `8000` | Cap on the portfolio review's answer, thinking included (2026-10-23). |
| `GEMINI_CONFIG` | `{"effort": "low", "max_output_tokens": 8192}` | Thinking level and output cap for every Gemini call: holding news and macro news (2026-10-23). |
| `DIGEST.REUSE_EVALUATION_MAX_AGE_MINUTES` | `60` | The digest reuses an evaluation this recent (if the stock moved less than `REEVAL_NATR_MULTIPLE` NATRs since) instead of calling Claude again. `0` turns reuse off. |
| `ALLOWED_CLAUDE_MODELS` | see file | Models a user may pick, each with the effort levels it accepts. |
| `CLAUDE_REFUSAL_FALLBACK` | `True` | On models that support it, a request refused by a safety classifier is re-run server-side on a recommended model. |

### Per user (`.env`, mapped through `src/personalized/user_profiles.py`)

The profile maps each setting to an `.env` key, the same pattern as the existing keys. An unset value uses the global default. An invalid value prints a warning and uses the default, so it never crashes the run.

| Profile key | `.env` example | Allowed values |
|---|---|---|
| `watchdog_interval_hours` | `ANDRES_WATCHDOG_INTERVAL_HOURS=4` | `0` (off) to `168` |
| `watchdog_session` | `ANDRES_WATCHDOG_SESSION=extended` | `regular`, `extended`, `24_7` |
| `watchdog_tripwire` | `ANDRES_WATCHDOG_TRIPWIRE=true` | `true` / `false` (also `yes`/`no`, `1`/`0`) |
| `holdings_llm_model` | `ANDRES_HOLDINGS_LLM_MODEL=deepseek-flash` | any key of `ALLOWED_CLAUDE_MODELS` or `ALLOWED_DEEPSEEK_MODELS` (default `LLM["HOLDINGS"]`) |
| `holdings_llm_effort` | `ANDRES_HOLDINGS_LLM_EFFORT=low` | Claude: `low` `medium` `high` `xhigh` `max`; DeepSeek: `none` (no thinking) `low` (the default) `high` `max` |
| `holdings_claude_model` / `holdings_claude_effort` | `ANDRES_HOLDINGS_CLAUDE_MODEL=claude-sonnet-5-5` | older names, still read when the `LLM` ones above are unset |
| `receive_holding_news` | `ANDRES_RECEIVE_HOLDING_NEWS=True` | `True` / `False` (default `True`): Gemini news bullets for each holding |
| `long_recs` / `swing_recs` | `MAURICIO_LONG_RECS=5` | `1` to `10`: the most long-term / swing ideas their digest shows (default `LONG_TERM_N_AMOUNT` / `SWING_TERM_N_AMOUNT`). Added 2026-10-24; `.env` only, not on the sign-up form. Each size is picked from the same scores (no extra LLM cost, beyond re-scoring close calls for the longest list) |
| `anthropic_api_key` | `ANDRES_ANTHROPIC_API_KEY=sk-ant-...` | the user's own Anthropic key (required when their model is Claude) |
| `deepseek_api_key` | `ANDRES_DEEPSEEK_API_KEY=sk-...` | the user's own DeepSeek key (required when their model is DeepSeek) |
| `gemini_api_key` | `ANDRES_GEMINI_API_KEY=...` | the user's own Gemini API key, used for the per-holding news (only needed with the news on since 2026-10-23, when the portfolio review moved to the holdings model) |
| `home_currency` (added 2026-10-21) | `ANDRES_HOME_CURRENCY=CAD` | `HOLDINGS.HOME_CURRENCIES`: `USD` (the default, `HOLDINGS.DEFAULT_HOME_CURRENCY`), `CAD`, `EUR`, `GBP`, `AUD`, `COP`. The email's overview shows the portfolio in it, and also in USD |
| `holdings_source` | `KOBY_HOLDINGS_SOURCE=NO_HOLDINGS` | `SNAPTRADE`, `SHEETS`, or `NO_HOLDINGS` (an ideas-only user, below) |

If the model doesn't accept the effort, the effort falls back to `LLM["HOLDINGS"]`'s effort when that's the same model, and otherwise to the provider's default (`CLAUDE_CONFIG` / `DEEPSEEK_CONFIG` `"effort"`). The model and effort apply wherever holdings are evaluated: watchdog runs and the digest.

#### Ideas-only users (added 2026-10-21)

A user with `<NAME>_HOLDINGS_SOURCE=NO_HOLDINGS` has no holdings connected and gets the rest of the digest: the markets, the macro news, and the swing and long-term ideas.
- **What they need in `.env`:** `<NAME>_EMAIL` and the two `<NAME>_RECEIVE_*_DIGEST` flags. `<NAME>_TIMEZONE` is optional and sets the greeting's time of day (US Eastern without it). No API keys: nothing is fetched or evaluated for them, so they add no cost.
- **Their email:** no overview card, portfolio risk, or holdings. The glossary leaves out the holdings-only terms (the five signals, unrealized P/L, weight, flagged, volatility, leveraged ETFs).
- **The watchdog** skips them, as it skips anyone without a holdings source.
- **Code:** `personalized_consolidated.get_ideas_only_content`, called from `DigestPayload.personal_content`.

The API keys are the only settings with no default: they never fall back to the shared `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / `GEMINI_API_KEY` used for the universal content. If a user's LLM key is unset, their holdings get no recommendation; if their Gemini key is unset, their holding cards get no news bullets. Either way an `[ERROR]` line is printed and other users are unaffected.

#### Choosing the LLM: Claude or DeepSeek (added 2026-10-16)

Every job that took a Claude model can take a DeepSeek model instead. `src/llm/llm_models.py` picks the provider from the model name, and `src/llm/deepseek_client.py` makes the DeepSeek calls.

| Job | Set in | Key |
|---|---|---|
| New-stock scoring (~37 calls a run) | `UNIVERSAL_LLM_MODEL` / `UNIVERSAL_LLM_EFFORT` in `.env` (added 2026-10-17), else `LLM["UNIVERSAL"]` in `parameters.py` | shared `ANTHROPIC_API_KEY` or `DEEPSEEK_API_KEY` |
| Holdings evaluation (watchdog and digest) | `LLM["HOLDINGS"]`, overridden per user by `<NAME>_HOLDINGS_LLM_MODEL` / `_EFFORT` | the user's own `<NAME>_ANTHROPIC_API_KEY` or `<NAME>_DEEPSEEK_API_KEY` |

**DeepSeek models** (checked 2026-10-16):
- **`deepseek-flash`** (V4.1-Flash): cheap and fast.
- **`deepseek-v4-pro`**: the stronger tier.

DeepSeek retired R1 and V3. Their difference is now the effort setting on either model: `none` turns thinking off (like V3 / `deepseek-chat`), and `low` / `high` / `max` turn it on (like R1 / `deepseek-reasoner`).

**What stays the same:**
- DeepSeek calls use the same tool schema and validation as Claude's. DeepSeek has no strict mode, so its answers are repaired first (below).
- They retry rate limits and outages the same way, and stop at the first key error (wrong key, empty balance).
- The pre-run key check is free: it reads the account balance.

**Costs:**
- **Pricing:** DeepSeek prices are in `API_PRICES["deepseek"]`. Off-peak calls cost half; peak is `DEEPSEEK_PEAK_HOURS_UTC`, weekdays, and the digest and the watchdog run off-peak.
- **In the digest:** each payload's `api_usage` has a `deepseek` block.
- **In the track record:** it keeps the model behind each pick, so Claude and DeepSeek can be compared.

Estimated per run of ~37 candidates, from the 2026-10-16 run's token counts:

| Model | Per run |
|---|---|
| `claude-sonnet-5-5` | ~$1.23 |
| `deepseek-v4-pro` | ~$0.38 |
| `deepseek-flash` (`none`) | ~$0.09 |

Thinking adds output tokens.

**Measured on 2026-10-17** (the same 37 candidates, Post-Market):

| | `claude-sonnet-5-5` | `deepseek-flash` (`none`) |
|---|---|---|
| New-stock scoring | $1.23 | $0.06 |
| Andres's 10 holdings | $0.27 | $0.01 |
| Scored | 36 of 37 | 31 of 37 (before the repair below) |
| Long-term picks | NVDA 94.5, TSM 92.5 | TSM 96, NVDA 93.5 |
| Holdings | | Same signal on 8 of 10 |

Since then `LLM["UNIVERSAL"]` is `deepseek-flash`, and so is Andres's `ANDRES_HOLDINGS_LLM_MODEL`; both use effort `low` since 2026-10-19 (below).

**Low thinking** (2026-10-19). A DeepSeek model now thinks at effort `low` unless an effort is set: `DEEPSEEK_CONFIG["effort"]`, `LLM["UNIVERSAL"]`, and Andres's `.env`. Without thinking, the same stock's swing score could move by 10 points or more from one run to the next. Measured on 8 candidates, each scored twice per setting:

| `deepseek-flash` | `none` | `low` |
|---|---|---|
| Swing score change between runs | 9.9 on average, up to 31.5 | 3.5 on average, up to 6.1 |
| Long-term score change between runs | 2.7, up to 7.0 | 3.4, up to 6.5 |
| Output tokens per stock | ~540 | ~3,000 (mostly thinking) |
| Time for 8 stocks at once | 5 s | 23 to 31 s |

The first full run with it (2026-10-19) scored 95 candidates for $0.28 instead of ~$0.11, and took 27 minutes instead of ~17. Andres's 10 holdings cost $0.02. Setting the effort to `none` turns thinking off again.

**Setting the scoring model in `.env`.** `UNIVERSAL_LLM_MODEL` and `UNIVERSAL_LLM_EFFORT` override `LLM["UNIVERSAL"]`, so each machine can choose without editing `parameters.py`:
- **Model:** any key of `ALLOWED_CLAUDE_MODELS` or `ALLOWED_DEEPSEEK_MODELS`. A model that isn't allowed is ignored with a warning, and `parameters.py` is used.
- **Effort:** if it's unset or the model doesn't accept it, the effort is `parameters.py`'s when that's the same model, and otherwise the provider's default (`low` for DeepSeek).
- **Checking:** `python -m src.config.check_config` shows the model in use, and reports a set but unusable value as INVALID.

**Misshapen answers** (`validated_answer` in `src/llm/deepseek_client.py`, added 2026-10-17). Without thinking, `deepseek-flash` sometimes breaks the schema's shape: in that run it nested the swing section inside `long_term` (4 stocks) or added keys like `valuation_score_note` (2), and those stocks were never scored.
- **Repair:** `repair_arguments` moves a section back beside its sibling and drops keys the schema doesn't allow, using the tool's schema. It never changes values, so an out-of-range score still fails validation. Each fix is logged as a warning, e.g. "Fixed the answer's shape: moved long_term.swing to swing".
- **One retry:** an answer that is missing, not JSON, or still invalid is asked for once more (`INVALID_ANSWER_RETRIES`). One cut off at `max_tokens` or withheld by the content filter isn't, since it would fail the same way. Both answers are billed.
- **Scope:** new-stock scoring and holdings evaluations on DeepSeek. Claude's strict tool use already keeps its answers in shape.

**Privacy:** with a DeepSeek model, the prompts go to DeepSeek's servers. For holdings, that includes the user's positions.

#### When holdings can't be read (`PortfolioFetcher.holdings_issues`)

A broken holdings source used to look exactly like an empty portfolio. Now each problem is recorded as `{source, reason, message}` and sent to the user's Telegram once per `ALERT_COOLDOWN_HOURS` (the watchdog and digest share the cooldown), and the digest payload lists it under `data_source_issues`:

| Source | Reasons |
|---|---|
| SnapTrade | `api_error` (unreachable or keys rejected), `no_accounts`, `connection_disabled` (the brokerage needs the user to log in again through SnapTrade), `positions_error` (one account's positions failed), `stale_sync` (holdings not synced for `HOLDINGS.STALE_SYNC_HOURS`, default 96) |
| Google Sheets | `sheet_unavailable` (wrong ID or not shared with the service account), `sheet_empty`, `sheet_invalid` (missing `symbol`, `shares_qty`, or `avg_purchase_price`) |

Holdings that could still be read are evaluated as usual.

#### When a key stops working (`src/personalized/api_errors.py`)

Missing, invalid, revoked, expired, unpermitted, out-of-credit, spend-limited, and quota-exhausted keys are expected situations, not crashes:

| Where | What happens |
|---|---|
| Watchdog | Before any Finnhub / Twelve Data / Claude spend, the Anthropic key gets a free check (lists one model). A missing, invalid, expired, or unpermitted key stops there. An empty credit balance or a spend limit is only detectable on the first Claude request; the remaining requests are then skipped. Only evaluations that actually ran count toward `MAX_CLAUDE_CALLS_PER_USER_DAY`, and the scheduled check is not marked done, so it is retried next hour. The user gets one Telegram notice per `ALERT_COOLDOWN_HOURS` saying what to fix. |
| Digest | The same Claude handling (market data is still fetched, because the holding cards use it). For Gemini, the news requests stop at the first key failure (keeping news already fetched). The portfolio review uses the holdings model's key (2026-10-23), so it's skipped without a request when the holdings evaluation already found that key unusable. The payload's `api_key_issues` list says which key failed and why. |
| Gemini retries | Only timeouts, connection errors, per-minute 429s (honoring the API's retry delay), and 5xx are retried, up to 4 attempts. Key, billing, daily-quota, and other 4xx errors fail immediately. A 429 that survives the retries stops the news requests for that run. |

#### Who is told how to fix it (`src/users/admins.py`, 2026-10-17)

Only the admins (`OPS.ALERT_USERS`, Andres) can change `.env`, the provider accounts, or the SnapTrade and sheet setup. So key and holdings notices are worded for their reader, in Telegram and in the email:
- **An admin** gets the problem, the fix, and the API's own message, e.g. "Gemini API key for 'Andres' has expired. Create a new key in Google AI Studio and update ANDRES_GEMINI_API_KEY in .env."
- **Everyone else** gets the problem and "Contact Andres to address this issue.", e.g. "Your Gemini API key has expired. Contact Andres to address this issue." A rate limit, which clears by itself, says it will be retried instead.
- **Shared data sources** (the universal content's Finnhub, Twelve Data, Yahoo, and shared keys): an admin's email lists each problem. Anyone else's shows one plain line naming the sources, plus the contact line. The admins are alerted about these separately.
- **The run log** always has the full text.

In the payload, `api_key_issues` and `data_source_issues` carry both texts: `message` (the admin's) and `user_message` (worded for the user whose digest it is).

## Schedules

Since 2026-10-25 (Andres) a check is due **N hours after the user's last check or digest**, not on fixed blocks (`src/market/watchdog_schedule.py`, `check_due`). With cron at :45 for the watchdog and :10 for the digest:
1. **A digest counts as a check** at the next :45 (7:10 counts as 7:45). The next check is due N hours after the later of that and the last watchdog check, at the first run on or after it. The digest's actual send time is recorded per user (`last_digest_at` in `data/watchdog_state.json`).
2. **The first check of each market day** is due at the first run in the session, whatever yesterday did. A digest before the session opens (7:10, for a regular session from 9:30) resets the clock like any other, so a regular 4-hour user has no 9:45 check.
3. **Skipped before a digest:** a due check is skipped when a digest is due to run within 1.5 hours after it (not for an interval of 1 hour).
4. **Last check of the session:** when the next check would fall after the close, one runs at the last :45 before it instead (19:45 for `extended`), unless a check or digest ran within the 1.5 hours before it (18:15 or later).
5. **`24_7`:** no session, so no first or last check; the clock rolls on from the last check or digest, and the skip before a digest still applies. No digest on weekends, so no reset.
6. **Unchanged:** changing a user's interval or session makes them due at once, and a check stopped by a key problem is retried next run.
7. **Tripwire:** works as before, except that a user whose digest went out less than an hour ago gets no tripwire pass (the digest just evaluated every holding).

Check times on a full trading day with a pre-market digest at 7:10 and a post-market one at 16:10 (a digest can run a little later if it was retried):

| Interval | `regular` (9:30–16:00) | `extended` (4:00–20:00) |
|---|---|---|
| 1 | 9:45 … 15:45 (7 checks) | 4:45–6:45, 8:45–15:45, 17:45–19:45 |
| 2 | 9:45, 11:45, 13:45 | 4:45, 9:45, 11:45, 13:45, 18:45 |
| 3 | 10:45, 13:45 | 4:45, 10:45, 13:45, 19:45 |
| 4 | 11:45 | 4:45, 11:45, 19:45 |
| 6 | 13:45 | 4:45, 13:45, 19:45 |
| 8 or more | none (the clock from the 7:10 digest runs out at 15:45, just before the 16:10 digest) | 4:45, 19:45 |

Switching to this schedule gave every user one extra check on its first run (the old state had no `last_check`).

## When Claude is called

**On a scheduled (due) check**, a stock is evaluated if any of these is true:
- it has never been evaluated;
- its last evaluation is older than `MAX_EVALUATION_AGE_HOURS`;
- it moved at least `REEVAL_NATR_MULTIPLE` × NATR since its last evaluation;
- earnings are within `EARNINGS_WINDOW_DAYS`;
- it reported earnings 1 to `EARNINGS_AFTER_TRADING_DAYS` trading days ago (2026-10-26, default 1; the report is the state's `last_earnings_date`, kept by `record_evaluations` once the next date moves on, or a `next_earnings_date` already past);
- **24_7 only**, while the market is overnight or closed: Finnhub has headlines newer than the last evaluation (one Finnhub call per stock).

**On any run with the tripwire on**, a stock is evaluated if:
- it moved at least `TRIPWIRE_NATR_MULTIPLE` × NATR; or
- its price fell below the SAR stop recorded at its last evaluation.

NATR is the stock's typical daily move as a % of price, so the thresholds adapt to each stock's volatility. Stocks with no NATR (no price history) can still be re-evaluated through the age and earnings triggers.

A scheduled check where nothing changed evaluates nothing. That's intended, and it's what keeps costs bounded.

## Prices outside regular hours

| Market phase | Price used (`session_price`) |
|---|---|
| `pre_market`, `after_hours` | Latest extended-hours trade from 1-minute yfinance bars (`prepost=True`), with a timestamp |
| `regular`, `overnight`, `closed` | `current_price` from SnapTrade or Sheets |

`session_price`, `session_price_as_of` and `session_price_age_minutes` are passed to Claude together with the `market_session`. The prompt (`src/personalized/LLM_hold_instr.py`) tells Claude that outside regular hours, `urgent_sell` means "act at the next trading opportunity", and that thin after-hours moves alone don't justify it.

## Daily market-context cache

`add_metrics_to_holdings` caches each stock's sanitized market context in `data/market_context_cache.json`, shared across users:
- **Slow-changing sections** (fundamentals, quarterly series, insider data, earnings history, Twelve Data indicators) are re-fetched only after the most recent market open or close. In practice that means the first check of each trading day, plus once after the close.
- **Price action and headlines** are refreshed when older than `FRESH_CONTEXT_MAX_AGE_MINUTES`.

A full fetch costs about 9 Finnhub calls and 6 Twelve Data credits per stock. A refresh costs one Yahoo quote plus Yahoo and Finnhub news.

## Alerts

- **Content:** each alert line names the stock, the recommendation, and Claude's rationale, HTML-escaped because Telegram uses HTML formatting.
- **Duplicate key:** duplicates are identified by **(user, symbol, recommendation)**. The old key was the whole holding row, whose prices change every hour, so the cooldown never matched.
- **Logging:** a send is logged only after Telegram accepts it, so a failed send is retried on the next run.
- **Addressed by name, and "Urgent" only when it is** (2026-10-23, Andres): every Telegram message used to start with "Urgent Alert for <user>". Now the urgent ones, the watchdog's sell alerts and a swing idea's position reaching its stop or target, start with "🚨 Urgent alert for <user>:" (`send_telegram_message(..., urgent=True)`), and every other message (settings, sign-ups, key and holdings problems, earnings reminders, the holiday notice, scorecards, run failures) starts with "Hi <user>," above its own heading.
- **Which recommendations alert:** the latest *recorded* recommendation, not only this run's evaluations, so an `urgent_sell` from the morning digest also alerts. Recommendations older than `MAX_EVALUATION_AGE_HOURS` never alert.
- **The digest sends its own (2026-10-26):** once every digest of a run is out, `run_daily_digests` calls `hourly_watchdog.send_digest_alerts` from the main thread, for each user with holdings and a chat ID, watchdog on or off, limited to the stocks in their digest. Before, a digest's `urgent_sell` waited for the next watchdog run past its gates, about an hour later (the tripwire sits out the hour after a digest), and never came for a user with the watchdog off. Same alert log and cooldown, so neither job repeats the other's alert; a failed send stays unrecorded, and the watchdog retries it.
- **Confirmed first (added 2026-10-22, at Andres's request):** a review that says `urgent_sell` or `profit_sell` (`HOLDINGS_RECHECK.SIGNALS`) is followed by `EXTRA_REVIEWS` (2) more of the same stock, and the majority's label is kept; a tie goes to the milder label (of three different ones, the middle). So a one-off urgent sell neither alerts nor flags the stock. The same holdings' labels matched 8 times out of 10 on identical data (2026-10-20). Other labels, and sentiment, are never re-reviewed. Each extra review costs one more request for that stock (`recommendation.ParallelHoldingEvaluator._confirmed`, `majority_review`).

## Files written

| File | Written by | Contents |
|---|---|---|
| `data/watchdog_state.json` | `holdings_state.py` (watchdog + digest) | Per user: the last scheduled check and last digest (the clock for the next check), Claude calls today, and per stock the last evaluation (time, price, NATR, recommendation, rationale, SAR stop, next earnings date). Sold positions are dropped. |
| `data/market_context_cache.json` | `add_metrics.py` | Shared daily market context per stock. |
| `data/alerts_sent/telegram_alerts_timestamped.csv` | `hourly_watchdog.py` | Alert log used for the cooldown (kept 30 days). |
| `data/usage/api_usage.jsonl` | `usage_tracking.py` (watchdog + digest + universal) | One line per Claude or Gemini call: user, source, model, tokens, estimated cost. Append-only. |
| `data/holding_news/<user>.json` | `news_reuse.py` (digest, 2026-10-23) | Each holding's news fetched today, with the price, NATR, signal, and earnings date it was fetched at, for later digests the same day. |
| `data/remote_settings.json` | `remote_settings.py` (digest, 2026-10-23) | The settings accepted from the spreadsheet's Parameters tab, applied by every Finnbot process. |

The first two files are written atomically (temp file, then rename) and are gitignored.

## Running it

From the project root on the VM. The watchdog, the digest, and the backup share one `flock` lock, so they never write the state files at the same time. Each waits up to 15 minutes for the lock instead of skipping the run. Since 2026-10-16, `scripts/cron_job.sh` takes the lock, runs the job, and reports the result to healthchecks.io. Both jobs now exit with code 1 when a user's run fails. Setup is in [operations.md](operations.md).

```cron
45 * * * * cd ~/ai_financial_advisor && bash scripts/cron_job.sh watchdog
# Digest: every hour at :10, in any VM timezone. src/digest/digest_schedule.py decides from the exchange calendar
# whether the Pre-Market (07:00 ET until the open) or Post-Market (close + 10 min, so 16:10 ET) digest is due, skips
# days no configured exchange trades, and remembers who already received it. Daylight saving needs no changes.
10 * * * * cd ~/ai_financial_advisor && bash scripts/cron_job.sh digest
# Nightly backup of data/ to Google Drive (docs/operations.md)
30 2 * * * cd ~/ai_financial_advisor && bash scripts/cron_job.sh backup
```

Check what the digest would do at any moment without sending anything: `venv/bin/python src/digest/digest_payload.py --check [--now 2026-10-15T16:10:00-04:00]`.

**Build real payloads without HTML or email** with `--payload-only` (added 2026-10-16), e.g. for a local test run:

```bash
venv/bin/python src/digest/digest_payload.py --payload-only                      # the digest that's due now, if any
venv/bin/python src/digest/digest_payload.py --payload-only --period Pre-Market  # build it now, at any time
```

- **Same pipeline as a real run:** live data and real Claude and Gemini calls (so it costs the same), the universal content and users in parallel, and recent watchdog evaluations reused.
- **Output:** each recipient's full payload goes to `test_output/digest_payloads/<user>_<date>_<period>_<HHMM ET>.json` (gitignored). That's exactly what `create_html` would receive.
- **Skipped:** `create_html` and the email. Nothing is recorded as sent, and missed digests aren't checked, so a test run never changes what cron sends.
- **Who gets built:** recipients still need `<NAME>_RECEIVE_PRE/POSTMARKET_DIGEST=True`.
- **Still recorded:** new evaluations are saved to the watchdog state and the track record, and spend to the usage ledger, like any other run.
- **Still alerted:** problems found while building, such as a broken holdings connection or a bad key, send their usual notices.

**Test the email from a saved payload** with `--from-payload` (added 2026-10-17). It runs only the last two steps of a real run, `create_html` and then `send_gmail`, so it fetches no data and calls no LLM:

```bash
venv/bin/python src/digest/digest_payload.py --from-payload test_output/digest_payloads/Andres_2026-10-16_Pre-Market_0801.json
venv/bin/python src/digest/digest_payload.py --from-payload <file> --no-email   # only save the HTML
```

- **Recipient:** the email goes to the payload's user at their `<NAME>_EMAIL` in `.env`.
- **Subject:** starts with `[Test]`.
- **Nothing is recorded as sent.**
- **The HTML is saved** to `src/digest/html_reports/<user>_<date>_<period>.html` (gitignored). Open it in a browser to check the layout.

On Windows, run `venv\Scripts\python.exe` instead, and set `$env:PYTHONUTF8 = "1"` first.

### The digest email (rewritten 2026-10-17)

`src/digest/create_html.py` turns each user's payload into the email, and `src/digest/email_template.html` lays it out.

**What the email answers, in order** (UI pass, 2026-10-17): how is my portfolio doing, do I need to act, what is the market doing, and are there new ideas.
1. **Header:** the period (☀️ pre-market or 🌙 post-market), the date, a greeting by the time of day in the user's home timezone, and when it was prepared. Since 2026-10-21 a button under it opens Finviz's S&P 500 heat map over the past week (`DIGEST.HEAT_MAP_URL`, `https://finviz.com/map?t=sec&st=w1`).
2. **Notices** about anything missing.
3. **Overview card** (always open):
   - the portfolio's value, with the day's change in a coloured pill. Since 2026-10-21 these are in the user's home currency (`<NAME>_HOME_CURRENCY`), and when that isn't USD, a line under the value gives it in USD with the rate, e.g. "$102,345.67 USD · 1 CAD = 0.7111 USD";
   - three figure boxes: unrealized gain, flagged positions and their share of the value, and new ideas;
   - "Needs attention" chips for each flagged stock, most urgent first;
   - "Earnings soon" for holdings reporting within the catalyst window. Since 2026-10-21, a holding card with no earnings date known at all says "No earnings date available" (often for listings outside the US, which Finnhub's free plan doesn't cover); a date past the 14-day window isn't mentioned;
   - **Jumping to a stock:** in the AMP version, tapping a stock in either list opens Your holdings and that stock's row, then scrolls to it and highlights it.
     - **How:** AMP email has no scroll action, so the row focuses its own header once it has opened (its `expand` event), and focusing scrolls it into view.
     - **Fixed 2026-10-20, unwanted jumps:** AMP hands an `expand` event to the nearest element up the page that handles it, so opening a holding's Why or News dropdown also ran the row's focus action. With the row's header scrolled off the top, the page jumped about 650 px up to it. Those dropdowns now handle their own `expand` event by focusing their own header, which is on screen because it was just tapped. In a phone-sized browser test, with the email as the page and inside a Gmail-like auto-sized frame, no tap moves the page any more, and the chips still jump.
     - **Tested:** in a browser with real taps. Whether the Gmail app lets the email scroll its own view is unconfirmed; if not, the row still opens and is highlighted.
     - **HTML version:** the chips stay plain there, because jump links don't work in the Gmail app or into closed dropdowns.
   - the connected brokerage, with each account's value in a small dropdown.
4. **Portfolio risk:** beta, the share of the portfolio's value under each signal, a bar chart of the share in each sector (added 2026-10-25; the six largest, the rest as "Other"), and the portfolio review's notes. Since 2026-10-21 the review writes its amounts in the home currency, like the overview: the model gets the portfolio summary in that currency (`money_currency`, `money_format`) and is told to write every amount in it.
5. **Markets & macro:** since 2026-10-21, graded market signals (`create_html._signals`), followed by the top stories. Each indicator has a dot and a short grade (e.g. "Uptrend", "Calm", "Defensive") coloured like the dot, what the grade rests on, and its value and move. The four tiles and three gauges this replaced are gone.
   - **Key signals first:** when the section opens, `MARKET_SIGNALS["KEY_SIGNALS"]` show first: the S&P 500's trend, the market mood, breadth, the 10Y yield, and Nasdaq-100 vs Dow. The rest are under the "All indicators" dropdown, in their groups (Benchmarks, Sentiment, Macro), with a line above the sentiment group on how VIX and Fear & Greed differ.
   - **Links (`DIGEST.INDICATOR_LINKS`):** each indicator's name opens its chart. They replaced a table of 1-day, 5-day, and 1-month moves.
     - **Google Finance**, at its 1-month view: the S&P 500, VIX, the 10Y yield, and crude oil. Each "A vs B" opens A, since a Google link can't preset a comparison.
     - **Yahoo:** the dollar index (opens on 1 day).
     - **Barchart:** breadth, its `$S5FI` (opens on 3 months).
     - **FRED:** the 10Y-2Y spread (its full recent history).
     - **CNN:** Fear & Greed, the mood, and put/call (CNN's page has a put/call chart).
     - **Until 2026-10-21:** StockCharts, which Andres asked to replace.
   - **Market mood:** VIX and Fear & Greed read together from `MARKET_SIGNALS["MOOD_MATRIX"]`. VIX comes from option prices and jumps within hours of a shock; Fear & Greed blends seven gauges and moves over days. Its label replaces the VIX level on the collapsed section's line, e.g. "S&P 500 +0.3% day · Mood: Quietly cautious · 4 stories".

     | VIX \ Fear & Greed | Greed (over 55) | Neutral (45-55) | Fear (under 45) |
     |---|---|---|---|
     | Calm (under 20) | Confident (green) | Steady (green) | Quietly cautious (yellow) |
     | Elevated (20-30) | Jittery rally (yellow) | Unsettled (yellow) | Stressed (red) |
     | Panic (over 30) | Fresh shock (red) | Shock spreading (red) | Capitulation (red) |

   - **The levels (2026-10-21):** first taken from Andres's "Global Macro Pulse" table. At his request, they were then set from research and 2 years of market data, with the outperformance gap at his 2 points:

   | Group | Indicator | Green | Yellow | Red | Grey | Why |
   |---|---|---|---|---|---|---|
   | Benchmarks | S&P 500 vs its 50- and 200-day averages | above both (uptrend) | above one (pullback, recovering) | below both (downtrend) | | The standard trend test |
   | | Nasdaq-100 vs Dow, over 5 days | growth leads by 2+ pts (risk-on) | value leads by 2+ (defensive) | | even | A 1-pt gap flagged a lead on 36-55% of days; 2 pts is a real rotation |
   | | NYSE Composite vs S&P 500 | in step (broad) | the S&P leads by 2+ (concentrated) | | the NYSE leads | |
   | | Russell 2000 vs S&P 500 | small caps lead by 2+ | | small caps lag by 2+ (credit warning) | in step | |
   | Sentiment | VIX | under 20 | 20-30 | over 30 | | Its long-run average is about 19.5-20; over 30 is stress (78% and 3% of days over 2 years) |
   | | Cboe total put/call (and its 5-day average) | 0.70-1.00 (balanced) | under 0.70 (over-exuberance) | over 1.00 (fear) | | Very low put buying is complacency, a caution rather than a good sign |
   | | CNN Fear & Greed (and the prior close) | 45-75 (neutral, greed) | 25-44 (fear), over 75 (extreme greed) | under 25 (extreme fear) | | CNN's own bands; both extremes are cautions |
   | | % of S&P 500 stocks above their 50-day average | over 60% | 40-60% | under 40% | | No standard extremes; readings over 90% have been followed by gains, so high breadth isn't flagged |
   | Macro | US Dollar Index, 5-day move | down 1%+ | | up 1.5%+ | steady | A typical week moves ~0.6%; 1.5% is the top ~10% |
   | | 10Y yield, 1-month move | down 25+ bps | | up 25+ bps | steady | A typical month moves ~12 bps; 25 is a quarter point |
   | | 10Y-2Y spread | positive | positive again within 6 months of an inversion | inverted | | Un-inversion has come closer to past recessions than the inversion itself |
   | | WTI crude, 5-day move | | down 10%+ | up 10%+ | calm | A typical week moves ~3%; 10% is the top ~10% |

   Every level is in `params_dict["MARKET_SIGNALS"]`, and the glossary quotes them from there. The Nasdaq-100 and the Dow share a row, since one leading the other is the signal for both.
6. **Your holdings:** most urgent signal first, then largest position. A stock held in several accounts gets one row.
7. **Swing ideas.**
8. **Long-term ideas.** Each card's tiles: forward P/E, PEG, revenue growth, net margin, and since 2026-10-23 (Andres) momentum: RSI 14 (overbought at 70+, oversold at 30 or less, as on the holding cards) and the 12-month price return (Yahoo's 52-week change; `RECOMMENDATION_FIELDS.top_long_term.rsi_14`, `all.52_week_change_percent`). Six tiles go two a row, like the four on a holding card.
9. **Glossary** (added 2026-10-17): a collapsed card with five category rows: signals and ideas, your portfolio, technical indicators, valuation and fundamentals, and market indicators. Each row lists the terms, abbreviations and indicators the email uses, and how to read them. The text is static, in `src/digest/glossary.py`.
10. **Footer:** "About this digest", showing what it cost, the email's own size in KB (`Email size`, added 2026-10-25), which model scored the ideas, and how many stocks were left after each screening stage (added 2026-10-17; see `screening` below).

**Currencies (added 2026-10-17).**

| Amounts | Shown in | Source |
|---|---|---|
| The overview: portfolio total, the day's change, unrealized P/L, account values | The user's home currency (2026-10-21), plus the total in USD when that isn't USD | `home_currency` in `create_html.py`: `<NAME>_HOME_CURRENCY` from the payload, at the current rate |
| The home currency's week (2026-10-21) | For a non-USD home currency: its move against the dollar over the past 5 trading days, after the rate on the overview's USD line, e.g. "$8,466.64 USD · 1 AUD = 0.703 USD · AUD +0.6% this week" | `fx_rates.usd_rate_change` (Yahoo `<CODE>USD=X` daily closes); left out when Yahoo has no history. Added because DXY (the dollar against the euro, yen, pound, Canadian dollar, krona, and franc) says how the dollar affects US stocks, not what it does to a holder whose currency is another |
| Holding cards: value, price, unrealized P/L (2026-10-21) | Each stock's own currency, at Andres's request: "C$1,000.00", "€200.00", "COP$2,800". London shows its price in pence as quoted ("Price · pence", "418.5p") and its value and P/L in pounds. Since 2026-10-23 the line under the name also gives the average price paid ("10 shares · avg cost $290.36 · Individual RRSP"; in pence for London; a stock in several accounts is averaged over them; left out when the broker reported no cost basis). The line atop Your holdings says "Values in each stock's currency" (or "Values in USD" when every holding is in USD) | `_own_currency` in `create_html.py`: the card's USD amounts back at the rate they were converted at. Before 2026-10-21 cards were in USD, with a "Listed in CAD at C$64.50 · 1 CAD = 0.707 USD" note |
| Totals behind the overview, weights, beta (any currency) | USD at the current rate, then the home currency for the overview | `pull_holdings_data` converts money amounts to USD (`USD_MONEY_COLUMNS`); `holdings_in_usd` in `create_html.py` restates them at the current rate just before the email is built (rates from `src/market/fx_rates.py`: live from Yahoo, else the last cached rate, else the payload's own rate). If there's no rate at all, the holding stays in its currency and a notice says so |
| The portfolio review's notes (2026-10-21) | The home currency | `build_portfolio_summary(..., home)` and the review prompt |
| Ideas: prices, trade levels, targets | "Prices in USD" | Always USD: the ideas come from the US screener (`IDEAS_CURRENCY`) |
| Market dashboard ETF and oil prices | $ (US ETFs, e.g. "S&P 500 · SPY $771.79") | Always USD: US ETFs and WTI |
| API costs | Their `currency` | `api_usage.currency` |

Where USD is assumed for a holding without a currency, or for API costs in a payload without `api_usage.currency`, the run log gets a `[NOTE] ... (not in the payload)` line.

**Holdings listed outside North America (added 2026-10-21).** Each Yahoo suffix names its exchange and currency (`market_session.LISTING_SUFFIXES`):

| Suffix | Exchange calendar | Currency |
|---|---|---|
| `.TO` `.V` `.NE` `.CN` | XTSE | CAD |
| `.DE` `.F` `.PA` `.AS` `.MI` `.MC` `.BR` `.LS` `.VI` `.HE` `.IR` | XETR, XFRA, XPAR, XAMS, XMIL, XMAD, XBRU, XLIS, XWBO, XHEL, XDUB | EUR |
| `.L` | XLON | GBX (pence) |
| `.AX` | XASX | AUD |
| `.CL` | XBOG | COP |

- **The calendar:** each holding's daily P/L covers its own exchange's last session. A 07:10 ET pre-market digest is mid-session in Europe (live price), after the close in Sydney (that day's close), and before the open in Bogota (the previous day's close). Since 2026-10-21 each card is labelled by its own session (see the labels below).
- **The currency:** used only when SnapTrade or the sheet doesn't give one. What they give, or Yahoo's own currency for a sheet's symbol, wins.
- **What these holdings may lack:** Finnhub's free plan is mainly for US listings, so their Finnhub sections can be empty (the Yahoo data is there), and Finviz has no chart for them.
- **Ideas:** the idea pipeline stays US-only.
- **The P/L check (2026-10-21):** at today's rates, a mixed portfolio's value minus the day's change equals the previous close's value, and unrealized P/L excludes currency moves since purchase (`tests/test_create_html.py`).

**"Prev. day" and "Day" labels (added 2026-10-17).** Every daily change covers one full trading session, close to close: the previous session in a Pre-Market digest, today's in a Post-Market one. To make that clear, every daily change is labelled "Prev. day" in Pre-Market digests and "Day" in Post-Market ones:
- the overview's change pill;
- each holding's collapsed row and its Price box;
- the line at the top of Your holdings;
- the market tiles, the table's one-day column, and the markets summary;
- the yield-curve change;
- the inbox preview line.

**Each market's own session (added 2026-10-21).** A holding's daily change covers its own exchange's latest session: the move so far at the latest price while that exchange is trading, otherwise its latest full session. Its card is labelled to match (`create_html._day_label`, from `daily_pnl_session` and `daily_pnl_live`):

| Label | When |
|---|---|
| "live" | The stock's exchange is trading now, e.g. London during a pre-market digest |
| "prev. day" | Its latest full session is before the digest's market date, e.g. New York before the open |
| "today" (Pre-Market) | Another market's session that finished today, e.g. Sydney before New York opens |
| "day" (Post-Market) | Today's session, finished |

When the cards share one label, the overview reads as before ("Prev. day · Tue, Oct 20"). When they don't, the overview says "Latest session per market", and the line atop Your holdings explains each label used, e.g. "Prev. day = the Oct 20 session · Today = the Oct 21 session · Live = still trading". Before 2026-10-21 every card said "prev. day" in a pre-market digest, London's live move included.

It is built for a phone first: one column up to 600 px wide.

**Dropdowns (nested since 2026-10-17).** The email opens as a short overview, and everything else is a dropdown inside a dropdown:

| Level | What's shown while collapsed | Inside |
|---|---|---|
| Always open | Header, notices, overview card | – |
| 1. Section card | A coloured icon, the title and a count, and a coloured one-line summary, e.g. "Your holdings 10 · ● 6 risky · ● 4 holds". The arrow is on the right, as in mobile apps. A section with nothing in it is a plain card saying why, e.g. "No setups scored 80+ today". | The section's key figures and its rows |
| 2. Row | **Holdings and ideas:** ticker, signal or score (green from 90), value, weight and day change<br>**Portfolio risk:** each review note's short title<br>**Markets & macro:** each story's headline | **Stock:** figures (a 2×2 grid), catalysts, its chart (a link to its Finviz page), and the smaller dropdowns (the Yahoo/Finviz/TradingView buttons are the first thing dropped when the email is too big)<br>**Note:** its full text<br>**Story:** why it matters, and a source button |
| 3. Small dropdown | "Why" (the reasoning), News, extra figures, About this digest | The content |

- **Starting state:** everything starts collapsed. List any of `"risk"`, `"markets"`, `"holdings"`, `"swing"`, `"long_term"` in `DIGEST["OPEN_SECTIONS"]` to start those sections open.
- **Note titles:** Gemini's portfolio review now writes a 3–8 word title for each note. Payloads saved before 2026-10-17 use the note's first clause instead.
- **Narrow phones:** the layout fits a small phone without any width-specific CSS, because the Gmail app may not apply it.
  - **Figure boxes:** sets of four (a stock's figures, a trade plan, valuation) form a 2×2 grid.
  - **Row headers:** a signal badge can wrap under its ticker.
  - **Margins:** narrow everywhere.
  - **Extra tightening:** on screens up to 420 px, margins and fonts tighten further where supported.
  - **Tested:** with every dropdown open, in both versions, nothing runs past the right edge at 280–390 px wide, with or without that CSS. The test also covered six-figure values and the longest badges.
  - **Enlarged text (2026-10-21):** the Gmail app on Android enlarges email text by the phone's font-size setting (Andres's: about 1.75×), and clips whatever is wider than its view instead of scrolling. "A$12,060.73" at 34 px cut the overview card off on the right. So:
    - the portfolio total is 34 px only up to 9 characters, then 26, 22, and 18 px;
    - the full indicator table puts each name on its own line above its figures;
    - figure boxes have 6 px sides.

    Tested with every font multiplied: it fits at 1.75× in 375 and 395 px views and at 1.5× in 345 px. Since 2026-10-21 a stock row's company-name line may also break inside a long word ("Semiconductor") when nothing else fits. That changes nothing at those sizes, but brings the widest stock header at 2× from 314 to 268 px. Keep any text that can't wrap (big figures, no-wrap cells, rows of figures) under about 190 px at normal size.
  - **Whole phrases (2026-10-21):** where a line wraps, a short phrase moves to the next line whole instead of being split. Examples: "1 AUD = 0.702 USD", "Day · Mon, Oct 19", a section summary's "MGA 85". The greeting is 21 px (was 25), so "Good afternoon," fits on one line on Andres's phone without being forced to (forced, it overflowed narrower phones).
    - **How:** `create_html._phrases` (phrases of up to 24 characters) and `_part` (summary parts of up to 18).
    - **Never adds a line:** measured with and without, every block's height compared at 1× and enlarged text. The two places where keeping a phrase whole added a line wrap freely instead: the risk summary's "High market risk" and the holdings note.

Where they work:
- **Collapse in the HTML version:** Apple Mail (including the iPhone Mail app with a Gmail account) and Samsung Email.
- **Show expanded:** Gmail and Outlook, under the same labels, because neither supports dropdowns in HTML email.
- **Gmail:** gets working dropdowns from the AMP version (below).

**Charts (added 2026-10-17).** Each holding and idea has Finviz's daily candlestick chart: candles, 20/50/200-day moving averages, trendlines and volume.
- **Shown as soon as the stock's row is opened,** under its figures.
- **Not shown for holdings listed in another currency,** since Finviz covers US listings.
- **Freshness:** each digest's chart links are unique, so Gmail's image cache can't show yesterday's chart.
- **Finviz blocks headless browsers (403).** Previewing the saved HTML in an automated browser needs a normal user agent. Gmail's image proxy and ordinary browsers get the charts.

**AMP version: working dropdowns in Gmail (added 2026-10-17).** With `DIGEST["AMP_EMAIL"] = True`, every email also carries an AMP for Email version, built from the same template. Its dropdowns are `amp-accordion`, which work in the Gmail app on Android and iOS and on the web.
- **Who sees it:** only Gmail renders AMP. Every other mail app ignores it and shows the HTML version.
- **What Gmail requires:**
  - a sender different from the recipient;
  - SPF and DKIM passing, which Gmail-to-Gmail mail does;
  - a sender address the recipient has allowed.

One-time setup (per recipient):
1. Create a Gmail account for sending the digests, e.g. `finnbot.digest@gmail.com`.
2. Turn on 2-Step Verification for it, then create an app password: Google Account → Security → App passwords.
3. In `.env`, set `GMAIL_USER` to that address and `GMAIL_APP_PASSWORD` to the app password.
4. Each recipient, in Gmail on the web: **Settings → See all settings → General → Dynamic email**. Tick **Enable dynamic email**, click **Developer settings**, add the sender address, and save.
5. Send a test: `--from-payload` (above). Without the setup, Gmail quietly shows the HTML version.

Other limits:
- **Expiry:** Gmail shows an AMP email's HTML version once it is 30 days old.
- **Size:** the AMP spec allows 200 KB, but Gmail doesn't document its own limit. Some guides say 100 KB, and a 90 KB digest is known to render. So the AMP version is trimmed toward 100 KB the same way as the HTML version (below), and is only left out above 200 KB.
- **Validation:** Gmail falls back to HTML when the AMP version isn't valid. After editing the template, check the saved `src/digest/html_reports/<user>_<date>_<period>.amp.html` with Google's validator:

  ```bash
  npm install amphtml-validator
  npx amphtml-validator --html_format AMP4EMAIL <file>.amp.html
  ```

**Dark mode.** The Gmail app recolours emails itself and ignores the email's own dark-mode styles.
- **How the design handles it:** every block is a light background with dark text, which Gmail converts cleanly. There are no dark panels with white text, because the Gmail iOS app turns white text dark.
- **Charts:** Gmail doesn't recolour images, so they stay white panels in dark mode.
- **Apple Mail:** keeps the light design.

**Gmail's size limit.** Gmail hides everything past about 102 KB of the HTML version behind "[Message clipped]".
- **Normal size:** a typical digest is about 90 KB.
- **Trimming:** when a digest is bigger, `fit_for_gmail` leaves out the least important content first, and the footer says so. Order (since 2026-10-25, Andres's choice; `create_html.TRIM_STEPS`): the charts' direct Finviz links (`DIGEST.CHART_DIRECT_USERS`, about 270 bytes each) go back to finviz.com's short ones; hold cards' news except High priority; Low-priority news; the Yahoo / Finviz / TradingView quote buttons on every card; "About this digest" (costs, model, screening counts); the glossary (about 10 KB); the risk section's sector bar chart (about 1 KB); the swing ideas' "About the company"; every card's news down to its top story, except urgent sell and risky cards; all hold cards' news; the ideas' detail dropdowns; the urgent sell and risky cards' news down to the top story; all holding news; the hold cards' charts. A typical digest of about 10 holdings keeps the glossary, at about 96 KB in each version.
- **Where the bytes go** (2026-10-19, 10 holdings, 29 news bullets, 2 long-term ideas): holdings about half (news bullets 21 KB, reasoning 9 KB, figures 7 KB), long-term ideas 10 KB (about 5 KB each; a swing idea is about 3.4 KB), glossary 10 KB. Only about a quarter of the bytes were readable text; the rest was markup.
- **Trimmed on 2026-10-19:** repeated inline colours, paddings and alignments became short classes in the `<style>` block (104.3 to 93.8 KB for the AMP version, with no visible change), holding news became one sentence per story (about 55% shorter), and the holdings reasoning became at most 2 plain sentences. With 10 holdings and 5 swing ideas, only the glossary should need to go.
- **Trimmed on 2026-10-20:** 4 macro stories, each with a one-sentence impact (5.0 to 3.8 KB), a tighter glossary with every term kept (9.6 to 8.8 KB), and idea pitches in plain words with one-sentence key risks. The 2026-10-19 post-market AMP digest was 83.9 KB. A day with 5 swing ideas (about 3.6 KB each) would be about 102 KB, so the glossary would still be left out that day.
- **Leaner markup (2026-10-22):** badges and dots name a colour class per email (`create_html.style_classes`) instead of repeating their colours inline, figure boxes take their width from a class, and line breaks before block-level tags are dropped. Pixel-for-pixel the same page (checked with every dropdown open at 395 and 600 px); 5.9 KB less per version with 19 holdings.
- **Glossary as a web page (2026-10-25, Andres):** `src/digest/glossary_page.py` builds the whole glossary as one page (each term with its own `#anchor`) and publishes it to GitHub Pages from a separate public repo, `andresc44/finnbot-glossary`, cloned at `GLOSSARY_SITE_DIR` (default `~/finnbot-glossary`). Every cron digest run republishes it when its text changed (pushing with this machine's SSH key; a failure is only a warning). With `DIGEST.GLOSSARY_URL` set, the email shows a one-line "Glossary ↗" card instead of the ~10 KB card, and the plain-text version ends with the link. Set up once: create the public repo, clone it, `venv/bin/python -m src.digest.glossary_page --publish`, then in the repo's Settings → Pages, deploy from the `main` branch, root folder.
- **Leaner cards (2026-10-25):** the Yahoo/Finviz/TradingView buttons are gone (each chart links to Finviz), the figure boxes and the holdings' header line use less markup for the same look, and the volatility line is the reasoning's last sentence: 400 B less per holding card (Mauricio's 20-stock digest: 124.9 to 114.4 KB before trimming).
- **News scaled to the portfolio (2026-10-22, Andres's rule):** with more than `DIGEST.NEWS_FULL_UP_TO_HOLDINGS` (10) holdings, each card shows its `NEWS_LARGE_PORTFOLIO_BULLETS` (1) most important story, except the `NEWS_FULL_SIGNALS` (urgent sell, risky), which keep all of theirs. Trimming also cuts those stocks' news last (`_flagged_top_news_only`, after every hold stock's news is gone).
- **Glossary of the day (2026-10-22):** the glossary shows only the terms the email uses: a term counts when one of its markers (`glossary.USAGE_MARKERS`; otherwise the term itself) is in the email's visible text (`terms_in_use`, from `fit_for_gmail`). The 2026-10-20 digest with 10 holdings kept 49 of 56 terms, and now fits untrimmed (91 KB HTML, 93 KB AMP).
- **Never removed:** each holding's signal, figures and reasoning.
- **Capacity:** about 20 stocks fit; a larger portfolio needs Gmail's "View entire message" link for the rest.
- **Untrimmed:** the plain-text version is always complete.

### Fresh data in each digest

The pre- and post-market digests on the same day are built independently; nothing from the morning digest is copied into the afternoon one:

| Part | Freshness |
|---|---|
| Holdings, prices, daily P/L | Fetched every run. Daily P/L covers each stock's most recent regular session on its own exchange (`daily_pnl_session`): today's from the open (live, vs the previous close), otherwise the last completed session at its official close. So the 7:00 digest shows the previous trading day's move, and extended-hours prices never mix in. Since 2026-10-20 the value and unrealized P/L are measured at that same price (`valuation_price`), so the value minus the day's change is the previous close's value. Before, they used the broker's latest price, a pre-market or after-hours quote outside regular hours, and a Monday 7:20 ET run was $175 off. The broker's price stays in `current_price` for the watchdog. |
| Market data for holdings (`data/market_context_cache.json`) | Quote and headlines are re-fetched when older than `FRESH_CONTEXT_MAX_AGE_MINUTES` (30). Fundamentals and daily indicators are re-fetched after each market open and close; the close only counts from 16:01 ET, hence the digest at close + 10 minutes. |
| Claude recommendations | Fresh, except a watchdog evaluation from the last `REUSE_EVALUATION_MAX_AGE_MINUTES` (60) for a stock that has moved less than 1 NATR since. Set it to `0` to always evaluate fresh. |
| Gemini news | Fetched once a day per holding (2026-10-23): a later digest the same US Eastern day shows the earlier news again, unless the stock moved more than `NEWS_REUSE.MAX_MOVE_NATR` NATRs, its signal changed, or it reported earnings since (`src/personalized/news_reuse.py`, `data/holding_news/`). |
| Portfolio review | Fresh every run. |
| Company names | Yahoo's name (`profile.name` in the market context) replaces the brokerage's description, which can be wrong (SnapTrade has called Agilent's "A" "AgileThought, Inc."). The holding news starts before the market context is fetched, so it uses the name cached from an earlier run (`cached_company_names`). |
| Currency | Money amounts (market value, cost basis, unrealized and daily P/L) are in USD, converted at the latest Yahoo rate (`src/market/fx_rates.py`; the last good rate in `data/fx_rates.json` if Yahoo fails). Per-share prices stay in the listing's currency, with `*_usd` copies. Unrealized P/L uses today's rate, so it excludes currency moves since purchase. The email shows each holding card back in its own currency (2026-10-21). |
| Universal content | Fresh every run, except the NASDAQ screener's ticker list. That list is downloaded once per morning and once per afternoon, and again whenever the stage-1 settings in `parameters.py` change. |

### Track record (`src/tracking/track_record.py`)

Every new holding evaluation (digest or watchdog) is appended to `data/track_record/personal.jsonl`, and every universal pick to `data/track_record/universal.jsonl`, with the price at the time. The files are never rewritten. To see how the advice played out:

```bash
venv/bin/python -m src.tracking.track_record                 # all recommendations
venv/bin/python -m src.tracking.track_record --since 2026-11-01
```

It prints, per recommendation (buy_more / hold / risky / profit_sell / urgent_sell; older rows keep hold / trim / risk_alert) and per universal list (long_term / swing): how many, the average return 1, 5, and 20 trading days later, the share that went up, the average return minus SPY's over the same days, and how many are still pending. A good `urgent_sell` record shows negative returns; a good `long_term` list beats SPY.

### Logs and failure alerts

Each run writes to `logs/digest.log` or `logs/watchdog.log` (`src/ops/run_logging.py`): one line per message with a timestamp and the user it belongs to, rotating at `OPS.LOG_MAX_MB` (5 MB) and keeping `OPS.LOG_BACKUPS` (5) old files. Under cron the output no longer also goes to `digest.log` / `watchdog.log` in the project root, which now only catch crashes before logging starts; set `FINNBOT_LOG_ECHO=1` to see the output when piping it. Each run ends with a summary line (who was sent or checked, who failed, how long it took, and how many files the process had open).

What the log holds (since 2026-10-24, at Andres's request): errors, warnings, tracebacks, and each run's and user's results with counts and times. It doesn't hold what users own: no amounts, P&L, account names, or holdings symbols, except in an error about that stock. The backup copies `logs/` to Google Drive unencrypted by default, so this matters. Progress lines (fetching X, N tickers left after Y), the per-stock evaluations, and the HTTP request lines of the libraries (httpx, Gemini, Anthropic) are left out. Run with `FINNBOT_LOG_VERBOSE=1` to get them back while debugging; these lines can include holdings symbols.

`OPS.ALERT_USERS` (default `["Andres"]`) get a Telegram alert when the digest's universal content fails, a user's digest or watchdog check fails, or a run crashes, at most once per `OPS.ALERT_COOLDOWN_HOURS` (6) for each kind of failure. The traceback is in the log.

**Earnings reminders (added 2026-10-21).** Every hourly digest run also checks each user with holdings and a Telegram chat ID for held stocks reporting earnings within `WATCHDOG.EARNINGS_REMINDER_HOURS` (24), and sends one Telegram reminder per stock and report: when it reports, before the open or after the close, and Finnbot's latest call on it.
- **No API calls:** the held stocks come from `data/watchdog_state.json` (the watchdog forgets sold positions on every check), and the report dates and times from the market-context cache (Finnhub's earnings calendar).
- **Expected times (US Eastern):** before the open 08:00, after the close 16:05, during market hours 12:00, and 08:00 when no time is known, so the reminder is never late.
- **Every day:** it runs at weekends too, so a Monday-morning report is caught on Sunday.
- **Code:** `src/telegram_alert/earnings_reminder.py`, called by `run_daily_digests`.

**Holdings bought from Finnbot's ideas (added 2026-10-22).** When a user buys a stock Finnbot suggested, Finnbot remembers the idea and uses it (`src/tracking/idea_positions.py`, state in the gitignored `data/idea_positions.json`, which the Drive backup includes):
- **What counts:** a new position (a symbol held now that wasn't at the user's previous digest or watchdog check; adding shares to a stock already held doesn't count), bought by the end of the next trading day after the idea. A purchase is only seen at the next check, so the test is that the previous check came before the NYSE close of the first trading day after the idea's date. The ideas are the track record's (`data/track_record/universal.jsonl`). A user's first check only remembers what they hold, so stocks owned before this existed are never linked.
- **The holding card:** one grey line, e.g. "Swing idea · Oct 21 · entry $100.00 · stop $95.00 · target $112.00" ("· target reached" or "· stop hit" once the price gets there), or "Long-term idea · Oct 16 · score 93". About 0.26 KB per stock that has one.
- **The AI review:** the stock's payload carries `opened_from_finnbot_idea` (list, date, score, what was paid, and a swing idea's entry, stop, and target), and the review's instructions say to weigh the plan without following it blindly.
- **Target and stop alerts:** when a swing idea's position reaches its target or falls to its stop, a Telegram message, once each (users with a chat ID). Checked at every digest and watchdog check.
- **"You hold this":** a small badge on a swing or long-term idea the user already holds.
- **Monthly scorecard:** from the first cron digest run of a month, a Telegram message to each user who bought from ideas the month before: each position's change since it was bought (or until it was sold), and the average beside the S&P 500 over the same days. Nothing is sent to a user without such positions.

**US market holidays (added 2026-10-21).** On a weekday the NYSE is closed, `OPS.ALERT_USERS` get one Telegram notice from the first digest run at or after `DIGEST.PRE_MARKET_TIME_ET` (07:10 ET under cron). It says:
- the holiday, from the exchange calendar (e.g. "Thanksgiving"; "a special closure" for one-off closures);
- when trading resumes, and whether that day closes early;
- whether the digests still go out. They do when another exchange in `WATCHDOG.EXCHANGE_CALENDARS` trades: the TSX is open on US Thanksgiving.

The alert log's key includes the date, so later runs that day don't repeat it. `OPS.HOLIDAY_NOTICE: False` turns it off. Code: `src/telegram_alert/holiday_notice.py`, called by `run_daily_digests` (not with `--check` or `--payload-only`).

To test without spending anything:

```bash
venv/bin/python src/telegram_alert/hourly_watchdog.py --dry-run
venv/bin/python src/telegram_alert/hourly_watchdog.py --dry-run --now 2026-10-14T13:45:00-04:00
```

A dry run reads holdings and free price data and prints which users are due and which stocks would be evaluated, with reasons. It makes no Claude calls, sends no Telegram messages, and writes no files. `--now` only works together with `--dry-run`.

New dependency: `exchange_calendars` (pinned in `requirements.txt`, together with the previously missing `anthropic` and their dependencies). Run `pip install -r requirements.txt` on the VM.

### When the universal content can't be built properly

The universal picks use the shared `ANTHROPIC_API_KEY`, `FINNHUB_API_KEY`, and Twelve Data key, not the users' keys. Before 2026-10-16 a bad shared key failed silently: every candidate's Claude call failed (so both lists came back empty with no alert), Finnhub and Twelve Data errors were printed and the picks were scored on partial data, and a missing Finnhub key called `sys.exit`, which stopped every user's digest. Now:

| Problem | What happens |
|---|---|
| Shared Anthropic key missing, invalid, out of credits, or over its spend limit | A free check runs before any data is fetched. The digest goes out with empty picks. |
| The key fails partway through scoring | Scoring stops; candidates already scored are kept. |
| `FINNHUB_API_KEY` missing | The digest goes out with empty picks. |
| Finnhub rate limit (429) | Calls are paced to `FINNHUB_CALLS_PER_MINUTE` (60, the free plan's limit) in any 60 s, so it shouldn't happen (added 2026-10-18). If it does, e.g. another process used the key in the same minute, the call is retried after 2, 5, 15, and 60 s; the last wait outlasts Finnhub's one-minute window. |
| Finnhub rejects the key (401) | No more Finnhub calls this run. |
| Twelve Data per-minute limit | Waits for the next minute, up to 2 times per request. |
| Twelve Data daily credits used up, or key rejected | No more Twelve Data calls this run. |
| A Twelve Data request needs more credits than the plan allows per minute (one per ticker; the free plan allows 8). Only applies to indicators not computed from Yahoo prices (below). | Skipped at once instead of waiting it out, and not retried this run. The technical filter then keeps those tickers instead of dropping them all. |
| Yahoo's daily prices for the indicators can't be downloaded | Reported as a `Yahoo prices` issue. Those tickers have no indicators, which the completeness check also flags. |

**Share-class tickers** (2026-10-17): the NASDAQ screener writes `BRK/B`, which Yahoo doesn't know, so Berkshire used to drop out of the candidates. The ticker list is converted to Yahoo's `BRK-B` when it's loaded, and each Finnhub call converts it to Finnhub's `BRK.B` (`yahoo_symbol` and `finnhub_symbol` in `data_source_guard.py`).

**Technical indicators** come from Yahoo daily prices by default (`TECHNICAL_INDICATOR_SOURCE = "yahoo"`, `src/universal/recommendations/local_indicators.py`, added 2026-10-16).
- **Why:** on Twelve Data's free plan, the ~40 candidates and any holdings refresh of more than 8 stocks never got their indicators.
- **Covered:** rsi, ema, sma, bbands, sar, adx, and atr, on daily or weekly bars. They are computed from unadjusted bars with TA-Lib's conventions. Checked against Twelve Data, they matched to within 0.0001% (`tests/test_local_indicators.py` replays it on saved AAPL data).
- **beta:** market beta against SPY.
- **Everything else** (another indicator, an intraday interval, an option like `ma_type`) still goes to Twelve Data. Set the source to `"twelvedata"` to send everything there.

**Daily bars** (`DAILY_BARS_DAYS`, default 21, added 2026-10-18): each stock's LLM input, for new-stock scoring and holdings alike, includes its last 21 daily bars as `daily_bars`: date, open, high, low, close, and volume, newest first, unadjusted like the indicators.
- **Why:** the indicators only summarize the price path. The bars let the model see pullbacks into support, higher lows, bounces, gaps, and whether volume is heavier on up or down days, which the swing rubric's rebound signals and the holdings' buy-more check ask for.
- **Source:** the same Yahoo download as the indicators, so no extra calls. The newest bar can be a session still in progress.
- **Cost:** about 0.9K input tokens per stock, ~33K a run for 37 candidates: about $0.01 on `deepseek-flash`, $0.07 on `claude-sonnet-5-5`.
- **Holdings cache:** cached holdings contexts get bars at their next full fetch, after the next market open or close.
- **Turning them off:** set `DAILY_BARS_DAYS` to `0`.
| More than `UNIVERSAL_DATA_QUALITY.MAX_MISSING_PCT` (20%) of tickers missing Yahoo, Finnhub, or Twelve Data data | Picks are still made, but flagged. |

Each problem is added to the universal payload's `universal_issues` (`{"source", "reason", "message"}`), so the email can say why picks are missing or based on partial data. Each is also sent to `OPS.ALERT_USERS` by Telegram, once per cooldown. The macro news (`src/universal/market_news.get_macro_stories`, still disabled) works the same way with the shared `GEMINI_API_KEY`: on failure it returns no stories plus the issue, and never raises.

### Swing screen (added 2026-10-20)

Swing ideas have their own pipeline (`src/universal/recommendations/swing_screen.py`), run beside the long-term screens on the same stage-1 list (the NASDAQ screener: market cap, volume, and price minimums, so no penny stocks). Before, swing ideas came from the long-term candidates, which are great businesses at fair prices; on most days only a handful were in a swing setup, and none scored 80.

All its settings are `SWING_SCREEN` in `parameters.py`:

| Stage | Setting | What it keeps (default) |
|---|---|---|
| 1. Fundamentals | `YAHOO_FILTERS` | A decent business, looser than the long-term screen: average volume 1M+, 3+ analysts, consensus Hold or better (3.0), forward P/E between 0 and 60, net margin 1%+, revenue at most 5% below a year ago. Uses the Yahoo info the long-term screen already fetched. |
| 2. Setup | `SETUP` | Price $10+, $20M+ traded a day, RSI 20-65, still early in its move (`SWING_EARLY_ENTRY`), not still falling (no sign of a turn while RSI is at a 10-day low, `EXCLUDE_FALLING_KNIVES`), no earnings within 5 trading days (`EARNINGS_BLACKOUT_SESSIONS`, Yahoo's date), and either a **pullback** (at most 5% under its 200-day average, and down 1.5+ ATRs but at most 30% from its 20-day high close) or a **new uptrend** (the 9-day average crossed above the 21-day within the last 5 sessions, i.e. at most 4 ago). Each setup can be switched off. One batch of Yahoo daily bars. |
| 3. Plan | `LEVELS`, `MIN_PLAN_RR`, `MAX_ABOVE_SUPPORT_ATR` | Entry at the latest close; stop 0.5 ATR below the nearest support (EMA 21, 50- or 200-day average, lower Bollinger Band, 10-day low), and at least 1.5 ATRs below the entry; target at the nearest level at least 1.5 ATRs up (20- or 60-day high close, 50- or 200-day average from below, upper band), at most 8 ATRs up. Plans under 1.5:1 are dropped, and so are plans whose price is more than 2 ATRs above that support. |
| To the LLM | `MAX_CANDIDATES` | The best 40 plans: the most of the swing score the code can check (`screen_points`, out of 80: the plan's risk/reward points, the technical start, and the middle of the momentum band its signs of a turn allow), then the earliest in its move |

- **Calibrated** on 2026-10-19's data. First settings: 613 -> 395 fundamentals -> 118 in a setup (85 pullbacks, 32 new uptrends) -> 67 plans of 1.5:1+ -> 20 to the LLM, of which 6 were also long-term candidates. Loosened the same day at Andres's request, for a larger pool to pick the best from (the values above; the old ones are noted in `parameters.py`): 596 -> 468 -> 187 -> 108 -> 40 to the LLM (22 pullbacks, 18 new uptrends). The plan minimum stays 1.5:1: a plan under it earns at most ~8 of the 30 risk/reward points, so it can't reach 80.
- **Tightened 2026-10-22** at Andres's request, to match the rubric without sending much less: a cross at most 4 sessions old (was 7), a pullback of 1.5+ ATRs (was 1), no more dips far under the 200-day just because the 50-day is still above it (`OR_SMA50_ABOVE_SMA200` off: SE, 20% under, got through), the price within 2 ATRs of its support, no report within 5 trading days, no falling knives, and the ranking above (it was risk/reward alone). On that day's data: 476 fundamentals -> 50 in a setup (145 before) -> 30 plans (78) -> 30 to the LLM (40). About 10 of either list could reach 80 even at the top of every band; the ones no longer sent couldn't, apart from NSC (reporting that day), TGT (a 1.4-ATR pullback), and ALAB (still falling). Requiring a sign of a turn, and no pullbacks under both averages, would have cut the list to 20, so a stock with no sign yet or in a weaker trend is ranked lower instead (`MIN_TURN_SIGNS` 1 makes the first a rule). The setups also get their catalyst facts: the next earnings and the analysts' mean target from Yahoo, then the last 4 reports' beats and insiders' net buying from each candidate's Finnhub data (`add_report_facts`, after the fetch).
- **Both lists are fetched together:** a stock on both is fetched once. The swing screen's daily bars are the same download the long-term technicals use.
- **Long-term picks can be swing picks too** (the old rule left them out).

**One LLM call per horizon** (2026-10-20). Long-term candidates get `LLM_rec_instr.LONG_TERM_SYSTEM_PROMPT` (the same rubric as before) and swing candidates `SWING_SYSTEM_PROMPT`, each with only the data its rubric uses (`LLM_PAYLOAD`, `sanitizer.horizon_payload`): long-term calls skip the technicals and daily bars, swing calls the full fundamentals (keeping a few as `key_fundamentals`) and get the screen's setup, facts, and plan as `swing_setup`. A pick shows its other score only when it was scored on both.

| First run (2026-10-19 post-market data) | Before (one combined call) | After |
|---|---|---|
| Calls | 93-96 | 93 long-term + 20 swing |
| Tokens per call | 8.8K in, 2.2K out | long-term 3.9K in, 0.8K out; swing 9.2K in, 1.1K out |
| Cost at off-peak prices | $0.24-0.26 | $0.14 |
| Whole run | ~27 min | 23 min |

The run is faster because the long-term Finnhub filter now fetches only the section its rules read (`basic_financials`, 1 call per Yahoo survivor instead of 4), and the quarterly series that comes with it is reused instead of fetched again.

**Swing rubric, from the screen's facts** (`SWING_SYSTEM_PROMPT`). The model scores three parts (70 points) from `swing_setup.facts` and the usual data; the fourth, risk / reward (30), is `plan_risk_reward_score` of the screen's plan, so the model no longer picks levels. Each band names the facts it rests on: e.g. rebound signals count four signs of a turn (RSI rising, more volume on up days, a higher low, a close above EMA 9 or the SAR flipped below). Earnings within 5 trading days cap the catalyst score at 10: a report days away is a coin flip, not a catalyst. On the same 20 candidates scored three times, the swing score moved 3.6 points on average (median 3.0, max 13.5), against 7.1 (max 11.7) for the old call.

**The rubric's checks are worked out in code** (2026-10-22, at Andres's request: less left to the model's judgment, without raising scores). `swing_screen.rubric_checks` gives each candidate `swing_setup.checks`, and the prompt tells the model to apply them as given. The numbers are in `LLM_rec_instr.py`, shared by the prompt and the code.

| Part | What code works out | What the model still judges |
|---|---|---|
| Technical (30) | `technical_start`: 30, less 6 per weakness (12 for a double one): a mixed / weak trend (the price above the 200-day, and the 50-day above the 200-day: one fails / both fail), 1-2 / 2+ ATRs above the support, a pullback outside 1.5-6 ATRs, and for a new uptrend a cross 4+ sessions old, a close under EMA 21, or 2+ ATRs above its 10-day low | Up to 5 off for how the chart looks (orderly or erratic); at most 11 when the bars contradict the setup |
| Momentum (20) | The four `turn_signs`: RSI up on 5 sessions ago and under 55; up/down volume 1.2+; no new 10-day low for 2 sessions and 0.5+ ATR off it; a close above EMA 9 or the SAR below the price. Their count sets the band: 19-20, 16-18, 11-15, 6-10, 0-5 | Where in the band, by how clear the signs are; at most 5 when RSI is at a 10-day low |
| Catalyst (20) | The `lifts`, 2 points each: earnings 6-40 trading days away after 3+ beats in the last 4, the mean target 15%+ above the price, net insider buying over 3 months | A temporary dip (0-8: the market fell at least a third as much over the same days, or an overreaction; 4 when unexplained; 0 for a business cause), 2 more for a dated event in the headlines, and no red flags (0-4). Thesis-breaking news caps it at 5, earnings within 5 days at 10 |

Each new band sits where the old band for the same case was (one weakness: 24 less the chart, against 18-24 before; a typical catalyst: 4 + 2-4 + 4, against an average of 12.1), so the bar isn't easier to reach. The old bands also contradicted each other: a stock below its 200-day average with the 50-day above it was "one weakness" (18-24) and also "below both averages", "stretched" (12-17). The trend is now one fact.

### Universal picks and macro content in the payload

Each pick in `recommendations.top_long_term` / `top_swing` carries Claude's reasoning (added 2026-10-16) plus the data set in `RECOMMENDATION_FIELDS`:

| From | Long-term picks | Swing picks |
|---|---|---|
| Claude's evaluation | `longScore`, `swingScore` (null unless also a swing candidate), `sentiment` (1–3+ years), `thesis`, `key_risk`, `sub_scores` | `swingScore`, `longScore` (null unless also a long-term candidate), `sentiment` (2–8 weeks), `thesis`, `entry_price`, `stop_loss`, `price_target`, `risk_reward`, `setup`, `stop_basis`, `target_basis`, `sub_scores` |
| `RECOMMENDATION_FIELDS["all"]` | name, sector, industry, price, market cap, 52-week range and change, analyst rating, target, and count, next earnings date | same |
| The list's own fields | P/E (trailing, forward), PEG, revenue and earnings growth, profit margin, ROE, debt-to-equity, free cash flow | latest RSI, EMA 9/21, Bollinger bands, Parabolic SAR, 50/200-day averages, beta, volume |

- **Swing levels:** the levels are only kept when they're consistent (0 < stop < entry < target). Otherwise all four are `null` and the log says why. `risk_reward` is the upside to the target per unit of risk to the stop, e.g. `2.5` for 2.5:1.
- **Thresholds:** a stock needs a total of at least `MIN_LONG_SCORE` / `MIN_SWING_SCORE` (85 / 75 out of 100 since 2026-10-22; both were 80) to be picked, so some days have fewer picks than `LONG_TERM_N_AMOUNT` / `SWING_TERM_N_AMOUNT`, or none.
- **Close calls are scored again (added 2026-10-22, at Andres's request):** the same stock scored twice can come out about 4 points apart (13.5 at most), so a score could cross the minimum by chance. Unless a list already has as many candidates more than `IDEA_RESCORE.ABOVE_MIN` (5) above its minimum as it shows (`LONG_TERM_N_AMOUNT` 3, `SWING_TERM_N_AMOUNT` 5), each candidate within 5 points of the minimum (`BELOW_MIN` / `ABOVE_MIN`: long-term 80-90, swing 70-80) is scored `EXTRA_RUNS` (2) more times and keeps the run with the middle score, its sub-scores and thesis included (`scorer.rescore_borderline`).
- **Swing rubric** (rewritten 2026-10-18, `src/universal/recommendations/LLM_rec_instr.py`): a good swing trade buys a stock while it is temporarily cheap and likely to go back up within 2-8 weeks, i.e. a pullback to support in an intact trend where the selling is running out, not one that has already risen for weeks. The four parts:
  - **Pullback setup** (`technical_setup_score`, 30): high at support in an intact longer trend; low when stretched after a run or breaking down with no support. Since 2026-10-22, the technical start the code works out, less up to 5 for the chart (above).
  - **Risk / reward** (`risk_reward_ratio_score`, 30): since 2026-10-20 scored from the swing screen's plan (above); until then from the model's own entry, stop, and target. The stop goes just below the support the trade relies on, and the target is where the rebound should reach. Points (since 2026-10-22, `SWING_SCREEN.RISK_REWARD_POINTS`): 12 at 1.5:1, 18 at 2:1, 22.5 at 2.5:1, 26 at 3:1, 28.5 at 3.5:1, and the full 30 from 4:1, on straight lines in between (0 at 0.5:1 or less, rising to 12 at 1.5:1). Before that the bands were 0-11 below 1.5:1, 12-24 up to 2.5:1, then 25-30 with full marks only at 4.5:1. Those bands jumped a point at 2.5:1, and full marks were out of reach for a clean pullback: its target (the recent high) is at most about 6 ATRs up and its stop at least 1.5 ATRs down, so about 4:1 is its best. On the 2026-10-20/21 candidates the average stayed about the same (24.2 to 23.6 on 10-21) and plans were told apart better (spread 2.9 to 3.4 points). A stop closer than a normal day's range (the 14-day average true range from the daily bars, and at least 1% of the price) is counted as that far, so a tight stop on a falling stock can't inflate the score.
  - **Rebound signals** (`momentum_volume_score`, 20): the four signs of a turn (RSI rising, more volume on up days, a higher low, a close above EMA 9 or the SAR below the price), checked in code since 2026-10-22; their count sets the band.
  - **Catalysts & sentiment** (`catalyst_sentiment_score`, 20): whether the drop looks temporary (0-8), something can lift the price soon (0-8), and no red flags (0-4), since 2026-10-22 (above).

  The field names are unchanged, so older payloads and the track record still read. The old bands described three setups that rarely happen together (breakout, at support, bouncing from oversold), and never scored the model's own levels.
- **Early entries only** (`SWING_EARLY_ENTRY`, added 2026-10-18): a swing pick must still be early in its move. A stock that scores 80+ is left out if it is more than 3 normal days' ranges (14-day ATR) above its 10-day low, has an RSI above 70, or closes above its upper Bollinger Band (`scorer.too_far_along`; the log says which). The check only removes picks, so a day with no good early setup has no swing picks. Calibrated on 2026-10-16's 97 candidates: stocks just turning up near support were 0.9-1.9 ranges above their 10-day low, and 26 of 97 were more than 3, including TRU (up 20% in 10 days), which the model had scored as the best swing setup. The screening counts show "Still early in their run" under "Swing score 80+".
- **Sector mix** (`PICK_SECTOR_RULES`, added 2026-10-17): the long-term picks (3 since 2026-10-22, were 2) are from different sectors (`all_different`), and more than half the swing picks are from different sectors (`majority_different`: distinct sectors > picks / 2, e.g. 3 sectors among 5 picks), by Yahoo's industry (`PICK_GROUP_BY`, since 2026-10-25; before that its sector). Long-term lists also hold at least a third non-tech picks, rounded up when the list has two or more (`LONG_TERM_MIN_NON_TECH_SHARE`), which may score from 80, and tech picks follow the industry rule too. Swing lists of 4 or more ideas hold at least 1 non-tech pick (2 from 8 ideas) when one cleared the bar (`SWING_MIN_NON_TECH`). A stock the rule leaves out makes room for the next best that fits and clears the bar; when none fits, the best left-out stocks fill the list (2026-10-25, Andres; `scorer.fill_relaxed`), so a list is short only when too few stocks clear the minimum score. The log names each stock left out and each one added back. A stock without a sector counts as its own. `"any"` turns a rule off.
- **At least one long-term pick outside tech** (added 2026-10-22, Andres's rule; `LONG_TERM_MIN_NON_TECH_PICKS` 1, `TECH_SECTORS`, `LONG_TERM_NON_TECH_MIN_SCORE` 80): when the picks are all tech, the best non-tech candidate scoring 80 or more, from a sector not already picked, joins them: added when there are fewer than 3 picks, otherwise in place of the lowest-scoring tech pick (`scorer.with_non_tech_picks`). So the non-tech pick may score under the usual 85. If none scores 80, or nothing reached 85, the picks stay as they are and the log says so. Tech is Yahoo's Technology and Communication Services sectors: the internet and ad-tech names (GOOGL, META, RDDT, APP) sit in the second.
- **At most two tech picks** (added 2026-10-23, Andres's rule; `LONG_TERM_MAX_TECH_PICKS` 2): with 3 or more long-term picks, at most 2 are tech, and tech stocks may share a sector with each other; the other sectors still follow `PICK_SECTOR_RULES` (`scorer.pick_with_sector_rule`'s `capped`). Before, `all_different` let only one Technology stock in: on 2026-10-22 all nine candidates at 85+ were Technology (TSM 94, NVDA, ASML, AVGO, ANET, SKHY, LRCX, MU), so the list had only TSM and BAM (82.5, from the non-tech rule). With the new rule it would have been TSM, NVDA, and BAM.
- **Track record:** it also keeps each pick's sentiment and swing levels, for checking later whether they held up.

**Macro content.** It is switched with `UNIVERSAL_EXTRAS`, and both parts are on by default. Both run in the background while the picks are built, and both are included even when no picks could be made.
- `headline_news`: `{"macro_stories": [{"priority", "event_summary", "market_impact", "source_url"}]}`, the top `UNIVERSAL_EXTRAS.MACRO_NEWS_STORIES` (4) stories, each impact one sentence, from Gemini with Google Search, using the shared `GEMINI_API_KEY`. A `source_url` is kept only when it matches a page the search returned; otherwise it's `null`.
- `market_data`: index, VIX, dollar, 10-year yield, and oil moves (1 day, 5 days, 1 month); the 10Y–2Y spread (FRED); CNN Fear & Greed; and the share of S&P 500 stocks above their 50-day average. Added 2026-10-21 for the signals: the S&P 500's 50- and 200-day averages (a year of Yahoo history, was two months), the 10Y yield's moves in basis points, the spread's 5-day move, Fear & Greed's previous close, breadth's 1-day, 5-day, and 1-month changes, and `put_call_ratio`: Cboe's total put/call ratio for the latest session it has published (one JSON file per session at `cdn.cboe.com`, posted after the close) and the average of the last 5.

A section that can't be fetched holds `{"Error": ...}` and adds a `universal_issues` entry. It never stops the digest.

**Screening counts** (`screening`, added 2026-10-17; `screening_counts` in `src/universal/universal_consolidated.py`). How many stocks got through each stage this run, shown at the bottom of "About this digest":

| `stage` | Email label | Count |
|---|---|---|
| `listings` | US-listed stocks | Everything NASDAQ's screener lists (NASDAQ, NYSE, and AMEX), before any filter. Saved as `<file>_listings.json` next to the half-day's screener CSV, so it's missing for a CSV made before this was added |
| `screener` | Size, volume & price | Kept by stage 1: `MKTCAP_*`, `VOLUME_*`, `LASTSALE_*`, region, sector |
| `yahoo` | Long-term: analysts & fundamentals | Kept by `YAHOO_FILTERS` |
| `finnhub` | Long-term: growth & debt | Kept by `FINNHUB_FILTERS` |
| `technical` | Long-term: technical screen | Kept by `TWELVEDATA_FILTERS` |
| `long_term_ranked` | Long-term: best by the rubric's figures | Added 2026-10-25: the best `LONG_TERM_SCREEN.MAX_CANDIDATES` of those the screens kept (`of` = how many that was); only shown when some were left out |
| `scored` | Long-term: scored by AI | Valid long-term evaluations from the LLM. `of` is how many were sent; the email shows "36 of 37" when some failed |
| `swing_fundamentals` | Swing: fundamentals | Kept by `SWING_SCREEN["YAHOO_FILTERS"]` (added 2026-10-20, like the next three) |
| `swing_setup` | Swing: dip or fresh turn up | In a pullback or new-uptrend setup (`SWING_SCREEN["SETUP"]`) |
| `swing_plan` | Swing: reward : risk 1.5+ | With a plan of at least `MIN_PLAN_RR` (`min_score`) |
| `swing_scored` | Swing: best setups scored by AI | The best `MAX_CANDIDATES` plans, scored; `of` is how many were sent |
| `long_term_score` / `swing_score` | Long-term score 85+ / Swing score 75+ | Scored at least `min_score` (`MIN_LONG_SCORE` / `MIN_SWING_SCORE`). A stock can clear both |
| `picked` | Picked | `long_term` + `swing`, capped at `LONG_TERM_N_AMOUNT` / `SWING_TERM_N_AMOUNT` |

- **Rules:** each filter stage also has its `rules` as text, e.g. `"forwardPE <= 30"`, so a payload records what that run checked even after `parameters.py` changes.
- **Stages without rules:** a filter stage with no rules lets everything through, so it's left out.
- **No picks possible:** when no picks could be made (no LLM or Finnhub key), `screening` is `{}` and the email leaves the section out.

### API cost tracking (`src/tracking/usage_tracking.py`)

Every Claude, DeepSeek and Gemini response is recorded in `data/usage/api_usage.jsonl`: holding evaluations (watchdog and digest), holding news, the portfolio review (an Anthropic call since 2026-10-23), and the universal scoring and macro news under the user `_shared`. Each user's digest payload gets an `api_usage` block with the estimated spend on their own keys and, under `admin`, on the admin's shared keys (2026-10-22, Andres), each for this run and the last 30 days:

```json
"api_usage": {
  "currency": "USD",
  "note": "Estimated from token counts at list prices as of 2026-10-16; ...",
  "this_digest":   {"anthropic": {"calls": 12, "input_tokens": 41000, "output_tokens": 6100, "cache_write_tokens": 0,
                                  "cache_read_tokens": 0, "cost_usd": 0.286},
                    "gemini":    {"calls": 9, "...": "...", "search_queries": 22, "cost_usd": 0.0123},
                    "total_cost_usd": 0.2983},
  "last_30_days":  {"...": "the last 30 days on their own keys, watchdog included"},
  "admin": {"this_digest": {"...": "the shared keys this run: new-stock ideas and market news"},
            "last_30_days": {"...": "the shared keys over the last 30 days"}}
}
```

About this digest shows them as "This run · you", "This run · admin", "Last 30 days · you", and "Last 30 days · admin" (until 2026-10-22: this digest, today, and the month to date, own keys only). The admin side is added when each user's payload is put together (`DigestPayload.personalized_content`, `usage_tracking.shared_usage`), after the shared content is done: a user's own section can finish first. An ideas-only user has only the admin side.

These are estimates from token counts and list prices. They can't see credits, discounts, or use of the same key outside Finnbot. If a model has no price, its calls are still counted but cost $0, and it is listed under `unpriced_models`. Gemini's Google Search grounding is free for the first `GEMINI_FREE_SEARCH_REQUESTS_PER_MONTH` (5,000) requests per key each month, so search cost only counts once a user passes that.

**When the section started:** each user's payload also has `personal_started_at`, the moment their personal section began. It's given in their home timezone (`<NAME>_TIMEZONE`) and in US Eastern: `{"home_timezone", "home", "home_iso", "us_eastern", "us_eastern_iso"}`. The display strings look like `Friday, October 16, 2026, 7:02 AM EDT`. If the timezone isn't a valid IANA name, the home fields are `null` and only Eastern time is shown; `check_config` reports the setting as INVALID.

**Keeping prices current:** prices are in `params_dict["API_PRICES"]` as dated lists. To add a price change, append an entry with its `"from"` date. Usage before that date keeps the old price, so past months don't change. For example, the gemini-3.6-flash entry already holds the doubled price that starts 2027-01-01. Update `AS_OF` when you check the price pages listed in `SOURCES`. Model ids match on the longest prefix, so `claude-opus-5-5` gets its own price, not `claude-opus-5`'s.

## Changing settings from the spreadsheet (2026-10-23)

Every setting in `src/config/parameters.py` is also a row of the **Parameters** tab in the sign-up spreadsheet (`SIGNUP_FORM_SHEET_ID`; the tab is `SETTINGS_TAB_GID`, else the one named "Parameters"). Changing a setting there changes it for the next digest run, without editing or deploying `parameters.py`. Written at Andres's request (`src/config/remote_settings.py`, `src/config/settings_overrides.py`, `src/config/parameter_notes.py`).

| Column | Who writes it | What it is |
|---|---|---|
| Parameter | Finnbot | The setting's keys joined by dots, e.g. `DIGEST.SYMBOL_NEWS_BATCH_SIZE`. A dict is split into one row per key; a list is one row. |
| Value | **You** | The value to use. Write it the way Python does: `True` / `False`, `None`, numbers, text without quotes, and lists like `['Technology', 'Communication Services']`. Blank = the default. |
| Default (parameters.py) | Finnbot | `parameters.py`'s own value. |
| Finnbot status | Finnbot | `Default`, `✅ Applied since <time>`, or `❌ Not applied: <why>`. The header says when Finnbot last read the tab. |
| What it does | Finnbot | A one-line note (`src/config/parameter_notes.py`). |

**When it's read.** At the start of every digest run, before the sign-up sync: the hourly cron run (at :10, whether or not a digest is due) and manual `--payload-only` runs alike (Andres, 2026-10-23; `--check` and `--from-payload` don't build a digest, so they don't). By hand: `python -m src.config.remote_settings --sync` (`--show` lists the values in force on this computer).

**How a value is checked.** It must have the default's type (a whole number where the default is one, `True`/`False`, a list shaped like the default's items), and pass the setting's own rules where it has them: allowed values (efforts, sector rules, sessions, signals, sections), a model that `ALLOWED_CLAUDE_MODELS` or `ALLOWED_DEEPSEEK_MODELS` lists with an effort it accepts, a valid timezone or `HH:MM` time, counts of at least 1, no negative numbers where the default isn't negative, scores up to 100. A value that fails isn't applied: the status says why, and the value accepted before (or the default) stays in force.

**Where it applies.** The accepted values are saved in `data/remote_settings.json` and laid over `parameters.py` at the end of that file, so every Finnbot process (digest, watchdog, `check_config`) starts with them. The run that read the tab also applies them in place, so that digest already uses them. A few settings are read only when a process starts (`FINNHUB_CALLS_PER_MINUTE`, `WATCHDOG.TIMEZONE`, `WATCHDOG.ALERT_COOLDOWN_HOURS`, `WATCHDOG.ALERT_LOG_RETENTION_DAYS`, `OPS.LOG_MAX_MB`, `OPS.LOG_BACKUPS`): their status says they take effect from the next run.

**When `parameters.py` changes.** The newest change wins (Andres, 2026-10-23). Finnbot notices a change in `parameters.py` (by Andres or Claude) because the default no longer matches the Default column it last wrote. At the next sync the row's Value and Default both become the new default, even if the Value had been changed in the sheet, the status says "Default (changed in parameters.py, <time>)", and the admins are told on Telegram (e.g. "MIN_SWING_SCORE: 70.0 → 72.0 (changed in parameters.py)"). A new setting gets its row then too. Every computer that runs a digest syncs the sheet, so keep their `parameters.py` the same (pull after a change): one with an older copy would put its older defaults back.

**Exceptions.**
- `ACTIVE_USERS` is shown but read-only: the sign-up sync sets it from the form's answers. Edits there are ignored and overwritten.
- `.env`'s `UNIVERSAL_LLM_MODEL` / `UNIVERSAL_LLM_EFFORT` still win over `LLM.UNIVERSAL` on the computer that has them; the status says so when they differ.
- A row that isn't a setting (renamed or removed from `parameters.py`) is kept and marked ignored, so you can delete it.

**When something goes wrong.** If the tab can't be read or written (network, permissions), the values accepted last time stay in force and the admins get a Telegram alert. The service account needs **Editor** access to the spreadsheet, because Finnbot writes the status back.

**Telegram.** The admins hear once per change: which settings changed (old → new, or back to the default) and which values weren't applied, with the reason.

**Layout.** The first sync sets up the tab: a frozen header, plain-text cells (so `07:00` or `1.50` aren't turned into a date or number), wrapped text, green and red status cells, and a warning (not a lock) when editing the columns Finnbot writes. A note on `A1` repeats the rules.

**Adding a setting.** Add it to `parameters.py` as before, and a note for it to `src/config/parameter_notes.py` (`tests/test_remote_settings.py` fails until it has one). The next sync adds its row.

## Holding news and the portfolio review (2026-10-23)

Gemini was about 85% of a user's bill, and about 90% of that was output tokens, mostly thinking: no request set a thinking level, so gemini-3.6-flash thought as much as it liked. At Andres's request:

1. **Thinking low, project-wide.** `GEMINI_CONFIG.effort` (`minimal`, `low`, `medium`, `high`, or `default`) sets the thinking level of every Gemini request, the holding news and the macro news (`src/llm/gemini_settings.py`).
2. **The portfolio review on the holdings model.** It's written by `LLM.HOLDINGS` (Claude Haiku 5.5, low) with the user's own Anthropic key, through a strict tool schema like the holdings evaluation, instead of Gemini. A user with the news off no longer needs a Gemini key.
3. **The morning's news in the evening.** News fetched earlier the same US Eastern day is shown again (`DIGEST.NEWS_REUSE`), unless the stock moved more than `MAX_MOVE_NATR` normal days' ranges since, its signal changed, or it reported earnings; those are fetched again once the holdings are evaluated. Saved per user in `data/holding_news/<user>.json` (`src/personalized/news_reuse.py`).
4. **Several holdings per request.** `DIGEST.SYMBOL_NEWS_BATCH_SIZE` (3) holdings share one Gemini request. A holding the answer leaves out, or every holding of an answer that can't be read, is asked for again on its own. It was 5 at first; on Andres's 10 holdings that kept 11 stories against 13 with one holding per request, at about half the cost ($0.012 against $0.022 a run), so Andres chose 3.
5. **An output cap.** `GEMINI_CONFIG.max_output_tokens` (8,192) and `DIGEST.PORTFOLIO_REVIEW_MAX_TOKENS` (8,000) cap each answer, thinking included. Holding news cut off by the cap is asked for again one holding at a time.

**Source links at low thinking.** In the first run with these settings, Gemini wrote the articles' own addresses instead of its search results' links, which Finnbot can't verify, so 0 of 10 holding-news links were kept. The prompts (holding and macro news) now ask for the grounding redirect link by name: in a test of 6 requests, 33 of 33 links were search links.

**Measured** on Andres's 10 holdings, 2026-10-22:

| | Before (that morning) | After |
|---|---|---|
| Holding news, one digest | 10 Gemini requests, $0.1165 | 2 requests, $0.0168 (with links) |
| Holding news, a later digest the same day | Same as above | No Gemini request: all 10 reused |
| Portfolio review | ~$0.017 (Gemini) | $0.0014 (Claude Haiku 5.5, low) |
| Macro news request | 26 s | 6 s |
| News links kept | 14 of 15 | 9 of 9 (holdings), 4 of 4 (macro) |

Medium effort, tried twice on the same 5 stocks, cost 2.5 times as much as low and found no more stories (8-9 against 9-10), so low is the default. For a user with 10 holdings, news on, and both digests, the monthly estimate fell from about $6.60 to about $1.45 (`docs/new_user_guide.txt`, 3.10).

**Not added: a count of the 5,000 free searches.** Finnbot can't know the true number: with a response schema Gemini doesn't report its searches, Google has no way to ask how much of a key's allowance is used, and the key could be used outside Finnbot.

## Known limitations

- **TSX symbol formats.** Some brokerage symbols don't match Yahoo's format; for example `DLR.U.TO` (Yahoo uses `DLR-U.TO`). Those stocks get no price history, no NATR and no extended-hours price, so only the age and earnings triggers re-evaluate them.
- **Sheets timestamp.** The Sheets source records `data_as_of` as naive local time. The price age is clipped at 0 rather than corrected.
- **Session windows** must stay within one market-local day. The default offsets do.
- **Digest recording.** Evaluations from the digest are recorded automatically through `add_recommendations`, but `src/digest/digest_payload.py` itself still needs its own fixes before it can run.
- **New-stock scoring.** `src/universal/recommendations/LLM_recommendation.py` doesn't use per-user models; it's shared across users and uses `LLM["UNIVERSAL"]` (Claude or DeepSeek). On Claude it sends a strict tool schema with `tool_choice: auto` and refusal fallbacks, like the holdings evaluator.

## Authorship

Everything below was written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-13, at Andres's request, following a plan agreed in the same session.

| File | What Claude wrote | Why |
|---|---|---|
| `src/market/market_session.py` (new) | Market phases, session windows, cache refresh boundary (`block_id`, the old fixed-block schedule, is no longer used) | Lets one hourly cron serve per-user intervals and sessions, with holidays, half-days and DST handled by exchange calendars. Pure functions of `now` so they can be tested at fixed times. |
| `src/personalized/user_settings.py` (new) | Reads, validates and defaults per-user settings | One place that turns `.env` values into settings, so a typo can't crash the run or send an invalid API request. |
| `src/personalized/holdings_state.py` (new) | `data/watchdog_state.json` storage | The memory needed to evaluate "only when needed", shared by the watchdog and the digest. |
| `src/personalized/add_metrics.py` | Whole file: market context for holdings, plus the daily cache | Gives the sell-side LLM the same evidence as the buy-side LLM, and keeps API use bounded at higher check frequencies. |
| `src/personalized/recommendation.py` | Whole file: Claude buy_more/hold/risky/profit_sell/urgent_sell evaluation (hold/trim/risk_alert/urgent_sell before 2026-10-18), per-user model and effort, session context, refusal fallbacks, state recording | Replaced placeholder rules that referenced non-existent columns. |
| `src/personalized/LLM_hold_instr.py` | Whole file: prompt and rubric | Keeps the sell-side prompt separate from the code, mirroring `LLM_rec_instr.py`. |
| `src/telegram_alert/hourly_watchdog.py` | Everything below the `Claude-authored` banner, plus `--dry-run` / `--now`. `load_and_prune_alert_logs` and `is_alert_suppressed` are Andres's originals, unchanged. | Per-user scheduling and gates, plus fixes: alerts were sent before the cooldown check, the duplicate key could never match, and one user's failure stopped the loop. |
| `src/personalized/pull_holdings_data.py` | `src.*` imports, `user_name` / `claude_overrides` in `__init__`, session-price methods, `symbols` parameter on `get_holdings_digest_payload`, guard for a missing `GOOGLE_APPLICATION_CREDENTIALS` | Needed by the watchdog. The guard fixes a `TypeError` crash. |
| `src/telegram_alert/send_telegram.py` | Imports only | The watchdog couldn't import it from the project root. |
| `src/personalized/user_profiles.py` | Five new profile keys per user | Per-user settings. |
| `src/config/parameters.py` | `WATCHDOG`, `CLAUDE_REFUSAL_FALLBACK`, `ALLOWED_CLAUDE_MODELS` | Global defaults and limits. |
| `requirements.txt`, `.gitignore`, this document | New pins and ignore rules; documentation | Reproducible installs, and no runtime state committed to the repo. |
