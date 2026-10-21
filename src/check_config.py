# File to check that every .env key the project needs is set and valid, and to print the .env block for a new user.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-15, at Andres's request, to make adding
users routine.

Usage (from the project root):
  venv/bin/python -m src.check_config                   # check the shared keys and every user in ACTIVE_USERS
  venv/bin/python -m src.check_config --template Maria  # print the .env lines to fill in for a new user

Secret values are never printed; only settings like the holdings source or True/False flags are shown when they're
invalid. Exits with code 1 when anything required is missing or invalid, so it can gate a deploy.
"""
import argparse
import os
import sys
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict
from src.personalized.user_profiles import PROFILE_ENV_SUFFIXES, build_profile
from src import llm_models
from src.personalized.user_settings import get_active_users, get_llm_settings

# Shared .env keys: (required, what it's for)
SHARED_KEYS = {
    "FINNHUB_API_KEY": (True, "Finnhub, shared market data"),
    "TWELVEDATA_API_KEY": (False, "Twelve Data: only indicators not computed from Yahoo prices (TECHNICAL_INDICATOR_SOURCE)"),
    # Claude-authored (2026-10-16): only the one LLM["UNIVERSAL"] needs is required (see run_check)
    "ANTHROPIC_API_KEY": (True, "Claude for the universal recommendations (shared), when LLM['UNIVERSAL'] is Claude"),
    "DEEPSEEK_API_KEY": (False, "DeepSeek for the universal recommendations (shared), when LLM['UNIVERSAL'] is DeepSeek"),
    # Claude-authored (2026-10-17): override LLM["UNIVERSAL"] in parameters.py (src/llm_models.ENV_OVERRIDES)
    "UNIVERSAL_LLM_MODEL": (False, "LLM for the universal recommendations: a model in ALLOWED_CLAUDE_MODELS or "
                                    "ALLOWED_DEEPSEEK_MODELS (default LLM['UNIVERSAL'])"),
    "UNIVERSAL_LLM_EFFORT": (False, "Its effort. Claude: low, medium, high, xhigh, max; DeepSeek: none (no thinking), "
                                     "low, high, max"),
    "GMAIL_USER": (True, "Gmail account the digest is sent from"),
    "GMAIL_APP_PASSWORD": (True, "App password for that Gmail account"),
    "TELEGRAM_BOT_TOKEN": (True, "Telegram bot for alerts and notices"),
    "GOOGLE_APPLICATION_CREDENTIALS": (False, "Service-account JSON path; needed to read private Google Sheets"),
    "GEMINI_API_KEY": (False, "Gemini for the macro news (shared; only when headline news is enabled)"),
    "FRED_API_KEY": (False, "FRED yield curve (only when market indicators are enabled)"),
    # Claude-authored (2026-10-16): monitoring and backups, read by scripts/cron_job.sh and scripts/backup_to_drive.sh
    "HEALTHCHECKS_PING_KEY": (False, "healthchecks.io ping key; without it cron jobs run unmonitored"),
    "FINNBOT_BACKUP_REMOTE": (False, "rclone destination for the nightly backup (default finnbot-backup:)"),
    "FINNBOT_BACKUP_KEEP_DAYS": (False, "Days to keep replaced files in the backup (default 90)"),
}

HOLDINGS_SOURCES = ("SNAPTRADE", "SHEETS", "NO_HOLDINGS")
WITH_HOLDINGS = ("SNAPTRADE", "SHEETS")

# Per-user profile keys: (when it's required, what to put there). "always", "holdings" (SNAPTRADE or SHEETS),
# "holdings_anthropic" / "holdings_deepseek" (holdings, and the user's model is from that provider), "SNAPTRADE",
# "SHEETS", or "optional".
USER_KEYS = {
    "holdings_source": ("always", "SNAPTRADE, SHEETS, or NO_HOLDINGS"),
    "destination_email": ("always", "Where the digest is sent"),
    "Receive_Premarket_Digest": ("always", "True or False: send the 07:00 ET digest"),
    "Receive_Postmarket_Digest": ("always", "True or False: send the after-close digest"),
    "account_company": ("holdings", "Brokerage name shown in the digest, e.g. Questrade"),
    "home_timezone": ("holdings", "IANA timezone, e.g. America/Toronto"),
    # Claude-authored (2026-10-21)
    "home_currency": ("optional", f"{', '.join(params_dict['HOLDINGS']['HOME_CURRENCIES'])}: the overview shows the "
                                  f"portfolio in it and in USD (default {params_dict['HOLDINGS']['DEFAULT_HOME_CURRENCY']})"),
    "anthropic_api_key": ("holdings_anthropic", "The user's own Anthropic key (holdings evaluation on a Claude model)"),
    "deepseek_api_key": ("holdings_deepseek", "The user's own DeepSeek key (holdings evaluation on a DeepSeek model)"),
    "gemini_api_key": ("holdings", "The user's own Gemini key (holding news and portfolio review)"),
    "snaptrade_client_id": ("SNAPTRADE", "SnapTrade personal client ID"),
    "snaptrade_consumer_key": ("SNAPTRADE", "SnapTrade personal consumer key"),
    "google_sheets_id": ("SHEETS", "ID of the holdings Google Sheet (from its URL)"),
    "telegram_chat_id": ("optional", "Telegram chat ID; without it there are no watchdog alerts or notices"),
    "watchdog_interval_hours": ("optional", f"Hours between watchdog checks, 0 = off (default {params_dict['WATCHDOG']['DEFAULT_INTERVAL_HOURS']})"),
    "watchdog_session": ("optional", "regular, extended, or 24_7 (default regular)"),
    "watchdog_tripwire": ("optional", "true or false: hourly price check between checks"),
    "holdings_llm_model": ("optional", "LLM for holdings: a model in ALLOWED_CLAUDE_MODELS or ALLOWED_DEEPSEEK_MODELS (default LLM['HOLDINGS'])"),
    "holdings_llm_effort": ("optional", "Claude: low..max; DeepSeek: none (no thinking), low, high, or max"),
    "receive_holding_news": ("optional", "True or False: Gemini news bullets for each holding (default True)"),
    "holdings_claude_model": ("optional", "Older name for HOLDINGS_LLM_MODEL (used when that's unset)"),
    "holdings_claude_effort": ("optional", "Older name for HOLDINGS_LLM_EFFORT (used when that's unset)"),
}


def _valid_timezone(value: str) -> bool:
    try:
        ZoneInfo(value)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def _valid_int(value: str, low: int, high: int) -> bool:
    try:
        return low <= int(value) <= high
    except ValueError:
        return False


# Value checks for settings that aren't secrets: (is valid, what's expected). Shown with the value when invalid.
VALUE_CHECKS: dict[str, tuple[Callable[[str], bool], str]] = {
    "holdings_source": (lambda v: v in HOLDINGS_SOURCES, "one of " + ", ".join(HOLDINGS_SOURCES)),
    "Receive_Premarket_Digest": (lambda v: v in ("True", "False"), "exactly True or False"),
    "Receive_Postmarket_Digest": (lambda v: v in ("True", "False"), "exactly True or False"),
    "home_timezone": (_valid_timezone, "an IANA timezone like America/Toronto"),
    "home_currency": (lambda v: v.upper() in params_dict["HOLDINGS"]["HOME_CURRENCIES"],
                      "one of " + ", ".join(params_dict["HOLDINGS"]["HOME_CURRENCIES"])),
    "watchdog_interval_hours": (
        lambda v: _valid_int(v, 0, params_dict["WATCHDOG"]["MAX_INTERVAL_HOURS"]),
        f"a whole number from 0 to {params_dict['WATCHDOG']['MAX_INTERVAL_HOURS']}",
    ),
    "watchdog_session": (lambda v: v.lower() in params_dict["WATCHDOG"]["SESSIONS"], "regular, extended, or 24_7"),
    "watchdog_tripwire": (lambda v: v.lower() in ("true", "false", "1", "0", "yes", "no", "on", "off"), "true or false"),
    "holdings_claude_model": (lambda v: v in llm_models.allowed_models(), "a model in ALLOWED_CLAUDE_MODELS or ALLOWED_DEEPSEEK_MODELS"),
    "holdings_llm_model": (lambda v: v in llm_models.allowed_models(), "a model in ALLOWED_CLAUDE_MODELS or ALLOWED_DEEPSEEK_MODELS"),
    "holdings_llm_effort": (lambda v: v.lower() in {e for efforts in llm_models.allowed_models().values() for e in efforts},
                            "none, low, medium, high, xhigh, or max (and one the model accepts)"),
    "receive_holding_news": (lambda v: v.lower() in ("true", "false", "1", "0", "yes", "no", "on", "off"), "true or false"),
    "holdings_claude_effort": (lambda v: v.lower() in ("low", "medium", "high", "xhigh", "max"), "low, medium, high, xhigh, or max"),
}


def _env(key: str) -> Optional[str]:
    value = os.environ.get(key)
    return value.strip() if value and value.strip() else None


def _required(rule: str, source: Optional[str], provider: Optional[str] = None) -> bool:
    if rule == "always":
        return True
    if rule == "holdings":
        return source in WITH_HOLDINGS
    if rule in ("holdings_anthropic", "holdings_deepseek"):  # Claude-authored (2026-10-16): the key the user's model needs
        return source in WITH_HOLDINGS and provider == (llm_models.ANTHROPIC if rule == "holdings_anthropic" else llm_models.DEEPSEEK)
    return rule == source


def check_user(user_name: str) -> list[tuple[str, str, str]]:
    """(status, .env key, note) for each of a user's keys. Status: OK, MISSING, INVALID, or unset (optional)."""
    profile = build_profile(user_name)
    source = _env(profile["holdings_source"])
    provider = get_llm_settings(user_name)["provider"]
    rows = []
    for key, (rule, description) in USER_KEYS.items():
        env_key = profile[key]
        value = _env(env_key)
        if value is None:
            status = "MISSING" if _required(rule, source, provider) else "unset"
            rows.append((status, env_key, description))
            continue
        check = VALUE_CHECKS.get(key)
        if check and not check[0](value):
            rows.append(("INVALID", env_key, f"must be {check[1]} (is '{value}')"))
        else:
            rows.append(("OK", env_key, description))
    return rows


def run_check() -> bool:
    """Prints the status of every shared and per-user key. Returns True when nothing required is missing or invalid."""
    load_dotenv(project_root / ".env", override=True)
    ok = True

    # Claude-authored (2026-10-16): which LLM each job uses, and so which shared key the universal content needs
    universal, holdings = llm_models.job_settings("UNIVERSAL"), llm_models.job_settings("HOLDINGS")
    print("LLM")
    print(f"  UNIVERSAL  {universal['model']} (effort {universal['effort']}, {universal['provider']})")
    print(f"  HOLDINGS   {holdings['model']} (effort {holdings['effort']}, {holdings['provider']}; default for users)\n")
    needs = {"ANTHROPIC_API_KEY": universal["provider"] == llm_models.ANTHROPIC,
             "DEEPSEEK_API_KEY": universal["provider"] == llm_models.DEEPSEEK}

    # Claude-authored (2026-10-17): a set but unusable model or effort override is INVALID (job_settings ignores it)
    valid = {"UNIVERSAL_LLM_MODEL": lambda value: value.strip() in llm_models.allowed_models(),
             "UNIVERSAL_LLM_EFFORT": lambda value: value.strip().lower() in llm_models.allowed_models().get(universal["model"], [])}
    print("Shared keys")
    for env_key, (required, description) in SHARED_KEYS.items():
        required = needs.get(env_key, required)
        value = _env(env_key)
        status = ("OK" if valid.get(env_key, bool)(value) else "INVALID") if value else ("MISSING" if required else "unset")
        ok &= status not in ("MISSING", "INVALID")
        print(f"  {status:8} {env_key:34} {description}")

    users = get_active_users()
    print(f"\nACTIVE_USERS: {users or 'none'}")
    for user_name in users:
        source = _env(build_profile(user_name)["holdings_source"]) or "no holdings source"
        settings = get_llm_settings(user_name)
        llm = f"; holdings LLM {settings['model']}, effort {settings['effort']}" if source in WITH_HOLDINGS else ""
        print(f"\n{user_name} ({source}{llm})")  # 2026-10-21: no holdings LLM for an ideas-only user
        for status, env_key, note in check_user(user_name):
            ok &= status not in ("MISSING", "INVALID")
            print(f"  {status:8} {env_key:34} {note}")

    print("\nAll required settings are present." if ok else "\nFix the MISSING / INVALID lines above.")
    return ok


def env_template(user_name: str) -> str:
    """The .env lines to fill in for a new user, with what each value should be."""
    profile = build_profile(user_name)
    lines = [f"# --- {user_name} (python -m src.check_config --template {user_name}) ---"]
    for key in PROFILE_ENV_SUFFIXES:
        rule, description = USER_KEYS[key]
        needed = {"always": "required", "holdings": "required with holdings", "optional": "optional",
                  "holdings_anthropic": "required with holdings on a Claude model",
                  "holdings_deepseek": "required with holdings on a DeepSeek model"}.get(rule, f"required for {rule}")
        lines.append(f"{profile[key]}=  # {needed}: {description}")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check Finnbot's .env settings")
    parser.add_argument("--template", metavar="NAME", help="Print the .env block for a new user instead of checking.")
    args = parser.parse_args()
    if args.template:
        print(env_template(args.template))
    else:
        sys.exit(0 if run_check() else 1)
