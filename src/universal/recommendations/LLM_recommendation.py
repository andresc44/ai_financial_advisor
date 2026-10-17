import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from anthropic import APIError, AsyncAnthropic
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src import llm_models
from src.deepseek_client import DeepSeekClient, DeepSeekError, build_request, validated_answer
from src.parameters import params_dict
from src.personalized.api_errors import (
    ApiKeyUnusable, anthropic_key_error, deepseek_key_error, shared_api_key, shared_key_issue,
)
from src.usage_tracking import record_anthropic_response, record_deepseek_response
from src.universal.recommendations.LLM_rec_instr import (
    VALUATION_SCORE_DESCRIPTION,
    FINANCIAL_HEALTH_SCORE_DESCRIPTION,
    GROWTH_MOMENTUM_SCORE_DESCRIPTION,
    COMPETITIVE_MOAT_SCORE_DESCRIPTION,
    TECHNICAL_SETUP_SCORE_DESCRIPTION,
    RISK_REWARD_SCORE_DESCRIPTION,
    MOMENTUM_VOLUME_SCORE_DESCRIPTION,
    CATALYST_SENTIMENT_SCORE_DESCRIPTION,
    LONG_TERM_SENTIMENT_DESCRIPTION,
    LONG_TERM_THESIS_DESCRIPTION,
    KEY_RISK_DESCRIPTION,
    SWING_SENTIMENT_DESCRIPTION,
    SWING_THESIS_DESCRIPTION,
    ENTRY_PRICE_DESCRIPTION,
    STOP_LOSS_DESCRIPTION,
    PRICE_TARGET_DESCRIPTION,
    EVALUATION_TOOL_NAME,
    EVALUATION_TOOL_DESCRIPTION,
    SYSTEM_PROMPT,
    format_user_prompt,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("Finnbot.LLMEvaluator")


# Pydantic Sub-Score Schemas
# Claude-authored (2026-10-13): extra="forbid" on every model emits additionalProperties: false, which strict tool
# use requires at every object level. The ge/le ranges stay here and are enforced when the tool input is parsed.
class LongTermSubScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valuation_score: float = Field(
        ..., ge=0.0, le=25.0, description=VALUATION_SCORE_DESCRIPTION
    )
    financial_health_score: float = Field(
        ..., ge=0.0, le=25.0, description=FINANCIAL_HEALTH_SCORE_DESCRIPTION
    )
    growth_momentum_score: float = Field(
        ..., ge=0.0, le=25.0, description=GROWTH_MOMENTUM_SCORE_DESCRIPTION
    )
    competitive_moat_score: float = Field(
        ..., ge=0.0, le=25.0, description=COMPETITIVE_MOAT_SCORE_DESCRIPTION
    )
    # Claude-authored (2026-10-16): the reasoning and sentiment shown with the pick in the digest
    sentiment: Literal["Bullish", "Neutral", "Bearish"] = Field(..., description=LONG_TERM_SENTIMENT_DESCRIPTION)
    thesis: str = Field(..., description=LONG_TERM_THESIS_DESCRIPTION)
    key_risk: str = Field(..., description=KEY_RISK_DESCRIPTION)

    @property
    def total_score(self) -> float:
        return round(
            self.valuation_score
            + self.financial_health_score
            + self.growth_momentum_score
            + self.competitive_moat_score,
            2,
        )


class SwingSubScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    technical_setup_score: float = Field(
        ..., ge=0.0, le=30.0, description=TECHNICAL_SETUP_SCORE_DESCRIPTION
    )
    risk_reward_ratio_score: float = Field(
        ..., ge=0.0, le=30.0, description=RISK_REWARD_SCORE_DESCRIPTION
    )
    momentum_volume_score: float = Field(
        ..., ge=0.0, le=20.0, description=MOMENTUM_VOLUME_SCORE_DESCRIPTION
    )
    catalyst_sentiment_score: float = Field(
        ..., ge=0.0, le=20.0, description=CATALYST_SENTIMENT_SCORE_DESCRIPTION
    )
    # Claude-authored (2026-10-16): the reasoning, sentiment, and trade levels shown with the pick in the digest.
    # The levels have no range constraints here: an inconsistent set (e.g. a stop above the entry) is dropped by the
    # scorer instead of discarding the whole evaluation.
    sentiment: Literal["Bullish", "Neutral", "Bearish"] = Field(..., description=SWING_SENTIMENT_DESCRIPTION)
    thesis: str = Field(..., description=SWING_THESIS_DESCRIPTION)
    entry_price: float = Field(..., description=ENTRY_PRICE_DESCRIPTION)
    stop_loss: float = Field(..., description=STOP_LOSS_DESCRIPTION)
    price_target: float = Field(..., description=PRICE_TARGET_DESCRIPTION)

    @property
    def total_score(self) -> float:
        return round(
            self.technical_setup_score
            + self.risk_reward_ratio_score
            + self.momentum_volume_score
            + self.catalyst_sentiment_score,
            2,
        )


class StockEvaluationBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    long_term: LongTermSubScores
    swing: SwingSubScores


# Claude-authored (2026-10-13): strict tool use rejects numeric/string constraints in the schema it is sent,
# so they are stripped from the copy sent to the API. The score ranges are still stated in each field's
# description (from LLM_rec_instr.py) and enforced by Pydantic when the tool input is parsed.
_STRICT_UNSUPPORTED_KEYS = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength",
}


def _strict_schema(schema: Any) -> Any:
    """Returns a copy of a JSON schema with keywords that strict tool use doesn't accept removed."""
    if isinstance(schema, dict):
        return {k: _strict_schema(v) for k, v in schema.items() if k not in _STRICT_UNSUPPORTED_KEYS}
    if isinstance(schema, list):
        return [_strict_schema(item) for item in schema]
    return schema


EVALUATION_TOOL_SPEC = {
    "name": EVALUATION_TOOL_NAME,
    "description": EVALUATION_TOOL_DESCRIPTION,
    "strict": True,  # Guarantees schema-valid tool input on models that support strict tool use
    "input_schema": _strict_schema(StockEvaluationBreakdown.model_json_schema()),
}

# Beta header for the server-side `fallbacks: "default"` parameter (models listed in
# params_dict["CLAUDE_FALLBACK_DEFAULT_MODELS"]); same value as in src/personalized/recommendation.py.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ParallelStockEvaluator:
    # Only used for settings nothing else sets (the model and effort come from params_dict["LLM"]["UNIVERSAL"])
    DEFAULT_CONFIG = {
        "max_tokens": 16000,
        "system_prompt": SYSTEM_PROMPT,
        "max_concurrency": 15,
    }

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        tool_spec: Optional[Dict[str, Any]] = None,
        tool_choice: Optional[Dict[str, Any]] = None,
    ):
        # Claude-authored (2026-10-13): CLAUDE_CONFIG supplies model settings only. Its "system_prompt" key (if
        # present) is ignored so the rubric in LLM_rec_instr.SYSTEM_PROMPT is always what Claude receives.
        # A caller can still pass a different system_prompt explicitly via `config`.
        # 2026-10-16: the model and effort are params_dict["LLM"]["UNIVERSAL"] (Claude or DeepSeek, src/llm_models.py);
        # the provider's request settings (CLAUDE_CONFIG / DEEPSEEK_CONFIG) fill in the rest.
        chosen = {**llm_models.job_settings("UNIVERSAL"), **(config or {})}
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
        self.tool_spec = tool_spec or EVALUATION_TOOL_SPEC
        # Claude-authored (2026-10-13): "auto" instead of forcing the tool ({"type": "tool", ...}), which returns a
        # 400 on Opus 5.5 / Sonnet 5.5 / Fable 5.1. SYSTEM_PROMPT instructs the model to call the tool, and the
        # strict schema keeps its input valid.
        self.tool_choice = tool_choice or {"type": "auto"}

        # Claude-authored (2026-10-16): the shared key, with the same failure handling as users' keys
        # (src/personalized/api_errors.py). A missing key raises ApiKeyUnusable, which is a ValueError as before.
        api_key = shared_api_key(self.provider)  # ANTHROPIC_API_KEY or DEEPSEEK_API_KEY
        self.key_error: Optional[ApiKeyUnusable] = None  # First key failure; later tickers are skipped

        self.deepseek = DeepSeekClient(api_key=api_key) if self.provider == llm_models.DEEPSEEK else None
        self.client = AsyncAnthropic(api_key=api_key) if self.deepseek is None else None
        self.semaphore = asyncio.Semaphore(self.config["max_concurrency"])

    async def _evaluate_with_deepseek(self, symbol: str, user_prompt: str) -> Optional[StockEvaluationBreakdown]:
        """
        Claude-authored (2026-10-16): one candidate scored on DeepSeek, with the same tool schema and validation.
        2026-10-17: a misshapen answer is repaired, and one still invalid is asked for once more (validated_answer).
        """
        request = build_request(
            self.config["model"], self.config["effort"], self.config["system_prompt"], user_prompt, self.tool_spec
        )
        breakdown = await validated_answer(
            self.deepseek, request, self.tool_spec, StockEvaluationBreakdown, symbol, logger,
            on_response=lambda response: record_deepseek_response(None, "universal", self.config["model"], response),
        )
        if breakdown is None:
            return None
        logger.info(
            f"[{symbol}] Evaluated -> swingScore: {breakdown.swing.total_score:.1f}, "
            f"longScore: {breakdown.long_term.total_score:.1f}"
        )
        return breakdown

    async def _evaluate_single_ticker(
        self, symbol: str, sanitized_payload: Dict[str, Any]
    ) -> Optional[StockEvaluationBreakdown]:
        async with self.semaphore:
            if self.key_error is not None:  # Same key, same failure: don't send the remaining tickers
                return None
            sanitized_prompt_data = json.dumps(
                sanitized_payload, indent=2, default=str
            )
            user_prompt = format_user_prompt(symbol, sanitized_prompt_data)

            try:
                if self.deepseek is not None:
                    return await self._evaluate_with_deepseek(symbol, user_prompt)
                request = {
                    "model": self.config["model"],
                    "max_tokens": self.config["max_tokens"],
                    "output_config": {"effort": self.config["effort"]},
                    "system": self.config["system_prompt"],
                    "tools": [self.tool_spec],
                    "tool_choice": self.tool_choice,
                    "messages": [{"role": "user", "content": user_prompt}],
                }
                # Claude-authored (2026-10-13): on models that support it, a safety-classifier refusal is re-run
                # server-side on a recommended fallback model instead of losing the ticker.
                if (
                    params_dict.get("CLAUDE_REFUSAL_FALLBACK", True)
                    and self.config["model"] in params_dict.get("CLAUDE_FALLBACK_DEFAULT_MODELS", [])
                ):
                    response = await self.client.beta.messages.create(
                        **request, betas=[FALLBACK_BETA], fallbacks="default"
                    )
                else:
                    response = await self.client.messages.create(**request)
                record_anthropic_response(None, "universal", response)  # Claude-authored (2026-10-16): shared-key cost

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

                tool_block = next(
                    (
                        b for b in response.content
                        if b.type == "tool_use" and b.name == self.tool_spec["name"]
                    ),
                    None,
                )
                if not tool_block:
                    logger.error(
                        f"[{symbol}] Failed to extract tool output block from response."
                    )
                    return None

                breakdown = StockEvaluationBreakdown(**tool_block.input)
                logger.info(
                    f"[{symbol}] Evaluated -> swingScore: {breakdown.swing.total_score:.1f}, "
                    f"longScore: {breakdown.long_term.total_score:.1f}"
                )
                return breakdown

            except ValidationError as e:
                # Claude-authored (2026-10-13): score ranges are no longer in the API schema (strict mode
                # rejects them), so an out-of-range score is caught here and the ticker is skipped.
                logger.error(f"[{symbol}] Sub-scores failed validation (e.g. outside rubric range): {e}")
            except (APIError, DeepSeekError) as e:
                key_error = (deepseek_key_error if isinstance(e, DeepSeekError) else anthropic_key_error)(e, None)
                if key_error is None:
                    logger.error(f"[{symbol}] {self.provider} API Error: {e.message}")
                elif self.key_error is None:
                    self.key_error = key_error
                    logger.error(f"[{symbol}] {key_error}")
            except Exception as e:
                logger.error(
                    f"[{symbol}] Unexpected Error during evaluation: {str(e)}"
                )

            return None

    async def evaluate_candidates(
        self, sanitized_inputs: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        symbols = list(sanitized_inputs.keys())
        logger.info(
            f"Starting parallel evaluation for {len(symbols)} candidate payloads "
            f"({self.config['model']}, effort={self.config['effort']})..."
        )

        tasks = [
            self._evaluate_single_ticker(symbol, sanitized_inputs[symbol])
            for symbol in symbols
        ]

        results: List[Optional[StockEvaluationBreakdown]] = await asyncio.gather(
            *tasks
        )
        if self.deepseek is not None:
            await self.deepseek.aclose()
        evaluations: Dict[str, Dict[str, Any]] = {}

        for symbol, result in zip(symbols, results):
            if result is None:
                continue

            evaluations[symbol] = {
                "swingScore": result.swing.total_score,
                "longScore": result.long_term.total_score,
                "sub_scores": result.model_dump(),
                "model": self.config["model"],  # Claude-authored (2026-10-16): which LLM scored it (track record)
            }

        logger.info(
            f"Evaluation complete. Successfully scored {len(evaluations)}/{len(symbols)} tickers."
        )
        return evaluations


def run_llm_evaluation(
    sanitized_inputs: Dict[str, Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
    tool_spec: Optional[Dict[str, Any]] = None,
    issues: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Synchronous entry point to run the async Claude scoring pipeline.

    Claude-authored (2026-10-16): a shared-key failure (invalid, expired, out of credits, spend limit) stops the
    remaining requests and is appended to `issues` (when given) as {"source", "reason", "message"}.
    """
    evaluator = ParallelStockEvaluator(config=config, tool_spec=tool_spec)
    evaluations = asyncio.run(evaluator.evaluate_candidates(sanitized_inputs))
    if evaluator.key_error is not None and issues is not None:
        issues.append(shared_key_issue(evaluator.key_error))
    return evaluations