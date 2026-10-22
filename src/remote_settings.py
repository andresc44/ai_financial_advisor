# File to keep Finnbot's settings in the sign-up spreadsheet's "Parameters" tab, one row per setting, with Finnbot's status.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-23, at Andres's request.

Andres wanted to change settings without editing parameters.py. A tab of the sign-up spreadsheet
(SIGNUP_FORM_SHEET_ID; the tab SETTINGS_TAB_GID, else the one named "Parameters") lists every setting in
parameters.py, one per row:

    Parameter | Value | Default (parameters.py) | Finnbot status | What it does

Each cron digest run reads it first (sync_settings, before the sign-up sync) and:
- checks every Value that differs from its Default (type, allowed values, ranges). A valid one replaces parameters.py's
  value from that run on, in every Finnbot process (src/settings_overrides.py, data/remote_settings.json);
- a blank Value, or one still equal to the Default Finnbot last wrote, follows parameters.py, so a later change of the
  default there applies too (and the Value is updated to it);
- a Value that isn't valid isn't applied: its status says why, and the value accepted before (or the default) stays;
- writes back the Default, Finnbot status, and What it does columns (src/parameter_notes.py), and adds a row for each
  new setting. A row that isn't a setting is kept, marked as ignored;
- shows ACTIVE_USERS read-only: the sign-up sync sets it from the form's answers (src/import_signup.py);
- tells the admins on Telegram what changed or was rejected.
If the tab can't be read, the values accepted last time stay in force. Values are written the way Python writes them:
True / False, None, numbers, text without quotes, and lists such as ['Technology', 'Communication Services'].

By hand: python -m src.remote_settings --sync (read the tab, apply, write the status) or --show (the values in force).
"""
import argparse
import ast
import copy
import hashlib
import html
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src import settings_overrides as store
from src.parameter_notes import note_for
from src.parameters import params_dict

load_dotenv(project_root / ".env")

SHEET_ID_KEY = "SIGNUP_FORM_SHEET_ID"
TAB_GID_KEY = "SETTINGS_TAB_GID"
TAB_TITLE = "Parameters"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]  # Read and write: Finnbot writes the status back
API = "https://sheets.googleapis.com/v4/spreadsheets"
HEADER = ["Parameter", "Value", "Default (parameters.py)", "Finnbot status", "What it does"]
STATUS_HEADER = "Finnbot status"
MARKET_TZ = ZoneInfo("America/New_York")
NOTICE_COOLDOWN_HOURS = 24 * 365  # Each notice is keyed by its changes, so it's sent once
DEFAULT_STATUS = "Default"
READ_ONLY_STATUS = "🔒 Set by the sign-up form: change the form's answers instead"

# .env keys that win over a setting on the machine that has them (src/llm_models.ENV_OVERRIDES)
ENV_WINS = {("LLM", "UNIVERSAL", "model"): "UNIVERSAL_LLM_MODEL", ("LLM", "UNIVERSAL", "effort"): "UNIVERSAL_LLM_EFFORT"}
# Read once when a Finnbot process starts, so a change applies from the next run rather than the one that read it
NEXT_RUN_ONLY = {("FINNHUB_CALLS_PER_MINUTE",), ("WATCHDOG", "TIMEZONE"), ("WATCHDOG", "ALERT_COOLDOWN_HOURS"),
                 ("WATCHDOG", "ALERT_LOG_RETENTION_DAYS"), ("OPS", "LOG_MAX_MB"), ("OPS", "LOG_BACKUPS")}

# ---------------------------------------------------------------------------- Checks on a value
SIGNALS = ("buy_more", "hold", "risky", "profit_sell", "urgent_sell")
SECTOR_RULES = ("all_different", "majority_different", "any")
CHOICES = {
    ("TECHNICAL_INDICATOR_SOURCE",): ("yahoo", "twelvedata"),
    ("GEMINI_CONFIG", "effort"): ("minimal", "low", "medium", "high", "default"),
    ("CLAUDE_CONFIG", "effort"): ("low", "medium", "high", "xhigh", "max"),
    ("DEEPSEEK_CONFIG", "effort"): ("none", "low", "high", "max"),
    ("PICK_SECTOR_RULES", "LONG_TERM"): SECTOR_RULES,
    ("PICK_SECTOR_RULES", "SWING"): SECTOR_RULES,
    ("MARKET_SIGNALS", "RELATIVE_WINDOW"): ("1D", "5D", "1M"),
    ("WATCHDOG", "SESSION"): ("regular", "extended", "24_7"),
    ("DIGEST", "SYMBOL_NEWS_MIN_PRIORITY"): ("HIGH", "MEDIUM", "LOW"),
}
LIST_CHOICES = {
    ("DIGEST", "OPEN_SECTIONS"): ("risk", "markets", "holdings", "swing", "long_term"),
    ("DIGEST", "NEWS_FULL_SIGNALS"): SIGNALS,
    ("HOLDINGS_RECHECK", "SIGNALS"): SIGNALS,
    ("WATCHDOG", "ALERT_RECOMMENDATIONS"): SIGNALS,
}
AT_LEAST_ONE = {
    ("FINNHUB_CALLS_PER_MINUTE",), ("YAHOO_FETCH_CONCURRENCY",), ("CLAUDE_CONFIG", "max_tokens"),
    ("CLAUDE_CONFIG", "max_concurrency"), ("DEEPSEEK_CONFIG", "max_concurrency"), ("DEEPSEEK_CONFIG", "timeout_seconds"),
    ("GEMINI_CONFIG", "max_output_tokens"), ("DIGEST", "SYMBOL_NEWS_BATCH_SIZE"), ("DIGEST", "SYMBOL_NEWS_CONCURRENCY"),
    ("DIGEST", "MAX_PARALLEL_USERS"), ("DIGEST", "PORTFOLIO_REVIEW_MAX_TOKENS"), ("WATCHDOG", "MAX_PARALLEL_USERS"),
    ("OPS", "LOG_MAX_MB"), ("SWING_SCREEN", "MAX_CANDIDATES"),
}
SCORES = {("MIN_LONG_SCORE",), ("MIN_SWING_SCORE",), ("LONG_TERM_NON_TECH_MIN_SCORE",)}
BOOLEANS = {"true": True, "false": False, "yes": True, "no": False, "on": True, "off": False}


def format_value(value: Any) -> str:
    """How a value is written in the sheet: text as it is, anything else as Python writes it."""
    return value if isinstance(value, str) else repr(value)


def _number(text: str) -> Optional[float]:
    try:
        number = float(text.replace(",", "").replace("_", ""))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "True/False"
    if isinstance(value, (int, float)):
        return "number"
    if value is None:
        return "None"
    if isinstance(value, str):
        return "text"
    if isinstance(value, (list, tuple)):
        return "list"
    return type(value).__name__


def _item_problem(item: Any, examples: list) -> Optional[str]:
    """None when `item` is shaped like one of the default list's items (same kind; same kinds in the same places)."""
    if isinstance(item, (list, tuple)):
        alike = [e for e in examples if isinstance(e, (list, tuple)) and len(e) == len(item)]
        if not any(all(_kind(a) == _kind(b) for a, b in zip(item, e)) for e in alike):
            return f"{format_value(item)} isn't shaped like {format_value(examples[0])}"
        return None
    if _kind(item) not in {_kind(e) for e in examples}:
        return f"{format_value(item)} should be like {format_value(examples[0])}"
    return None


def parse_value(text: str, default: Any) -> Any:
    """`text` as a value of the default's type. Raises ValueError with the reason the status shows."""
    text = text.strip()
    if isinstance(default, bool):
        if text.lower() in BOOLEANS:
            return BOOLEANS[text.lower()]
        raise ValueError("must be True or False")
    if isinstance(default, int):
        number = _number(text)
        if number is None or number != int(number):
            raise ValueError("must be a whole number")
        return int(number)
    if isinstance(default, float):
        number = _number(text)
        if number is None:
            raise ValueError("must be a number")
        return float(number)
    if isinstance(default, str):
        if not text:
            raise ValueError("can't be empty")
        return text
    if default is None:
        if text.lower() in ("none", "null"):
            return None
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return text
    if isinstance(default, (list, tuple, dict)):
        try:
            value = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            raise ValueError(f"must be written like {format_value(default)[:80]}") from None
        if isinstance(default, tuple) and isinstance(value, list):
            value = tuple(value)
        if isinstance(default, list) and isinstance(value, tuple):
            value = list(value)
        if type(value) is not type(default):
            raise ValueError(f"must be a {_kind(default)} like {format_value(default)[:80]}")
        if isinstance(default, tuple):
            problem = _item_problem(value, [default])
        elif isinstance(default, list) and default:
            problem = next((p for p in (_item_problem(item, default) for item in value) if p), None)
        else:
            problem = None
        if problem:
            raise ValueError(problem)
        return value
    raise ValueError("can't be set from the sheet")


def check_value(path: tuple, value: Any, candidate: dict) -> Optional[str]:
    """Why `value` can't be used for `path`, or None. `candidate` is parameters.py with every value read from the sheet."""
    if path in CHOICES and value not in CHOICES[path]:
        return f"must be one of {', '.join(CHOICES[path])}"
    if path in LIST_CHOICES:
        wrong = [item for item in value if item not in LIST_CHOICES[path]]
        if wrong:
            return f"{wrong[0]!r} isn't one of {', '.join(LIST_CHOICES[path])}"
    allowed = {**candidate.get("ALLOWED_CLAUDE_MODELS", {}), **candidate.get("ALLOWED_DEEPSEEK_MODELS", {})}
    if path[0] == "LLM" and path[-1] == "model" and value not in allowed:
        return "must be a model in ALLOWED_CLAUDE_MODELS or ALLOWED_DEEPSEEK_MODELS"
    if path[0] == "LLM" and path[-1] == "effort":
        model = store.get_path(candidate, path[:-1] + ("model",))
        if value not in allowed.get(model, []):
            return f"{model} accepts {', '.join(allowed.get(model, [])) or 'no effort here'}"
    if path[0] == "GEMINI_MODELS" and not str(value).startswith("gemini-"):
        return "must be a Gemini model, e.g. gemini-3.6-flash"
    if path == ("DIGEST", "PRE_MARKET_TIME_ET") and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", value):
        return "must be a time like 07:00"
    if path == ("WATCHDOG", "TIMEZONE"):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            return "must be a timezone name like America/New_York"
    if path == ("HOLDINGS", "DEFAULT_HOME_CURRENCY") and value not in candidate["HOLDINGS"]["HOME_CURRENCIES"]:
        return "must be one of HOLDINGS.HOME_CURRENCIES"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        default = store.get_path(store.defaults(), path)
        if path in AT_LEAST_ONE and value < 1:
            return "must be at least 1"
        if isinstance(default, (int, float)) and default >= 0 and value < 0:
            return "can't be negative"
        if path in SCORES and value > 100:
            return "is a score out of 100"
    return None


# ---------------------------------------------------------------------------- Planning the sheet
@dataclass
class SheetRow:
    number: int  # In the sheet, counting the header as row 1
    name: str
    value: str
    written_default: str  # The Default column as Finnbot last wrote it


@dataclass
class SyncPlan:
    overrides: dict[tuple, Any] = field(default_factory=dict)  # Values in force that differ from parameters.py
    applied_at: dict[tuple, str] = field(default_factory=dict)
    rejected: dict[str, dict] = field(default_factory=dict)    # name -> {"value", "reason"}
    table: list[list[str]] = field(default_factory=list)       # Rows under the header: Parameter .. What it does
    changes: list[str] = field(default_factory=list)           # For the admins' notice
    new_rejections: list[str] = field(default_factory=list)


def _name(path: tuple) -> str:
    return ".".join(path)


def _short(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _sheet_rows(values: list[list[str]]) -> tuple[dict[str, SheetRow], list[SheetRow]]:
    """({name: its first row}, every other row with a name: duplicates and unknown names are kept for the table)."""
    rows, extra = {}, []
    for number, cells in enumerate(values[1:], start=2):
        cells = list(cells) + [""] * (len(HEADER) - len(cells))
        row = SheetRow(number, cells[0].strip(), cells[1], cells[2])
        if not row.name:
            continue
        if row.name in rows:
            extra.append(row)
        else:
            rows[row.name] = row
    return rows, extra


def plan_sync(values: list[list[str]], previous: dict, now: datetime) -> SyncPlan:
    """
    What the sheet's rows (`values`, header included) mean: the accepted values, each row's status, and the table to
    write back. `previous` is data/remote_settings.json as the last sync left it.
    """
    plan = SyncPlan()
    now_text = f"{now.astimezone(MARKET_TZ):%Y-%m-%d %H:%M} ET"
    defaults = store.defaults()
    before = {}
    for entry in previous.get("overrides", []):
        try:
            before[tuple(entry["path"])] = (ast.literal_eval(entry["value"]), entry.get("applied_at") or now_text)
        except (ValueError, SyntaxError, KeyError, TypeError):
            continue
    rows, extra = _sheet_rows(values)
    paths = store.leaf_paths(defaults)
    known = {_name(path) for path in paths}

    shown: dict[tuple, str] = {}    # The Value column to write back
    status: dict[tuple, str] = {}
    wanted: dict[tuple, Any] = {}   # Values that differ from the default, before the checks
    problems: dict[tuple, str] = {}
    for path in paths:
        name, default = _name(path), store.get_path(defaults, path)
        row = rows.get(name)
        if path in store.READ_ONLY:
            shown[path], status[path] = format_value(store.get_path(params_dict, path)), READ_ONLY_STATUS
            continue
        text = row.value.strip() if row else ""
        if not text:
            shown[path], status[path] = format_value(default), DEFAULT_STATUS
            continue
        shown[path] = row.value
        try:
            value = parse_value(text, default)
        except ValueError as e:
            problems[path] = str(e)
            continue
        try:
            written = parse_value(row.written_default, default) if row.written_default.strip() else default
        except ValueError:
            written = default
        if value == default or value == written:  # Unchanged: follows parameters.py, even when its default changed
            shown[path], status[path] = format_value(default), DEFAULT_STATUS
        else:
            wanted[path] = value

    candidate = copy.deepcopy(defaults)
    for path, value in wanted.items():
        store.get_path(candidate, path[:-1])[path[-1]] = value
    for path, value in wanted.items():
        problem = check_value(path, value, candidate)
        if problem:
            problems[path] = problem
        else:
            plan.overrides[path] = value
            plan.applied_at[path] = before[path][1] if path in before and before[path][0] == value else now_text

    for path, problem in problems.items():
        name, default = _name(path), store.get_path(defaults, path)
        kept = before.get(path)
        if kept is not None and kept[0] != default and check_value(path, kept[0], candidate) is None:
            plan.overrides[path], plan.applied_at[path] = kept
            fallback = f"Still using {_short(format_value(kept[0]), 60)}, accepted {kept[1]}."
        else:
            fallback = f"Using the default, {_short(format_value(default), 60)}."
        status[path] = f"❌ Not applied: {problem}. {fallback}"
        plan.rejected[name] = {"value": shown[path], "reason": problem}
        if previous.get("rejected", {}).get(name) != plan.rejected[name]:
            plan.new_rejections.append(f"{name} = {_short(shown[path].strip(), 60)}: {problem}")

    for path in plan.overrides:
        if path in problems:
            continue
        text = f"✅ Applied since {plan.applied_at[path]}"
        env_key, value = ENV_WINS.get(path), plan.overrides[path]
        env_value = (os.environ.get(env_key) or "").strip() if env_key else ""
        if env_value and env_value.lower() != str(value).lower():
            text += f", but this computer's .env sets {env_key}={env_value}, which wins here"
        if path in NEXT_RUN_ONLY and plan.applied_at[path] == now_text:
            text += " (takes effect from the next run)"
        status[path] = text

    for path in paths:
        if path in store.READ_ONLY:
            continue
        old = before[path][0] if path in before else store.get_path(defaults, path)
        new = plan.overrides.get(path, store.get_path(defaults, path))
        if old != new:
            back = " (the default)" if path not in plan.overrides else ""
            plan.changes.append(f"{_name(path)}: {_short(format_value(old), 50)} → {_short(format_value(new), 50)}{back}")

    plan.table = [[_name(path), shown[path], format_value(store.get_path(defaults, path)), status[path],
                   note_for(_name(path))] for path in paths]
    for row in extra + [row for name, row in rows.items() if name not in known]:
        if row.name in known:
            note = f"Repeats row {rows[row.name].number}, so it's ignored: change that row instead"
        else:
            note = "❓ Not a setting in parameters.py (maybe renamed or removed), so it's ignored"
        plan.table.append([row.name, row.value, "", note, ""])
    return plan


# ---------------------------------------------------------------------------- The sheet
class SettingsSheet:
    """The settings tab, read and written as the service account (GOOGLE_APPLICATION_CREDENTIALS)."""

    def __init__(self, sheet_id: str, tab_gid: Optional[int] = None):
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not creds_path or not os.path.exists(creds_path):
            raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS must point to the service account's JSON file.")
        creds = service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        creds.refresh(Request())
        self.headers = {"Authorization": f"Bearer {creds.token}"}
        self.sheet_id = sheet_id
        meta = self._call("get", "", params={"fields": "sheets.properties"})
        tabs = [sheet["properties"] for sheet in meta.get("sheets", [])]
        tab = next((t for t in tabs if tab_gid is not None and t["sheetId"] == tab_gid), None)
        tab = tab or next((t for t in tabs if t["title"].strip().lower() == TAB_TITLE.lower()), None)
        if tab is None:
            raise RuntimeError(f"The sign-up spreadsheet has no tab {tab_gid or TAB_TITLE!r} for the settings.")
        self.tab = tab

    def _call(self, method: str, suffix: str, **kwargs) -> dict:
        response = requests.request(method, f"{API}/{self.sheet_id}{suffix}", headers=self.headers, timeout=60, **kwargs)
        if not response.ok:
            hint = " (share the spreadsheet with the service account as an Editor)" if response.status_code == 403 else ""
            raise RuntimeError(f"Sheets API {response.status_code}{hint}: {response.text[:300]}")
        return response.json() if response.content else {}

    def _range(self, cells: str) -> str:
        return f"'{self.tab['title']}'!{cells}"

    def read(self) -> list[list[str]]:
        rows = self.tab["gridProperties"]["rowCount"]
        data = self._call("get", f"/values/{quote(self._range(f'A1:E{rows}'))}",
                          params={"valueRenderOption": "FORMATTED_VALUE"})
        return data.get("values", [])

    def write(self, ranges: dict[str, list[list[str]]]) -> None:
        """Writes each range ("A1:E3": rows) as plain text (RAW: nothing is turned into a number, date, or formula)."""
        self._call("post", "/values:batchUpdate", json={"valueInputOption": "RAW", "data": [
            {"range": self._range(cells), "values": rows} for cells, rows in ranges.items()]})

    def clear(self, cells: str) -> None:
        self._call("post", f"/values/{quote(self._range(cells))}:clear", json={})

    def set_up(self, rows_needed: int) -> None:
        """Layout, done once (when the header isn't there): widths, wrapping, plain-text values, a frozen header."""
        gid = self.tab["sheetId"]
        width = {"A": 330, "B": 260, "C": 220, "D": 330, "E": 560}
        requests_ = [
            {"updateSheetProperties": {"properties": {"sheetId": gid, "gridProperties": {"frozenRowCount": 1}},
                                       "fields": "gridProperties.frozenRowCount"}},
            {"repeatCell": {"range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                                           "backgroundColor": {"red": 0.9, "green": 0.92, "blue": 0.95}}},
                            "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
            # Plain text, so 07:00 stays a time of day and 1.50 stays as typed instead of becoming a date or number
            {"repeatCell": {"range": {"sheetId": gid, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 5},
                            "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT"}, "wrapStrategy": "WRAP",
                                                           "verticalAlignment": "TOP"}},
                            "fields": "userEnteredFormat(numberFormat,wrapStrategy,verticalAlignment)"}},
            {"addConditionalFormatRule": {"index": 0, "rule": {
                "ranges": [{"sheetId": gid, "startRowIndex": 1, "startColumnIndex": 3, "endColumnIndex": 4}],
                "booleanRule": {"condition": {"type": "TEXT_STARTS_WITH", "values": [{"userEnteredValue": "❌"}]},
                                "format": {"backgroundColor": {"red": 0.98, "green": 0.85, "blue": 0.85}}}}}},
            {"addConditionalFormatRule": {"index": 0, "rule": {
                "ranges": [{"sheetId": gid, "startRowIndex": 1, "startColumnIndex": 3, "endColumnIndex": 4}],
                "booleanRule": {"condition": {"type": "TEXT_STARTS_WITH", "values": [{"userEnteredValue": "✅"}]},
                                "format": {"backgroundColor": {"red": 0.85, "green": 0.95, "blue": 0.86}}}}}},
            # A warning (not a lock) when editing the columns Finnbot writes
            {"addProtectedRange": {"protectedRange": {
                "range": {"sheetId": gid, "startColumnIndex": 2, "endColumnIndex": 5},
                "description": "Finnbot writes these columns on every run", "warningOnly": True}}},
            {"updateCells": {"range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0,
                                       "endColumnIndex": 1},
                             "rows": [{"values": [{"note": SHEET_NOTE}]}], "fields": "note"}},
        ]
        for index, column in enumerate("ABCDE"):
            requests_.append({"updateDimensionProperties": {
                "range": {"sheetId": gid, "dimension": "COLUMNS", "startIndex": index, "endIndex": index + 1},
                "properties": {"pixelSize": width[column]}, "fields": "pixelSize"}})
        missing_rows = rows_needed - self.tab["gridProperties"]["rowCount"]
        if missing_rows > 0:
            requests_.append({"appendDimension": {"sheetId": gid, "dimension": "ROWS", "length": missing_rows + 50}})
        self._call("post", ":batchUpdate", json={"requests": requests_})


SHEET_NOTE = (
    "Finnbot's settings (parameters.py). Change a Value to override its default; it's checked and applied from the next "
    "digest run, and the Finnbot status column says how it went. Clear a Value (or set it back to the Default) to use "
    "parameters.py's value again. Write values the way Python does: True / False, None, numbers, text without quotes, "
    "and lists like ['A', 'B']. Finnbot rewrites the Default, status, and What it does columns."
)


# ---------------------------------------------------------------------------- The sync
def _load_previous() -> dict:
    try:
        return json.loads(store.OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(plan: SyncPlan, now: datetime) -> None:
    saved = {
        "read_at": now.isoformat(timespec="seconds"),
        "overrides": [{"name": _name(path), "path": list(path), "value": repr(value),
                       "applied_at": plan.applied_at[path]} for path, value in plan.overrides.items()],
        "rejected": plan.rejected,
    }
    store.OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.OVERRIDES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, store.OVERRIDES_PATH)


def _write_back(sheet: Any, values: list[list[str]], plan: SyncPlan, now: datetime) -> None:
    """Writes the table when anything in it changed, and always the time the tab was read (in the status header)."""
    header = HEADER[:3] + [f"{STATUS_HEADER} (read {now.astimezone(MARKET_TZ):%Y-%m-%d %H:%M} ET)", HEADER[4]]
    current = [list(row) + [""] * (len(HEADER) - len(row)) for row in values[1:]]
    while current and not any(cell.strip() for cell in current[-1]):
        current.pop()
    if values and values[0][:3] == HEADER[:3] and [row[:len(HEADER)] for row in current] == plan.table:
        sheet.write({"D1": [[header[3]]]})
        return
    sheet.write({f"A1:E{len(plan.table) + 1}": [header] + plan.table})
    if len(current) > len(plan.table):
        sheet.clear(f"A{len(plan.table) + 2}:E{len(current) + 1}")


def _notify(plan: SyncPlan) -> None:
    if not plan.changes and not plan.new_rejections:
        return
    from src.admins import admins
    from src.telegram_alert.notices import send_notice_now

    body = "⚙️ <b>Finnbot settings changed in the sheet</b>"
    if plan.changes:
        body += "\n\nNow in force:\n" + "\n".join(f"• {html.escape(line)}" for line in plan.changes)
    if plan.new_rejections:
        body += "\n\nNot applied:\n" + "\n".join(f"• {html.escape(line)}" for line in plan.new_rejections)
    reason = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    for admin in admins():
        try:
            send_notice_now(admin, "settings", reason, body, cooldown_hours=NOTICE_COOLDOWN_HOURS)
        except Exception as e:  # A notice must never stop the digest
            print(f"[settings] Couldn't tell {admin}: {e}")


def sync_settings(sheet: Any = None, now: Optional[datetime] = None) -> Optional[SyncPlan]:
    """
    Reads the settings tab, applies the accepted values to this run (and saves them for every later process), writes
    the status back, and tells the admins what changed. Never raises: if the tab can't be read or written, the values
    accepted last time stay in force and the admins are alerted. Returns the plan, or None when nothing was read.
    """
    now = now or datetime.now(timezone.utc)
    try:
        if sheet is None:
            sheet_id = os.environ.get(SHEET_ID_KEY, "").strip()
            if not sheet_id:
                return None
            gid = os.environ.get(TAB_GID_KEY, "").strip()
            sheet = SettingsSheet(sheet_id, int(gid) if gid.isdigit() else None)
        values = sheet.read()
        plan = plan_sync(values, _load_previous(), now)
        if not values or [cell.strip() for cell in values[0][:3]] != HEADER[:3]:
            sheet.set_up(len(plan.table) + 1)
        _save(plan, now)
        store.apply_overrides(params_dict, plan.overrides)
        _write_back(sheet, values, plan, now)
    except Exception as e:
        from src.telegram_alert.notices import alert_admins

        print(f"[settings] The settings sheet wasn't read or written ({e}); the values accepted last time stay in force.")
        alert_admins("settings sheet", f"The Parameters tab couldn't be read or updated: {str(e)[:300]}")
        return None
    print(f"[settings] {len(plan.overrides)} setting(s) from the sheet in force"
          + (f", {len(plan.rejected)} not applied" if plan.rejected else "") + ".")
    _notify(plan)
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Finnbot's settings in the sign-up spreadsheet's Parameters tab.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sync", action="store_true", help="Read the tab, apply what's valid, and write the status back.")
    group.add_argument("--show", action="store_true", help="List the settings from the sheet in force on this computer.")
    args = parser.parse_args()
    if args.sync:
        plan = sync_settings()
        if plan is None:
            sys.exit(1)
        for line in plan.changes + plan.new_rejections:
            print(f"  {line}")
        return
    for path, value in store.load_overrides().items():
        print(f"{_name(path)} = {format_value(value)}")


if __name__ == "__main__":
    main()
