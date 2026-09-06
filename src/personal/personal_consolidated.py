from user_profiles import USER_PROFILES
from personal.connect_snaptrade import PortfolioFetcher

def get_personal_content(user_name: str = "Andres"):
    """
    Returns a dictionary containing personalized content for the daily digest.
    This content is specific to the user and may include account information,
    watchlists, and other user-specific data.
    """
    user_profile = USER_PROFILES.get(user_name)
    if not user_profile:
        raise ValueError(f"User profile for '{user_name}' not found.")
    if user_profile.get("account_company") == "Questrade":
        portfolio = PortfolioFetcher(user_name=user_name)
        holdings = portfolio.get_holdings_df()
    else:
        holdings = None
        print("Client's account company is not Questrade. No holdings data available.")
        

    return {
        "account_company": user_profile.get("account_company"),
        "destination_email": user_profile.get("destination_email"),
        "home_timezone": user_profile.get("home_timezone"),
        "holdings": holdings
        # Add more personalized content as needed
    }