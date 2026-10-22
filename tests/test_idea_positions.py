# Claude-authored (2026-10-22): holdings bought from Finnbot's ideas (src/idea_positions.py) and where they show
import json
from datetime import date, datetime, timezone

import pandas as pd
import pytest

import src.idea_positions as ip
import src.track_record as track_record
from src.digest import create_html as ch
from src.personalized.LLM_hold_instr import SYSTEM_PROMPT
from src.personalized.recommendation import build_holding_payloads
from src.personalized.user_profiles import build_profile
from tests.test_create_html import TEMPLATE_DIR, holding, payload, swing_pick

UTC = timezone.utc
WED_PRE = datetime(2026, 10, 21, 11, 10, tzinfo=UTC)    # Wed Oct 21, 07:10 ET: a Pre-Market digest
WED_POST = datetime(2026, 10, 21, 20, 10, tzinfo=UTC)   # Wed 16:10 ET: Post-Market
THU_POST = datetime(2026, 10, 22, 20, 10, tzinfo=UTC)
FRI_POST = datetime(2026, 10, 23, 20, 10, tzinfo=UTC)


def ideas(*rows):
    """Writes the track record's idea lines (as log_universal does) and returns them as load_records reads them."""
    track_record.UNIVERSAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(track_record.UNIVERSAL_PATH, "w", encoding="utf-8") as handle:
        for when, kind, symbol, extra in rows:
            handle.write(json.dumps({"recorded_at": when.isoformat(), "list": kind, "symbol": symbol, "price": 100.0,
                                     "longScore": 91.0, "swingScore": 82.0, **extra}) + "\n")
    return track_record.load_records(track_record.UNIVERSAL_PATH)


SWING_PLAN = {"entry_price": 100.0, "stop_loss": 95.0, "price_target": 112.0}


def held(*rows):
    """Holdings rows: (symbol, shares, avg cost, price)."""
    return pd.DataFrame([{"symbol": s, "shares": n, "avg_cost_basis": cost, "current_price": price}
                         for s, n, cost, price in rows])


def test_the_deadline_is_the_close_of_the_next_trading_day():
    assert ip.idea_deadline(WED_PRE) == datetime(2026, 10, 22, 20, 0, tzinfo=UTC)  # Thu's 16:00 ET close
    assert ip.idea_deadline(WED_POST) == datetime(2026, 10, 22, 20, 0, tzinfo=UTC)
    assert ip.idea_deadline(FRI_POST) == datetime(2026, 10, 26, 20, 0, tzinfo=UTC)  # Over the weekend: Monday
    # The day before Thanksgiving: Friday's 13:00 ET half-day close
    assert ip.idea_deadline(datetime(2026, 11, 25, 21, 10, tzinfo=UTC)) == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)


def test_only_new_positions_bought_by_the_next_trading_day_are_linked(tmp_path):
    path = tmp_path / "state.json"
    idea_rows = ideas((WED_PRE, "swing", "SWK", SWING_PLAN), (WED_PRE, "long_term", "SWK", {}),
                      (WED_PRE, "long_term", "NVDA", {}), (WED_PRE, "long_term", "MSFT", {}),
                      (WED_PRE, "long_term", "AAPL", {}))
    # The first check only remembers what's held: AAPL was already owned, idea or not
    links, _ = ip.link_positions("Tess", held(("AAPL", 10, 150, 160)), WED_PRE, path, idea_rows)
    assert links == {}
    # SWK appears that afternoon: bought after the morning's swing idea (the swing one wins over the long-term one)
    links, hits = ip.link_positions("Tess", held(("AAPL", 15, 150, 160), ("SWK", 10, 101.2, 103)), WED_POST, path,
                                    idea_rows)
    assert set(links) == {"SWK"} and hits == []  # AAPL's added shares don't count
    swk = links["SWK"]
    assert (swk["list"], swk["entry"], swk["stop"], swk["target"], swk["paid"], swk["score"]) == (
        "swing", 100.0, 95.0, 112.0, 101.2, 82.0)
    # NVDA appears Thursday: still by the end of the next trading day
    rows = (("AAPL", 15, 150, 160), ("SWK", 10, 101.2, 103), ("NVDA", 5, 0.0, 200))
    links, _ = ip.link_positions("Tess", held(*rows), THU_POST, path, idea_rows)
    assert links["NVDA"]["list"] == "long_term" and links["NVDA"]["paid"] == 200  # Cost unknown: the price then
    # MSFT only appears Friday: the previous check (Thursday 16:10 ET) came after Thursday's close, so it's too late
    links, _ = ip.link_positions("Tess", held(*rows, ("MSFT", 2, 500, 510)), FRI_POST, path, idea_rows)
    assert "MSFT" not in links


def test_target_and_stop_alerts_go_once_and_sales_are_kept(tmp_path, monkeypatch, telegram):
    path = tmp_path / "state.json"
    monkeypatch.setitem(ip.USER_PROFILES, "Tess", build_profile("Tess"))
    monkeypatch.setenv("TESS_TELEGRAM_CHAT_ID", "123")
    idea_rows = ideas((WED_PRE, "swing", "SWK", SWING_PLAN))
    ip.link_positions("Tess", held(("AAPL", 1, 1, 1)), WED_PRE, path, idea_rows)
    frame = ip.attach_idea_origins("Tess", held(("AAPL", 1, 1, 1), ("SWK", 10, 101.2, 103)), WED_POST, path)
    assert frame["idea_origin"].iloc[1]["symbol"] == "SWK" and frame["idea_origin"].iloc[0] is None
    assert telegram.messages == []
    monkeypatch.setattr(ip, "_ideas", lambda: idea_rows)
    ip.attach_idea_origins("Tess", held(("AAPL", 1, 1, 1), ("SWK", 10, 101.2, 112.4)), THU_POST, path)
    assert len(telegram.messages) == 1 and telegram.urgent == [True]  # 2026-10-23: one of the few urgent notices
    assert "SWK reached the $112.00 target of Finnbot's Oct 21 swing idea (now $112.40)" in telegram.messages[0][1]
    assert "+11.1%" in telegram.messages[0][1]
    ip.attach_idea_origins("Tess", held(("AAPL", 1, 1, 1), ("SWK", 10, 101.2, 113)), FRI_POST, path)
    assert len(telegram.messages) == 1  # Once
    # Sold: kept as closed, for the scorecard
    ip.link_positions("Tess", held(("AAPL", 1, 1, 1)), datetime(2026, 10, 26, 20, 10, tzinfo=UTC), path)
    user = ip.load_state(path)["users"]["Tess"]
    assert user["open"] == {} and user["closed"][0]["symbol"] == "SWK" and user["closed"][0]["hit"] == "target"
    assert user["closed"][0]["last_price"] == 113


def test_a_stop_alert_and_no_alert_without_telegram(tmp_path, monkeypatch, telegram):
    path = tmp_path / "state.json"
    idea_rows = ideas((WED_PRE, "swing", "SWK", SWING_PLAN))
    monkeypatch.setattr(ip, "_ideas", lambda: idea_rows)
    ip.link_positions("Tess", held(("AAPL", 1, 1, 1)), WED_PRE, path)
    ip.attach_idea_origins("Tess", held(("AAPL", 1, 1, 1), ("SWK", 10, 101.2, 103)), WED_POST, path)
    links, hits = ip.link_positions("Tess", held(("AAPL", 1, 1, 1), ("SWK", 10, 101.2, 94.8)), THU_POST, path)
    assert hits[0]["hit"] == "stop" and "fell to the $95.00 stop" in ip.hit_text(hits[0])
    assert ip.send_hit_alerts("Tess", hits) == 0 and telegram.messages == []  # Tess has no Telegram chat ID


def test_the_review_card_and_ideas_show_where_a_stock_came_from(tmp_path):
    origin = {"symbol": "SWK", "list": "swing", "idea_at": WED_PRE.isoformat(), "score": 82.0, "entry": 100.0,
              "stop": 95.0, "target": 112.0, "first_seen": WED_POST.isoformat(), "paid": 101.2, "hit": None}
    # The AI review gets the plan, once per stock
    frame = pd.DataFrame([{"symbol": "SWK", "shares": 10, "market_value": 1030.0, "idea_origin": origin}])
    swk = build_holding_payloads(frame)["SWK"]
    assert swk["opened_from_finnbot_idea"] == {
        "list": "swing", "suggested_on": "2026-10-21", "score": 82.0, "bought_at": 101.2, "first_held_on": "2026-10-21",
        "entry_price": 100.0, "stop_loss": 95.0, "price_target": 112.0, "reached": None}
    assert "idea_origin" not in swk["positions"][0] and "opened_from_finnbot_idea" in SYSTEM_PROMPT
    # The card's label, and "You hold this" on an idea already held
    assert ip.idea_label(origin) == "Swing idea · Oct 21 · entry $100.00 · stop $95.00 · target $112.00"
    assert ip.idea_label({**origin, "hit": "stop"}).endswith("· stop hit")
    assert ip.idea_label({"list": "long_term", "idea_at": WED_PRE.isoformat(), "score": 91.0}) == (
        "Long-term idea · Oct 21 · score 91")
    view = ch.build_view(payload(holdings=[holding("SWK", idea_origin=origin), holding("AAA")],
                                 recommendations={"top_swing": [swing_pick("SWK"), swing_pick("ZZZ")],
                                                  "top_long_term": []}))
    cards = {card["symbol"]: card for card in view["holdings"]}
    assert cards["SWK"]["idea"].startswith("Swing idea · Oct 21") and cards["AAA"]["idea"] == ""
    assert [card["held"] for card in view["swing"]] == [True, False]
    html, text = ch.render_html(view, TEMPLATE_DIR), ch.render_text(view)
    assert '<span class="nw">target $112.00</span>' in html and html.count("You hold this") == 1
    assert "  From Finnbot's Swing idea · Oct 21" in text and "(you hold this)" in text


def test_the_monthly_scorecard_goes_once_and_only_with_positions(tmp_path, monkeypatch, telegram):
    path = tmp_path / "state.json"
    monkeypatch.setattr(ip, "STATE_PATH", path)
    for name in ("Tess", "Uma"):
        monkeypatch.setitem(ip.USER_PROFILES, name, build_profile(name))
        monkeypatch.setenv(f"{name.upper()}_TELEGRAM_CHAT_ID", "1")
    link = {"list": "swing", "idea_at": WED_PRE.isoformat(), "score": 82.0, "first_seen": WED_POST.isoformat(),
            "paid": 100.0, "last_price": 111.0, "last_seen": THU_POST.isoformat(), "hit": "target"}
    path.write_text(json.dumps({"users": {
        "Tess": {"open": {"SWK": {**link, "symbol": "SWK"}},
                 "closed": [{**link, "symbol": "NVDA", "list": "long_term", "hit": None, "last_price": 95.0,
                             "closed_at": FRI_POST.isoformat()},
                            {**link, "symbol": "OLD", "first_seen": "2026-09-10T15:00:00+00:00"}]},
        "Uma": {"open": {}, "closed": []}}}))  # Uma bought nothing from ideas: no message
    days = pd.Series([100.0, 101.0, 102.0, 103.0], index=[date(2026, 10, 20), date(2026, 10, 21),
                                                        date(2026, 10, 22), date(2026, 10, 23)])
    monkeypatch.setattr(track_record, "fetch_daily_closes", lambda symbols, start: {"SPY": days})
    now = datetime(2026, 11, 2, 12, 10, tzinfo=UTC)
    assert ip.send_idea_scorecards(now, users=["Tess", "Uma"]) == 1
    (user, text), = telegram.messages
    assert user == "Tess" and "Finnbot ideas you acted on in October" in text and "OLD" not in text
    assert "SWK, swing idea Oct 21: +11.0% (reached its target)" in text
    assert "NVDA, long-term idea Oct 21: -5.0% (sold)" in text
    # Average +3.0%; SPY from the close before Oct 21 (100) to Oct 22 (102) for both: +2.0%
    assert "Average +3.0%, against +2.0% for the S&P 500 over the same days." in text
    assert ip.send_idea_scorecards(now, users=["Tess", "Uma"]) == 0  # Once a month


def test_problems_never_stop_the_holdings(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ip, "link_positions", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    frame = held(("AAPL", 1, 1, 1))
    assert ip.attach_idea_origins("Tess", frame, WED_POST, tmp_path / "s.json") is frame
    assert "couldn't match holdings" in capsys.readouterr().out
    assert ip.attach_idea_origins("Tess", pd.DataFrame(), WED_POST) is not None
