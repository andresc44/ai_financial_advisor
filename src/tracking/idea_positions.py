# File to remember which holdings were bought from Finnbot's ideas, and use it: card labels, the plan in the AI review,
# stop / target alerts, and a monthly scorecard on Telegram.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-22, at Andres's request.

What counts as bought from an idea (link_positions, at each digest and watchdog check of a user with holdings):
- A new position: a symbol held now that wasn't at the user's previous check (data/idea_positions.json). Adding
  shares to a stock already held doesn't count, and a user's first check only remembers what they hold, since
  nothing can be called new yet.
- Bought by the end of the next trading day after the idea: the idea (data/track_record/universal.jsonl, every swing
  and long-term pick a digest showed) came before this check, and the user's previous check came before the
  deadline, the NYSE close of the first trading day after the idea's date (US Eastern). A purchase is only seen at
  the next check, so that is as close as it can be timed. The newest such idea for the symbol wins; a swing idea
  over a long-term one from the same run.
- What a link keeps: the list, when it was suggested, its score, a swing idea's plan (entry, stop, target), when the
  position was first seen, what was paid (the average cost, or the price then when the broker or sheet doesn't say),
  the latest price, and whether the price reached the target or the stop. When the stock is sold, the link moves to
  "closed" with the last price seen.

How it's used:
- attach_idea_origins puts each held stock's link on its rows as `idea_origin`: the digest card's label
  (create_html), and `opened_from_finnbot_idea` in the AI review (recommendation.build_holding_payloads), so the
  review weighs the original plan.
- A swing idea's position reaching its target or stop sends the user a Telegram message, once each.
- send_idea_scorecards (every cron digest run; once a month per user): how the previous month's idea positions did,
  beside the S&P 500 over the same days, on Telegram.
"""
import html
import json
import os
import sys
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.ops.run_logging import debug
from src.tracking import track_record
from src.market.market_session import _get_calendar, market_date
from src.personalized.user_profiles import USER_PROFILES, env_setting

STATE_PATH = project_root / "data" / "idea_positions.json"
_LOCK = threading.Lock()  # Users run in parallel threads
SWING, LONG_TERM = "swing", "long_term"
CLOSED_KEEP_DAYS = 400  # Sold positions are kept this long, for the scorecard
NOTICE_COOLDOWN_HOURS = 24 * 400  # Each alert and scorecard is keyed by what it's about, so it goes once


# ---------------------------------------------------------------------------- State

def load_state(path: Optional[Path] = None) -> dict:
    try:
        return json.loads((path or STATE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _when(value: Any) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # NaN -> None


# ---------------------------------------------------------------------------- Matching new positions to ideas

def idea_deadline(idea_at: datetime) -> datetime:
    """The NYSE close of the first trading day after the idea's US Eastern date (its half-day close on a half-day)."""
    calendar = _get_calendar("XNYS")
    next_day = pd.Timestamp(market_date(idea_at)) + pd.Timedelta(days=1)
    return calendar.session_close(calendar.date_to_session(next_day, direction="next")).to_pydatetime()


def _ideas() -> pd.DataFrame:
    return track_record.load_records(track_record.UNIVERSAL_PATH)


def _matching_idea(symbol: str, previous_check: datetime, now: datetime, ideas: pd.DataFrame) -> Optional[dict]:
    """The newest idea for `symbol` that the position, first seen at `now`, could have been bought within."""
    if ideas.empty or "symbol" not in ideas.columns:
        return None
    rows = ideas[(ideas["symbol"].astype(str).str.upper() == symbol.upper()) & (ideas["recorded_at"] <= pd.Timestamp(now))]
    best = None
    for row in rows.itertuples(index=False):
        idea_at = row.recorded_at.to_pydatetime()
        if previous_check >= idea_deadline(idea_at):
            continue  # Already not held after the deadline: bought later than the next trading day
        key = (idea_at, getattr(row, "list", "") == SWING)
        if best is None or key > best[0]:
            best = (key, row)
    if best is None:
        return None
    row = best[1]
    swing = getattr(row, "list", "") == SWING
    return {
        "list": SWING if swing else LONG_TERM,
        "idea_at": row.recorded_at.isoformat(),
        "score": _num(getattr(row, "swingScore" if swing else "longScore", None)),
        "entry": _num(getattr(row, "entry_price", None)) if swing else None,
        "stop": _num(getattr(row, "stop_loss", None)) if swing else None,
        "target": _num(getattr(row, "price_target", None)) if swing else None,
    }


def _position_prices(holdings: pd.DataFrame) -> dict[str, tuple[Optional[float], Optional[float]]]:
    """{symbol: (average price paid over its lots, latest price)}, in the listing's currency."""
    prices = {}
    for symbol, lots in holdings.groupby(holdings["symbol"].astype(str)):
        if not symbol or symbol == "nan":
            continue
        first = lots.iloc[0]
        price = next((_num(first.get(col)) for col in ("session_price", "valuation_price", "current_price")
                      if _num(first.get(col)) is not None), None)
        paid_lots = [(_num(lot.get("shares")) or 0.0, _num(lot.get("avg_cost_basis")) or 0.0) for _, lot in lots.iterrows()]
        shares = sum(s for s, cost in paid_lots if cost > 0)
        paid = sum(s * cost for s, cost in paid_lots if cost > 0) / shares if shares > 0 else None
        prices[symbol] = (paid, price)
    return prices


def link_positions(user_name: str, holdings: pd.DataFrame, now: datetime, path: Optional[Path] = None,
                   ideas: Optional[pd.DataFrame] = None) -> tuple[dict[str, dict], list[dict]]:
    """
    Updates the user's links from this check's holdings (see the module docstring). Returns ({symbol: link} for the
    held stocks bought from an idea, [links whose target or stop was reached at this check]).
    """
    path = path or STATE_PATH
    with _LOCK:
        state = load_state(path)
        user = state.setdefault("users", {}).setdefault(user_name, {"held": None, "checked_at": None,
                                                                   "open": {}, "closed": []})
        prices = _position_prices(holdings)
        previous, previous_check = user.get("held"), _when(user.get("checked_at"))
        if previous is not None and previous_check is not None:
            new = [s for s in prices if s not in set(previous) and s not in user["open"]]
            if new:
                ideas = _ideas() if ideas is None else ideas
                for symbol in new:
                    idea = _matching_idea(symbol, previous_check, now, ideas)
                    if idea:
                        paid, price = prices[symbol]
                        user["open"][symbol] = {**idea, "symbol": symbol, "first_seen": now.isoformat(),
                                                "paid": paid if paid is not None else price, "last_price": price,
                                                "last_seen": now.isoformat(), "hit": None}
        hits = []
        for symbol, link in list(user["open"].items()):
            if symbol not in prices:  # Sold
                user["closed"].append({**link, "closed_at": now.isoformat()})
                del user["open"][symbol]
                continue
            price = prices[symbol][1]
            if price is not None:
                link["last_price"], link["last_seen"] = price, now.isoformat()
            if link["list"] == SWING and not link.get("hit") and price is not None:
                kind = ("target" if link.get("target") and price >= link["target"]
                        else "stop" if link.get("stop") and price <= link["stop"] else None)
                if kind:
                    link.update({"hit": kind, "hit_at": now.isoformat(), "hit_price": price})
                    hits.append(dict(link))
        cutoff = now - timedelta(days=CLOSED_KEEP_DAYS)
        user["closed"] = [link for link in user["closed"] if (_when(link.get("closed_at")) or now) >= cutoff]
        user["held"], user["checked_at"] = sorted(prices), now.isoformat()
        _save(path, state)
        return {symbol: dict(link) for symbol, link in user["open"].items()}, hits


# ---------------------------------------------------------------------------- Using the links

def _has_telegram(user_name: str) -> bool:
    return bool(env_setting((USER_PROFILES.get(user_name) or {}).get("telegram_chat_id")))


def _short_date(value: Any) -> str:
    moment = _when(value)
    return f"{moment.astimezone(track_record.MARKET_TZ):%b} {moment.astimezone(track_record.MARKET_TZ).day}" if moment else ""


def _money(value: Optional[float]) -> str:
    return f"${value:,.2f}" if value is not None else "?"


def hit_text(link: dict) -> str:
    """The Telegram message for a swing idea's position reaching its target or stop (HTML)."""
    symbol, price, paid = html.escape(link["symbol"]), link.get("hit_price"), link.get("paid")
    change = f" You paid {_money(paid)}: {(price / paid - 1) * 100:+.1f}%." if price and paid else ""
    idea = f"Finnbot's {_short_date(link['idea_at'])} swing idea"
    if link["hit"] == "target":
        return f"\U0001f3af {symbol} reached the {_money(link.get('target'))} target of {idea} (now {_money(price)}).{change}"
    return f"\U0001f6d1 {symbol} fell to the {_money(link.get('stop'))} stop of {idea} (now {_money(price)}).{change}"


def send_hit_alerts(user_name: str, hits: list[dict]) -> int:
    """Sends each target / stop alert, once (the notices log remembers it). Never raises. Returns how many went out."""
    if not hits or not _has_telegram(user_name):
        return 0
    from src.telegram_alert.notices import send_notice_now
    sent = 0
    for link in hits:
        try:
            # 2026-10-23: urgent (the only notices that are): the position reached its stop or target
            if send_notice_now(user_name, "idea plan", f"{link['symbol']} {link['idea_at'][:10]} {link['hit']}",
                               hit_text(link), cooldown_hours=NOTICE_COOLDOWN_HOURS, urgent=True):
                sent += 1
        except Exception as e:
            print(f"[WARNING] {user_name}: the {link['symbol']} {link['hit']} alert failed: {e}")
    return sent


def attach_idea_origins(user_name: str, holdings: pd.DataFrame, now: datetime, path: Optional[Path] = None,
                        alerts: bool = True) -> pd.DataFrame:
    """
    Claude-authored (2026-10-22): links this check's new positions to ideas, puts each held stock's link on its rows
    as `idea_origin` (None for the rest), and sends any target / stop alert. Called by the digest
    (personalized_consolidated) and the watchdog (hourly_watchdog) before they evaluate the holdings. Never raises:
    a problem is logged and the holdings go on without it.
    """
    if holdings is None or holdings.empty or "symbol" not in holdings.columns:
        return holdings
    try:
        links, hits = link_positions(user_name, holdings, now, path)
    except Exception as e:
        print(f"[WARNING] {user_name}: couldn't match holdings to Finnbot's ideas ({type(e).__name__}: {e}).")
        return holdings
    holdings = holdings.copy()
    holdings["idea_origin"] = pd.Series([links.get(str(symbol)) for symbol in holdings["symbol"]],
                                        index=holdings.index, dtype=object)
    for link in links.values():
        if link["first_seen"] == now.isoformat():
            debug(f"[{user_name}] {link['symbol']} was bought after Finnbot's {_short_date(link['idea_at'])} "
                  f"{link['list'].replace('_', '-')} idea.")
    if alerts:
        send_hit_alerts(user_name, hits)
    return holdings


def idea_label(origin: Any) -> str:
    """The holding card's line, e.g. 'Swing idea · Oct 21 · entry $100.00 · stop $95.00 · target $112.00'."""
    if not isinstance(origin, dict) or not origin.get("list"):
        return ""
    when = _short_date(origin.get("idea_at"))
    if origin["list"] != SWING:
        score = _num(origin.get("score"))
        return " · ".join(part for part in ("Long-term idea", when, f"score {score:.0f}" if score is not None else "") if part)
    parts = ["Swing idea", when] + [f"{name} {_money(_num(origin.get(key)))}" for name, key in
                                    (("entry", "entry"), ("stop", "stop"), ("target", "target"))
                                    if _num(origin.get(key)) is not None]
    if origin.get("hit"):
        parts.append("target reached" if origin["hit"] == "target" else "stop hit")
    return " · ".join(part for part in parts if part)


def prompt_view(origin: Any) -> Optional[dict]:
    """What the AI review is told about the idea a position was bought from."""
    if not isinstance(origin, dict) or not origin.get("list"):
        return None
    view = {"list": origin["list"], "suggested_on": str(origin.get("idea_at", ""))[:10], "score": origin.get("score"),
            "bought_at": origin.get("paid"), "first_held_on": str(origin.get("first_seen", ""))[:10]}
    if origin["list"] == SWING:
        view.update({"entry_price": origin.get("entry"), "stop_loss": origin.get("stop"),
                     "price_target": origin.get("target"), "reached": origin.get("hit")})
    return view


# ---------------------------------------------------------------------------- Monthly scorecard

def _previous_month(today: date) -> tuple[date, date]:
    first_this = today.replace(day=1)
    last_previous = first_this - timedelta(days=1)
    return last_previous.replace(day=1), first_this


def _spy_change(closes: Optional[pd.Series], start: date, end: date) -> Optional[float]:
    """The S&P 500's (SPY) % change from the close before `start` to the close on or before `end`."""
    if closes is None or closes.empty:
        return None
    before, until = closes[closes.index < start], closes[closes.index <= end]
    if before.empty or until.empty:
        return None
    return (float(until.iloc[-1]) / float(before.iloc[-1]) - 1) * 100


def scorecard_text(month_start: date, links: list[dict], spy: Optional[pd.Series]) -> str:
    """The scorecard's Telegram text (HTML)."""
    lines = [f"\U0001f4ca <b>Finnbot ideas you acted on in {month_start:%B}</b>"]
    returns, benchmarks = [], []
    for link in sorted(links, key=lambda l: l["first_seen"]):
        paid, last = _num(link.get("paid")), _num(link.get("last_price"))
        change = (last / paid - 1) * 100 if paid and last else None
        notes = [{"target": "reached its target", "stop": "hit its stop"}.get(link.get("hit") or "", ""),
                 "sold" if link.get("closed_at") else ""]
        note = ", ".join(n for n in notes if n)
        lines.append(f"• {html.escape(link['symbol'])}, {link['list'].replace('_', '-')} idea "
                     f"{_short_date(link['idea_at'])}: " + (f"{change:+.1f}%" if change is not None else "no price")
                     + (f" ({note})" if note else ""))
        if change is not None:
            returns.append(change)
            first, last_seen = _when(link["first_seen"]), _when(link.get("last_seen")) or _when(link["first_seen"])
            spy_change = _spy_change(spy, market_date(first), market_date(last_seen))
            if spy_change is not None:
                benchmarks.append(spy_change)
    if returns:
        average = f"\nAverage {sum(returns) / len(returns):+.1f}%"
        if len(benchmarks) == len(returns):
            average += f", against {sum(benchmarks) / len(benchmarks):+.1f}% for the S&P 500 over the same days"
        lines.append(average + ".")
    return "\n".join(lines)


def send_idea_scorecards(now: datetime, users: Optional[list[str]] = None, path: Optional[Path] = None) -> int:
    """
    Claude-authored (2026-10-22): from the first cron digest run of a month, each user with Telegram gets one message
    about the positions they bought from ideas in the previous month (open or sold since): each one's change since
    it was bought, and the average beside the S&P 500 over the same days. Nothing is sent to a user without such
    positions. Never raises. Returns how many went out.
    """
    try:
        from src.personalized.user_settings import get_active_users
        from src.telegram_alert.notices import send_notice_now
        start, end = _previous_month(market_date(now))
        state = load_state(path).get("users", {})
        cards = {}
        for user_name in users if users is not None else get_active_users():
            user = state.get(user_name) or {}
            links = [link for link in list(user.get("open", {}).values()) + user.get("closed", [])
                     if start <= market_date(_when(link["first_seen"])) < end]
            if links and _has_telegram(user_name):
                cards[user_name] = links
        if not cards:
            return 0
        earliest = min(market_date(_when(link["first_seen"])) for links in cards.values() for link in links)
        try:
            spy = track_record.fetch_daily_closes([track_record.BENCHMARK], earliest - timedelta(days=7)).get(
                track_record.BENCHMARK)
        except Exception as e:  # The scorecard goes out without the comparison
            print(f"[WARNING] Scorecard: no S&P 500 prices ({e}).")
            spy = None
        sent = 0
        for user_name, links in cards.items():
            if send_notice_now(user_name, "idea scorecard", f"{start:%Y-%m}", scorecard_text(start, links, spy),
                               cooldown_hours=NOTICE_COOLDOWN_HOURS):
                sent += 1
        return sent
    except Exception as e:
        print(f"[WARNING] Idea scorecards failed ({type(e).__name__}: {e}).")
        return 0
