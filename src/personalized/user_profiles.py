# File mapping each user to the names of their .env keys.
"""
Claude-authored (2026-10-15), at Andres's request: profiles are generated instead of hand-written. Every user's
.env keys follow <PREFIX>_<SUFFIX>, where PREFIX is the user's name in capitals (e.g. ANDRES_EMAIL); the names are
the same ones the hand-written profiles used, so existing .env files keep working.

To add a user:
  1. Add their name to ACTIVE_USERS in src/config/parameters.py.
  2. Print the .env lines to fill in:   venv/bin/python -m src.config.check_config --template <Name>
  3. Check nothing required is missing: venv/bin/python -m src.config.check_config
Use PROFILE_OVERRIDES only for a user whose .env key names don't follow the pattern.
"""
import os
import re
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config.parameters import params_dict

# Profile key -> .env key suffix, with what the value should be
PROFILE_ENV_SUFFIXES = {
    "destination_email": "EMAIL",                              # Where the digest is sent
    "account_company": "INSTITUTION",                          # Brokerage name shown in the digest, e.g. Questrade
    "home_timezone": "TIMEZONE",                               # IANA timezone, e.g. America/Toronto
    "home_currency": "HOME_CURRENCY",                          # Claude-authored (2026-10-21): one of HOLDINGS.HOME_CURRENCIES
    "telegram_chat_id": "TELEGRAM_CHAT_ID",                    # Needed for watchdog alerts and notices
    "snaptrade_client_id": "SNAPTRADE_CLIENT_ID",              # SNAPTRADE only
    "snaptrade_consumer_key": "SNAPTRADE_CONSUMER_KEY",        # SNAPTRADE only
    "holdings_source": "HOLDINGS_SOURCE",                      # SNAPTRADE | SHEETS | NO_HOLDINGS
    "google_sheets_id": "SHEETS_ID",                           # SHEETS only
    "Receive_Premarket_Digest": "RECEIVE_PREMARKET_DIGEST",    # True | False
    "Receive_Postmarket_Digest": "RECEIVE_POSTMARKET_DIGEST",  # True | False
    # Per-user watchdog + model settings (resolved by user_settings.py; unset or invalid values fall back to
    # params_dict["WATCHDOG"] / ["LLM"]["HOLDINGS"]). See docs/watchdog.md.
    "watchdog_interval_hours": "WATCHDOG_INTERVAL_HOURS",      # int hours between checks; 0 = off
    "watchdog_session": "WATCHDOG_SESSION",                    # "regular" | "extended" | "24_7"
    "watchdog_tripwire": "WATCHDOG_TRIPWIRE",                  # true/false: hourly price check between checks
    # Claude-authored (2026-10-16): the LLM for this user's holdings, Claude or DeepSeek (src/llm/llm_models.py)
    "holdings_llm_model": "HOLDINGS_LLM_MODEL",                # a model in ALLOWED_CLAUDE_MODELS or ALLOWED_DEEPSEEK_MODELS
    "holdings_llm_effort": "HOLDINGS_LLM_EFFORT",              # Claude: low..max; DeepSeek: none (no thinking) | low | high | max
    "holdings_claude_model": "HOLDINGS_CLAUDE_MODEL",          # Older name for HOLDINGS_LLM_MODEL, still read when that's unset
    "holdings_claude_effort": "HOLDINGS_CLAUDE_EFFORT",        # Older name for HOLDINGS_LLM_EFFORT, still read when that's unset
    "receive_holding_news": "RECEIVE_HOLDING_NEWS",            # True | False: Gemini news bullets for each holding (default True)
    # Claude-authored (2026-10-24, Andres): how many ideas of each kind this user's digest shows, instead of
    # LONG_TERM_N_AMOUNT / SWING_TERM_N_AMOUNT (set in .env only; the sign-up form doesn't ask)
    "long_recs": "LONG_RECS",                                  # whole number, 1 to user_settings.MAX_IDEAS_PER_LIST
    "swing_recs": "SWING_RECS",                                # whole number, 1 to user_settings.MAX_IDEAS_PER_LIST
    # The user's own Anthropic or DeepSeek key (holdings evaluation, whichever their model needs) and Gemini key
    # (digest holding news and portfolio review). None falls back to the shared keys.
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
}

# Claude-authored (2026-10-22): the sign-up sync (src/users/import_signup.py) writes a required API key it found missing as
# KEY=None, so the gap shows in .env; env_setting, which every reader of users' settings goes through, treats it
# as unset
MISSING_VALUE = "None"


def env_setting(env_key: str | None) -> str | None:
    """The stripped value of environment variable `env_key`, or None when it's unset, empty, or MISSING_VALUE."""
    value = (os.environ.get(env_key) or "").strip() if env_key else ""
    return value if value and value != MISSING_VALUE else None


# {user: {profile key: .env key}} for users whose key names don't follow <PREFIX>_<SUFFIX>
PROFILE_OVERRIDES: dict[str, dict[str, str]] = {}


def env_prefix(user_name: str) -> str:
    """The .env prefix for a user: their name in capitals, with anything but letters and digits as underscores."""
    return re.sub(r"[^A-Za-z0-9]+", "_", user_name).strip("_").upper()


def build_profile(user_name: str) -> dict[str, str]:
    """{profile key: .env key name} for one user."""
    prefix = env_prefix(user_name)
    profile = {key: f"{prefix}_{suffix}" for key, suffix in PROFILE_ENV_SUFFIXES.items()}
    profile.update(PROFILE_OVERRIDES.get(user_name, {}))
    return profile


USER_PROFILES = {user_name: build_profile(user_name) for user_name in params_dict.get("ACTIVE_USERS", [])}
