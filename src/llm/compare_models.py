# File to compare LLM settings (model + effort) on identical inputs: new-stock scoring, holdings reviews, and the news.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-26, at Andres's request, so the comparisons
behind docs/model_selection.md can be repeated when a new model comes out (docs/admin_handbook.md, section 15). The
2026-10-22 scripts were working files on another computer and were never saved.

Three steps, each its own command (run from the project root, on the VM under the shared lock and maintenance mode):

  1. capture   Runs the real universal and personal content builds with every LLM call replaced by a recorder, and
               saves what each call WOULD have received: the long-term and swing candidates, each user's holdings
               payloads, and the holdings' company names. Costs no LLM money (market data calls only, and it takes
               about 25 minutes for the ideas). It can still send a stop / target Telegram alert, like any digest run.
      python -m src.llm.compare_models capture --out test_output/model_compare/2026-11-01 [--user Andres] [--skip ideas]

  2. score     Replays the saved inputs through the same entry points the digest uses (run_llm_evaluation,
               run_holding_evaluation, fetch_symbol_news, fetch_live_macro_stories) once per setting and run, and
               saves every answer with its tokens, seconds, and cost. Usage goes to its own ledger (OUT/usage.jsonl),
               never data/usage/api_usage.jsonl. Second looks (HOLDINGS_RECHECK) are off unless --second-looks, so each
               answer is one model call. A run cut short (a limit, a spend cap) can be resumed: finished runs are skipped.
      python -m src.llm.compare_models score --inputs DIR --out DIR --jobs long_term,swing,holdings \\
          --setting claude-opus-5-5:medium --setting claude-haiku-5-5:low --setting deepseek-flash:high --runs 2

  3. report    Compares every setting with the reference: score gaps, ranking, pass / fail agreement, picks, label
               agreement, cost, and how much each model disagrees with itself. `show` prints the news side by side.
      python -m src.llm.compare_models report --results DIR --reference claude-opus-5-5:medium

A setting is "model[:effort]", the model a key of ALLOWED_CLAUDE_MODELS / ALLOWED_DEEPSEEK_MODELS (a new model goes
there first, with its price in API_PRICES) or, for the news job, a Gemini model. Every comparison is pairwise: each
run of a setting against each run of the reference, averaged; the reference's own runs against each other give the
best agreement any model can be expected to reach.
"""
import argparse
import json
import re
import sys
import time
from itertools import combinations
from pathlib import Path
from statistics import mean
from typing import Any, Optional

import pandas as pd

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config.parameters import params_dict

# From the most to the least bullish, as docs/model_selection.md measures how many steps more or less cautious a label is
LABEL_ORDER = ["buy_more", "hold", "profit_sell", "risky", "urgent_sell"]
SELL_LABELS = {"profit_sell", "urgent_sell"}
SCORE_KEYS = {"long_term": "longScore", "swing": "swingScore"}
HORIZONS = tuple(SCORE_KEYS)


# ---------------------------------------------------------------------------- Settings and files
def parse_setting(text: str) -> tuple[str, Optional[str]]:
    """'claude-haiku-5-5:low' -> ('claude-haiku-5-5', 'low'); 'gemini-3.5-flash-lite' -> (..., None)."""
    model, _, effort = text.strip().partition(":")
    if not model:
        raise ValueError(f"Not a setting: {text!r} (expected model[:effort])")
    return model, (effort or None)


def setting_problem(job: str, model: str, effort: Optional[str]) -> Optional[str]:
    """Why `model:effort` can't run `job`, or None: the news job takes a Gemini model; the others a model Finnbot allows."""
    from src.llm import llm_models
    if job == "news":
        return None if model.startswith("gemini-") else f"{model} isn't a Gemini model (the news job searches with Gemini)"
    efforts = llm_models.allowed_models().get(model)
    if efforts is None:
        return f"{model} isn't in ALLOWED_CLAUDE_MODELS / ALLOWED_DEEPSEEK_MODELS (add it, and its price, first)"
    if effort and effort not in efforts:
        return f"{model} accepts the efforts {', '.join(efforts)}, not {effort!r}"
    return None


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", text).strip("-")


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------- Metrics (pure functions)
def _ranks(values: pd.Series) -> pd.Series:
    return values.rank(method="average")


def rank_correlation(ref: dict[str, float], cand: dict[str, float]) -> Optional[float]:
    """Spearman correlation of the two scorings over the stocks both have (1 = the same order); None under 3 stocks."""
    common = sorted(set(ref) & set(cand))
    if len(common) < 3:
        return None
    a, b = _ranks(pd.Series([ref[t] for t in common])), _ranks(pd.Series([cand[t] for t in common]))
    value = a.corr(b)
    return None if pd.isna(value) else float(value)


def pass_agreement(ref: dict[str, float], cand: dict[str, float], mark: float) -> Optional[float]:
    """
    Agreement on which stocks pass `mark`: the F1 of the candidate's passing set against the reference's, one figure
    combining the share of the reference's stocks it finds and the share of its own the reference agrees with. None when
    neither passes anything.
    """
    common = set(ref) & set(cand)
    want = {t for t in common if ref[t] >= mark}
    got = {t for t in common if cand[t] >= mark}
    if not want and not got:
        return None
    hits = len(want & got)
    if hits == 0:
        return 0.0
    precision, recall = hits / len(got), hits / len(want)
    return 2 * precision * recall / (precision + recall)


def same_size_overlap(ref: dict[str, float], cand: dict[str, float], mark: float) -> Optional[float]:
    """If the reference passes K stocks, the share of the candidate's own top K that are among them (ignores a model
    scoring everything higher or lower)."""
    common = sorted(set(ref) & set(cand))
    want = {t for t in common if ref[t] >= mark}
    if not want:
        return None
    top = sorted(common, key=lambda t: cand[t], reverse=True)[:len(want)]
    return len(want & set(top)) / len(want)


def top_n_overlap(ref: dict[str, float], cand: dict[str, float], n: int) -> Optional[float]:
    """How many of the reference's top n stocks are in the candidate's top n, as a share of n (picks, ignoring the mix rules)."""
    common = sorted(set(ref) & set(cand))
    if not common or n <= 0:
        return None
    top = lambda scores: set(sorted(common, key=lambda t: scores[t], reverse=True)[:n])  # noqa: E731
    return len(top(ref) & top(cand)) / min(n, len(common))


def score_gap(ref: dict[str, float], cand: dict[str, float]) -> tuple[Optional[float], Optional[float]]:
    """(mean absolute gap, bias) in points over the common stocks; bias keeps the sign (+ = the candidate scores higher)."""
    common = sorted(set(ref) & set(cand))
    if not common:
        return None, None
    gaps = [cand[t] - ref[t] for t in common]
    return mean(abs(g) for g in gaps), mean(gaps)


def label_comparison(ref: dict[str, str], cand: dict[str, str]) -> dict[str, Optional[float]]:
    """
    Holdings labels over the common stocks: `agree` (the share given the same label), `steps` (mean steps more
    cautious, + = the candidate is more cautious, along LABEL_ORDER), `sell_caught` (the share of the reference's sell
    calls, profit or urgent sell, that the candidate also calls a sell), `false_sells` (the candidate's sell calls the
    reference doesn't make, as a share of the common stocks).
    """
    common = sorted(t for t in set(ref) & set(cand) if ref[t] in LABEL_ORDER and cand[t] in LABEL_ORDER)
    if not common:
        return {"agree": None, "steps": None, "sell_caught": None, "false_sells": None}
    ref_sells = [t for t in common if ref[t] in SELL_LABELS]
    return {
        "agree": sum(ref[t] == cand[t] for t in common) / len(common),
        "steps": mean(LABEL_ORDER.index(cand[t]) - LABEL_ORDER.index(ref[t]) for t in common),
        "sell_caught": (sum(cand[t] in SELL_LABELS for t in ref_sells) / len(ref_sells)) if ref_sells else None,
        "false_sells": sum(cand[t] in SELL_LABELS and ref[t] not in SELL_LABELS for t in common) / len(common),
    }


def _average(rows: list[dict[str, Optional[float]]]) -> dict[str, Optional[float]]:
    out = {}
    for key in rows[0] if rows else []:
        values = [r[key] for r in rows if r.get(key) is not None]
        out[key] = mean(values) if values else None
    return out


def compare_idea_runs(ref_runs: list[dict[str, float]], cand_runs: list[dict[str, float]], marks: tuple[float, ...],
                      n_picks: int, same: bool = False) -> dict[str, Optional[float]]:
    """Pairwise metrics, averaged: every candidate run against every reference run (`same`: the runs of one setting
    against each other, each pair once)."""
    pairs = list(combinations(ref_runs, 2)) if same else [(r, c) for r in ref_runs for c in cand_runs]
    rows = []
    for ref, cand in pairs:
        gap, bias = score_gap(ref, cand)
        row = {"gap": gap, "bias": bias, "rank": rank_correlation(ref, cand), "top_n": top_n_overlap(ref, cand, n_picks)}
        for mark in marks:
            row[f"pass_{mark:g}"] = pass_agreement(ref, cand, mark)
            row[f"overlap_{mark:g}"] = same_size_overlap(ref, cand, mark)
        rows.append(row)
    return _average(rows)


def compare_holding_runs(ref_runs: list[dict[str, str]], cand_runs: list[dict[str, str]],
                         same: bool = False) -> dict[str, Optional[float]]:
    pairs = list(combinations(ref_runs, 2)) if same else [(r, c) for r in ref_runs for c in cand_runs]
    return _average([label_comparison(ref, cand) for ref, cand in pairs])


# ---------------------------------------------------------------------------- Step 1: capture
def capture(args: argparse.Namespace) -> None:
    """Runs the content builds with the LLM calls replaced by recorders, and saves what they received."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    skip = set(filter(None, (args.skip or "").split(",")))
    meta: dict[str, Any] = {"captured_at": pd.Timestamp.now(tz="UTC").isoformat(), "users": {}}

    if "ideas" not in skip:
        import src.universal.recommendations.scorer as scorer
        import src.universal.universal_consolidated as universal

        def record_ideas(sanitized_inputs, config=None, tool_spec=None, issues=None, horizon="long_term"):
            _write(out / f"ideas_{horizon}.json", sanitized_inputs)
            print(f"Captured {len(sanitized_inputs)} {horizon} candidates.")
            return {}

        scorer.run_llm_evaluation = record_ideas
        universal.get_macro_stories = lambda: (None, [])  # One Gemini call that isn't being compared here
        universal.get_universal_content()

    if "holdings" not in skip:
        import src.personalized.personalized_consolidated as personal
        import src.personalized.recommendation as holdings
        from src.market.market_session import market_phase
        from datetime import datetime, timezone

        params_dict["DIGEST"]["REUSE_EVALUATION_MAX_AGE_MINUTES"] = 0  # Capture every holding, not just the stale ones

        def record_holdings(holding_payloads, api_key=None, config=None, market_session=None, user_name=None, source=""):
            _write(out / f"holdings_{user_name}.json", holding_payloads)
            meta["users"].setdefault(user_name, {})["market_session"] = market_session or market_phase(datetime.now(timezone.utc))
            print(f"Captured {len(holding_payloads)} holdings for {user_name}.")
            return {}, None

        def record_news(user_name, companies, *a, **k):
            _write(out / f"companies_{user_name}.json", companies)
            return {}, None

        holdings.run_holding_evaluation = record_holdings
        personal.fetch_holdings_news = record_news
        personal.get_portfolio_review = lambda *a, **k: (None, None)
        for user in args.user:
            personal.get_personal_content(user)
    _write(out / "meta.json", meta)
    print(f"Saved the inputs in {out}.")


# ---------------------------------------------------------------------------- Step 2: score
def _ledger_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _cost_since(ledger: Path, before: int) -> dict[str, Any]:
    rows = _ledger_rows(ledger)[before:]
    tokens = {k: sum(r.get(k, 0) for r in rows) for k in ("input_tokens", "output_tokens", "cache_write_tokens",
                                                          "cache_read_tokens", "search_queries")}
    return {"cost_usd": sum(r.get("cost_usd", 0) for r in rows), "calls": len(rows), **tokens}


def _key_for(provider: str, user: Optional[str]) -> str:
    """The user's own key for `provider`, else the shared one (the holdings go to that provider either way)."""
    from src.personalized.api_errors import shared_api_key
    from src.personalized.user_settings import get_llm_api_key
    try:
        return get_llm_api_key(user, provider) if user else shared_api_key(provider)
    except ValueError:
        return shared_api_key(provider)


def _run_job(job: str, model: str, effort: Optional[str], inputs: Path, user: Optional[str]) -> dict[str, Any]:
    """One run of one job. Returns {"results": ..., "inputs": n, "extra": ...}."""
    from src.llm import llm_models
    config = {"model": model, **({"effort": effort} if effort else {})}
    if job in HORIZONS:
        from src.universal.recommendations.LLM_recommendation import run_llm_evaluation
        sanitized = _read(inputs / f"ideas_{job}.json")
        return {"results": run_llm_evaluation(sanitized_inputs=sanitized, config=config, horizon=job), "inputs": len(sanitized)}
    if job == "holdings":
        from src.personalized.recommendation import run_holding_evaluation
        payloads = _read(inputs / f"holdings_{user}.json")
        session = (_read(inputs / "meta.json").get("users", {}).get(user) or {}).get("market_session")
        provider = llm_models.provider_of(model)
        results, key_error = run_holding_evaluation(payloads, api_key=_key_for(provider, user), config=config,
                                                    market_session=session, user_name="_experiment", source="experiment")
        return {"results": results, "inputs": len(payloads), "key_error": str(key_error) if key_error else None}
    if job == "news":
        return _run_news(model, inputs, user)
    raise ValueError(f"Unknown job {job!r}")


def _run_news(model: str, inputs: Path, user: Optional[str]) -> dict[str, Any]:
    """Holdings news and macro news with `model` (a Gemini model), through the digest's own functions."""
    import src.personalized.symbol_specific_news as holding_news
    import src.universal.market_news as macro_news
    from src.personalized.user_settings import get_gemini_api_key
    from src.personalized.api_errors import shared_api_key

    params_dict["GEMINI_MODELS"] = {"HOLDING_NEWS": model, "MACRO_NEWS": model}
    companies = _read(inputs / f"companies_{user}.json")
    news, key_error = holding_news.fetch_symbol_news(companies, get_gemini_api_key(user), user_name="_experiment")
    payload = macro_news.fetch_live_macro_stories(api_key=shared_api_key("Gemini"))
    return {"results": {"holdings": {s: [i.model_dump() for i in items] for s, items in news.items()},
                        "macro": [story.model_dump() for story in payload.macro_stories]},
            "inputs": len(companies), "key_error": str(key_error) if key_error else None}


def score(args: argparse.Namespace) -> None:
    import src.tracking.usage_tracking as usage

    inputs, out = Path(args.inputs), Path(args.out)
    ledger = out / "usage.jsonl"
    usage.LEDGER_PATH = ledger  # Experiments never write the real usage ledger
    if not args.second_looks:
        params_dict["HOLDINGS_RECHECK"]["EXTRA_REVIEWS"] = 0  # One model call per answer
    jobs = [j for j in args.jobs.split(",") if j]
    users = args.user or [p.stem.removeprefix("holdings_") for p in inputs.glob("holdings_*.json")]
    for text in args.setting:
        model, effort = parse_setting(text)
        for run in range(1, args.runs + 1):
            for job in jobs:
                for user in (users if job in ("holdings", "news") else [None]):
                    name = f"{slug(text)}__{job}{'_' + user if user else ''}__run{run}.json"
                    path = out / "results" / name
                    problem = setting_problem(job, model, effort)
                    if problem:
                        print(f"{name}: skipped: {problem}")
                        continue
                    if path.exists():
                        print(f"{name}: done already, skipped.")
                        continue
                    before, started = len(_ledger_rows(ledger)), time.monotonic()
                    try:
                        data = _run_job(job, model, effort, inputs, user)
                    except Exception as e:  # One failed run (a limit, a spend cap) must not lose the others
                        print(f"{name}: FAILED: {type(e).__name__}: {e}")
                        continue
                    record = {"setting": text, "job": job, "user": user, "run": run,
                              "seconds": round(time.monotonic() - started, 1), **_cost_since(ledger, before), **data}
                    _write(path, record)
                    answered = len(data["results"]["holdings"]) if job == "news" else len(data["results"])
                    print(f"{name}: {record['seconds']}s, ${record['cost_usd']:.4f}, {answered} of {data['inputs']} answered.")


# ---------------------------------------------------------------------------- Step 3: report
def _load(results: Path) -> list[dict]:
    return [_read(p) for p in sorted((results / "results").glob("*.json"))]


def _scores(record: dict, key: str) -> dict[str, float]:
    return {t: float(v[key]) for t, v in record["results"].items() if isinstance(v, dict) and v.get(key) is not None}


def _labels(record: dict) -> dict[str, str]:
    return {t: v["recommendation"] for t, v in record["results"].items() if isinstance(v, dict) and v.get("recommendation")}


def _fmt(value: Optional[float], pct: bool = False, signed: bool = False) -> str:
    if value is None:
        return "-"
    return f"{value:.0%}" if pct else (f"{value:+.1f}" if signed else f"{value:.2f}")


def report(args: argparse.Namespace) -> str:
    records = _load(Path(args.results))
    if not records:
        return "No results found."
    marks = tuple(float(m) for m in args.marks.split(","))
    settings = sorted({r["setting"] for r in records}, key=lambda s: (s != args.reference, s))
    lines = [f"Reference: {args.reference}. Every comparison is pairwise over runs; the reference row compares its own runs.", ""]

    def runs_of(setting: str, job: str, user: Optional[str] = None) -> list[dict]:
        return [r for r in records if r["setting"] == setting and r["job"] == job and (user is None or r["user"] == user)]

    for horizon, key in SCORE_KEYS.items():
        if not any(r["job"] == horizon for r in records):
            continue
        n_picks = int(params_dict["LONG_TERM_N_AMOUNT" if horizon == "long_term" else "SWING_TERM_N_AMOUNT"])
        ref_runs = [_scores(r, key) for r in runs_of(args.reference, horizon)]
        lines += [f"## {horizon.replace('_', '-')} ideas",
                  "| Setting | Cost / run | Seconds | Answered | Gap | Bias | Rank | Top-" + str(n_picks) + " |"
                  + "".join(f" Pass {m:g} | Overlap {m:g} |" for m in marks),
                  "|---|---|---|---|---|---|---|---|" + "---|---|" * len(marks)]
        for setting in settings:
            runs = runs_of(setting, horizon)
            if not runs:
                continue
            cand_runs = [_scores(r, key) for r in runs]
            m = (compare_idea_runs(ref_runs, cand_runs, marks, n_picks, same=True) if setting == args.reference
                 else compare_idea_runs(ref_runs, cand_runs, marks, n_picks))
            lines.append(f"| {setting} | ${mean(r['cost_usd'] for r in runs):.3f} | {mean(r['seconds'] for r in runs):.0f} | "
                         f"{mean(len(r['results']) for r in runs):.0f}/{runs[0]['inputs']} | {_fmt(m.get('gap'))} | "
                         f"{_fmt(m.get('bias'), signed=True)} | {_fmt(m.get('rank'))} | {_fmt(m.get('top_n'), pct=True)} |"
                         + "".join(f" {_fmt(m.get(f'pass_{x:g}'), pct=True)} | {_fmt(m.get(f'overlap_{x:g}'), pct=True)} |" for x in marks))
        lines.append("")

    holdings_users = sorted({r["user"] for r in records if r["job"] == "holdings"})
    if holdings_users:
        lines += ["## Holdings (each user compared on their own holdings, then averaged)",
                  "| Setting | Cost / run | Seconds | Same label | Steps more cautious | Sell calls caught | False sells |",
                  "|---|---|---|---|---|---|---|"]
        for setting in settings:
            rows, costs, secs = [], [], []
            for user in holdings_users:
                runs = runs_of(setting, "holdings", user)
                if not runs:
                    continue
                ref_runs = [_labels(r) for r in runs_of(args.reference, "holdings", user)]
                rows.append(compare_holding_runs(ref_runs, [_labels(r) for r in runs], same=setting == args.reference))
                costs += [r["cost_usd"] for r in runs]
                secs += [r["seconds"] for r in runs]
            if rows:
                m = _average(rows)
                lines.append(f"| {setting} | ${mean(costs):.4f} | {mean(secs):.0f} | {_fmt(m.get('agree'), pct=True)} | "
                             f"{_fmt(m.get('steps'), signed=True)} | {_fmt(m.get('sell_caught'), pct=True)} | "
                             f"{_fmt(m.get('false_sells'), pct=True)} |")
        lines.append("")

    news = [r for r in records if r["job"] == "news"]
    if news:
        lines += ["## News (holdings and macro together; read them side by side with `show`)",
                  "| Setting | Cost / run | Seconds | Stories | Stocks with none | Links kept |", "|---|---|---|---|---|---|"]
        for setting in settings:
            runs = [r for r in news if r["setting"] == setting]
            if not runs:
                continue
            items = [[i for v in r["results"]["holdings"].values() for i in v] for r in runs]
            empty = [sum(1 for v in r["results"]["holdings"].values() if not v) + (r["inputs"] - len(r["results"]["holdings"])) for r in runs]
            links = [sum(bool(i.get("source_url")) for i in its) / max(1, len(its)) for its in items]
            lines.append(f"| {setting} | ${mean(r['cost_usd'] for r in runs):.4f} | {mean(r['seconds'] for r in runs):.0f} | "
                         f"{mean(len(i) for i in items):.1f} | {mean(empty):.1f} | {mean(links):.0%} |")
        lines.append("")
    text = "\n".join(lines)
    (Path(args.results) / "report.md").write_text(text, encoding="utf-8")
    return text


def show(args: argparse.Namespace) -> str:
    """The first run of each setting's news, stock by stock, for reading side by side."""
    records = [r for r in _load(Path(args.results)) if r["job"] == "news" and r["run"] == 1]
    lines = []
    symbols = sorted({s for r in records for s in r["results"]["holdings"]})
    for symbol in symbols:
        lines.append(f"=== {symbol}")
        for r in records:
            items = r["results"]["holdings"].get(symbol) or []
            for item in items or [{"priority": "-", "summary": "(none)", "source_url": ""}]:
                lines.append(f"  {r['setting']} [{str(item['priority'])[:1]}] {str(item['summary'])[:140]} <{item.get('source_url') or 'NO LINK'}>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------- Command line
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compare LLM settings on identical inputs (docs/admin_handbook.md, 15).")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("capture", help="Save the inputs the digest would send to the LLMs (no LLM spend)")
    p.add_argument("--out", required=True)
    p.add_argument("--user", action="append", default=[], help="A user whose holdings to capture (repeatable)")
    p.add_argument("--skip", help="Comma list: ideas, holdings")
    p.set_defaults(func=capture)

    p = sub.add_parser("score", help="Replay the inputs through each setting (spends API money)")
    p.add_argument("--inputs", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--setting", action="append", required=True, help="model[:effort], repeatable")
    p.add_argument("--jobs", default="long_term,swing,holdings", help="Comma list of long_term, swing, holdings, news")
    p.add_argument("--runs", type=int, default=2)
    p.add_argument("--user", action="append", help="Only these users' holdings / news (default: every captured one)")
    p.add_argument("--second-looks", action="store_true", help="Keep the holdings' extra reviews of sell calls")
    p.set_defaults(func=score)

    p = sub.add_parser("report", help="Compare the settings with the reference")
    p.add_argument("--results", required=True, help="The score step's --out folder")
    p.add_argument("--reference", required=True, help="model[:effort] to compare the others with")
    p.add_argument("--marks", default="70,60", help="Pass marks for the pass / fail agreement")
    p.set_defaults(func=lambda a: print(report(a)))

    p = sub.add_parser("show", help="Print the news of each setting side by side")
    p.add_argument("--results", required=True)
    p.set_defaults(func=lambda a: print(show(a)))

    args = parser.parse_args(argv)
    if args.command == "capture" and not args.user and "holdings" not in set((args.skip or "").split(",")):
        parser.error("capture needs --user NAME (or --skip holdings)")
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
