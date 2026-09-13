import os
import zoneinfo
from datetime import datetime
from dotenv import load_dotenv

from pathlib import Path
from dotenv import load_dotenv

# Path to the directory containing THIS script file
SCRIPT_DIR = Path(__file__).resolve().parent

# Navigate up 3 parent directory levels
# Level 1 (.parents[0]): parent directory
# Level 2 (.parents[1]): grandparent directory
# Level 3 (.parents[2]): great-grandparent directory
env_path = SCRIPT_DIR.parents[2] / ".env"


def determine_recipients(recipients):
    """
    Checks the current Eastern time, determines if it's pre-market or post-market,
    and returns a list of users configured to receive a digest at this time.
    """    
    load_dotenv(dotenv_path=env_path, override=True)
    # Get current time in US Eastern Time (handles EST/EDT automatically)
    eastern_time = datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    current_hour = eastern_time.hour
    
    # Calculate absolute distance to 7 AM (hour 7) vs 4 PM (hour 16)
    dist_to_7am = abs(current_hour - 7)
    dist_to_4pm = abs(current_hour - 16)
    
    # True if closer to 7 AM, False if closer to 4 PM
    is_pre_market = dist_to_7am < dist_to_4pm
    
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
        if receive_now:
            send_digest_now.append(user_name)
            
    # Return the list of recipients (or an empty list)
    return send_digest_now