"""Digest scheduling and parallel runs, per-user content, news links, portfolio review, track record, user config, and locks."""
import json
import os
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import src.digest.digest_payload as dp
import src.digest.digest_schedule as schedule
import src.personalized.add_metrics as add_metrics
import src.personalized.holdings_state as holdings_state
import src.personalized.personalized_consolidated as pc
import src.personalized.symbol_specific_news as news
import src.tracking.track_record as track_record
from src.config.check_config import check_user, env_template
from src.personalized.portfolio_review import SYSTEM_PROMPT, build_portfolio_summary
from src.personalized.user_profiles import USER_PROFILES, build_profile, env_prefix

ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------------------------------- schedule
@pytest.mark.parametrize("when, expected", [
    ("2026-10-15T06:10", None),
    ("2026-10-15T07:10", "Pre-Market"),
    ("2026-10-15T09:40", None),          # After the open: too late for Pre-Market
    ("2026-10-15T16:05", None),          # Before close + 10 minutes
    ("2026-10-15T16:10", "Post-Market"),
    ("2026-10-15T20:15", None),          # Window over
    ("2026-10-17T07:10", None),          # Saturday
    ("2026-11-26T07:10", "Pre-Market"),  # US Thanksgiving, but the TSX trades
])
def test_due_digest(when, expected):
    due = schedule.due_digest(datetime.fromisoformat(when).replace(tzinfo=ET))
    assert (due[1] if due else None) == expected


def test_sent_state_prevents_duplicates(tmp_path):
    path, day = tmp_path / "digest_state.json", date(2026, 10, 15)
    schedule.record_sent(day, "Pre-Market", "Andres", path)
    schedule.record_sent(day, "Pre-Market", "Andres", path)
    schedule.record_sent(date(2026, 9, 1), "Post-Market", "Andres", path)  # Older than retention: pruned next write
    schedule.record_sent(day, "Post-Market", "Steven", path)
    assert schedule.users_already_sent(day, "Pre-Market", path) == {"Andres"}
    assert "2026-09-01 Post-Market" not in json.loads(path.read_text())


# --------------------------------------------------------------------------------------------- digest run
@pytest.fixture
def digest_run(monkeypatch, tmp_path):
    """run_daily_digests with fake content: users are due at 07:10 ET and none has been sent yet."""
    sent, failing = [], set()
    monkeypatch.setattr(dp, "determine_recipients", lambda recipients, period=None: recipients)
    monkeypatch.setattr(dp, "users_already_sent", lambda day, period: set())
    monkeypatch.setattr(dp, "record_sent", lambda day, period, user: None)

    def universal(self):
        time.sleep(0.5)
        self.payload["recommendations"] = {}
        return True

    def personal(self, user):
        time.sleep(0.5)
        if user in failing:
            raise RuntimeError(f"{user} broke")
        return {"holdings": [user]}

    def send(self, user, personal=None):
        payload = self.personalized_content(user, personal)
        sent.append((user, "recommendations" in payload, payload["subject"]))

    monkeypatch.setattr(dp.DigestPayload, "universal_content", universal)
    monkeypatch.setattr(dp.DigestPayload, "personal_content", personal)
    monkeypatch.setattr(dp.DigestPayload, "send_daily_digest_to", send)
    return SimpleNamespace(sent=sent, failing=failing, now=datetime(2026, 10, 15, 7, 10, tzinfo=ET))


def test_digest_builds_users_and_universal_in_parallel(digest_run, monkeypatch):
    monkeypatch.setitem(dp.params_dict["DIGEST"], "MAX_PARALLEL_USERS", 4)
    start = time.monotonic()
    dp.run_daily_digests(["U0", "U1", "U2", "U3"], now=digest_run.now)
    assert time.monotonic() - start < 1.5  # ~0.5 s of work each, all at once
    assert sorted(user for user, _, _ in digest_run.sent) == ["U0", "U1", "U2", "U3"]
    assert all(has_universal and "Pre-Market" in subject for _, has_universal, subject in digest_run.sent)


def test_one_users_failure_alerts_admins_and_others_still_send(digest_run, telegram):
    digest_run.failing.add("U1")
    dp.run_daily_digests(["U0", "U1"], now=digest_run.now)
    assert [user for user, _, _ in digest_run.sent] == ["U0"]
    assert any("Pre-Market digest failed for U1" in body for _, body in telegram.messages)


def test_universal_failure_sends_nothing_and_alerts(digest_run, monkeypatch, telegram):
    monkeypatch.setattr(dp.DigestPayload, "universal_content", lambda self: (_ for _ in ()).throw(SystemExit(1)))
    dp.run_daily_digests(["U0", "U1"], now=digest_run.now)
    assert digest_run.sent == []
    assert any("universal content failed" in body for _, body in telegram.messages)


def test_check_only_and_nothing_due_send_nothing(digest_run):
    dp.run_daily_digests(["U0"], now=digest_run.now, check_only=True)
    dp.run_daily_digests(["U0"], now=datetime(2026, 10, 15, 12, 0, tzinfo=ET))
    assert digest_run.sent == []


# --------------------------------------------------------------------------------------------- one user's content
@pytest.fixture
def personal_env(monkeypatch):
    os.environ.update({"TESTUSER_INST": "Questrade", "TESTUSER_EMAIL": "t@example.com", "TESTUSER_TZ": "America/Toronto"})
    monkeypatch.setitem(pc.USER_PROFILES, "TestUser",
                        {"account_company": "TESTUSER_INST", "destination_email": "TESTUSER_EMAIL", "home_timezone": "TESTUSER_TZ"})
    constructed = []
    overlap = {"claude_running": False, "news_saw_claude_running": False}

    class FakeFetcher:
        def __init__(self, user_name):
            constructed.append(user_name)
            self.holdings_issues = []
            self.full_data = pd.DataFrame({"symbol": ["AAA", "BBB", "AAA"], "description": ["Alpha", float("nan"), "Alpha"]})

        def get_holdings_digest_payload(self, reuse_recent_evaluations=False):
            assert reuse_recent_evaluations
            overlap["claude_running"] = True
            time.sleep(0.5)
            overlap["claude_running"] = False
            return {"holdings": [{"symbol": "AAA"}, {"symbol": "BBB"}, {"symbol": "AAA"}], "portfolio_beta": 1.0, "api_key_issues": []}

    companies_seen = {}

    def fake_news(companies, api_key, user_name=None):
        companies_seen.update(companies)
        time.sleep(0.2)
        overlap["news_saw_claude_running"] = overlap["claude_running"]
        item = news.SymbolNewsItem(priority="HIGH", summary="e.", source_url=None)
        return {symbol: [item] for symbol in companies}, None

    monkeypatch.setattr(pc, "PortfolioFetcher", FakeFetcher)
    monkeypatch.setattr(pc, "fetch_symbol_news", fake_news)
    monkeypatch.setattr(pc, "get_gemini_api_key", lambda user: "g")
    monkeypatch.setattr(pc, "get_llm_api_key", lambda user, provider: "a")  # 2026-10-23: the review's key
    monkeypatch.setattr(pc, "review_portfolio", lambda holdings, beta, api_key, user_name=None, home=None: ([], None))
    yield SimpleNamespace(constructed=constructed, overlap=overlap, companies=companies_seen)
    for key in ("TESTUSER_INST", "TESTUSER_EMAIL", "TESTUSER_TZ"):
        os.environ.pop(key, None)


def test_news_runs_while_claude_evaluates(personal_env):
    content = pc.get_personal_content("TestUser")
    assert personal_env.overlap["news_saw_claude_running"]
    assert personal_env.companies == {"AAA": "Alpha", "BBB": None}  # One request per stock; NaN name -> ticker
    assert all(len(h["news_bullets"]) == 1 for h in content["holdings"])


def test_payload_reports_only_this_users_api_spend(personal_env, monkeypatch):
    import src.tracking.usage_tracking as usage

    def call(user):
        return usage.record_anthropic_response(user, "digest", SimpleNamespace(model="claude-opus-5-5", usage=SimpleNamespace(
            input_tokens=1_000_000, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0, iterations=None)))

    real_news = pc.fetch_symbol_news

    def news_and_spend(companies, api_key, user_name=None):
        call(user_name)
        call("SomeoneElse")
        return real_news(companies, api_key, user_name)

    monkeypatch.setattr(pc, "fetch_symbol_news", news_and_spend)
    spend = pc.get_personal_content("TestUser")["api_usage"]
    assert spend["currency"] == "USD"
    assert spend["this_digest"]["anthropic"]["calls"] == 1
    assert spend["this_digest"]["total_cost_usd"] == pytest.approx(4.0)


def test_missing_env_value_fails_before_any_api_work(personal_env):
    os.environ.pop("TESTUSER_EMAIL")
    with pytest.raises(ValueError):
        pc.get_personal_content("TestUser")
    assert personal_env.constructed == []


def test_a_user_with_no_holdings_gets_an_ideas_only_digest(personal_env, monkeypatch):
    # Claude-authored (2026-10-21): <NAME>_HOLDINGS_SOURCE=NO_HOLDINGS, e.g. Koby. Nothing is fetched and no key used.
    monkeypatch.setitem(USER_PROFILES, "Ideas", build_profile("Ideas"))
    for key in list(os.environ):
        if key.startswith("IDEAS_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("IDEAS_HOLDINGS_SOURCE", "NO_HOLDINGS")
    monkeypatch.setenv("IDEAS_EMAIL", "ideas@example.com")
    monkeypatch.setattr(pc, "get_gemini_api_key", lambda user: pytest.fail("an ideas-only user needs no key"))
    content = dp.DigestPayload(period="Pre-Market").personal_content("Ideas")
    assert personal_env.constructed == []  # No holdings fetched
    assert content["holdings"] == [] and content["holdings_source"] == "NO_HOLDINGS"
    started = content["personal_started_at"]
    assert started["home_timezone"] == "America/New_York" and started["us_eastern"]  # No timezone set: US Eastern
    monkeypatch.setenv("IDEAS_TIMEZONE", "America/Bogota")
    assert dp.DigestPayload().personal_content("Ideas")["personal_started_at"]["home_timezone"] == "America/Bogota"
    # The template's required keys for this kind of user: no holdings or LLM keys, but an email address
    rows = {env_key: status for status, env_key, _ in check_user("Ideas")}
    assert rows["IDEAS_GEMINI_API_KEY"] == "unset" and rows["IDEAS_ANTHROPIC_API_KEY"] == "unset"
    assert rows["IDEAS_RECEIVE_PREMARKET_DIGEST"] == "MISSING"
    monkeypatch.delenv("IDEAS_EMAIL")
    with pytest.raises(ValueError):  # Fails before the universal content finishes, like a user with holdings
        dp.DigestPayload().personal_content("Ideas")


def test_the_hourly_run_sends_the_holiday_notice(digest_run, telegram):
    # Claude-authored (2026-10-21): US Thanksgiving; the TSX trades, so the Pre-Market digest still goes out
    thanksgiving = datetime(2026, 11, 26, 7, 10, tzinfo=ET)
    dp.run_daily_digests(["U0"], now=thanksgiving, check_only=True)
    assert telegram.messages == []  # --check sends nothing
    dp.run_daily_digests(["U0"], now=thanksgiving)
    assert [user for user, _, _ in digest_run.sent] == ["U0"]
    assert [(user, "Thanksgiving" in body) for user, body in telegram.messages] == [("Andres", True)]


# --------------------------------------------------------------------------------------------- news links + review
def test_only_links_google_search_returned_are_kept(monkeypatch):
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
    targets = {redirect + "a": "https://www.reuters.com/business/alpha-beats/?utm_source=x"}
    monkeypatch.setattr(news.requests, "get", lambda url, allow_redirects, timeout: SimpleNamespace(
        is_redirect=True, headers={"Location": targets[url]}))
    chunks = [SimpleNamespace(web=SimpleNamespace(uri=redirect + "a")), SimpleNamespace(web=SimpleNamespace(uri="https://sec.gov/f/8k"))]
    response = SimpleNamespace(candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(grounding_chunks=chunks))])
    items = [news.SymbolNewsItem(priority="HIGH", summary="", source_url=url) for url in (
        "http://reuters.com/business/alpha-beats", "https://www.sec.gov/f/8k/", "https://www.reuters.com/business/invented", None)]
    news._verify_source_urls(items, news._grounded_urls(response))
    assert [item.source_url for item in items] == [targets[redirect + "a"], "https://sec.gov/f/8k", None, None]


def test_review_summary_math_and_missing_values():
    nan = float("nan")
    summary = build_portfolio_summary([
        {"symbol": "AAA", "shares": 10.0, "market_value": 6000.0, "total_cost_basis": 4000.0, "daily_pnl": 60.0,
         "last_close_usd": 594.0, "sector": "Technology", "currency": "USD", "account_name": "TFSA"},
        {"symbol": "AAA", "shares": 5.0, "market_value": 3000.0, "total_cost_basis": 0.0, "daily_pnl": 30.0,
         "last_close_usd": 594.0, "currency": "USD", "account_name": "Margin"},  # Unknown cost basis
        {"symbol": "BBB", "shares": 10.0, "market_value": 1000.0, "total_cost_basis": 1250.0, "daily_pnl": -20.0,
         "last_close_usd": 102.0, "sector": nan, "recommendation": nan, "rsi": nan, "currency": "CAD"},
    ], portfolio_beta=1.2)
    portfolio, stocks = summary["portfolio"], {s["symbol"]: s for s in summary["holdings"]}
    assert stocks["AAA"]["weight_pct"] == 90.0 and stocks["AAA"]["unrealized_pnl_pct"] == 50.0
    assert portfolio["daily_pnl_pct"] == round(70 / (594 * 15 + 102 * 10) * 100, 2)
    assert stocks["BBB"]["sector"] is None and stocks["BBB"]["recommendation"] is None and stocks["BBB"]["rsi"] is None
    assert "NaN" not in json.dumps(summary, default=str)


def test_the_review_summary_is_in_the_home_currency(monkeypatch):
    # Claude-authored (2026-10-21): Andres asked for the portfolio risk notes in the home currency, like the
    # overview. The holdings' amounts are USD; the summary restates them, and the prompt names the currency.
    rows = [{"symbol": "AAA", "shares": 10.0, "market_value": 1250.0, "total_cost_basis": 1000.0, "daily_pnl": 12.5,
             "last_close_usd": 123.75, "currency": "USD", "daily_pnl_live": False},
            {"symbol": "VOD.L", "shares": 100.0, "market_value": 125.0, "total_cost_basis": 100.0, "daily_pnl": 1.0,
             "last_close_usd": 1.24, "currency": "GBX", "daily_pnl_live": True}]
    usd, gbp = build_portfolio_summary(rows, 1.0), build_portfolio_summary(rows, 1.0, ("GBP", 1.25))
    assert (usd["portfolio"]["money_currency"], usd["portfolio"]["money_format"]) == ("USD", "$1,234")
    assert (gbp["portfolio"]["money_currency"], gbp["portfolio"]["money_format"]) == ("GBP", "\u00a31,234")
    assert gbp["portfolio"]["total_market_value"] == 1100.0  # $1,375 at 1.25
    stocks = {s["symbol"]: s for s in gbp["holdings"]}
    assert stocks["AAA"]["market_value"] == 1000.0 and stocks["AAA"]["weight_pct"] == usd["holdings"][0]["weight_pct"]
    assert gbp["portfolio"]["daily_pnl_pct"] == usd["portfolio"]["daily_pnl_pct"]  # Percentages don't change
    assert stocks["VOD.L"]["daily_pnl_live"] is True and stocks["AAA"]["daily_pnl_live"] is False
    assert "money_currency" in SYSTEM_PROMPT and "daily_pnl_live" in SYSTEM_PROMPT
    # The digest asks for the overview's currency, or USD when it has no rate (as the overview does)
    monkeypatch.setattr(pc, "get_home_currency", lambda user: "GBP")
    monkeypatch.setattr(pc, "usd_rates", lambda codes: {"GBP": 1.25})
    assert pc._home("Steven") == ("GBP", 1.25)
    monkeypatch.setattr(pc, "usd_rates", lambda codes: {"GBP": None})
    assert pc._home("Steven") == ("USD", 1.0)


# --------------------------------------------------------------------------------------------- track record
def test_track_record_forward_returns():
    when = datetime(2026, 9, 15, 11, 10, tzinfo=timezone.utc)  # 07:10 ET
    df = pd.DataFrame({"symbol": ["AAPL", "AAPL", "MSFT"], "recommendation": ["hold", "hold", "trim"],
                       "current_price": [100.0, 100.0, 200.0]})
    track_record.log_personal("Andres", df, when, source="digest")
    track_record.log_universal({"top_long_term": [{"ticker": "NET", "longScore": 80}], "top_swing": []},
                               {"NET": {"price_action": {"current_price": 50.0}}}, when)
    records = track_record.load_records(track_record.PERSONAL_PATH)
    assert len(records) == 2 and len(track_record.load_records(track_record.UNIVERSAL_PATH)) == 1

    days = [date(2026, 9, d) for d in (14, 15, 16, 17, 18, 21)]
    closes = {"AAPL": pd.Series([100, 101, 102, 103, 104, 105.0], index=days),
              "MSFT": pd.Series([200, 198, 196, 194, 192, 190.0], index=days),
              "SPY": pd.Series([500, 505, 505, 505, 505, 510.0], index=days)}
    out = track_record.add_forward_returns(records, closes, horizons=(1, 5, 20)).set_index("symbol")
    assert out.loc["AAPL", "return_1d"] == pytest.approx(1.0) and out.loc["AAPL", "excess_5d"] == pytest.approx(3.0)
    assert out.loc["MSFT", "return_5d"] == pytest.approx(-5.0)
    assert pd.isna(out.loc["AAPL", "return_20d"])  # Pending
    summary = track_record.summarize(out.reset_index(), "recommendation", horizons=(5,))
    assert summary.set_index("recommendation").loc["trim", "vs_spy_5d_%"] == pytest.approx(-7.0)


# --------------------------------------------------------------------------------------------- user config
def test_generated_profiles_keep_the_existing_env_names():
    profile = build_profile("Andres")
    assert profile["account_company"] == "ANDRES_INSTITUTION" and profile["google_sheets_id"] == "ANDRES_SHEETS_ID"
    assert profile["Receive_Premarket_Digest"] == "ANDRES_RECEIVE_PREMARKET_DIGEST"
    assert profile["anthropic_api_key"] == "ANDRES_ANTHROPIC_API_KEY" and len(profile) == 25
    assert profile["gemini_free_api_key"] == "ANDRES_GEMINI_FREE_API_KEY"  # 2026-10-26
    assert profile["deepseek_api_key"] == "ANDRES_DEEPSEEK_API_KEY" and profile["holdings_llm_model"] == "ANDRES_HOLDINGS_LLM_MODEL"
    assert profile["home_currency"] == "ANDRES_HOME_CURRENCY"  # 2026-10-21
    assert profile["long_recs"] == "ANDRES_LONG_RECS" and profile["swing_recs"] == "ANDRES_SWING_RECS"  # 2026-10-24
    assert env_prefix("Mary-Jo") == "MARY_JO"


def test_home_currency_is_one_of_the_allowed_ones_or_usd(monkeypatch):
    # Claude-authored (2026-10-21): <NAME>_HOME_CURRENCY, read case-insensitively; anything else falls back to USD
    from src.personalized.user_settings import get_home_currency

    monkeypatch.setitem(USER_PROFILES, "Newbie", build_profile("Newbie"))
    for value, expected in (("cop", "COP"), (" EUR ", "EUR"), ("JPY", "USD"), (None, "USD")):
        if value is None:
            monkeypatch.delenv("NEWBIE_HOME_CURRENCY", raising=False)
        else:
            monkeypatch.setenv("NEWBIE_HOME_CURRENCY", value)
        assert get_home_currency("Newbie") == expected
    monkeypatch.setenv("NEWBIE_HOME_CURRENCY", "JPY")
    rows = {env_key: (status, note) for status, env_key, note in check_user("Newbie")}
    assert rows["NEWBIE_HOME_CURRENCY"][0] == "INVALID"


def test_check_config_rows_never_show_secrets(monkeypatch):
    for key in list(os.environ):
        if key.startswith("NEWBIE_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("NEWBIE_HOLDINGS_SOURCE", "SNAPTRADE")
    monkeypatch.setenv("NEWBIE_RECEIVE_PREMARKET_DIGEST", "true")  # Must be exactly True / False
    monkeypatch.setenv("NEWBIE_ANTHROPIC_API_KEY", "sk-ant-secret-value")
    rows = {env_key: (status, note) for status, env_key, note in check_user("Newbie")}
    assert rows["NEWBIE_RECEIVE_PREMARKET_DIGEST"][0] == "INVALID"
    assert rows["NEWBIE_SNAPTRADE_CLIENT_ID"][0] == "MISSING"   # Required for SNAPTRADE
    assert rows["NEWBIE_SHEETS_ID"][0] == "unset"               # Not required for SNAPTRADE
    assert rows["NEWBIE_ANTHROPIC_API_KEY"][0] == "OK"
    assert "sk-ant-secret-value" not in json.dumps(rows)
    assert "NEWBIE_EMAIL=" in env_template("Newbie")


# --------------------------------------------------------------------------------------------- locks
def test_parallel_state_updates_are_not_lost(tmp_path):
    path = tmp_path / "state.json"
    threads = [threading.Thread(target=holdings_state.update_user_state,
                                args=("Andres", lambda us, i=i: us["symbols"].update({f"X{i}": {}}), path))
               for i in range(24)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert len(holdings_state.load_state(path)["users"]["Andres"]["symbols"]) == 24


def test_shared_symbols_are_fetched_once_across_users(monkeypatch):
    fetched = []

    class FakeDataFetcher:
        def __init__(self, tickers):
            pass

        def congregate_LLM_input_data(self, symbols, as_dict_records=True):
            fetched.append(sorted(symbols))
            time.sleep(0.2)
            return {symbol: {} for symbol in symbols}

    monkeypatch.setattr(add_metrics, "HoldingsDataFetcher", FakeDataFetcher)
    users = [pd.DataFrame({"symbol": ["A", "B"]}), pd.DataFrame({"symbol": ["B", "C"]})]
    results = [None, None]

    def run(i):
        results[i] = add_metrics.add_metrics_to_holdings(None, users[i])

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert sorted(symbol for batch in fetched for symbol in batch) == ["A", "B", "C"]
    assert all(result["market_context"].notna().all() for result in results)



def test_cached_contexts_without_daily_bars_get_them(monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    add_metrics._save_context_cache({  # Saved before daily bars were added; otherwise current
        "A": {"context": {"symbol": "A"}, "slow_fetched_at": now, "fresh_fetched_at": now},
        "B": {"context": {"symbol": "B", "daily_bars": [{"date": "2026-10-16"}]}, "slow_fetched_at": now,
              "fresh_fetched_at": now},
    })
    asked = []

    class FakeDataFetcher:
        def __init__(self, tickers):
            pass

        def congregate_LLM_input_data(self, symbols, as_dict_records=True):
            raise AssertionError("a full fetch for a context that only lacked bars")

        def recent_daily_bars(self, symbols, days):
            asked.append((sorted(symbols), days))
            return {symbol: [{"date": "2026-10-16", "close": 1.0}] for symbol in symbols}

    monkeypatch.setattr(add_metrics, "HoldingsDataFetcher", FakeDataFetcher)
    monkeypatch.setitem(add_metrics.params_dict, "DAILY_BARS_DAYS", 21)
    result = add_metrics.add_metrics_to_holdings(None, pd.DataFrame({"symbol": ["A", "B"]}))
    assert asked == [(["A"], 21)]  # B already has them
    contexts = dict(zip(result["symbol"], result["market_context"]))
    assert contexts["A"]["daily_bars"] == [{"date": "2026-10-16", "close": 1.0}]
    assert "daily_bars" in add_metrics._load_context_cache()["A"]["context"]  # Saved, so it isn't asked again


# --------------------------------------------------------------------------------------------- heartbeat status
@pytest.mark.parametrize("when, expected", [
    ("2026-10-15T07:10", []),
    ("2026-10-15T10:10", ["Pre-Market"]),                 # Pre-Market window closed at the open
    ("2026-10-15T21:10", ["Pre-Market", "Post-Market"]),  # Post-Market window closed at 20:10
    ("2026-10-17T10:10", []),                              # Saturday: no digests at all
])
def test_ended_digests(when, expected):
    assert [period for _, period in schedule.ended_digests(datetime.fromisoformat(when).replace(tzinfo=ET))] == expected


def test_run_result_is_ok_only_when_every_digest_went_out(digest_run):
    ok, status = dp.run_daily_digests(["U0", "U1"], now=digest_run.now)
    assert ok and status.startswith("Pre-Market 2026-10-15: sent 2, failed 0 in ")
    digest_run.failing.add("U1")
    ok, status = dp.run_daily_digests(["U0", "U1"], now=digest_run.now)
    assert not ok and "sent 1, failed 1" in status and "U1" not in status  # Counts only: it goes to healthchecks.io


def test_missed_digest_fails_the_heartbeat_and_alerts(digest_run, monkeypatch, telegram):
    later = datetime(2026, 10, 15, 10, 10, tzinfo=ET)  # Nothing due; the Pre-Market window has closed
    monkeypatch.setattr(dp, "users_already_sent", lambda day, period: {"U0"})
    ok, status = dp.run_daily_digests(["U0", "U1"], now=later)
    assert not ok and status == "No digest due. Missed today: Pre-Market 2026-10-15 (1 user)."
    assert any("never went out" in body and "U1" in body for _, body in telegram.messages)

    monkeypatch.setattr(dp, "users_already_sent", lambda day, period: {"U0", "U1"})
    assert dp.run_daily_digests(["U0", "U1"], now=later) == (True, "No digest due.")


def test_run_status_file(tmp_path, monkeypatch):
    import src.ops.run_logging as run_logging

    monkeypatch.setattr(run_logging, "LOG_DIR", tmp_path / "logs")
    run_logging.write_run_status("digest", "Pre-Market 2026-10-15: sent 2, failed 0 in 31s.\n")
    assert (tmp_path / "logs" / "digest.status").read_text() == "Pre-Market 2026-10-15: sent 2, failed 0 in 31s.\n"


# --------------------------------------------------------------------------------------------- section start time
def test_section_start_in_home_and_eastern_time():
    started = datetime(2026, 10, 16, 11, 2, 30, tzinfo=timezone.utc)
    assert pc.section_started_at(started, "America/Bogota", "U") == {
        "home_timezone": "America/Bogota",
        "home": "Friday, October 16, 2026, 6:02 AM -05",
        "home_iso": "2026-10-16T06:02:30-05:00",
        "us_eastern": "Friday, October 16, 2026, 7:02 AM EDT",
        "us_eastern_iso": "2026-10-16T07:02:30-04:00",
    }
    noon = pc.section_started_at(datetime(2026, 12, 1, 17, 5, tzinfo=timezone.utc), "Europe/Madrid", "U")
    assert noon["home"] == "Tuesday, December 1, 2026, 6:05 PM CET" and noon["us_eastern"].endswith("12:05 PM EST")


def test_invalid_home_timezone_shows_eastern_only():
    times = pc.section_started_at(datetime(2026, 10, 16, 4, 30, tzinfo=timezone.utc), "Toronto", "U")
    assert times["home"] is None and times["home_timezone"] is None
    assert times["us_eastern"] == "Friday, October 16, 2026, 12:30 AM EDT"


def test_payload_has_the_section_start_time(personal_env):
    times = pc.get_personal_content("TestUser")["personal_started_at"]
    assert times["home_timezone"] == "America/Toronto"
    assert times["home"].rsplit(" ", 1)[1] in ("EDT", "EST") and times["us_eastern"].rsplit(" ", 1)[1] in ("EDT", "EST")


def test_check_only_never_alerts_about_missed_digests(digest_run, telegram):
    ok, status = dp.run_daily_digests(["U0"], now=datetime(2026, 10, 15, 10, 10, tzinfo=ET), check_only=True)
    assert not ok and "Missed today" in status and telegram.messages == []


# --------------------------------------------------------------------------------------------- --payload-only
def test_payload_only_saves_json_and_changes_nothing(digest_run, monkeypatch, tmp_path, telegram):
    import numpy as np

    monkeypatch.setattr(dp, "PAYLOAD_DIR", tmp_path / "payloads")
    monkeypatch.setattr(dp, "users_already_sent", lambda day, period: {"U0"})  # Built anyway
    monkeypatch.setattr(dp, "record_sent", lambda *args: pytest.fail("a payload-only run recorded a send"))
    monkeypatch.setattr(dp.DigestPayload, "personal_content", lambda self, user: {"holdings": [{
        "symbol": "AAA", "rsi": float("nan"), "shares": np.float64(2.5), "next_earnings": pd.Timestamp("2026-10-20"),
        "ex_date": pd.NaT}]})
    noon = datetime(2026, 10, 15, 12, 0, tzinfo=ET)  # Outside both windows, after the Pre-Market one closed

    ok, status = dp.run_daily_digests(["U0", "U1"], now=noon, payload_only=True, period="Post-Market")

    assert ok and status.startswith("Post-Market 2026-10-15: saved 2, failed 0")
    assert digest_run.sent == [] and telegram.messages == []  # No email, and no "missed digest" alert
    files = sorted((tmp_path / "payloads").glob("*.json"))
    assert [f.name.rsplit("_", 1)[0] for f in files] == ["U0_2026-10-15_Post-Market", "U1_2026-10-15_Post-Market"]

    def strict(constant):
        raise ValueError(f"not valid JSON: {constant}")

    payload = json.loads(files[0].read_text(encoding="utf-8"), parse_constant=strict)
    assert payload["subject"] == "Post-Market Digest 2026-10-15" and payload["user_name"] == "U0"
    assert "recommendations" in payload
    assert payload["holdings"][0] == {"symbol": "AAA", "rsi": None, "shares": 2.5,
                                      "next_earnings": "2026-10-20T00:00:00", "ex_date": None}


def test_payload_only_without_a_period_waits_for_a_window(digest_run, tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "PAYLOAD_DIR", tmp_path / "payloads")
    assert dp.run_daily_digests(["U0"], now=datetime(2026, 10, 15, 12, 0, tzinfo=ET), payload_only=True) == (
        True, "No digest due.")
    assert not (tmp_path / "payloads").exists()


def test_grounding_redirects_are_verified_without_metadata(monkeypatch):
    """gemini-3.6-flash with a response schema returns no grounding metadata, only the redirect links it was given."""
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
    targets = {redirect + "real": "https://investors.caterpillar.com/news/2026/dividend"}
    monkeypatch.setattr(news.requests, "get", lambda url, allow_redirects, timeout: SimpleNamespace(
        is_redirect=url in targets, headers={"Location": targets.get(url)}))
    response = SimpleNamespace(candidates=[SimpleNamespace(grounding_metadata=None)])
    items = [news.SymbolNewsItem(priority="HIGH", summary="", source_url=url) for url in (
        redirect + "real", redirect + "made-up-token", "https://www.reuters.com/business/invented")]
    news._verify_source_urls(items, news._grounded_urls(response))
    assert [item.source_url for item in items] == [targets[redirect + "real"], None, None]


def test_holding_news_can_be_switched_off(personal_env, monkeypatch):
    monkeypatch.setitem(pc.USER_PROFILES["TestUser"], "receive_holding_news", "TESTUSER_NEWS")
    monkeypatch.setenv("TESTUSER_NEWS", "False")
    monkeypatch.setattr(pc, "fetch_symbol_news", lambda *args, **kwargs: pytest.fail("news fetched while switched off"))
    content = pc.get_personal_content("TestUser")
    assert content["holdings"] and all(h["news_bullets"] == [] for h in content["holdings"])
