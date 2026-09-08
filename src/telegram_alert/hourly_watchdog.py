#File to run hourly to check for alerts and send Telegram messages to users based on their profiles.
#TODO: Setup cron job timer

import os
from personalized.pull_holdings_data import PortfolioFetcher
from send_telegram import send_telegram_message
from personalized.user_profiles import USER_PROFILES
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import pandas as pd

CSV_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "alerts_sent"
CSV_PATH = CSV_DIR / "telegram_alerts_timestamped.csv"
RETENTION_DAYS = 30  # Number of days to keep alert logs
COOLDOWN_HOURS = 24  # Number of hours to suppress repeated alerts


def load_and_prune_alert_logs(
    csv_path: Path, retention_days: int = RETENTION_DAYS
) -> pd.DataFrame:
    """Reads the CSV log, prunes entries older than retention_days, and returns the cleaned DataFrame."""
    cols = ["user", "alerts", "timestamp"]
    if not csv_path.exists():
        return pd.DataFrame(columns=cols)

    try:
        df = pd.read_csv(csv_path)
        if df.empty or not set(cols).issubset(df.columns):
            return pd.DataFrame(columns=cols)

        df["timestamp_dt"] = pd.to_datetime(
            df["timestamp"], utc=True, errors="coerce"
        )
        df = df.dropna(subset=["timestamp_dt"])

        # Preserve only rows within the retention window
        cutoff_date = datetime.now(timezone.utc) - timedelta(
            days=retention_days
        )
        pruned_df = df[df["timestamp_dt"] >= cutoff_date].copy()

        pruned_df["timestamp"] = pruned_df["timestamp_dt"].dt.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        return pruned_df[cols]
    except Exception as e:
        print(f"[WARNING] Failed to parse existing alert CSV: {e}")
        return pd.DataFrame(columns=cols)


def is_alert_suppressed(
    df_logs: pd.DataFrame,
    user: str,
    serialized_alerts: str,
    cooldown_hours: int = COOLDOWN_HOURS,
) -> bool:
    """Returns True if an identical alert set was sent to the user within cooldown_hours."""
    if df_logs.empty:
        return False

    matches = df_logs[
        (df_logs["user"] == user) & (df_logs["alerts"] == serialized_alerts)
    ]
    if matches.empty:
        return False

    timestamps = pd.to_datetime(
        matches["timestamp"], utc=True, errors="coerce"
    )
    last_sent = timestamps.max()

    if pd.isna(last_sent):
        return False

    return (datetime.now(timezone.utc) - last_sent) < timedelta(
        hours=cooldown_hours
    )

def check_for_alerts(user_name: str = "Andres"):
    """
    Check for alerts based on the user's holdings and profile.
    """
    user_profile = USER_PROFILES.get(user_name)
    if not user_profile:
        raise ValueError(f"User profile for '{user_name}' not found.")
    
    portfolio = PortfolioFetcher(user_name=user_name)
    holdings_dict = portfolio.get_holdings_digest_payload()
    results = {"alert": False, "body": ""}
    
    alert_holidings = []
    if holdings_dict["holdings"]:
        for holding in holdings_dict["holdings"]:
            if holding.get("recommendation") == "urgent_sell":
                alert_holidings.append(holding)
                results["alert"] = True
                results["body"] += f"URGENT SELL alert for {holding['symbol']}.\n"
    if results["alert"]:
        results["body"] = f"🚨🚨 ACTION REQUIRED: Finnbot urgent alerts for {user_name}:\n" + results["body"]
        send_telegram_message(user_name, results["body"])
    return alert_holidings

def process_user_alerts(
    retention_days: int = RETENTION_DAYS, cooldown_hours: int = COOLDOWN_HOURS
) -> None:
    """Main execution function accepting custom retention and cooldown windows."""
    CSV_DIR.mkdir(parents=True, exist_ok=True)

    # Load log history and prune entries older than retention_days
    alert_logs = load_and_prune_alert_logs(
        CSV_PATH, retention_days=retention_days
    )

    users = USER_PROFILES.keys()
    for user in users:
        holdings_source = USER_PROFILES[user]["holdings_source"]
        src = os.environ.get(holdings_source)

        telegram_chat_id_value = USER_PROFILES[user].get("telegram_chat_id")
        if not isinstance(telegram_chat_id_value, str):
            cid_active = False
        else:
            cid_active = os.environ.get(telegram_chat_id_value) not in [
                None,
                "",
            ]

        if src in ["SNAPTRADE", "SHEETS"] and cid_active:
            print(f"Checking for alerts for user: {user}")
            alert_holdings = check_for_alerts(user)

            if alert_holdings != []:
                alerts_serialized = json.dumps(alert_holdings, sort_keys=True)

                if is_alert_suppressed(
                    alert_logs,
                    user,
                    alerts_serialized,
                    cooldown_hours=cooldown_hours,
                ):
                    print(
                        f"[SUPPRESSED] Identical alert sent to {user} within {cooldown_hours}h. Skipping."
                    )
                else:
                    # send_telegram_alert(user, alert_holdings)
                    print(f"[SENT] Alert sent to {user}.")

                    now_utc = datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                    new_log = pd.DataFrame(
                        [
                            {
                                "user": user,
                                "alerts": alerts_serialized,
                                "timestamp": now_utc,
                            }
                        ]
                    )
                    alert_logs = pd.concat(
                        [alert_logs, new_log], ignore_index=True
                    )

    # Save cleaned history back to CSV
    alert_logs.to_csv(CSV_PATH, index=False)
    print(f"Alert log updated at: {CSV_PATH}")


if __name__ == "__main__":
    process_user_alerts(retention_days=RETENTION_DAYS, cooldown_hours=COOLDOWN_HOURS)