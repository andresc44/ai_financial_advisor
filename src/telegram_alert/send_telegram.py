import os
import sys
from pathlib import Path
import pandas as pd
import requests
from dotenv import load_dotenv

# Claude-authored (2026-10-13): src.* imports with the project root on sys.path, so the watchdog can
# import this module when run from the project root under cron (the old `personalized.*` form needed src/ on PYTHONPATH).
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.personalized.pull_holdings_data import PortfolioFetcher
from src.personalized.user_profiles import USER_PROFILES


load_dotenv()


def send_telegram_column(
    df: pd.DataFrame,
    column_name: str,
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> None:
    """Sends all items from a DataFrame column as a formatted Telegram message."""
    # Retrieve credentials and enforce str type for type checkers
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not cid:
        raise ValueError(
            "Missing credentials. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"
        )

    if column_name not in df.columns:
        raise ValueError(f"Column '{column_name}' not found in DataFrame.")

    # Extract non-null column values as strings
    items = df[column_name].dropna().astype(str).tolist()

    # Format into a clean HTML list
    message_list = f"<b>{column_name.replace('_', ' ').title()}:</b>\n\n" + "\n".join(
        f"• {item}" for item in items
    )
    message_text = f" <b>📊 Portfolio Holdings</b>\n\n{message_list}"

    # Telegram API Endpoint
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": cid,
        "text": message_text,
        "parse_mode": "HTML",
    }

    # Send POST request
    response = requests.post(url, json=payload, timeout=10)
    response.raise_for_status()

    print(f"✅ Telegram message containing column '{column_name}' sent successfully.")

def send_telegram_message(user_name: str, message: str, urgent: bool = False) -> bool:
    """
    Sends a custom message to the user's Telegram chat.
    Claude-authored (2026-10-23, Andres): only an `urgent` message starts with "Urgent alert for <user>" (a swing idea's
    position reaching its stop or target). Every message used to, notices about settings, sign-ups, and scorecards
    included; each has its own heading, and the watchdog's sell alerts their own "ACTION REQUIRED" line.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")

    user_profile = USER_PROFILES.get(user_name)
    if user_profile is None:
        raise ValueError(f"User '{user_name}' not found in USER_PROFILES.")

    chat_id_value = user_profile.get("telegram_chat_id")
    if chat_id_value is None:
        raise ValueError(
            f"Telegram chat ID for user '{user_name}' is not configured in USER_PROFILES."
        )

    cid = os.environ.get(chat_id_value)

    if not token or not cid:
        raise ValueError(
            "Missing credentials. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"
        )

    formatted_message = f"<b>🚨 Urgent alert for {user_name}:</b>\n\n{message}" if urgent else message

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": cid,
        "text": formatted_message,
        "parse_mode": "HTML",
    }

    response = requests.post(url, json=payload, timeout=10)
    response.raise_for_status()
    print(f"✅ Telegram message sent to {user_name}.")
    return True

    
if __name__ == "__main__":
    # Sample DataFrame matching your holdings layout
    fetcher = PortfolioFetcher()
    df_full = fetcher.get_holdings_df()
    # Send 'symbol' column
    send_telegram_column(df=df_full, column_name="symbol")