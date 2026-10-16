"""
Tests for the digest email: src/digest/create_html.py with email_template.html, send_gmail.py, and
digest_payload.py --from-payload. Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-17, at Andres's
request. Every payload here is made up; none of it is anyone's real portfolio.
"""
import json
import re
from email import message_from_bytes
from email.policy import default as default_policy
from pathlib import Path

import src.digest.create_html as ch
import src.digest.digest_payload as dp
import src.digest.send_gmail as sg

TEMPLATE_DIR = Path(ch.__file__).resolve().parent
RATIONALE = ("Price is above the 21-day EMA and the SAR, RSI is neutral, and earnings are three weeks away. " * 6).strip()
NEWS = ("On October 15, 2026, the company launched a new product line. The launch should support the stock over "
        "the next 1-3 weeks if demand holds up as analysts expect, though margins remain the key question.")


def holding(symbol, recommendation="hold", weight=10.0, account="Individual TFSA", value=1000.0, **extra):
    row = {
        "symbol": symbol, "description": f"{symbol} Corp.", "shares": 10.0, "current_price": value / 10,
        "market_value": value, "total_cost_basis": value * 0.9, "unrealized_pnl": value * 0.1,
        "daily_pnl": 5.0, "daily_pnl_pct": 0.5, "daily_pnl_session": "2026-10-15",
        "last_close": value / 10 - 0.5, "last_close_usd": value / 10 - 0.5, "portfolio_weight_pct": weight,
        "currency": "USD", "account_name": account, "recommendation": recommendation,
        "recommendation_rationale": RATIONALE, "rsi": 55.0, "natr": 2.0, "annualized_vol": 25.0,
        "ma_status": "Above 21 EMA (+1.2%)", "sentiment": "Neutral", "sector": "Industrials",
        "catalysts": [{"event_type": "Earnings", "label": "Q3 FY26 Earnings (after close)",
                       "event_date": "2026-10-21", "days_until": 5}],
        "news_bullets": [{"priority": p, "text": NEWS, "source_url": None} for p in ("HIGH", "MEDIUM", "LOW")],
    }
    row.update(extra)
    return row


def long_pick(ticker="LTA", **extra):
    pick = {
        "ticker": ticker, "longScore": 91.0, "swingScore": 50.0, "model": "claude-sonnet-5-5", "sentiment": "Bullish",
        "thesis": "Forward P/E of 18 with 30% revenue growth and a strong balance sheet.",
        "key_risk": "Demand could cool after a strong year.",
        "sub_scores": {"valuation_score": 21.0, "financial_health_score": 23.0, "growth_momentum_score": 24.0,
                       "competitive_moat_score": 23.0},
        "name": f"{ticker} Holdings", "sector": "Technology", "industry": "Software", "current_price": 100.0,
        "market_cap": 2.5e11, "52_week_low": 60.0, "52_week_high": 120.0, "analyst_rating": "buy",
        "analyst_target_price": 130.0, "analyst_count": 25, "next_earnings_date": "2026-11-20", "pe_trailing": 25.0,
        "pe_forward": 18.0, "peg_ratio": 0.9, "revenue_growth_yoy": 0.3, "earnings_growth_yoy": 0.4,
        "profit_margin": 0.25, "return_on_equity": 0.3, "debt_to_equity": 40.0, "free_cash_flow": 5e9,
    }
    pick.update(extra)
    return pick


def swing_pick(ticker="SWA", **extra):
    pick = long_pick(ticker, swingScore=85.0, longScore=60.0, key_risk=None)
    pick.update({
        "thesis": "Reclaimed the 21 EMA on rising volume, and the SAR flipped below price.",
        "entry_price": 100.0, "stop_loss": 95.0, "price_target": 112.0, "risk_reward": 2.4,
        "sub_scores": {"technical_setup_score": 26.0, "risk_reward_ratio_score": 24.0, "momentum_volume_score": 17.0,
                       "catalyst_sentiment_score": 18.0},
        "rsi_14": 58.0, "ema_9": 99.0, "ema_21": 97.5, "bollinger_lower": 92.0, "bollinger_upper": 104.0,
        "parabolic_sar": 94.0, "50_day_average": 96.0, "200_day_average": 88.0, "beta": 1.2, "volume": 3e6,
        "average_volume_3m": 2e6, "next_earnings_date": "2026-10-20",
    })
    pick.update(extra)
    return pick


def payload(**overrides):
    base = {
        "date": "2026-10-16", "period": "Pre-Market", "subject": "Finnbot Pre-Market Digest 2026-10-16",
        "user_name": "Test", "account_company": "Questrade", "home_timezone": "America/Toronto",
        "personal_started_at": {
            "home_timezone": "America/Toronto", "home": "Friday, October 16, 2026, 7:50 AM EDT",
            "home_iso": "2026-10-16T07:50:00-04:00", "us_eastern": "Friday, October 16, 2026, 7:50 AM EDT",
            "us_eastern_iso": "2026-10-16T07:50:00-04:00",
        },
        "recommendations": {"top_long_term": [long_pick()], "top_swing": [swing_pick()]},
        "market_data": {
            "yahoo_finance": {"S&P 500 (SPY)": {"Latest": 700.0, "1D %": "+0.28%", "5D %": "-0.28%", "1M %": "+2.61%"},
                              "10Y Treasury Yield ($TNX)": {"Latest": 4.5, "1D %": "-1.00%", "5D %": "+0.10%",
                                                            "1M %": "+0.00%"}},
            "yield_curve_10y_2y": {"Spread": "0.33%", "1D Change": "-4.0 bps"},
            "fear_and_greed": {"Score": 32.1, "Rating": "Fear"},
            "sp500_breadth": {"% > 50MA": "44.82%"},
        },
        "headline_news": {"macro_stories": [{"priority": "HIGH", "event_summary": "The central bank held rates.",
                                             "market_impact": "Yields stay high.", "source_url": "https://example.com/a"}]},
        "universal_issues": [],
        "holdings": [holding("AAA", "risk_alert", 30.0), holding("BBB", "hold", 50.0),
                     holding("CCC", "urgent_sell", 5.0), holding("DDD", "trim", 15.0)],
        "portfolio_beta": 1.4,
        "portfolio_review": [{"category": "WATCH", "text": "AAA is 30% of the portfolio.", "symbols": ["AAA"]},
                             {"category": "NOTE", "text": "BBB is steady.", "symbols": ["BBB"]}],
        "api_key_issues": [], "data_source_issues": [],
        "api_usage": {"note": "Estimated.", "this_digest": {"anthropic": {"cost_usd": 0.2}, "gemini": {"cost_usd": 0.1},
                                                            "total_cost_usd": 0.3}},
    }
    base.update(overrides)
    return base


def render(digest_payload):
    view = ch.build_view(digest_payload)
    return view, ch.render_html(view, TEMPLATE_DIR)


def test_sections_follow_the_requested_order():
    _, html = render(payload())
    assert html.startswith("<!DOCTYPE html>")
    markers = ["Good morning, Test", "Your portfolio", "Portfolio risk", "Connected account", "Markets &amp; macro",
               "Top macro stories", "Your holdings", "Swing ideas", "Long-term ideas"]
    positions = [html.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_holdings_most_urgent_first_with_each_stock_on_one_card():
    view = ch.build_view(payload(holdings=[
        holding("BBB", "hold", 50.0), holding("AAA", "risk_alert", 40.0, account="Individual TFSA"),
        holding("AAA", "risk_alert", 40.0, account="Individual RRSP"), holding("CCC", "urgent_sell", 5.0),
        holding("DDD", "trim", 15.0), holding("EEE", "risk_alert", 25.0), holding("FFF", None, 1.0),
    ]))
    assert [card["symbol"] for card in view["holdings"]] == ["CCC", "AAA", "EEE", "DDD", "BBB", "FFF"]
    merged = view["holdings"][1]
    assert merged["value"] == "$2,000.00"
    assert merged["meta"] == "20 shares · Individual RRSP · Individual TFSA"
    assert view["holdings"][-1]["signal"]["text"] == "Not evaluated"
    assert {row["name"]: row["positions"] for row in view["account"]["accounts"]} == {
        "Individual TFSA": "6 positions", "Individual RRSP": "1 position"}


def test_overview_and_risk_figures():
    view = ch.build_view(payload())
    portfolio, risk = view["portfolio"], view["risk"]
    assert portfolio["value"] == "$4,000.00"
    assert portfolio["session"] == "Oct 15 session"
    assert portfolio["change"] == "+$20.00 (+0.50%)"
    assert [stat["value"] for stat in portfolio["stats"]] == ["4", "+$400", "2"]  # Unrealized: 10% gain on cost
    assert risk["beta"]["value"] == "1.40" and risk["beta"]["label"] == "Above-market risk"
    assert [(m["label"], m["pct"]) for m in risk["mix"]] == [
        ("Urgent sell", "5%"), ("Risk alert", "30%"), ("Trim", "15%"), ("Hold", "50%")]
    assert [n["text"] for n in risk["watch"]] == ["AAA is 30% of the portfolio."]
    assert [n["text"] for n in risk["notes"]] == ["BBB is steady."]


def test_llm_text_is_escaped():
    _, html = render(payload(holdings=[holding("AAA", recommendation_rationale="<script>alert(1)</script> & more")]))
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; more" in html


def test_long_text_sits_in_dropdowns():
    _, html = render(payload())
    folded = "".join(re.findall(r"<details>(.*?)</details>", html, re.S))
    for text in (RATIONALE, NEWS, "Yields stay high.", "Trailing P/E", "Bollinger bands (20)"):
        assert html.count(text) == folded.count(text) > 0, text


def test_swing_levels_and_earnings_warning():
    view = ch.build_view(payload())
    card = view["swing"][0]
    assert [stat["value"] for stat in card["plan"]] == ["$100.00", "$95.00", "$112.00", "2.4 : 1"]
    assert card["earnings_warning"].startswith("Earnings Oct 20 (in 4 days)")

    no_levels = payload(recommendations={"top_long_term": [], "top_swing": [
        swing_pick(entry_price=None, stop_loss=None, price_target=None, risk_reward=None)]})
    view, html = render(no_levels)
    assert view["swing"][0]["plan"] == []
    assert "No trade levels" in html


def test_long_term_card_figures():
    card = ch.build_view(payload())["long_term"][0]
    assert [stat["value"] for stat in card["stats"]] == ["18.0", "0.90", "+30%", "25%"]
    assert card["analyst"] == "Analysts: Buy (25 analysts) · target $130.00 (+30%)"
    assert card["range"]["note"] == "17% below its 52-week high"
    assert ("Debt / equity", "40%") in card["details"]
    assert card["sector"] == "Technology · Software · $250.00B"


def test_digest_without_personal_or_macro_content():
    view, html = render(payload(
        holdings=[], portfolio_review=[], portfolio_beta=None, account_company=None, personal_started_at={},
        market_data={}, headline_news={}, recommendations={"top_long_term": [], "top_swing": []}, api_usage=None,
    ))
    assert view["portfolio"] is None and view["risk"] is None and view["account"] is None and view["market"] is None
    assert "Hello, Test" in html
    assert "No swing setups scored 80 or more" in html and "No long-term ideas scored 80 or more" in html


def test_issues_are_shown_first():
    _, html = render(payload(
        api_key_issues=[{"provider": "Gemini", "reason": "no_credits", "message": "The Gemini key is out of credits."}],
        universal_issues=[{"source": "Finnhub", "reason": "missing_key", "message": "FINNHUB_API_KEY is missing."}],
    ))
    assert html.index("Some parts of this digest are incomplete") < html.index("Your portfolio")
    assert "The Gemini key is out of credits." in html and "FINNHUB_API_KEY is missing." in html


def test_a_full_digest_fits_under_gmails_clipping_limit():
    full = payload(
        holdings=[holding(f"T{i:02d}", "risk_alert" if i % 2 else "hold", 10.0) for i in range(10)],
        recommendations={"top_long_term": [long_pick("L1"), long_pick("L2")],
                         "top_swing": [swing_pick(f"S{i}") for i in range(5)]},
    )
    view = ch.build_view(full)
    html = ch.fit_for_gmail(view, TEMPLATE_DIR)
    assert len(html.encode("utf-8")) <= ch.GMAIL_CLIP_BYTES
    assert view["trimmed"] is False  # 10 holdings with 3 news items each, 5 swing and 2 long-term ideas fit as is


def test_a_large_portfolio_drops_low_priority_news_to_fit():
    large = payload(holdings=[holding(f"T{i:02d}", "risk_alert" if i < 4 else "hold", 5.0) for i in range(20)])
    view = ch.build_view(large)
    html = ch.fit_for_gmail(view, TEMPLATE_DIR)
    assert len(html.encode("utf-8")) <= ch.GMAIL_CLIP_BYTES
    assert view["trimmed"] is True and "left out to keep this email under Gmail" in html
    alerts, holds = view["holdings"][:4], view["holdings"][4:]
    assert all(card["news"] for card in alerts)  # Positions that need attention keep their top news
    assert all(len(card["news"]) < 3 for card in holds)
    assert all(card["rationale"] for card in view["holdings"])  # Reasoning is never dropped


def test_a_portfolio_too_big_for_gmail_still_renders_and_warns(capsys):
    huge = payload(holdings=[holding(f"T{i:02d}", "hold", 1.0) for i in range(60)])
    view = ch.build_view(huge)
    html = ch.fit_for_gmail(view, TEMPLATE_DIR)
    ch.verify_output(html, view)
    assert all(card["news"] == [] and card["rationale"] for card in view["holdings"])
    assert "Gmail clips emails" in capsys.readouterr().out


def test_plain_text_version_covers_every_section():
    text = ch.render_text(ch.build_view(payload()))
    for expected in ("Good morning, Test.", "PORTFOLIO $4,000.00", "PORTFOLIO RISK", "CONNECTED ACCOUNT Questrade",
                     "MARKETS", "The central bank held rates.", "CCC - Urgent sell", "SWING IDEAS", "SWA (score 85)",
                     "LONG-TERM IDEAS", "LTA (score 91)", "not investment advice"):
        assert expected in text, expected


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def test_send_html_email_sends_html_and_text_parts(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(sg.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setitem(sg.USER_PROFILES, "Test", {"destination_email": "TEST_EMAIL"})
    monkeypatch.setenv("TEST_EMAIL", "test@example.com")
    monkeypatch.setenv("GMAIL_USER", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")

    assert sg.send_html_email("Test", "Subject", "<p>Hello</p>", "Hello") == "test@example.com"
    msg = message_from_bytes(FakeSMTP.sent[0].as_bytes(), policy=default_policy)
    assert msg["To"] == "test@example.com" and msg["From"] == "Finnbot <sender@example.com>"
    assert msg.get_body(("plain",)).get_content().strip() == "Hello"
    assert msg.get_body(("html",)).get_content().strip() == "<p>Hello</p>"


def test_send_html_email_needs_gmail_credentials(monkeypatch):
    monkeypatch.setitem(sg.USER_PROFILES, "Test", {"destination_email": "TEST_EMAIL"})
    monkeypatch.setenv("TEST_EMAIL", "test@example.com")
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setenv("GMAIL_USER", "sender@example.com")
    try:
        sg.send_html_email("Test", "Subject", "<p>Hello</p>")
    except ValueError as e:
        assert "GMAIL_APP_PASSWORD" in str(e)
    else:
        raise AssertionError("expected a ValueError")


def test_from_payload_renders_and_emails_a_saved_payload(tmp_path, monkeypatch):
    saved = tmp_path / "Test_2026-10-16_Pre-Market_0801.json"
    saved.write_text(json.dumps(payload()), encoding="utf-8")
    emails = []
    monkeypatch.setattr(dp, "send_html_email", lambda user, subject, html, text: emails.append((user, subject, html, text)))

    assert dp.send_saved_payload(saved) == f"Emailed Test's saved digest ({saved.name})."
    user, subject, html, text = emails[0]
    assert (user, subject) == ("Test", "[Test] Finnbot Pre-Market Digest 2026-10-16")
    assert "Your holdings" in html and "YOUR HOLDINGS" in text
    assert (ch.REPORTS_DIR / "Test_2026-10-16_Pre-Market.html").read_text(encoding="utf-8") == html

    assert "not emailed" in dp.send_saved_payload(saved, email=False)
    assert len(emails) == 1
