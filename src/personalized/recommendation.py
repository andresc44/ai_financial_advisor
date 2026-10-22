# File to have Claude evaluate each held position and attach a buy_more / hold / risky / profit_sell / urgent_sell recommendation.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code), at Andres's request.
- 2026-10-13: replaced the placeholder rule-based logic with a Claude evaluation modelled on
  src/universal/recommendations/LLM_recommendation.py (async client, concurrency semaphore, Pydantic
  schema as a tool). Uses a strict tool schema with tool_choice "auto" instead of a forced tool call,
  because newer models (Opus 5.5, Sonnet 5.5, Fable 5.1) reject forced tool_choice.
- 2026-10-13: added per-user model/effort (PortfolioFetcher.claude_overrides, resolved in user_settings.py),
  the current market session in the prompt, server-side refusal fallbacks on models that support them,
  and recording of every evaluation in data/watchdog_state.json (holdings_state.py) so the watchdog only
  re-evaluates stocks that changed. See docs/watchdog.md.
- 2026-10-14: each user's evaluations now use their own Anthropic key (user_settings.get_anthropic_api_key)
  instead of the shared ANTHROPIC_API_KEY. Claude also returns a Bullish / Neutral / Bearish sentiment for the
  stock, judged from all the data and news, which the digest shows beside the headlines.
- 2026-10-15: key failures (missing, invalid, expired, no permission, out of credits, spend limit) are handled
  explicitly: resolve_anthropic_key runs a free validity check before any work, the evaluator stops sending
  requests after the first key error, and add_recommendations exposes it as self.claude_key_error so the
  watchdog and digest can react (see api_errors.py).
- 2026-10-15: the digest can reuse a watchdog evaluation from the last hour (reuse_recent_evaluations), so
  stocks that were just evaluated and haven't moved aren't sent to Claude twice.
- 2026-10-16: the model can be Claude or DeepSeek (src/llm_models.py), per user (PortfolioFetcher.llm_settings,
  formerly claude_overrides). DeepSeek requests go through src/deepseek_client.py with the same tool schema, the
  same validation, and the same stop-at-the-first-key-error handling; resolve_llm_key checks the user's key for
  whichever provider their model needs (formerly resolve_anthropic_key), and the error is self.llm_key_error.
"""
import asyncio
from collections import Counter
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from zoneinfo import ZoneInfo

import pandas as pd
from anthropic import APIError, AsyncAnthropic
from pydantic import BaseModel, ConfigDict, Field, ValidationError

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src import llm_models
from src.deepseek_client import DeepSeekClient, DeepSeekError, build_request, validated_answer
from src.idea_positions import prompt_view
from src.market_session import market_phase
from src.parameters import params_dict
from src.personalized.api_errors import ApiKeyUnusable, anthropic_key_error, deepseek_key_error, verify_llm_key
from src.personalized.holdings_state import record_evaluations, reusable_evaluations
from src.personalized.LLM_hold_instr import (
    RECOMMENDATION_DESCRIPTION,
    SENTIMENT_DESCRIPTION,
    RATIONALE_DESCRIPTION,
    EVALUATION_TOOL_NAME,
    EVALUATION_TOOL_DESCRIPTION,
    SYSTEM_PROMPT,
    format_user_prompt,
)
from src.personalized.user_settings import get_llm_api_key
from src.track_record import log_personal
from src.usage_tracking import record_anthropic_response, record_deepseek_response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("Finnbot.HoldingEvaluator")

# Holdings columns that are internal identifiers and add no signal for the LLM
EXCLUDED_POSITION_COLUMNS = {
    "market_context",
    "portfolio_weight_pct",  # Sent once per stock at the payload's top level
    # Claude-authored (2026-10-15): USD display copies of per-share prices; Claude gets the listing-currency prices
    # (which match market_context) plus fx_rate_to_usd
    "current_price_usd",
    "avg_cost_basis_usd",
    "last_close_usd",
    "account_name",
    "exchange",
    "idea_origin",  # Claude-authored (2026-10-22): sent once per stock as opened_from_finnbot_idea
    "account_id",
    "instrument_id",
    "figi_code",
    "figi_share_class",
}

# Beta header for the server-side `fallbacks: "default"` parameter, sent only for the models listed in
# params_dict["CLAUDE_FALLBACK_DEFAULT_MODELS"]. On a safety-classifier refusal the API re-runs the request on a
# recommended model instead of returning nothing. Same value as in LLM_recommendation.py.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


# Pydantic Evaluation Schema
class HoldingEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")  # Emits additionalProperties: false, required for strict tool use

    recommendation: Literal["buy_more", "hold", "risky", "profit_sell", "urgent_sell"] = Field(  # 2026-10-18
        ..., description=RECOMMENDATION_DESCRIPTION
    )
    sentiment: Literal["Bullish", "Neutral", "Bearish"] = Field(..., description=SENTIMENT_DESCRIPTION)
    rationale: str = Field(..., description=RATIONALE_DESCRIPTION)


EVALUATION_TOOL_SPEC = {
    "name": EVALUATION_TOOL_NAME,
    "description": EVALUATION_TOOL_DESCRIPTION,
    "strict": True,
    "input_schema": HoldingEvaluation.model_json_schema(),
}


# Claude-authored (2026-10-22): how cautious each label is, for breaking a tie between reviews (majority_review)
SEVERITY = {"buy_more": 0, "hold": 1, "profit_sell": 2, "risky": 3, "urgent_sell": 4}


def majority_review(reviews: List[HoldingEvaluation]) -> HoldingEvaluation:
    """
    Claude-authored (2026-10-22): the label most reviews gave. A tie goes to the milder side: of two labels the
    milder one, of three different ones the middle one. Returns the first review with that label, so its sentiment
    and rationale match it.
    """
    votes = Counter(review.recommendation for review in reviews)
    most = max(votes.values())
    tied = sorted((label for label, count in votes.items() if count == most), key=lambda label: SEVERITY.get(label, 1))
    winner = tied[(len(tied) - 1) // 2]
    return next(review for review in reviews if review.recommendation == winner)


class ParallelHoldingEvaluator:
    # Only used for settings nothing else sets (the model and effort come from params_dict["LLM"] or the user)
    DEFAULT_CONFIG = {
        "max_tokens": 16000,
        "system_prompt": SYSTEM_PROMPT,
        "max_concurrency": 15,
    }

    def __init__(
        self,
        api_key: str,
        config: Optional[Dict[str, Any]] = None,
        market_session: Optional[str] = None,
        user_name: Optional[str] = None,
        source: str = "watchdog",
    ):
        # Claude-authored (2026-10-16): the model and effort are params_dict["LLM"]["HOLDINGS"] with `config` (a user's
        # settings from user_settings.get_llm_settings) on top; the model decides the provider, whose request settings
        # (CLAUDE_CONFIG / DEEPSEEK_CONFIG) fill in the rest. Prompts come only from LLM_hold_instr.py.
        chosen = {**llm_models.job_settings("HOLDINGS"), **(config or {})}
        efforts = llm_models.allowed_models().get(chosen["model"])
        if efforts is not None and chosen.get("effort") not in efforts:
            chosen["effort"] = llm_models.default_effort(chosen["model"])
        self.provider = llm_models.provider_of(chosen["model"])
        provider_config = params_dict.get("DEEPSEEK_CONFIG" if self.provider == llm_models.DEEPSEEK else "CLAUDE_CONFIG", {})
        self.config = {
            **self.DEFAULT_CONFIG,
            **{key: value for key, value in provider_config.items() if key in ("max_tokens", "max_concurrency", "effort")},
            **chosen,
        }
        self.market_session = market_session or "unknown"

        # The evaluated user's own key for this provider (user_settings.get_llm_api_key)
        self.deepseek = DeepSeekClient(api_key=api_key) if self.provider == llm_models.DEEPSEEK else None
        self.client = AsyncAnthropic(api_key=api_key) if self.deepseek is None else None
        self.user_name = user_name
        self.source = source  # "digest" or "watchdog", for the usage ledger (src/usage_tracking.py)
        self.key_error: Optional[ApiKeyUnusable] = None  # First key failure; later requests are skipped
        self.semaphore = asyncio.Semaphore(self.config["max_concurrency"])
        self.evaluated_at = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M %Z")

    async def _create_message(self, user_prompt: str) -> Any:
        """Sends one evaluation request, opting into server-side refusal fallbacks when the model supports them."""
        request = {
            "model": self.config["model"],
            "max_tokens": self.config["max_tokens"],
            "output_config": {"effort": self.config["effort"]},
            "system": self.config["system_prompt"],
            "tools": [EVALUATION_TOOL_SPEC],
            # tool_choice "auto" + strict schema: forced tool_choice is rejected by newer models
            "tool_choice": {"type": "auto"},
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if (
            params_dict.get("CLAUDE_REFUSAL_FALLBACK", True)
            and self.config["model"] in params_dict.get("CLAUDE_FALLBACK_DEFAULT_MODELS", [])
        ):
            return await self.client.beta.messages.create(
                **request, betas=[FALLBACK_BETA], fallbacks="default"
            )
        return await self.client.messages.create(**request)

    async def _evaluate_with_deepseek(self, symbol: str, user_prompt: str) -> Optional[HoldingEvaluation]:
        """
        Claude-authored (2026-10-16): one evaluation on DeepSeek, with the same tool schema and validation.
        2026-10-17: a misshapen answer is repaired, and one still invalid is asked for once more (validated_answer).
        """
        request = build_request(
            self.config["model"], self.config["effort"], self.config["system_prompt"], user_prompt, EVALUATION_TOOL_SPEC
        )
        evaluation = await validated_answer(
            self.deepseek, request, EVALUATION_TOOL_SPEC, HoldingEvaluation, symbol, logger,
            on_response=lambda response: record_deepseek_response(
                self.user_name, self.source, self.config["model"], response),
        )
        if evaluation is not None:
            logger.info(f"[{symbol}] Evaluated -> {evaluation.recommendation}")
        return evaluation

    async def _evaluate_single_holding(
        self, symbol: str, holding_payload: Dict[str, Any]
    ) -> Optional[HoldingEvaluation]:
        async with self.semaphore:
            if self.key_error is not None:  # Same key, same failure: don't send (holdings queued past max_concurrency)
                return None
            holding_prompt_data = json.dumps(holding_payload, indent=2, default=str)
            user_prompt = format_user_prompt(
                symbol, self.evaluated_at, holding_prompt_data, market_session=self.market_session
            )
            return await self._confirmed(symbol, user_prompt, await self._review(symbol, user_prompt))

    async def _confirmed(
        self, symbol: str, user_prompt: str, first: Optional[HoldingEvaluation]
    ) -> Optional[HoldingEvaluation]:
        """
        Claude-authored (2026-10-22, at Andres's request): a first review saying one of HOLDINGS_RECHECK's SIGNALS
        (urgent_sell, profit_sell) gets EXTRA_REVIEWS more, and the majority's label wins (majority_review), so a
        one-off urgent sell can't send a Telegram alert. Any other first review stands as it is.
        """
        recheck = params_dict.get("HOLDINGS_RECHECK") or {}
        extra_reviews = recheck.get("EXTRA_REVIEWS", 0)
        if first is None or first.recommendation not in recheck.get("SIGNALS", []) or extra_reviews <= 0:
            return first
        extra = await asyncio.gather(*(self._review(symbol, user_prompt) for _ in range(extra_reviews)))
        reviews = [first] + [review for review in extra if review is not None]
        chosen = majority_review(reviews)
        logger.info(f"[{symbol}] First review said {first.recommendation}; reviewed {len(reviews)} times: "
                    f"{[review.recommendation for review in reviews]} -> {chosen.recommendation}")
        return chosen

    async def _review(self, symbol: str, user_prompt: str) -> Optional[HoldingEvaluation]:
        """One review of one holding (2026-10-22: its own method, so _confirmed can ask again)."""
        try:
            if self.deepseek is not None:
                return await self._evaluate_with_deepseek(symbol, user_prompt)
            response = await self._create_message(user_prompt)
            # Claude-authored (2026-10-16): every answered request is billed, refusals and truncations included
            record_anthropic_response(self.user_name, self.source, response)

            if response.stop_reason == "refusal":
                category = getattr(response.stop_details, "category", None)
                logger.error(f"[{symbol}] Request declined by safety classifier ({category}).")
                return None
            if response.stop_reason == "max_tokens":
                logger.error(
                    f"[{symbol}] Response truncated at max_tokens "
                    f"({self.config['max_tokens']}); raise max_tokens or lower effort."
                )
                return None
            iterations = getattr(response.usage, "iterations", None) or []
            if any(getattr(entry, "type", None) == "fallback_message" for entry in iterations):
                logger.info(f"[{symbol}] Served by fallback model {response.model} after a refusal.")

            tool_block = next(
                (
                    b for b in response.content
                    if b.type == "tool_use" and b.name == EVALUATION_TOOL_NAME
                ),
                None,
            )
            if not tool_block:
                logger.error(
                    f"[{symbol}] Model did not call {EVALUATION_TOOL_NAME}; no recommendation recorded."
                )
                return None

            evaluation = HoldingEvaluation(**tool_block.input)
            logger.info(f"[{symbol}] Evaluated -> {evaluation.recommendation}")
            return evaluation

        except (APIError, DeepSeekError) as e:
            key_error = (deepseek_key_error if isinstance(e, DeepSeekError) else anthropic_key_error)(e, self.user_name)
            if key_error is None:
                logger.error(f"[{symbol}] {self.provider} API Error: {e.message}")
            elif self.key_error is None:
                self.key_error = key_error
                logger.error(f"[{symbol}] {key_error}")
        except ValidationError as e:
            logger.error(f"[{symbol}] The model's answer didn't match the expected fields: {e}")
        except Exception as e:
            logger.error(
                f"[{symbol}] Unexpected Error during evaluation: {str(e)}"
            )

        return None

    async def evaluate_holdings(
        self, holding_payloads: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        symbols = list(holding_payloads.keys())
        logger.info(
            f"Starting parallel evaluation for {len(symbols)} held positions "
            f"({self.config['model']}, effort={self.config['effort']}, session={self.market_session})..."
        )

        tasks = [
            self._evaluate_single_holding(symbol, holding_payloads[symbol])
            for symbol in symbols
        ]

        results: List[Optional[HoldingEvaluation]] = await asyncio.gather(*tasks)
        if self.deepseek is not None:
            await self.deepseek.aclose()
        evaluations: Dict[str, Dict[str, Any]] = {
            symbol: result.model_dump()
            for symbol, result in zip(symbols, results)
            if result is not None
        }

        logger.info(
            f"Evaluation complete. Successfully evaluated {len(evaluations)}/{len(symbols)} holdings."
            + (f" Stopped early: {self.provider} key problem ({self.key_error.reason})." if self.key_error else "")
        )
        return evaluations


def run_holding_evaluation(
    holding_payloads: Dict[str, Dict[str, Any]],
    api_key: str,
    config: Optional[Dict[str, Any]] = None,
    market_session: Optional[str] = None,
    user_name: Optional[str] = None,
    source: str = "watchdog",
) -> tuple[Dict[str, Dict[str, Any]], Optional[ApiKeyUnusable]]:
    """
    Synchronous entry point to run the async Claude holding evaluation pipeline. Returns the evaluations that
    succeeded and the key error that stopped the run, if any (evaluations finished before it are kept).
    """
    evaluator = ParallelHoldingEvaluator(
        api_key=api_key, config=config, market_session=market_session, user_name=user_name, source=source
    )
    evaluations = asyncio.run(evaluator.evaluate_holdings(holding_payloads))
    return evaluations, evaluator.key_error


def resolve_llm_key(user_name: Optional[str], provider: str = llm_models.ANTHROPIC) -> str:
    """
    The user's own key for `provider` ("Anthropic" or "DeepSeek") after a free check (Anthropic: lists a model;
    DeepSeek: reads the balance). Raises ApiKeyUnusable when it is missing, invalid, revoked, expired, unpermitted,
    or (DeepSeek) out of balance, so callers can stop before fetching any market data for the evaluation.
    """
    api_key = get_llm_api_key(user_name, provider)
    verify_llm_key(provider, api_key, user_name)
    return api_key


def build_holding_payloads(holdings_df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """
    Groups holdings by symbol into LLM payloads: every lot's position data plus the shared market context.

    Claude-authored (2026-10-15): uses the 'portfolio_weight_pct' column when present. get_holdings_digest_payload
    computes it over the whole portfolio before the watchdog narrows to the stocks being re-checked; computing it
    here would measure each stock against only that subset.
    """
    position_cols = [c for c in holdings_df.columns if c not in EXCLUDED_POSITION_COLUMNS]
    total_market_value = holdings_df["market_value"].sum()

    payloads: Dict[str, Dict[str, Any]] = {}
    for symbol, group in holdings_df.groupby("symbol"):
        symbol_market_value = group["market_value"].sum()
        if "portfolio_weight_pct" in group.columns and pd.notna(group["portfolio_weight_pct"].iloc[0]):
            weight = float(group["portfolio_weight_pct"].iloc[0])
        else:
            weight = round(symbol_market_value / total_market_value * 100, 2) if total_market_value > 0 else None
        payloads[symbol] = {
            "symbol": symbol,
            "portfolio_weight_pct": weight,
            "positions": group[position_cols].to_dict(orient="records"),
            "market_context": (
                group["market_context"].iloc[0] if "market_context" in group.columns else None
            ),
        }
        # Claude-authored (2026-10-22): the Finnbot idea the user bought it from, with a swing idea's plan
        origin = prompt_view(group["idea_origin"].iloc[0]) if "idea_origin" in group.columns else None
        if origin:
            payloads[symbol]["opened_from_finnbot_idea"] = origin
    return payloads


def add_recommendations(
    self, holdings_df: pd.DataFrame, reuse_recent_evaluations: bool = False
) -> pd.DataFrame:
    """
    Uses Claude to add a 'recommendation' (buy_more / hold / risky / profit_sell / urgent_sell), a 'sentiment'
    (Bullish / Neutral / Bearish), and a 'recommendation_rationale' to each holding, based on the position
    data and the 'market_context' column added by add_metrics_to_holdings. Uses the user's model/effort
    (self.llm_settings: Claude or DeepSeek) with their own key for it, and records results in the watchdog state file.

    Sets self.llm_key_error to the ApiKeyUnusable that stopped the evaluation (None otherwise). Holdings
    evaluated before a mid-run key failure (e.g. a spend limit reached) keep their recommendations.

    With reuse_recent_evaluations (the digest), stocks evaluated within DIGEST.REUSE_EVALUATION_MAX_AGE_MINUTES
    that have moved less than WATCHDOG.REEVAL_NATR_MULTIPLE x NATR since keep that evaluation instead of calling
    Claude again. The watchdog leaves it off: it has already decided which stocks need a fresh evaluation.
    """
    self.llm_key_error = None
    settings = getattr(self, "llm_settings", None) or llm_models.job_settings("HOLDINGS")
    if holdings_df.empty:
        print("Holdings DataFrame is empty. No recommendations to add.")
        return holdings_df

    df = holdings_df.copy()
    holding_payloads = build_holding_payloads(df)
    now = datetime.now(timezone.utc)
    user_name = getattr(self, "user_name", None)

    # Claude-authored (2026-10-15): reuse evaluations the watchdog made minutes ago
    reused: Dict[str, Dict[str, Any]] = {}
    if reuse_recent_evaluations and user_name:
        reused = reusable_evaluations(
            user_name,
            df,
            now,
            max_age_minutes=params_dict["DIGEST"]["REUSE_EVALUATION_MAX_AGE_MINUTES"],
            max_natr_move=params_dict["WATCHDOG"]["REEVAL_NATR_MULTIPLE"],
        )
        if reused:
            print(f"Reusing {len(reused)} recent evaluation(s) instead of calling the LLM again: {sorted(reused)}")
    to_evaluate = {symbol: payload for symbol, payload in holding_payloads.items() if symbol not in reused}

    evaluations: Dict[str, Dict[str, Any]] = {}
    if to_evaluate:
        try:
            evaluations, self.llm_key_error = run_holding_evaluation(
                to_evaluate,
                api_key=resolve_llm_key(user_name, settings.get("provider") or llm_models.provider_of(settings.get("model"))),
                config=settings,
                market_session=market_phase(now),
                user_name=user_name,
                source="digest" if reuse_recent_evaluations else "watchdog",  # Only the digest reuses evaluations
            )
        except ApiKeyUnusable as e:
            self.llm_key_error = e
        except ValueError as e:
            print(f"[ERROR] Could not initialize holding evaluator: {e}")
        if self.llm_key_error is not None:
            print(f"[ERROR] {self.llm_key_error}")

    results = {**reused, **evaluations}
    df["recommendation"] = df["symbol"].map(
        lambda s: results.get(s, {}).get("recommendation")
    )
    df["sentiment"] = df["symbol"].map(
        lambda s: results.get(s, {}).get("sentiment")
    )
    df["recommendation_rationale"] = df["symbol"].map(
        lambda s: results.get(s, {}).get("rationale")
    )

    # Claude-authored (2026-10-13): remember each evaluation so the watchdog can skip unchanged stocks.
    # 2026-10-15: only new evaluations; reused ones keep their original time so they still age out.
    if user_name and evaluations:
        evaluated = df[df["symbol"].isin(evaluations)]
        record_evaluations(user_name, evaluated, evaluated_at=now)
        # Claude-authored (2026-10-15): append to the long-term track record (src/track_record.py). Only the digest
        # reuses recent evaluations, so the flag also tells which run made them.
        log_personal(
            user_name,
            evaluated,
            now,
            source="digest" if reuse_recent_evaluations else "watchdog",
            model=settings.get("model"),
        )
    return df
