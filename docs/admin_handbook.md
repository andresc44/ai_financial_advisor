# Finnbot admin handbook

Andres's reference for running Finnbot: the commands first, then every part of the pipeline and where its details live. Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-23, at Andres's request, and brought up to date on 2026-10-26. State: branch `feature/watchdog-digest-upgrades` (not merged to `main`), running on the VM.

**Contents.** Sections 1-11 are the reference: 1 commands, 2 a day in Finnbot, 3 the ideas, 4 the holdings, 5 the email, 6 Telegram messages, 7 settings, 8 users and onboarding, 9 models and costs, 10 files, 11 quick troubleshooting. Sections 12-18 are the memory: 12 how it is set up and running, 13 why it is built this way (the decisions and their evidence), 14 how it was tested, 15 **upgrading to a new LLM and comparing models**, 16 **diagnosing a problem and recreating Finnbot from nothing**, 17 the comparison tool, 18 open items.

The other docs: [new_user_guide.txt](new_user_guide.txt) (what users choose and pay), [watchdog.md](watchdog.md) (the full technical reference), [model_selection.md](model_selection.md) (why each model), [operations.md](operations.md) (VM monitoring and backups).

---

## 1. Commands

Run everything from the project root. On the VM (where Finnbot runs), use `venv/bin/python`; on a Windows PC, `venv\Scripts\python.exe -X utf8` (and set `PYTHONUTF8=1`, since the logs contain emoji). Below, `python` means whichever applies. On the VM, a manual run that sends or spends goes under the shared lock (`flock -w 900 /tmp/finnbot.lock ...`) so it can't overlap cron, and any change to files or `git pull` goes under maintenance mode (below).

### Digest (`src/digest/digest_payload.py`)

| Command | What it does | Costs money? | Emails anyone? |
|---|---|---|---|
| `python src/digest/digest_payload.py` | **The real run, what cron does every hour at :10.** Reads the Parameters tab and the sign-up sheet, sends the holiday notice, earnings reminders, and monthly scorecards if due, then builds and emails the Pre-Market or Post-Market digest if one is due, to users who haven't had it yet, and records them as sent. Once every email is out, sends each user's urgent sell alerts on Telegram. Does nothing else when no digest is due. | Yes, when a digest is due | Yes |
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
| `python src/telegram_alert/hourly_watchdog.py` | The real hourly check (cron, at :45): reviews holdings that moved, are near earnings (within 4 trading days, or the trading day after a report), or have old reviews, and sends sell alerts on Telegram. Starts with the same settings and sign-up sync as the digest. |
| `... --dry-run` | Shows which users are due and which stocks would be reviewed. No LLM, no Telegram, no writes. |
| `... --dry-run --now 2026-10-23T13:45:00-04:00` | The same, as if it were that time. |

### Settings, users, and checks

| Command | What it does |
|---|---|
| `python -m src.config.remote_settings --sync` | Reads the Parameters tab now, applies what's valid, writes the status back, and messages the admins about changes. Every digest run does this first. |
| `python -m src.config.remote_settings --show` | Lists the settings from the sheet in force on this computer (`data/remote_settings.json`). |
| `python -m src.users.import_signup --list` | Lists every sign-up answer and stops. |
| `python -m src.users.import_signup --sync` | Syncs every user from the sign-up sheet into `.env` and `ACTIVE_USERS`, as each cron run does. It writes for real: `--dry-run` only works with one person's name (below), not with `--sync`. |
| `python -m src.users.import_signup <first name or email>` | Imports one person's answer. Options: `--dry-run` (show only), `--no-activate` (leave `ACTIVE_USERS` alone), `--overwrite` (replace an existing user whose email differs), `--enter-keys` (then type their missing keys, hidden, to save them), `--sheet <ID>` (another answers sheet). |
| `python -m src.config.check_config` | Checks every shared and per-user `.env` setting: OK, MISSING, INVALID, or unset. Run it after adding or changing a user. |
| `python -m src.config.check_config --template <Name>` | Prints the `.env` block for a new user, to fill in. |
| `python -m src.tracking.track_record [--since 2026-10-01]` | How past recommendations played out (holdings signals and ideas), from `data/track_record/`. |
| `python -m src.llm.gemini_key_rotation --status` | Whether any Gemini slot has switched from a free key to its paid key (the free-key trial is over; see section 9). `--reset <universal\|Andres\|all>` clears a switch. |

### Tests and checks on the email

| Command | What it does |
|---|---|
| `python -m pytest` | The test suite (about 440 tests, a minute or two, offline: no API is called). A test stuck for 120 s prints every thread's stack (`pytest.ini`). |
| `npx amphtml-validator --html_format AMP4EMAIL src/digest/html_reports/<file>.amp.html` | Validates the AMP version after a template change. Gmail falls back to the plain version when it's invalid. |
| `python src/universal/market_news.py` | Fetches today's macro stories once and prints them (one Gemini call on the shared key). |

### On the VM (cron)

| Command | What it does |
|---|---|
| `bash scripts/cron_job.sh digest` | The digest under the shared lock, pinging healthchecks.io before and after (`HEALTHCHECKS_PING_KEY`). |
| `bash scripts/cron_job.sh watchdog` | The same for the watchdog. |
| `bash scripts/cron_job.sh backup` | The nightly Drive backup (`scripts/backup_to_drive.sh`, rclone). |

```cron
45 * * * * ulimit -n 65536 && cd ~/ai_financial_advisor && bash scripts/cron_job.sh watchdog
10 * * * * ulimit -n 65536 && cd ~/ai_financial_advisor && bash scripts/cron_job.sh digest
30 2 * * * ulimit -n 65536 && cd ~/ai_financial_advisor && bash scripts/cron_job.sh backup
```

Each job waits up to 15 minutes for the lock, so the watchdog and the digest never write state at the same time. A job that waited out a digest that is still running reports a skip, not a failure. The `ulimit` raises cron's 1024 open-file limit, which the digest ran out of on 2026-10-23. Setup: [operations.md](operations.md).

### Maintenance mode (before changing anything on the VM)

Cron runs whatever is checked out, so every change to code, templates, `parameters.py`, `.env`, or docs, and every `git pull`, checkout, or merge, goes between these (Andres's rule, 2026-10-25):

| Command | What it does |
|---|---|
| `bash scripts/maintenance.sh on [hours]` | Skips the watchdog and digest until it ends (1 hour by default, up to 6; it turns itself off). Waits for a running job to finish first. |
| `bash scripts/maintenance.sh off` | Ends it early. Run it as soon as the tests pass. |

During a digest window (the first :10 run after 07:00 ET until the open, and 16:10 to 20:10 ET), keep it under an hour: a skipped digest is retried at the next hour's run, but not after its window closes. The backup still runs.

### Claude Code session on the VM (tmux, auto-restart)

Claude Code (with the Telegram channel) runs in the tmux session `claude`, so closing the laptop or losing SSH doesn't stop it. Reconnect with `tmux attach -t claude`. Start Claude only from inside tmux, never from Cursor's terminal: those processes die with the Cursor connection.

The scripts live in `~/bin`, outside this repo (they are not versioned):

| Script | What it does |
|---|---|
| `claude-tg` | Runs `claude --continue --channels plugin:telegram@claude-plugins-official` in a loop and restarts it if it dies (a crash within 15 s restarts without `--continue`). A clean exit (`/exit`, Ctrl-D) stops the loop and creates `~/.claude-tg-stop`. |
| `claude-tg-ensure` | Starts `claude-tg` in the `claude` tmux session if no Telegram-channel Claude is running. Does nothing if `~/.claude-tg-stop` exists. |
| `claude-tg-health` | If Claude is running but its Telegram plugin process (`bun run ... telegram`) is gone, stops that Claude so it is restarted. Skips a Claude under 3 minutes old and does nothing if `~/.claude-tg-stop` exists. It checks processes, not Telegram's servers. |

```cron
@reboot sleep 30 && /home/ubuntu/bin/claude-tg-ensure >> /home/ubuntu/claude-tg-ensure.log 2>&1
*/5 * * * * /home/ubuntu/bin/claude-tg-ensure >> /home/ubuntu/claude-tg-ensure.log 2>&1
0 * * * * /home/ubuntu/bin/claude-tg-health >> /home/ubuntu/claude-tg-ensure.log 2>&1
```

- **Stop it on purpose:** `touch ~/.claude-tg-stop`, then end Claude (or `/exit`). Cron then leaves it alone.
- **Turn auto-restart back on:** run `claude-tg` in the tmux window (it deletes the marker), or `rm ~/.claude-tg-stop` and wait up to 5 minutes (or run `claude-tg-ensure`).
- **Limits:** a `tmux kill-session` is revived within 5 minutes unless the marker exists; a restart interrupts a task in progress (`--continue` reloads the conversation, not the unfinished work). Log: `~/claude-tg-ensure.log`.

---

## 2. A day in Finnbot

| When (ET) | What runs |
|---|---|
| Every hour at :10 | The digest run: settings sync, sign-up sync, holiday notice (weekday NYSE closures, once), earnings reminders (held stocks reporting within 24 h, once per report), monthly idea scorecards. Then a digest only if one is due. |
| From 07:00 until the open | The **Pre-Market digest** (the first :10 run after 07:00). |
| 16:10 (close + 10 min) | The **Post-Market digest**; retried for 4 hours if missed. |
| Every hour at :45 | The **watchdog**, for users with holdings, Telegram, and an interval above 0: due 4 hours (default) after their last check or digest, between 4:00 and 20:00 ET on trading days. |
| 02:30 UTC | The **backup** to Google Drive. |

Trading days come from the exchange calendars (`WATCHDOG.EXCHANGE_CALENDARS`: NYSE and Toronto). A day either one trades is a digest day.

**One digest run, in order:**
1. Read the Parameters tab (settings), then the sign-up sheet (users and their `.env` settings).
2. Decide what's due (`digest_schedule.due_digest`) and for whom (`<NAME>_RECEIVE_*_DIGEST`).
3. Build, at the same time: the **universal content** (the ideas, the markets dashboard, the macro news) and up to 4 users' **personal content** (holdings, reviews, news, the portfolio review). Finnhub and Twelve Data calls are serialized by one lock, since they share one key.
4. For each user, as soon as both are ready: render the HTML and AMP versions, trim to fit Gmail (about 100 KB), email from the digest account, and record as sent.
5. Once every email is out, from the main thread: send each user's urgent sell alerts on Telegram (`hourly_watchdog.send_digest_alerts`), for every user with holdings and a chat ID, watchdog on or off, for the stocks in their digest. They share the watchdog's alert log and 24-hour cooldown, so neither job repeats the other's alert, and a failed send is left for the watchdog to retry.

---

## 3. The ideas (universal content, admin's keys)

Starts from the NASDAQ screener's US list: market cap from `MKTCAP_MIN` (8,000 $M), volume from 1 M shares, price from `LASTSALE_MIN` ($15), the same on the Parameters tab and in `parameters.py` since 2026-10-26 (they were 10,000 and $30, with 9,000 in force from the sheet). Several hundred stocks (661 on 2026-10-25 at the old floors).

**Long-term** (`YAHOO_FILTERS`, `FINNHUB_FILTERS`, `TWELVEDATA_FILTERS`):
- Yahoo quality screen, then a Finnhub 12-month confirmation, then ADX ≤ 45. About 100 stocks remain.
- The best `LONG_TERM_SCREEN.MAX_CANDIDATES` 35 of those go on (since 2026-10-25; 0 = all), ranked in code on the rubric's own figures (PEG and forward P/E; debt, current ratio and free cash flow; revenue and earnings growth and operating margin; gross margin and return on equity as a stand-in for a moat). A missing figure counts as the middle. See `long_term_screen.py`.
- DeepSeek V4.1 Flash (high effort) scores each of them out of 100: valuation, financial health, growth momentum, competitive moat.
- **Close calls** (within `IDEA_RESCORE` 5 points of the bar) are scored twice more, and the middle score is kept.
- **Picks:** up to `LONG_TERM_N_AMOUNT` 3 at `MIN_LONG_SCORE` 85+, under these rules:
  - every pick from a different **industry** (`PICK_GROUP_BY` `industry`, since 2026-10-25; `sector` is the old way; `PICK_SECTOR_RULES.LONG_TERM` `all_different`), tech picks included, so two semiconductor stocks can't both be picked;
  - a third of the list, rounded up, outside tech (`LONG_TERM_MIN_NON_TECH_SHARE` 0.33: 1 of 2-3, 2 of 4-6, 3 of 7-9, 4 of 10; none for a list of 1), and the rest may be tech (`TECH_SECTORS`: Technology and Communication Services);
  - the non-tech picks may score from `LONG_TERM_NON_TECH_MIN_SCORE` 80.
  - these mix rules only choose between candidates (since 2026-10-25): when nothing else can take a stock's place (no non-tech stock at 80+, or one industry above the bar), the list is topped up with the best left-out stocks, so it is still full. Swing lists work the same way.
- **Swing non-tech minimum** (`SWING_MIN_NON_TECH` `[[4, 1], [8, 2]]`, since 2026-10-25): lists of 4 to 7 ideas hold at least 1 non-tech pick, 8 or more at least 2, if a non-tech stock cleared `MIN_SWING_SCORE` (it replaces the lowest-scoring tech pick when the list is full). Below 4 ideas there is no tech rule.
- **Cards:** forward P/E, PEG, revenue growth, margin, RSI 14, 12-month return, the 52-week range, and the score breakdown.

**Swing** (`SWING_SCREEN`):
- A looser fundamentals screen, then a setup in the daily bars: a pullback in an intact trend, or a new uptrend (EMA 9 over EMA 21 within 5 sessions).
- Before scoring: not still falling, no earnings within 5 sessions, and a trade plan worked out in code (stop under support, target at resistance, at least 1.5:1).
- The best 40 by the code's own checks are scored, on technical setup, risk/reward (computed from the plan), momentum and volume, and catalysts.
- **Picks:** up to 5 at `MIN_SWING_SCORE` 75+ that are still early in their move (`SWING_EARLY_ENTRY`), with more than half from different industries (`PICK_SECTOR_RULES.SWING` `majority_different`).

**Also in the universal content:**
- The **markets dashboard**: indexes, VIX, yields, the 10Y-2Y spread, Fear & Greed, breadth, put/call. Each signal is graded green, yellow or red at the levels in `MARKET_SIGNALS`.
- The **macro news**: 4 stories from Gemini 3.5 Flash-Lite with Google Search, on the shared key.

Details: [watchdog.md](watchdog.md), sections "Swing screen" and "Universal picks and macro content in the payload". Why these models: [model_selection.md](model_selection.md).

---

## 4. The holdings (personal content, each user's keys)

**Holdings source** (`<NAME>_HOLDINGS_SOURCE`): SnapTrade (the brokerage, read-only), a Google Sheet, or `NO_HOLDINGS` (ideas only). Prices and FX come from Yahoo; money amounts are converted to USD, and each card shows its stock's own currency.

**One user's section:**
1. **Fetch holdings.** Problems (an expired connection, an unshared sheet, no sync for 96 h) go to the user on Telegram and appear in the email.
2. **Link new positions to recent ideas.** A position bought by the end of the next trading day after an idea gets the idea's label and plan, stop/target alerts, and the monthly scorecard (`src/tracking/idea_positions.py`).
3. **Fetch the news**, in the background while the next step runs:
   - Gemini 3.5 Flash-Lite (no thinking), 3 holdings per request (`DIGEST.SYMBOL_NEWS_BATCH_SIZE`), on the user's own key, which needs billing on.
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
- which stocks: moved 1+ NATR since their last review, earnings within 4 trading days or 1 trading day after a report, or a review older than 24 h;
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
8. About this digest: costs for this run and the last 30 days, the user's and the admin's. The email's size in KB is listed too.

**Versions:** an HTML version and an AMP version (`DIGEST.AMP_EMAIL`).
- The AMP version's dropdowns work only in Gmail, and only for a reader who has allowed the sender `finnbot107@gmail.com` (dynamic email; new_user_guide 1.4).
- Everyone else gets the HTML version.

**Size:** emails are trimmed toward 100 KB to fit Gmail's limit. With more than 10 holdings, each card keeps one story, except risky and urgent-sell ones.

**Phone testing:** Andres reads it in the Gmail app, in dark mode, with text enlarged about 1.75x. Test layout changes there.

---

## 6. Telegram messages

| Message | To | When | Starts with "Urgent alert"? |
|---|---|---|---|
| Sell alert (`WATCHDOG.ALERT_RECOMMENDATIONS`: urgent_sell) | User | Right after each digest's emails (2026-10-26, also for a user without the watchdog) and from the watchdog, once per stock per 24 h | Yes |
| A swing idea's stop or target reached | User | Digest or watchdog, once each | Yes |
| Earnings reminder | User (with a chat ID) | Hourly digest run, about 24 h before, once per report | No |
| Monthly idea scorecard (vs the S&P 500) | User | Once a month | No |
| Holdings can't be read, or an API key stopped working | User | Once per cooldown | No |
| New or edited sign-up, user added or removed, name clash, or an answer the importer couldn't read | Admins | Sign-up sync | No |
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
3. `.env` on one machine: only `UNIVERSAL_LLM_MODEL` / `UNIVERSAL_LLM_EFFORT`, which override `LLM.UNIVERSAL`. The VM's `.env` has them commented out, so the tab decides; keep it that way. (Per-user watchdog settings, `<NAME>_WATCHDOG_*` and `<NAME>_LONG_RECS` / `_SWING_RECS`, are `.env` too, but they are per-user settings, below.)

**Per user:** `<NAME>_*` keys in `.env`, written by the sign-up sync from the form's answers (the sheet is the reference: a hand edit to a setting the form covers is put back). API keys and the sheet ID arrive privately and are added by hand or with `--enter-keys`. Three kinds are never written by the sync, so they're yours to set: `<NAME>_WATCHDOG_INTERVAL_HOURS` / `_SESSION` / `_TRIPWIRE` (unset means the defaults `WATCHDOG.DEFAULT_INTERVAL_HOURS` 4, `SESSION` extended, `TRIPWIRE_DEFAULT` on, which are also rows on the Parameters tab), and `<NAME>_LONG_RECS` / `_SWING_RECS` (1 to 10 ideas; unset means 3 and 5).

**Adding a setting:**
1. Add it to `parameters.py`.
2. Add a note to `src/config/parameter_notes.py` (a test fails until it has one).
3. The next sync adds its row.

**Keep every machine's `parameters.py` the same:** each machine that runs a digest syncs the same tab, and an older copy would put its old defaults back. When a default changes in `parameters.py` the newest change wins over the tab, so after you change a value on the tab, copy it into `parameters.py` too (done for `MKTCAP_MIN`, `LASTSALE_MIN`, and the earnings window on 2026-10-26).

Details: [watchdog.md](watchdog.md), "Changing settings from the spreadsheet".

---

## 8. Users and onboarding

1. **Sign-up:** send them [setup_instructions.md](setup_instructions.md) first (two pages: the accounts and keys to get ready, in order; [new_user_guide.txt](new_user_guide.txt) has the details). Then they fill in the form. The answers sheet `SIGNUP_FORM_SHEET_ID` must be shared with the service account as an **Editor** (the sign-up sync only reads it, but the settings sync writes the Parameters tab's status columns to the same spreadsheet). They send their keys, and their sheet ID if they chose a Google Sheet, privately: the form no longer asks for the sheet ID, how it's shared, or the watchdog's interval, session, or tripwire (2026-10-26). A user changes their answers by filling in the form again.
2. **Sync:** every cron run, digest (:10) and watchdog (:45), starts by syncing (`src/ops/cron_start.py`). It writes their `.env` settings and adds them to `ACTIVE_USERS` once nothing required is missing. Missing keys get `KEY=None` placeholders, and a user who chose Telegram gets an empty `<NAME>_TELEGRAM_CHAT_ID=` line, and one who chose a Google Sheet an empty `<NAME>_SHEETS_ID=` line, to fill in. Once you've filled in their keys, the next run (within the hour) activates them: you get a "New Finnbot user added" message, and the Parameters tab's `ACTIVE_USERS` row shows them in that same run.
3. **Required keys:**
   - Anthropic, with holdings;
   - Gemini, only with holding news on, on a project with billing turned on (the free tier has no web search);
   - SnapTrade keys, or a sheet ID, depending on the holdings source;
   - Telegram is optional (a user who chose it needs a chat ID).
4. **Check:** `python -m src.config.check_config`.
5. **Removing a user:** delete their sign-up answer. They drop out of `ACTIVE_USERS` at the next sync, and their `.env` settings stay.
6. **If the form's wording changes:** the importer matches the holdings answer by keyword (SnapTrade, Google Sheet, No holdings) and the others by exact question title. An answer it can't read keeps an active user active and tells you on Telegram; on 2026-10-26 a reworded form briefly switched everyone off before this guard. After editing the form, check each user with `python -m src.users.import_signup <Name> --dry-run` (shows what would change, writes nothing) and read the `[signup]` lines in `logs/watchdog.log`.

---

## 9. Models and costs

| Job | Model | Whose key |
|---|---|---|
| Idea scoring | DeepSeek V4.1 Flash, high | Admin (`DEEPSEEK_API_KEY`) |
| Macro news | Gemini 3.5 Flash-Lite, with Google Search | Admin (`GEMINI_API_KEY`) |
| Holdings reviews (digest and watchdog), portfolio review | Claude Haiku 5.5, low | User (`<NAME>_ANTHROPIC_API_KEY`) |
| Holding news | Gemini 3.5 Flash-Lite, with Google Search, 3 per request | User (`<NAME>_GEMINI_API_KEY`) |

**Measured on 2026-10-22, per digest:**
- **Admin:** about $0.17 (DeepSeek $0.17, macro news $0.004), so about $7 a month with both digests.
- **A user with 10 holdings:** about $0.03 (reviews $0.02, news $0.008, the review $0.0015), with Gemini 3.6 Flash. Gemini 3.5 Flash-Lite cut the news about in half (2026-10-26 comparison: $0.0087 against $0.0175 per run for 11 holdings).

**What users pay:** the table in new_user_guide 3.10, from about $0.07 a month (1 stock, 1 digest, news off) to $2.27 (20 stocks, both digests, news on), plus the watchdog. Neither current model has an announced price change; `gemini-3.6-flash`, which doubles on 2027-01-01, is no longer used.

**Prompt caching (since 2026-10-24):** the holdings reviews' shared instructions (~5,000 tokens of the ~16,800 each review sends) are cached, so each review after a run's first pays a tenth for them. That cuts the review cost by about a fifth (`CLAUDE_PROMPT_CACHE`, `src/llm/claude_cache.py`). The first holding is reviewed alone so the rest can read the cache, which adds a few seconds to each run.

**Where costs come from:** `data/usage/api_usage.jsonl` records every call; prices are in `API_PRICES`. A count of the 5,000 free monthly searches isn't possible: Gemini doesn't report searches with a response schema.

**Gemini's free tier doesn't work for news (tested 2026-10-26).** Andres tried free-tier keys (`GEMINI_FREE_API_KEY` for the macro news, `ANDRES_GEMINI_FREE_API_KEY` for his holdings), tried before the paid ones, and the morning digest went out without news: Google Search grounding has no free-tier quota on any current model (a grounded request gets `429 RESOURCE_EXHAUSTED`; plain requests work), and that 429 classified as a per-minute rate limit, which didn't switch to the paid key. Fixed the same day (a rate limit that outlasts the retries now switches), and the free keys are removed from `.env`. Only `gemini-robotics-er-2-preview` searched on a free key, with about twice as many stocks left without news. `src/llm/gemini_key_rotation.py` stays in the code, unused without a free key. The model comparison is in [model_selection.md](model_selection.md#the-news-model-2026-10-26).

**The news model (2026-10-26):** `GEMINI_MODELS` is `gemini-3.5-flash-lite` for both jobs, tunable from the Parameters tab. Every Gemini key needs billing on; a project gets 5,000 free searches a month, far more than Finnbot uses.

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
| `data/maintenance_until` | When maintenance mode ends (epoch seconds); written by `scripts/maintenance.sh`. |
| `data/gemini_key_state.json` | Which Gemini slots switched from a free key to paid (absent: none). |
| `data/track_record/*.jsonl` | Every review and every idea, with the price then. |
| `data/usage/api_usage.jsonl` | Every API call's tokens and cost. |
| `data/alerts_sent/` | The Telegram log behind the cooldowns. |
| `logs/digest.log`, `logs/watchdog.log` | The run logs (rotated at 5 MB). `logs/<job>.status` holds the last run's one-line status. |
| `test_output/digest_payloads/` | Payloads saved by `--payload-only`. |
| `src/digest/html_reports/` | Rendered emails. |

**Backed up nightly:** track record, usage, watchdog and digest state, alert log, and idea positions (`scripts/backup_filter.txt`), plus `logs/`.

**Rebuilt by the next run:** the market data cache, FX rates, held-stock news, and the settings cache.

---

## 11. When something breaks (quick table; the full playbook is section 16)

| Symptom | Where to look |
|---|---|
| No email arrived | `logs/digest.log`; `python src/digest/digest_payload.py --check`; maintenance mode left on (`cat data/maintenance_until`); the healthchecks.io `finnbot-digest` check. |
| A user's section is empty or says a key failed | The top of their email, and your Telegram (the full fix). |
| A setting change didn't apply | Its status in the Parameters tab (❌ says why); `python -m src.config.remote_settings --show`; `.env` overrides. |
| Ideas list short or empty | The log lists each stock left out and why (score, industry rule, tech limit, too far along). |
| No news on holdings or macro | The log's Gemini lines: a 429 means the key's project has no billing (the free tier has no search); check the key and its billing. |
| An urgent sell showed in the email but not on Telegram | The user needs a chat ID in `.env`; check `data/alerts_sent/` for the 24 h cooldown and `logs/digest.log` for "urgent alerts" errors. |
| A user vanished from the digest | `ACTIVE_USERS` and the `[signup]` lines in `logs/watchdog.log`: a key or sheet ID still `None` or empty takes them out until it's filled in. |
| News missing a link | Only links Google Search actually returned are kept; the log shows "N/M source links verified". |
| Dropdowns don't open in Gmail | The reader hasn't allowed `finnbot107@gmail.com` (guide 1.4), the AMP version failed validation, or the email is over 30 days old. |
| Tests stall | Rerun; after 120 s a stuck test prints every thread's stack. |

---

## 12. How Finnbot is set up and running (as of 2026-10-26)

Sections 1 to 11 say what each part does. This part says what is actually installed, so that a year from now you can tell what you're looking at.

### 12.1 The machine

| | |
|---|---|
| What | One Ubuntu VM, user `ubuntu`, project in `~/ai_financial_advisor` (the live deployment: cron runs whatever is checked out there) |
| Size | 2 vCPUs, about 3.8 GB of memory, 4 GB of swap, a 38 GB disk (about 12 GB used on 2026-10-26) |
| Software | Python 3.14.4 from `/usr/bin/python3.14`, a venv at `~/ai_financial_advisor/venv` built from `requirements.txt` (exact pins; `requirements-dev.txt` adds pytest) |
| Clock | UTC. The crontab starts with `CRON_TZ=America/Toronto`, but the jobs have been observed running on UTC (the backup at 02:30Z), so it appears to have no effect on this cron. The digest doesn't depend on it: the digest decides what is due from the exchange calendar, not from the clock. |
| Git | `origin` is `git@github.com:andresc44/ai_financial_advisor.git`, pushed with the VM's SSH key. The live branch is `feature/watchdog-digest-upgrades`; it has never been merged to `main`. |
| Provider | **Not recorded in the repo: fill this in.** `docs/vm_setup.md` was written for Google Cloud, but this machine is bigger than that guide's e2-micro, and `.env` has a key named `OVH_VM_PW` (the VM's login password; never print it), which suggests OVH. Write here: the provider, where its console is, whose account and card pay for it, and how to restart the machine from the console if SSH stops answering. |
| Measured load | A full digest for three users: about 25 minutes, peak memory 788-798 MB, about 80 open files at the end (`logs/digest.log`'s last line shows both). A watchdog run: seconds to a minute or two. |

### 12.2 What runs on it

| What | How it starts | Notes |
|---|---|---|
| Digest | cron, every hour at :10 → `scripts/cron_job.sh digest` | Does a full run only when a digest is due (section 2). |
| Watchdog | cron, every hour at :45 → `scripts/cron_job.sh watchdog` | |
| Backup | cron, 02:30 → `scripts/cron_job.sh backup` | rclone to Google Drive (section 10, operations.md). |
| Claude Code session | `~/bin/claude-tg*` from cron (`@reboot`, every 5 minutes, hourly health check), inside the tmux session `claude` | Andres talks to Claude Code from Telegram. Not part of Finnbot itself and not versioned (section 1). |

Everything else is on someone else's servers. All the services Finnbot depends on, what each is for, where its credential lives, and what breaks without it:

| Service | Used for | Credential (`.env` key, names only) | If it's gone |
|---|---|---|---|
| Anthropic | Holdings reviews, portfolio review (each user's own key); the ideas' scoring too if `LLM.UNIVERSAL` is Claude | `<NAME>_ANTHROPIC_API_KEY`, shared `ANTHROPIC_API_KEY` (required by `check_config`) | That user's holdings get no signal; the top of their email says why and Andres gets a Telegram message. |
| DeepSeek | Scoring the ideas (`LLM.UNIVERSAL`, Flash 4.1 high) | `DEEPSEEK_API_KEY` | No ideas that day (the digest still goes out; `universal_issues`). |
| Gemini | News per holding (each user's key) and the macro news (shared) | `<NAME>_GEMINI_API_KEY`, `GEMINI_API_KEY`; the project must have billing on | No news (stories are skipped; the email says why). |
| Finnhub | Fundamentals, analyst data, earnings dates, headlines (free plan, paced to `FINNHUB_CALLS_PER_MINUTE` 60) | `FINNHUB_API_KEY` | Fewer ideas and holdings facts; the log lists what failed. |
| Yahoo Finance (`yfinance`, no key) | Prices, FX, company names, the first screens, technical indicators | none | Almost everything. It prints `HTTP Error 401 Invalid Crumb` now and then at the start of a run; failed fetches are retried (4 lines at the start of the 2026-10-26 pre-market digest, and the run went on normally). |
| Twelve Data, FRED, Cboe, CNN Fear & Greed | Optional indicators, the yield curve, put/call, mood | `TWELVEDATA_API_KEY`, `FRED_API_KEY` (the other two need none) | The matching market signal shows as unavailable. |
| SnapTrade | Reading a user's brokerage positions (read-only) | `<NAME>_SNAPTRADE_CLIENT_ID`, `_CONSUMER_KEY` | That user's holdings can't be read: they and Andres get a notice (a connection that stops syncing for 96 hours too). |
| Google service account `finnbot-service@finnbot-504921.iam.gserviceaccount.com` (project `finnbot-504921`) | Reading users' holdings sheets; reading the sign-up answers; **writing** the Parameters tab's status columns | `GOOGLE_APPLICATION_CREDENTIALS` (path of its JSON file) | Sheets users get a notice; the settings sync and sign-up sync fail (logged, nothing is switched off). |
| Gmail, `finnbot107@gmail.com` | Sending every digest by SMTP (`smtp.gmail.com:587`) | `GMAIL_USER`, `GMAIL_APP_PASSWORD` | No email at all. Users also must allow this sender for dynamic email (new_user_guide 1.4). |
| Telegram bot `@Finn31_bot` | Every Telegram message to users and admins | `TELEGRAM_BOT_TOKEN`; each user's `<NAME>_TELEGRAM_CHAT_ID` (they must press Start first) | No alerts or notices. |
| healthchecks.io | Heartbeat of the three cron jobs | `HEALTHCHECKS_PING_KEY` | Jobs still run, unmonitored. |
| Google Drive (rclone remotes `gdrive` and the encrypted `finnbot-backup`) | Nightly backup and the readable logs | `~/.config/rclone/rclone.conf` on the VM; the two crypt passwords are in Andres's password manager | No backups; the `finnbot-backup` check turns red. |
| GitHub | The code (`andresc44/ai_financial_advisor`) and the glossary page (`andresc44/finnbot-glossary`, public, served by GitHub Pages at `DIGEST.GLOSSARY_URL`) | The VM's SSH key | The glossary link goes stale (a failed publish is only a warning). |
| A Google Form and its answers spreadsheet | Sign-ups, and the **Parameters** tab of the same spreadsheet | `SIGNUP_FORM_SHEET_ID`, `SETTINGS_TAB_GID` (402243155) | No new sign-ups; the last accepted settings stay in force. |

**The answers spreadsheet must be shared with the service account as an Editor**, not a Viewer: the sign-up sync only reads it, but the settings sync writes each setting's status back to the Parameters tab, and fails without write access. (Section 8 used to say Viewer was enough.)

### 12.3 The users today

Three: Andres (the admin; SnapTrade, news on), Mauricio (a Google Sheet, news off), and Claudia (SnapTrade, news off). All three get both digests and Telegram with the watchdog, on the defaults in `parameters.py` (every 4 hours, extended hours, tripwire on). Mauricio has `MAURICIO_LONG_RECS` set. Who is active comes from the sign-up sheet, not from `parameters.py` (section 8).

### 12.4 The environment file, by purpose

`.env` is never committed. Its keys, grouped (names only; `check_config` shows which are required and which are missing):
- **Shared services:** `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`, `FINNHUB_API_KEY`, `TWELVEDATA_API_KEY`, `FRED_API_KEY`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `GOOGLE_APPLICATION_CREDENTIALS`.
- **Sheets and monitoring:** `SIGNUP_FORM_SHEET_ID`, `SETTINGS_TAB_GID`, `HEALTHCHECKS_PING_KEY`; optional `FINNBOT_BACKUP_REMOTE`, `FINNBOT_BACKUP_KEEP_DAYS`, `FINNBOT_LOGS_REMOTE`.
- **Per user, `<NAME>_...`** (written by the sign-up sync, except keys and the three never-synced settings): `EMAIL`, `INSTITUTION`, `TIMEZONE`, `HOME_CURRENCY`, `HOLDINGS_SOURCE`, `RECEIVE_PREMARKET_DIGEST`, `RECEIVE_POSTMARKET_DIGEST`, `RECEIVE_HOLDING_NEWS`, `SHEETS_ID`, `TELEGRAM_CHAT_ID`, and the keys `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `SNAPTRADE_CLIENT_ID`, `SNAPTRADE_CONSUMER_KEY`; yours to set: `WATCHDOG_INTERVAL_HOURS` / `_SESSION` / `_TRIPWIRE`, `LONG_RECS`, `SWING_RECS`.
- **Not Finnbot's:** other keys in the file (`OPENROUTER_API_KEY`, `FINNHUB_LOGIN`, `OVH_VM_PW`) belong to Andres's other tools or the machine; nothing in `src/` reads them.
- **Where the only copy of each secret is:** Andres's password manager (every key, the two rclone crypt passwords, the Gmail app password, the Google service account JSON, the VM login). A VM wiped without them can't be rebuilt (16.5).

---

## 13. Why it is built this way: the decisions and the evidence

The order is the order they were made (2026-08-09 first commit; most of it between 2026-10-15 and 2026-10-26). For each: what was chosen, why, what evidence decided it, and how to change it. The long versions of the numbers are in [model_selection.md](model_selection.md), [watchdog.md](watchdog.md), and the comments beside each setting in `src/config/parameters.py`.

**How the project grew.** August: Yahoo and Finnhub screens for new stocks, a Claude HTML email, SnapTrade and Telegram. September: Gemini headlines and macro indicators, a Google Sheets holdings source, cron, Claude scoring of candidates. 2026-10-15 to 10-17: the holdings watchdog, per-user keys, the track record, run logs and failure alerts, the test suite (84 tests then, 443 now), healthchecks and the Drive backup, DeepSeek as an alternative provider, and a mobile-first email with an AMP version. 2026-10-18 to 10-26: the model comparison, the swing screen, the sign-up form and settings sheet, cost cuts, email size work, the VM, and the watchdog and alert refinements below.

### 13.1 Scheduling and safety

- **One digest check every hour, decided from the exchange calendar** (2026-10-15). Not fixed clock times in cron: that would need editing twice a year for daylight saving and every holiday and half-day. `digest_schedule.due_digest` reads `exchange_calendars` (NYSE and Toronto; a day either trades is a digest day), and remembers who already got which digest (`data/digest_state.json`) so a retry is harmless. A missed Post-Market digest is retried for 4 hours. *Change:* `WATCHDOG.EXCHANGE_CALENDARS`, `DIGEST.PRE_MARKET_TIME_ET`.
- **One lock for every job** (`/tmp/finnbot.lock`, taken by `scripts/cron_job.sh`). The digest, watchdog and backup all write the same state files, and the digest alone takes about 25 minutes, so jobs wait (up to 15 minutes) instead of overlapping. A job that waited out a *running digest* ends as a success with the note "Skipped: an earlier digest is still running" (2026-10-25: whenever a long digest held the lock past 15 minutes, the :45 watchdog waited it out and turned its healthcheck red, for nothing). Any other holder, or a holder that has died, is still exit code 75 and a red check.
- **healthchecks.io and the Drive backup** (2026-10-16): Finnbot's own failure alerts only work while it runs; healthchecks catches the VM, cron or the disk failing. rclone uses the `drive.file` scope (it can only see files it made) and a crypt remote on top, because the state files show what each user holds. The logs go to Drive as `.txt` so Andres can read them on a phone.
- **Maintenance mode and the open-file limit** (2026-10-23/24). On 2026-10-23 every digest failed with `[Errno 24] Too many open files`: cron starts jobs with a soft limit of 1024 open files, and a digest (parallel users and requests, sockets and files at once) goes past it. `ulimit -n 65536` is set in both the crontab and `cron_job.sh`. Code edited while a run starts can crash or import a mix of old and new files, so `scripts/maintenance.sh` pauses the jobs (and cron_job.sh reports the skip as a success). Andres's rule is in `CLAUDE.md`: pause before any file change or git operation on the VM. *Leak watch:* each run's last log line has the open-file count and peak memory; a number that keeps climbing is a leak.

### 13.2 The AI models

- **Ideas on DeepSeek V4.1 Flash at high effort** (decided 2026-10-22, started 2026-10-17). The first scoring runs used Claude Sonnet (about $1.23 for 37 candidates); DeepSeek Flash scored the same candidates for $0.06 and found the same top two. Without thinking its scores moved up to 31 points between runs, so thinking was added (low: 3.5 points on average; 2026-10-19), and on 2026-10-22 ten settings were compared on identical inputs against Claude Opus 5.5 (90 long-term and 30 swing candidates, three Opus runs): Flash 4.1 high ranked long-term candidates as well as Sonnet did (rank correlation 0.89 for both; Opus against itself 0.98), passed exactly the same swing stocks as Opus at 70 in every run, and cost $0.23 a run against $2.59 for Sonnet and $6.06 for Opus. DeepSeek V4 Pro cost four times as much and did no better. Its answers are sometimes misshapen JSON, so they are repaired and retried (`deepseek_client.validated_answer`). *Change:* `LLM.UNIVERSAL`; per machine `UNIVERSAL_LLM_MODEL` / `_EFFORT` in `.env` (currently unset, so the Parameters tab decides).
- **Pass marks follow the model's score level.** Flash 4.1 scores long-term ideas about 2 points above Opus, so the bar went to **85** and the swing bar to **75** (2026-10-22). A different model shifts the level (Haiku scores 5-6 points below Opus), so a model change means re-checking `MIN_LONG_SCORE`, `MIN_SWING_SCORE` and `LONG_TERM_NON_TECH_MIN_SCORE` (15.5).
- **Close calls are scored again** (2026-10-22). The same stock can score 4 points apart between two runs (13.5 at most), enough to cross a pass mark by luck; a score within 5 points of a bar is scored twice more and the middle one kept (`IDEA_RESCORE`), unless the list is already full of clear passes. A holdings `urgent_sell` or `profit_sell` gets two more reviews and the majority wins (`HOLDINGS_RECHECK`), so one odd reading can't send an alert.
- **Holdings on Claude Haiku 5.5 at low effort, for every user** (2026-10-22). The default first moved from Sonnet to Haiku because Sonnet's reviews averaged 342 output tokens at medium effort, so about 85% of its $0.027 per stock was reading its 11.7K input tokens; Haiku is about $0.0013 per stock, and keeps users' holdings with Anthropic (a DeepSeek default would have sent them to DeepSeek without anyone choosing it). The comparison of 29 holdings (Andres's 10, a test portfolio's 19) then showed that with the rubric rewritten around **facts computed in code** (`holding_checks.py`: the long-term trend, pullback or run, the four signs of a turn, stretched, days to earnings, fund or not), Haiku at low effort gave Opus's label for 93% of holdings (medium: 91%) at about $0.05 for both portfolios against $2.04 for Opus. Without those facts, models disagreed on judgement calls the code can settle. The sign-up form stopped asking for a model, and `<NAME>_HOLDINGS_LLM_MODEL` is ignored. *Change:* `LLM.HOLDINGS`.
- **Prompt caching** (2026-10-24, `CLAUDE_PROMPT_CACHE`): the holdings reviews' shared instructions (about 5,000 of 16,800 tokens each) are cached, cutting the review cost by about a fifth; the first holding is reviewed alone so the rest can read the cache.
- **News on Gemini 3.5 Flash-Lite** (2026-10-26): see 13.3 and 15.

### 13.3 Gemini: costs, the free tier, and the news model

- **Cost cuts of 2026-10-23.** Gemini had become about 85% of a user's bill, 90% of that output tokens, mostly thinking (no request set a level). Changes: `GEMINI_CONFIG.effort` low and an output cap; 5 then **3 holdings per request** (on Andres's 10 holdings, 5 per request kept 11 stories for about $0.012 a run, and one per request found 13 for about $0.022; Andres chose 3 in between); the morning's news is reused in the evening (`DIGEST.NEWS_REUSE`) unless the stock moved more than 1 NATR, changed signal, or reported; and the **portfolio review moved to Claude Haiku** with the user's Anthropic key. Measured on 10 holdings: news $0.1165 → $0.0168 a digest, nothing for a later digest the same day; the review $0.017 → $0.0014. A side effect found the same day: at low thinking, Gemini wrote articles' own addresses instead of the search results' redirect links, which can't be verified, so the prompts now ask for the redirect link by name (in a test of 6 requests, 33 of 33 links were search links).
- **Source links.** With a response schema Gemini returns no grounding metadata, so `_verify_source_urls` follows each redirect link one hop and keeps the article URL; anything that doesn't redirect is dropped. A news story with no link means the search link didn't resolve, not that the story is invented.
- **The free tier doesn't work for news** (2026-10-26). Andres created free-tier keys (a project without billing) and the code tried them before the paid ones, switching a slot to the paid key for good on a daily-quota or key error. The morning digest then went out with no news and no switch. Testing found the cause: **Google Search grounding has no free quota on any current model**; every grounded request got `429 RESOURCE_EXHAUSTED` (without the quota details that would classify as a daily quota, so it looked like a per-minute rate limit, which never switched), while plain requests worked on the free keys and grounded ones on the billed key. Two fixes: a rate limit that outlasts every retry now counts as a failure (`gemini_key_rotation.free_key_failed`), and the free keys were removed from `.env`. `gemini_key_rotation.py` stays in the code, unused. Only `gemini-robotics-er-2-preview` searched on a free key, and it left about twice as many stocks without news (3.0 of 11 against 1.5).
- **Gemini 3.5 Flash-Lite chosen over 3.6 Flash** (2026-10-26, comparison in [model_selection.md](model_selection.md#the-news-model-2026-10-26)): about half the cost of 3.6 today and a quarter once 3.6's introductory price doubles on 2027-01-01, because it doesn't think; news close to 3.6's (it found stories 3.6 missed, with one stale item and 2 of 4 macro links unresolved). 3.1 Flash-Lite thought about 5,000 tokens a run and cost 54% more than 3.5, with the most filler. Prices are per million tokens: 3.5 Flash-Lite $0.30 in / $2.50 out; 3.1 Flash-Lite $0.25 / $1.50; 3.6 Flash $0.75 / $3.75 (then $1.50 / $7.50). A billed project also gets 5,000 free searches a month per key, far more than Finnbot's few searches per digest use.

### 13.4 The ideas

- **The funnel** (long-term): the NASDAQ screener list (market cap from $8 billion, 1 million shares a day, $15; the last two were $10 billion and $30 until 2026-10-26, when the sheet's values became the defaults) → a Yahoo quality screen → a Finnhub 12-month check → ADX ≤ 45 → about 100 stocks (99 on 2026-10-25, of which 7 scored 85+) → the best **35** by the rubric's own figures (2026-10-25; about $0.0018 a candidate on Flash, so about 65 fewer calls a run) → the model scores each out of 100. Swing has its own screen (2026-10-20/21; before, swing ideas came from the long-term candidates, great businesses at fair prices, and on most days only a handful were in a swing setup and none scored 80): a pullback in an intact trend or a fresh EMA 9 / 21 cross, a trade plan worked out in code (stop under support, target at resistance, at least 1.5:1), and the best 40 by the code's own checks go to the model. The model no longer picks levels, and the reward-to-risk score (30 of 100) is computed from the plan. The checks that a fact can settle (the signs of a turn, the technical start) are worked out in code and given to the model, so two models read the same chart the same way. *Evidence:* on the same 20 candidates scored three times, the swing score moved 3.6 points on average (max 13.5) against 7.1 for the old call, and the split into a long-term call (93 calls, 3.9K tokens in) and a swing call (20 calls) cut a run from $0.24-0.26 to $0.14.
- **Pick rules** (all in `scorer.py`, set in `parameters.py`):
  - Long-term: up to 3 at 85+, each from a different **industry** (Yahoo's ~140 industries; 2026-10-25, Andres: sectors let two chipmakers in), with at least a third (⌈N/3⌉, none for N = 1) outside tech (Technology and Communication Services), and the non-tech picks may score from 80. *Why:* on 2026-10-22 all nine stocks at 85+ were tech (TSM 94, NVDA, ASML, AVGO, ANET, SKHY, LRCX, MU), and the best non-tech stocks scored 82-84 (VEEV, NVT, NBIX, VRT, STT), so a flat 85 bar gave a list of tech only. 
  - Swing: up to 5 at 75+ that are still early in their move (not already run up from the 10-day low, RSI ≤ 70, not above the upper Bollinger Band), more than half from different industries, 1 non-tech in lists of 4+ and 2 in lists of 8+.
  - **The mix rules only choose between candidates:** when nothing else can take a stock's place, the list is topped up with the best left-out stocks (`fill_relaxed`, 2026-10-25), so a quiet day still gives a full list, and the log says which stocks were added.
- **Per-user list sizes** (2026-10-24, `<NAME>_LONG_RECS` / `_SWING_RECS`, 1-10): each size is picked from the same scores, so the mix rules hold for each, and no extra scoring is needed beyond re-scoring close calls for the longest list.
- **Ideas acted on** (`idea_positions.py`): a position that is new (not added shares) by the end of the trading day after an idea is linked to it, labelled on its card, given the idea's plan in its review, and alerted at the stop or target; a monthly scorecard compares them with the S&P 500. A user's first check only records what is held, so older holdings are never linked.

### 13.5 The watchdog and the alerts

- **Evaluate only what changed.** A review of every holding every hour would cost a lot and mostly repeat itself, so each check first looks at prices with no LLM (cheapest gate first) and re-reviews only a stock that moved about a day's range (1 NATR), reports within 4 trading days or 1 trading day ago, or has a review older than 24 hours; the tripwire (2 NATR, or below the SAR stop) reviews early; at most 40 reviews per user per day (`WATCHDOG.MAX_CLAUDE_CALLS_PER_USER_DAY`). Thresholds are in NATR so they fit each stock's volatility. The earnings window is counted in **trading days** and extended to the day after a report (2026-10-26, Andres): the market reacts the day after a report released after the close. `holdings_state.py` keeps `next_earnings_date` and, once it passes, `last_earnings_date`.
- **The schedule counts from the last check or digest** (2026-10-25, `watchdog_schedule.check_due`) instead of fixed blocks, so a digest at 7:10 counts as a 7:45 check and the next one is due at 11:45: a digest counts as a check at the next :45; a due check is skipped when a digest is due within 1.5 hours (except hourly); the first check of the day is at the session's first slot; and a check that would fall after the close runs at the last slot before it. The tripwire sits out the hour after a digest (it just reviewed everything).
- **Extended hours by default** (2026-10-26): many companies report before the open or after the close; extended checks use pre-market and after-hours prices (`add_session_prices`).
- **The digest sends its own urgent alerts** (2026-10-26). Before, an urgent sell found by a digest waited for the next watchdog run past its gates: about an hour (the tripwire pause), longer if a digest held the lock past :45, and never for a user with the watchdog off. The digest now calls `hourly_watchdog.send_digest_alerts` from the main thread after every email is out: the same alert log and 24-hour cooldown as the watchdog (neither repeats the other's alert), only stocks still in that user's digest, nothing sent for `--payload-only`, a failed send left unrecorded so the watchdog retries it, and it never fails the run. It runs after the emails so an email is never delayed by Telegram, and in the main thread because the alert log is a shared file that parallel users would overwrite.
- **A user who chose "No Telegram" has the watchdog and tripwire off entirely** (interval 0, no chat ID); one who chose Telegram without the watchdog still gets digest alerts, earnings reminders, stop and target alerts, and notices.

### 13.6 Users, the sign-up form, and settings

- **The Google Sheet is the source of truth for who is a user** (2026-10-22, Andres). Every cron run applies each user's newest answer to `.env` and sets `ACTIVE_USERS` to the users with nothing required missing, so there is no second list to forget. A required API key not yet received is written as `KEY=None` (counted as unset everywhere), so the gap is visible in `.env`; likewise empty `<NAME>_SHEETS_ID=` and `<NAME>_TELEGRAM_CHAT_ID=` lines are written for a user who chose them. If the sheet can't be read or has no answers, nothing changes. Hand edits to a setting the form covers are put back. Settings the form doesn't ask about (`WATCHDOG_*`, `LONG_RECS`, `SWING_RECS`) are yours to set in `.env`, and the sync leaves them alone.
- **The form was simplified on 2026-10-26** (Andres): no sheet ID (sent privately like a key), no sharing question, no watchdog interval / session / tripwire (the defaults apply: every 4 hours, extended, tripwire on; set `<NAME>_WATCHDOG_*` for an exception), and no note. The live form was edited by hand and **no longer matches `docs/finnbot_signup_form.gs`**; don't re-run that script to edit it (it creates a new form).
- **What went wrong that day:** the reworded holdings options ("Link automatically to brokerage...", "Manually update a Google Sheet", "No holdings...") matched nothing in the importer, so every answer was "unreadable" and the 09:45 UTC sync set `ACTIVE_USERS` to nothing; the 11:10 digest would have gone to no one. Found from the three "no longer active" Telegram messages and restored by hand within the hour. Fixed: the holdings answer is matched by keyword (so a spelling fix doesn't matter), and an unreadable answer leaves an active user active and tells the admins (`import_signup.sync_signups`, "kept as they were").
- **Settings from the spreadsheet** (2026-10-23): every setting in `parameters.py` is a row of the Parameters tab with its default, Finnbot's status, and a note, checked (type, allowed values, range) before it applies; the newest change wins between `parameters.py` and the tab (the sync notices a changed default by the Default column Finnbot last wrote, and tells the admins). *To make a value permanent, copy it into `parameters.py`* (done for the stage-1 screen and the earnings window on 2026-10-26).

### 13.7 The email

- **Built for the Gmail app on a phone** (2026-10-17): one column up to 600 px; an AMP version with working dropdowns for readers who allowed `finnbot107@gmail.com`, an HTML version for everyone else; tested with dark mode and text enlarged about 1.75×.
- **Gmail clips an email at about 102 KB**, so size is managed (2026-10-19 to 10-26): short CSS classes instead of inline styles; the glossary is a web page (`finnbot-glossary` on GitHub Pages) and one line in the email; the news is scaled to the portfolio (with more than 10 holdings, one story per card except risky and urgent-sell ones); reasoning is about 200 characters for hold and buy-more and about 320 for urgent, risky and profit-sell; and `fit_for_gmail` removes content in a fixed order when the email is still too big, with the footer saying so (`create_html.TRIM_STEPS`; the signal, figures and reasoning are never removed). The email's own size is shown in "About this digest". *Order, in short:* the charts' direct Finviz links go back to short ones first, then news of hold cards, the quote buttons, the cost footer, the glossary, and so on (watchdog.md).
- **Costs are shown to everyone**, each user's own and the admin's, for this run and the last 30 days (`usage_tracking.py`); prices are in `API_PRICES` as dated lists so past months don't change when a price does.

### 13.8 Privacy and security

Each user's keys are their own (never a shared fallback), so one user's usage can never bill another's key. A user's holdings go to Anthropic (and to DeepSeek only if a DeepSeek model were set for holdings); Gemini sees only symbols and company names; SnapTrade is read-only; the idea screens use public data only. Backups are encrypted; logs on Drive are not unless `FINNBOT_LOGS_REMOTE` is set. Users send keys privately, never in the form. `data/`, `logs/`, `test_output/` and `.env` are gitignored.

---

## 14. How it was tested

**The test suite** (`python -m pytest`, about 440 tests in a minute or two): it is offline by design. `tests/conftest.py` redirects every state file, cache and ledger to a temporary folder, blocks SMTP, `yfinance.download` and network clients, records Telegram messages instead of sending them, and clears the Gemini key state; so no test spends money, sends anything or touches `data/`. It covers: the pick rules and scoring (`test_universal_pipeline.py`, `test_swing_screen.py`, `test_long_term_screen.py`), the holdings rubric's code-computed facts (`test_holding_checks.py`), the watchdog's gates, schedule and alerts (`test_watchdog_and_alerts.py`, `test_watchdog_schedule.py`), the digest run (`test_digest.py`), the email's markup and trimming (`test_create_html.py`), the sign-up importer and the settings sync (`test_import_signup.py`, `test_remote_settings.py`), costs and prices (`test_universal_and_costs.py`), DeepSeek (`test_deepseek.py`), API error handling (`test_api_errors.py`), the Gemini free-key code (`test_gemini_key_rotation.py`) and the comparison tool (`test_compare_models.py`). A test that stops making progress for 120 seconds prints every thread's stack (`pytest.ini`).

**What the suite can't tell you** (do these by hand after a change that touches them, under maintenance mode):
1. `python src/digest/digest_payload.py --payload-only --period Post-Market` (a full build with live data; about $0.20 and 25 minutes), then `--from-payload <file>` to email a `[Test]` to yourself. This proved the VM could do a real run.
2. The email on a phone, in the Gmail app, with dark mode and enlarged text, and after any template change `npx amphtml-validator --html_format AMP4EMAIL <file>.amp.html` (Gmail falls back to the plain version when the AMP one is invalid).
3. `python -m src.config.check_config` after any `.env` change; `python -m src.config.remote_settings --sync` and `python -m src.users.import_signup --list` after any change to the Parameters tab or the form.
4. A watchdog dry run: `python src/telegram_alert/hourly_watchdog.py --dry-run [--now 2026-10-23T13:45:00-04:00]`.

**How each decision above was tested** (so you can repeat it): model choices by replaying identical captured inputs through every setting and comparing with a reference (15.4); Gemini changes by running the real news functions on Andres's 10-11 holdings before and after and comparing tokens, cost, stories and links; the free-tier finding by sending the same one-line search request to every model on a free and on a billed key; costs by the usage ledger (`data/usage/api_usage.jsonl`) rather than estimates; email size by measuring the rendered KB for 10 and 20 holdings; watchdog timing by `check_due` unit tests on fixed dates (`test_watchdog_schedule.py`) and `--dry-run --now`; the earnings window by tests on real calendar dates (a Friday report and the Monday and Tuesday after).

---

## 15. Upgrading to a new LLM, and comparing models

### 15.1 Where each model is chosen

| Job | Setting | Key | Allowed models and efforts | Price |
|---|---|---|---|---|
| Scoring the ideas | `LLM.UNIVERSAL` (`model`, `effort`); `.env` `UNIVERSAL_LLM_MODEL` / `_EFFORT` override it on one machine | shared `ANTHROPIC_API_KEY` or `DEEPSEEK_API_KEY` | `ALLOWED_CLAUDE_MODELS`, `ALLOWED_DEEPSEEK_MODELS` | `API_PRICES["anthropic"]` / `["deepseek"]` |
| Holdings reviews (digest and watchdog) and the portfolio review | `LLM.HOLDINGS`, for every user | the user's own Anthropic (or DeepSeek) key | same lists | same |
| Holding news and macro news | `GEMINI_MODELS` (`HOLDING_NEWS`, `MACRO_NEWS`) and `GEMINI_CONFIG` (thinking level, output cap) | the user's `GEMINI_API_KEY`, the shared `GEMINI_API_KEY` | any Gemini model that supports Google Search grounding and a response schema | `API_PRICES["gemini"]` |

All of these except the `.env` override are rows on the Parameters tab, so a model can be changed (and changed back) without touching the code. A model with no price is still counted in `api_usage` but costed at $0 and listed under `unpriced_models`.

### 15.2 When to look at a change

- **A provider announces a retirement or a price change** (Google's 3.6 Flash price doubles on 2027-01-01; DeepSeek retired R1 and V3 earlier; `deepseek-flash` is an alias that DeepSeek updated on 2026-10-17 without renaming, so the same name can score differently after an update). A failed run with "model not found" or a 404 in `logs/*.log` means the model was retired.
- **A new model is released** that is cheaper or stronger at the same job. Look at least every quarter; the 2026-10-22 and 10-26 comparisons cost $43.87 (Anthropic $39.99, DeepSeek $3.88: Opus ran three times) and about $0.10. Use fewer runs of an expensive reference, or only one job, to spend less.
- **Costs or quality drift:** the 30-day cost in "About this digest", or the track record (`python -m src.tracking.track_record`) shows a model's picks and signals doing worse.

### 15.3 Adding a model (a checklist)

1. **Allow it:** add it to `ALLOWED_CLAUDE_MODELS` or `ALLOWED_DEEPSEEK_MODELS` with the efforts it accepts (`low … max` for Claude; `none low high max` for DeepSeek). Both evaluators send a **strict tool schema with `tool_choice: auto`**, so a Claude model must support strict tool use (Opus 4.7, Opus 4.6 and Sonnet 4.6 were removed on 2026-10-13 because they don't; newer models reject a *forced* `tool_choice`). A model that can fall back server-side after a refusal also goes in `CLAUDE_FALLBACK_DEFAULT_MODELS`.
2. **Price it:** add an `API_PRICES` entry with a `"from"` date (and a later entry for any announced change), then update `AS_OF` and check the pages in `SOURCES`. Model ids match by longest prefix, so `claude-opus-5-5` gets its own price, not `claude-opus-5`'s. Haiku-style tiers (a higher price above 100K prompt tokens) have `above_tokens`.
3. **A new Gemini model:** put it in `GEMINI_MODELS`, price it (with `search_per_1000`), and **test that it searches on a billed key** before comparing (a one-line request with `tools=[{"google_search": {}}]`; the response must have `groundingMetadata`). Some models reject `thinking_level`; set `GEMINI_CONFIG.effort` to `default` if so. Remember the free tier has no search.
4. **Check it:** `python -m src.config.check_config` (reports an unknown model or a bad effort as INVALID) and `python -m pytest`.
5. **Compare it** (15.4) before it becomes a default.

### 15.4 Comparing models for performance

`src/llm/compare_models.py` (2026-10-26) replays identical inputs through every setting, as the 2026-10-22 comparison did. Run it under maintenance mode and the shared lock, from the project root:

```bash
bash scripts/maintenance.sh on 3
D=test_output/model_compare/$(date +%F)
# 1. Save what the digest would send to each LLM. No LLM spend; ~25 minutes for the ideas. Give it every user whose holdings should be in the test.
flock -w 900 /tmp/finnbot.lock venv/bin/python -m src.llm.compare_models capture --out $D --user Andres
# 2. Score them with each setting, at least twice (three for the reference). This spends money: see the cost notes below.
venv/bin/python -m src.llm.compare_models score --inputs $D --out $D/run --jobs long_term,swing,holdings \
    --setting claude-opus-5-5:medium --setting claude-haiku-5-5:low --setting deepseek-flash:high --runs 2
# 3. Compare each with the reference
venv/bin/python -m src.llm.compare_models report --results $D/run --reference claude-opus-5-5:medium
# News: replay the holdings' news and the macro news with Gemini models (needs a captured user with news on)
venv/bin/python -m src.llm.compare_models score --inputs $D --out $D/news --jobs news --user Andres \
    --setting gemini-3.6-flash --setting gemini-3.5-flash-lite --runs 2
venv/bin/python -m src.llm.compare_models report --results $D/news --reference gemini-3.6-flash
venv/bin/python -m src.llm.compare_models show --results $D/news      # read the stories side by side
bash scripts/maintenance.sh off
```

Notes on how it works and how to read it:
- **What is replayed:** the same functions the digest calls (`run_llm_evaluation`, `run_holding_evaluation`, `fetch_symbol_news`, `fetch_live_macro_stories`) with `config={"model":…, "effort":…}`, so prompts, schemas, repairs and retries are the real ones. Usage goes to `OUT/usage.jsonl`, never the real ledger; finished runs are skipped on a re-run, so a run cut short by a limit or a spend cap can be resumed. Second looks (`HOLDINGS_RECHECK`) are off unless `--second-looks`, so each answer is one model call; the close-call rescoring isn't part of the replay.
- **Run the reference on every job you compare** (a setting's row shows `-` for a job where the reference has no runs), with the same inputs and runs. A setting that can't run a job is skipped with the reason (a model not in `ALLOWED_*`, an effort it doesn't accept, or a non-Gemini model for `news`).
- **The reference** is the strongest model you can afford (Claude Opus 5.5 at medium effort in 2026-10). It is a reference, not the truth: the question is how close a cheaper setting gets and at what cost. Its own runs compared with each other (the reference row) show the best agreement any model can be expected to reach.
- **Metrics** (every comparison is pairwise over runs, averaged):

  | Metric | Meaning |
  |---|---|
  | Gap / bias | Average points from the reference; bias keeps the sign (+ = scores higher) |
  | Rank | Rank correlation of the whole list (1 = same order) |
  | Pass at 70 / 60 | Agreement (F1) on which stocks pass the mark: combines the share of the reference's stocks found and the share of its own the reference agrees with |
  | Overlap | If the reference passes K stocks, how many of the candidate's own top K are among them (ignores a score level that is higher or lower) |
  | Top-N | How many of the reference's top N (Finnbot's list size) are in the candidate's top N, before the mix rules |
  | Same label / steps | Holdings given the same label as the reference; how many steps more cautious (buy more → hold → profit sell → risky → urgent sell) |
  | Sell calls caught / false sells | Whether the reference's sell calls (profit sell or urgent sell) are also sells, and the candidate's sells the reference doesn't make |
  | Cost / seconds | From the experiment's own ledger and clock |

- **News** has no formula: count stories and resolved links, then read them side by side (`show`) with the model names hidden if you can, judging relevance (0-2 per story), recency (within the 7-day lookback), and whether anything is invented; also compare two runs of the same model, since every model varies between runs (the 3.6 baseline matched its own top story for 1 of 9 stocks).
- **Cost of the experiment** (list prices, off-peak for DeepSeek): the ideas alone are about $0.14-0.23 a run for Flash, $0.17-0.18 for Haiku, $2.59 for Sonnet 5.5, $6.06 for Opus 5.5; holdings for both test portfolios (29 stocks) about $0.05 for Haiku against $2.04 for Opus; news about $0.01-0.02 a run (3.5 Flash-Lite $0.0087 plus the macro news $0.0026). Opus three times on everything was most of the $43.87 of 2026-10-22; use fewer runs for the reference if you only care about one job.
- **Caveats:** one day's candidates make pick-level results hinge on a few close calls (TSM against NVDA scored 93.0 against 90.5 by Opus); trust the ranking over all ~120 stocks more than the picks. Costs scale with the number of candidates and double in DeepSeek's peak hours. Repeat the comparison after any model update behind an alias.

### 15.5 What "good enough" looked like

These were judgments by Andres on the results, not fixed thresholds, except for the news, where the criteria were written down before the test. Use them as a starting point.

| Job | What the chosen setting scored | What decided it |
|---|---|---|
| Ideas (`deepseek-flash` high) | Long-term ranking correlation 0.89 against Opus's 0.98 with itself; pass / fail agreement at 70: 89% long-term, 100% swing; scores about 2 points above Opus | Close to the best cheap settings on ranking, the same swing picks as Opus in every run, at about $0.23 a run against $2.59-6.06; DeepSeek V4 Pro cost four times as much with no gain |
| Holdings (`claude-haiku-5-5` low) | The same label as Opus for 93% of 29 holdings (medium 91%), at about $0.05 for both portfolios against $2.04 | Agreement near the ceiling, the sell calls caught, and the holdings staying with Anthropic |
| News (`gemini-3.5-flash-lite`) | Criteria set beforehand: no format failures, relevance within about 10% of the baseline, at least 40% cheaper per digest at both today's and next year's prices, links resolving. Result: 50% cheaper now, 75% from 2027, no failures, 2 of 4 macro links unresolved | It passed; 3.1 Flash-Lite failed on cost (only 23% cheaper) and quality; the free model failed on coverage |

Also look at, for any new model: how often answers fail to parse or are cut off (the run log's "Successfully scored N/M"), the time per run, and any sign it is more cautious or bolder as a group (the `steps` column).

### 15.6 Rolling out a new model

1. **Re-tune the bars first.** A new model scores at a different level (Flash 4.1 about +2 over Opus, Haiku −5 to −6 on long-term), so set `MIN_LONG_SCORE`, `MIN_SWING_SCORE`, `LONG_TERM_NON_TECH_MIN_SCORE` and the close-call band `IDEA_RESCORE` from the new model's own scores on the captured candidates, not the old numbers. The rubrics are in `LLM_rec_instr.py` and `LLM_hold_instr.py`; a model may need small wording changes, so re-run the comparison after any.
2. **Try it on one machine first** with `.env` `UNIVERSAL_LLM_MODEL` / `_EFFORT` (the ideas only), or on the Parameters tab for a day when no one minds, with maintenance mode off only after a `--payload-only` run looks right.
3. **Watch the first real digest:** the log line "Successfully scored N/M tickers" (answers lost to a failure show here), `[WARNING] Fixed the answer's shape` counts, the usage ledger for cost against the comparison's estimate, and the track record for what it picked. Compare the day's picks with the old model's captured scores if you have them.
4. **Roll back** by putting the previous value back on the Parameters tab (or `git revert` and the bar values). Nothing in the state files depends on the model: stored signals are labels, and the track record keeps the model behind each pick.
5. **Update the docs:** the model tables in README.md, section 9 of this handbook, model_selection.md (add a section with the comparison, as for 2026-10-22 and 10-26), the guide's costs (new_user_guide 3.4 and 3.10), and `API_PRICES`' `AS_OF`.

### 15.7 Things that tripped earlier models (look for them in a new one)

- Refusals on strong Claude models (hence the server-side fallback list); answers cut off at `max_tokens` (an answer cut off or withheld by the content filter isn't retried, since it would fail the same way).
- DeepSeek's misshapen JSON without thinking (repaired in `deepseek_client.repair_arguments`, one retry), its "medium" effort behaving like high, and `deepseek-flash` changing under the same name.
- Gemini: no grounding metadata with a response schema (links must be verified from the redirect), thinking spending most of the output budget (hence `GEMINI_CONFIG`), answers cut off at the output cap (retried per stock), and the free tier having no search.
- Claude Haiku's higher price above 100K prompt tokens, and strict-schema models rejecting a forced `tool_choice`.

---

## 16. Diagnosing a problem a year from now

### 16.1 The first ten minutes

1. **Is it running at all?** `crontab -l` (three Finnbot lines present), `pgrep -af "digest_payload|hourly_watchdog"`, `bash scripts/maintenance.sh status` (maintenance mode left on is the most common reason for "nothing ran"; it turns itself off after at most 6 hours), `cat logs/digest.status logs/watchdog.status logs/backup.status` (the last run of each, one line, with a reason when skipped), and the healthchecks.io page for `finnbot-digest`, `finnbot-watchdog`, `finnbot-backup`.
2. **What did the last runs say?** `tail -n 60 logs/digest.log`; `grep -n "\[ERROR\]\|Traceback" logs/digest.log | tail`; the same for `logs/watchdog.log`. Every run ends with a summary line (`... finished in 1501s: sent [...], failed none. (81 files open, peak memory 798 MB)`). The older logs are in `logs/*.log.1` to `.5` and on Drive in `finnbot-logs` (up to the previous night).
3. **What does the code think is due?** `venv/bin/python src/digest/digest_payload.py --check`, and `venv/bin/python src/telegram_alert/hourly_watchdog.py --dry-run`. `--check --now <ISO time with offset>` answers "what would it have done then".
4. **Is the configuration complete?** `venv/bin/python -m src.config.check_config`, then `venv/bin/python -m src.users.import_signup --list` (the answers the sync sees), `grep "\[signup\]" logs/watchdog.log | tail`, and `grep -n "ACTIVE_USERS" -A 5 src/config/parameters.py` (who is active; it is rewritten by the sync).
5. **Is it the machine?** `df -h /` (a full disk stops everything), `free -m`, `uptime`, `date -u`, `sudo dmesg | tail` ("Out of memory"), `ls -la data/` (the state files exist and aren't empty).
6. **Is it the code?** `git log --oneline -5`, `git status` (did a change get half-applied? maintenance mode should have covered it), and `venv/bin/python -m pytest`.

### 16.2 Symptom to cause

| Symptom | Most likely cause, and where to look |
|---|---|
| No digest, no errors | Maintenance mode still on; the day isn't a trading day for NYSE or Toronto; the digest was already recorded as sent (`data/digest_state.json`); a user has `<NAME>_RECEIVE_*_DIGEST` false. `--check` says which. |
| Healthcheck red with exit code 75 | A job waited 15 minutes for `/tmp/finnbot.lock` and the holder wasn't a running digest: a stuck or manual job (`cat /tmp/finnbot.lock.holder`, `pgrep -af python`). |
| Everyone vanished from the digest | `ACTIVE_USERS` is empty or short: the sign-up sync couldn't read the answers, a form wording changed, or a key became `None`/empty. See `[signup]` lines; incident 2026-10-26. Restore with `python -m src.users.import_signup --sync` (it writes for real) after fixing the cause, or by hand-editing the list under maintenance mode (the next sync overrides it from the sheet). |
| One user's section is empty or has a banner | Their key: expired, out of credit, a spend limit, or billing off (Gemini). The banner and your Telegram say which. |
| No news anywhere | Gemini: a 429 means the key's project has no billing or no search quota; a 404 or "model not found" means the model was retired (`GEMINI_MODELS`). `grep "Gemini" logs/digest.log`. |
| No ideas, or few | `logs/digest.log`: the screening counts per stage ("Tickers kept by each stage"), each stock left out and why, and `universal_issues` in the email's notices. A shared key (DeepSeek, Finnhub) failing shows as an admin Telegram message. |
| Wrong or odd signals after a model change | Compare with the track record and the previous model's scores (15.6); rubric or bar mismatch. |
| Urgent sell in the email, no Telegram | The user has no `<NAME>_TELEGRAM_CHAT_ID`, or the alert was suppressed by the 24-hour cooldown (`data/alerts_sent/`), or Telegram failed (a failed send is retried by the next watchdog run). |
| Alert twice | The cooldown key is (user, symbol, recommendation): a signal that changed and changed back inside the cooldown can repeat; check `data/alerts_sent/`. |
| A watchdog check didn't happen | The user isn't due (`--dry-run` shows `due=` and the reason: counted from the last check or digest, skipped near a digest), the market is closed, the interval is 0, or maintenance mode was on. |
| Digest very late or killed | Memory (`dmesg`), or a data source rate-limiting (Finnhub is paced to 60 calls a minute): the 2026-10-26 digest took 1,501 s; much more means something is slow. `peak memory` in the last log line. |
| Email clipped by Gmail ("Message clipped") | Over about 102 KB: look at "Email size" in About this digest and the trim notes; more holdings or news than the trim order can absorb (about 20 stocks fit). |
| Dropdowns don't open | The reader hasn't allowed `finnbot107@gmail.com`, the AMP version failed validation, or the email is over 30 days old. |
| Backup red | rclone: `logs/backup.log`; usually the Google login expired (`rclone config reconnect gdrive:`) or the disk is full. |
| Parameters tab status ❌ | Its message says why; the previous good value stays in force. `python -m src.config.remote_settings --show`. |
| Tests fail after a change | Run the failing file alone; most failures are a changed default (`parameters.py`) that a test still expects: read the assertion's comment for why it holds that value. |

### 16.3 What has gone wrong before

| Date | What happened | Cause | Fix |
|---|---|---|---|
| 2026-10-23 | Every digest failed on the VM, `[Errno 24] Too many open files` | Cron's soft limit of 1024 open files | `ulimit -n 65536` in the crontab and `cron_job.sh` |
| 2026-10-23 | The test suite twice stopped making progress and sat idle | Not found (no stack was captured) | `faulthandler_timeout = 120` in `pytest.ini`: a test still running after 120 s prints every thread's stack, so the next time shows where |
| 2026-10-25 | The :45 watchdog went red during every long digest | Waiting 15 minutes for the lock, then exit 75 | The "earlier digest still running" skip |
| 2026-10-26 | **Everyone removed from `ACTIVE_USERS`** at 09:45 UTC | The form's holdings options were reworded and the importer matched the old wording | Keyword matching; unreadable answers keep an active user active and tell the admins |
| 2026-10-26 | The morning digest had **no news** | Free Gemini keys have no Google Search quota; the 429 was classified as a rate limit and never switched to the paid key | A rate limit that outlasts the retries switches; the free keys removed |
| 2026-10-26 | Urgent sells from a digest reached Telegram an hour late, or never | The watchdog's early exits (tripwire pause, watchdog off) | The digest sends its own alerts |
| 2026-10-26 | `import_signup --sync --dry-run` writes for real | `--sync` ignores `--dry-run` (found while writing this; a code bug, not fixed) | Use `<Name> --dry-run` per user |

### 16.4 What will break on its own with time

Things with an expiry, or that depend on someone else staying the same:
- **Credentials:** an Anthropic key set to expire (the guide suggests a date or none); the Gmail app password if the account's password or 2-step settings change; the Google service account's JSON key; the rclone Google login token (`rclone config reconnect gdrive:`); the Telegram bot token if revoked; SnapTrade connections (they expire and the user must log in again; the notice says so); a user's card or credit running out.
- **Prices and plans:** model prices change (`API_PRICES`, with `AS_OF`); Finnhub's free plan limits or covered markets; Yahoo's endpoints (`yfinance` is pinned at 1.5.2, and Yahoo changes its endpoints without notice (the "Invalid Crumb" errors are one sign): upgrade it, run the tests, and check one real run); Gmail's rules for dynamic email and its clipping size.
- **Libraries and calendars:** `exchange_calendars` needs updating when exchanges change their holidays (it is pinned at 4.13.2 and decides every digest day); Python 3.14 and the pinned packages age (a `pip install -U` of one package at a time, with the tests, under maintenance mode); `requirements.txt` holds exact versions so a rebuild reproduces today's behavior.
- **Models:** retirements and renames (15.2); a changed alias.
- **Disk:** `data/track_record/` and the usage ledger only grow (the backup keeps 90 days of previous versions on Drive; logs rotate at 5 MB); check `df -h /` yearly.
- **The form and sheets:** a form edit that changes wording (16.3, 2026-10-26); someone deleting or renaming the Parameters tab (the sync recreates its rows; the answers tab is found by its name starting with "Form responses").
- **Drift between copies:** the guide on a Google Doc, the live form and `docs/finnbot_signup_form.gs` are separate copies; the repo is the one kept in step.

### 16.5 Recreating Finnbot from nothing

If the VM is gone (the secrets are only in Andres's password manager and the state only on Drive):
1. **A new Ubuntu VM** with at least 2 GB of memory and 4 GB of swap (the digest peaks at about 800 MB; vm_setup.md has the swap commands), the clock on UTC, and Python 3.14 (`/usr/bin/python3.14` here; vm_setup.md part 4 describes installing it).
2. **The code:** add the VM's SSH key to GitHub, `git clone git@github.com:andresc44/ai_financial_advisor.git`, check out `feature/watchdog-digest-upgrades` (or `main` if it was merged since), `python3.14 -m venv venv && venv/bin/pip install -r requirements.txt`, `venv/bin/python -m pytest` (all pass, offline).
3. **The secrets:** `.env` from the password manager (every key in 12.4) and the service account JSON at the path `GOOGLE_APPLICATION_CREDENTIALS` names. `venv/bin/python -m src.config.check_config` lists what's missing. The sign-up sync rebuilds each user's settings lines from the form's answers, so a lost `.env` loses only the keys, the sheet IDs, the chat IDs, and the three never-synced settings (`WATCHDOG_*`, `LONG_RECS`, `SWING_RECS`): until a user's keys are back in `.env` they stay inactive (`KEY=None`), so users resend their keys and their chat IDs.
4. **The state:** install rclone, recreate the two remotes with **the same crypt passwords** (operations.md), then `rclone copy finnbot-backup:current/data data`. Without it, Finnbot starts clean: the first digest per user works but the track record, idea positions, usage history and the watchdog's memory are empty (the watchdog re-reviews everything once).
5. **Test before cron:** `--check`, then `--payload-only --period Post-Market` and `--from-payload` to email yourself a `[Test]` (section 14), `hourly_watchdog.py --dry-run`, and `--check` on `python -m src.users.import_signup --list`.
6. **Cron and monitoring:** the three lines from section 1, `HEALTHCHECKS_PING_KEY` in `.env` (the checks recreate themselves on first ping; set grace periods, operations.md), and the backup line.
7. **The glossary page:** clone `git@github.com:andresc44/finnbot-glossary.git` to `~/finnbot-glossary` (`GLOSSARY_SITE_DIR`) and run `venv/bin/python -m src.digest.glossary_page --publish` (the repo's Pages setting deploys `main`, root).
8. **Services outside the VM that survive it:** the Google Form and answers spreadsheet (with the Parameters tab), the Gmail sender account, the Telegram bot (users must not need to press Start again), healthchecks.io, the GitHub repos, and the Drive backups.
9. **Optional:** the Claude Code Telegram session (`~/bin/claude-tg*` aren't versioned; section 1 describes them, rebuild from that).

Other recoveries: **a leaked key**: revoke it at the provider, create a new one, put it in `.env`, and tell the user (new_user_guide 6.5); **removing a user**: delete their answer from the form's sheet (section 8), and their `.env` lines can stay; **a bad deploy**: `git revert` or `git checkout <good commit>` under maintenance mode, then `pytest` and a `--check`.

---

## 17. Appendix: the model comparison tool, in short

`python -m src.llm.compare_models capture | score | report | show` (15.4). Written 2026-10-26 because the 2026-10-22 scripts were working files on another computer and were never saved. Its metrics are plain functions (`rank_correlation`, `pass_agreement`, `same_size_overlap`, `top_n_overlap`, `score_gap`, `label_comparison`) tested in `tests/test_compare_models.py`; to add a metric, add a function there, a test, and a column in `report`.

---

## 18. Open items (2026-10-26)

Done since 2026-10-23: Finnbot runs on the VM with the cron jobs, the healthchecks.io heartbeat, and the Drive backup; the sign-up form was rebuilt and then simplified; the digest sends its own urgent alerts; the news moved to Gemini 3.5 Flash-Lite; this handbook and the comparison tool.

Open:
- **Fill in 12.1's provider line** (who hosts the VM, the console, the billing, how to restart it).
- **Healthchecks grace:** set the `finnbot-digest` and `finnbot-watchdog` checks' grace period on healthchecks.io (operations.md, step 6); a digest takes about 25 minutes.
- **News costs:** confirm the Flash-Lite news cost over a few real digests (per stock at 3 per request, and how many holdings the evening digest refreshes).
- **The branch:** `feature/watchdog-digest-upgrades` has not been merged to `main`.
- **`import_signup --sync --dry-run` writes for real** (a small code bug; the handbook warns about it).
- **Unused code:** `src/llm/gemini_key_rotation.py` and its tests, and the two `GEMINI_FREE_*` keys in `check_config`, do nothing without a free key; remove them if you won't try a free key again.
- **The comparison tool's first real use:** it was validated on 2026-10-26 on a small live run (a capture of 11 holdings and 35 + 31 idea candidates, then Haiku low and medium on the holdings, DeepSeek Flash with no thinking on the ideas, and two Flash-Lite models on the news: about $0.12), which found and fixed a crash in `report` for a single reference run. The first full comparison after a model release is its real test.
