import os
import pandas as pd
import requests
from personal.connect_snaptrade import PortfolioFetcher
from dotenv import load_dotenv
from personal.user_profiles import USER_PROFILES


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

def send_telegram_message(user_name: str, message: str) -> bool:
    """Sends a custom message to the user's Telegram chat."""
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

    # Format message with user name
    formatted_message = f"<b>Urgent Alert for {user_name}:</b>\n\n{message}"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": cid,
        "text": formatted_message,
        "parse_mode": "HTML",
    }

    response = requests.post(url, json=payload, timeout=10)
    response.raise_for_status()
    return True

    print(f"✅ Telegram message sent to {user_name}.")
if __name__ == "__main__":
    # Sample DataFrame matching your holdings layout
    fetcher = PortfolioFetcher()
    df_full = fetcher.get_holdings_df()
    # Send 'symbol' column
    send_telegram_column(df=df_full, column_name="symbol")