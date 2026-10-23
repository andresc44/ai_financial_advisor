"""Watchdog bookkeeping and notices, the shared alert log, failure alerts, holdings source problems, and run logs."""
import copy
import logging
import sys
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

import src.ops.run_logging as run_logging
import src.telegram_alert.hourly_watchdog as wd
import src.telegram_alert.notices as notices
from src.config.parameters import params_dict
from src.personalized.api_errors import ApiKeyUnusable
from src.personalized.holdings_state import get_user_state
from src.personalized.pull_holdings_data import PortfolioFetcher

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)
EMPTY_LOG = pd.DataFrame(columns=["user", "alerts", "timestamp"])


@pytest.fixture
def watchdog_env(monkeypatch):
    """In-memory watchdog state and a fake fetcher whose behaviour each test sets."""
    state = {"users": {}}
    monkeypatch.setattr(wd, "load_state", lambda *a, **k: copy.deepcopy(state))
    monkeypatch.setattr(wd, "update_user_state", lambda user, updater, *a, **k: updater(get_user_state(state, user)))
    monkeypatch.setattr(wd, "get_watchdog_settings", lambda user: SimpleNamespace(interval_hours=4, session="24_7", tripwire=False))
    monkeypatch.setattr(wd, "is_in_session", lambda now, session: True)
    monkeypatch.setattr(wd, "block_id", lambda now, hours, session: "BLOCK-1")
    monkeypatch.setattr(wd, "symbols_with_news_since", lambda states, symbols: {})
    behaviour = {"resolve_error": None, "key_error": None, "evaluated": set(), "payload_calls": 0, "issues": [], "empty": False}

    class FakeFetcher:
        def __init__(self, user_name):
            self.holdings_issues = list(behaviour["issues"])
            self.full_data = pd.DataFrame() if behaviour["empty"] else pd.DataFrame(
                {"symbol": ["AAA", "BBB", "CCC"], "current_price": [10.0, 20.0, 30.0], "natr": [2.0] * 3})
            self.llm_key_error = None
            self.llm_settings = {"provider": "Anthropic", "model": "claude-sonnet-5-5", "effort": "medium"}

        def add_session_prices(self, df, phase):
            return df

        def get_holdings_digest_payload(self, desired_columns=None, symbols=None):
            behaviour["payload_calls"] += 1
            self.llm_key_error = behaviour["key_error"]
            return {"holdings": [{"symbol": s, "recommendation": "hold" if s in behaviour["evaluated"] else None} for s in symbols]}

    def resolve(user, provider):
        if behaviour["resolve_error"]:
            raise behaviour["resolve_error"]
        return "k"

    monkeypatch.setattr(wd, "PortfolioFetcher", FakeFetcher)
    monkeypatch.setattr(wd, "resolve_llm_key", resolve)
    return SimpleNamespace(state=state, behaviour=behaviour)


def user_state(env):
    return env.state["users"]["Andres"]


def test_missing_key_skips_spend_and_notifies_once(watchdog_env, telegram):
    watchdog_env.behaviour["resolve_error"] = ApiKeyUnusable("Anthropic", "missing", "Andres")
    logs = wd.check_user("Andres", NOW, EMPTY_LOG)
    assert watchdog_env.behaviour["payload_calls"] == 0
    assert user_state(watchdog_env)["claude_calls"]["count"] == 0 and user_state(watchdog_env)["last_block"] is None
    assert len(telegram.messages) == 1 and "not set" in telegram.messages[0][1]

    wd.check_user("Andres", NOW, logs)  # Same problem within the cooldown
    assert len(telegram.messages) == 1


def test_credits_running_out_counts_only_real_evaluations(watchdog_env):
    watchdog_env.behaviour.update(key_error=ApiKeyUnusable("Anthropic", "no_credits", "Andres"), evaluated={"AAA"})
    wd.check_user("Andres", NOW, EMPTY_LOG)
    assert user_state(watchdog_env)["claude_calls"]["count"] == 1 and user_state(watchdog_env)["last_block"] is None


def test_healthy_key_marks_the_check_done(watchdog_env, telegram):
    watchdog_env.behaviour["evaluated"] = {"AAA", "BBB", "CCC"}
    wd.check_user("Andres", NOW, EMPTY_LOG)
    assert user_state(watchdog_env)["claude_calls"]["count"] == 3 and user_state(watchdog_env)["last_block"] == "BLOCK-1"
    assert telegram.messages == []


def test_holdings_problem_is_sent_not_mistaken_for_empty(watchdog_env, telegram):
    issue = {"source": "SnapTrade", "reason": "connection_disabled", "message": "Your Questrade connection has been disabled."}
    watchdog_env.behaviour.update(issues=[issue], empty=True)
    logs = wd.check_user("Andres", NOW, EMPTY_LOG)
    assert len(telegram.messages) == 1 and "Questrade connection has been disabled" in telegram.messages[0][1]
    wd.check_user("Andres", NOW, logs)
    assert len(telegram.messages) == 1  # Cooldown

    telegram.messages.clear()
    wd.check_user("Andres", NOW, EMPTY_LOG, dry_run=True)
    assert telegram.messages == []  # Dry runs send nothing


def test_users_run_in_parallel_and_failures_alert_admins(monkeypatch, telegram):
    notices.CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"user": "old", "alerts": "k", "timestamp": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}]).to_csv(
        notices.CSV_PATH, index=False)
    monkeypatch.setattr(wd, "get_active_users", lambda: ["U0", "U1", "U2", "U3"])
    monkeypatch.setattr(wd, "is_user_eligible", lambda user: True)
    running = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fake_check(user, now, alert_logs, cooldown_hours=24, dry_run=False):
        with lock:
            running["now"] += 1
            running["max"] = max(running["max"], running["now"])
        time.sleep(0.2)
        with lock:
            running["now"] -= 1
        if user == "U2":
            raise RuntimeError("boom")
        row = pd.DataFrame([{"user": user, "alerts": f"{user}-alert", "timestamp": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}])
        return pd.concat([alert_logs, row], ignore_index=True)

    monkeypatch.setattr(wd, "check_user", fake_check)
    ok, status = wd.process_user_alerts(now=NOW)

    assert not ok and status.startswith("Checked 4 user(s), 1 failed")  # Heartbeat: exit 1, no names
    assert running["max"] > 1  # Users overlapped
    log = pd.read_csv(notices.CSV_PATH)
    assert {"old", "U0", "U1", "U3"} <= set(log["user"])
    assert any(user == "Andres" and "watchdog check failed for U2" in body for user, body in telegram.messages)
    assert "Andres" in set(log["user"])  # The admin alert written mid-run survived the end-of-run save


# --------------------------------------------------------------------------------------------- notices
def test_alert_admins_cooldown_and_send_failures(telegram):
    notices.alert_admins("digest failed for Steven", "RuntimeError('boom')")
    notices.alert_admins("digest failed for Steven", "RuntimeError('boom')")
    notices.alert_admins("watchdog check failed for Steven", "KeyError('x')")
    first_lines = [body.splitlines()[0] for _, body in telegram.messages]
    assert len(first_lines) == 2  # The repeat was suppressed
    assert first_lines[0].endswith("digest failed for Steven")
    assert first_lines[1].endswith("watchdog check failed for Steven")
    telegram.fail = True
    notices.alert_admins("another failure", "x")  # Must not raise


# --------------------------------------------------------------------------------------------- holdings sources
def fetcher_with(client, user_name="Andres"):
    fetcher = PortfolioFetcher.__new__(PortfolioFetcher)
    fetcher.user_name = user_name
    fetcher.holdings_issues = []
    fetcher.client = client
    return fetcher


def raise_(error):
    raise error


def test_snaptrade_problems_are_recorded():
    stale = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=6)).isoformat()
    fetcher = fetcher_with(SimpleNamespace(
        account_information=SimpleNamespace(
            list_user_accounts=lambda: SimpleNamespace(body=[{"id": "a1", "name": "TFSA", "sync_status": {"holdings": {"last_successful_sync": stale}}}]),
            get_all_account_positions=lambda account_id: raise_(RuntimeError("502 Bad Gateway"))),
        connections=SimpleNamespace(list_brokerage_authorizations=lambda: SimpleNamespace(
            body=[{"disabled": True, "disabled_date": "2026-10-12T00:00:00Z", "brokerage": {"name": "Questrade"}}]))))
    assert fetcher.get_holdings_df().empty
    assert [i["reason"] for i in fetcher.holdings_issues] == ["connection_disabled", "stale_sync", "positions_error"]

    down = fetcher_with(SimpleNamespace(account_information=SimpleNamespace(list_user_accounts=lambda: raise_(RuntimeError("401")))))
    down.get_holdings_df()
    assert [i["reason"] for i in down.holdings_issues] == ["api_error"]

    none = fetcher_with(SimpleNamespace(account_information=SimpleNamespace(list_user_accounts=lambda: SimpleNamespace(body=[]))))
    none.get_holdings_df()
    assert [i["reason"] for i in none.holdings_issues] == ["no_accounts"]


def test_holdings_problems_give_the_fix_only_to_admins(monkeypatch):
    monkeypatch.setitem(params_dict["OPS"], "ALERT_USERS", ["Andres"])
    down = SimpleNamespace(account_information=SimpleNamespace(list_user_accounts=lambda: raise_(RuntimeError("401 Unauthorized"))))
    for user in ("Andres", "Steven"):
        fetcher = fetcher_with(down, user)
        fetcher.get_holdings_df()
        issue = fetcher.holdings_issues[0]
        # The admins' text (run log, admin alerts) always has the fix and the API's message
        assert "Check the SnapTrade client ID" in issue["message"] and "401 Unauthorized" in issue["message"]
        body = notices.holdings_issue_body(issue)
        assert body.count("SnapTrade could not be reached") == 1
        if user == "Andres":
            assert "Check the SnapTrade client ID" in body and "Contact Andres" not in body
        else:
            assert "Contact Andres to address this issue." in body
            assert ".env" not in body and "401" not in body


def test_key_notice_gives_the_fix_only_to_admins(monkeypatch, telegram):
    monkeypatch.setitem(params_dict["OPS"], "ALERT_USERS", ["Andres"])
    for user in ("Andres", "Steven"):
        wd.send_key_notice(user, ApiKeyUnusable("Anthropic", "no_credits", user, "Your credit balance is too low."), EMPTY_LOG)
    (_, andres), (_, steven) = telegram.messages
    assert "Add credits or enable billing in platform.claude.com." in andres and "credit balance is too low" in andres
    assert "Your Anthropic API key has no credits left, or billing is not set up. Contact Andres to address this issue." in steven
    assert "platform.claude.com" not in steven


def test_sheet_missing_columns_is_reported_not_raised(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(pd, "read_csv", lambda url: pd.DataFrame({"symbol": ["AAPL"], "qty": [1]}))
    fetcher = fetcher_with(None)
    assert fetcher.pull_sheets_holdings("sheet-id").empty
    assert [i["reason"] for i in fetcher.holdings_issues] == ["sheet_invalid"]


# --------------------------------------------------------------------------------------------- run logs
def test_run_log_writes_timestamped_lines_with_the_user(tmp_path, monkeypatch):
    monkeypatch.setattr(run_logging, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(sys, "stdout", sys.stdout)  # Restored by monkeypatch after the test
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    root = logging.getLogger()
    handlers_before, level_before = list(root.handlers), root.level
    try:
        path = run_logging.start_run_log("test_run")

        def user_thread():
            run_logging.name_thread("Steven")
            print("checking holdings")

        thread = threading.Thread(target=user_thread)
        thread.start()
        thread.join()
        print("partial ", end="")
        print("line")
    finally:
        for handler in root.handlers[:]:
            if handler not in handlers_before:
                root.removeHandler(handler)
                handler.close()
        root.handlers[:] = handlers_before
        root.setLevel(level_before)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert any("[Steven] checking holdings" in line for line in lines)
    assert any(line.endswith("partial line") for line in lines)  # Partial writes are joined into one line
