# Finnbot (ai_financial_advisor)

AI-powered assistant that recommends new stocks to explore and alerts you when it's time to sell.

Finnbot emails each user a stock-market digest every trading day, before the open and after the close, and watches their holdings in between, sending a Telegram alert when something needs action. One admin (Andres) runs it for a small group of invited users. Each user pays only for the AI that reviews their own holdings, through their own API keys: about $0.90 to $1.40 a month for 10 holdings.

> Finnbot's signals and ideas are AI-generated opinions from public market data and news. They are not financial advice. Always make your own decisions.

---

## What it does

**The digest** (an email designed for the Gmail app on a phone, with dropdowns so each part can be skipped):

| Section | What's in it |
|---|---|
| Overview | The portfolio's value in the user's home currency, the day's change, unrealized gain or loss, and which positions need attention |
| Risk | A review of the portfolio as a whole (concentration, market sensitivity, earnings clustered together, how much is flagged risky) |
| Markets & macro | Graded market signals (trend, mood from VIX and Fear & Greed, breadth, rates, the dollar, oil) and the day's top macro news with sources |
| Your holdings | One card per stock: price, average cost, gain or loss, its **signal** with the reason, technicals, upcoming earnings, and the news that matters, with links |
| Swing ideas | US stocks set up for a move over the next few weeks, each with an entry, a stop, a target, and the ratio of gain to risk |
| Long-term ideas | US businesses worth owning for years at today's price, with the thesis, the key risk, valuation, growth, and momentum |
| Glossary, costs | The terms that day's email uses; what this digest and the last 30 days cost |

**The five signals** each holding gets, in the order they're checked:

| Signal | Meaning |
|---|---|
| Urgent sell | A specific, time-sensitive problem that can't wait (the only signal that sends a Telegram alert) |
| Profit sell | A real recent run that is showing signs of tiring |
| Risky | Something serious, or two lesser warnings: keep a close eye on it |
| Buy more | Pulled back within a healthy trend, with signs the selling is ending: a time to add |
| Hold | Everything else: healthy, or recovering normally |

**On Telegram:**
- Urgent sell alerts: right after each digest (for every user with Telegram), and from the watchdog between digests.
- Alerts when a stock bought from a swing idea reaches the idea's stop or target, and a monthly scorecard of how those ideas did against the S&P 500.
- Earnings reminders 24 hours before a held stock reports.
- Notices when a brokerage connection or holdings sheet stops working.

---

## Who it's for, and use cases

**For** individual investors who hold a handful to a few dozen stocks or ETFs (US, Canadian, UK, European, Australian, Colombian listings), want a short twice-daily briefing instead of watching the market, and want ideas screened the same disciplined way every day. Holdings come from a brokerage link (SnapTrade) or a simple Google Sheet, or users can take the ideas only.

**Use cases:**
- **A morning plan and an evening wrap-up:** what moved, why, and what needs attention, before the open and after the close.
- **Watching positions between digests:** a check every 4 hours through extended trading hours, re-reviews when a stock moves a lot or nears earnings, an hourly tripwire for big moves, and an alert only when it's urgent.
- **Finding ideas:** several hundred large US stocks screened every day for long-term quality and short-term swing setups, scored by an AI model against a fixed rubric, with an explicit trade plan for each swing idea.
- **Following through on ideas:** a stock bought from an idea is recognized, its plan is tracked, and its outcome is scored.
- **Running it for friends cheaply:** a sign-up form onboards new users, and each one pays a few cents a day on their own keys.

---

## How it works

```mermaid
flowchart TD
    cron["cron, every hour at :10"] --> sync["Read the settings tab and the sign-up sheet"]
    sync --> due{"Pre-Market or Post-Market digest due?"}
    due -- no --> extras["Earnings reminders, holiday notice, monthly scorecards"]
    due -- yes --> universal & personal
    subgraph universal["Universal content (admin's keys)"]
        u1["Several hundred large US stocks"] --> u2["Long-term screens: Yahoo, Finnhub, ADX"]
        u1 --> u3["Swing screen: setup, trade plan in code"]
        u2 --> u4["AI scoring: DeepSeek V4.1 Flash"]
        u3 --> u4
        u4 --> u5["Picks: pass marks, industry and tech rules"]
        u6["Markets dashboard"] ~~~ u7["Macro news: Gemini Flash-Lite + Google Search"]
    end
    subgraph personal["Each user's content (their own keys)"]
        p1["Holdings: SnapTrade or Google Sheet"] --> p2["Market data + facts computed in code"]
        p2 --> p3["AI review per holding: Claude Haiku 5.5"]
        p1 --> p4["News per holding: Gemini Flash-Lite, reused later the same day"]
        p3 --> p5["Portfolio review: Claude Haiku 5.5"]
        p4 --> p5
    end
    universal --> email["Email: HTML + AMP, trimmed for Gmail"]
    personal --> email
    email --> tg
    watchdog["cron, every hour at :45: watchdog"] --> w1["Re-review holdings that moved or near earnings"] --> tg["Telegram: urgent sells"]
```

**The ideas** (shared by everyone):
- **The starting list:** every US-listed stock over a market-cap, volume, and price floor ($8 billion, 1 million shares a day, $15; several hundred stocks).
- **Long-term:**
  - screened for quality (analyst coverage, profitability, growth, valuation, debt) and scored out of 100 on valuation, financial health, growth, and competitive moat;
  - **picks:** up to 3 scoring 85 or more, each from a different industry, with at least a third (1 of 3) outside tech, which may score from 80; a list the mix rules would leave short is filled with the best of the rest. A user can ask for 1 to 10 ideas.
- **Swing:**
  - screened for a pullback in a healthy trend or a fresh uptrend, with a trade plan worked out in code, then scored on setup, reward against risk, momentum, and catalysts;
  - **picks:** up to 5 scoring 75 or more that are still early in their move, mostly from different industries, with at least 1 outside tech in a list of 4 or more.
- **Close calls:** a score near the bar is scored again, and the middle score is kept.

**The holdings** (per user):
- **Facts first:** code works out each holding's facts (trend, pullback or run, signs of a turn, stretched, days to earnings, fund or not).
- **Then the review:** Claude Haiku 5.5 applies the rubric above in order.
- **Second looks:** an urgent sell or profit sell is reviewed twice more, and the majority wins, so a one-off reading can't send an alert.

Plain-language details for users: [docs/new_user_guide.txt](docs/new_user_guide.txt), sections 8 and 9. Technical details: [docs/watchdog.md](docs/watchdog.md).

---

## Automations

| Job | When | What it does |
|---|---|---|
| Digest run | Every hour at :10 (cron) | Syncs settings and users, then sends the Pre-Market digest (from 07:00 ET until the open) or the Post-Market digest (16:10 ET, retried for 4 hours) when due, followed by each user's urgent sell alerts on Telegram. Also sends earnings reminders, the US-holiday notice, and the monthly idea scorecards. |
| Watchdog | Every hour at :45 | For users who chose Telegram with the watchdog: a check every 4 hours (default) from 4:00 AM to 8:00 PM ET on trading days, counted from the last check or digest. It re-reviews holdings that moved about a day's range, report within 4 trading days or the day after, or have reviews over 24 h old; an hourly tripwire catches bigger moves. At most 40 reviews per user per day. |
| Settings sync | Every digest run | Applies valid changes from the **Parameters** tab of the sign-up spreadsheet; a change in `parameters.py` wins over the tab. |
| Sign-up sync | Every cron digest run | Imports new or edited sign-up form answers into `.env` and the list of active users. |
| Backup | 02:30 on the VM's clock (UTC), daily | Encrypted copy of the state to Google Drive (90 days of versions), plus the run logs. |
| Heartbeat | Every job | Pings healthchecks.io, which alerts on Telegram when a job fails, hangs, or doesn't run. |
| Failure alerts | When something breaks | The admin gets a Telegram message (once per problem per 6 hours); users see what went wrong at the top of their email. |
| Maintenance mode | By hand | `bash scripts/maintenance.sh on` pauses the digest and watchdog while code changes on the VM (see [CLAUDE.md](CLAUDE.md)). |

---

## Models and costs

| Job | Model | Paid by |
|---|---|---|
| Idea scoring | DeepSeek V4.1 Flash (high effort) | Admin |
| Macro news | Gemini 3.5 Flash-Lite with Google Search | Admin |
| Holdings reviews and the portfolio review | Claude Haiku 5.5 (low effort) | Each user (Anthropic key) |
| News for each holding | Gemini 3.5 Flash-Lite with Google Search (3 holdings per request) | Each user (Gemini key with billing on; only with news on) |

Each model was chosen in a side-by-side comparison ([docs/model_selection.md](docs/model_selection.md)): the ideas and holdings models against Claude Opus 5.5, and the news model against four Gemini options. Google's free tier has no web search on any current model, so a Gemini key needs billing on.

**Per user, per month** (21 trading days; estimates at October 2026 prices; the watchdog adds about $0.20-0.50 for 10 holdings):

| Digests a day | Holdings | News off | News on |
|---|---|---|---|
| 1 | 10 | $0.45 | $0.66 |
| 2 | 10 | $0.90 | $1.17 |
| 2 | 20 | $1.74 | $2.27 |

The full table, split by Anthropic and Google, is in the user guide (3.10). An ideas-only user pays nothing.

**Admin:**
- **The shared part:** about $0.17 per digest (almost all of it the idea scoring; the macro news is about $0.003), so about $7 a month.
- **Hosting:** one OVHcloud VPS-1 (2 vCPUs, about 4 GB of memory) at about A$85 a year; a full digest peaks at about 800 MB and takes about 25 minutes (see [docs/vm_setup.md](docs/vm_setup.md)).
- **Measured, not guessed:** every API call's cost is recorded in `data/usage/api_usage.jsonl` and shown at the bottom of each digest.

---

## Usage

**Requirements:**
- Python 3.14 and the packages in `requirements.txt` (`requirements-dev.txt` adds pytest);
- a Google Cloud service account (for the Google Sheets);
- a Gmail account to send from;
- a Telegram bot;
- API keys: Anthropic and/or DeepSeek, Gemini (billing on), Finnhub, FRED, and Twelve Data (optional).

**Set up:**
```bash
python -m venv venv && venv/bin/pip install -r requirements-dev.txt   # or uv; Windows: venv\Scripts\...
cp .env.example .env                                                   # then fill it in (never commit it)
venv/bin/python -m src.config.check_config                                    # reports anything missing or invalid
```

**Run** (all commands and options: [docs/admin_handbook.md](docs/admin_handbook.md), section 1):

| Command | Does |
|---|---|
| `venv/bin/python src/digest/digest_payload.py` | The scheduled digest run (what cron calls) |
| `... --payload-only --period Post-Market` | Builds a digest now with live data and saves it, without emailing anyone |
| `... --from-payload test_output/digest_payloads/<file>.json` | Emails a saved digest as a `[Test]` |
| `... --check` | Says whether a digest is due and for whom |
| `venv/bin/python src/telegram_alert/hourly_watchdog.py --dry-run` | Shows what the watchdog would check |
| `venv/bin/python -m src.config.remote_settings --sync` | Applies the Parameters tab now |
| `venv/bin/python -m src.users.import_signup --sync` | Imports the sign-up form's answers now |
| `venv/bin/python -m src.llm.compare_models capture\|score\|report` | Compares LLM settings on identical inputs (handbook section 15) |
| `venv/bin/python -m pytest` | The tests |

**Deploy:** [docs/vm_setup.md](docs/vm_setup.md) has the step by step for a VM (written for Google Cloud; Finnbot runs on an OVHcloud VPS-1), plus monitoring and backups.

### Adding a user

Through the sign-up form (built by [docs/finnbot_signup_form.gs](docs/finnbot_signup_form.gs), explained in [docs/new_user_guide.txt](docs/new_user_guide.txt)):
1. The new user fills in the form and sends their API keys, and their sheet ID if they chose a Google Sheet, privately, never in the form.
2. Every cron run (digest at :10 and watchdog at :45) applies every user's newest answer to `.env`, and puts the users with nothing required missing in `ACTIVE_USERS`. The form doesn't ask for the watchdog's interval, session, or tripwire: users who chose it get the defaults in `parameters.py` (every 4 hours, extended hours, tripwire on) unless `<NAME>_WATCHDOG_*` is set in `.env`.
3. A key a user's choices need but hasn't arrived yet is written as `KEY=None`, and a missing sheet ID or Telegram chat ID as an empty line. Replace them with what the user sends (`python -m src.users.import_signup <Name> --enter-keys` takes keys with hidden input), and the next run activates them.
4. The admin hears about each sign-up, activation, and deactivation on Telegram. An answer the importer can't read (for example after the form's wording changes) leaves an active user active and tells the admin.

Without the sheet: add the name to `ACTIVE_USERS` in `src/config/parameters.py`, print the `.env` lines to fill in with `python -m src.config.check_config --template <Name>`, then run `python -m src.config.check_config`.

---

## Configuration

Three layers, the last one winning:
1. **`src/config/parameters.py`:** every setting and its default, commented with why it has its value.
2. **The Parameters tab** of the sign-up spreadsheet: one row per setting, with Finnbot's status and a note on what it does. Changes there are checked and apply from the next digest run, without editing code. A later change in `parameters.py` wins over the tab.
3. **`.env`:** secrets, per-user settings (written by the sign-up sync), and an optional per-machine override of the ideas' model.

---

## Repository layout

```
src/
  config/                   Settings
    parameters.py               All settings and their defaults
    settings_overrides.py       Applies the Parameters tab's accepted values at import
    remote_settings.py          Syncs the Parameters tab (read, check, write the status back)
    parameter_notes.py          The "What it does" note for each setting
    check_config.py             Checks .env
  users/                    Who uses Finnbot
    import_signup.py            The sign-up form → .env and ACTIVE_USERS
    admins.py                   Who the admins are, and how notices are worded for everyone else
  llm/                      The AI models
    llm_models.py               Which model does which job, and its provider
    deepseek_client.py          DeepSeek requests and answer repair
    claude_cache.py             Prompt caching for the holdings reviews
    gemini_settings.py          Gemini's thinking level and output cap for every call
    gemini_key_rotation.py      Optional free-tier Gemini key tried before the paid one (unused: no web search on the free tier)
  market/                   Market time and money
    market_session.py           Exchange calendars, sessions, and trading-day counts
    watchdog_schedule.py        When each user's watchdog check is due
    fx_rates.py                 Exchange rates to USD
  tracking/                 What Finnbot recommended and spent
    track_record.py             How past signals and ideas played out
    idea_positions.py           Holdings bought from ideas: labels, alerts, scorecards
    usage_tracking.py           Tokens and cost of every API call
  ops/                      Running it
    cron_start.py               What every cron run does first: the settings tab, then the sign-up answers
    run_logging.py              Run logs and crash alerts
    shared_locks.py             The lock around the shared market-data keys
  universal/                The shared content
    universal_consolidated.py   Screens, scoring, picks, dashboard, macro news
    recommendations/            Stage filters, swing screen, LLM rubric and scoring, picks
    macro_indicators.py, market_news.py
  personalized/             Each user's content
    pull_holdings_data.py       Holdings from SnapTrade or a Google Sheet, prices, FX
    holding_checks.py           Facts computed in code for the review
    holdings_state.py           Each stock's latest review and each user's watchdog clock
    recommendation.py, LLM_hold_instr.py   The holdings review and its rubric
    symbol_specific_news.py, news_reuse.py, portfolio_review.py
  digest/                   Scheduling, the email (create_html.py, email_template.html), the glossary page, sending
  telegram_alert/           Watchdog, notices, earnings reminders, holiday notice
scripts/                    The cron wrapper (healthchecks.io pings), the Drive backup, maintenance mode
docs/                       Guides (below), and the sign-up form's Apps Script
tests/                      About 440 offline tests
```

At the top level: `requirements.txt` / `requirements-dev.txt`, `pytest.ini`, `.env.example` (every key `.env` takes), and `CONVENTIONS.md` (coding rules).

Runtime state lives in `data/` and logs in `logs/`. Both are gitignored, since they contain users' holdings.

---

## Documentation

| Doc | For | What's in it |
|---|---|---|
| [docs/setup_instructions.md](docs/setup_instructions.md) | New users | The accounts and keys to get ready, in order, in two pages |
| [docs/new_user_guide.txt](docs/new_user_guide.txt) | New users | Every choice, what it costs, how to get each key, Gmail setup, how ideas and holdings work, keeping keys safe |
| [docs/admin_handbook.md](docs/admin_handbook.md) | The admin | Every command and option, the pipeline, Telegram messages, settings, onboarding, files; how it's set up and running, why it's built this way and the evidence, how it was tested, how to upgrade to a new LLM and compare models, how to diagnose problems and recreate it from nothing |
| [docs/vm_setup.md](docs/vm_setup.md) | The admin | Moving Finnbot to a VM, step by step (written for Google Cloud; the live one is an OVHcloud VPS-1) |
| [docs/operations.md](docs/operations.md) | The admin | healthchecks.io monitoring and the Google Drive backup |
| [docs/watchdog.md](docs/watchdog.md) | Developers | The full technical reference: every setting, file, and design decision |
| [docs/model_selection.md](docs/model_selection.md) | Developers | How the models and efforts were compared and chosen (ideas, holdings, and the news model), with the results |

---

## Data, privacy, and security

- **API keys and passwords** live only in `.env` (gitignored) and in each user's password manager. Users send them privately, never through the form.
- **What goes where:**
  - a user's holdings go to Anthropic for the reviews;
  - only symbols and company names go to Gemini for the news;
  - SnapTrade, when used, reads the brokerage positions read-only;
  - the idea screens use public market data only.
- **Backups** of the state are encrypted before they reach Google Drive.
- **Tests** call no real API and write only to temporary folders (`python -m pytest`).

---

## Status

Running on a VM since 2026-10-23 ([docs/vm_setup.md](docs/vm_setup.md)): the hourly digest and watchdog under cron, the nightly Drive backup, and the healthchecks.io heartbeat. Three users, with Andres as admin. Work on the VM is done under maintenance mode (see [CLAUDE.md](CLAUDE.md)). The VM runs `main` (the dev branch was merged and deleted on 2026-10-26).
