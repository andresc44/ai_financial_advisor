# File for what every cron run (digest and watchdog) does first: read the settings sheet and the sign-up answers.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-25, at Andres's request.

Why: a user who signed up becomes active once every key and ID their choices need is in .env (filled in by hand
after the form). Only the hourly digest run checked that, and it read the Parameters tab before the check, so the
tab's read-only ACTIVE_USERS row showed the old list until the next hour. Now both cron jobs, at the start:
1. read the Parameters tab (remote_settings.sync_settings);
2. apply the sign-up answers and set ACTIVE_USERS to the users with nothing required missing
   (import_signup.sync_signups, which also tells the admins on Telegram who became active or inactive);
3. when ACTIVE_USERS changed, write the Parameters tab again, so it shows the new list in this same run.
Neither sync ever raises: a sheet that can't be read leaves everything as it was.
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config.parameters import params_dict
from src.config.remote_settings import sync_settings
from src.users.import_signup import sync_signups


def sync_at_start(signups: bool = True) -> None:
    """The settings sheet, then (with `signups`, cron runs only) the sign-up answers, then the settings sheet again
    when ACTIVE_USERS changed."""
    sync_settings()
    if not signups:
        return
    before = list(params_dict.get("ACTIVE_USERS", []))
    sync_signups()
    if list(params_dict.get("ACTIVE_USERS", [])) != before:
        print(f"[signup] ACTIVE_USERS is now {params_dict['ACTIVE_USERS']}; updating the Parameters tab.")
        sync_settings()
