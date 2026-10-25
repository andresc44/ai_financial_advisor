#File to send emails via gmail using the Gmail SMTP server. This file is called by the daily digest payload to send the generated HTML report to users.
"""
Claude-authored (2026-10-17): send_html_email now takes the rendered HTML and plain text from create_html.html_main
instead of re-reading html_reports/ by date. The old `date=datetime.now(...)` default was evaluated once, at
import, so a long-running process would have looked for the wrong day's file, and two digests on one day shared a
file name. It also checks the Gmail settings before connecting, sends a real plain-text part (spam filters and
text-only mail apps), and gives up on an unresponsive server after SMTP_TIMEOUT_SECONDS.
"""
import os
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.personalized.user_profiles import USER_PROFILES

load_dotenv()

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 587
SMTP_TIMEOUT_SECONDS = 60
SENDER_NAME = "Finnbot"


def destination_email(user_name: str) -> str:
    """The user's <NAME>_EMAIL from .env."""
    user_profile = USER_PROFILES.get(user_name)
    if user_profile is None:
        raise ValueError(f"User '{user_name}' not found in USER_PROFILES.")
    env_key = user_profile.get("destination_email")
    to_email = (os.environ.get(env_key) or "").strip() if env_key else ""
    if not to_email:
        raise ValueError(f"Destination email for user '{user_name}' is not configured ({env_key} in .env).")
    return to_email


def send_html_email(
    user_name: str, subject: str, html: str, text: Optional[str] = None, amp: Optional[str] = None
) -> str:
    """
    Emails `html` to the user from GMAIL_USER, with `text` as the plain-text version and `amp` (2026-10-17) as the
    AMP for Email version; returns the address. Gmail shows the AMP version, whose dropdowns work in its app, only
    when the sender is allowed in the recipient's Gmail settings and differs from the recipient; every other case
    shows the HTML version (docs/watchdog.md).
    """
    to_email = destination_email(user_name)
    sender_email = (os.environ.get("GMAIL_USER") or "").strip()
    app_password = (os.environ.get("GMAIL_APP_PASSWORD") or "").strip()
    if not sender_email or not app_password:
        raise ValueError("Missing credentials. Set GMAIL_USER and GMAIL_APP_PASSWORD.")
    if amp and sender_email.lower() == to_email.lower():
        print(f"[NOTE] {user_name}'s digest is sent from their own address, so Gmail shows the HTML version, not the "
              "AMP one with working dropdowns (Gmail requires a different sender; see docs/watchdog.md).")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{SENDER_NAME} <{sender_email}>"
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender_email.rsplit("@", 1)[-1])
    # Plain-text version for mail apps that don't show HTML
    msg.set_content(text or "This digest needs a mail app that shows HTML.")
    if amp:  # AMP goes before the HTML part, as Gmail requires; apps without AMP support skip it
        msg.add_alternative(amp, subtype="x-amp-html")
    msg.add_alternative(html, subtype="html")

    # Connect to Gmail's SMTP server with TLS (port 587)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
        server.starttls()
        server.login(sender_email, app_password)
        server.send_message(msg)

    print(f"✅ Email successfully sent to {user_name}.")
    return to_email
