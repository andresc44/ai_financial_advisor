#File to run hourly to check for alerts and send Telegram messages to users based on their profiles.
from personalized.pull_holdings_data import PortfolioFetcher
from send_telegram import send_telegram_message
from personalized.user_profiles import USER_PROFILES

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
    if holdings_dict["holdings"]:
        for holding in holdings_dict["holdings"]:
            if holding.get("recommendation") == "urgent_sell":
                results["alert"] = True
                results["body"] += f"URGENT SELL alert for {holding['symbol']}.\n"
    if results["alert"]:
        results["body"] = f"🚨🚨 ACTION REQUIRED: Finnbot urgent alerts for {user_name}:\n" + results["body"]
        send_telegram_message(user_name, results["body"])
    return results["alert"]

if __name__ == "__main__":
    users = ["Andres", "Mauricio", "Steven"]
    for user in users:
        alert_bool = check_for_alerts(user)