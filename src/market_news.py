import os
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

# 1. Load variables from .env file into environment
print("[DIAGNOSTIC] Loading environment variables...")
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if api_key:
    print(f"[DIAGNOSTIC] API Key detected (Prefix: {api_key[:6]}...)")
else:
    print("[DIAGNOSTIC] WARNING: No GEMINI_API_KEY or GOOGLE_API_KEY found in .env!")

# 2. Define Pydantic Schema
class MacroStory(BaseModel):
    priority: str        # Expects "HIGH", "MEDIUM", or "LOW"
    event_summary: str   # 1 concise sentence on what occurred
    market_impact: str   # 1-2 concise sentences on market/sector ripple effects
    source_url: str      # Direct link to the primary news source

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

# Helper function to print retry attempts for diagnostic tracking
def log_retry_attempt(retry_state):
    exception = retry_state.outcome.exception()
    print(f"[DIAGNOSTIC] Attempt {retry_state.attempt_number} failed with error: {exception}")
    print(f"[DIAGNOSTIC] Rate limit hit. Retrying in {retry_state.next_action.sleep} seconds...")

# 4. Fetch Function using Chat interface to fix AFC warning
@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=4, max=20),
    retry=retry_if_exception_type(APIError),
    before_sleep=log_retry_attempt,
    reraise=True
)
def fetch_live_macro_stories() -> DigestPayload:
    print("\n[DIAGNOSTIC] Initializing Gemini Client...")
    client = genai.Client()

    prompt_text = (
        "Perform a web search for today's top global macroeconomic, central bank, "
        "and high-impact world financial news. Select the top 5 items based on priority."
    )

    print("[DIAGNOSTIC] Creating Chat session with Google Search grounding enabled...")
    # Using client.chats.create avoids the Automatic Function Calling warning
    chat = client.chats.create(
        model="gemini-3.6-flash",
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
    
    elapsed = round(time.time() - start_time, 2)
    print(f"[DIAGNOSTIC] Response received successfully in {elapsed}s.")

    return DigestPayload.model_validate_json(response.text)

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