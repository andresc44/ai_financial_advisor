# File mapping each user to the names of their .env keys.
"""
Claude-authored (2026-10-15), at Andres's request: profiles are generated instead of hand-written. Every user's
.env keys follow <PREFIX>_<SUFFIX>, where PREFIX is the user's name in capitals (e.g. ANDRES_EMAIL); the names are
the same ones the hand-written profiles used, so existing .env files keep working.

To add a user:
  1. Add their name to ACTIVE_USERS in src/parameters.py.
  2. Print the .env lines to fill in:   venv/bin/python -m src.check_config --template <Name>
  3. Check nothing required is missing: venv/bin/python -m src.check_config
Use PROFILE_OVERRIDES only for a user whose .env key names don't follow the pattern.
"""
import re
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict

# Profile key -> .env key suffix, with what the value should be
PROFILE_ENV_SUFFIXES = {
    "account_company": "INSTITUTION",                          # Brokerage name shown in the digest, e.g. Questrade
    "destination_email": "EMAIL",                              # Where the digest is sent
    "home_timezone": "TIMEZONE",                               # IANA timezone, e.g. America/Toronto
    "telegram_chat_id": "TELEGRAM_CHAT_ID",                    # Needed for watchdog alerts and notices
    "holdings_source": "HOLDINGS_SOURCE",                      # SNAPTRADE | SHEETS | NO_HOLDINGS
    "snaptrade_client_id": "SNAPTRADE_CLIENT_ID",              # SNAPTRADE only
    "snaptrade_consumer_key": "SNAPTRADE_CONSUMER_KEY",        # SNAPTRADE only
    "google_sheets_id": "SHEETS_ID",                           # SHEETS only
    "Receive_Premarket_Digest": "RECEIVE_PREMARKET_DIGEST",    # True | False
    "Receive_Postmarket_Digest": "RECEIVE_POSTMARKET_DIGEST",  # True | False
    # Per-user watchdog + model settings (resolved by user_settings.py; unset or invalid values fall back to
    # params_dict["WATCHDOG"] / ["CLAUDE_CONFIG"]). See docs/watchdog.md.
    "watchdog_interval_hours": "WATCHDOG_INTERVAL_HOURS",      # int hours between checks; 0 = off
    "watchdog_session": "WATCHDOG_SESSION",                    # "regular" | "extended" | "24_7"
    "watchdog_tripwire": "WATCHDOG_TRIPWIRE",                  # true/false: hourly price check between checks
    "holdings_claude_model": "HOLDINGS_CLAUDE_MODEL",          # must be in params_dict["ALLOWED_CLAUDE_MODELS"]
    "holdings_claude_effort": "HOLDINGS_CLAUDE_EFFORT",        # low | medium | high | xhigh | max
    # The user's own Anthropic key (holdings evaluation) and Gemini key (digest holding news and portfolio review).
    # Neither falls back to the shared keys.
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
}

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
