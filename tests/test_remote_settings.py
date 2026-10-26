"""
The settings sheet (2026-10-23): every setting in parameters.py as a row of the sign-up spreadsheet's Parameters tab,
with Finnbot's status. Written by Claude (Claude Opus 5.5 via Claude Code), at Andres's request.
"""
import html
import json
import re
from datetime import datetime, timezone

import pytest

import src.config.remote_settings as rs
import src.config.settings_overrides as store
from src.config.parameter_notes import own_note
from src.config.parameters import params_dict

NOW = datetime(2026, 10, 23, 20, 10, tzinfo=timezone.utc)  # 16:10 ET
LATER = datetime(2026, 10, 24, 11, 10, tzinfo=timezone.utc)


class FakeSheet:
    """The settings tab in memory: read, write ranges like "A1:E3" or "D1", and clear."""

    def __init__(self, values=None, fail=False):
        self.values = [list(row) for row in values or []]
        self.set_up_calls = 0
        self.fail = fail

    def read(self):
        if self.fail:
            raise RuntimeError("Sheets API 403")
        return [list(row) for row in self.values]

    def set_up(self, rows_needed):
        self.set_up_calls += 1

    def write(self, ranges):
        for cells, rows in ranges.items():
            match = re.match(r"([A-E])(\d+)", cells)
            column, first = "ABCDE".index(match[1]), int(match[2]) - 1
            for offset, row in enumerate(rows):
                while len(self.values) <= first + offset:
                    self.values.append([])
                target = self.values[first + offset]
                target += [""] * (5 - len(target))
                target[column:column + len(row)] = row

    def clear(self, cells):
        first, last = (int(n) for n in re.findall(r"\d+", cells))
        for index in range(first - 1, min(last, len(self.values))):
            self.values[index] = [""] * 5

    def row(self, name):
        return next(row for row in self.values if row and row[0] == name)

    def edit(self, name, value):
        self.row(name)[1] = value


@pytest.fixture
def sheet(monkeypatch, telegram):
    """A tab Finnbot has already filled in, and params_dict back to parameters.py's values afterwards."""
    monkeypatch.setitem(params_dict["OPS"], "ALERT_USERS", ["Andres"])
    seeded = FakeSheet()
    rs.sync_settings(seeded, now=NOW)
    yield seeded
    store.apply_overrides(params_dict, {})


def test_an_empty_tab_gets_every_setting_with_its_default_and_a_note(sheet):
    assert sheet.set_up_calls == 1
    header, rows = sheet.values[0], sheet.values[1:]
    assert header[:3] == rs.HEADER[:3] and header[3] == "Finnbot status (read 2026-10-23 16:10 ET)"
    names = [row[0] for row in rows]
    assert names == [".".join(path) for path in store.leaf_paths(store.defaults())]
    batch = sheet.row("DIGEST.SYMBOL_NEWS_BATCH_SIZE")
    assert batch[1] == batch[2] == "3" and batch[3] == "Default" and batch[4]
    assert sheet.row("ACTIVE_USERS")[3].startswith("🔒")
    assert json.loads(store.OVERRIDES_PATH.read_text(encoding="utf-8"))["overrides"] == []


def test_only_urgent_telegram_messages_say_so(monkeypatch):
    # 2026-10-23 (Andres): "Urgent alert for <user>" only on something urgent and time-sensitive, and every message
    # addressed to the user by name
    import src.telegram_alert.send_telegram as send_telegram

    sent = []
    monkeypatch.setitem(send_telegram.USER_PROFILES, "Andres", {"telegram_chat_id": "ANDRES_TELEGRAM_CHAT_ID"})
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("ANDRES_TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(send_telegram.requests, "post", lambda url, json, timeout: sent.append(json["text"]) or
                        type("Ok", (), {"raise_for_status": lambda self: None})())
    send_telegram.send_telegram_message("Andres", "Settings changed.")
    send_telegram.send_telegram_message("Andres", "SWK fell to its stop.", urgent=True)
    assert sent == ["Hi Andres,\n\nSettings changed.", "<b>\U0001f6a8 Urgent alert for Andres:</b>\n\nSWK fell to its stop."]


def test_every_setting_has_a_note_of_its_own_and_survives_the_round_trip():
    for path in store.leaf_paths(store.defaults()):
        name, default = ".".join(path), store.get_path(store.defaults(), path)
        assert own_note(name), f"{name} needs a note in src/config/parameter_notes.py"
        assert rs.parse_value(rs.format_value(default), default) == default


def test_a_changed_value_applies_to_this_run_and_to_every_later_process(sheet, telegram):
    digest = params_dict["DIGEST"]  # Modules keep references like this one
    sheet.edit("DIGEST.SYMBOL_NEWS_BATCH_SIZE", "4")
    sheet.edit("GEMINI_CONFIG.effort", "minimal")
    plan = rs.sync_settings(sheet, now=NOW)
    assert digest["SYMBOL_NEWS_BATCH_SIZE"] == 4 and params_dict["GEMINI_CONFIG"]["effort"] == "minimal"
    assert sheet.row("DIGEST.SYMBOL_NEWS_BATCH_SIZE")[3] == "✅ Applied since 2026-10-23 16:10 ET"
    assert sorted(plan.changes) == ["DIGEST.SYMBOL_NEWS_BATCH_SIZE: 3 → 4", "GEMINI_CONFIG.effort: low → minimal"]
    assert len(telegram.messages) == 1 and "SYMBOL_NEWS_BATCH_SIZE: 3 → 4" in telegram.messages[0][1]
    assert telegram.urgent == [False]  # 2026-10-23: not urgent, so no "Urgent alert" line
    # A new process: parameters.py's values, then the saved file
    fresh = json.loads(json.dumps(store.defaults()))
    fresh["DIGEST"]["SYMBOL_NEWS_BATCH_SIZE"] = 3
    store.apply_overrides(fresh, store.load_overrides())
    assert fresh["DIGEST"]["SYMBOL_NEWS_BATCH_SIZE"] == 4
    # The next read changes nothing, keeps the time it was applied, and sends no second notice
    rs.sync_settings(sheet, now=LATER)
    assert sheet.row("DIGEST.SYMBOL_NEWS_BATCH_SIZE")[3] == "✅ Applied since 2026-10-23 16:10 ET"
    assert len(telegram.messages) == 1


@pytest.mark.parametrize("name, value, reason", [
    ("DIGEST.SYMBOL_NEWS_BATCH_SIZE", "three", "must be a whole number"),
    ("DIGEST.SYMBOL_NEWS_BATCH_SIZE", "0", "must be at least 1"),
    ("MIN_SWING_SCORE", "120", "is a score out of 100"),
    ("GEMINI_CONFIG.effort", "lots", "must be one of minimal, low, medium, high, default"),
    ("DIGEST.OPEN_SECTIONS", "['risk', 'news']", "'news' isn't one of"),
    ("LLM.UNIVERSAL.model", "gpt-9", "must be a model in ALLOWED_CLAUDE_MODELS"),
    ("LLM.UNIVERSAL.effort", "xhigh", "deepseek-flash accepts none, low, high, max"),
    ("DIGEST.PRE_MARKET_TIME_ET", "7am", "must be a time like 07:00"),
    ("SWING_SCREEN.YAHOO_FILTERS", "[('forwardPE', '>')]", "isn't shaped like"),
    ("OPS.HOLIDAY_NOTICE", "maybe", "must be True or False"),
])
def test_a_value_that_isnt_valid_is_not_applied_and_says_why(sheet, telegram, name, value, reason):
    path = tuple(name.split("."))
    default = store.get_path(store.defaults(), path)
    sheet.edit(name, value)
    rs.sync_settings(sheet, now=NOW)
    status = sheet.row(name)[3]
    assert status.startswith("❌ Not applied:") and reason in status and "Using the default" in status
    assert store.get_path(params_dict, path) == default and sheet.row(name)[1] == value  # Left as typed
    assert reason in html.unescape(telegram.messages[-1][1])


def test_a_bad_edit_keeps_the_value_accepted_before(sheet):
    sheet.edit("MIN_SWING_SCORE", "70")
    rs.sync_settings(sheet, now=NOW)
    sheet.edit("MIN_SWING_SCORE", "seventy")
    rs.sync_settings(sheet, now=LATER)
    assert params_dict["MIN_SWING_SCORE"] == 70.0
    assert "Still using 70.0, accepted 2026-10-23 16:10 ET" in sheet.row("MIN_SWING_SCORE")[3]


def test_clearing_a_value_goes_back_to_the_default(sheet, telegram):
    sheet.edit("MIN_SWING_SCORE", "70")
    rs.sync_settings(sheet, now=NOW)
    sheet.edit("MIN_SWING_SCORE", "")
    plan = rs.sync_settings(sheet, now=LATER)
    assert params_dict["MIN_SWING_SCORE"] == 75.0 and sheet.row("MIN_SWING_SCORE")[1:4] == ["75.0", "75.0", "Default"]
    assert plan.changes == ["MIN_SWING_SCORE: 70.0 → 75.0 (the default)"]


def test_an_untouched_value_follows_a_new_default_in_parameters_py(sheet):
    # The sheet still shows the old default (5) in both columns: parameters.py's 3 is used and shown
    sheet.row("DIGEST.SYMBOL_NEWS_BATCH_SIZE")[1:3] = ["5", "5"]
    plan = rs.sync_settings(sheet, now=NOW)
    assert plan.overrides == {} and sheet.row("DIGEST.SYMBOL_NEWS_BATCH_SIZE")[1:3] == ["3", "3"]
    assert plan.changes == ["DIGEST.SYMBOL_NEWS_BATCH_SIZE: 5 → 3 (changed in parameters.py)"]


def test_a_change_in_parameters_py_replaces_the_sheets_value(sheet, telegram, monkeypatch):
    # 2026-10-23 (Andres): the newest change wins, even over a value set in the sheet
    sheet.edit("MIN_SWING_SCORE", "70")
    rs.sync_settings(sheet, now=NOW)
    assert params_dict["MIN_SWING_SCORE"] == 70.0
    monkeypatch.setattr(store, "_defaults", {**store.defaults(), "MIN_SWING_SCORE": 72.0,
                                             "DIGEST": {**store.defaults()["DIGEST"], "SYMBOL_NEWS_BATCH_SIZE": 4}})
    plan = rs.sync_settings(sheet, now=LATER)
    assert params_dict["MIN_SWING_SCORE"] == 72.0 and params_dict["DIGEST"]["SYMBOL_NEWS_BATCH_SIZE"] == 4
    assert sheet.row("MIN_SWING_SCORE")[1:3] == ["72.0", "72.0"]
    assert sheet.row("MIN_SWING_SCORE")[3].startswith("Default (changed in parameters.py")
    assert sorted(plan.changes) == ["DIGEST.SYMBOL_NEWS_BATCH_SIZE: 3 → 4 (changed in parameters.py)",
                                    "MIN_SWING_SCORE: 70.0 → 72.0 (changed in parameters.py)"]
    assert "changed in parameters.py" in telegram.messages[-1][1]
    # The next sync sees nothing new
    assert rs.sync_settings(sheet, now=LATER).changes == [] and sheet.row("MIN_SWING_SCORE")[3] == "Default"


def test_active_users_and_unknown_rows_are_left_alone(sheet):
    sheet.edit("ACTIVE_USERS", "['Mallory']")
    sheet.values.append(["OLD_SETTING", "1", "", "", ""])
    plan = rs.sync_settings(sheet, now=NOW)
    assert params_dict["ACTIVE_USERS"] != ["Mallory"] and plan.overrides == {}
    assert sheet.row("ACTIVE_USERS")[1] == repr(params_dict["ACTIVE_USERS"])
    assert sheet.row("OLD_SETTING")[3].startswith("❓ Not a setting")


def test_a_sheet_that_cant_be_read_keeps_the_values_accepted_before(sheet, telegram):
    sheet.edit("MIN_SWING_SCORE", "70")
    rs.sync_settings(sheet, now=NOW)
    assert rs.sync_settings(FakeSheet(fail=True), now=LATER) is None
    assert params_dict["MIN_SWING_SCORE"] == 70.0
    assert store.load_overrides() == {("MIN_SWING_SCORE",): 70.0}
    assert "settings sheet" in telegram.messages[-1][1]


def test_saved_values_apply_when_parameters_py_is_imported(tmp_path, monkeypatch):
    saved = tmp_path / "remote_settings.json"
    saved.write_text(json.dumps({"overrides": [
        {"path": ["MARKET_SIGNALS", "MOOD_MATRIX", "calm", "greed"], "value": repr(("Bold", "pos", "Text."))},
        {"path": ["NOT_A_SETTING"], "value": "1"}]}), encoding="utf-8")
    monkeypatch.setattr(store, "OVERRIDES_PATH", saved)
    monkeypatch.setattr(store, "_defaults", None)
    params = {"MARKET_SIGNALS": {"MOOD_MATRIX": {"calm": {"greed": ("Confident", "pos", "Old.")}}}}
    monkeypatch.delenv(store.IGNORE_ENV)
    store.apply_saved(params)
    assert params["MARKET_SIGNALS"]["MOOD_MATRIX"]["calm"]["greed"] == ("Bold", "pos", "Text.")  # Still a tuple
    assert "NOT_A_SETTING" not in params
    monkeypatch.setenv(store.IGNORE_ENV, "1")
    params = {"MARKET_SIGNALS": {"MOOD_MATRIX": {"calm": {"greed": ("Confident", "pos", "Old.")}}}}
    store.apply_saved(params)
    assert params["MARKET_SIGNALS"]["MOOD_MATRIX"]["calm"]["greed"][0] == "Confident"


def test_the_watchdog_defaults_come_from_env_then_the_tab_then_parameters_py(sheet, monkeypatch):
    # Claude-authored (2026-10-26, Andres): the form only asks whether a user wants the watchdog, so its interval,
    # session, and tripwire are the defaults on the tab (else parameters.py), unless the user's .env sets them
    from src.personalized import user_settings
    from src.personalized.user_profiles import USER_PROFILES, build_profile
    monkeypatch.setitem(USER_PROFILES, "Tester", build_profile("Tester"))
    for key in ("INTERVAL_HOURS", "SESSION", "TRIPWIRE"):
        monkeypatch.delenv(f"TESTER_WATCHDOG_{key}", raising=False)
    defaults = params_dict["WATCHDOG"]
    assert user_settings.get_watchdog_settings("Tester") == user_settings.WatchdogSettings(
        defaults["DEFAULT_INTERVAL_HOURS"], defaults["SESSION"], defaults["TRIPWIRE_DEFAULT"])

    sheet.edit("WATCHDOG.DEFAULT_INTERVAL_HOURS", "2")
    sheet.edit("WATCHDOG.SESSION", "24_7")
    sheet.edit("WATCHDOG.TRIPWIRE_DEFAULT", "False")
    rs.sync_settings(sheet, now=LATER)
    assert user_settings.get_watchdog_settings("Tester") == user_settings.WatchdogSettings(2, "24_7", False)

    monkeypatch.setenv("TESTER_WATCHDOG_INTERVAL_HOURS", "6")
    monkeypatch.setenv("TESTER_WATCHDOG_SESSION", "regular")
    assert user_settings.get_watchdog_settings("Tester") == user_settings.WatchdogSettings(6, "regular", False)
