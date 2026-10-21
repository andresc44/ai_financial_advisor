#File to consolidate universal content for the daily digest, including market data, news, and recommendations, into a single structured dictionary for easy access and display.
"""
Claude-authored changes (Claude Opus 5.5 via Claude Code), at Andres's request:
- 2026-10-15: market-data fetching takes shared_locks.MARKET_DATA_LOCK; each pick is logged to the track record.
- 2026-10-16: no more silent failures. The shared Anthropic key is checked (free) before any data is fetched;
  shared-key failures during scoring, Finnhub / Twelve Data rate limits and rejected keys, and sources missing for
  too many tickers (UNIVERSAL_DATA_QUALITY.MAX_MISSING_PCT) are returned as `universal_issues` and sent to the
  admins. When recommendations can't be produced (no Anthropic or Finnhub key), the digest still goes out with
  empty picks and the issue, instead of sys.exit aborting every user's digest.
- 2026-10-16 (later): macro news and the market dashboard are on (params_dict["UNIVERSAL_EXTRAS"] switches each
  off). They run in background threads while the screener, filters, and Claude scoring run, are included even when
  no picks can be produced, and their failures become universal_issues like the rest.
- 2026-10-17: `screening`, how many stocks got through each stage, from NASDAQ's listings to the picks.
- 2026-10-20: the swing ideas have their own screen (swing_screen.py), run beside the long-term screens on the same
  stage-1 list. Both candidate lists are fetched together (a stock in both is fetched once), each is scored by its
  own LLM call, and the screening counts show both paths.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import sys
import json

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict
from src import llm_models
from src.personalized.api_errors import ApiKeyUnusable, shared_api_key, shared_key_issue, verify_llm_key
from src.universal.recommendations.ticker_fetcher import Tickers
from src.universal.recommendations.sanitizer import sanitize_ticker_payload
from src.universal.recommendations.scorer import select_top_candidates
from src.universal.recommendations.enriched_rec import enrich_recommendations
from src.universal.recommendations.filter_tickers import DataFetcher
from src.universal.recommendations.data_source_guard import missing_data_issues
from src.universal.recommendations import swing_screen
from src.universal.market_news import get_macro_stories
from src.universal.macro_indicators import get_market_dashboard
from src.shared_locks import MARKET_DATA_LOCK
from src.telegram_alert.notices import alert_admins
from src.track_record import log_universal

NO_RECOMMENDATIONS = {"top_long_term": [], "top_swing": []}


def _report_issues(issues: list[dict]) -> None:
    """Sends each universal-content problem to the admins (once per cooldown per problem)."""
    for issue in issues:
        alert_admins(f"universal content: {issue['source']} {issue['reason']}", issue["message"])


def _without_recommendations(issues: list[dict]) -> dict:
    """No picks, when they can't be produced; the digest still goes out with the issue (and the macro content)."""
    _report_issues(issues)
    return {"recommendations": enrich_recommendations(NO_RECOMMENDATIONS, {}), "universal_issues": issues,
            "screening": {}}


def _rule(field: str, operator: str, threshold) -> str:
    return f"{field} {operator} {threshold:,}" if isinstance(threshold, (int, float)) else f"{field} {operator} {threshold}"


def stage1_rules() -> list[str]:
    """The NASDAQ screener's settings in parameters.py as rules, e.g. 'marketCap_M >= 10,000.0'."""
    settings = (("marketCap_M", ">=", "MKTCAP_MIN"), ("marketCap_M", "<=", "MKTCAP_MAX"),
                ("volume_M", ">=", "VOLUME_MIN"), ("volume_M", "<=", "VOLUME_MAX"),
                ("price", ">=", "LASTSALE_MIN"), ("price", "<=", "LASTSALE_MAX"),
                ("region", "=", "REGION"), ("country", "=", "COUNTRY"),
                ("sector", "=", "SECTOR"), ("industry", "=", "INDUSTRY"))
    return [_rule(field, op, params_dict[key]) for field, op, key in settings if params_dict.get(key) is not None]


def screening_counts(fetcher, stats: dict, recommendations: dict, swing_counts: dict | None = None) -> dict:
    """
    Claude-authored (2026-10-17): how many stocks got through each stage this run, for the digest's "About this
    digest" section: {"stages": [{"stage", "count", ...}], "model"}. In order:
    - listings: every stock NASDAQ's screener listed (only when this half-day's screener file was downloaded with
      the count; see Tickers.listings_path)
    - screener, yahoo, finnhub, technical: kept by stage 1 (parameters.py market cap, volume, price, ...) and the
      YAHOO_FILTERS, FINNHUB_FILTERS, and TWELVEDATA_FILTERS stages; each has its `rules`
    - scored: valid evaluations from the LLM, with `of` = how many were sent
    - long_term_score / swing_score: scored at or above MIN_LONG_SCORE / MIN_SWING_SCORE (`min_score`)
    - picked: `long_term` + `swing`, capped at LONG_TERM_N_AMOUNT / SWING_TERM_N_AMOUNT
    A filter stage without rules lets everything through, so it is left out.
    2026-10-20: the swing screen's own stages (swing_screen.screen's `swing_counts`) after the long-term ones:
    swing_fundamentals (with its rules), swing_setup, swing_plan (`min_score` = MIN_PLAN_RR), and swing_scored
    (the best MAX_CANDIDATES plans; `of` = how many were sent), then swing_score and swing_early as before.
    """
    kept = getattr(fetcher, "stage_counts", {}) or {}
    stages = []
    listings = Tickers.listing_count(getattr(fetcher, "ticker_file", None))
    if listings is not None:
        stages.append({"stage": "listings", "count": listings})
    filters = (
        ("screener", stage1_rules()),
        ("yahoo", [_rule(*rule) for rule in params_dict["YAHOO_FILTERS"]]),
        ("finnhub", [_rule(*rule[1:]) for rule in params_dict["FINNHUB_FILTERS"]]),
        ("technical", [_rule(f"{rule[0]} ({rule[1]})", rule[2], rule[3]) for rule in params_dict["TWELVEDATA_FILTERS"]]),
    )
    for stage, rules in filters:
        if stage in kept and (rules or stage == "screener"):
            stages.append({"stage": stage, "count": kept[stage], "rules": rules})
    if "scored" in stats:
        stages.append({"stage": "scored", "count": stats["scored"], "of": stats["sent"]})
        stages.append({"stage": "long_term_score", "count": stats["long_qualified"],
                       "min_score": params_dict["MIN_LONG_SCORE"]})
    if swing_counts:
        swing = params_dict.get("SWING_SCREEN") or {}
        stages.append({"stage": "swing_fundamentals", "count": swing_counts.get("fundamentals", 0),
                       "rules": [_rule(*rule) for rule in swing.get("YAHOO_FILTERS") or []]})
        stages.append({"stage": "swing_setup", "count": swing_counts.get("setup", 0)})
        stages.append({"stage": "swing_plan", "count": swing_counts.get("plan", 0), "min_score": swing.get("MIN_PLAN_RR")})
        stages.append({"stage": "swing_scored", "count": stats.get("swing_scored", 0),
                       "of": stats.get("swing_sent", swing_counts.get("candidates", 0))})
    if "scored" in stats:
        stages.append({"stage": "swing_score", "count": stats["swing_qualified"],
                       "min_score": params_dict["MIN_SWING_SCORE"]})
        if stats["swing_qualified"]:  # 2026-10-18: of those, the ones not already too far along their run
            stages.append({"stage": "swing_early", "count": stats.get("swing_early", stats["swing_qualified"])})
    long_term = len(recommendations.get("top_long_term") or [])
    swing = len(recommendations.get("top_swing") or [])
    stages.append({"stage": "picked", "count": long_term + swing, "long_term": long_term, "swing": swing})
    return {"stages": stages, "model": llm_models.job_settings("UNIVERSAL").get("model")}


def get_universal_content():
    """
    Returns a dictionary containing universal content for the daily digest.
    This content is not user-specific and can be shared across all users.

    Claude-authored (2026-10-16): {"recommendations", "market_data", "headline_news", "universal_issues"}, plus
    "screening" (2026-10-17, see screening_counts). The
    macro news (Gemini with Google Search) and the market dashboard (Yahoo, FRED, CNN, Wikipedia) are fetched in
    the background while the picks are built, since neither needs the other.
    """
    extras = params_dict.get("UNIVERSAL_EXTRAS", {})
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="macro") as pool:
        news_future = pool.submit(get_macro_stories) if extras.get("MACRO_NEWS", True) else None
        dashboard_future = pool.submit(get_market_dashboard) if extras.get("MARKET_DASHBOARD", True) else None
        content = _get_recommendations()
        stories, news_issues = news_future.result() if news_future else (None, [])
        market_data, dashboard_issues = dashboard_future.result() if dashboard_future else ({}, [])
    _report_issues(dashboard_issues)  # get_macro_stories already sent its own
    return {
        "recommendations": content["recommendations"],
        "market_data": market_data,
        # {"macro_stories": [{"priority", "event_summary", "market_impact", "source_url"}]}; {} when off or failed
        "headline_news": stories.model_dump() if stories else {},
        # Why picks, news, or indicators may be missing or based on partial data
        "universal_issues": content["universal_issues"] + news_issues + dashboard_issues,
        # How many stocks got through each stage of the screening; {} when no picks could be produced
        "screening": content.get("screening") or {},
    }


def _get_recommendations() -> dict:
    """
    The scored, enriched picks and the problems found building them: {"recommendations", "universal_issues",
    "screening"}.
    """
    issues: list[dict] = []
    # Claude-authored (2026-10-16): a missing, invalid, expired, unpermitted, or (DeepSeek) unfunded shared LLM key is
    # found here, for free, before the screener and market data are fetched for scoring that couldn't run
    # 2026-10-16: the shared key of whichever provider LLM["UNIVERSAL"] uses (ANTHROPIC_API_KEY or DEEPSEEK_API_KEY)
    provider = llm_models.job_settings("UNIVERSAL")["provider"]
    try:
        verify_llm_key(provider, shared_api_key(provider), None)
    except ApiKeyUnusable as e:
        return _without_recommendations([shared_key_issue(e)])

    payload = {}
    ticker_client = Tickers()
    file_path = ticker_client.fetch_ticker_data(
        mktcap_min=params_dict["MKTCAP_MIN"],        # Minimum market cap in Millions USD
        mktcap_max=params_dict["MKTCAP_MAX"],        # Maximum market cap in Millions USD
        volume_min=params_dict["VOLUME_MIN"],        # Minimum trading volume in Millions
        volume_max=params_dict["VOLUME_MAX"],        # Maximum trading volume in Millions
        lastsale_min=params_dict["LASTSALE_MIN"],    # Minimum share price USD
        lastsale_max=params_dict["LASTSALE_MAX"],    # Maximum share price USD
        region=params_dict["REGION"],                # Takes priority over 'country'
        country=params_dict["COUNTRY"],              # Ignored when region is defined
        sector=params_dict["SECTOR"],                # Takes priority over 'industry'
        industry=params_dict["INDUSTRY"],            # Ignored when sector is defined
        clear_existing_data=params_dict["CLEAR_EXISTING_DATA"],  # Deletes previous CSV exports in output dir
    )
    # Claude-authored (2026-10-15): the digest now builds this universal content while users run in parallel.
    # Finnhub / Twelve Data use one shared key, so this fetch takes the same lock as the holdings fetch
    # (src/shared_locks.py); Claude scoring below runs outside it.
    with MARKET_DATA_LOCK:
        print("Initializing DataFetcher instance...")
        try:
            fetcher = DataFetcher()
        except ValueError as e:
            print(f"Initialization Failed: {e}")
            print("Verify that your .env file exists and contains FINNHUB_API_KEY.")
            # Claude-authored (2026-10-16): reported instead of sys.exit(1), which aborted every user's digest
            return _without_recommendations([{"source": "Finnhub", "reason": "missing_key", "message": str(e)}])


        #Fetching all tickers and testing pipeline
        print(f"Testing filter_all() for {fetcher.all_tickers} ---")
        filtered_tickers = fetcher.filter_all()
        # Claude-authored (2026-10-20): the swing screen, beside the long-term screens on the same stage-1 list. It
        # reuses the Yahoo info the long-term screen just fetched and one batch of daily bars.
        swing_tickers, swing_setups, swing_counts = swing_screen.screen(fetcher, fetcher.all_tickers)
        candidates = filtered_tickers + [t for t in swing_tickers if t not in filtered_tickers]
        llm_packets = fetcher.congregate_LLM_input_data(candidates, as_dict_records=True)
        sanitized_LLM_inputs = {}

        for ticker in llm_packets.keys():
            raw_data = llm_packets[ticker]
            sanitized_payload = sanitize_ticker_payload(ticker, raw_data)
            sanitized_LLM_inputs[ticker] = sanitized_payload
            # output_filename = "LLM_input_sanitized.json"

            # with open(output_filename, "w", encoding="utf-8") as f:
            #     json.dump(sanitized_payload, f, indent=4, default=str)

            # print(f"✓ Saved output to {output_filename}")
        print(f"✓ Sanitized payload for {len(llm_packets)} tickers")

    # Claude-authored (2026-10-16): rate limits / rejected keys seen while fetching, and sources missing for too many
    # tickers, which used to only print [DEBUG] lines
    issues += fetcher.data_source_issues.items
    issues += missing_data_issues(llm_packets, params_dict["UNIVERSAL_DATA_QUALITY"]["MAX_MISSING_PCT"])

    stats: dict = {}
    generated_recommendations = select_top_candidates(
        sanitized_inputs=sanitized_LLM_inputs,
        long_n=params_dict["LONG_TERM_N_AMOUNT"],
        swing_n=params_dict["SWING_TERM_N_AMOUNT"],
        issues=issues,  # Claude-authored (2026-10-16): a shared Anthropic key failure during scoring
        stats=stats,  # Claude-authored (2026-10-17): how many were scored and cleared each bar
        long_tickers=filtered_tickers,  # Claude-authored (2026-10-20): each list gets its own LLM call
        swing_setups=swing_setups,
    )
    print(f"Generated_recommendations:\n{generated_recommendations}")
    log_universal(generated_recommendations, sanitized_LLM_inputs)  # Claude-authored (2026-10-15): track record
    recs_w_data = enrich_recommendations(generated_recommendations, sanitized_LLM_inputs)

    _report_issues(issues)
    return {"recommendations": recs_w_data, "universal_issues": issues,
            "screening": screening_counts(fetcher, stats, generated_recommendations, swing_counts)}

if __name__ == "__main__":
    universal_content = get_universal_content()
    print("Universal content fetched successfully.")
