# ai_financial_advisor
AI-powered assistant to recommend new stocks to explore and alert when it's time to sell

- [Holdings watchdog and per-user model selection](docs/watchdog.md): how holdings are re-evaluated on each user's schedule, the settings, and the crontab lines.
- [Monitoring and backups](docs/operations.md): the healthchecks.io heartbeat and the nightly Google Drive backup, with setup steps.

## Adding a user

**Through the sign-up form (2026-10-22).** The sign-up sheet is the list of users. New users fill in the form built by `docs/finnbot_signup_form.gs` (explained in `docs/new_user_guide.txt`) and send their API keys privately. With `SIGNUP_FORM_SHEET_ID` in `.env`, every cron digest run starts by applying each user's newest answer to `.env` and setting `ACTIVE_USERS` to the sheet's users with nothing required missing; an API key a user's choices need that isn't set yet is written as `KEY=None`, which counts as missing everywhere. Replace it with the key they send you, and the next run activates them. The admins hear about each sign-up, activation, and deactivation on Telegram. `venv/bin/python -m src.import_signup --sync` does the same now; `<Name> --dry-run` previews one answer, `<Name> --enter-keys` saves keys typed with hidden input, and `--list` shows every answer.

**By hand (only without the sheet; with it, the sync replaces `ACTIVE_USERS`):**

1. Add their name to `ACTIVE_USERS` in `src/parameters.py` (the digest and the watchdog both use this list).
2. Print the `.env` lines to fill in: `venv/bin/python -m src.check_config --template <Name>`. Each user's keys are named `<NAME>_<SETTING>`; `.env.example` shows every shared and per-user key.
3. Check nothing required is missing: `venv/bin/python -m src.check_config` (exit code 1 if something is missing or invalid; secret values are never printed).


## Tests

`venv/bin/pip install -r requirements-dev.txt`, then `venv/bin/python -m pytest` from the project root. Every API (Anthropic, Gemini, SnapTrade, Yahoo, Telegram) is mocked and every file the code writes goes to a temporary folder, so the tests cost nothing, need no keys, and never touch real users or data.
