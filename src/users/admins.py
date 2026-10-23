# File to word problem notices for their reader: an admin gets the fix, everyone else is told to contact an admin.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code), at Andres's request.
- 2026-10-17: created. Why: the fixes in key and holdings notices ("update GEMINI_API_KEY in .env", "reconnect the
  brokerage in SnapTrade") need access to the server and the accounts, which only the admins (OPS.ALERT_USERS,
  currently Andres) have. Other users now get the problem plus "Contact Andres to address this issue."
"""
import sys
from pathlib import Path
from typing import Optional

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config.parameters import params_dict


def admins() -> list[str]:
    """The users who run Finnbot and can fix its problems (OPS.ALERT_USERS)."""
    return list(params_dict["OPS"]["ALERT_USERS"])


def is_admin(user_name: Optional[str]) -> bool:
    """True for an admin, and for None (no reader: the run log, or a notice about a shared key)."""
    return user_name is None or user_name in admins()


def contact_admin() -> str:
    """e.g. 'Contact Andres to address this issue.'"""
    names = admins()
    if not names:
        return "Contact the Finnbot admin to address this issue."
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" or {names[-1]}"
    return f"Contact {who} to address this issue."


def notice_text(problem: str, fix: str, user_name: Optional[str], detail: str = "", self_resolving: bool = False) -> str:
    """
    A problem worded for `user_name`. An admin gets the fix and the API's own message (`detail`); anyone else gets
    the contact line instead, unless the problem clears on its own (`self_resolving`, e.g. a rate limit), where the
    fix only says so and is shown to everyone.
    """
    if is_admin(user_name):
        text = " ".join(part for part in (problem, fix) if part)
        return f"{text} (API said: {detail[:200]})" if detail else text
    return " ".join(part for part in (problem, fix if self_resolving else contact_admin()) if part)
