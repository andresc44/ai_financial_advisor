"""
Tests for the digest email: src/digest/create_html.py with email_template.html, send_gmail.py, and
digest_payload.py --from-payload. Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-17, at Andres's
request. Every payload here is made up; none of it is anyone's real portfolio.
"""
import json
from datetime import date
import re
from email import message_from_bytes
from email.policy import default as default_policy
from pathlib import Path

import pytest

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
        "holdings": [holding("AAA", "risky", 30.0), holding("BBB", "hold", 50.0),
                     holding("CCC", "urgent_sell", 5.0), holding("DDD", "profit_sell", 15.0)],
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
    # The connected account is a small dropdown at the foot of the overview card (UI pass, 2026-10-17)
    markers = ["Good morning, Test", "Your portfolio", "Needs attention", '<span class="nw">Questrade</span>&nbsp;&middot; <span class="nw">1 account</span>', "Portfolio risk",
               "Markets &amp; macro", "Top stories", "Your holdings", "Swing ideas", "Long-term ideas"]
    positions = [html.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_holdings_most_urgent_first_with_each_stock_on_one_card():
    view = ch.build_view(payload(holdings=[
        holding("BBB", "hold", 50.0), holding("AAA", "risky", 40.0, account="Individual TFSA"),
        holding("AAA", "risky", 40.0, account="Individual RRSP"), holding("CCC", "urgent_sell", 5.0),
        holding("DDD", "profit_sell", 15.0), holding("EEE", "risky", 25.0), holding("FFF", None, 1.0),
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
    assert portfolio["session"] == "Prev. day · Thu, Oct 15"  # With the day of the week (2026-10-18)
    assert portfolio["change"] == "▲ $20.00 · 0.50%"
    # Unrealized: a 10% gain on cost; flagged: urgent sell, risky, and profit sell (an urgent sell makes it red)
    assert [stat["value"] for stat in portfolio["stats"]] == ["+$400", "3 of 4", "2"]
    assert (portfolio["stats"][1]["sub"], portfolio["stats"][1]["tone"]) == ("50% of value", "neg")
    assert [chip["text"] for chip in portfolio["attention"]] == ["CCC", "AAA", "DDD"]  # Most urgent first
    assert [item["text"] for item in portfolio["earnings"]] == ["AAA Oct 21", "BBB Oct 21", "CCC Oct 21", "DDD Oct 21"]
    assert risk["beta"]["value"] == "1.40" and risk["beta"]["label"] == "Above-market risk"
    assert [(m["label"], m["pct"]) for m in risk["mix"]] == [
        ("Urgent sell", "5%"), ("Risky", "30%"), ("Profit sell", "15%"), ("Hold", "50%")]
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


def test_swing_card_names_its_setup_and_what_the_plan_rests_on():
    # 2026-10-20: the swing screen's setup and the plan's bases; a pick from before then has none of these rows
    pick = swing_pick(setup="pullback", stop_basis="0.5 ATR below the EMA 21",
                      target_basis="highest close of the last 20 days")
    card = ch.build_view(payload(recommendations={"top_long_term": [], "top_swing": [pick]}))["swing"][0]
    assert card["details"][:3] == [("Setup", "Dip in an uptrend"), ("Stop at", "0.5 ATR below the EMA 21"),
                                   ("Target at", "highest close of the last 20 days")]
    assert not {"Setup", "Stop at", "Target at"} & {label for label, _ in ch.build_view(payload())["swing"][0]["details"]}
    # A pick scored on one horizon only has no row for the other score
    lone = swing_pick(longScore=None)
    details = ch.build_view(payload(recommendations={"top_long_term": [], "top_swing": [lone]}))["swing"][0]["details"]
    assert "Long-term score" not in {label for label, _ in details}


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
    assert "No setups scored 75+ today" in html and "No ideas scored 85+ today" in html  # Minimums: 75 swing, 85 long-term
    # Empty sections are plain cards, with nothing to open; the only dropdowns left are the glossary's
    assert html.count("<details>") == 1 + len(view["glossary"])


def test_issues_are_shown_first(monkeypatch):
    monkeypatch.setitem(ch.params_dict["OPS"], "ALERT_USERS", ["Andres"])
    issues = dict(
        # user_message is worded for the key's owner, who reads the digest (here a non-admin)
        api_key_issues=[{"provider": "Gemini", "reason": "no_credits", "message": "The Gemini key is out of credits. Add credits.",
                         "user_message": "Your Gemini API key has no credits left. Contact Andres to address this issue."}],
        data_source_issues=[{"source": "Google Sheets", "reason": "sheet_empty", "message": "The holdings sheet has no rows."}],
        universal_issues=[{"source": "Finnhub", "reason": "missing_key", "message": "FINNHUB_API_KEY is missing."},
                          {"source": "Twelve Data", "reason": "plan_limit", "message": "The plan allows 8 credits."}],
    )
    # An admin sees every shared data problem as it was reported, and older issues without a contact line
    view, html = render(payload(user_name="Andres", **{**issues, "api_key_issues": []}))
    assert html.index("Some parts of this digest are incomplete") < html.index("Your portfolio")
    assert [issue["message"] for issue in view["issues"]] == [
        "The holdings sheet has no rows.", "FINNHUB_API_KEY is missing.", "The plan allows 8 credits."]
    # Anyone else sees their own problems with the contact line, and one plain line for the shared data sources
    view, html = render(payload(user_name="Steven", **issues))
    assert [issue["message"] for issue in view["issues"]] == [
        "Your Gemini API key has no credits left. Contact Andres to address this issue.",
        "The holdings sheet has no rows. Contact Andres to address this issue.",  # An older payload's issue
        "Finnhub and Twelve Data had problems this run, so some ideas or market data may be missing or incomplete. "
        "Contact Andres to address this issue.",
    ]
    assert "FINNHUB_API_KEY" not in html and "Add credits" not in html


def test_a_full_digest_fits_under_gmails_clipping_limit():
    full = payload(
        holdings=[holding(f"T{i:02d}", "risky" if i % 2 else "hold", 10.0) for i in range(10)],
        recommendations={"top_long_term": [long_pick("L1"), long_pick("L2")],
                         "top_swing": [swing_pick(f"S{i}") for i in range(5)]},
    )
    view = ch.build_view(full)
    html = ch.fit_for_gmail(view, TEMPLATE_DIR)
    assert len(html.encode("utf-8")) <= ch.GMAIL_CLIP_BYTES
    # 10 holdings with 3 news items each, 5 swing and 2 long-term ideas fit. Since the repeated inline styles
    # became classes (2026-10-19), nothing is left out, not even the glossary
    assert view["trimmed"] is False and view["glossary"]
    assert all(len(card["news"]) == 3 for card in view["holdings"])


def test_a_large_portfolio_drops_low_priority_news_to_fit():
    large = payload(holdings=[holding(f"T{i:02d}", "risky" if i < 4 else "hold", 5.0) for i in range(20)])
    view = ch.build_view(large)
    html = ch.fit_for_gmail(view, TEMPLATE_DIR)
    assert len(html.encode("utf-8")) <= ch.GMAIL_CLIP_BYTES
    assert view["trimmed"] is True and "left out to keep this email under Gmail" in html
    alerts, holds = view["holdings"][:4], view["holdings"][4:]
    assert all(card["news"] for card in alerts)  # Positions that need attention keep their top news
    assert all(len(card["news"]) < 3 for card in holds)
    assert all(card["rationale"] for card in view["holdings"])  # Reasoning is never dropped

    amp_view = ch.build_view(large)  # The AMP version is trimmed the same way, toward its own target
    amp = ch.render_amp(amp_view, TEMPLATE_DIR)
    assert len(amp.encode("utf-8")) <= ch.AMP_TARGET_BYTES and amp_view["trimmed"] is True


def test_a_portfolio_too_big_for_gmail_still_renders_and_warns(capsys):
    huge = payload(holdings=[holding(f"T{i:02d}", "hold", 1.0) for i in range(60)])
    view = ch.build_view(huge)
    html = ch.fit_for_gmail(view, TEMPLATE_DIR)
    ch.verify_output(html, view)
    assert all(card["news"] == [] and card["rationale"] for card in view["holdings"])
    assert "Gmail clips emails" in capsys.readouterr().out


def test_plain_text_version_covers_every_section():
    text = ch.render_text(ch.build_view(payload()))
    for expected in ("Good morning, Test.", "PORTFOLIO (USD) $4,000.00", "PORTFOLIO RISK", "CONNECTED ACCOUNT Questrade",
                     "KEY MARKET SIGNALS", "The central bank held rates.", "CCC - Urgent sell", "SWING IDEAS", "SWA (score 85)",
                     "LONG-TERM IDEAS", "LTA (score 91)"):
        assert expected in text, expected
    assert "investment advice" not in text  # Removed at Andres's request (2026-10-17)


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


def test_send_html_email_puts_the_amp_part_before_the_html(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(sg.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setitem(sg.USER_PROFILES, "Test", {"destination_email": "TEST_EMAIL"})
    monkeypatch.setenv("TEST_EMAIL", "test@example.com")
    monkeypatch.setenv("GMAIL_USER", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")

    sg.send_html_email("Test", "Subject", "<p>Hello</p>", "Hello", amp="<html amp4email>Hello</html>")
    msg = message_from_bytes(FakeSMTP.sent[0].as_bytes(), policy=default_policy)
    assert [part.get_content_type() for part in msg.iter_parts()] == ["text/plain", "text/x-amp-html", "text/html"]


def test_charts_for_every_stock_except_non_us_holdings():
    view = ch.build_view(payload(holdings=[holding("AAA"), holding("XYZ.DE", currency="EUR")]))
    charts = {card["symbol"]: card["chart"] for card in view["holdings"]}
    assert charts["XYZ.DE"] is None  # Finviz covers US listings only
    assert charts["AAA"]["src"] == "https://finviz.com/chart.ashx?t=AAA&ty=c&ta=1&p=d&s=l&v=20261016pre"
    assert view["swing"][0]["chart"]["src"].startswith("https://finviz.com/chart.ashx?t=SWA&")
    assert view["long_term"][0]["chart"]["link"] == "https://finviz.com/quote.ashx?t=LTA"
    html = ch.render_html(view, TEMPLATE_DIR)
    assert html.count('<img src="https://finviz.com/chart.ashx?') == 3  # AAA, SWA, LTA
    assert "t=AAA&amp;ty=c" in html  # & escaped inside the attribute


def test_amp_version_uses_amp_markup_only():
    view = ch.build_view(payload())
    amp = ch.render_amp(view, TEMPLATE_DIR)
    assert amp.startswith("<!DOCTYPE html><html amp4email data-css-strict")  # 2026-10-22: no line breaks before block tags
    assert '<meta charset="utf-8"><script async src="https://cdn.ampproject.org/v0.js"></script>' in amp
    assert "<style amp4email-boilerplate>body{visibility:hidden}</style>" in amp
    assert amp.count("<amp-accordion") == html_folds(view) and "<amp-img" in amp  # Same dropdowns as the HTML
    for disallowed in ("<details", "<summary", "<img", "!important", "<!--[if", "mso-", "<meta name="):
        assert disallowed not in amp, disallowed
    assert "<section expanded" not in amp and amp.count("<section") == amp.count("<amp-accordion")  # All collapsed


def html_folds(view):
    """How many dropdowns the HTML version has for this view."""
    return ch.render_html(view, TEMPLATE_DIR).count("<details")


def flat(parts):
    """A collapsed section's summary parts as the text they show."""
    return " · ".join(part["prefix"] + part["text"] + part["suffix"] for part in parts)


def test_main_sections_collapse_to_a_summary(monkeypatch):
    view = ch.build_view(payload())
    assert flat(view["risk"]["summary"]) == "Beta 1.40 · Above-market risk · 1 to watch"
    assert view["risk"]["summary"][1]["tone"] == "warn"
    assert view["account"]["summary"] == "Questrade · 1 account · 4 positions"
    assert flat(view["markets_summary"]) == "S&P 500 +0.3% prev. day · 1 story"
    assert view["markets_summary"][0]["tone"] == "pos"  # Only the change is coloured, not "S&P 500"
    assert flat(view["holdings_summary"]) == "1 urgent sell · 1 risky · 1 profit sell · 1 hold"
    assert [part["dot"] for part in view["holdings_summary"]] == ["#DC2626", "#F97316", "#F59E0B", "#38BDF8"]
    assert (flat(view["swing_summary"]), flat(view["long_summary"])) == ("SWA 85", "LTA 91")

    amp = ch.render_amp(view, TEMPLATE_DIR)
    html = ch.render_html(view, TEMPLATE_DIR)
    # Six main sections, a row per holding and idea, and the dropdowns inside them, all collapsed
    assert "<section expanded>" not in amp and "<details open>" not in html
    assert amp.count("<amp-accordion") == html.count("<details>")
    swing_section_start = html.rfind("<details>", 0, html.index(">Swing ideas "))
    holdings_section = html[html.index(">Your holdings "):swing_section_start]
    assert holdings_section.count("<details>") == 4 * 3  # Each holding's row, reasoning, and news (the chart is in the row)

    monkeypatch.setitem(ch.params_dict["DIGEST"], "OPEN_SECTIONS", ["holdings", "long_term"])
    view = ch.build_view(payload())
    assert ch.render_html(view, TEMPLATE_DIR).count("<details open>") == 2
    assert ch.render_amp(view, TEMPLATE_DIR).count("<section expanded>") == 2


def test_overview_chips_open_their_holding_in_the_amp_version():
    view = ch.build_view(payload())
    amp = ch.render_amp(view, TEMPLATE_DIR)
    # Tapping CCC's chip opens Your holdings and CCC's row (closed first, so its expand event always fires)...
    assert ('on="tap:acc-s-holdings.expand,acc-h-ccc.collapse,acc-h-ccc.expand">CCC</button>') in amp
    # ...and the row, once open, focuses its own header, which scrolls it into view and highlights it
    assert '<amp-accordion animate id="acc-h-ccc"><section on="expand:hdr-h-ccc.focus"><header class="fh p12" id="hdr-h-ccc">' in amp
    assert '<amp-accordion animate id="acc-s-holdings">' in amp
    # 2026-10-20: the row's Why and News dropdowns handle their own expand event; AMP otherwise passes it up to the
    # row, whose focus action jumped the page ~650px up to the row's header when that was scrolled off screen
    for part in ("why", "news"):
        assert (f'<section on="expand:hdr-h-ccc-{part}.focus"><header class="fh p6" id="hdr-h-ccc-{part}">') in amp
    assert amp.count("<button") == len(view["portfolio"]["attention"]) + len(view["portfolio"]["earnings"])
    # The HTML version keeps plain chips: jump links don't work in the Gmail app, nor into closed dropdowns
    html = ch.render_html(view, TEMPLATE_DIR)
    assert "<button" not in html and " on=" not in html



def test_a_cards_price_is_the_one_its_value_and_day_change_use():
    # 2026-10-20: outside regular hours the broker's price is an extended-hours quote; the card shows valuation_price,
    # the session close its value and daily change are measured at (payloads without it fall back to current_price)
    pre_market = holding("SOXL", value=749.36, current_price=103.39, valuation_price=93.67)
    card = ch.build_view(payload(holdings=[pre_market]))["holdings"][0]
    assert card["stats"][0]["value"] == "$93.67"
    older = holding("SOXL", value=749.36, current_price=93.67)
    assert ch.build_view(payload(holdings=[older]))["holdings"][0]["stats"][0]["value"] == "$93.67"


def test_four_figure_boxes_form_a_2x2_grid():
    # Four boxes side by side ran past a phone's right edge in the Gmail app (2026-10-17), so they wrap 2x2;
    # up to three stay in one row
    html = ch.render_html(ch.build_view(payload(holdings=[holding("AAA")])), TEMPLATE_DIR)
    row_start = html.rfind("<details>", 0, html.index(">AAA<"))
    grid = html[html.index('<table role="presentation" width="100%" class="sg">', row_start):]
    grid = grid[:grid.index("</table>")]
    assert grid.count("<tr>") == 2 and grid.count('class="st w2') == 4  # 2026-10-22: widths as classes (w2 = 50%)
    overview = html[html.index("Your portfolio"):html.index(">Portfolio risk<")]
    assert overview.count('class="st w3') == 3


def test_a_stocks_chart_shows_as_soon_as_its_row_opens():
    html = ch.render_html(ch.build_view(payload(holdings=[holding("AAA")])), TEMPLATE_DIR)
    assert ">Chart<" not in html  # No dropdown of its own
    row_start = html.rfind("<details>", 0, html.index(">AAA<"))
    reasoning = html.index(">Why hold<", row_start)
    assert row_start < html.index('<img src="https://finviz.com/chart.ashx?t=AAA', row_start) < reasoning


def test_review_notes_and_stories_are_rows_with_titles():
    review = [
        {"category": "WATCH", "title": "Leveraged ETFs drive a high beta", "text": "SPXL and SOXL are 43% of value."},
        {"category": "WATCH", "text": "Industrials sector exposure is heavily concentrated at 31.53% across CHRW (13.29%), "
                                      "AAON (9.77%), and CAT (8.47%). All three are below their EMAs."},
    ]
    view = ch.build_view(payload(portfolio_review=review))
    titles = [note["title"] for note in view["risk"]["watch"]]
    # Gemini's title when there is one; otherwise the first sentence, cut at a bracket and shortened at a word
    assert titles == ["Leveraged ETFs drive a high beta", "Industrials sector exposure is heavily concentrated at 31.53%…"]
    assert flat(view["markets_summary"]) == "S&P 500 +0.3% prev. day · 1 story"

    html = ch.render_html(view, TEMPLATE_DIR)
    for title in titles + ["The central bank held rates."]:
        summary_start = html.rfind("<summary", 0, html.index(title))
        assert html.index("</summary>", summary_start) > html.index(title)  # The title is the row's header
    # 2026-10-21: no 1-day / 5-day / 1-month table any more; each signal's name opens a chart of its month
    # 2026-10-21: the key signals first, the rest under "All indicators"; each name opens its chart
    assert html.index(">Key signals<") < html.index(">All indicators<") and ">Why it matters<" in html
    assert ('<a href="https://www.google.com/finance/quote/SPY:NYSEARCA?window=1M" class="qa">'
            'S&amp;P 500</a>') in html
    # 2026-10-21: the signals it has data for: the key ones first, the rest in their groups
    assert [s["name"] for s in view["market"]["key"]] == ["S&P 500", "S&P breadth", "10Y yield"]
    assert [(g["name"], [s["name"] for s in g["signals"]]) for g in view["market"]["groups"]] == [
        ("Sentiment", ["Fear & Greed"]), ("Macro", ["10Y–2Y spread"])]


def macro_pulse(**changes):
    """A full dashboard (macro_indicators.get_market_dashboard_data's shape) on a calm, healthy day."""
    def instrument(latest, d1="+0.20%", d5="+1.00%", m1="+2.00%", **extra):
        return {"Latest": latest, "1D %": d1, "5D %": d5, "1M %": m1, **extra}
    yahoo = {
        "S&P 500 (SPY)": instrument(700.0, **{"50D MA": 690.0, "200D MA": 650.0}),
        "Nasdaq-100 (QQQ)": instrument(600.0, d5="+1.20%"), "Dow Jones (DIA)": instrument(450.0, d5="+0.90%"),
        "NYSE Composite ($NYA)": instrument(20000.0, d5="+0.80%"), "Russell 2000 (IWM)": instrument(240.0, d5="+1.30%"),
        "VIX Index": instrument(15.0, d1="-3.00%"), "US Dollar Index (DXY)": instrument(98.0, d5="-0.50%"),
        "10Y Treasury Yield ($TNX)": instrument(4.1, **{"1D bps": 2.0, "5D bps": -4.0, "1M bps": 10.0}),
        "Crude Oil (WTI)": instrument(70.0, d5="+2.00%"),
    }
    for name, values in changes.items():
        yahoo[name] = {**yahoo[name], **values}
    return {"yahoo_finance": yahoo,
            "yield_curve_10y_2y": {"Spread": "0.45%", "1D Change": "+2.0 bps", "5D Change": "+5.0 bps"},
            "fear_and_greed": {"Score": 65.0, "Rating": "Greed", "Previous close": 62.0},
            "sp500_breadth": {"% > 50MA": "66.00%", "1D Change": 3.0, "5D Change": -2.0, "1M Change": 8.0},
            "put_call_ratio": {"Ratio": 0.65, "Session": "2026-10-15", "5D Avg": 0.72}}


def signals_of(dashboard, today=date(2026, 10, 16)):
    return {sig["name"]: (sig["tone"], sig["text"], sig["value"], sig["sub"]) for sig in ch._signals(dashboard, "day", today)}


def test_market_signals_on_a_calm_healthy_day():
    # Claude-authored (2026-10-21): graded at params_dict["MARKET_SIGNALS"] (researched levels)
    signals = signals_of(macro_pulse())
    assert list(signals) == ["S&P 500", "Nasdaq-100 vs Dow", "NYSE vs S&P 500", "Russell 2000 vs S&P", "Market mood",
                             "VIX", "Put/call ratio", "Fear & Greed", "S&P breadth", "US Dollar (DXY)", "10Y yield",
                             "10Y–2Y spread", "Crude oil (WTI)"]
    assert signals["S&P 500"] == ("pos", "Uptrend: above its 50- and 200-day averages", "$700.00", "+0.2% day")
    assert signals["Nasdaq-100 vs Dow"][:3] == ("flat", "Even: growth and value moved together", "+0.3 pts")  # Under 2
    assert signals["NYSE vs S&P 500"][:2] == ("pos", "Broad: in step with the S&P: wide participation")
    assert signals["Russell 2000 vs S&P"][:2] == ("flat", "In step: small caps moved with the S&P")
    assert signals["Market mood"][0] == "pos" and signals["Market mood"][1].startswith("Confident: Low volatility")
    assert signals["Market mood"][2:] == ("VIX 15.0", "F&G 65")
    assert signals["VIX"] == ("pos", "Calm: under 20, its long-run average", "15.00", "-3.0% day")
    # Heavy call buying is over-exuberance: a caution, not a good sign
    assert signals["Put/call ratio"] == ("warn", "Complacent: under 0.70: heavy call buying, often over-exuberance "
                                                 "(Oct 15 session)", "0.65", "avg 0.72")
    assert signals["Fear & Greed"] == ("pos", "Greed: investors are buying", "65", "was 62")
    assert signals["S&P breadth"][:3] == ("pos", "Strong: 66% of S&P 500 stocks above their 50-day average", "66%")
    assert signals["US Dollar (DXY)"][:2] == ("flat", "Steady: no big move, -0.5% in 5 days")  # Under the 1% fall
    assert signals["10Y yield"] == ("flat", "Steady: no big move, +10 bps in a month", "4.10%", "+2 bps day")
    assert signals["10Y–2Y spread"][:3] == ("pos", "Positive: no recession signal", "+0.45%")
    assert signals["Crude oil (WTI)"][:2] == ("flat", "Calm: no sharp weekly move, +2.0% in 5 days")


def test_market_signals_on_a_stressed_day():
    dashboard = macro_pulse(**{
        "S&P 500 (SPY)": {"Latest": 640.0, "5D %": "-2.00%"}, "Nasdaq-100 (QQQ)": {"5D %": "-4.00%"},
        "Dow Jones (DIA)": {"5D %": "-1.00%"}, "NYSE Composite ($NYA)": {"5D %": "-4.50%"},
        "Russell 2000 (IWM)": {"5D %": "-4.50%"}, "VIX Index": {"Latest": 31.0}, "US Dollar Index (DXY)": {"5D %": "+1.60%"},
        "10Y Treasury Yield ($TNX)": {"1M bps": 32.0}, "Crude Oil (WTI)": {"5D %": "+10.50%"}})
    dashboard.update(fear_and_greed={"Score": 18.0, "Rating": "extreme fear"}, put_call_ratio={"Ratio": 1.12},
                     sp500_breadth={"% > 50MA": "31.00%"}, yield_curve_10y_2y={"Spread": "-0.12%"})
    signals = signals_of(dashboard)
    assert signals["S&P 500"][:2] == ("neg", "Downtrend: below its 50- and 200-day averages")
    assert signals["Nasdaq-100 vs Dow"][:3] == ("warn", "Defensive: value stocks beat growth", "-3.0 pts")
    assert signals["NYSE vs S&P 500"][:3] == ("warn", "Concentrated: the S&P beat the broad market: a few giants "
                                                      "carry it", "-2.5 pts")
    assert signals["Russell 2000 vs S&P"][:2] == ("neg", "Credit warning: small caps lagged the S&P")
    assert signals["Market mood"][:2] == ("neg", "Capitulation: Fear on both gauges: the worst of a sell-off, which has "
                                                 "often come near short-term bottoms for patient buyers.")
    assert signals["VIX"][:2] == ("neg", "High fear: over 30: big swings expected")
    assert signals["Put/call ratio"][:2] == ("neg", "Fearful: over 1.00: heavy put buying")
    assert signals["Fear & Greed"][:2] == ("neg", "Extreme fear: often near a bottom for patient buyers")
    assert signals["S&P breadth"][0] == "neg"
    assert signals["US Dollar (DXY)"][:2] == ("neg", "Rising fast: a headwind for stocks, +1.6% in 5 days")
    assert signals["10Y yield"][:2] == ("neg", "Rising: pressure on stock valuations, +32 bps in a month")
    assert signals["10Y–2Y spread"][:2] == ("neg", "Inverted: a recession warning")
    assert signals["Crude oil (WTI)"][:2] == ("neg", "Spiking: an inflation risk, +10.5% in 5 days")
    # A curve that turned positive within 6 months of being inverted is a caution; longer ago, it isn't
    dashboard["yield_curve_10y_2y"] = {"Spread": "0.08%", "Last inverted": "2026-08-03"}
    assert signals_of(dashboard)["10Y–2Y spread"][:2] == (
        "warn", "Recently un-inverted: positive since Aug 3: historically close to recessions")
    assert signals_of(dashboard, today=date(2027, 3, 1))["10Y–2Y spread"][0] == "pos"
    # Extreme greed is froth: a caution
    dashboard["fear_and_greed"] = {"Score": 80.0}
    assert signals_of(dashboard)["Fear & Greed"][:2] == ("warn", "Extreme greed: froth: often near a top")


def test_key_signals_come_first_and_the_rest_are_under_all_indicators():
    # Claude-authored (2026-10-21): MARKET_SIGNALS["KEY_SIGNALS"], each with its grade; each name opens its month
    view, html = render(payload(market_data=macro_pulse()))
    assert [sig["name"] for sig in view["market"]["key"]] == ["S&P 500", "Market mood", "S&P breadth", "10Y yield",
                                                             "Nasdaq-100 vs Dow"]
    assert [(g["name"], [sig["name"] for sig in g["signals"]]) for g in view["market"]["groups"]] == [
        ("Benchmarks", ["NYSE vs S&P 500", "Russell 2000 vs S&P"]),
        ("Sentiment", ["VIX", "Put/call ratio", "Fear & Greed"]),
        ("Macro", ["US Dollar (DXY)", "10Y–2Y spread", "Crude oil (WTI)"])]
    key, rest = html.index(">Key signals<"), html.index(">All indicators<")
    mood_row = html.index(">Market mood<")
    assert key < mood_row < html.index('<b class="pos">Confident</b>', mood_row) < rest
    assert rest < html.index("VIX jumps within hours of a shock") < html.index('class="qa">VIX</a>')
    assert html.count('<span class="dot dp"></span>') == 7  # S&P, NYSE, mood, VIX, Fear & Greed, breadth, spread
    links = {sig["name"]: sig["link"] for sig in view["market"]["signals"]}
    # Google Finance at 1 month where it has the series (an "A vs B" opens A), then Yahoo, Barchart, FRED, and CNN
    google = "https://www.google.com/finance/quote/{}?window=1M"
    assert links["Nasdaq-100 vs Dow"] == google.format("QQQ:NASDAQ") and links["VIX"] == google.format("VIX:INDEXCBOE")
    assert links["Crude oil (WTI)"] == google.format("CLW00:NYMEX")
    assert links["US Dollar (DXY)"] == "https://finance.yahoo.com/quote/DX-Y.NYB/"
    assert links["S&P breadth"] == "https://www.barchart.com/stocks/quotes/$S5FI/overview"
    assert links["10Y–2Y spread"] == "https://fred.stlouisfed.org/series/T10Y2Y"
    assert links["Fear & Greed"] == links["Market mood"] == links["Put/call ratio"] == "https://www.cnn.com/markets/fear-and-greed"
    assert "stockcharts" not in html
    assert all(links.values()) and html.count('class="qa"') == len(links)
    text = ch.render_text(view)
    assert text.index("KEY MARKET SIGNALS") < text.index("  🟢 Market mood VIX 15.0 (F&G 65): Confident: ")
    assert "  🟢 VIX 15.00 (-3.0% prev. day): Calm: under 20, its long-run average" in text


def test_market_mood_reads_vix_and_fear_greed_together():
    # Claude-authored (2026-10-21): MARKET_SIGNALS["MOOD_MATRIX"], VIX state by Fear & Greed state
    def mood(vix, score):
        dashboard = macro_pulse(**{"VIX Index": {"Latest": vix}})
        dashboard["fear_and_greed"] = {"Score": score}
        return ch._mood(dashboard)
    assert (mood(15, 70)["grade"], mood(15, 70)["tone"]) == ("Confident", "pos")
    assert mood(15, 30)["grade"] == "Quietly cautious" and mood(25, 50)["grade"] == "Unsettled"
    assert mood(35, 70)["grade"] == "Fresh shock"  # VIX spiked before the slower gauges turned
    assert (mood(35, 20)["grade"], mood(35, 20)["tone"]) == ("Capitulation", "neg")
    assert ch._mood({"yahoo_finance": {}, "fear_and_greed": {"Score": 50}}) is None  # Needs both
    # Its label replaces the VIX level in the collapsed section's line
    view, _ = render(payload(market_data=macro_pulse()))
    assert flat(view["markets_summary"]).startswith("S&P 500 +0.2% prev. day · Mood: Confident")


def test_the_heat_map_button_is_under_the_greeting():
    # Claude-authored (2026-10-21): Finviz's S&P 500 map over the past week (DIGEST.HEAT_MAP_URL), for every reader
    for digest_payload in (payload(), payload(holdings=[], user_name="Koby")):
        view, html = render(digest_payload)
        link = '<a href="https://finviz.com/map?t=sec&amp;st=w1" class="btn">S&amp;P 500 heat map · 1 week</a>'
        assert html.index(view["greeting"]) < html.index(link) < html.index("Markets &amp; macro")
        assert "S&P 500 heat map · 1 week: https://finviz.com/map?t=sec&st=w1" in ch.render_text(view)


def test_short_phrases_are_kept_on_one_line():
    # Claude-authored (2026-10-21): where text wraps, a whole " · " phrase moves to the next line
    assert ch._phrases("$8,466.64 USD · 1 AUD = 0.702 USD") == (
        '<span class="nw">$8,466.64 USD</span>&nbsp;&middot; <span class="nw">1 AUD = 0.702 USD</span>')
    long = "Short-term setups: days to a few weeks"  # Longer than a phone's line at enlarged text: may wrap
    assert ch._phrases(f"{long} · Prices in USD") == f'{long}&nbsp;&middot; <span class="nw">Prices in USD</span>'
    assert ch._phrases("S&P <b> · x") == '<span class="nw">S&amp;P &lt;b&gt;</span>&nbsp;&middot; <span class="nw">x</span>'
    view, html = render(payload())
    # The greeting is 21 px, so "Good afternoon," fits on one line at Andres's text size without being forced to
    # (forced, it overflowed narrower phones); the pill's session date stays whole
    assert 'font-size:21px;line-height:27px;' in html and "Good morning, Test" in html
    assert f'<span class="nw">{view["portfolio"]["session"]}</span>' in html
    # Short summary parts are whole; "Tap a stock" and the cost note are gone
    amp = ch.render_html(view, TEMPLATE_DIR, amp=True)
    assert '<span class="nw">1 story</span>' in html and "Tap a stock" not in amp
    # A stock row's name line may break inside a long word, only when nothing else fits (very large text)
    assert html.count('<div class="xs mu t2 ow">') == len(view["holdings"]) + len(view["swing"]) + len(view["long_term"])
    assert ".ow{overflow-wrap:anywhere;}" in html and ".ow{overflow-wrap:anywhere;}" in amp


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
    monkeypatch.setitem(ch.params_dict["DIGEST"], "AMP_EMAIL", True)
    monkeypatch.setattr(dp, "send_html_email",
                        lambda user, subject, html, text, amp=None: emails.append((user, subject, html, text, amp)))

    assert dp.send_saved_payload(saved) == f"Emailed Test's saved digest ({saved.name})."
    user, subject, html, text, amp = emails[0]
    assert (user, subject) == ("Test", "[Test] Finnbot Pre-Market Digest 2026-10-16")
    assert "Your holdings" in html and "YOUR HOLDINGS" in text and "amp4email" in amp
    assert (ch.REPORTS_DIR / "Test_2026-10-16_Pre-Market.html").read_text(encoding="utf-8") == html
    assert (ch.REPORTS_DIR / "Test_2026-10-16_Pre-Market.amp.html").read_text(encoding="utf-8") == amp

    assert "not emailed" in dp.send_saved_payload(saved, email=False)
    assert len(emails) == 1


def test_daily_changes_say_prev_day_before_the_open_and_day_after_the_close():
    pre = ch.build_view(payload())
    after_close = payload(period="Post-Market", subject="Finnbot Post-Market Digest 2026-10-16")
    for lot in after_close["holdings"]:
        lot["daily_pnl_session"] = "2026-10-16"  # After the close, the day's own session
    post = ch.build_view(after_close)
    assert (pre["day_label"], post["day_label"]) == ("Prev. day", "Day")
    assert pre["portfolio"]["session"] == "Prev. day · Thu, Oct 15" and post["portfolio"]["session"] == "Day · Fri, Oct 16"
    assert pre["holdings"][0]["stats"][0]["sub"] == "+0.50% prev. day"
    assert post["holdings"][0]["stats"][0]["sub"] == "+0.50% day"
    assert pre["holdings_note"].endswith("Prev. day = the Oct 15 session")
    assert post["holdings_note"].endswith("Day = the Oct 16 session")
    assert flat(pre["markets_summary"]).startswith("S&P 500 +0.3% prev. day")
    assert pre["market"]["signals"][0]["sub"] == "+0.3% prev. day"
    assert [s["sub"] for s in pre["market"]["signals"] if s["name"] == "10Y–2Y spread"] == ["-4 bps prev. day"]
    assert "(+0.50% prev. day)" in pre["preheader"]
    pre_html, post_html = ch.render_html(pre, TEMPLATE_DIR), ch.render_html(post, TEMPLATE_DIR)
    assert "S&amp;P 500 +0.3% prev. day" in pre_html.replace('<b class="pos">', "").replace("</b>", "")
    post["glossary"] = []  # The glossary defines both terms
    assert "prev. day" not in ch.render_html(post, TEMPLATE_DIR).lower()
    assert "S&P 500 $700.00 (+0.3% day)" in ch.render_text(post)


def test_the_glossary_is_static_and_left_out_first():
    view = ch.build_view(payload())
    from src.digest.glossary import GLOSSARY
    assert [g["category"] for g in view["glossary"]] == [category for category, _ in GLOSSARY]
    html = ch.render_html(view, TEMPLATE_DIR)
    assert html.index(">Glossary<") > html.index(">Long-term ideas ")  # At the very bottom
    assert "<b>RSI 14</b><br>Relative Strength Index" in html and "<b>Beta</b><br>" in html
    # 2026-10-21: "About this digest" goes first, then the glossary
    assert [step.__name__ for step in ch.TRIM_STEPS[:2]] == ["_drop_about", "_drop_glossary"]
    trimmed = ch.build_view(payload(screening={"stages": [{"stage": "listings", "count": 7012}]}))
    ch._drop_about(trimmed)
    assert trimmed["usage"] == [] and trimmed["screening"] == []
    assert "About this digest" not in ch.render_html(trimmed, TEMPLATE_DIR)


def test_currencies_come_from_the_payload_or_default_to_usd(monkeypatch):
    monkeypatch.setattr(ch, "usd_rates", lambda codes: {"USD": 1.0, "EUR": 1.10})
    view = ch.build_view(payload(holdings=[holding("AAA"), holding("XYZ.DE", currency="EUR"), holding("NOC", currency=None)]))
    cards = {card["symbol"]: card for card in view["holdings"]}
    # 2026-10-21: each card in its stock's own currency (Andres's request); only the overview converts
    assert (cards["AAA"]["stats"][0]["label"], cards["AAA"]["stats"][0]["value"]) == ("Price · USD", "$100.00")
    assert (cards["XYZ.DE"]["stats"][0]["label"], cards["XYZ.DE"]["stats"][0]["value"]) == ("Price · EUR", "€100.00")
    assert cards["XYZ.DE"]["value"] == "€1,000.00" and cards["XYZ.DE"]["stats"][1]["label"] == "Unrealized · EUR"
    assert "Listed in" not in cards["XYZ.DE"]["meta"]  # No conversion on the card, so no rate to explain
    assert cards["NOC"]["stats"][0]["label"] == "Price · USD" and cards["NOC"]["currency_assumed"] is True
    assert view["portfolio"]["label"] == "Your portfolio · USD"
    assert "Values in each stock's currency" in view["holdings_note"]
    usd_only = ch.build_view(payload(holdings=[holding("AAA"), holding("BBB")]))
    assert "Values in USD" in usd_only["holdings_note"]
    # The ideas and the market dashboard are US-listed, so always USD and never noted; API usage without a
    # currency (older payloads) is assumed USD and noted
    assert view["ideas_currency"] == "USD" and "Prices in USD" in ch.render_html(view, TEMPLATE_DIR)
    assert view["market"]["rows"][0]["last"] == "$700.00"  # SPY's share price
    assert view["currency_notes"] == [
        "holdings without a currency, shown as USD: NOC",
        "API usage has no currency; costs are shown as USD",
    ]
    assert dict(view["usage"])["This run · you"].startswith("$0.30 USD")  # An older payload: no admin side
    with_currency = payload(api_usage={"currency": "USD", "this_digest": {"total_cost_usd": 0.3}})
    assert ch.build_view(with_currency)["currency_notes"] == []


def test_holdings_in_other_currencies_show_their_own_currency_and_add_up_in_usd(monkeypatch):
    monkeypatch.setattr(ch, "usd_rates", lambda codes: {"USD": 1.0, "CAD": 0.70, "AUD": 0.65, "GBX": 0.0125,
                                                        "EUR": 1.10, "COP": 0.00025})
    view = ch.build_view(payload(holdings=[
        holding("AAA", value=1000.0),
        # CAD the payload already converted at 0.72: money amounts in USD at that rate, prices still in CAD
        holding("ENB.TO", value=720.0, currency="CAD", fx_rate_to_usd=0.72, current_price=50.0, last_close=49.0,
                total_cost_basis=648.0),
        # AUD the payload couldn't convert (no rate): money amounts and prices still in AUD
        holding("BHP.AX", value=100.0, currency="AUD", current_price=40.0, total_cost_basis=90.0),
        holding("VOD.L", value=100.0, currency="GBX", fx_rate_to_usd=0.0125, current_price=80.0, total_cost_basis=90.0),
        # 2026-10-21: EUR and COP too. COP the payload converted at 0.00024: a 2,800-peso share, $100 of them
        holding("SAP.DE", value=110.0, currency="EUR", fx_rate_to_usd=1.10, current_price=200.0, total_cost_basis=99.0),
        holding("ECOPETROL.CL", value=96.0, currency="COP", fx_rate_to_usd=0.00024, current_price=2800.0,
                total_cost_basis=72.0),
    ]))
    cards = {card["symbol"]: card for card in view["holdings"]}
    enb, bhp, vod, sap = cards["ENB.TO"], cards["BHP.AX"], cards["VOD.L"], cards["SAP.DE"]
    eco = cards["ECOPETROL.CL"]
    # 2026-10-21: each card in the stock's own currency, back from the USD amounts at the rate they were
    # converted at: $700 at 0.70 is C$1,000
    assert (enb["value"], enb["stats"][0]["label"], enb["stats"][0]["value"]) == ("C$1,000.00", "Price · CAD", "C$50.00")
    assert "Listed in" not in enb["meta"] and enb["chart"] is None
    assert (bhp["value"], bhp["stats"][0]["value"]) == ("A$100.00", "A$40.00")  # The payload's own AUD
    # London: the price in pence as quoted, the value and P/L in pounds
    assert (vod["value"], vod["stats"][0]["label"], vod["stats"][0]["value"]) == ("£80.00", "Price · pence", "80p")
    assert vod["stats"][1]["label"] == "Unrealized · GBP"
    assert (sap["value"], sap["stats"][0]["label"], sap["stats"][0]["value"]) == ("€100.00", "Price · EUR", "€200.00")
    # COP in whole pesos
    assert (eco["value"], eco["stats"][0]["value"]) == ("COP$400,000", "COP$2,800")
    # $25 at 0.00025 is 100,000 pesos; a card's amounts past 11 characters are shortened to fit a phone
    assert eco["stats"][1]["value"] == "+COP$100K"
    assert (ch._card_money(3579200, "COP"), ch._card_money(14196.47, "CAD")) == ("COP$3.58M", "C$14,196.47")
    assert enb["stats"][1]["value"] == "+C$100.00"  # (720 - 648) CAD-in-USD at 0.72 is C$100
    # The weights are recomputed from the USD values
    assert round(sum(card["weight_pct"] for card in view["holdings"]), 1) == 100.0
    assert enb["weight_pct"] == round(700 / (1000 + 700 + 65 + 100 + 110 + 100) * 100, 2)
    assert "converted CAD holdings to USD at 1 CAD = 0.700 USD" in view["fx_notes"]
    assert "converted COP holdings to USD at 1 USD = 4,000 COP" in view["fx_notes"]
    assert view["issues"] == []


def test_a_holding_without_any_exchange_rate_stays_in_its_currency_and_says_so(monkeypatch):
    monkeypatch.setattr(ch, "usd_rates", lambda codes: {"USD": 1.0, "CAD": None})
    view = ch.build_view(payload(holdings=[holding("AAA"), holding("ENB.TO", currency="CAD")]))
    enb = {card["symbol"]: card for card in view["holdings"]}["ENB.TO"]
    assert enb["stats"][0]["label"] == "Price · CAD"
    assert [issue["title"] for issue in view["issues"]] == ["Exchange rate"]
    assert "ENB.TO" in view["issues"][0]["message"]


def mixed_currency_payload(**overrides):
    """
    Holdings as PortfolioFetcher.convert_to_usd leaves them: money amounts in USD at the payload's rate, per-share
    prices in the listing's currency. Each moved from last_close to valuation_price over the session.
    """
    rows = []
    for symbol, code, rate, shares, price, last_close, cost in (
            ("AAPL", "USD", 1.0, 10.0, 250.0, 245.0, 2000.0), ("SHOP.TO", "CAD", 0.72, 20.0, 150.0, 155.0, 2500.0),
            ("SAP.DE", "EUR", 1.08, 5.0, 200.0, 198.0, 900.0), ("ECOPETROL.CL", "COP", 0.00024, 4000.0, 2800.0, 2750.0, 2.4e3),
            ("BHP.AX", "AUD", 0.66, 30.0, 45.0, 44.0, 800.0), ("VOD.L", "GBX", 0.0133, 1000.0, 120.0, 121.0, 1500.0)):
        rows.append(holding(
            symbol, shares=shares, currency=code, fx_rate_to_usd=rate, current_price=price, valuation_price=price,
            last_close=last_close, last_close_usd=last_close * rate, market_value=shares * price * rate,
            daily_pnl=(price - last_close) * shares * rate, daily_pnl_pct=(price / last_close - 1) * 100,
            total_cost_basis=cost, unrealized_pnl=shares * price * rate - cost))
    return payload(holdings=rows, **overrides)


RATES_NOW = {"USD": 1.0, "CAD": 0.70, "EUR": 1.10, "COP": 0.00025, "AUD": 0.65, "GBX": 0.0130, "GBP": 1.30}


def test_pnl_adds_up_across_currencies_after_restating_at_the_current_rate(monkeypatch):
    # Claude-authored (2026-10-21): the P/L check for converted holdings. At today's rates the value minus the
    # day's change is the previous close's value, and each figure is shares x price x the current rate.
    monkeypatch.setattr(ch, "usd_rates", lambda codes: {code: RATES_NOW[code] for code in [*codes, "USD"]})
    source = mixed_currency_payload()["holdings"]
    holdings, _, unconverted = ch.holdings_in_usd([dict(h) for h in source])
    assert unconverted == []
    value = sum(h["market_value"] for h in holdings)
    daily = sum(h["daily_pnl"] for h in holdings)
    previous = sum(h["last_close_usd"] * h["shares"] for h in holdings)
    assert value == pytest.approx(sum(h["shares"] * h["valuation_price"] * RATES_NOW[h["currency"]] for h in source))
    assert value - daily == pytest.approx(previous)
    # Cost is restated at the same rate, so unrealized P/L excludes currency moves since purchase, as documented
    assert sum(h["market_value"] - h["total_cost_basis"] for h in holdings) == pytest.approx(
        sum(h["unrealized_pnl"] for h in holdings))
    view = ch.build_view(mixed_currency_payload())
    assert view["portfolio"]["value"] == f"${value:,.2f}"
    assert view["portfolio"]["change_pct"] == f"{daily / previous * 100:+.2f}%"


def portfolio_args(total):
    """_portfolio's arguments for one USD holding worth `total`."""
    lot = holding("AAA", value=total)
    return [lot], 1.0, ch._holding_cards([lot], "day"), (0, 0), "day"


def test_the_overview_is_in_the_home_currency_and_usd(monkeypatch):
    # Claude-authored (2026-10-21): <NAME>_HOME_CURRENCY; each holding stays in its own currency
    monkeypatch.setattr(ch, "usd_rates", lambda codes: {code: RATES_NOW[code] for code in [*codes, "USD"]})
    usd = ch.build_view(mixed_currency_payload())["portfolio"]
    total = float(usd["value"].lstrip("$").replace(",", ""))
    view = ch.build_view(mixed_currency_payload(home_currency="CAD"))
    cad = view["portfolio"]
    assert cad["label"] == "Your portfolio · CAD" and cad["value"] == f"C${total / 0.70:,.2f}"
    assert cad["usd_value"] == f"{usd['value']} USD · 1 CAD = 0.700 USD"
    assert cad["change"].split(" · ")[0][2:].startswith("C$") and cad["change_pct"] == usd["change_pct"]
    assert cad["stats"][0]["value"].startswith(("+C$", "-C$")) and cad["stats"][0]["sub"] == usd["stats"][0]["sub"]
    assert view["account"]["accounts"][0]["value"].startswith("C$")
    cards = {card["symbol"]: card for card in view["holdings"]}
    assert cards["AAPL"]["value"].startswith("$") and cards["SHOP.TO"]["value"].startswith("C$")
    assert view["preheader"].startswith(f"Portfolio C${total / 0.70:,.2f} CAD")
    html, text = ch.render_html(view, TEMPLATE_DIR), ch.render_text(view)
    assert cad["value"] in html and '<span class="nw">1 CAD = 0.700 USD</span>' in html  # A phrase kept whole
    assert f"PORTFOLIO (CAD) {cad['value']}" in text and f"In USD: {cad['usd_value']}" in text
    # Pesos: no cents, the rate per dollar, and a smaller font for the long number
    cop = ch.build_view(mixed_currency_payload(home_currency="COP"))["portfolio"]
    assert cop["value"] == f"COP${total / 0.00025:,.0f}" and cop["value_size"] < 34
    assert cop["usd_value"].endswith("1 USD = 4,000 COP")
    # USD at home (and payloads from before 2026-10-21): one figure, no USD line
    assert usd["label"] == "Your portfolio · USD" and usd["usd_value"] == ""
    # The font steps down as the total gets longer: the Gmail app enlarges email text (Andres's phone about 1.75x)
    # and clips what doesn't fit, as "A$12,060.73" at 34 px was
    sizes = {length: ch._portfolio(*portfolio_args(total))["value_size"]
             for length, total in ((9, 1234.56), (11, 123456.78), (13, 1234567.89), (17, 12345678901234.5))}
    assert sizes == {9: 34, 11: 26, 13: 22, 17: 18}


def test_the_overview_shows_the_home_currencys_week_against_the_dollar(monkeypatch):
    # Claude-authored (2026-10-21): for a non-USD home currency, how it moved against the dollar over the past week,
    # after the exchange rate (condensed at Andres's request from a line of its own)
    monkeypatch.setattr(ch, "usd_rates", lambda codes: {code: RATES_NOW.get(code, 0.70) for code in [*codes, "USD"]})
    weeks = {"CAD": (0.7084, 0.70)}  # CAD +1.2% vs USD
    monkeypatch.setattr(ch, "usd_rate_change", lambda code: weeks.get(code))
    view = ch.build_view(mixed_currency_payload(home_currency="CAD"))
    line = view["portfolio"]["usd_value"]
    assert line.endswith(" USD · 1 CAD = 0.700 USD · CAD +1.2% this week")
    html, text = ch.render_html(view, TEMPLATE_DIR), ch.render_text(view)
    assert '<span class="nw">CAD +1.2% this week</span>' in html and "vs USD this week" not in html
    assert f"  In USD: {line}" in text.splitlines()
    weeks["CAD"] = (0.7, 0.70001)  # Barely moved
    assert ch.build_view(mixed_currency_payload(home_currency="CAD"))["portfolio"]["usd_value"].endswith(
        "USD · CAD flat this week")
    # No history from Yahoo: the rate alone. USD at home: no line at all
    del weeks["CAD"]
    assert ch.build_view(mixed_currency_payload(home_currency="CAD"))["portfolio"]["usd_value"].endswith(
        " USD · 1 CAD = 0.700 USD")
    assert ch.build_view(mixed_currency_payload())["portfolio"]["usd_value"] == ""


def test_a_home_currency_without_a_rate_shows_the_overview_in_usd(monkeypatch):
    monkeypatch.setattr(ch, "usd_rates", lambda codes: {code: None if code == "EUR" else 1.0 for code in codes})
    view = ch.build_view(payload(home_currency="EUR"))
    assert view["portfolio"]["label"] == "Your portfolio · USD" and view["portfolio"]["usd_value"] == ""
    assert "no EUR exchange rate, so the overview is in USD instead of EUR" in view["fx_notes"]


def test_an_ideas_only_digest_has_no_holdings_parts(monkeypatch):
    # Claude-authored (2026-10-21): <NAME>_HOLDINGS_SOURCE=NO_HOLDINGS (get_ideas_only_content)
    monkeypatch.setattr(ch, "usd_rates", lambda codes: pytest.fail("an ideas-only digest needs no exchange rate"))
    ideas_only = payload(user_name="Koby", holdings=[], holdings_source="NO_HOLDINGS", home_currency="COP")
    for key in ("account_company", "portfolio_beta", "portfolio_review", "api_usage"):
        ideas_only.pop(key)
    view, html = render(ideas_only)
    assert view["portfolio"] is None and view["risk"] is None and view["account"] is None and view["holdings"] == []
    assert view["greeting"] == "Good morning, Koby" and view["swing"] and view["long_term"] and view["market"]
    for absent in ("Your portfolio", "Your holdings", "Portfolio risk", "Needs attention"):
        assert absent not in html
    positions = [html.index(marker) for marker in ("Good morning, Koby", "Markets &amp; macro", "Swing ideas",
                                                    "Long-term ideas")]
    assert positions == sorted(positions)
    assert view["preheader"].startswith("2 new ideas")
    assert "YOUR HOLDINGS" not in ch.render_text(view) and "PORTFOLIO" not in ch.render_text(view)
    # The glossary leaves out the holdings' terms, keeping the ones the markets table and ideas use
    glossary = {group["category"]: [term for term, _ in group["terms"]] for group in view["glossary"]}
    assert glossary["Price moves & risk"] == ["Prev. day / Day", "Beta", "ATR"]
    assert glossary["Signals & ideas"] == ["Sentiment", "Score", "Swing idea", "Long-term idea"]
    assert len(ch.build_view(payload())["glossary"][1]["terms"]) == 8  # Unchanged with holdings


def test_about_section_ends_with_the_stocks_left_after_each_screening_stage():
    # 2026-10-20: the long-term and swing screens run side by side, so each path's stages are named for it
    stages = [{"stage": "listings", "count": 7012}, {"stage": "screener", "count": 635, "rules": ["price >= 30.0"]},
              {"stage": "yahoo", "count": 85}, {"stage": "finnhub", "count": 37}, {"stage": "technical", "count": 37},
              {"stage": "scored", "count": 36, "of": 37}, {"stage": "long_term_score", "count": 16, "min_score": 80.0},
              {"stage": "swing_fundamentals", "count": 390}, {"stage": "swing_setup", "count": 117},
              {"stage": "swing_plan", "count": 95, "min_score": 1.5}, {"stage": "swing_scored", "count": 19, "of": 20},
              {"stage": "swing_score", "count": 0, "min_score": 80.0}, {"stage": "swing_early", "count": 0},
              {"stage": "picked", "count": 2, "long_term": 2, "swing": 0}, {"stage": "unknown", "count": 1}]
    view, html = render(payload(screening={"stages": stages}))
    assert view["screening"] == [
        ("US-listed stocks", "7,012"), ("Size, volume & price", "635"), ("Long-term: analysts & fundamentals", "85"),
        ("Long-term: growth & debt", "37"), ("Long-term: technical screen", "37"), ("Long-term: scored by AI", "36 of 37"),
        ("Long-term score 80+", "16"), ("Swing: fundamentals", "390"), ("Swing: dip or fresh turn up", "117"),
        ("Swing: reward : risk 1.5+", "95"), ("Swing: best setups scored by AI", "19 of 20"), ("Swing score 80+", "0"),
        ("Still early in their run", "0"), ("Picked", "2 long-term \u00b7 0 swing")]
    about = html.index("About this digest")
    # 2026-10-21: no "Estimated from token counts..." note under the costs
    assert about < html.index("Stocks left after each screening stage") < html.index("7,012")
    assert "Estimated." not in html
    assert "STOCKS LEFT AFTER EACH SCREENING STAGE" in ch.render_text(view) and "  Long-term: scored by AI: 36 of 37" in ch.render_text(view)
    # Everything scored: no "of"
    assert ch.build_view(payload(screening={"stages": [{"stage": "scored", "count": 37, "of": 37}]}))["screening"] == [
        ("Long-term: scored by AI", "37")]
    # No counts (older payloads, or no picks could be made): no section
    view, html = render(payload())
    assert view["screening"] == [] and "screening stage" not in html
    assert ch.build_view(payload(screening={}, api_usage=None))["screening"] == []


def test_buy_more_is_a_chip_but_not_flagged_and_older_labels_are_translated():
    view = ch.build_view(payload(holdings=[holding("AAA", "buy_more", 40.0), holding("BBB", "hold", 30.0),
                                           holding("CCC", "trim", 20.0), holding("DDD", "risk_alert", 10.0)]))
    cards = {card["symbol"]: card for card in view["holdings"]}
    # Labels saved before 2026-10-18 (watchdog state, older payloads) show as today's
    assert (cards["CCC"]["signal"]["text"], cards["DDD"]["signal"]["text"]) == ("Profit sell", "Risky")
    assert [card["symbol"] for card in view["holdings"]] == ["DDD", "CCC", "AAA", "BBB"]  # Most urgent first
    portfolio = view["portfolio"]
    assert portfolio["stats"][1]["value"] == "2 of 4"  # Buy more isn't a reason to sell or watch ...
    assert [chip["text"] for chip in portfolio["attention"]] == ["DDD", "CCC", "AAA"]  # ... but it is worth a look
    assert cards["AAA"]["signal"]["text"] == "Buy more" and cards["AAA"]["signal_key"] == "buy_more"
    assert flat(view["holdings_summary"]) == "1 risky \u00b7 1 profit sell \u00b7 1 buy more \u00b7 1 hold"
    assert "1 risky \u00b7 1 profit sell \u00b7 1 buy more" in view["preheader"]
    many = ch.build_view(payload(holdings=[holding("A1", "risky"), holding("A2", "risky"), holding("B1", "buy_more"),
                                           holding("B2", "buy_more"), holding("C1", "urgent_sell"),
                                           holding("C2", "urgent_sell")]))
    assert flat(many["holdings_summary"]) == "2 urgent sells \u00b7 2 risky \u00b7 2 buy more"
    calm = ch.build_view(payload(holdings=[holding("A", "buy_more"), holding("B", "hold")]))
    assert calm["portfolio"]["stats"][1]["sub"] == "none flagged"


def test_each_card_labels_its_own_markets_session(monkeypatch):
    # Claude-authored (2026-10-21): each stock's daily change covers its own exchange's latest session, live
    # while it trades. A 08:10 ET pre-market digest found London mid-session and Sydney closed for the day, yet
    # every card said "prev. day" (the Oct 20 session)
    monkeypatch.setattr(ch, "usd_rates", lambda codes: {code: RATES_NOW.get(code, 0.70) for code in [*codes, "USD"]})
    lots = [holding("AAA"), holding("VOD.L", currency="GBX", fx_rate_to_usd=0.013, daily_pnl_session="2026-10-16",
                                     daily_pnl_live=True),
            holding("BHP.AX", currency="AUD", fx_rate_to_usd=0.65, daily_pnl_session="2026-10-16",
                    daily_pnl_live=False)]
    view = ch.build_view(payload(holdings=lots))
    cards = {card["symbol"]: card for card in view["holdings"]}
    assert [cards[s]["day"]["label"] for s in ("AAA", "VOD.L", "BHP.AX")] == ["prev. day", "live", "today"]
    assert cards["VOD.L"]["stats"][0]["sub"] == "+0.50% live"
    assert view["portfolio"]["session"] == "Latest session per market"
    assert view["holdings_note"].endswith(
        "Prev. day = the Oct 15 session · Today = the Oct 16 session · Live = still trading")
    assert view["preheader"].startswith("Portfolio ") and "% latest sessions)" in view["preheader"]
    html = ch.render_html(view, TEMPLATE_DIR)
    assert '<span class="mu">live</span>' in html and '<span class="mu">today</span>' in html
    assert "Latest session per market" in html and "Live = still trading" in ch.render_text(view)
    # One market, one label: as before
    assert ch.build_view(payload())["portfolio"]["session"] == "Prev. day · Thu, Oct 15"


def test_a_card_says_when_no_earnings_date_is_known():
    # Claude-authored (2026-10-21): often for listings outside the US. A date past the cards' 14-day window
    # isn't news, and payloads from before next_earnings_date existed say nothing
    lots = [holding("AAA", catalysts=[], next_earnings_date=None),
            holding("BBB", catalysts=[], next_earnings_date="2027-01-20"),
            holding("CCC", catalysts=[]),
            holding("DDD", next_earnings_date="2026-10-21")]
    view = ch.build_view(payload(holdings=lots))
    notes = {card["symbol"]: card["earnings_note"] for card in view["holdings"]}
    assert notes == {"AAA": "No earnings date available", "BBB": "", "CCC": "", "DDD": ""}
    assert ch.render_html(view, TEMPLATE_DIR).count("No earnings date available") == 1
    assert "  No earnings date available" in ch.render_text(view).splitlines()


def test_about_shows_the_users_and_the_admins_costs():
    # 2026-10-22 (Andres): this run and the last 30 days, each the reader's own keys and the admin's shared ones
    api_usage = {"currency": "USD",
                 "this_digest": {"total_cost_usd": 0.05, "anthropic": {"cost_usd": 0.02}, "gemini": {"cost_usd": 0.03}},
                 "last_30_days": {"total_cost_usd": 4.2, "anthropic": {"cost_usd": 1.2}, "gemini": {"cost_usd": 3.0}},
                 "admin": {"this_digest": {"total_cost_usd": 0.31, "deepseek": {"cost_usd": 0.3}, "gemini": {"cost_usd": 0.01}},
                           "last_30_days": {"total_cost_usd": 12.5, "deepseek": {"cost_usd": 12.5}}}}
    rows = ch.build_view(payload(api_usage=api_usage))["usage"]
    assert [label for label, _ in rows[:4]] == ["This run · you", "This run · admin", "Last 30 days · you",
                                                "Last 30 days · admin"]
    assert dict(rows)["This run · admin"] == "$0.31 USD (DeepSeek $0.30 · Gemini $0.01)"
    assert dict(rows)["Last 30 days · you"] == "$4.20 USD (Anthropic $1.20 · Gemini $3.00)"
    # Each side's two providers always show, $0.00 included; another provider that cost something follows
    assert dict(rows)["Last 30 days · admin"] == "$12.50 USD (DeepSeek $12.50 · Gemini $0.00)"
    api_usage["admin"]["last_30_days"] = {"total_cost_usd": 3.0, "deepseek": {"cost_usd": 1.0}, "anthropic": {"cost_usd": 2.0}}
    rows = dict(ch.build_view(payload(api_usage=api_usage))["usage"])
    assert rows["Last 30 days · admin"] == "$3.00 USD (DeepSeek $1.00 · Gemini $0.00 · Anthropic $2.00)"
