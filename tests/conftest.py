"""
Shared test setup. Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-15, at Andres's request.

Every test runs offline and touches nothing real:
- files the code writes (market-context cache, alert log, FX cache, track record, usage ledger) go to a
  temporary folder;
- Telegram messages are captured in `telegram` instead of sent;
- yfinance downloads fail loudly unless a test replaces them, so no test silently depends on live market data.
Anthropic and Gemini calls go through mock HTTP transports inside the tests that need them.
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

    def no_network(*args, **kwargs):
        raise RuntimeError("yfinance.download called in a test without a fake")

    monkeypatch.setattr(yfinance, "download", no_network)
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
