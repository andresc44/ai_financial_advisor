import os
import zoneinfo
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

from pathlib import Path
from dotenv import load_dotenv
import sys

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
from src.personalized.user_profiles import USER_PROFILES

# Path to the directory containing THIS script file
SCRIPT_DIR = Path(__file__).resolve().parent

# Navigate up 3 parent directory levels
# Level 1 (.parents[0]): parent directory
# Level 2 (.parents[1]): grandparent directory
# Level 3 (.parents[2]): great-grandparent directory
env_path = SCRIPT_DIR.parents[1] / ".env"


# Claude-authored (2026-10-13): single source of the pre/post rule (closer to 7 AM vs 4 PM US Eastern),
# used by determine_recipients below and by the email subject in digest_payload.py.
def get_digest_period(now: Optional[datetime] = None) -> str:
    """Returns "Pre-Market" or "Post-Market" for `now` (default: current time) in US Eastern time."""
    eastern_time = (now or datetime.now(zoneinfo.ZoneInfo("America/New_York"))).astimezone(
        zoneinfo.ZoneInfo("America/New_York")
    )
    current_hour = eastern_time.hour
    return "Pre-Market" if abs(current_hour - 7) < abs(current_hour - 16) else "Post-Market"


def determine_recipients(recipients, period: Optional[str] = None):
    """
    Checks the current Eastern time, determines if it's pre-market or post-market,
    and returns a list of users configured to receive a digest at this time.

    Claude-authored (2026-10-15): `period` ("Pre-Market" / "Post-Market") comes from digest_schedule.due_digest when
    the digest is calendar-scheduled; without it, the current Eastern hour decides, as before.
    """
    load_dotenv(dotenv_path=env_path, override=True)
    # Claude-authored (2026-10-13): the pre/post rule now lives only in get_digest_period, so the
    # recipient list and the email subject's Pre-Market / Post-Market label can never disagree.
    is_pre_market = (period or get_digest_period()) == "Pre-Market"

    field = "Receive_Premarket_Digest" if is_pre_market else "Receive_Postmarket_Digest"
    send_digest_now = []
    
    for user_name in recipients:
        user_profile = USER_PROFILES.get(user_name)
        if user_profile is None:
            raise ValueError(f"User '{user_name}' not found in USER_PROFILES.")

        receive_now_value = user_profile.get(field)
        if receive_now_value is None:
            raise ValueError(
                f"Receive now value for user '{user_name}' is not configured"
                " in USER_PROFILES."
            )
            
        receive_now = os.environ.get(receive_now_value)
        if receive_now == "True":
            send_digest_now.append(user_name)
            
    # Return the list of recipients (or an empty list)
    return send_digest_now