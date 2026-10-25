# File to publish the digest's glossary as a web page (GitHub Pages), so the email can link to it instead of carrying it.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-25, at Andres's request.

Why: the glossary was about 10 KB of every email, the first thing cut from a big portfolio's digest to stay under
Gmail's ~102 KB clipping limit. The same text for everyone now lives on one public page, built from
src/digest/glossary.py, and the email shows a one-line link to it (params_dict["DIGEST"]["GLOSSARY_URL"]).
2026-10-25 (Andres): a designed page (search, topic tabs, the email's signal colours) with a section on how the swing
and long-term ideas are picked. Its numbers are read from params_dict, and its signal colours and rubric labels from
create_html, so the page can't disagree with what the digest does.

Where it's published: a separate public repo (github.com/andresc44/finnbot-glossary) served by GitHub Pages, so this
repo stays private. A clone of it lives at GLOSSARY_SITE_DIR (.env; default ~/finnbot-glossary). publish() writes
index.html there and commits and pushes it only when the page changed. Every cron digest run calls
publish_if_changed() (digest_payload.__main__), so an edit to glossary.py, or a setting changed from the settings
sheet, is on the page within the hour. Pushing uses this machine's SSH key; a failure is logged and never stops a
digest. The page holds no user data: only the terms, how to read them, and how the ideas are found.

Usage (from the project root):
  venv/bin/python -m src.digest.glossary_page --write /tmp/glossary.html   # build the page to look at it
  venv/bin/python -m src.digest.glossary_page --publish                    # write, commit, and push if changed
"""
import argparse
import html
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config.parameters import params_dict
from src.digest.create_html import LONG_TERM_SCORES, SENTIMENTS, SIGNALS, SWING_SCORES
from src.digest.glossary import GLOSSARY
from src.llm import llm_models

SITE_DIR_KEY = "GLOSSARY_SITE_DIR"
DEFAULT_SITE_DIR = Path.home() / "finnbot-glossary"
COMMIT_MESSAGE = "Update the glossary (published by Finnbot)"
METHOD_ID = "how-ideas-are-picked"

# Each topic's icon and tile colour (light, dark), in the spirit of the email's section icons
TOPIC_STYLE = {
    "Signals & ideas": ("🚦", "#FEF3C7", "#3A2E0B"),
    "Your portfolio": ("💼", "#E0E7FF", "#1E2550"),
    "Technical indicators": ("📈", "#E0F2FE", "#0C2D44"),
    "Valuation & fundamentals": ("🏦", "#DCFCE7", "#0E3320"),
    "Market indicators": ("🌐", "#EDE9FE", "#2A2150"),
}
DEFAULT_TOPIC_STYLE = ("📘", "#F1F5F9", "#1E293B")
# Signal grade dots, as in the email's market section
GRADE_DOTS = (("Good", "#16A34A"), ("Caution", "#F59E0B"), ("Warning", "#DC2626"), ("No clear signal", "#94A3B8"))
# How the scoring model is named on the page; any other model shows its id
MODEL_NAMES = {"deepseek-flash": "DeepSeek V4.1 Flash", "claude-haiku-5-5": "Claude Haiku 5.5",
               "claude-sonnet-5-5": "Claude Sonnet 5.5", "claude-opus-5-5": "Claude Opus 5.5"}

STYLE = """
:root{color-scheme:light dark;--bg:#F4F6FA;--surface:#FFFFFF;--surface-2:#F8FAFC;--ink:#0F2A44;--text:#334155;
--muted:#64748B;--line:#E2E8F0;--accent:#0B6BCB;--accent-soft:#EAF2FB;--shadow:0 1px 2px rgba(15,42,68,.06),
0 4px 16px rgba(15,42,68,.06);--green:#15803D;--amber:#B45309;--on-track:#FFFFFF;}
@media (prefers-color-scheme:dark){:root{--bg:#0B1220;--surface:#121B2B;--surface-2:#0F1726;--ink:#E5EDF5;
--text:#C7D2E0;--muted:#94A3B8;--line:#22304A;--accent:#60A5FA;--accent-soft:#16263D;--shadow:none;
--green:#4ADE80;--amber:#FBBF24;--on-track:#0B1220;}}
*{box-sizing:border-box;}
[hidden]{display:none !important;}
html{scroll-behavior:smooth;}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",
Roboto,Helvetica,Arial,sans-serif;-webkit-text-size-adjust:100%;}
a{color:var(--accent);}
.wrap{max-width:880px;margin:0 auto;padding:0 16px;}
header.hero{background:linear-gradient(135deg,#0F2A44 0%,#0B4F8A 60%,#0B6BCB 100%);color:#FFFFFF;padding:40px 0 28px;}
.brand{display:inline-flex;align-items:center;gap:8px;font-size:13px;font-weight:700;letter-spacing:.08em;
text-transform:uppercase;color:#BFDBFE;}
.hero h1{font-size:34px;line-height:1.15;margin:10px 0 8px;letter-spacing:-.01em;}
.hero p{margin:0;color:#DBEAFE;max-width:600px;}
.facts{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 20px;padding:0;list-style:none;}
.facts li{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.18);border-radius:999px;padding:4px 12px;
font-size:13px;color:#EFF6FF;}
.search{position:relative;max-width:560px;}
.search input{width:100%;font:inherit;font-size:16px;padding:13px 44px 13px 44px;border-radius:12px;border:0;
background:#FFFFFF;color:#0F2A44;box-shadow:0 6px 20px rgba(0,0,0,.18);}
.search input:focus{outline:3px solid #93C5FD;outline-offset:2px;}
.search .icon{position:absolute;left:15px;top:50%;transform:translateY(-50%);font-size:16px;opacity:.6;}
.search kbd{position:absolute;right:12px;top:50%;transform:translateY(-50%);font:12px/1 ui-monospace,monospace;
color:#64748B;border:1px solid #CBD5E1;border-radius:6px;padding:4px 7px;background:#F8FAFC;}
.result{min-height:20px;margin:10px 0 0;font-size:14px;color:#DBEAFE;}
nav.tabs{position:sticky;top:0;z-index:5;background:var(--bg);background:color-mix(in srgb,var(--bg) 88%,transparent);
backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);border-bottom:1px solid var(--line);}
nav.tabs .wrap{display:flex;gap:8px;overflow-x:auto;padding:10px 16px;scrollbar-width:none;}
nav.tabs .wrap::-webkit-scrollbar{display:none;}
@media (min-width:820px){nav.tabs .wrap{flex-wrap:wrap;overflow:visible;}
.topic,.term,.method{scroll-margin-top:124px;}}
nav.tabs a{flex:none;display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:999px;
background:var(--surface);border:1px solid var(--line);color:var(--ink);font-size:14px;font-weight:600;
text-decoration:none;}
nav.tabs a:hover,nav.tabs a:focus-visible{border-color:var(--accent);color:var(--accent);}
nav.tabs a.method-tab{background:var(--accent-soft);color:var(--accent);border-color:transparent;}
main{padding:24px 0 48px;}
.topic{background:var(--surface);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow);
margin:0 0 20px;overflow:hidden;scroll-margin-top:72px;}
.topic-head{display:flex;align-items:center;gap:12px;padding:18px 20px 14px;}
.tile{flex:none;display:grid;place-items:center;width:42px;height:42px;border-radius:12px;background:var(--tile-light,#F1F5F9);
font-size:21px;}
@media (prefers-color-scheme:dark){.tile{background:var(--tile-dark,#1E293B);}}
.topic h2{margin:0;color:var(--ink);font-size:19px;line-height:1.3;}
.topic .count{color:var(--muted);font-size:13px;font-weight:500;}
dl{margin:0;}
.term{display:grid;grid-template-columns:minmax(150px,210px) 1fr;gap:4px 20px;padding:13px 20px;
border-top:1px solid var(--line);scroll-margin-top:72px;}
.term:target{background:var(--accent-soft);box-shadow:inset 3px 0 0 var(--accent);}
dt{color:var(--ink);font-weight:700;line-height:1.45;}
dt a.name{color:inherit;text-decoration:none;}
dt a.name:hover{color:var(--accent);}
dd{margin:0;}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:13px;font-weight:700;line-height:1.6;}
.extras{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}
.dot{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--muted);margin-right:10px;}
.dot i{width:10px;height:10px;border-radius:50%;display:inline-block;}
.empty{display:none;text-align:center;color:var(--muted);padding:32px 16px;}
.method{scroll-margin-top:72px;margin-top:36px;}
.method-head{display:flex;gap:14px;align-items:flex-start;margin-bottom:16px;}
.method-head h2{margin:0 0 4px;color:var(--ink);font-size:24px;line-height:1.25;}
.method-head p{margin:0;color:var(--muted);}
.card{background:var(--surface);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow);
padding:18px 20px;margin:0 0 16px;}
.card h3{margin:0 0 6px;color:var(--ink);font-size:17px;}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 0;padding:0;list-style:none;}
.chips li{background:var(--surface-2);border:1px solid var(--line);border-radius:10px;padding:6px 12px;font-size:14px;
color:var(--ink);}
.chips b{color:var(--accent);}
.tracks{display:grid;grid-template-columns:1fr;gap:16px;}
@media (min-width:820px){.tracks{grid-template-columns:1fr 1fr;}}
.track{border-top:4px solid var(--track);margin:0;}
.track.long{--track:var(--green);}
.track.swing{--track:var(--amber);}
.track-head{display:flex;align-items:center;gap:10px;margin-bottom:4px;}
.track-head .tile{width:36px;height:36px;font-size:18px;}
.track .sub{color:var(--muted);font-size:14px;margin:0 0 12px;}
ol.steps{list-style:none;margin:0;padding:0;counter-reset:step;}
ol.steps>li{position:relative;padding:0 0 14px 38px;counter-increment:step;}
ol.steps>li::before{content:counter(step);position:absolute;left:0;top:0;width:26px;height:26px;border-radius:50%;
background:var(--track);color:var(--on-track);font-size:13px;font-weight:800;display:grid;place-items:center;}
ol.steps>li::after{content:"";position:absolute;left:12px;top:30px;bottom:4px;width:2px;background:var(--line);}
ol.steps>li:last-child::after{display:none;}
ol.steps b.step{display:block;color:var(--ink);}
ol.steps ul,ol.steps .lead{margin:4px 0 0;font-size:14.5px;}
ol.steps ul{padding-left:18px;}
ol.steps ul li{margin:2px 0;}
.rubric{margin:8px 0 2px;}
.bar{display:flex;height:12px;border-radius:999px;overflow:hidden;background:var(--line);}
.bar span{display:block;height:100%;}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:8px;font-size:13px;color:var(--muted);}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px;}
.pass{display:inline-block;margin-top:8px;font-size:13px;font-weight:700;color:var(--ink);background:var(--surface-2);
border:1px solid var(--line);border-radius:999px;padding:3px 10px;}
.callout{display:flex;gap:12px;background:var(--accent-soft);border-radius:14px;padding:14px 16px;font-size:14.5px;
color:var(--ink);margin:0 0 12px;}
.callout.warn{background:#FEF3C7;background:color-mix(in srgb,#F59E0B 14%,var(--surface));}
.callout .tile{width:32px;height:32px;font-size:16px;background:var(--surface);}
@media (prefers-color-scheme:dark){.callout .tile{background:var(--surface);}}
footer{color:var(--muted);font-size:13px;text-align:center;padding:8px 16px 40px;}
.top{position:fixed;right:16px;bottom:16px;width:44px;height:44px;border-radius:50%;background:var(--ink);
color:var(--bg);display:grid;place-items:center;text-decoration:none;font-size:20px;box-shadow:0 6px 18px rgba(0,0,0,.2);
opacity:0;pointer-events:none;transition:opacity .2s;}
.top.show{opacity:.9;pointer-events:auto;}
:focus-visible{outline:3px solid var(--accent);outline-offset:2px;}
@media (max-width:600px){.hero h1{font-size:28px;}.term{grid-template-columns:1fr;gap:2px;padding:12px 16px;}
.topic-head{padding:16px 16px 12px;}.card{padding:16px;}.search kbd{display:none;}}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto;}.top{transition:none;}}
"""

SCRIPT = """
(function(){
  var q=document.getElementById('q'),terms=[].slice.call(document.querySelectorAll('.term')),
      topics=[].slice.call(document.querySelectorAll('.topic')),result=document.getElementById('result'),
      empty=document.getElementById('empty'),method=document.getElementById('%(method)s'),
      top=document.getElementById('top');
  function run(){
    var v=q.value.trim().toLowerCase(),n=0;
    terms.forEach(function(t){var hit=!v||t.getAttribute('data-text').indexOf(v)>=0;t.hidden=!hit;if(hit)n++;});
    topics.forEach(function(s){s.hidden=!!v&&!s.querySelector('.term:not([hidden])');});
    method.hidden=!!v;
    empty.style.display=(v&&n===0)?'block':'none';
    result.textContent=v?(n+(n===1?' matching term':' matching terms')):'';
  }
  q.addEventListener('input',run);
  q.addEventListener('keydown',function(e){if(e.key==='Escape'){q.value='';run();q.blur();}});
  document.addEventListener('keydown',function(e){
    if(e.key==='/'&&document.activeElement!==q){e.preventDefault();q.focus();}
  });
  window.addEventListener('scroll',function(){top.classList.toggle('show',window.scrollY>600);},{passive:true});
})();
"""


def slug(text: str) -> str:
    """A term's anchor, e.g. "RSI 14" -> "rsi-14", "P/E (trailing, forward)" -> "p-e-trailing-forward"."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "term"


def _num(value: Any) -> str:
    """A setting as the page writes it: 9000.0 -> "9,000", 1.5 -> "1.5"."""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:g}"


def _pct(fraction: float) -> str:
    return f"{abs(fraction) * 100:g}%"


def _bounds(rules: Iterable[tuple]) -> dict[str, dict[str, Any]]:
    """{field: {operator: threshold}} from (field, operator, threshold, ...) rules, in their order."""
    bounds: dict[str, dict[str, Any]] = {}
    for field, operator, threshold, *_ in rules:
        bounds.setdefault(field, {})[operator] = threshold
    return bounds


def _rule_phrases(rules: Iterable[tuple]) -> list[str]:
    """The screen's rules in plain words, e.g. ("profitMargins", ">=", 0.07) -> "net profit margin of at least 7%"."""
    phrases = []
    for field, b in _bounds(rules).items():
        low = next((b[op] for op in (">=", ">") if op in b), None)
        high = next((b[op] for op in ("<=", "<") if op in b), None)
        if field == "averageVolume" and low is not None:
            phrases.append(f"at least {_num(low / 1e6)} million shares traded a day")
        elif field == "numberOfAnalystOpinions" and low is not None:
            phrases.append(f"followed by at least {_num(low)} analysts")
        elif field == "recommendationMean" and high is not None:
            phrases.append(f"analysts' average rating of {_num(high)} or better (1 = Strong Buy, 3 = Hold)")
        elif field == "forwardPE" and high is not None:
            phrases.append(f"expected to be profitable over the next year, at a forward P/E of {_num(high)} or less")
        elif field == "profitMargins" and low is not None:
            phrases.append(f"net profit margin of at least {_pct(low)}")
        elif field == "revenueGrowth" and low is not None:
            phrases.append(f"revenue up at least {_pct(low)} on a year ago" if low >= 0
                           else f"revenue no more than {_pct(low)} below a year ago")
        elif field == "returnOnEquity" and low is not None:
            phrases.append(f"return on equity of at least {_pct(low)}")
        elif field == "revenueGrowthTTMYoy" and low is not None:
            phrases.append(f"revenue up at least {_num(low)}% over the last 12 months")
        elif field == "epsGrowthTTMYoy" and low is not None:
            phrases.append("earnings per share growing over the last 12 months")
        elif field == "totalDebt/totalEquityQuarterly" and high is not None:
            phrases.append(f"debt at most {_num(high)}× equity")
        elif field == "peTTM" and high is not None:
            phrases.append(f"trailing P/E between {_num(low or 0)} and {_num(high)}")
        elif field == "adx" and high is not None:
            phrases.append(f"a trend strength (ADX) of {_num(high)} or less: not already running hot")
        else:  # A rule added since: shown as written
            phrases.append(" and ".join(f"{field} {op} {_num(value) if isinstance(value, (int, float)) else value}"
                                        for op, value in b.items()))
    return phrases


def _list(items: Iterable[str]) -> str:
    return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + "</ul>"


def _rubric(spec: tuple, colours: tuple[str, ...], pass_mark: float) -> str:
    """The score's parts as one bar (each part as wide as its maximum), a legend, and the pass mark."""
    total = sum(top for _, _, top in spec) or 100
    bar = "".join(f'<span style="width:{top / total * 100:g}%;background:{colour}"></span>'
                  for (_, _, top), colour in zip(spec, colours))
    legend = "".join(f'<span><i style="background:{colour}"></i>{html.escape(label)} {top}</span>'
                     for (_, label, top), colour in zip(spec, colours))
    return (f'<div class="rubric"><div class="bar" role="img" aria-label="Score out of {total}">{bar}</div>'
            f'<div class="legend">{legend}</div><span class="pass">Needs {_num(pass_mark)}+ out of {total}</span></div>')


def _method_html() -> str:
    """How the ideas are picked, for the curious: the shared starting point, then each list's steps."""
    p = params_dict
    swing = p.get("SWING_SCREEN") or {}
    setup, levels = swing.get("SETUP") or {}, swing.get("LEVELS") or {}
    pullback, uptrend = setup.get("PULLBACK") or {}, setup.get("NEW_UPTREND") or {}
    early = p.get("SWING_EARLY_ENTRY") or {}
    rescore = p.get("IDEA_RESCORE") or {}
    model = llm_models.job_settings("UNIVERSAL").get("model")
    model_name = MODEL_NAMES.get(model, model or "an AI model")
    tech = " or ".join(p.get("TECH_SECTORS") or ["Technology"])

    universe = []
    if p.get("MKTCAP_MIN"):
        universe.append(f"<b>${_num(p['MKTCAP_MIN'] / 1000)} billion+</b> in market value")
    if p.get("VOLUME_MIN"):
        universe.append(f"<b>{_num(p['VOLUME_MIN'])} million+</b> shares traded a day")
    if p.get("LASTSALE_MIN"):
        universe.append(f"<b>${_num(p['LASTSALE_MIN'])}+</b> a share")
    universe_html = "".join(f"<li>{item}</li>" for item in ["Listed in the <b>US</b> (NYSE, Nasdaq, NYSE American)"]
                            + universe)

    long_pick = [f"Up to {p['LONG_TERM_N_AMOUNT']} ideas (your digest may be set to more or fewer) scoring "
                 f"{_num(p['MIN_LONG_SCORE'])} or more, highest first.",
                 "Each from a different sector, so the list isn't one bet."]
    if p.get("LONG_TERM_MAX_TECH_PICKS") is not None:
        long_pick.append(f"At most {p['LONG_TERM_MAX_TECH_PICKS']} tech stocks ({tech}), which may share a sector.")
    if p.get("LONG_TERM_MIN_NON_TECH_PICKS"):
        long_pick.append(f"At least {p['LONG_TERM_MIN_NON_TECH_PICKS']} outside tech, which may score from "
                         f"{_num(p.get('LONG_TERM_NON_TECH_MIN_SCORE', p['MIN_LONG_SCORE']))}.")
    long_steps = [
        ("Quality screen", _rule_phrases(p.get("YAHOO_FILTERS") or [])),
        ("Confirmation from the reported financials", _rule_phrases(
            (rule[1], rule[2], rule[3]) for rule in p.get("FINNHUB_FILTERS") or [])),
        ("Not overheated", _rule_phrases((rule[0], rule[2], rule[3]) for rule in p.get("TWELVEDATA_FILTERS") or [])),
        ("Scored by AI", None),
        ("Picked", long_pick),
    ]

    setups = []
    if pullback.get("ENABLED", True):
        setups.append(f"Dip in an uptrend: at least {_num(pullback.get('MIN_PULLBACK_ATR', 1.5))} normal days' ranges "
                      f"(ATR) below its {pullback.get('LOOKBACK_DAYS', 20)}-day high, no more than "
                      f"{_num(pullback.get('MAX_PULLBACK_PCT', 30))}% down, and at most "
                      f"{_num(pullback.get('MAX_BELOW_SMA200_PCT', 5))}% below its 200-day average.")
    if uptrend.get("ENABLED", True):
        setups.append(f"Fresh turn up: its 9-day average crossed above its 21-day average in the last "
                      f"{uptrend.get('CROSS_WITHIN_DAYS', 5)} sessions.")
    setups.append(f"Either way: at least ${_num(setup.get('MIN_PRICE', 10))} a share and "
                  f"${_num(setup.get('MIN_DOLLAR_VOLUME_M', 20))} million traded a day, an RSI between "
                  f"{setup.get('MIN_RSI', 20)} and {setup.get('MAX_RSI', 65)}"
                  + (", not still falling" if setup.get("EXCLUDE_FALLING_KNIVES", True) else "")
                  + f", and no earnings report within {setup.get('EARNINGS_BLACKOUT_SESSIONS', 5)} trading days.")
    plan = [f"The stop sits just under the nearest support, at least {_num(levels.get('MIN_RISK_ATR', 1.5))} ATR "
            f"below the entry.",
            f"The target is the nearest resistance above (a recent high, a moving average, or the upper Bollinger "
            f"Band), {_num(levels.get('MIN_TARGET_ATR', 1.5))} to {_num(levels.get('MAX_TARGET_ATR', 8))} ATR up.",
            f"Plans with less than {_num(swing.get('MIN_PLAN_RR', 1.5))}× as much to gain as to lose are dropped; "
            f"the best {swing.get('MAX_CANDIDATES', 40)} go on to scoring."]
    swing_pick = [f"Up to {p['SWING_TERM_N_AMOUNT']} ideas (your digest may be set to more or fewer) scoring "
                  f"{_num(p['MIN_SWING_SCORE'])} or more.",
                  f"Still early in the move: no more than {_num(early.get('MAX_RUN_ATR', 3))} ATR above the "
                  f"{early.get('LOOKBACK_DAYS', 10)}-day low, RSI {early.get('MAX_RSI', 70)} or less, and not above "
                  f"the upper Bollinger Band.",
                  "More than half of them from different sectors."]
    swing_steps = [
        ("Looser quality screen", _rule_phrases(swing.get("YAHOO_FILTERS") or [])),
        ("A setup in the daily prices", setups),
        ("A trade plan, worked out by Finnbot", plan),
        ("Scored by AI", None),
        ("Picked", swing_pick),
    ]

    def steps(items: list, spec: tuple, colours: tuple, pass_mark: float, scoring: str) -> str:
        out = []
        for title, body in items:
            if body is None:  # The scoring step: what the model scores, as a bar
                content = f'<div class="lead">{html.escape(scoring)}</div>{_rubric(spec, colours, pass_mark)}'
            else:
                content = _list(body)
            out.append(f'<li><b class="step">{html.escape(title)}</b>{content}</li>')
        return f'<ol class="steps">{"".join(out)}</ol>'

    long_html = steps(long_steps, LONG_TERM_SCORES, ("#15803D", "#22C55E", "#4ADE80", "#86EFAC"),
                      p["MIN_LONG_SCORE"], "Each survivor is scored out of 100 on four parts:")
    swing_html = steps(swing_steps, SWING_SCORES, ("#B45309", "#F59E0B", "#FBBF24", "#FCD34D"),
                       p["MIN_SWING_SCORE"], "Each plan is scored out of 100; the reward against the risk is "
                                             "worked out by Finnbot, not the model:")
    close_calls = (f"A score within {_num(rescore.get('BELOW_MIN', 5))} points of the pass mark is scored "
                   f"{rescore.get('EXTRA_RUNS', 2)} more times and the middle score kept, so one unlucky run can't "
                   f"decide a close call.") if rescore.get("EXTRA_RUNS") else ""

    return f"""
<section class="method" id="{METHOD_ID}" aria-labelledby="{METHOD_ID}-title">
  <div class="method-head"><span class="tile" style="--tile-light:#EAF2FB;--tile-dark:#16263D">🧭</span>
    <div><h2 id="{METHOD_ID}-title">How the ideas are picked</h2>
    <p>For the curious: the path from every US stock to the handful in your digest, every time it's built.</p></div>
  </div>
  <div class="card">
    <h3>Where both lists start</h3>
    <div>Every digest starts from the same pool of several hundred established stocks:</div>
    <ul class="chips">{universe_html}</ul>
  </div>
  <div class="tracks">
    <article class="card track long">
      <div class="track-head"><span class="tile" style="--tile-light:#DCFCE7;--tile-dark:#0E3320">🌱</span>
        <h3>Long-term ideas</h3></div>
      <p class="sub">Businesses worth owning for years at today's price.</p>
      {long_html}
    </article>
    <article class="card track swing">
      <div class="track-head"><span class="tile" style="--tile-light:#FEF3C7;--tile-dark:#3A2E0B">⚡</span>
        <h3>Swing ideas</h3></div>
      <p class="sub">Trades over the next few days to weeks, each with an entry, a stop, and a target.</p>
      {swing_html}
    </article>
  </div>
  <div class="callout"><span class="tile">🤖</span><div><b>The scoring model.</b> Survivors are scored by
    {html.escape(model_name)}, chosen after comparing it with stronger models on the same stocks. {close_calls}
    Each digest's “About this digest” shows how many stocks got through each stage that day.</div></div>
  <div class="callout warn"><span class="tile">⚠️</span><div><b>Not financial advice.</b> These are AI-scored ideas
    from public data. A day with few strong candidates has fewer ideas, or none. Always do your own research before
    buying.</div></div>
</section>"""


def _extras(term: str) -> str:
    """Colour samples under a few terms: the signals' and sentiments' badges, and the market grade dots."""
    if term == "Sentiment":
        return '<div class="extras">' + "".join(
            f'<span class="pill" style="color:{fg};background:{bg}">{html.escape(name)}</span>'
            for name, (fg, bg) in SENTIMENTS.items()) + "</div>"
    if term == "Signal grades":
        return '<div class="extras">' + "".join(
            f'<span class="dot"><i style="background:{colour}"></i>{name}</span>' for name, colour in GRADE_DOTS) + "</div>"
    return ""


def _term_name(term: str) -> str:
    """The term as shown: a signal in the email's badge colours, anything else as text."""
    signal = next((style for style in SIGNALS.values() if style[0] == term), None)
    if signal:
        return f'<span class="pill" style="color:{signal[1]};background:{signal[2]}">{html.escape(term)}</span>'
    return html.escape(term)


def page_html() -> str:
    """The whole page: every glossary topic and term (holdings and ideas-only alike), then how the ideas are picked."""
    tabs, topics, used, count = [], [], set(), 0
    for category, terms in GLOSSARY:
        icon, light, dark = TOPIC_STYLE.get(category, DEFAULT_TOPIC_STYLE)
        topic_id = slug(category)
        tabs.append(f'<a href="#{topic_id}"><span aria-hidden="true">{icon}</span>{html.escape(category)}</a>')
        rows = []
        for term, text in terms:
            anchor = slug(term)
            while anchor in used:  # Two terms with the same anchor keep apart
                anchor += "-2"
            used.add(anchor)
            searchable = html.escape(f"{term} {text}".lower(), quote=True)
            rows.append(f'<div class="term" id="{anchor}" data-text="{searchable}"><dt><a class="name" '
                        f'href="#{anchor}">{_term_name(term)}</a></dt><dd>{html.escape(text)}{_extras(term)}</dd></div>')
        count += len(terms)
        topics.append(
            f'<section class="topic" id="{topic_id}" style="--tile-light:{light};--tile-dark:{dark}">'
            f'<div class="topic-head"><span class="tile" aria-hidden="true">{icon}</span><div><h2>'
            f'{html.escape(category)}</h2><span class="count">{len(terms)} terms</span></div></div>'
            f'<dl>{"".join(rows)}</dl></section>')
    tabs.append(f'<a class="method-tab" href="#{METHOD_ID}"><span aria-hidden="true">🧭</span>How ideas are picked</a>')
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Finnbot Glossary</title>\n"
        '<meta name="description" content="The terms, abbreviations, and indicators in the Finnbot digest, how to '
        'read them, and how its swing and long-term ideas are picked.">\n'
        '<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22>'
        '<text y=%22.9em%22 font-size=%2290%22>📖</text></svg>">\n'
        f"<style>{STYLE}</style>\n</head>\n<body>\n"
        '<header class="hero"><div class="wrap">\n'
        '<span class="brand"><span aria-hidden="true">📖</span>Finnbot</span>\n'
        "<h1>Glossary</h1>\n"
        "<p>Every term, abbreviation, and indicator in your Finnbot digest, in plain words, and how its ideas are "
        "picked.</p>\n"
        f'<ul class="facts"><li>{count} terms</li><li>{len(GLOSSARY)} topics</li><li>Updated with Finnbot</li></ul>\n'
        '<div class="search" role="search"><span class="icon" aria-hidden="true">🔍</span>'
        '<input id="q" type="search" placeholder="Search terms, e.g. RSI, P/E, stop" autocomplete="off" '
        'aria-label="Search the glossary"><kbd aria-hidden="true">/</kbd></div>\n'
        '<div class="result" id="result" aria-live="polite"></div>\n'
        "</div></header>\n"
        f'<nav class="tabs" aria-label="Topics"><div class="wrap">{"".join(tabs)}</div></nav>\n'
        '<main><div class="wrap">\n'
        f'{"".join(topics)}\n'
        '<p class="empty" id="empty">No term matches that. Try a shorter word, like “ratio” or “average”.</p>\n'
        f"{_method_html()}\n"
        "</div></main>\n"
        "<footer>Finnbot · AI-generated signals and ideas are opinions, not financial advice.</footer>\n"
        '<a class="top" id="top" href="#" aria-label="Back to top">↑</a>\n'
        f"<script>{SCRIPT % {'method': METHOD_ID}}</script>\n"
        "</body>\n</html>\n"
    )


def site_dir() -> Path:
    """The local clone of the Pages repo: GLOSSARY_SITE_DIR in .env, else ~/finnbot-glossary."""
    value = (os.environ.get(SITE_DIR_KEY) or "").strip()
    return Path(value).expanduser() if value else DEFAULT_SITE_DIR


def _git(directory: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, timeout=60)


def publish(directory: Optional[Path] = None) -> str:
    """
    Writes index.html (and .nojekyll, so GitHub serves the file as is) into the clone, and commits and pushes when
    the page changed. Returns what happened, in one line. Raises RuntimeError when git fails.
    """
    directory = directory or site_dir()
    if not (directory / ".git").exists():
        raise RuntimeError(f"{directory} isn't a clone of the glossary repo (set {SITE_DIR_KEY} in .env).")
    page = directory / "index.html"
    new = page_html()
    if page.exists() and page.read_text(encoding="utf-8") == new:
        return "Glossary page unchanged."
    page.write_text(new, encoding="utf-8")
    (directory / ".nojekyll").touch()
    for args in (("add", "index.html", ".nojekyll"), ("commit", "-m", COMMIT_MESSAGE), ("push", "origin", "HEAD")):
        result = _git(directory, *args)
        if result.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {(result.stderr or result.stdout).strip()[:300]}")
    return "Glossary page updated and pushed."


def publish_if_changed() -> None:
    """For the cron digest run: publish() when a clone is set up, never raising. Quiet when nothing changed."""
    if not (site_dir() / ".git").exists():
        return  # Not set up on this machine
    try:
        status = publish()
        if status != "Glossary page unchanged.":
            print(f"[glossary] {status}")
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        print(f"[WARNING] [glossary] Couldn't publish the glossary page: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build or publish the Finnbot glossary page")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", type=Path, metavar="PATH", help="Write the page to PATH only.")
    group.add_argument("--publish", action="store_true", help="Write it to the Pages clone, commit, and push.")
    args = parser.parse_args()
    if args.write:
        args.write.write_text(page_html(), encoding="utf-8")
        print(f"Wrote {args.write}")
    else:
        print(publish())
