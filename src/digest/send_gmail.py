#File to send emails via gmail using the Gmail SMTP server. This file is called by the daily digest payload to send the generated HTML report to users.
import os
from pathlib import Path
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo
from personal.user_profiles import USER_PROFILES

load_dotenv()

def get_latest_report_path(user_name: str="Andres",
                           date: datetime = datetime.now(ZoneInfo("America/New_York")),
                           here: Path = Path(__file__).resolve().parent,
                           ) -> Path:
    """Generates the report path dynamically using current US Eastern time."""
    today_est = date.strftime("%d_%m_%y")
    return here / "html_reports" / f"{user_name}_{today_est}_report.html"


def send_html_email(user_name: str = "Andres",
                    subject: str = "Daily Finnbot Digest",
                    date: datetime = datetime.now(ZoneInfo("America/New_York")),
                    here: Path = Path(__file__).resolve().parent
                    ):
    user_profile = USER_PROFILES.get(user_name)
    if user_profile is None:
        raise ValueError(f"User '{user_name}' not found in USER_PROFILES.")

    to_email_value = user_profile.get("destination_email")
    if to_email_value is None:
        raise ValueError(
            f"Destination email for user '{user_name}' is not configured in USER_PROFILES."
        )
    to_email = os.environ[to_email_value]

    sender_email = os.environ["GMAIL_USER"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]
    if not sender_email or not app_password:
        raise ValueError("Missing credentials. Set GMAIL_USER and GMAIL_APP_PASSWORD.")

    # 1. Read HTML file content
    resolved_path = get_latest_report_path(user_name, date, here)

    with open(resolved_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # 2. Build email payload
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = to_email

    # Plain-text fallback for non-HTML clients
    msg.set_content("Please enable HTML viewing to render this email properly.")
    
    # Attach HTML body
    msg.add_alternative(html_content, subtype="html")

    # 3. Connect to Gmail SMTP server via TLS (Port 587)
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender_email, app_password)
        server.send_message(msg)

    print(f"✅ Email successfully sent to {to_email}")

if __name__ == "__main__":
    send_html_email(
        subject="Testing email functionality with main file",
    )