"""Claude-authored (2026-10-26, Andres): the free-tier/paid Gemini key switch for the universal and per-user slots."""
from types import SimpleNamespace

import pytest

import src.llm.gemini_key_rotation as rotation
import src.personalized.personalized_consolidated as pc
import src.universal.market_news as market_news
from src.personalized.api_errors import ApiKeyUnusable
from src.personalized.user_profiles import USER_PROFILES, build_profile


@pytest.fixture
def tester(monkeypatch):
    """A throwaway user with both a free and a paid Gemini key, isolated from the real .env."""
    monkeypatch.setitem(USER_PROFILES, "Tester", build_profile("Tester"))
    monkeypatch.setenv("TESTER_GEMINI_FREE_API_KEY", "free-key")
    monkeypatch.setenv("TESTER_GEMINI_API_KEY", "paid-key")
    return "Tester"


def quota_error(user_name=None):
    return ApiKeyUnusable("Gemini", "quota_exhausted", user_name, "daily quota exceeded")


def rate_limit_error(user_name=None):
    return ApiKeyUnusable("Gemini", "rate_limited", user_name, "per-minute limit")


# --------------------------------------------------------------------------------------------- key resolution
def test_free_key_falls_back_to_none_when_unset_or_switched(tester, monkeypatch):
    assert rotation.free_key(tester) == "free-key"
    assert rotation.free_key("Nobody") is None  # No profile, no free key
    rotation.switch_to_paid(tester, quota_error(tester))
    assert rotation.free_key(tester) is None  # Switched: callers go straight to paid
    assert rotation.paid_key(tester) == "paid-key"


def test_shared_slot_reads_the_plain_env_vars(monkeypatch):
    monkeypatch.setenv("GEMINI_FREE_API_KEY", "shared-free")
    monkeypatch.setenv("GEMINI_API_KEY", "shared-paid")
    assert rotation.free_key() == "shared-free" and rotation.paid_key(None) == "shared-paid"
    monkeypatch.delenv("GEMINI_FREE_API_KEY")
    assert rotation.free_key() is None


def test_switch_is_permanent_until_reset(tester):
    error = quota_error(tester)
    assert rotation.switch_to_paid(tester, error) is True  # First time: tells the caller to notify
    assert rotation.switched_to_paid(tester) is True
    assert rotation.switch_to_paid(tester, error) is False  # Already switched: idempotent, no second notify
    rotation.reset(tester)
    assert rotation.switched_to_paid(tester) is False
    assert rotation.free_key(tester) == "free-key"  # Tried again after a reset


def test_reset_all_clears_every_slot(tester):
    rotation.switch_to_paid(None, quota_error())
    rotation.switch_to_paid(tester, quota_error(tester))
    rotation.reset(every_slot=True)
    assert not rotation.switched_to_paid() and not rotation.switched_to_paid(tester)


def test_notify_switch_tells_only_andres_once(tester, telegram):
    error = quota_error(tester)
    rotation.notify_switch(tester, error)
    assert telegram.messages == [("Andres", telegram.messages[0][1])]
    assert "Tester" in telegram.messages[0][1] and "TESTER_GEMINI_FREE_API_KEY" in telegram.messages[0][1]
    assert "--reset Tester" in telegram.messages[0][1]
    rotation.notify_switch(tester, error)  # Same reason key (NOTICE_COOLDOWN_HOURS): not sent again
    assert len(telegram.messages) == 1


def test_notify_switch_names_the_shared_slot(telegram):
    rotation.notify_switch(None, quota_error())
    assert "shared Gemini key" in telegram.messages[0][1] and "--reset universal" in telegram.messages[0][1]


# --------------------------------------------------------------------------------------------- universal (shared)
def test_macro_news_switches_to_paid_on_a_blocking_free_key_failure(monkeypatch, telegram):
    monkeypatch.setenv("GEMINI_FREE_API_KEY", "shared-free")
    monkeypatch.setenv("GEMINI_API_KEY", "shared-paid")
    calls = []
    stories = SimpleNamespace(macro_stories=["ok"])

    def fetch(api_key=None):
        calls.append(api_key)
        if api_key == "shared-free":
            raise quota_error()
        return stories

    monkeypatch.setattr(market_news, "fetch_live_macro_stories", fetch)
    result, issues = market_news.get_macro_stories()
    assert result is stories and issues == []
    assert calls == ["shared-free", None]  # Free key tried, then the paid key (api_key=None -> shared_api_key)
    assert rotation.switched_to_paid() is True
    assert any("shared Gemini key" in body for _, body in telegram.messages)

    # The next call goes straight to the paid key, and doesn't notify again
    calls.clear()
    telegram.messages.clear()
    assert market_news.get_macro_stories() == (stories, [])
    assert calls == [None] and telegram.messages == []


def test_macro_news_does_not_switch_on_a_transient_rate_limit(monkeypatch, telegram):
    monkeypatch.setenv("GEMINI_FREE_API_KEY", "shared-free")
    monkeypatch.setenv("GEMINI_API_KEY", "shared-paid")

    def fetch(api_key=None):
        raise rate_limit_error()

    monkeypatch.setattr(market_news, "fetch_live_macro_stories", fetch)
    stories, issues = market_news.get_macro_stories()
    assert stories is None and issues[0]["reason"] == "rate_limited"
    # Not a blocking failure: no switch, and no "switched to paid" notice (alert_admins' own notice still fires)
    assert not rotation.switched_to_paid()
    assert not any("switched to the paid key" in body for _, body in telegram.messages)


# --------------------------------------------------------------------------------------------- per-user (Andres)
def test_holding_news_switches_to_paid_and_retries_only_whats_missing(tester, monkeypatch, telegram):
    calls = []

    def fetch(companies, api_key, user_name=None, **kwargs):
        calls.append((api_key, sorted(companies)))
        if api_key == "free-key":
            return {"AAA": ["news"]}, quota_error(user_name)  # AAA got news before the key failed; BBB didn't
        return {"BBB": ["more news"]}, None

    monkeypatch.setattr(pc, "fetch_symbol_news", fetch)
    news, key_error = pc.fetch_holdings_news(tester, {"AAA": "Alpha", "BBB": "Beta"})
    assert news == {"AAA": ["news"], "BBB": ["more news"]} and key_error is None
    assert calls == [("free-key", ["AAA", "BBB"]), ("paid-key", ["BBB"])]  # Only the missing stock is retried
    assert rotation.switched_to_paid(tester) is True
    assert any("Tester" in body for _, body in telegram.messages)


def test_holding_news_already_switched_goes_straight_to_paid(tester, monkeypatch, telegram):
    rotation.switch_to_paid(tester, quota_error(tester))
    calls = []

    def fetch(companies, api_key, user_name=None, **kwargs):
        calls.append(api_key)
        return {"AAA": ["news"]}, None

    monkeypatch.setattr(pc, "fetch_symbol_news", fetch)
    news, key_error = pc.fetch_holdings_news(tester, {"AAA": "Alpha"})
    assert calls == ["paid-key"] and key_error is None and telegram.messages == []


def test_holding_news_keeps_what_it_got_when_theres_no_paid_key_either(monkeypatch, telegram):
    monkeypatch.setitem(USER_PROFILES, "FreeOnly", build_profile("FreeOnly"))
    monkeypatch.setenv("FREEONLY_GEMINI_FREE_API_KEY", "free-key")
    monkeypatch.delenv("FREEONLY_GEMINI_API_KEY", raising=False)

    def fetch(companies, api_key, user_name=None, **kwargs):
        return {}, quota_error(user_name)

    monkeypatch.setattr(pc, "fetch_symbol_news", fetch)
    news, key_error = pc.fetch_holdings_news("FreeOnly", {"AAA": "Alpha"})
    assert news == {} and isinstance(key_error, ApiKeyUnusable) and key_error.reason == "missing"
    assert rotation.switched_to_paid("FreeOnly") is True  # Still recorded: the free key itself is still bad
