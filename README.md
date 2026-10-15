# ai_financial_advisor
AI-powered assistant to recommend new stocks to explore and alert when it's time to sell

- [Holdings watchdog and per-user model selection](docs/watchdog.md): how holdings are re-evaluated on each user's schedule, the settings, and the crontab lines.

## Adding a user

1. Add their name to `ACTIVE_USERS` in `src/parameters.py` (the digest and the watchdog both use this list).
2. Print the `.env` lines to fill in: `venv/bin/python -m src.check_config --template <Name>`. Each user's keys are named `<NAME>_<SETTING>`; `.env.example` shows every shared and per-user key.
3. Check nothing required is missing: `venv/bin/python -m src.check_config` (exit code 1 if something is missing or invalid; secret values are never printed).

