from user_profiles import USER_PROFILES
from pull_holdings_data import PortfolioFetcher

def get_personal_content(user_name: str = "Andres"):
    """
    Returns a dictionary containing personalized content for the daily digest.
    This content is specific to the user and may include account information,
    watchlists, and other user-specific data.
    """
    user_profile = USER_PROFILES.get(user_name)
    if not user_profile:
        raise ValueError(f"User profile for '{user_name}' not found.")
    
    portfolio = PortfolioFetcher(user_name=user_name)
    holdings_dict = portfolio.get_holdings_digest_payload()
        

    return {
        "account_company": user_profile.get("account_company"),
        "destination_email": user_profile.get("destination_email"),
        "home_timezone": user_profile.get("home_timezone"),
        "holdings": holdings_dict.get("holdings"),
        # Add more personalized content as needed
    }