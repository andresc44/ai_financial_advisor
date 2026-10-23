# File to lay the settings accepted from the settings sheet over parameters.py's defaults, in every Finnbot process.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-23, at Andres's request.

parameters.py holds the defaults. Each cron digest run reads the "Parameters" tab of the sign-up spreadsheet
(src/config/remote_settings.py), checks every changed value, and keeps the accepted ones in data/remote_settings.json. This
module applies that file:
- at the end of parameters.py, so every process (digest, watchdog, check_config) starts with the sheet's values;
- again in the digest run that read the sheet, in place, so the change applies to that run's digest too. Values are
  set inside the existing dicts, so modules holding params_dict["DIGEST"] and the like see them.

It imports nothing from Finnbot, because parameters.py imports it. The file stores each value as a Python literal
(repr), read back with ast.literal_eval, so tuples stay tuples. FINNBOT_IGNORE_SETTINGS_SHEET=1 skips the file
(the tests set it, so they never depend on a machine's sheet values).
"""
import ast
import copy
import json
import os
from pathlib import Path
from typing import Any, Optional

project_root = Path(__file__).resolve().parents[2]
OVERRIDES_PATH = project_root / "data" / "remote_settings.json"
IGNORE_ENV = "FINNBOT_IGNORE_SETTINGS_SHEET"
# Set elsewhere and never from the settings sheet: the sign-up sync owns ACTIVE_USERS (src/users/import_signup.py)
READ_ONLY = {("ACTIVE_USERS",)}

_defaults: Optional[dict] = None  # parameters.py's own values, kept before anything is laid over them


def leaf_paths(params: dict, prefix: tuple = ()) -> list[tuple]:
    """Every setting's path, in parameters.py's order. Dicts with text keys are opened; anything else is one setting."""
    paths = []
    for key, value in params.items():
        path = prefix + (key,)
        if isinstance(value, dict) and value and all(isinstance(k, str) for k in value):
            paths += leaf_paths(value, path)
        else:
            paths.append(path)
    return paths


def get_path(params: dict, path: tuple) -> Any:
    node = params
    for key in path:
        node = node[key]
    return node


def _set_path(params: dict, path: tuple, value: Any) -> None:
    """Sets an existing setting inside its own dict (never adds a key)."""
    try:
        parent = get_path(params, path[:-1])
    except (KeyError, TypeError):  # Shaped differently here (e.g. replaced in a test): left alone
        return
    if isinstance(parent, dict) and path[-1] in parent and parent[path[-1]] != value:
        parent[path[-1]] = copy.deepcopy(value)


def defaults() -> dict:
    """parameters.py's values, before any sheet value."""
    if _defaults is None:
        raise RuntimeError("parameters.py hasn't been imported yet.")
    return _defaults


def apply_overrides(params: dict, overrides: dict[tuple, Any]) -> None:
    """Every setting becomes its override, or goes back to parameters.py's value; read-only ones are left alone."""
    for path in leaf_paths(defaults()):
        if path not in READ_ONLY:
            _set_path(params, path, overrides[path] if path in overrides else get_path(defaults(), path))


def load_overrides(path: Optional[Path] = None) -> dict[tuple, Any]:
    """{path: value} from data/remote_settings.json; {} when there is no file."""
    file = path or OVERRIDES_PATH
    if not file.exists():
        return {}
    saved = json.loads(file.read_text(encoding="utf-8"))
    return {tuple(entry["path"]): ast.literal_eval(entry["value"]) for entry in saved.get("overrides", [])}


def apply_saved(params: dict) -> None:
    """Called at the end of parameters.py: keeps its defaults, then applies the sheet's accepted values."""
    global _defaults
    _defaults = copy.deepcopy(params)
    if os.environ.get(IGNORE_ENV, "").strip().lower() in ("1", "true", "yes"):
        return
    try:
        overrides = load_overrides()
    except (OSError, ValueError, SyntaxError, KeyError, TypeError) as e:
        print(f"[WARNING] Couldn't read {OVERRIDES_PATH.name} ({e}); using parameters.py's values.")
        return
    known = set(leaf_paths(params))
    apply_overrides(params, {path: value for path, value in overrides.items() if path in known})
