import os
from pathlib import Path
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo

load_dotenv()

here = Path(__file__).resolve().parent


def get_latest_report_path() -> Path:
    """Generates the report path dynamically using current US Eastern time."""
    today_est = datetime.now(ZoneInfo("America/New_York")).strftime("%d_%m_%y")
    return here / "html_reports" / f"{today_est}_report.html"


def send_html_email(
    subject: str,
    to_email: str = "andres.cervera.rozo@gmail.com",
    html_file_path: str | Path | None = None,
    sender_email: str = os.environ["GMAIL_USER"],
    app_password: str = os.environ["GMAIL_APP_PASSWORD"],
):

    if not sender_email or not app_password:
        raise ValueError("Missing credentials. Set GMAIL_USER and GMAIL_APP_PASSWORD.")

    # 1. Read HTML file content
    resolved_path = Path(html_file_path) if html_file_path else get_latest_report_path()

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
        subject="First attempt to send email with HTML report",
    )