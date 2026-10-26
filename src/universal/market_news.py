#File to connect to Gemini API and fetch the top macroeconomic stories for daily digest, with Google Search grounding enabled.
"""
Claude-authored changes (Claude Opus 5.5 via Claude Code, 2026-10-16), at Andres's request, ahead of switching
the macro news on:
- The import no longer prints the first characters of the API key (logs get shared).
- Retries use api_errors.GEMINI_RETRY like the per-stock news: only timeouts, per-minute rate limits, and 5xx are
  retried; key, billing, and quota errors fail at once instead of being retried four times.
- get_macro_stories() never raises: a key problem (invalid, expired, no credits, quota) or any other failure is
  returned as an issue for universal_issues and sent to the admins. Usage is recorded under the shared key.
- When enabled (later on 2026-10-16): links are verified like the per-stock news (a source_url is kept only when it
  matches a page Google Search grounding returned, else None), and the prompt states today's date.
- 2026-10-20: STORY_COUNT stories (UNIVERSAL_EXTRAS.MACRO_NEWS_STORIES, 4; was 5), each with a one-sentence
  market impact (was 1-2 sentences), at Andres's request: the stories were about 6% of the email.
- 2026-10-23: the request uses GEMINI_CONFIG's effort (thinking level) and output cap (src/llm/gemini_settings.py), and
  the model and story count are read when the request is built, so a change from the settings sheet applies at once.
  The prompt asks for the search result's grounding redirect link by name, which low effort otherwise often replaced
  with the article's own address (which can't be verified, so it was dropped).
"""
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google import genai
from google.genai.errors import APIError
from pydantic import BaseModel
from tenacity import retry

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.ops.run_logging import debug
from src.llm.gemini_settings import gemini_model, generation_config
from src.config.parameters import params_dict
from src.personalized.api_errors import GEMINI_RETRY, ApiKeyUnusable, gemini_key_error, shared_api_key, shared_key_issue
from src.personalized.symbol_specific_news import _grounded_urls, _verify_source_urls
from src.tracking.usage_tracking import record_gemini_response


def story_count() -> int:
    """Claude-authored (2026-10-20): how many stories (UNIVERSAL_EXTRAS.MACRO_NEWS_STORIES); read when used (2026-10-23)."""
    return int(params_dict["UNIVERSAL_EXTRAS"].get("MACRO_NEWS_STORIES", 4))

# 1. Load variables from .env file into environment
load_dotenv()
if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
    print("[WARNING] No GEMINI_API_KEY or GOOGLE_API_KEY found in .env!")

# 2. Define Pydantic Schema
class MacroStory(BaseModel):
    priority: str        # Expects "HIGH", "MEDIUM", or "LOW"
    event_summary: str   # 1 concise sentence on what occurred
    market_impact: str   # 1 succinct sentence on the direct market/sector effect
    source_url: Optional[str]  # Direct link to the primary news source; None unless grounding returned that page

class DigestPayload(BaseModel):
    macro_stories: list[MacroStory]

# 3. System Prompt (2026-10-23: built per request, for the current story count)
def system_prompt(count: int) -> str:
    return f"""
You are a senior macro strategist. Extract the top {count} major global stories from the news feed or web search results for a daily digest.

PRIORITY HIERARCHY:
1. HIGH: Geopolitical shocks, emergency central bank actions, sharp energy/commodity spikes, or major broad-market shifts (+/- 2% intraday).
2. MEDIUM: Critical macro reports (CPI, NFP, GDP) with significant consensus beats or misses.
3. LOW (FILTER OUT): Routine commentary, minor corporate updates, or normal sector fluctuations.

SELECTION RULE:
Select strictly the {count} highest-priority stories available. Do NOT balance across tiers (e.g., return {count} HIGH items if {count} exist). Only fall back to lower tiers if higher-priority events are absent.

FORMATTING REQUIREMENTS:
Keep all fields extremely succinct and direct:
- priority: Must be "HIGH", "MEDIUM", or "LOW".
- event_summary: 1 concise sentence summarizing what occurred.
- market_impact: ONE succinct sentence of at most 25 words on the direct effect on assets, sectors, yields, or volatility, e.g. "Higher oil costs raise inflation risk, lifting yields and weighing on airlines." Never add a second sentence.
- source_url: The search result's link exactly as your Google Search tool returned it (it starts with https://vertexaisearch.cloud.google.com/grounding-api-redirect/), copied character for character. Never replace it with the article's own address, and never construct or guess a URL; use null if you have no such link for the story.
"""

# 4. Fetch Function using Chat interface to fix AFC warning
@retry(**GEMINI_RETRY)  # Claude-authored (2026-10-16): retries only what can recover
def fetch_live_macro_stories(api_key: Optional[str] = None) -> DigestPayload:
    """
    `api_key` overrides the shared GEMINI_API_KEY / GOOGLE_API_KEY (2026-10-26, Andres): get_macro_stories tries the
    free-tier key first (src/llm/gemini_key_rotation.py); None (the default) uses the regular shared key, as before.
    """
    debug("\n[DIAGNOSTIC] Initializing Gemini Client...")
    client = genai.Client(api_key=api_key or shared_api_key("Gemini"))
    model, count = gemini_model("MACRO_NEWS"), story_count()

    today = datetime.now(ZoneInfo("America/New_York")).date()
    prompt_text = (
        f"Today is {today:%Y-%m-%d}. Perform a web search for today's top global macroeconomic, central bank, "
        f"and high-impact world financial news. Select the top {count} items based on priority."
    )

    debug("[DIAGNOSTIC] Creating Chat session with Google Search grounding enabled...")
    # Using client.chats.create avoids the Automatic Function Calling warning
    chat = client.chats.create(
        model=model,
        config=generation_config(  # 2026-10-23: with GEMINI_CONFIG's effort and output cap
            system_instruction=system_prompt(count),
            tools=[{"google_search": {}}],  # Enables Search Grounding
            response_mime_type="application/json",
            response_schema=DigestPayload,
        )
    )

    start_time = time.time()
    debug("[DIAGNOSTIC] Sending request to Gemini API (Awaiting search grounding)...")

    response = chat.send_message(prompt_text)
    record_gemini_response(None, "universal", model, response)  # Claude-authored (2026-10-16): shared-key cost

    elapsed = round(time.time() - start_time, 2)
    debug(f"[DIAGNOSTIC] Response received successfully in {elapsed}s.")

    payload = DigestPayload.model_validate_json(response.text)
    payload.macro_stories = payload.macro_stories[:count]  # Claude-authored (2026-10-20): in case it sends more
    _verify_source_urls(payload.macro_stories, _grounded_urls(response))  # Claude-authored (2026-10-16)
    return payload


def _macro_issue(e: BaseException) -> tuple[dict, Optional[ApiKeyUnusable]]:
    """(issue, key_error) for a failed attempt: shared_key_issue's shape, and the ApiKeyUnusable behind it (if any)."""
    key_error = e if isinstance(e, ApiKeyUnusable) else gemini_key_error(e, None)
    if key_error:
        return shared_key_issue(key_error), key_error
    return {"source": "Gemini (macro news)", "reason": "failed", "message": f"Macro news failed: {str(e)[:200]}"}, None


def get_macro_stories() -> tuple[Optional[DigestPayload], list[dict]]:
    """
    Claude-authored (2026-10-16): (stories, issues) for the universal content. Never raises: a missing or failing
    shared Gemini key, or any other failure, comes back as an issue ({"source", "reason", "message"}) and alerts the
    admins, so the digest still goes out without macro news.

    2026-10-26 (Andres): tries the free-tier GEMINI_FREE_API_KEY first (src/llm/gemini_key_rotation.py). The first
    time it fails for anything but a transient rate limit, this slot switches to the paid key for good, Andres is
    told once on Telegram, and this same request is retried right away with the paid key.
    """
    from src.telegram_alert.notices import alert_admins  # Imported here: only needed when something fails
    from src.llm.gemini_key_rotation import free_key, free_key_failed, notify_switch, switch_to_paid

    api_key = free_key()  # None when there's no free key, or this slot already switched to paid
    try:
        return fetch_live_macro_stories(api_key=api_key), []
    except Exception as e:
        issue, key_error = _macro_issue(e)

    if api_key and free_key_failed(key_error):
        if switch_to_paid(None, key_error):
            notify_switch(None, key_error)
        try:
            return fetch_live_macro_stories(), []  # No api_key -> shared_api_key("Gemini"), the paid key
        except Exception as e:
            issue, _ = _macro_issue(e)

    alert_admins(f"universal content: {issue['source']} {issue['reason']}", issue["message"])
    return None, [issue]

# Execution block with detailed diagnostics
if __name__ == "__main__":
    print("[DIAGNOSTIC] Starting Daily Macro Digest Generation...")
    try:
        payload = fetch_live_macro_stories()
        print("\n=== SUCCESSFUL PAYLOAD OUTPUT ===")
        for story in payload.macro_stories:
            print(f"[{story.priority}] Event: {story.event_summary}")
            print(f"Impact: {story.market_impact}")
            print(f"Link: {story.source_url}\n")

    except APIError as e:
        print("\n[DIAGNOSTIC ERROR DETAILED BREAKDOWN]")
        print(f"Status Code: {getattr(e, 'code', 'N/A')}")
        print(f"Message: {getattr(e, 'message', str(e))}")
        print(f"Details: {getattr(e, 'details', 'No extra details provided')}")
        print("\nPossible cause: Google Search grounding rate limit exceeded on this API key.")
    except Exception as e:
        print(f"\n[DIAGNOSTIC UNEXPECTED ERROR]: {e}")
