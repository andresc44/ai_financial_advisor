# File to pick the free-tier or paid Gemini key for the universal (shared) and per-user Gemini calls.
"""
Authorship: Written by Claude (Claude Sonnet 5 via Claude Code), 2026-10-26, at Andres's request.

Why: Andres set up a free-tier Gemini project (no billing enabled), in a second .env key per slot
(GEMINI_FREE_API_KEY for the shared/universal macro news, ANDRES_GEMINI_FREE_API_KEY for his own holding news),
to see whether the free tier's quota covers Finnbot's Gemini calls before paying for the existing paid project.

How it works:
- A slot (None/omitted = the shared universal key, or a user's name) tries its free key first.
- The first time that key fails for anything but a transient per-minute rate limit (any reason
  ApiKeyUnusable.blocks_run is True for: a key problem, a billing requirement, or the daily quota running out),
  the slot switches to its paid key (the one Finnbot used before the free key existed) for the rest of this call
  and every one after it, and Andres is told once on Telegram.
- The switch is permanent, remembered in data/gemini_key_state.json, until he resets it by hand (`--reset`,
  below): nothing clears it automatically, even once a daily quota would otherwise have reset, since he asked to
  decide each time rather than have it flip back and fail (and notify him) again on its own.
- A slot with no free key configured (every other user) behaves exactly as before: straight to the paid key.

Callers: src/universal/market_news.py (get_macro_stories, the shared slot) and
src/personalized/personalized_consolidated.py (fetch_holdings_news, each user's own slot) hold the actual
retry-with-the-other-key logic, since the two calls are shaped differently (one whole request vs. several
stocks batched in parallel, where only some may need retrying); this file only has the shared pieces: which key
to try, recording and checking the switch, and the Telegram notice.

CLI: `python -m src.llm.gemini_key_rotation --status` lists every slot's state; `--reset NAME` (or `universal`,
or `all`) clears a slot's switch so its free key is tried again.
"""
import argparse
import html
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.personalized.api_errors import REASON_TEXT, ApiKeyUnusable, SHARED_ENV_KEYS
from src.personalized.user_profiles import USER_PROFILES, env_setting

STATE_PATH = project_root / "data" / "gemini_key_state.json"
_LOCK = threading.Lock()
SHARED_SLOT = "_shared"  # The key in the state file for the universal (None) slot
SHARED_FREE_ENV_KEY = "GEMINI_FREE_API_KEY"
NOTICE_COOLDOWN_HOURS = 24 * 400  # Keyed by slot, so each slot's switch is told once (src/tracking/idea_positions.py)


def _slot_key(user_name: Optional[str]) -> str:
    return user_name or SHARED_SLOT


def _load() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save(state: dict) -> None:
    """Atomically writes the state file (temp file in the same directory, then os.replace), like the other small
    JSON state files (src/personalized/holdings_state.py)."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=STATE_PATH.parent, prefix=".gemini_key_state_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, STATE_PATH)
    except OSError:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def switched_to_paid(user_name: Optional[str] = None) -> bool:
    """Whether this slot (None/omitted = the shared universal key) already switched to its paid key for good."""
    return bool(_load().get(_slot_key(user_name), {}).get("switched"))


def reset(user_name: Optional[str] = None, every_slot: bool = False) -> None:
    """Clears one slot's switch (user_name, None = the shared slot), or every slot's (every_slot=True)."""
    with _LOCK:
        state = {} if every_slot else _load()
        if not every_slot:
            state.pop(_slot_key(user_name), None)
        _save(state)


def free_key(user_name: Optional[str] = None) -> Optional[str]:
    """
    This slot's free key, or None when there isn't one set in .env, or it's already switched to paid (so callers
    skip straight to paid_key() without trying the free one again).
    """
    if switched_to_paid(user_name):
        return None
    if user_name is None:
        return env_setting(SHARED_FREE_ENV_KEY)
    return env_setting((USER_PROFILES.get(user_name) or {}).get("gemini_free_api_key"))


def paid_key(user_name: Optional[str]) -> Optional[str]:
    """This slot's regular (paid-project) key, the one Finnbot used before the free key existed, or None if unset."""
    if user_name is None:
        for env_key in SHARED_ENV_KEYS["Gemini"]:
            value = env_setting(env_key)
            if value:
                return value
        return None
    return env_setting((USER_PROFILES.get(user_name) or {}).get("gemini_api_key"))


def switch_to_paid(user_name: Optional[str], error: ApiKeyUnusable) -> bool:
    """
    Records this slot's permanent switch to its paid key. Returns True the first time (so the caller knows to send
    the one-off Telegram notice); False if this slot had already switched (idempotent under concurrent callers).
    """
    with _LOCK:
        state = _load()
        key = _slot_key(user_name)
        if state.get(key, {}).get("switched"):
            return False
        state[key] = {"switched": True, "reason": error.reason, "at": datetime.now(timezone.utc).isoformat()}
        _save(state)
        return True


def notify_switch(user_name: Optional[str], error: ApiKeyUnusable) -> None:
    """Tells Andres, once per slot, that its free Gemini key failed and Finnbot switched to the paid one for good."""
    from src.telegram_alert.notices import send_notice_now

    slot = "the shared Gemini key (universal content)" if user_name is None else f"{user_name}'s Gemini key"
    env_key = SHARED_FREE_ENV_KEY if user_name is None else (USER_PROFILES.get(user_name) or {}).get(
        "gemini_free_api_key", "")
    reset_name = "universal" if user_name is None else user_name
    body = (
        f"The free-tier Gemini key for {html.escape(slot)} ({html.escape(env_key)}) {html.escape(REASON_TEXT[error.reason])}, "
        f"so Finnbot has switched to the paid key and will keep using it until you reset the free one.\n\n"
        f"{html.escape(error.detail[:300])}\n\n"
        f"To try the free key again: python -m src.llm.gemini_key_rotation --reset {reset_name}"
    )
    send_notice_now("Andres", "gemini free key", f"{reset_name} switched to paid", body,
                    cooldown_hours=NOTICE_COOLDOWN_HOURS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Status and reset for the free/paid Gemini key switch.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="List every slot's state (default).")
    group.add_argument("--reset", metavar="NAME", help="Clear one slot's switch ('universal', a user's name, or 'all').")
    args = parser.parse_args()
    if args.reset:
        if args.reset.lower() == "all":
            reset(every_slot=True)
            print("Reset every slot. The free key is tried again everywhere it's set.")
        else:
            name = None if args.reset.lower() == "universal" else args.reset
            reset(name)
            print(f"Reset {args.reset}. Its free key is tried again.")
    else:
        state = _load()
        if not state:
            print("No slot has switched to its paid key.")
        for slot, info in state.items():
            label = "universal (shared)" if slot == SHARED_SLOT else slot
            print(f"{label}: switched to paid since {info.get('at', '?')} ({info.get('reason', '?')})")
