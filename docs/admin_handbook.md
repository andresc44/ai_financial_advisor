# Finnbot admin handbook

Andres's reference for running Finnbot: the commands first, then every part of the pipeline and where its details live. Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-23, at Andres's request. State as of main `b46792c` plus that day's later changes.

The other docs: [new_user_guide.txt](new_user_guide.txt) (what users choose and pay), [watchdog.md](watchdog.md) (the full technical reference), [model_selection.md](model_selection.md) (why each model), [operations.md](operations.md) (VM monitoring and backups).

---

## 1. Commands

Run everything from the project root. On Windows use `venv\Scripts\python.exe -X utf8` (and set `PYTHONUTF8=1`, since the logs contain emoji); on the VM, `venv/bin/python`. Below, `python` means whichever applies.

### Digest (`src/digest/digest_payload.py`)

| Command | What it does | Costs money? | Emails anyone? |
|---|---|---|---|
| `python src/digest/digest_payload.py` | **The real run, what cron does every hour at :10.** Reads the Parameters tab and the sign-up sheet, sends the holiday notice, earnings reminders, and monthly scorecards if due, then builds and emails the Pre-Market or Post-Market digest if one is due, to users who haven't had it yet, and records them as sent. Does nothing else when no digest is due. | Yes, when a digest is due | Yes |
| `... --check` | Says whether a digest is due now and for whom. Builds and sends nothing, reads no sheet. | No | No |
| `... --check --now 2026-10-23T16:10:00-04:00` | The same, as if it were that time (needs a UTC offset). | No | No |
| `... --payload-only` | Builds the digest that is due now with live data and saves each user's payload to `test_output/digest_payloads/<user>_<date>_<period>_<HHMM>.json`. No HTML, no email, nothing recorded as sent (so it never changes what cron sends). Reads the Parameters tab first. | Yes (a full run: about $0.17 admin + about $0.03 per user) | No |
| `... --payload-only --period Pre-Market` (or `Post-Market`) | The same, for that digest now, outside its window. **The usual "full run".** | Yes | No |
| `... --from-payload test_output/digest_payloads/<file>.json` | Renders a saved payload and emails it to its user, subject starting with `[Test]`. Fetches no data and records nothing as sent. | No | Yes, a [Test] |
| `... --from-payload <file> --no-email` | Only renders it, to `src/digest/html_reports/<user>_<date>_<period>.html` and `.amp.html`. | No | No |

A full test run is the last two together: `--payload-only --period Post-Market`, then `--from-payload` on the file it printed.

### Watchdog (`src/telegram_alert/hourly_watchdog.py`)

| Command | What it does |
|---|---|
| `python src/telegram_alert/hourly_watchdog.py` | The real hourly check (cron, at :45): reviews holdings that moved, are near earnings, or have old reviews, and sends sell alerts on Telegram. |
| `... --dry-run` | Shows which users are due and which stocks would be reviewed. No LLM, no Telegram, no writes. |
| `... --dry-run --now 2026-10-23T13:45:00-04:00` | The same, as if it were that time. |

### Settings, users, and checks

| Command | What it does |
|---|---|
| `python -m src.config.remote_settings --sync` | Reads the Parameters tab now, applies what's valid, writes the status back, and messages the admins about changes. Every digest run does this first. |
| `python -m src.config.remote_settings --show` | Lists the settings from the sheet in force on this computer (`data/remote_settings.json`). |
| `python -m src.users.import_signup --list` | Lists every sign-up answer and stops. |
| `python -m src.users.import_signup --sync` | Syncs every user from the sign-up sheet into `.env` and `ACTIVE_USERS`, as each cron run does. Add `--dry-run` to only show what would change. |
| `python -m src.users.import_signup <first name or email>` | Imports one person's answer. Options: `--dry-run` (show only), `--no-activate` (leave `ACTIVE_USERS` alone), `--overwrite` (replace an existing user whose email differs), `--enter-keys` (then type their missing keys, hidden, to save them), `--sheet <ID>` (another answers sheet). |
| `python -m src.config.check_config` | Checks every shared and per-user `.env` setting: OK, MISSING, INVALID, or unset. Run it after adding or changing a user. |
| `python -m src.config.check_config --template <Name>` | Prints the `.env` block for a new user, to fill in. |
| `python -m src.tracking.track_record [--since 2026-10-01]` | How past recommendations played out (holdings signals and ideas), from `data/track_record/`. |

### Tests and checks on the email

| Command | What it does |
|---|---|
| `python -m pytest` | The test suite (about 380 tests, a minute, offline: no API is called). A test stuck for 120 s prints every thread's stack (`pytest.ini`). |
| `npx amphtml-validator --html_format AMP4EMAIL src/digest/html_reports/<file>.amp.html` | Validates the AMP version after a template change. Gmail falls back to the plain version when it's invalid. |
| `python src/universal/market_news.py` | Fetches today's macro stories once and prints them (one Gemini call on the shared key). |

### On the VM (cron)

| Command | What it does |
|---|---|
| `bash scripts/cron_job.sh digest` | The digest under the shared lock, pinging healthchecks.io before and after (`HEALTHCHECKS_PING_KEY`). |
| `bash scripts/cron_job.sh watchdog` | The same for the watchdog. |
| `bash scripts/cron_job.sh backup` | The nightly Drive backup (`scripts/backup_to_drive.sh`, rclone). |

```cron
45 * * * * cd ~/ai_financial_advisor && bash scripts/cron_job.sh watchdog
10 * * * * cd ~/ai_financial_advisor && bash scripts/cron_job.sh digest
30 2 * * * cd ~/ai_financial_advisor && bash scripts/cron_job.sh backup
```

Each job waits up to 15 minutes for the lock, so the watchdog and the digest never write state at the same time. Setup: [operations.md](operations.md).

---

## 2. A day in Finnbot

| When (ET) | What runs |
|---|---|
| Every hour at :10 | The digest run: settings sync, sign-up sync, holiday notice (weekday NYSE closures, once), earnings reminders (held stocks reporting within 24 h, once per report), monthly idea scorecards. Then a digest only if one is due. |
| From 07:00 until the open | The **Pre-Market digest** (the first :10 run after 07:00). |
| 16:10 (close + 10 min) | The **Post-Market digest**; retried for 4 hours if missed. |
| Every hour at :45 | The **watchdog**, for users with holdings, Telegram, and an interval above 0. |
| 02:30 UTC | The **backup** to Google Drive. |

Trading days come from the exchange calendars (`WATCHDOG.EXCHANGE_CALENDARS`: NYSE and Toronto). A day either one trades is a digest day.

**One digest run, in order:**
1. Read the Parameters tab (settings), then the sign-up sheet (users and their `.env` settings).
2. Decide what's due (`digest_schedule.due_digest`) and for whom (`<NAME>_RECEIVE_*_DIGEST`).
3. Build, at the same time: the **universal content** (the ideas, the markets dashboard, the macro news) and up to 4 users' **personal content** (holdings, reviews, news, the portfolio review). Finnhub and Twelve Data calls are serialized by one lock, since they share one key.
4. For each user, as soon as both are ready: render the HTML and AMP versions, trim to fit Gmail (about 100 KB), email from the digest account, and record as sent.

---

## 3. The ideas (universal content, admin's keys)

Starts from the NASDAQ screener's US list: market cap from `MKTCAP_MIN` (9,000 $M from the sheet since 2026-10-22; 10,000 in `parameters.py`), volume from 1 M shares, price from $30. About 600 stocks.

**Long-term** (`YAHOO_FILTERS`, `FINNHUB_FILTERS`, `TWELVEDATA_FILTERS`):
- Yahoo quality screen, then a Finnhub 12-month confirmation, then ADX ≤ 45. About 90 stocks remain.
- DeepSeek V4.1 Flash (high effort) scores each out of 100: valuation, financial health, growth momentum, competitive moat.
- **Close calls** (within `IDEA_RESCORE` 5 points of the bar) are scored twice more, and the middle score is kept.
- **Picks:** up to `LONG_TERM_N_AMOUNT` 3 at `MIN_LONG_SCORE` 85+, under these rules:
  - every pick from a different sector (`PICK_SECTOR_RULES.LONG_TERM` `all_different`);
  - at most `LONG_TERM_MAX_TECH_PICKS` 2 tech (`TECH_SECTORS`: Technology and Communication Services), which may share a sector;
  - at least `LONG_TERM_MIN_NON_TECH_PICKS` 1 non-tech, which may score from `LONG_TERM_NON_TECH_MIN_SCORE` 80.
- **Cards:** forward P/E, PEG, revenue growth, margin, RSI 14, 12-month return, the 52-week range, and the score breakdown.

**Swing** (`SWING_SCREEN`):
- A looser fundamentals screen, then a setup in the daily bars: a pullback in an intact trend, or a new uptrend (EMA 9 over EMA 21 within 5 sessions).
- Before scoring: not still falling, no earnings within 5 sessions, and a trade plan worked out in code (stop under support, target at resistance, at least 1.5:1).
- The best 40 by the code's own checks are scored, on technical setup, risk/reward (computed from the plan), momentum and volume, and catalysts.
- **Picks:** up to 5 at `MIN_SWING_SCORE` 75+ that are still early in their move (`SWING_EARLY_ENTRY`), with more than half from different sectors.

**Also in the universal content:**
- The **markets dashboard**: indexes, VIX, yields, the 10Y-2Y spread, Fear & Greed, breadth, put/call. Each signal is graded green, yellow or red at the levels in `MARKET_SIGNALS`.
- The **macro news**: 4 stories from Gemini with Google Search, on the shared key.

Details: [watchdog.md](watchdog.md), sections "Swing screen" and "Universal picks and macro content in the payload". Why these models: [model_selection.md](model_selection.md).

---

## 4. The holdings (personal content, each user's keys)

**Holdings source** (`<NAME>_HOLDINGS_SOURCE`): SnapTrade (the brokerage, read-only), a Google Sheet, or `NO_HOLDINGS` (ideas only). Prices and FX come from Yahoo; money amounts are converted to USD, and each card shows its stock's own currency.

**One user's section:**
1. **Fetch holdings.** Problems (an expired connection, an unshared sheet, no sync for 96 h) go to the user on Telegram and appear in the email.
2. **Link new positions to recent ideas.** A position bought by the end of the next trading day after an idea gets the idea's label and plan, stop/target alerts, and the monthly scorecard (`src/tracking/idea_positions.py`).
3. **Fetch the news**, in the background while the next step runs:
   - Gemini, low thinking, 3 holdings per request (`DIGEST.SYMBOL_NEWS_BATCH_SIZE`).
   - News fetched earlier the same day is reused (`DIGEST.NEWS_REUSE`), except for a stock that moved more than 1 NATR, changed signal, or reported earnings.
4. **Market data and checks.** The market data goes into a shared daily cache. Then the code-computed checks (`holding_checks.py`): trend, pullback or run, the 4 turn signs, stretched, earnings, fund or leveraged.
5. **The review.**
   - Claude Haiku 5.5 (low) on the user's Anthropic key gives each holding a label in the rubric's order: urgent sell → profit sell → risky → buy more → hold.
   - An urgent sell or profit sell gets 2 more reviews, and the majority wins (`HOLDINGS_RECHECK`).
   - A review less than 60 minutes old for an unmoved stock is reused.
6. **The portfolio review:** up to 5 notes on the whole portfolio, by the same Haiku model.
7. **Costs:** this user's API costs for this run and the last 30 days, theirs and the admin's (`usage_tracking.py`).

**Labels in practice:**
- **Buy more** needs a real pullback (1.5+ ATR off the 20-day high) plus 2 turn signs.
- **A fresh swing idea that started a new uptrend usually shows hold.** Kept that way on 2026-10-23.

**The watchdog** repeats steps 1-5 between digests for stocks that need it:
- which stocks: moved 1+ NATR since their last review, earnings within 7 days, or a review older than 24 h;
- the tripwire forces an early review at 2 NATRs;
- at most 40 reviews per user per day.

Details: [watchdog.md](watchdog.md), sections "How one run works" and "When Claude is called".

---

## 5. The email

**Sections, in order:**
1. The overview, in the user's home currency.
2. Risk and the portfolio review.
3. Markets & macro.
4. Your holdings: one card per stock, most urgent first.
5. Swing ideas.
6. Long-term ideas.
7. The glossary: only the terms that day's email uses.
8. About this digest: costs for this run and the last 30 days, the user's and the admin's.

**Versions:** an HTML version and an AMP version (`DIGEST.AMP_EMAIL`).
- The AMP version's dropdowns work only in Gmail, and only for a reader who has allowed the sender `finnbot107@gmail.com` (dynamic email; new_user_guide 1.4).
- Everyone else gets the HTML version.

**Size:** emails are trimmed toward 100 KB to fit Gmail's limit. With more than 10 holdings, each card keeps one story, except risky and urgent-sell ones.

**Phone testing:** Andres reads it in the Gmail app, in dark mode, with text enlarged about 1.75x. Test layout changes there.

---

## 6. Telegram messages

| Message | To | When | Starts with "Urgent alert"? |
|---|---|---|---|
| Sell alert (`WATCHDOG.ALERT_RECOMMENDATIONS`: urgent_sell) | User | Watchdog, once per stock per 24 h | Yes |
| A swing idea's stop or target reached | User | Digest or watchdog, once each | Yes |
| Earnings reminder | User | Hourly digest run, about 24 h before, once per report | No |
| Monthly idea scorecard (vs the S&P 500) | User | Once a month | No |
| Holdings can't be read, or an API key stopped working | User | Once per cooldown | No |
| New or edited sign-up, user added or removed, name clash | Admins | Sign-up sync | No |
| Settings changed or rejected in the Parameters tab, or changed in parameters.py | Admins | Settings sync, once per change | No |
| US market holiday | Admins | Once, on the day | No |
| Run failed or crashed, settings sheet unreadable, data source missing | Admins | At most once per 6 h per problem (`OPS.ALERT_COOLDOWN_HOURS`) | No |

Every message is addressed to its reader by name: "🚨 Urgent alert for <name>:" for the urgent ones, "Hi <name>," for the rest (`send_telegram.send_telegram_message`). Admins are `OPS.ALERT_USERS` (Andres only). Everyone else's notices say "Contact Andres to address this issue." instead of the fix.

---

## 7. Settings

**Three layers, last wins:**
1. `src/config/parameters.py`: the defaults, in git.
2. The **Parameters tab** of the sign-up spreadsheet (`SETTINGS_TAB_GID` 402243155):
   - every setting has a row with its value, default, Finnbot's status, and a note;
   - values are checked, saved in `data/remote_settings.json`, and applied by every process;
   - a change in `parameters.py` wins over the tab at the next sync (the newest change wins);
   - `ACTIVE_USERS` is read-only there.
3. `.env` on one machine: only `UNIVERSAL_LLM_MODEL` / `UNIVERSAL_LLM_EFFORT`, which override `LLM.UNIVERSAL`. Since 2026-10-23 the Windows PC's `.env` sets only `UNIVERSAL_LLM_MODEL=deepseek-flash` (the same as `parameters.py`); leave both out on the VM so the tab decides.

**Per user:** `<NAME>_*` keys in `.env`, written by the sign-up sync from the form's answers (the sheet is the reference: a hand edit to a form setting is put back). API keys arrive privately and are added by hand or with `--enter-keys`.

**Adding a setting:**
1. Add it to `parameters.py`.
2. Add a note to `src/config/parameter_notes.py` (a test fails until it has one).
3. The next sync adds its row.

**Keep every machine's `parameters.py` the same:** each machine that runs a digest syncs the same tab, and an older copy would put its old defaults back. Pull on the VM after each change.

Details: [watchdog.md](watchdog.md), "Changing settings from the spreadsheet".

---

## 8. Users and onboarding

1. **Sign-up:** the person fills in the form. The answers sheet `SIGNUP_FORM_SHEET_ID` must be shared with the service account; with Editor access, it can write the settings status back. They send their keys privately.
2. **Sync:** the next sync writes their `.env` settings and adds them to `ACTIVE_USERS` once nothing required is missing. Missing keys get `KEY=None` placeholders, and you get a "New Finnbot user added" message.
3. **Required keys:**
   - Anthropic, with holdings;
   - Gemini, only with holding news on;
   - SnapTrade or a sheet ID, depending on the holdings source;
   - Telegram is optional.
4. **Check:** `python -m src.config.check_config`.
5. **Removing a user:** delete their sign-up answer. They drop out of `ACTIVE_USERS` at the next sync, and their `.env` settings stay.

---

## 9. Models and costs

| Job | Model | Whose key |
|---|---|---|
| Idea scoring | DeepSeek V4.1 Flash, high | Admin (`DEEPSEEK_API_KEY`) |
| Macro news | Gemini 3.6 Flash, low thinking | Admin (`GEMINI_API_KEY`) |
| Holdings reviews (digest and watchdog), portfolio review | Claude Haiku 5.5, low | User (`<NAME>_ANTHROPIC_API_KEY`) |
| Holding news | Gemini 3.6 Flash, low thinking, 3 per request | User (`<NAME>_GEMINI_API_KEY`) |

**Measured on 2026-10-22, per digest:**
- **Admin:** about $0.17 (DeepSeek $0.17, macro news $0.004), so about $7 a month with both digests.
- **A user with 10 holdings:** about $0.03 (reviews $0.02, news $0.008, the review $0.0015).

**What users pay:** the table in new_user_guide 3.10, from about $0.07 a month (1 stock, 1 digest, news off) to $2.79 (20 stocks, both digests, news on), plus the watchdog. **Gemini's prices double on 2027-01-01**, which roughly doubles the news cost.

**Where costs come from:** `data/usage/api_usage.jsonl` records every call; prices are in `API_PRICES`. A count of the 5,000 free monthly searches isn't possible: Gemini doesn't report searches with a response schema.

---

## 10. Files

| Path | What it holds |
|---|---|
| `.env` | Every key and per-user setting. Never commit it; keep the keys in a password manager. |
| `data/watchdog_state.json` | Per user: each stock's latest review, the watchdog's schedule, and the LLM calls made today. |
| `data/market_context_cache.json` | Each stock's daily market data, shared by users. |
| `data/digest_state.json` | Who received which digest. |
| `data/holding_news/<user>.json` | Today's news per holding, for reuse later the same day. |
| `data/remote_settings.json` | The settings accepted from the Parameters tab. |
| `data/idea_positions.json` | Holdings bought from ideas, open and closed. |
| `data/track_record/*.jsonl` | Every review and every idea, with the price then. |
| `data/usage/api_usage.jsonl` | Every API call's tokens and cost. |
| `data/alerts_sent/` | The Telegram log behind the cooldowns. |
| `logs/digest.log`, `logs/watchdog.log` | The run logs (rotated at 5 MB). `logs/<job>.status` holds the last run's one-line status. |
| `test_output/digest_payloads/` | Payloads saved by `--payload-only`. |
| `src/digest/html_reports/` | Rendered emails. |

**Backed up nightly:** track record, usage, watchdog and digest state, alert log, and idea positions (`scripts/backup_filter.txt`), plus `logs/`.

**Rebuilt by the next run:** the market data cache, FX rates, held-stock news, and the settings cache.

---

## 11. When something breaks

| Symptom | Where to look |
|---|---|
| No email arrived | `logs/digest.log`; `python src/digest/digest_payload.py --check`; the healthchecks.io `finnbot-digest` check (once on the VM). |
| A user's section is empty or says a key failed | The top of their email, and your Telegram (the full fix). |
| A setting change didn't apply | Its status in the Parameters tab (❌ says why); `python -m src.config.remote_settings --show`; `.env` overrides. |
| Ideas list short or empty | The log lists each stock left out and why (score, sector rule, tech limit, too far along). |
| News missing a link | Only links Google Search actually returned are kept; the log shows "N/M source links verified". |
| Dropdowns don't open in Gmail | The reader hasn't allowed `finnbot107@gmail.com` (guide 1.4), the AMP version failed validation, or the email is over 30 days old. |
| Tests stall | Rerun; after 120 s a stuck test prints every thread's stack. |

---

## 12. Pending (2026-10-23)

1. **VM:** set it up on Google Cloud ([vm_setup.md](vm_setup.md)): the free e2-micro first, measured on one full run, and e2-small (about $12 a month) only if that's too small. Until it exists, nothing runs on a schedule.
2. **Heartbeat:** connect healthchecks.io to the VM's cron.
3. **Drive backup:** set it up on the VM.

Smaller:
- Confirm the news cost estimates the next time a digest runs (cost per stock at 3 per request, and how many holdings the evening digest refreshes).

Done on 2026-10-23: the live Google Form rebuilt from `docs/finnbot_signup_form.gs`; `.env`'s `UNIVERSAL_LLM_EFFORT=high` removed; a swing idea's target hit confirmed as urgent, like a stop. VM setup: [vm_setup.md](vm_setup.md).
