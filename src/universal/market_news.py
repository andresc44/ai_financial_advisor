#File to connect to Gemini API and fetch top 5 macroeconomic stories for daily digest, with Google Search grounding enabled.
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
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel
from tenacity import retry

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.personalized.api_errors import GEMINI_RETRY, ApiKeyUnusable, gemini_key_error, shared_api_key, shared_key_issue
from src.personalized.symbol_specific_news import _grounded_urls, _verify_source_urls
from src.usage_tracking import record_gemini_response

MACRO_NEWS_MODEL = "gemini-3.6-flash"

# 1. Load variables from .env file into environment
load_dotenv()
if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
    print("[DIAGNOSTIC] WARNING: No GEMINI_API_KEY or GOOGLE_API_KEY found in .env!")

# 2. Define Pydantic Schema
class MacroStory(BaseModel):
    priority: str        # Expects "HIGH", "MEDIUM", or "LOW"
    event_summary: str   # 1 concise sentence on what occurred
    market_impact: str   # 1-2 concise sentences on market/sector ripple effects
    source_url: Optional[str]  # Direct link to the primary news source; None unless grounding returned that page

class DigestPayload(BaseModel):
    macro_stories: list[MacroStory]

# 3. System Prompt
SYSTEM_PROMPT = """
You are a senior macro strategist. Extract the top 5 major global stories from the news feed or web search results for a daily digest.

PRIORITY HIERARCHY:
1. HIGH: Geopolitical shocks, emergency central bank actions, sharp energy/commodity spikes, or major broad-market shifts (+/- 2% intraday).
2. MEDIUM: Critical macro reports (CPI, NFP, GDP) with significant consensus beats or misses.
3. LOW (FILTER OUT): Routine commentary, minor corporate updates, or normal sector fluctuations.

SELECTION RULE:
Select strictly the 5 highest-priority stories available. Do NOT balance across tiers (e.g., return 5 HIGH items if 5 exist). Only fall back to lower tiers if higher-priority events are absent.

FORMATTING REQUIREMENTS:
Keep all fields extremely succinct and direct:
- priority: Must be "HIGH", "MEDIUM", or "LOW".
- event_summary: 1 concise sentence summarizing what occurred.
- market_impact: 1-2 concise sentences analyzing direct asset, sector, yield, or volatility impacts over the next 1-3 weeks.
- source_url: The direct URL link to the original article source.
"""

# 4. Fetch Function using Chat interface to fix AFC warning
@retry(**GEMINI_RETRY)  # Claude-authored (2026-10-16): retries only what can recover
def fetch_live_macro_stories() -> DigestPayload:
    print("\n[DIAGNOSTIC] Initializing Gemini Client...")
    client = genai.Client(api_key=shared_api_key("Gemini"))

    today = datetime.now(ZoneInfo("America/New_York")).date()
    prompt_text = (
        f"Today is {today:%Y-%m-%d}. Perform a web search for today's top global macroeconomic, central bank, "
        "and high-impact world financial news. Select the top 5 items based on priority."
    )

    print("[DIAGNOSTIC] Creating Chat session with Google Search grounding enabled...")
    # Using client.chats.create avoids the Automatic Function Calling warning
    chat = client.chats.create(
        model=MACRO_NEWS_MODEL,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[{"google_search": {}}],  # Enables Search Grounding
            response_mime_type="application/json",
            response_schema=DigestPayload,
        )
    )

    start_time = time.time()
    print("[DIAGNOSTIC] Sending request to Gemini API (Awaiting search grounding)...")

    response = chat.send_message(prompt_text)
    record_gemini_response(None, "universal", MACRO_NEWS_MODEL, response)  # Claude-authored (2026-10-16): shared-key cost

    elapsed = round(time.time() - start_time, 2)
    print(f"[DIAGNOSTIC] Response received successfully in {elapsed}s.")

    payload = DigestPayload.model_validate_json(response.text)
    _verify_source_urls(payload.macro_stories, _grounded_urls(response))  # Claude-authored (2026-10-16)
    return payload


def get_macro_stories() -> tuple[Optional[DigestPayload], list[dict]]:
    """
    Claude-authored (2026-10-16): (stories, issues) for the universal content. Never raises: a missing or failing
    shared Gemini key, or any other failure, comes back as an issue ({"source", "reason", "message"}) and alerts the
    admins, so the digest still goes out without macro news.
    """
    from src.telegram_alert.notices import alert_admins  # Imported here: only needed when something fails

    try:
        return fetch_live_macro_stories(), []
    except ApiKeyUnusable as e:  # Shared key not set
        issue = shared_key_issue(e)
    except Exception as e:
        key_error = gemini_key_error(e, None)
        issue = shared_key_issue(key_error) if key_error else {
            "source": "Gemini (macro news)", "reason": "failed", "message": f"Macro news failed: {str(e)[:200]}",
        }
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
