"""
Per-user setting resolution for the holdings watchdog and the holdings-evaluation Claude model.

Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-13, at Andres's request,
as part of the per-user watchdog scheduling and model selection plan (see docs/watchdog.md).
- 2026-10-14: added get_anthropic_api_key so each user's holdings evaluations use their own Anthropic key
  instead of the shared ANTHROPIC_API_KEY used for the universal recommendations, and get_gemini_api_key
  for the digest's per-holding news (symbol_specific_news.py).
- 2026-10-15: a missing key raises api_errors.ApiKeyUnusable (still a ValueError), so callers handle it the same
  way as an invalid, expired, or out-of-credit key. Added get_active_users (params_dict["ACTIVE_USERS"]), the
  one list of users shared by the digest and the watchdog.
- 2026-10-16: get_llm_settings replaces get_claude_overrides: a user's holdings model can be Claude or DeepSeek
  (src/llm_models.py), with their own key for that provider (get_llm_api_key). wants_holding_news reads the
  per-user switch for the digest's holding news.

Why this module exists:
- USER_PROFILES stores the *names* of .env keys, not values. This is the one place that reads those keys,
  casts and validates them, and falls back to params_dict defaults with a printed warning.
- A typo in one user's .env (e.g. WATCHDOG_INTERVAL_HOURS=four, or an unknown model name) must never
  crash the watchdog run for every user, and must never send an invalid request to the Claude API.
"""
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, TypeVar

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src import llm_models
from src.parameters import params_dict
from src.personalized.api_errors import ApiKeyUnusable
from src.personalized.user_profiles import USER_PROFILES, env_setting

load_dotenv(dotenv_path=project_root / ".env")

T = TypeVar("T")

WATCHDOG_PARAMS = params_dict["WATCHDOG"]
TRUE_VALUES = {"true", "1", "yes", "on"}
FALSE_VALUES = {"false", "0", "no", "off"}


@dataclass(frozen=True)
class WatchdogSettings:
    """Resolved watchdog settings for one user."""
    interval_hours: int  # 0 = watchdog off for this user
    session: str         # key of params_dict["WATCHDOG"]["SESSIONS"]
    tripwire: bool       # hourly price check between scheduled checks


def _read_profile_env(user_name: str, profile_key: str) -> Optional[str]:
    """
    Follows USER_PROFILES[user][profile_key] to its .env key and returns the stripped value, or None if unset.
    2026-10-22: also None for the sign-up sync's KEY=None placeholder (user_profiles.env_setting).
    """
    return env_setting(USER_PROFILES.get(user_name, {}).get(profile_key))


def _resolve(
    user_name: str,
    profile_key: str,
    default: T,
    parse: Callable[[str], T],
    is_valid: Callable[[T], bool],
) -> T:
    """Reads, parses, and validates one setting; prints a warning and returns `default` on any problem."""
    raw = _read_profile_env(user_name, profile_key)
    if raw is None:
        return default
    try:
        value = parse(raw)
    except ValueError:
        print(f"[WARNING] {user_name}: could not parse {profile_key}='{raw}'. Using default {default!r}.")
        return default
    if not is_valid(value):
        print(f"[WARNING] {user_name}: invalid {profile_key}='{raw}'. Using default {default!r}.")
        return default
    return value


def _parse_bool(raw: str) -> bool:
    lowered = raw.lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    raise ValueError(f"Not a boolean: {raw}")


def get_watchdog_settings(user_name: str) -> WatchdogSettings:
    """Resolves a user's watchdog interval, session, and tripwire setting with params_dict fallbacks."""
    sessions = WATCHDOG_PARAMS["SESSIONS"]
    default_session = WATCHDOG_PARAMS["SESSION"] if WATCHDOG_PARAMS["SESSION"] in sessions else "regular"

    interval_hours = _resolve(
        user_name,
        "watchdog_interval_hours",
        WATCHDOG_PARAMS["DEFAULT_INTERVAL_HOURS"],
        parse=int,
        is_valid=lambda v: 0 <= v <= WATCHDOG_PARAMS["MAX_INTERVAL_HOURS"],
    )
    session = _resolve(
        user_name,
        "watchdog_session",
        default_session,
        parse=str.lower,
        is_valid=lambda v: v in sessions,
    )
    tripwire = _resolve(
        user_name,
        "watchdog_tripwire",
        WATCHDOG_PARAMS["TRIPWIRE_DEFAULT"],
        parse=_parse_bool,
        is_valid=lambda v: True,
    )
    return WatchdogSettings(interval_hours=interval_hours, session=session, tripwire=tripwire)


def get_llm_settings(user_name: str) -> dict[str, str]:
    """
    {"provider", "model", "effort"} for this user's holdings evaluation (watchdog and digest).

    Claude-authored (2026-10-16), replacing get_claude_overrides. The model is <NAME>_HOLDINGS_LLM_MODEL (or the
    older <NAME>_HOLDINGS_CLAUDE_MODEL) when it's in ALLOWED_CLAUDE_MODELS or ALLOWED_DEEPSEEK_MODELS, otherwise
    params_dict["LLM"]["HOLDINGS"]. The effort is <NAME>_HOLDINGS_LLM_EFFORT (or HOLDINGS_CLAUDE_EFFORT) when the
    model accepts it; otherwise LLM["HOLDINGS"]'s effort if that model is the default one, else the provider's
    default (CLAUDE_CONFIG / DEEPSEEK_CONFIG "effort"). The provider follows from the model.

    2026-10-22 (Andres): every user's holdings use params_dict["LLM"]["HOLDINGS"] (Claude Haiku 5.5, low), chosen
    from the model comparison (docs/model_selection.md). The sign-up form no longer asks, and <NAME>_HOLDINGS_LLM_MODEL
    / _EFFORT (and the older HOLDINGS_CLAUDE_*) are ignored; the sign-up sync clears them.
    """
    return llm_models.job_settings("HOLDINGS")


def get_home_currency(user_name: str) -> str:
    """
    Claude-authored (2026-10-21): <NAME>_HOME_CURRENCY when it's in HOLDINGS.HOME_CURRENCIES, otherwise
    HOLDINGS.DEFAULT_HOME_CURRENCY (USD). The digest's overview shows the portfolio's value in it, and in USD.
    """
    holdings = params_dict["HOLDINGS"]
    return _resolve(user_name, "home_currency", holdings.get("DEFAULT_HOME_CURRENCY", "USD"), parse=str.upper,
                    is_valid=lambda v: v in holdings.get("HOME_CURRENCIES", ["USD"]))


def wants_holding_news(user_name: str) -> bool:
    """Claude-authored (2026-10-16): <NAME>_RECEIVE_HOLDING_NEWS (default True): Gemini news bullets per holding."""
    return _resolve(user_name, "receive_holding_news", True, parse=_parse_bool, is_valid=lambda v: True)


def get_active_users() -> list[str]:
    """
    Claude-authored (2026-10-15): the users the digest and the watchdog run for - params_dict["ACTIVE_USERS"] in
    order, without duplicates. Names with no profile in USER_PROFILES are skipped with a warning, so one typo
    can't stop the run for everyone else.
    """
    active_users = []
    for user_name in params_dict.get("ACTIVE_USERS", []):
        if user_name not in USER_PROFILES:
            print(f"[WARNING] ACTIVE_USERS lists '{user_name}', who has no profile in USER_PROFILES. Skipping.")
        elif user_name not in active_users:
            active_users.append(user_name)
    return active_users


def _require_api_key(user_name: str, profile_key: str, provider: str) -> str:
    """
    Returns the user's own API key for `provider` (USER_PROFILES[user][profile_key] -> .env), raising
    ApiKeyUnusable (a ValueError) with reason "missing" when it isn't set. Deliberately no fallback to a shared
    key, so one user's usage is never billed to another key.
    """
    api_key = _read_profile_env(user_name, profile_key)
    if api_key is None:
        raise ApiKeyUnusable(provider, "missing", user_name)
    return api_key


def get_anthropic_api_key(user_name: str) -> str:
    """The user's own Anthropic key for holdings evaluation (no fallback to the shared ANTHROPIC_API_KEY)."""
    return _require_api_key(user_name, "anthropic_api_key", "Anthropic")


def get_llm_api_key(user_name: str, provider: str) -> str:
    """Claude-authored (2026-10-16): the user's own key for `provider` ("Anthropic" or "DeepSeek"), no shared fallback."""
    if provider == llm_models.DEEPSEEK:
        return _require_api_key(user_name, "deepseek_api_key", "DeepSeek")
    return get_anthropic_api_key(user_name)


def get_gemini_api_key(user_name: str) -> str:
    """The user's own Gemini key for the digest's per-holding news (no fallback to the shared GEMINI_API_KEY)."""
    return _require_api_key(user_name, "gemini_api_key", "Gemini")
