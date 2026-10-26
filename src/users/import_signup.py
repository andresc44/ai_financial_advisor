# File to import sign-up form answers into .env and ACTIVE_USERS (by hand, or at the start of every digest run).
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-22, at Andres's request.

Usage (from the project root):
  venv/bin/python -m src.users.import_signup --list              # every answer: row, time, name, email, holdings
  venv/bin/python -m src.users.import_signup Sam --dry-run       # what Sam's newest answer would change; writes nothing
  venv/bin/python -m src.users.import_signup Sam                 # write it to .env, and activate Sam if nothing's missing
  venv/bin/python -m src.users.import_signup Sam --enter-keys    # then type the keys still missing (hidden) to save them
  venv/bin/python -m src.users.import_signup --sync              # sync everyone now, as every cron digest run does
Options: --no-activate leaves ACTIVE_USERS alone; --overwrite replaces an existing user whose email differs;
--sheet ID reads another answers sheet than SIGNUP_FORM_SHEET_ID.

The sheet is the reference (Andres, 2026-10-22): every cron digest run starts with sync_signups()
(src/digest/digest_payload.py), which applies every user's newest answer to .env and makes ACTIVE_USERS exactly the
sheet's users with nothing required missing (see sync_signups). Without SIGNUP_FORM_SHEET_ID in .env it does nothing.

Where the answers come from: the spreadsheet the sign-up form (docs/finnbot_signup_form.gs) writes to, shared with
the service account (GOOGLE_APPLICATION_CREDENTIALS). Its "Form responses" tab is found by name, since the
spreadsheet's first tab can be an empty "Sheet1".

What applying one answer (the user's newest, matched by first name or email) does:
- Writes each answered setting to .env as <NAME>_<SETTING> (src/personalized/user_profiles.py names), replacing the
  line if there is one (e.g. a "${ANDRES_EMAIL}" stand-in) and adding the rest to the user's block. Questions the
  user skipped (another branch of the form) leave .env untouched. Since 2026-10-22 the form doesn't ask for a
  holdings model (every user gets LLM["HOLDINGS"]), so each answer clears an earlier model choice.
- The same name and email as an existing user: the form is the reference, so its answers replace theirs. The same
  name with another email stops (it may be someone else), unless --overwrite.
- Checks what's still missing or invalid for the user's choices, API keys included (check_config's rules, plus the
  Telegram chat ID when they chose Telegram). The form never asks for keys, which users send privately; secret
  values are never printed.
- By hand (python -m src.users.import_signup Sam), adds the name to ACTIVE_USERS in src/config/parameters.py once nothing required
  is missing. The sync instead sets the whole list from the sheet. A sign-up still waiting for its keys becomes
  active at the first digest run after they're in .env.
"""
import argparse
import ast
import csv
import getpass
import hashlib
import html
import io
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import requests
from dotenv import dotenv_values, load_dotenv

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.llm import llm_models
from src.users.admins import admins
from src.config.check_config import USER_KEYS, VALUE_CHECKS, _required, news_on
from src.config.parameters import params_dict
from src.ops.run_logging import debug
from src.personalized.user_profiles import MISSING_VALUE, USER_PROFILES, build_profile, env_prefix

ENV_PATH = project_root / ".env"
PARAMS_PATH = project_root / "src" / "config" / "parameters.py"
SHEET_ID_KEY = "SIGNUP_FORM_SHEET_ID"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly", "https://www.googleapis.com/auth/drive.readonly"]
# Settings the form never asks for: sent privately, never printed, and offered by --enter-keys when missing
SECRET_SETTINGS = ("anthropic_api_key", "deepseek_api_key", "gemini_api_key", "snaptrade_client_id",
                   "snaptrade_consumer_key", "telegram_chat_id", "google_sheets_id")
# 2026-10-22: API keys a user's choices need get KEY=None in .env while missing, so the gap is visible there
API_KEY_SETTINGS = ("anthropic_api_key", "deepseek_api_key", "gemini_api_key", "snaptrade_client_id",
                    "snaptrade_consumer_key")
LEGACY_SETTINGS = ("holdings_claude_model", "holdings_claude_effort")  # Older names; never asked or required
# 2026-10-22: per-user holdings models, no longer used (user_settings.get_llm_settings): each answer clears them
HOLDINGS_MODEL_SETTINGS = ("holdings_llm_model", "holdings_llm_effort") + LEGACY_SETTINGS
NOTICE_COOLDOWN_HOURS = 24 * 365  # Each sign-up notice is keyed by its answer, so it's sent once


# ---------------------------------------------------------------------------- Reading the answers sheet

def fetch_responses(sheet_id: str) -> tuple[list[str], list[list[str]]]:
    """(header, rows) of the answers sheet's "Form responses" tab, read as the service account."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path or not os.path.exists(creds_path):
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS must point to the service account's JSON file.")
    creds = service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}"}
    meta = requests.get(f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}",
                        params={"fields": "sheets.properties(title,sheetId)"}, headers=headers, timeout=30)
    if not meta.ok:
        raise RuntimeError(f"Couldn't open the answers sheet ({meta.status_code}): check {SHEET_ID_KEY} and that "
                           f"it's shared with the service account.")
    tabs = [sheet["properties"] for sheet in meta.json().get("sheets", [])]
    tab = next((t for t in tabs if t["title"].lower().startswith("form responses")), tabs[0] if tabs else None)
    if tab is None:
        raise RuntimeError("The answers sheet has no tabs.")
    export = requests.get(f"https://docs.google.com/spreadsheets/d/{sheet_id}/export",
                          params={"format": "csv", "gid": tab["sheetId"]}, headers=headers, timeout=30)
    export.raise_for_status()
    rows = list(csv.reader(io.StringIO(export.content.decode("utf-8"))))
    return (rows[0], rows[1:]) if rows else ([], [])


@dataclass
class Answer:
    """One form response. Titles can repeat ("Please confirm", the two effort questions), so cells keep their order."""
    row_number: int  # In the sheet, counting the header as row 1
    cells: list[tuple[str, str]]

    @classmethod
    def from_row(cls, header: list[str], row: list[str], row_number: int) -> "Answer":
        padded = row + [""] * (len(header) - len(row))
        return cls(row_number, [(title.strip(), value.strip()) for title, value in zip(header, padded)])

    def get(self, title: str, nth: Optional[int] = None) -> str:
        """The answer to `title`: its nth occurrence's, or the first non-empty one."""
        values = [value for cell_title, value in self.cells if cell_title == title]
        if nth is not None:
            return values[nth] if nth < len(values) else ""
        return next((value for value in values if value), "")

    @property
    def name(self) -> str:
        return self.get("Your first name")

    @property
    def email(self) -> str:  # The digest's address, else the Google account the form collected
        return self.get("Email address for the digest") or self.get("Email address")

    def fingerprint(self) -> str:
        """Changes when the answer is edited; the state file keeps this instead of the answers themselves."""
        return hashlib.sha256(json.dumps(self.cells).encode("utf-8")).hexdigest()


def read_answers(sheet_id: str) -> list[Answer]:
    header, rows = fetch_responses(sheet_id)
    return [Answer.from_row(header, row, i + 2) for i, row in enumerate(rows) if any(cell.strip() for cell in row)]


def newest_answers(answers: list[Answer]) -> dict[str, Answer]:
    """{first name in lower case: that person's newest answer}. The sheet adds rows in order."""
    newest = {}
    for answer in answers:
        if answer.name:
            newest[_display_name(answer.name).lower()] = answer
    return newest


# ---------------------------------------------------------------------------- From answers to settings

@dataclass
class Plan:
    """What one answer means for the user's settings."""
    name: str
    settings: dict[str, Optional[str]] = field(default_factory=dict)  # profile key -> value; None = clear if set
    wants_telegram: bool = False
    wants_watchdog: bool = False
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _display_name(raw: str) -> str:
    """'mary-jo' or 'STEVEN' as 'Mary-Jo' / 'Steven': the name in ACTIVE_USERS and the greeting."""
    return "-".join(part[:1].upper() + part[1:].lower() for part in raw.strip().split("-") if part)


def plan_from_answer(answer: Answer) -> Plan:
    plan = Plan(name=_display_name(answer.name))
    s = plan.settings
    if not plan.name:
        plan.problems.append("The answer has no first name.")
    if answer.email:
        s["destination_email"] = answer.email

    digests = answer.get("Which digests do you want?")
    if digests:
        s["Receive_Premarket_Digest"] = str(digests.startswith(("Both", "Pre-Market")))
        s["Receive_Postmarket_Digest"] = str(digests.startswith(("Both", "Post-Market")))

    holdings = answer.get("How should Finnbot see your holdings?")
    source = _holdings_source(holdings)
    if source is None:
        plan.problems.append(f"Unrecognised holdings answer: {holdings!r}.")
        return plan
    s["holdings_source"] = source
    timezone_name = answer.get("Your timezone") or answer.get("Your timezone (optional)")
    if timezone_name:
        s["home_timezone"] = timezone_name
    if source == "NO_HOLDINGS":
        if digests.startswith("Neither"):
            plan.notes.append("Ideas only with neither digest: they would get nothing.")
        return plan

    if answer.get("Brokerage name to show in your digest"):
        s["account_company"] = answer.get("Brokerage name to show in your digest")
    if answer.get("Home currency"):
        s["home_currency"] = answer.get("Home currency").split()[0].upper()
    if answer.get("News for each holding"):
        s["receive_holding_news"] = str(answer.get("News for each holding").startswith("On"))

    # 2026-10-22 (Andres): no model question any more; every user's holdings use LLM["HOLDINGS"], so an earlier
    # choice in .env is cleared (a model answered on the old form is ignored)
    for key in HOLDINGS_MODEL_SETTINGS:
        s[key] = None

    # 2026-10-26 (Andres): the form no longer asks how often, when, or about the tripwire. With the watchdog, nothing
    # is written, so params_dict["WATCHDOG"]'s defaults apply (every 4 h, extended hours, tripwire on) unless .env
    # sets them, like <NAME>_LONG_RECS; apply_answer only clears a 0 left by an earlier answer without it
    telegram = answer.get("Do you want Telegram messages?")
    plan.wants_telegram = telegram.startswith("Yes")
    plan.wants_watchdog = telegram.startswith("Yes, with the watchdog")
    if telegram and not plan.wants_watchdog:
        s["watchdog_interval_hours"] = "0"  # Telegram without the watchdog, or no Telegram
    return plan


def _holdings_source(holdings: str) -> Optional[str]:
    """
    The holdings answer as SNAPTRADE, SHEETS, or NO_HOLDINGS (None if unrecognised). Claude-authored (2026-10-26):
    the form's options were reworded that day ("Link automatically to brokerage, ... (through SnapTrade)", "Manually
    update a Google Sheet", "No holdings, just suggestions to buy (free)"); the first form's wording still reads.
    Matched by keyword, so a spelling fix in the form (as that day's "auomatically") needs no change here.
    """
    lowered = holdings.lower()
    if "snaptrade" in lowered:
        return "SNAPTRADE"
    if "google sheet" in lowered:
        return "SHEETS"
    if lowered.startswith(("no holdings", "ideas only")):
        return "NO_HOLDINGS"
    return None


# ---------------------------------------------------------------------------- Writing .env and ACTIVE_USERS

_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./@+:,-]*$")


def _env_value(value: str) -> str:
    """As written in .env: bare when plain, else single-quoted (python-dotenv reads those literally, no ${...})."""
    if _SAFE_VALUE.match(value):
        return value
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def _line_key(line: str) -> Optional[str]:
    match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
    return match.group(1) if match else None


def _write_atomically(path: Path, text: str) -> None:
    """A temporary file in the same folder, then a rename, so a failure can't leave a half-written file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@dataclass
class EnvChange:
    key: str
    old: Optional[str]  # The line's raw value (a ${...} stand-in shows as written), None if there was no line
    new: Optional[str]  # None: cleared


def update_env(path: Path, prefix: str, name: str, assignments: dict[str, Optional[str]],
               dry_run: bool = False) -> list[EnvChange]:
    """
    Sets each KEY=value in .env (value None: empties an existing line), replacing the key's last line or adding it
    after the user's other keys (a new block at the end for a new user). Keeps the file's line endings.
    """
    raw = path.read_bytes().decode("utf-8") if path.exists() else ""
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    current = dotenv_values(stream=io.StringIO(raw), interpolate=False)
    changes, additions = [], []
    for key, value in assignments.items():
        indexes = [i for i, line in enumerate(lines) if _line_key(line) == key]
        old = current.get(key) if indexes else None
        if value is None:
            if indexes and (old or "").strip():
                lines[indexes[-1]] = f"{key}="
                changes.append(EnvChange(key, old, None))
        elif indexes:
            if (old or "") != value:
                lines[indexes[-1]] = f"{key}={_env_value(value)}"
                changes.append(EnvChange(key, old, value))
        else:
            additions.append(f"{key}={_env_value(value)}")
            changes.append(EnvChange(key, None, value))
    if additions:
        own = [i for i, line in enumerate(lines) if (_line_key(line) or "").startswith(prefix + "_")]
        if own:
            lines[own[-1] + 1:own[-1] + 1] = additions
        else:
            lines += ["", f"# --- {name} (from the sign-up form, {date.today().isoformat()}; "
                          f"python -m src.users.import_signup {name}) ---"] + additions
    if changes and not dry_run:
        _write_atomically(path, newline.join(lines) + newline)
    return changes


def _code(line: str) -> str:
    """A parameters.py line without its comment."""
    return line.split("#", 1)[0]


def activate_user(path: Path, name: str, dry_run: bool = False) -> tuple[bool, str]:
    """
    Adds `name` to ACTIVE_USERS in parameters.py, or uncomments it when it's there commented out. Returns (whether
    it changed the file, what happened). The new file must parse and list the name, or nothing is written.
    """
    raw = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.replace("\r\n", "\n").split("\n")
    start = next((i for i, line in enumerate(lines) if '"ACTIVE_USERS"' in _code(line)), None)
    end = next((i for i in range(start, len(lines)) if "]" in _code(lines[i])), None) if start is not None else None
    if start is None or end is None:
        return False, "ACTIVE_USERS wasn't found in parameters.py; add the name by hand."
    quoted = f'"{name}"'
    block = range(start, end + 1)
    if any(quoted.lower() in _code(lines[i]).lower() for i in block):
        return False, f"{name} is already in ACTIVE_USERS."
    commented = next((i for i in block if re.match(rf'^\s*#\s*{re.escape(quoted)}', lines[i], flags=re.I)), None)
    if commented is not None:
        line = re.sub(rf'#\s*{re.escape(quoted)}\s*,?', f'{quoted},', lines[commented], count=1, flags=re.I)
        lines[commented] = line
        message = f"{name} was commented out in ACTIVE_USERS; uncommented."
    elif start == end:  # All on one line: "ACTIVE_USERS": ["Andres"],
        code = _code(lines[end])
        before, after = code.rsplit("]", 1)
        joiner = "" if before.rstrip().endswith(("[", ",")) else ", "
        lines[end] = f'{before.rstrip()}{joiner}{quoted}]{after}' + lines[end][len(code):]
        message = f"{name} added to ACTIVE_USERS."
    else:
        previous = next((i for i in range(end - 1, start - 1, -1) if _code(lines[i]).strip()), start)
        code = _code(lines[previous])
        if not code.rstrip().endswith((",", "[")):  # The last entry needs a comma before the new one
            lines[previous] = code.rstrip() + "," + lines[previous][len(code.rstrip()):]
        indent = re.match(r"^\s*", lines[end]).group(0) or " " * 20
        lines.insert(end, f'{indent}{quoted},  # Added from the sign-up form ({date.today().isoformat()})')
        message = f"{name} added to ACTIVE_USERS."
    text = "\n".join(lines)
    users = next((ast.literal_eval(node.values[i]) for node in ast.walk(ast.parse(text)) if isinstance(node, ast.Dict)
                  for i, key in enumerate(node.keys) if isinstance(key, ast.Constant) and key.value == "ACTIVE_USERS"),
                 [])
    if name not in users:
        return False, "ACTIVE_USERS couldn't be updated safely; add the name by hand."
    if not dry_run:
        _write_atomically(path, text.replace("\n", newline))
    return True, message


def _listed_users(path: Path) -> list[str]:
    """ACTIVE_USERS as parameters.py lists it now ([] when it can't be read)."""
    try:
        tree = ast.parse(path.read_bytes().decode("utf-8"))
    except (OSError, SyntaxError, ValueError):
        return []
    return next((list(ast.literal_eval(node.values[i])) for node in ast.walk(tree) if isinstance(node, ast.Dict)
                 for i, key in enumerate(node.keys) if isinstance(key, ast.Constant) and key.value == "ACTIVE_USERS"),
                [])


def set_active_users(path: Path, names: list[str], dry_run: bool = False) -> bool:
    """
    Claude-authored (2026-10-22): makes ACTIVE_USERS in parameters.py exactly `names` (the sync's: the sign-up sheet's
    users with nothing required missing), one per line, replacing the whole list, commented-out names included.
    Returns whether it changed the file. The new file must parse and list exactly `names`, or nothing is written.
    """
    if _listed_users(path) == names:
        return False
    raw = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.replace("\r\n", "\n").split("\n")
    start = next((i for i, line in enumerate(lines) if '"ACTIVE_USERS"' in _code(line)), None)
    end = next((i for i in range(start, len(lines)) if "]" in _code(lines[i])), None) if start is not None else None
    if start is None or end is None:
        print("[signup] ACTIVE_USERS wasn't found in parameters.py; it wasn't changed.")
        return False
    indent = re.match(r"^\s*", lines[start]).group(0)
    after = _code(lines[end]).split("]", 1)[1]  # Usually ","
    block = ([f'{indent}"ACTIVE_USERS": [  # Synced from the sign-up sheet (src/users/import_signup.py): edit the sheet']
             + [f'{indent}    "{name}",' for name in names] + [f"{indent}]{after.rstrip()}"])
    text = "\n".join(lines[:start] + block + lines[end + 1:])
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = None
    users = next((list(ast.literal_eval(node.values[i])) for node in ast.walk(tree) if isinstance(node, ast.Dict)
                  for i, key in enumerate(node.keys) if isinstance(key, ast.Constant) and key.value == "ACTIVE_USERS"),
                 None) if tree else None
    if users != names:
        print("[signup] ACTIVE_USERS couldn't be rewritten safely; parameters.py wasn't changed.")
        return False
    if not dry_run:
        _write_atomically(path, text.replace("\n", newline))
    return True


# ---------------------------------------------------------------------------- What's still missing

def missing_report(name: str, env: dict[str, Optional[str]], plan: Plan) -> list[tuple[str, str, str]]:
    """(status, .env key, what it is) for each of the user's settings: OK, MISSING, INVALID, or unset (optional)."""
    profile = build_profile(name)

    def value(key: str) -> Optional[str]:  # Unset, empty, and the KEY=None placeholder all count as missing
        current = (env.get(profile[key]) or "").strip()
        return current if current and current != MISSING_VALUE else None

    source = value("holdings_source")
    model = llm_models.job_settings("HOLDINGS")["model"]  # 2026-10-22: the same for every user
    provider = llm_models.provider_of(model)
    news = news_on(value("receive_holding_news"))  # 2026-10-23: the Gemini key is only needed for the news
    rows = []
    for key, (rule, description) in USER_KEYS.items():
        if key in LEGACY_SETTINGS:
            continue
        required = _required(rule, source, provider, news)
        if key == "telegram_chat_id" and plan.wants_telegram and source in ("SNAPTRADE", "SHEETS"):
            required, description = True, "Telegram chat ID (they chose Telegram; sent privately)"
        if key in ("anthropic_api_key", "deepseek_api_key", "gemini_api_key", "snaptrade_client_id",
                   "snaptrade_consumer_key", "google_sheets_id"):  # 2026-10-26: the form no longer asks for the sheet ID
            description += " (sent privately)"
        current = value(key)
        if current is None:
            rows.append(("MISSING" if required else "unset", profile[key], description))
        elif key in VALUE_CHECKS and not VALUE_CHECKS[key][0](current):
            rows.append(("INVALID", profile[key], f"must be {VALUE_CHECKS[key][1]}"))
        else:
            rows.append(("OK", profile[key], description))
    return rows


def _incomplete(rows: list[tuple[str, str, str]], plan: Plan) -> list[str]:
    """What stops the user being activated: missing or invalid settings, and problems with the answer."""
    return [f"{status} {key}" for status, key, _ in rows if status in ("MISSING", "INVALID")] + plan.problems


def enter_keys(path: Path, name: str, rows: list[tuple[str, str, str]]) -> int:
    """Asks for each missing private setting with hidden input and saves what's typed. Returns how many were saved."""
    profile = build_profile(name)
    private = {profile[key] for key in SECRET_SETTINGS}
    saved = {}
    for status, env_key, description in rows:
        if status == "MISSING" and env_key in private:
            entered = getpass.getpass(f"{env_key} ({description}); input hidden, Enter to skip: ").strip()
            if entered:
                saved[env_key] = entered
    if saved:
        update_env(path, env_prefix(name), name, saved)
    return len(saved)


# ---------------------------------------------------------------------------- Applying one answer

@dataclass
class Applied:
    plan: Plan
    status: str = "applied"  # "applied", "conflict" (name taken by another email), or "invalid" (no name or source)
    existing: bool = False   # The name already had settings in .env
    conflict_email: str = ""
    shared_email_with: list[str] = field(default_factory=list)
    changes: list[EnvChange] = field(default_factory=list)
    rows: list[tuple[str, str, str]] = field(default_factory=list)
    env: dict[str, Optional[str]] = field(default_factory=dict)  # .env once applied (as it would be, in a dry run)
    activated: bool = False
    activation: str = ""


def apply_answer(answer: Answer, env_path: Path = ENV_PATH, params_path: Path = PARAMS_PATH, activate: bool = True,
                 overwrite: bool = False, dry_run: bool = False, keys: bool = False, write: bool = True) -> Applied:
    """
    Writes one answer's settings to .env and, when nothing required is missing, activates the user. write=False
    only checks .env as it is and activates (the sync, for a sign-up that was waiting for its keys), so manual
    edits made since the answer was applied stay.
    """
    plan = plan_from_answer(answer)
    result = Applied(plan)
    if not plan.name or "holdings_source" not in plan.settings:
        result.status = "invalid"
        return result
    prefix, profile = env_prefix(plan.name), build_profile(plan.name)
    env = dotenv_values(env_path) if env_path.exists() else {}

    # The same name already in .env: the form is the reference when the email matches too
    result.existing = any(key.startswith(prefix + "_") for key in env)
    their_email = (env.get(profile["destination_email"]) or "").strip()
    form_email = plan.settings.get("destination_email") or ""
    if result.existing and their_email and their_email.lower() != form_email.lower() and not overwrite:
        result.status, result.conflict_email = "conflict", their_email
        return result
    result.shared_email_with = sorted({
        key[:-len("_EMAIL")].title() for key, value in env.items()
        if key.endswith("_EMAIL") and not key.startswith(prefix + "_") and form_email
        and (value or "").strip().lower() == form_email.lower()})

    if plan.wants_watchdog and (env.get(profile["watchdog_interval_hours"]) or "").strip() == "0":
        plan.settings["watchdog_interval_hours"] = None  # Off from an earlier answer: back to the default
    if write:
        result.changes = update_env(env_path, prefix, plan.name,
                                    {profile[key]: value for key, value in plan.settings.items()}, dry_run=dry_run)
    if dry_run:
        after = dict(env)
        for change in result.changes:
            after[change.key] = change.new or ""
    else:
        after = dotenv_values(env_path)
    result.rows = missing_report(plan.name, after, plan)
    if write:  # Each missing API key their choices need gets KEY=None, so the gap shows in .env (never printed)
        api_keys = {profile[key] for key in API_KEY_SETTINGS}
        placeholders = {env_key: MISSING_VALUE for status, env_key, _ in result.rows
                        if status == "MISSING" and env_key in api_keys
                        and (after.get(env_key) or "").strip() != MISSING_VALUE}
        if placeholders:
            result.changes += update_env(env_path, prefix, plan.name, placeholders, dry_run=dry_run)
            after = {**after, **placeholders} if dry_run else dotenv_values(env_path)
        # Claude-authored (2026-10-24, Andres): an empty <NAME>_TELEGRAM_CHAT_ID= line when .env has none, so there's
        # a spot for the chat ID once they have one. 2026-10-25 (Andres): only when the form says they'll use Telegram.
        # A value already there is never touched. 2026-10-26 (Andres): the same for <NAME>_SHEETS_ID when they chose a
        # Google Sheet, since the form no longer asks for its ID; once it's filled in, the sync activates them
        spots = [profile["telegram_chat_id"]] if plan.wants_telegram else []
        if plan.settings["holdings_source"] == "SHEETS":
            spots.append(profile["google_sheets_id"])
        blanks = {env_key: "" for env_key in spots if env_key not in after}
        if blanks:
            result.changes += update_env(env_path, prefix, plan.name, blanks, dry_run=dry_run)
            after = {**after, **blanks} if dry_run else dotenv_values(env_path)
    if keys and not dry_run and enter_keys(env_path, plan.name, result.rows):
        after = dotenv_values(env_path)
        result.rows = missing_report(plan.name, after, plan)
    result.env = after
    if not plan.wants_telegram and plan.settings["holdings_source"] != "NO_HOLDINGS" and (
            after.get(profile["telegram_chat_id"]) or "").strip():
        plan.notes.append(f"They chose no Telegram, but {profile['telegram_chat_id']} is set: earnings reminders and "
                          f"notices still go there. Remove it if that's not wanted.")
    if activate:
        missing = _incomplete(result.rows, plan)
        if missing:
            result.activation = (f"not activated yet: {len(missing)} thing(s) missing; the first digest run after "
                                 f"they're fixed activates them")
        else:
            result.activated, result.activation = activate_user(params_path, plan.name, dry_run=dry_run)
    return result


def _print_result(result: Applied, answer: Answer, dry_run: bool) -> None:
    plan = result.plan
    print(f"\n{plan.name or '(no name)'}: answer in row {answer.row_number}" + (" (dry run: nothing written)" if dry_run else ""))
    for problem in plan.problems:
        print(f"  PROBLEM  {problem}")
    if result.status == "invalid":
        print("  STOPPED  The answer has no name or holdings choice, so there's nothing to import.")
        return
    if result.status == "conflict":
        print(f"  STOPPED  {plan.name} is already in .env with another email ({result.conflict_email}; the form says "
              f"{plan.settings.get('destination_email')}). It may be someone else: use another name, or --overwrite.")
        return
    if result.existing:
        print(f"  NOTE     {plan.name} already had settings: the form's answers replace them.")
    if result.shared_email_with:
        print(f"  NOTE     {', '.join(result.shared_email_with)} also use this email.")
    private = {build_profile(plan.name)[key] for key in SECRET_SETTINGS}
    print("  .env:" if result.changes else "  .env: already up to date")
    for change in result.changes:
        def shown(value):
            return "(set)" if change.key in private else value
        before = "new" if change.old is None else f"was {shown(change.old) if change.old else '(empty)'}"
        after = ("(cleared: the default applies)" if change.new is None
                 else "(empty: a spot to fill in)" if change.new == ""
                 else "None (placeholder: the key is still missing)" if change.new == MISSING_VALUE
                 else shown(change.new))
        print(f"    {change.key} = {after}   [{before}]")
    if result.activation:
        print(f"  ACTIVE_USERS: {result.activation}")
    for note in plan.notes:
        print(f"  NOTE     {note}")
    problems = [r for r in result.rows if r[0] in ("MISSING", "INVALID")]
    print("  Still to do:" if problems else "  Nothing required is missing.")
    for status, env_key, description in problems:
        print(f"    {status:8} {env_key:36} {description}")
    print("  Set: " + (", ".join(r[1] for r in result.rows if r[0] == "OK") or "nothing"))


# ---------------------------------------------------------------------------- At the start of every digest run

def _notify_admins(reason: str, body: str) -> None:
    """
    A Telegram message to the admins, once per `reason` (the notices log remembers it). An admin who isn't an active
    user still gets it: the Telegram sender finds their chat ID through USER_PROFILES, which doesn't make them active.
    """
    from src.telegram_alert.notices import send_notice_now
    for admin in admins():
        USER_PROFILES.setdefault(admin, build_profile(admin))
        try:
            send_notice_now(admin, "signup", reason, body, cooldown_hours=NOTICE_COOLDOWN_HOURS)
        except Exception as e:  # A notice must never stop the digest
            print(f"[WARNING] Sign-up notice to {admin} failed: {e}")


def _activation_notice(result: Applied) -> tuple[str, str]:
    """
    Claude-authored (2026-10-22, Andres): the admins' Telegram message when a user is added to ACTIVE_USERS, with what
    they signed up for, so it can be checked at a glance. (reason, body) for _notify_admins: once per user and day.
    """
    plan, s = result.plan, result.plan.settings
    source = {"SHEETS": "Google Sheets", "SNAPTRADE": "SnapTrade", "NO_HOLDINGS": "ideas only"}.get(
        s.get("holdings_source"), s.get("holdings_source") or "?")
    digests = [label for key, label in (("Receive_Premarket_Digest", "Pre-Market"), ("Receive_Postmarket_Digest", "Post-Market"))
               if s.get(key) == "True"]
    lines = [f"Holdings: {source}", f"Digests: {' and '.join(digests) or 'none'}"]
    lines += [f"{label}: {s[key]}" for key, label in (("home_currency", "Home currency"), ("home_timezone", "Timezone"))
              if s.get(key)]
    if source != "ideas only":
        lines.append("Telegram: " + ("no" if not plan.wants_telegram else _watchdog_summary(plan.name, result.env)))
    return (f"active {plan.name} {date.today().isoformat()}",
            f"✅ New Finnbot user added: {html.escape(plan.name)}. Nothing required is missing, so they get the next "
            f"digest.\n\n" + "\n".join(f"- {html.escape(line, quote=False)}" for line in lines))


def _watchdog_summary(name: str, env: dict[str, Optional[str]]) -> str:
    """
    Claude-authored (2026-10-26): "yes, watchdog every 4 h (extended), tripwire on" for a Telegram user: their .env
    settings, else params_dict["WATCHDOG"]'s defaults (the form no longer asks).
    """
    profile, watchdog = build_profile(name), params_dict["WATCHDOG"]

    def value(key: str) -> Optional[str]:
        current = (env.get(profile[key]) or "").strip()
        return current if current and current != MISSING_VALUE else None

    hours = value("watchdog_interval_hours") or str(watchdog["DEFAULT_INTERVAL_HOURS"])
    if hours == "0":
        return "yes, no watchdog"
    tripwire = value("watchdog_tripwire")
    tripwire_on = watchdog["TRIPWIRE_DEFAULT"] if tripwire is None else tripwire.lower() in ("true", "1", "yes", "on")
    return (f"yes, watchdog every {hours} h ({value('watchdog_session') or watchdog['SESSION']}), "
            f"tripwire {'on' if tripwire_on else 'off'}")


def sync_signups(env_path: Path = ENV_PATH, params_path: Path = PARAMS_PATH, sheet_id: Optional[str] = None,
                 dry_run: bool = False) -> list[str]:
    """
    Claude-authored (2026-10-22): run first in every cron digest run (digest_payload.__main__), before the users are
    read, and by `python -m src.users.import_signup --sync`. The sign-up sheet is the reference (Andres, 2026-10-22):
    - Every user's newest answer is applied to .env each run. Only settings that differ from the sheet are written,
      so a hand edit to a setting the form covers goes back to the sheet's value: edit the answer instead.
    - ACTIVE_USERS becomes exactly the sheet's users with nothing required missing, in the sheet's order: in
      parameters.py, and in this run's memory (params_dict["ACTIVE_USERS"], USER_PROFILES, os.environ), so a new
      user gets this run's digest, and anyone not in the sheet, or missing a key, stops getting them. Their .env
      settings stay, keys included, so they come back as soon as they're in the sheet and complete again.
    - The admins hear on Telegram, once each: a new or edited answer (with what's missing), a user becoming active
      ("New Finnbot user added", with what they signed up for; also when added by hand, 2026-10-22) or inactive,
      and a name clash.
    If the sheet can't be read or has no answers, nothing changes, so a glitch can't switch everyone off; an answer
    that can't be read (2026-10-26) leaves its user as they were, and the admins are told once.
    Never raises: a problem is logged and the digest goes on. Returns the users who became active.
    2026-10-26: `dry_run` (--sync --dry-run) only prints what it would change (setting names, never values) and who
    would be active: nothing is written to .env or parameters.py, nothing is sent, and this process's users don't change.
    Before, --dry-run was ignored by --sync, which then wrote for real.
    """
    sheet_id = sheet_id or os.environ.get(SHEET_ID_KEY, "").strip()
    if not sheet_id:
        return []
    try:
        answers = newest_answers(read_answers(sheet_id))
        if not answers:
            print("[signup] The sheet has no answers, so ACTIVE_USERS is left as it is.")
            return []
        wanted, waiting, kept, notices = [], {}, [], []  # Notices go last, once the run's users and settings are in place
        results = {}
        before = _listed_users(params_path)
        for answer in answers.values():
            result = apply_answer(answer, env_path, params_path, activate=False, dry_run=dry_run)
            results[result.plan.name] = result
            if dry_run and result.changes:
                print(f"[signup dry run] {result.plan.name}: would write {', '.join(c.key for c in result.changes)}")
            elif not dry_run:
                _report_answer(result, answer, notices)
            missing = _incomplete(result.rows, result.plan)
            if result.status == "applied" and not missing:
                wanted.append(result.plan.name)
            elif result.status == "applied":
                waiting[result.plan.name] = missing
            elif result.status == "invalid" and result.plan.name in before:
                # Claude-authored (2026-10-26): an answer that can't be read (e.g. the form's wording changed, as on
                # 2026-10-26, when every user was switched off) leaves an active user active, settings untouched
                wanted.append(result.plan.name)
                kept.append(result.plan.name)
        if dry_run:
            print(f"[signup dry run] ACTIVE_USERS would be: {', '.join(wanted) or 'none'}"
                  + (f"; added {', '.join(n for n in wanted if n not in before)}" if set(wanted) - set(before) else "")
                  + (f"; removed {', '.join(n for n in before if n not in wanted)}" if set(before) - set(wanted) else "")
                  + (f"; waiting for settings: {', '.join(waiting)}" if waiting else "") + ". Nothing was written.")
            return []
        set_active_users(params_path, wanted)
        load_dotenv(env_path, override=True)  # This run sees the new settings
        params_dict["ACTIVE_USERS"][:] = wanted
        for name in [name for name in USER_PROFILES if name not in wanted]:
            del USER_PROFILES[name]
        for name in wanted:
            USER_PROFILES[name] = build_profile(name)

        added = [name for name in wanted if name not in before]
        removed = [name for name in before if name not in wanted]
        today = date.today().isoformat()
        for name in added:  # 2026-10-22: with what they signed up for (_activation_notice)
            notices.append(_activation_notice(results[name]))
        for name in removed:
            why = (f"still missing: {', '.join(waiting[name])}" if name in waiting
                   else "they aren't in the sign-up sheet (or their answer couldn't be imported)")
            notices.append((f"inactive {name} {today}",
                            f"⏸️ {html.escape(name)} is no longer active: {html.escape(why, quote=False)}. Their "
                            f".env settings are kept."))
        for reason, body in notices:
            _notify_admins(reason, body)
        # 2026-10-24: logged when something changed or is waiting; every hour otherwise only while debugging
        (print if added or removed or waiting or kept else debug)(
              f"[signup] {len(answers)} user(s) in the sheet. ACTIVE_USERS: {', '.join(wanted) or 'none'}"
              + (f"; added {', '.join(added)}" if added else "") + (f"; removed {', '.join(removed)}" if removed else "")
              + (f"; waiting for settings: {', '.join(waiting)}" if waiting else "")
              + (f"; kept as they were (answer unreadable): {', '.join(kept)}" if kept else "") + ".")
        return added
    except Exception as e:
        print(f"[ERROR] Sign-up sync failed ({type(e).__name__}: {e}); the digest goes on without it.")
        return []


def _report_answer(result: Applied, answer: Answer, notices: list[tuple[str, str]]) -> None:
    """
    The run log line for one answer, and the admins' Telegram notice (added to `notices`: (reason, body)), when it
    changed something or can't be used.
    """
    plan, name = result.plan, html.escape(result.plan.name)
    fingerprint = answer.fingerprint()[:12]  # The notice goes once per version of the answer
    if result.status == "invalid":
        print(f"[signup] Row {answer.row_number}: {' '.join(plan.problems) or 'no name or holdings choice.'} Skipped.")
        if plan.name:  # 2026-10-26: e.g. a reworded option in the form, which the import has to learn
            notices.append((f"unreadable {plan.name} {fingerprint}", (
                f"⚠️ {name}'s sign-up answer couldn't be read: {html.escape(' '.join(plan.problems), quote=False)} "
                f"If {name} was active, they stay active with their current settings. Check the form's wording "
                f"against src/users/import_signup.py.")))
        return
    if result.status == "conflict":
        print(f"[signup] {plan.name}: the name is already used with another email; not imported.")
        notices.append((f"conflict {plan.name} {fingerprint}", (
            f"⚠️ Sign-up from {name} ({html.escape(plan.settings.get('destination_email', ''))}) wasn't "
            f"imported: {name} is already set up with another email. If it's the same person, run "
            f"python -m src.users.import_signup {name} --overwrite; otherwise ask them to sign up with another name.")))
        return
    if not result.changes:
        return
    missing = _incomplete(result.rows, plan)
    print(f"[signup] {plan.name}: {len(result.changes)} setting(s) written from the sheet; "
          f"missing: {', '.join(missing) or 'nothing'}")
    notices.append((f"applied {plan.name} {fingerprint}", (
        f"\U0001f4dd {'Updated' if result.existing else 'New'} Finnbot sign-up: {name} "
        f"({html.escape(plan.settings['holdings_source'])}), saved to .env.\n\n"
        + (("Still needed before they're active:\n" + "\n".join(f"- {html.escape(m)}" for m in missing))
           if missing else "Nothing required is missing."))))


# ---------------------------------------------------------------------------- Command line

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Import sign-up form answers into .env and ACTIVE_USERS.")
    parser.add_argument("who", nargs="?", help="The user's first name or email, as in the form")
    parser.add_argument("--list", action="store_true", help="List every answer and stop")
    parser.add_argument("--sync", action="store_true", help="Sync every user from the sheet, as each digest run does")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change; write nothing")
    parser.add_argument("--no-activate", action="store_true", help="Leave ACTIVE_USERS in parameters.py alone")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing user whose email differs")
    parser.add_argument("--enter-keys", action="store_true", help="Then type missing keys (hidden) to save them")
    parser.add_argument("--sheet", help=f"Answers sheet ID (default: {SHEET_ID_KEY} in .env)")
    args = parser.parse_args(argv)

    load_dotenv(ENV_PATH, override=True)
    sheet_id = args.sheet or os.environ.get(SHEET_ID_KEY, "").strip()
    if not sheet_id:
        parser.error(f"Set {SHEET_ID_KEY} in .env, or pass --sheet.")
    if args.sync:
        sync_signups(sheet_id=sheet_id, dry_run=args.dry_run)
        if not args.dry_run:
            print(f"ACTIVE_USERS is now: {', '.join(_listed_users(PARAMS_PATH)) or 'none'}")
        return 0
    answers = read_answers(sheet_id)
    if args.list or not args.who:
        print(f"{len(answers)} answer(s):")
        for a in answers:
            print(f"  row {a.row_number:3}  {a.get('Timestamp'):20}  {a.name:15}  {a.email:35}  "
                  f"{a.get('How should Finnbot see your holdings?').split(':')[0]}")
        return 0
    matches = [a for a in answers if args.who.lower() in (a.name.lower(), a.email.lower())]
    if not matches:
        print(f"No answer from {args.who!r}. Names in the sheet: {', '.join(sorted({a.name for a in answers})) or 'none'}.")
        return 1
    answer = matches[-1]
    if len(matches) > 1:
        print(f"{len(matches)} answers match {args.who!r}; using the newest (row {answer.row_number}).")
    result = apply_answer(answer, activate=not args.no_activate, overwrite=args.overwrite, dry_run=args.dry_run,
                          keys=args.enter_keys)
    _print_result(result, answer, args.dry_run)
    if result.activated and not args.dry_run:  # 2026-10-22: a user added by hand is announced like the sync's
        _notify_admins(*_activation_notice(result))
    if not args.dry_run and result.status == "applied":
        print("\nCheck everything with: venv/bin/python -m src.config.check_config")
    ok = result.status == "applied" and not _incomplete(result.rows, result.plan)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
