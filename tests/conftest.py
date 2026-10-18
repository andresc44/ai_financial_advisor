"""
Shared test setup. Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-15, at Andres's request.

Every test runs offline and touches nothing real:
- files the code writes (market-context cache, alert log, FX cache, track record, usage ledger, digest HTML) go
  to a temporary folder;
- Telegram messages are captured in `telegram` instead of sent, and emails fail loudly unless a test replaces
  the SMTP connection (2026-10-17);
- yfinance downloads fail loudly unless a test replaces them, so no test silently depends on live market data;
- the universal content's macro news and market dashboard return nothing unless a test asks for them.
Anthropic, DeepSeek, and Gemini calls go through mock HTTP transports inside the tests that need them; every job
uses Claude unless a test picks another model (2026-10-17).
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TelegramRecorder:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []
        self.fail = False

    def __call__(self, user_name: str, body: str) -> bool:
        if self.fail:
            raise ValueError("Telegram unavailable (test)")
        self.messages.append((user_name, body))
        return True


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Redirects every file the code writes, and blocks live Yahoo downloads."""
    import yfinance

    import src.fx_rates as fx_rates
    import src.personalized.add_metrics as add_metrics
    import src.telegram_alert.notices as notices
    import src.track_record as track_record
    import src.usage_tracking as usage_tracking

    monkeypatch.setattr(add_metrics, "CONTEXT_CACHE_PATH", tmp_path / "market_context_cache.json")
    monkeypatch.setattr(notices, "CSV_DIR", tmp_path / "alerts_sent")
    monkeypatch.setattr(notices, "CSV_PATH", tmp_path / "alerts_sent" / "alerts.csv")
    monkeypatch.setattr(fx_rates, "CACHE_PATH", tmp_path / "fx_rates.json")
    monkeypatch.setattr(track_record, "PERSONAL_PATH", tmp_path / "track" / "personal.jsonl")
    monkeypatch.setattr(track_record, "UNIVERSAL_PATH", tmp_path / "track" / "universal.jsonl")
    monkeypatch.setattr(usage_tracking, "LEDGER_PATH", tmp_path / "usage" / "api_usage.jsonl")

    # 2026-10-17: rendered digests go to the temporary folder, and no test can email anyone by accident
    import smtplib

    import src.digest.create_html as create_html

    monkeypatch.setattr(create_html, "REPORTS_DIR", tmp_path / "html_reports")

    def no_smtp(*args, **kwargs):
        raise RuntimeError("smtplib.SMTP called in a test without a fake")

    monkeypatch.setattr(smtplib, "SMTP", no_smtp)

    def no_network(*args, **kwargs):
        raise RuntimeError("yfinance.download called in a test without a fake")

    monkeypatch.setattr(yfinance, "download", no_network)

    # 2026-10-17: tests don't depend on which models parameters.py picks: every job uses Claude unless a test sets
    # LLM itself. A DeepSeek client built without a mock transport fails instead of calling the real API with the .env
    # key (with deepseek-flash the default, Claude-mocked tests had sent real requests).
    import httpx

    import src.deepseek_client as deepseek_client
    from src.parameters import params_dict

    monkeypatch.setitem(params_dict, "LLM", {"UNIVERSAL": {"model": "claude-sonnet-5-5", "effort": "medium"},
                                             "HOLDINGS": {"model": "claude-sonnet-5-5", "effort": "medium"}})
    import src.llm_models as llm_models

    monkeypatch.setattr(llm_models, "ENV_OVERRIDES", {})  # .env's UNIVERSAL_LLM_MODEL / _EFFORT too
    real_init = deepseek_client.DeepSeekClient.__init__

    def refuse(request):
        raise RuntimeError(f"DeepSeek called in a test without a mock transport: {request.url}")

    def offline_init(self, api_key, transport=None):
        real_init(self, api_key, transport=transport or httpx.MockTransport(refuse))

    monkeypatch.setattr(deepseek_client.DeepSeekClient, "__init__", offline_init)

    # 2026-10-18: each test starts with an empty Finnhub pacing window
    from src.universal.recommendations import data_source_guard

    monkeypatch.setattr(data_source_guard, "FINNHUB_PACER", data_source_guard.FinnhubPacer(60))

    # 2026-10-16: the universal content's macro news (a real Gemini call with the .env key) and market dashboard
    # (Yahoo, FRED, CNN, Wikipedia) are replaced with "nothing fetched"; tests of them call the real functions
    import src.universal.universal_consolidated as universal

    monkeypatch.setattr(universal, "get_macro_stories", lambda: (None, []))
    monkeypatch.setattr(universal, "get_market_dashboard", lambda: ({}, []))
    return tmp_path


@pytest.fixture(autouse=True)
def telegram(monkeypatch):
    """Captures Telegram messages from every module that sends them."""
    import src.telegram_alert.hourly_watchdog as hourly_watchdog
    import src.telegram_alert.notices as notices

    recorder = TelegramRecorder()
    monkeypatch.setattr(notices, "send_telegram_message", recorder)
    monkeypatch.setattr(hourly_watchdog, "send_telegram_message", recorder)
    return recorder
