from personal import get_personal_content
from send_telegram import send_telegram_message

def check_for_alerts(user_name: str = "Andres"):
    """
    Checks for alerts based on the user's profile and returns a list of alerts.
    This function can be expanded to include more complex alert logic.
    """
    results = {"alert": False,
                   "body": "",
                   }
    
    user_profile = get_personal_content(user_name)
    
    # Example alert logic based on user profile
    if user_profile.get("account_company") == "Questrade":
        results["alert"] = True
        results["body"] = f"Alert for {user_name}: Check your Questrade account."

    # Add more alert conditions as needed
    return results

if __name__ == "__main__":
    users = ["Andres", "Mauricio"]
    for user in users:
        alerts = check_for_alerts(user)
        if alerts["alert"]:
            print(alerts["body"])
            send_telegram_message(user, alerts["body"])