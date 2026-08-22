import os
import pandas as pd
import requests
from connect_questrade import QuestradePortfolioFetcher
from dotenv import load_dotenv

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


if __name__ == "__main__":
    # Sample DataFrame matching your holdings layout
    fetcher = QuestradePortfolioFetcher()
    df_full = fetcher.get_holdings_df()
    # Send 'symbol' column
    send_telegram_column(df=df_full, column_name="symbol")