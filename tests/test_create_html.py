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
    # The connected account is a small dropdown at the foot of the overview card (UI pass, 2026-10-17)
    markers = ["Good morning, Test", "Your portfolio", "Needs attention", "Questrade · 1 account", "Portfolio risk",
               "Markets &amp; macro", "Top stories", "Your holdings", "Swing ideas", "Long-term ideas"]
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
    assert portfolio["session"] == "Prev. day · Oct 15"
    assert portfolio["change"] == "▲ $20.00 · 0.50%"
    # Unrealized: a 10% gain on cost; flagged: urgent sell, risk alert, and trim (an urgent sell makes it red)
    assert [stat["value"] for stat in portfolio["stats"]] == ["+$400", "3 of 4", "2"]
    assert (portfolio["stats"][1]["sub"], portfolio["stats"][1]["tone"]) == ("50% of value", "neg")
    assert [chip["text"] for chip in portfolio["attention"]] == ["CCC", "AAA", "DDD"]  # Most urgent first
    assert [item["text"] for item in portfolio["earnings"]] == ["AAA Oct 21", "BBB Oct 21", "CCC Oct 21", "DDD Oct 21"]
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
    assert "No setups scored 80+ today" in html and "No ideas scored 80+ today" in html
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
        holdings=[holding(f"T{i:02d}", "risk_alert" if i % 2 else "hold", 10.0) for i in range(10)],
        recommendations={"top_long_term": [long_pick("L1"), long_pick("L2")],
                         "top_swing": [swing_pick(f"S{i}") for i in range(5)]},
    )
    view = ch.build_view(full)
    html = ch.fit_for_gmail(view, TEMPLATE_DIR)
    assert len(html.encode("utf-8")) <= ch.GMAIL_CLIP_BYTES
    # 10 holdings with 3 news items each, 5 swing and 2 long-term ideas fit once the glossary, the first thing
    # left out, is dropped: every news item stays
    assert view["trimmed"] is True and view["glossary"] == []
    assert all(len(card["news"]) == 3 for card in view["holdings"])


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
                     "MARKETS", "The central bank held rates.", "CCC - Urgent sell", "SWING IDEAS", "SWA (score 85)",
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
    assert amp.startswith("<!DOCTYPE html>\n<html amp4email data-css-strict")
    assert '<meta charset="utf-8">\n<script async src="https://cdn.ampproject.org/v0.js"></script>' in amp
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
    assert flat(view["holdings_summary"]) == "1 urgent sell · 1 risk alert · 1 trim · 1 hold"
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
    assert amp.count("<button") == len(view["portfolio"]["attention"]) + len(view["portfolio"]["earnings"])
    # The HTML version keeps plain chips: jump links don't work in the Gmail app, nor into closed dropdowns
    html = ch.render_html(view, TEMPLATE_DIR)
    assert "<button" not in html and " on=" not in html


def test_four_figure_boxes_form_a_2x2_grid():
    # Four boxes side by side ran past a phone's right edge in the Gmail app (2026-10-17), so they wrap 2x2;
    # up to three stay in one row
    html = ch.render_html(ch.build_view(payload(holdings=[holding("AAA")])), TEMPLATE_DIR)
    row_start = html.rfind("<details>", 0, html.index(">AAA<"))
    grid = html[html.index('<table role="presentation" width="100%" style="background-color:#F5F7FA;', row_start):]
    grid = grid[:grid.index("</table>")]
    assert grid.count("<tr>") == 2 and grid.count('width="50%"') == 4
    overview = html[html.index("Your portfolio"):html.index(">Portfolio risk<")]
    assert overview.count('width="33%"') == 3


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
    # Markets opens on its key tiles; the full table is one tap away
    assert ">All indicators<" in html and ">Why it matters<" in html
    assert [tile["label"] for tile in view["market"]["key"]] == ["S&P 500 · SPY", "10Y Yield"]  # The key ones it has


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
    post = ch.build_view(payload(period="Post-Market", subject="Finnbot Post-Market Digest 2026-10-16"))
    assert (pre["day_label"], post["day_label"]) == ("Prev. day", "Day")
    assert pre["portfolio"]["session"] == "Prev. day · Oct 15" and post["portfolio"]["session"] == "Day · Oct 15"
    assert pre["holdings"][0]["stats"][0]["sub"] == "+0.50% prev. day"
    assert post["holdings"][0]["stats"][0]["sub"] == "+0.50% day"
    assert pre["holdings_note"].endswith("Prev. day = the Oct 15 session")
    assert flat(pre["markets_summary"]).startswith("S&P 500 +0.3% prev. day")
    assert pre["market"]["key"][0]["sub"] == "+0.3% prev. day"
    assert [chip["sub"] for chip in pre["market"]["chips"] if chip["label"] == "10Y–2Y spread"] == ["-4.0 bps prev. day"]
    assert "(+0.50% prev. day)" in pre["preheader"]
    pre_html, post_html = ch.render_html(pre, TEMPLATE_DIR), ch.render_html(post, TEMPLATE_DIR)
    assert ">Prev. day</td>" in pre_html and ">Day</td>" in post_html  # The market table's one-day column
    post["glossary"] = []  # The glossary defines both terms
    assert "prev. day" not in ch.render_html(post, TEMPLATE_DIR).lower()
    assert "MARKETS (Day / 5D / 1M)" in ch.render_text(post)


def test_the_glossary_is_static_and_left_out_first():
    view = ch.build_view(payload())
    from src.digest.glossary import GLOSSARY
    assert [g["category"] for g in view["glossary"]] == [category for category, _ in GLOSSARY]
    html = ch.render_html(view, TEMPLATE_DIR)
    assert html.index(">Glossary<") > html.index(">Long-term ideas ")  # At the very bottom
    assert "<b>RSI 14</b><br>Relative Strength Index" in html and "<b>Beta</b><br>" in html
    assert ch.TRIM_STEPS[0].__name__ == "_drop_glossary"  # The least important content goes first


def test_currencies_come_from_the_payload_or_default_to_usd():
    view = ch.build_view(payload(holdings=[holding("AAA"), holding("XYZ.DE", currency="EUR"), holding("NOC", currency=None)]))
    cards = {card["symbol"]: card for card in view["holdings"]}
    # A holding's Price box is in its listing currency; position values and P/L are converted to USD
    assert (cards["AAA"]["stats"][0]["label"], cards["AAA"]["stats"][0]["value"]) == ("Price · USD", "$100.00")
    # Currencies other than CAD, AUD, and GBP are shown as the payload gives them
    assert (cards["XYZ.DE"]["stats"][0]["label"], cards["XYZ.DE"]["stats"][0]["value"]) == ("Price · EUR", "€100.00")
    assert cards["NOC"]["stats"][0]["label"] == "Price · USD" and cards["NOC"]["currency_assumed"] is True
    assert all(card["stats"][1]["label"] == "Unrealized · USD" for card in view["holdings"])
    assert view["portfolio"]["label"] == "Your portfolio · USD" and "Values in USD" in view["holdings_note"]
    # The ideas and the market dashboard are US-listed, so always USD and never noted; API usage without a
    # currency (older payloads) is assumed USD and noted
    assert view["ideas_currency"] == "USD" and "Prices in USD" in ch.render_html(view, TEMPLATE_DIR)
    assert view["market"]["rows"][0]["last"] == "$700.00"  # SPY's share price
    assert view["currency_notes"] == [
        "holdings without a currency, shown as USD: NOC",
        "API usage has no currency; costs are shown as USD",
    ]
    assert dict(view["usage"])["This digest"].startswith("$0.30 USD")
    with_currency = payload(api_usage={"currency": "USD", "this_digest": {"total_cost_usd": 0.3}})
    assert ch.build_view(with_currency)["currency_notes"] == []


def test_cad_aud_and_gbp_holdings_are_converted_to_usd_at_the_current_rate(monkeypatch):
    monkeypatch.setattr(ch, "usd_rates", lambda codes: {"USD": 1.0, "CAD": 0.70, "AUD": 0.65, "GBX": 0.0125})
    view = ch.build_view(payload(holdings=[
        holding("AAA", value=1000.0),
        # CAD the payload already converted at 0.72: money amounts in USD at that rate, prices still in CAD
        holding("ENB.TO", value=720.0, currency="CAD", fx_rate_to_usd=0.72, current_price=50.0, last_close=49.0,
                total_cost_basis=648.0),
        # AUD the payload couldn't convert (no rate): money amounts and prices still in AUD
        holding("BHP.AX", value=100.0, currency="AUD", current_price=40.0, total_cost_basis=90.0),
        holding("VOD.L", value=100.0, currency="GBX", fx_rate_to_usd=0.0125, current_price=80.0, total_cost_basis=90.0),
        holding("SAP.DE", value=100.0, currency="EUR"),  # Not converted: other currencies are taken as they come
    ]))
    cards = {card["symbol"]: card for card in view["holdings"]}
    enb, bhp, vod, sap = cards["ENB.TO"], cards["BHP.AX"], cards["VOD.L"], cards["SAP.DE"]
    assert (enb["value"], enb["stats"][0]["label"], enb["stats"][0]["value"]) == ("$700.00", "Price · USD", "$35.00")
    assert enb["meta"].endswith("Listed in CAD at C$50.00 (1 CAD = 0.7000 USD)") and enb["chart"] is None
    assert (bhp["value"], bhp["stats"][0]["value"]) == ("$65.00", "$26.00")  # 100 and 40 AUD at 0.65
    assert vod["stats"][0]["value"] == "$1.00" and vod["meta"].endswith("Listed in GBP pence at 80.0p (1 GBP = 1.2500 USD)")
    assert (sap["value"], sap["stats"][0]["label"]) == ("$100.00", "Price · EUR")
    # Unrealized P/L follows the converted values, and the weights are recomputed from them
    assert enb["stats"][1]["value"] == "+$70.00"  # (720 - 648) CAD-in-USD at 0.72, restated at 0.70
    assert round(sum(card["weight_pct"] for card in view["holdings"]), 1) == 100.0
    assert enb["weight_pct"] == round(700 / (1000 + 700 + 65 + 100 + 100) * 100, 2)
    assert "converted CAD holdings to USD at 1 CAD = 0.7000 USD" in view["fx_notes"]
    assert view["issues"] == []


def test_a_holding_without_any_exchange_rate_stays_in_its_currency_and_says_so(monkeypatch):
    monkeypatch.setattr(ch, "usd_rates", lambda codes: {"USD": 1.0, "CAD": None})
    view = ch.build_view(payload(holdings=[holding("AAA"), holding("ENB.TO", currency="CAD")]))
    enb = {card["symbol"]: card for card in view["holdings"]}["ENB.TO"]
    assert enb["stats"][0]["label"] == "Price · CAD"
    assert [issue["title"] for issue in view["issues"]] == ["Exchange rate"]
    assert "ENB.TO" in view["issues"][0]["message"]
