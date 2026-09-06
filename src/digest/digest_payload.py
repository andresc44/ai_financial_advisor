#main file for creating the daily digest payload and sending it to users. This file is called by a cron job to run daily.
from datetime import datetime
from zoneinfo import ZoneInfo
from create_html import html_main
from send_gmail import send_html_email
from universal.universal_consolidated import get_universal_content
from personal.personal_consolidated import get_personal_content
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
        user_payload.update(get_personal_content(user_name))
        return user_payload
    
    def send_daily_digest_to(self, user_name: str = "Andres"):
        #add if statement for if someone like Ste who wants the universal but not the personal, check if account linked
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
    #this is the file that gets put on a cron timer
    digest = DigestPayload()
    if digest.universal_content():
        print("Universal content added to payload.")
        digest.send_daily_digest_to("Andres")
        digest.send_daily_digest_to("Mauricio")
