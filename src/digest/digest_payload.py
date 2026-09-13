#main file for creating the daily digest payload and sending it to users. This file is called by a cron job to run daily.
from datetime import datetime
from zoneinfo import ZoneInfo
from create_html import html_main
from send_gmail import send_html_email
from universal.universal_consolidated import get_universal_content
from personal.personal_consolidated import get_personal_content
from pre_vs_post import check_timezone
import copy
from pathlib import Path

class DigestPayload:
    def __init__(self):
        self.src_dir = Path(__file__).resolve().parent
        self.payload = {
            "date": datetime.now(ZoneInfo("America/New_York")).date(),
            "subject": f"Finnbot Digest {self.payload['date']}",
            "title": "Daily Finnbot Digest",
        }
    def universal_content(self):
        self.payload.update(get_universal_content())
        return True
    
    def personalized_content(self, user_name: str = "Andres"):
        # copy the current class and add personalized content based on user_name
        user_payload = copy.deepcopy(self.payload)
        user_profile = USER_PROFILES.get(user_name)
        if user_profile is None:
            raise ValueError(f"User '{user_name}' not found in USER_PROFILES.")

        holdings_source_value = user_profile.get(holdings_source)
        if holdings_source_value is None:
            raise ValueError(
                f"Holdings source value for user '{user_name}' is not configured"
                " in USER_PROFILES."
            )
            
        holdings_source = os.environ.get(holdings_source_value)
        if holdings_source not in ["SNAPTRADE", "SHEETS"]:
            return user_payload  # Return the payload without personal content if holdings_source is not valid
        else:
            user_payload.update(get_personal_content(user_name))
            return user_payload
    
    def send_daily_digest_to(self, user_name: str = "Andres"):
        user_payload = self.personalized_content(user_name)
        html_main(user_payload, self.src_dir)
        # populate_and_send_email(user_payload)  # Placeholder for actual email sending logic
        send_html_email(user_name, user_payload["subject"],
                        user_payload["date"],
                        self.src_dir,
                        )
        print(f"Sending daily digest to {user_name} with personalized payload")
        return True


if __name__ == "__main__":
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] Starting digest check...")

    recipients = ["Andres", "Mauricio", "Steven"]
    digest_recipients = determine_recipients(recipients)

    if not digest_recipients:
        print(f"[{now}] No recipients scheduled for this hour. Exiting.")
        exit(0)

    print(f"[{now}] Active recipients found: {digest_recipients}. Fetching universal content...")
    digest = DigestPayload()

    if digest.universal_content():
        print(f"[{now}] Universal content loaded. Sending digests...")
        for user in digest_recipients:
            try:
                digest.send_daily_digest_to(user)
                print(f"[{now}] Successfully sent digest to {user}.")
            except Exception as e:
                print(f"[{now}] ERROR sending digest to {user}: {e}")
    else:
        print(f"[{now}] Failed to generate universal content. Digest aborted.")
