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
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import sys
import json

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict
from src.personalized.api_errors import ApiKeyUnusable, shared_api_key, shared_key_issue, verify_anthropic_key
from src.universal.recommendations.ticker_fetcher import Tickers
from src.universal.recommendations.sanitizer import sanitize_ticker_payload
from src.universal.recommendations.scorer import select_top_candidates
from src.universal.recommendations.enriched_rec import enrich_recommendations
from src.universal.recommendations.filter_tickers import DataFetcher
from src.universal.recommendations.data_source_guard import missing_data_issues
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
    return {"recommendations": enrich_recommendations(NO_RECOMMENDATIONS, {}), "universal_issues": issues}


def get_universal_content():
    """
    Returns a dictionary containing universal content for the daily digest.
    This content is not user-specific and can be shared across all users.

    Claude-authored (2026-10-16): {"recommendations", "market_data", "headline_news", "universal_issues"}. The
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
    }


def _get_recommendations() -> dict:
    """The scored, enriched picks and the problems found building them: {"recommendations", "universal_issues"}."""
    issues: list[dict] = []
    # Claude-authored (2026-10-16): a missing, invalid, expired, or unpermitted shared Anthropic key is found here,
    # for free, before the screener and market data are fetched for scoring that couldn't run
    try:
        verify_anthropic_key(shared_api_key("Anthropic"), None)
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
        llm_packets = fetcher.congregate_LLM_input_data(filtered_tickers,as_dict_records=True)
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

    generated_recommendations = select_top_candidates(
        sanitized_inputs=sanitized_LLM_inputs,
        long_n=params_dict["LONG_TERM_N_AMOUNT"],
        swing_n=params_dict["SWING_TERM_N_AMOUNT"],
        issues=issues,  # Claude-authored (2026-10-16): a shared Anthropic key failure during scoring
    )
    print(f"Generated_recommendations:\n{generated_recommendations}")
    log_universal(generated_recommendations, sanitized_LLM_inputs)  # Claude-authored (2026-10-15): track record
    recs_w_data = enrich_recommendations(generated_recommendations, sanitized_LLM_inputs)

    _report_issues(issues)
    return {"recommendations": recs_w_data, "universal_issues": issues}

if __name__ == "__main__":
    universal_content = get_universal_content()
    print("Universal content fetched successfully.")
