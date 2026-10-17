# Holdings watchdog and per-user model selection

The watchdog re-evaluates each user's holdings on that user's own schedule and sends a Telegram alert when Claude recommends `urgent_sell`. Each user also picks which Claude model and effort level evaluates their holdings.

> **Authorship:** this feature was designed with and written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-13, at Andres's request. [Authorship](#authorship) lists every file Claude wrote or changed and why. In code, Claude's work is marked with an `Authorship:` docstring (new files) or a `Claude-authored (2026-10-13)` comment (changes to existing files), so `grep -rn "Claude-authored\|Authorship:" src/` finds all of it.

## How one run works

Cron starts `src/telegram_alert/hourly_watchdog.py` every hour at :45. For each user in `ACTIVE_USERS` (`src/parameters.py`, the same list the digest uses) with a holdings source (`SNAPTRADE` or `SHEETS`) and a Telegram chat ID, it checks the gates below in order, cheapest first, and stops at the first one that says skip:

1. **Watchdog on?** `watchdog_interval_hours` is above 0.
2. **In session?** The time falls inside the user's session (`regular`, `extended` or `24_7`), using real exchange calendars, so holidays and 13:00 half-days are handled.
3. **Due or tripwire?** The user is *due* when the current N-hour block differs from the block of their last successful scheduled check. If they aren't due and the tripwire is off, nothing happens this hour.
4. **Cheap snapshot.** Holdings, P&L, RSI and NATR, plus a price suited to the market phase (see [Prices outside regular hours](#prices-outside-regular-hours)). No Finnhub, Twelve Data or Claude calls. If the brokerage returns nothing, the run stops without marking the check as done, because an outage looks the same as an empty portfolio.
5. **Choose stocks.** Only stocks that changed get a new evaluation (see [When Claude is called](#when-claude-is-called)). Every other stock keeps its last recommendation.
6. **Daily cap.** At most `MAX_CLAUDE_CALLS_PER_USER_DAY` watchdog evaluations per user per day. When the cap binds, the stocks whose last recommendation was most severe go first.
7. **Evaluate.** Market context (from the daily cache) plus Claude, run only for the chosen stocks.
8. **Alert.** One Telegram message covers every held stock whose latest recommendation is an alert level and isn't in cooldown.

Errors are contained per user: one user's failure is printed with a traceback, and the other users still run.

### Parallel users (watchdog and digest)

Both the watchdog and the digest process several users at once (`MAX_PARALLEL_USERS`), and the digest also builds the universal content at the same time. Inside one user's digest, the Gemini news (several stocks at a time) runs while market data and Claude run, and the portfolio review waits for both. What keeps this safe:

- **Shared data keys:** Finnhub and Twelve Data use one key each, so fetching from them is serialized by one lock (`src/shared_locks.py`). The request rate stays the same as a single-user run, and a user who waited finds symbols another user just fetched in the market-context cache.
- **Shared files:** `data/watchdog_state.json` updates take an in-process lock, and the market-context cache is only read and written under the lock above. The crontab `flock` still keeps the watchdog and digest processes apart.
- **Per-user keys:** Claude, Gemini, SnapTrade, and Telegram calls use each user's own credentials, so they run fully in parallel.
- Log lines from different users interleave; watchdog lines start with `[user]`.

## Settings

### Global (`src/parameters.py`)

| Key | Default | Meaning |
|---|---|---|
| `ACTIVE_USERS` | `["Andres", "Mauricio", "Steven"]` | Users the digest **and** the watchdog run for. Each needs a profile in `user_profiles.py`; unknown names are skipped with a warning. Their `.env` settings still decide which digests they get and whether the watchdog runs for them. |
| `WATCHDOG.SESSION` | `"regular"` | Default session for all users. |
| `WATCHDOG.SESSIONS` | see file | `pre_open_minutes` / `post_close_minutes` are measured from each day's actual open and close. `extended` = 330 / 240, which is 4:00–20:00 ET on a full day. Set `pre_open_minutes` to 150 to start at 7:00. |
| `WATCHDOG.EXCHANGE_CALENDARS` | `["XNYS", "XTSE"]` | A day counts as a trading day if **any** listed exchange trades, so US Thanksgiving is a trading day because the TSX is open. |
| `WATCHDOG.DEFAULT_INTERVAL_HOURS` | `4` | Hours between scheduled checks. |
| `WATCHDOG.TRIPWIRE_DEFAULT` | `True` | Hourly price check between scheduled checks. |
| `WATCHDOG.REEVAL_NATR_MULTIPLE` | `1.0` | On a scheduled check, re-evaluate a stock that moved at least this many NATRs since its last evaluation. |
| `WATCHDOG.TRIPWIRE_NATR_MULTIPLE` | `2.0` | Between scheduled checks, force an evaluation at this many NATRs. |
| `WATCHDOG.MAX_EVALUATION_AGE_HOURS` | `24` | On a scheduled check, re-evaluate anything older than this. Also the maximum age of an evaluation that can still trigger an alert. |
| `WATCHDOG.EARNINGS_WINDOW_DAYS` | `7` | On a scheduled check, re-evaluate when earnings fall within this many days. |
| `WATCHDOG.ALERT_RECOMMENDATIONS` | `["urgent_sell"]` | Recommendations that send Telegram alerts. |
| `WATCHDOG.ALERT_COOLDOWN_HOURS` | `24` | No repeat alert for the same (user, symbol, recommendation) within this window. |
| `WATCHDOG.MAX_CLAUDE_CALLS_PER_USER_DAY` | `40` | Cap on watchdog Claude calls per user per market day. Digest calls don't count. |
| `WATCHDOG.FRESH_CONTEXT_MAX_AGE_MINUTES` | `30` | Cached quote and news older than this are re-fetched. |
| `WATCHDOG.MAX_PARALLEL_USERS` | `4` | Users checked at the same time each hour. |
| `DIGEST.MAX_PARALLEL_USERS` | `4` | Users whose digest content is built at the same time, alongside the universal content. |
| `DIGEST.SYMBOL_NEWS_CONCURRENCY` | `4` | Gemini news requests in flight at once per user. Lower it if a free-tier Gemini key keeps hitting per-minute limits. |
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
| `holdings_llm_effort` | `ANDRES_HOLDINGS_LLM_EFFORT=none` | Claude: `low` `medium` `high` `xhigh` `max`; DeepSeek: `none` (no thinking) `low` `high` `max` |
| `holdings_claude_model` / `holdings_claude_effort` | `ANDRES_HOLDINGS_CLAUDE_MODEL=claude-sonnet-5-5` | older names, still read when the `LLM` ones above are unset |
| `receive_holding_news` | `ANDRES_RECEIVE_HOLDING_NEWS=True` | `True` / `False` (default `True`): Gemini news bullets for each holding |
| `anthropic_api_key` | `ANDRES_ANTHROPIC_API_KEY=sk-ant-...` | the user's own Anthropic key (required when their model is Claude) |
| `deepseek_api_key` | `ANDRES_DEEPSEEK_API_KEY=sk-...` | the user's own DeepSeek key (required when their model is DeepSeek) |
| `gemini_api_key` | `ANDRES_GEMINI_API_KEY=...` | the user's own Gemini API key, used for the per-holding news and the portfolio review |

If the model doesn't accept the effort, the effort falls back to `LLM["HOLDINGS"]`'s effort when that's the same model, and otherwise to the provider's default (`CLAUDE_CONFIG` / `DEEPSEEK_CONFIG` `"effort"`). The model and effort apply wherever holdings are evaluated: watchdog runs and the digest.

The API keys are the only settings with no default: they never fall back to the shared `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / `GEMINI_API_KEY` used for the universal content. If a user's LLM key is unset, their holdings get no recommendation; if their Gemini key is unset, their holding cards get no news bullets. Either way an `[ERROR]` line is printed and other users are unaffected.

#### Choosing the LLM: Claude or DeepSeek (added 2026-10-16)

Every job that took a Claude model can take a DeepSeek model instead. `src/llm_models.py` picks the provider from the model name, and `src/deepseek_client.py` makes the DeepSeek calls.

| Job | Set in | Key |
|---|---|---|
| New-stock scoring (~37 calls a run) | `LLM["UNIVERSAL"]` in `parameters.py` | shared `ANTHROPIC_API_KEY` or `DEEPSEEK_API_KEY` |
| Holdings evaluation (watchdog and digest) | `LLM["HOLDINGS"]`, overridden per user by `<NAME>_HOLDINGS_LLM_MODEL` / `_EFFORT` | the user's own `<NAME>_ANTHROPIC_API_KEY` or `<NAME>_DEEPSEEK_API_KEY` |

**DeepSeek models** (checked 2026-10-16):
- **`deepseek-flash`** (V4.1-Flash): cheap and fast.
- **`deepseek-v4-pro`**: the stronger tier.

DeepSeek retired R1 and V3. Their difference is now the effort setting on either model: `none` turns thinking off (like V3 / `deepseek-chat`), and `low` / `high` / `max` turn it on (like R1 / `deepseek-reasoner`).

**What stays the same:**
- DeepSeek calls use the same tool schema and validation as Claude's.
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
| Digest | The same Claude handling (market data is still fetched, because the holding cards use it). For Gemini, the per-stock news loop stops at the first key failure (keeping news already fetched), and the portfolio review is skipped without a request. The payload's `api_key_issues` list says which key failed and why. |
| Gemini retries | Only timeouts, connection errors, per-minute 429s (honoring the API's retry delay), and 5xx are retried, up to 4 attempts. Key, billing, daily-quota, and other 4xx errors fail immediately. A 429 that survives the retries stops the news loop for that run but still lets the review try. |

## Schedules

**Regular and extended:** blocks are counted from the session start and restart each trading day. **24_7:** blocks are counted continuously in UTC, so daylight-saving changes never shift them, and intervals like 5 or 48 hours stay evenly spaced. Changing a user's interval or session makes them due immediately.

Check times with cron at :45 ET on a full trading day:

| Interval | `regular` (9:30–16:00) | `extended` (4:00–20:00) |
|---|---|---|
| 1 | 9:45, 10:45 … 15:45 (7 checks) | 4:45 … 19:45 (16 checks) |
| 2 | 9:45, 11:45, 13:45, 15:45 | 4:45, 6:45 … 18:45 |
| 4 | 9:45, 13:45 | 4:45, 8:45, 12:45, 16:45 |
| ≥ session length | 9:45 (once a day) | 4:45 (once a day) |

With `24_7` and an interval of 4, blocks start at 00/04/08/12/16/20 UTC, every day including weekends.

## When Claude is called

**On a scheduled (due) check**, a stock is evaluated if any of these is true:
- it has never been evaluated;
- its last evaluation is older than `MAX_EVALUATION_AGE_HOURS`;
- it moved at least `REEVAL_NATR_MULTIPLE` × NATR since its last evaluation;
- earnings are within `EARNINGS_WINDOW_DAYS`;
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
- **Which recommendations alert:** the latest *recorded* recommendation, not only this run's evaluations, so an `urgent_sell` from the morning digest also alerts. Recommendations older than `MAX_EVALUATION_AGE_HOURS` never alert.

## Files written

| File | Written by | Contents |
|---|---|---|
| `data/watchdog_state.json` | `holdings_state.py` (watchdog + digest) | Per user: last scheduled block, Claude calls today, and per stock the last evaluation (time, price, NATR, recommendation, rationale, SAR stop, next earnings date). Sold positions are dropped. |
| `data/market_context_cache.json` | `add_metrics.py` | Shared daily market context per stock. |
| `data/alerts_sent/telegram_alerts_timestamped.csv` | `hourly_watchdog.py` | Alert log used for the cooldown (kept 30 days). |
| `data/usage/api_usage.jsonl` | `usage_tracking.py` (watchdog + digest + universal) | One line per Claude or Gemini call: user, source, model, tokens, estimated cost. Append-only. |

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
1. **Header:** the period (☀️ pre-market or 🌙 post-market), the date, a greeting by the time of day in the user's home timezone, and when it was prepared.
2. **Notices** about anything missing.
3. **Overview card** (always open):
   - the portfolio's value, with the day's change in a coloured pill;
   - three figure boxes: unrealized gain, flagged positions and their share of the value, and new ideas;
   - "Needs attention" chips for each flagged stock, most urgent first;
   - "Earnings soon" for holdings reporting within the catalyst window;
   - **Jumping to a stock:** in the AMP version, tapping a stock in either list opens Your holdings and that stock's row, then scrolls to it and highlights it.
     - **How:** AMP email has no scroll action, so the row focuses its own header once it has opened (its `expand` event), and focusing scrolls it into view.
     - **Tested:** in a browser with real taps. Whether the Gmail app lets the email scroll its own view is unconfirmed; if not, the row still opens and is highlighted.
     - **HTML version:** the chips stay plain there, because jump links don't work in the Gmail app or into closed dropdowns.
   - the connected brokerage, with each account's value in a small dropdown.
4. **Portfolio risk:** beta, the share of the portfolio's value under each signal, and the portfolio review's notes.
5. **Markets & macro:** four key tiles (S&P 500, Nasdaq-100, VIX, 10Y yield) and the gauges. The full table is under "All indicators", followed by the top stories.
6. **Your holdings:** most urgent signal first, then largest position. A stock held in several accounts gets one row.
7. **Swing ideas.**
8. **Long-term ideas.**
9. **Glossary** (added 2026-10-17): a collapsed card with five category rows: signals and ideas, your portfolio, technical indicators, valuation and fundamentals, and market indicators. Each row lists the terms, abbreviations and indicators the email uses, and how to read them. The text is static, in `src/digest/glossary.py`.
10. **Footer:** "About this digest", showing what it cost and which model scored the ideas.

**Currencies (added 2026-10-17).**

| Amounts | Shown in | Source |
|---|---|---|
| Portfolio total, position values, P/L, account values | USD ("Your portfolio · USD", "Values in USD", "Unrealized · USD") | `pull_holdings_data` converts these to USD (`USD_MONEY_COLUMNS`) |
| A holding's share price | Its listing currency ("Price · CAD", "C$46.94") | The holding's `currency` field |
| Ideas: prices, trade levels, targets | "Prices in USD" | Assumed: picks carry no currency |
| Market dashboard ETF and oil prices | $ (US ETFs, e.g. "S&P 500 · SPY $771.79") | Assumed: the dashboard carries no currency |
| API costs | Their `currency` | `api_usage.currency` |

Wherever USD is assumed, the run log gets a `[NOTE] ... (not in the payload)` line.

**"Prev. day" and "Day" labels (added 2026-10-17).** Every daily change covers one full trading session, close to close: the previous session in a Pre-Market digest, today's in a Post-Market one. To make that clear, every daily change is labelled "Prev. day" in Pre-Market digests and "Day" in Post-Market ones:
- the overview's change pill;
- each holding's collapsed row and its Price box;
- the line at the top of Your holdings;
- the market tiles, the table's one-day column, and the markets summary;
- the yield-curve change;
- the inbox preview line.

It is built for a phone first: one column up to 600 px wide.

**Dropdowns (nested since 2026-10-17).** The email opens as a short overview, and everything else is a dropdown inside a dropdown:

| Level | What's shown while collapsed | Inside |
|---|---|---|
| Always open | Header, notices, overview card | – |
| 1. Section card | A coloured icon, the title and a count, and a coloured one-line summary, e.g. "Your holdings 10 · ● 6 risk alerts · ● 4 holds". The arrow is on the right, as in mobile apps. A section with nothing in it is a plain card saying why, e.g. "No setups scored 80+ today". | The section's key figures and its rows |
| 2. Row | **Holdings and ideas:** ticker, signal or score (green from 90), value, weight and day change<br>**Portfolio risk:** each review note's short title<br>**Markets & macro:** each story's headline | **Stock:** figures (a 2×2 grid), catalysts, its chart, the smaller dropdowns, and Yahoo/Finviz/TradingView buttons<br>**Note:** its full text<br>**Story:** why it matters, and a source button |
| 3. Small dropdown | "Why risk alert" (the reasoning, named after the signal), News, All indicators, extra figures, About this digest | The content |

- **Starting state:** everything starts collapsed. List any of `"risk"`, `"markets"`, `"holdings"`, `"swing"`, `"long_term"` in `DIGEST["OPEN_SECTIONS"]` to start those sections open.
- **Note titles:** Gemini's portfolio review now writes a 3–8 word title for each note. Payloads saved before 2026-10-17 use the note's first clause instead.
- **Narrow phones:** the layout fits a small phone without any width-specific CSS, because the Gmail app may not apply it.
  - **Figure boxes:** sets of four (a stock's figures, a trade plan, valuation) form a 2×2 grid.
  - **Row headers:** a signal badge can wrap under its ticker.
  - **Margins:** narrow everywhere.
  - **Extra tightening:** on screens up to 420 px, margins and fonts tighten further where supported.
  - **Tested:** with every dropdown open, in both versions, nothing runs past the right edge at 280–390 px wide, with or without that CSS. The test also covered six-figure values and the longest badges.

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
- **Trimming:** when a digest is bigger, `fit_for_gmail` leaves out the least important content first, and the footer says so. Order: the glossary (about 10 KB), low-priority news, the picks' extra figures, then the remaining news, starting with "hold" positions. A typical digest of about 10 holdings keeps the glossary, at about 96 KB in each version.
- **Never removed:** each holding's signal, figures and reasoning.
- **Capacity:** about 20 stocks fit; a larger portfolio needs Gmail's "View entire message" link for the rest.
- **Untrimmed:** the plain-text version is always complete.

### Fresh data in each digest

The pre- and post-market digests on the same day are built independently; nothing from the morning digest is copied into the afternoon one:

| Part | Freshness |
|---|---|
| Holdings, prices, daily P/L | Fetched every run. Daily P/L covers each stock's most recent regular session on its own exchange (`daily_pnl_session`): today's from the open (live, vs the previous close), otherwise the last completed session at its official close. So the 7:00 digest shows the previous trading day's move, and extended-hours prices never mix in. |
| Market data for holdings (`data/market_context_cache.json`) | Quote and headlines are re-fetched when older than `FRESH_CONTEXT_MAX_AGE_MINUTES` (30). Fundamentals and daily indicators are re-fetched after each market open and close; the close only counts from 16:01 ET, hence the digest at close + 10 minutes. |
| Claude recommendations | Fresh, except a watchdog evaluation from the last `REUSE_EVALUATION_MAX_AGE_MINUTES` (60) for a stock that has moved less than 1 NATR since. Set it to `0` to always evaluate fresh. |
| Gemini news and portfolio review | Fresh every run (no cache). |
| Currency | Money amounts (market value, cost basis, unrealized and daily P/L) are in USD, converted at the latest Yahoo rate (`src/fx_rates.py`; the last good rate in `data/fx_rates.json` if Yahoo fails). Per-share prices stay in the listing's currency, with `*_usd` copies for display. Unrealized P/L uses today's rate, so it excludes currency moves since purchase. |
| Universal content | Fresh every run, except the NASDAQ screener's ticker list. That list is downloaded once per morning and once per afternoon, and again whenever the stage-1 settings in `parameters.py` change. |

### Track record (`src/track_record.py`)

Every new holding evaluation (digest or watchdog) is appended to `data/track_record/personal.jsonl`, and every universal pick to `data/track_record/universal.jsonl`, with the price at the time. The files are never rewritten. To see how the advice played out:

```bash
venv/bin/python -m src.track_record                 # all recommendations
venv/bin/python -m src.track_record --since 2026-11-01
```

It prints, per recommendation (hold / trim / risk_alert / urgent_sell) and per universal list (long_term / swing): how many, the average return 1, 5, and 20 trading days later, the share that went up, the average return minus SPY's over the same days, and how many are still pending. A good `urgent_sell` record shows negative returns; a good `long_term` list beats SPY.

### Logs and failure alerts

Each run writes to `logs/digest.log` or `logs/watchdog.log` (`src/run_logging.py`): one line per message with a timestamp and the user it belongs to, rotating at `OPS.LOG_MAX_MB` (5 MB) and keeping `OPS.LOG_BACKUPS` (5) old files. Under cron the output no longer also goes to `digest.log` / `watchdog.log` in the project root, which now only catch crashes before logging starts; set `FINNBOT_LOG_ECHO=1` to see the output when piping it. Each run ends with a summary line (who was sent or checked, who failed, how long it took).

`OPS.ALERT_USERS` (default `["Andres"]`) get a Telegram alert when the digest's universal content fails, a user's digest or watchdog check fails, or a run crashes, at most once per `OPS.ALERT_COOLDOWN_HOURS` (6) for each kind of failure. The traceback is in the log.

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
| Finnhub rate limit (429) | Retried after 2, 5, and 15 s. |
| Finnhub rejects the key (401) | No more Finnhub calls this run. |
| Twelve Data per-minute limit | Waits for the next minute, up to 2 times per request. |
| Twelve Data daily credits used up, or key rejected | No more Twelve Data calls this run. |
| A Twelve Data request needs more credits than the plan allows per minute (one per ticker; the free plan allows 8). Only applies to indicators not computed from Yahoo prices (below). | Skipped at once instead of waiting it out, and not retried this run. The technical filter then keeps those tickers instead of dropping them all. |
| Yahoo's daily prices for the indicators can't be downloaded | Reported as a `Yahoo prices` issue. Those tickers have no indicators, which the completeness check also flags. |

**Technical indicators** come from Yahoo daily prices by default (`TECHNICAL_INDICATOR_SOURCE = "yahoo"`, `src/universal/recommendations/local_indicators.py`, added 2026-10-16).
- **Why:** on Twelve Data's free plan, the ~40 candidates and any holdings refresh of more than 8 stocks never got their indicators.
- **Covered:** rsi, ema, sma, bbands, sar, adx, and atr, on daily or weekly bars. They are computed from unadjusted bars with TA-Lib's conventions. Checked against Twelve Data, they matched to within 0.0001% (`tests/test_local_indicators.py` replays it on saved AAPL data).
- **beta:** market beta against SPY.
- **Everything else** (another indicator, an intraday interval, an option like `ma_type`) still goes to Twelve Data. Set the source to `"twelvedata"` to send everything there.
| More than `UNIVERSAL_DATA_QUALITY.MAX_MISSING_PCT` (20%) of tickers missing Yahoo, Finnhub, or Twelve Data data | Picks are still made, but flagged. |

Each problem is added to the universal payload's `universal_issues` (`{"source", "reason", "message"}`), so the email can say why picks are missing or based on partial data. Each is also sent to `OPS.ALERT_USERS` by Telegram, once per cooldown. The macro news (`src/universal/market_news.get_macro_stories`, still disabled) works the same way with the shared `GEMINI_API_KEY`: on failure it returns no stories plus the issue, and never raises.

### Universal picks and macro content in the payload

Each pick in `recommendations.top_long_term` / `top_swing` carries Claude's reasoning (added 2026-10-16) plus the data set in `RECOMMENDATION_FIELDS`:

| From | Long-term picks | Swing picks |
|---|---|---|
| Claude's evaluation | `longScore`, `swingScore`, `sentiment` (1–3+ years), `thesis`, `key_risk`, `sub_scores` | `swingScore`, `longScore`, `sentiment` (2–8 weeks), `thesis`, `entry_price`, `stop_loss`, `price_target`, `risk_reward`, `sub_scores` |
| `RECOMMENDATION_FIELDS["all"]` | name, sector, industry, price, market cap, 52-week range and change, analyst rating, target, and count, next earnings date | same |
| The list's own fields | P/E (trailing, forward), PEG, revenue and earnings growth, profit margin, ROE, debt-to-equity, free cash flow | latest RSI, EMA 9/21, Bollinger bands, Parabolic SAR, 50/200-day averages, beta, volume |

- **Swing levels:** the levels are only kept when they're consistent (0 < stop < entry < target). Otherwise all four are `null` and the log says why. `risk_reward` is the upside to the target per unit of risk to the stop, e.g. `2.5` for 2.5:1.
- **Thresholds:** a stock needs a total of at least `MIN_LONG_SCORE` / `MIN_SWING_SCORE` (80 out of 100) to be picked, so some days have fewer picks than `LONG_TERM_N_AMOUNT` / `SWING_TERM_N_AMOUNT`, or none.
- **Track record:** it also keeps each pick's sentiment and swing levels, for checking later whether they held up.

**Macro content.** It is switched with `UNIVERSAL_EXTRAS`, and both parts are on by default. Both run in the background while the picks are built, and both are included even when no picks could be made.
- `headline_news`: `{"macro_stories": [{"priority", "event_summary", "market_impact", "source_url"}]}` from Gemini with Google Search, using the shared `GEMINI_API_KEY`. A `source_url` is kept only when it matches a page the search returned; otherwise it's `null`.
- `market_data`: index, VIX, dollar, 10-year yield, and oil moves (1 day, 5 days, 1 month); the 10Y–2Y spread (FRED); CNN Fear & Greed; and the share of S&P 500 stocks above their 50-day average.

A section that can't be fetched holds `{"Error": ...}` and adds a `universal_issues` entry. It never stops the digest.

### API cost tracking (`src/usage_tracking.py`)

Every Claude and Gemini response is recorded in `data/usage/api_usage.jsonl`: holding evaluations (watchdog and digest), holding news, the portfolio review, and the universal scoring and macro news under the user `_shared`. Each user's digest payload gets an `api_usage` block with the estimated spend on their own keys:

```json
"api_usage": {
  "currency": "USD",
  "note": "Estimated from token counts at list prices as of 2026-10-16; ...",
  "this_digest":   {"anthropic": {"calls": 12, "input_tokens": 41000, "output_tokens": 6100, "cache_write_tokens": 0,
                                  "cache_read_tokens": 0, "cost_usd": 0.286},
                    "gemini":    {"calls": 9, "...": "...", "search_queries": 22, "cost_usd": 0.0123},
                    "total_cost_usd": 0.2983},
  "today":         {"...": "this digest plus earlier runs today (US Eastern date), watchdog included"},
  "month_to_date": {"...": "since the 1st (UTC calendar month, as the providers bill)"}
}
```

These are estimates from token counts and list prices. They can't see credits, discounts, or use of the same key outside Finnbot. If a model has no price, its calls are still counted but cost $0, and it is listed under `unpriced_models`. Gemini's Google Search grounding is free for the first `GEMINI_FREE_SEARCH_REQUESTS_PER_MONTH` (5,000) requests per key each month, so search cost only counts once a user passes that.

**When the section started:** each user's payload also has `personal_started_at`, the moment their personal section began. It's given in their home timezone (`<NAME>_TIMEZONE`) and in US Eastern: `{"home_timezone", "home", "home_iso", "us_eastern", "us_eastern_iso"}`. The display strings look like `Friday, October 16, 2026, 7:02 AM EDT`. If the timezone isn't a valid IANA name, the home fields are `null` and only Eastern time is shown; `check_config` reports the setting as INVALID.

**Keeping prices current:** prices are in `params_dict["API_PRICES"]` as dated lists. To add a price change, append an entry with its `"from"` date. Usage before that date keeps the old price, so past months don't change. For example, the gemini-3.6-flash entry already holds the doubled price that starts 2027-01-01. Update `AS_OF` when you check the price pages listed in `SOURCES`. Model ids match on the longest prefix, so `claude-opus-5-5` gets its own price, not `claude-opus-5`'s.

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
| `src/market_session.py` (new) | Market phases, session windows, N-hour block ids, cache refresh boundary | Lets one hourly cron serve per-user intervals and sessions, with holidays, half-days and DST handled by exchange calendars. Pure functions of `now` so they can be tested at fixed times. |
| `src/personalized/user_settings.py` (new) | Reads, validates and defaults per-user settings | One place that turns `.env` values into settings, so a typo can't crash the run or send an invalid API request. |
| `src/personalized/holdings_state.py` (new) | `data/watchdog_state.json` storage | The memory needed to evaluate "only when needed", shared by the watchdog and the digest. |
| `src/personalized/add_metrics.py` | Whole file: market context for holdings, plus the daily cache | Gives the sell-side LLM the same evidence as the buy-side LLM, and keeps API use bounded at higher check frequencies. |
| `src/personalized/recommendation.py` | Whole file: Claude hold/trim/risk_alert/urgent_sell evaluation, per-user model and effort, session context, refusal fallbacks, state recording | Replaced placeholder rules that referenced non-existent columns. |
| `src/personalized/LLM_hold_instr.py` | Whole file: prompt and rubric | Keeps the sell-side prompt separate from the code, mirroring `LLM_rec_instr.py`. |
| `src/telegram_alert/hourly_watchdog.py` | Everything below the `Claude-authored` banner, plus `--dry-run` / `--now`. `load_and_prune_alert_logs` and `is_alert_suppressed` are Andres's originals, unchanged. | Per-user scheduling and gates, plus fixes: alerts were sent before the cooldown check, the duplicate key could never match, and one user's failure stopped the loop. |
| `src/personalized/pull_holdings_data.py` | `src.*` imports, `user_name` / `claude_overrides` in `__init__`, session-price methods, `symbols` parameter on `get_holdings_digest_payload`, guard for a missing `GOOGLE_APPLICATION_CREDENTIALS` | Needed by the watchdog. The guard fixes a `TypeError` crash. |
| `src/telegram_alert/send_telegram.py` | Imports only | The watchdog couldn't import it from the project root. |
| `src/personalized/user_profiles.py` | Five new profile keys per user | Per-user settings. |
| `src/parameters.py` | `WATCHDOG`, `CLAUDE_REFUSAL_FALLBACK`, `ALLOWED_CLAUDE_MODELS` | Global defaults and limits. |
| `requirements.txt`, `.gitignore`, this document | New pins and ignore rules; documentation | Reproducible installs, and no runtime state committed to the repo. |
