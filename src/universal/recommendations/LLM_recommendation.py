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
    MOMENTUM_VOLUME_SCORE_DESCRIPTION,
    CATALYST_SENTIMENT_SCORE_DESCRIPTION,
    LONG_TERM_SENTIMENT_DESCRIPTION,
    LONG_TERM_THESIS_DESCRIPTION,
    KEY_RISK_DESCRIPTION,
    SWING_SENTIMENT_DESCRIPTION,
    SWING_THESIS_DESCRIPTION,
    LONG_TERM_TOOL_NAME,
    LONG_TERM_TOOL_DESCRIPTION,
    SWING_TOOL_NAME,
    SWING_TOOL_DESCRIPTION,
    LONG_TERM_SYSTEM_PROMPT,
    SWING_SYSTEM_PROMPT,
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
    """
    Claude-authored (2026-10-20): the swing call's answer. The fourth sub-score, risk_reward_ratio_score, and the
    entry, stop, and target come from the swing screen's fixed plan (swing_screen.trade_plan), scored in code by
    plan_risk_reward_score, so the model no longer chooses levels (swing_evaluation).
    """
    model_config = ConfigDict(extra="forbid")

    technical_setup_score: float = Field(
        ..., ge=0.0, le=30.0, description=TECHNICAL_SETUP_SCORE_DESCRIPTION
    )
    momentum_volume_score: float = Field(
        ..., ge=0.0, le=20.0, description=MOMENTUM_VOLUME_SCORE_DESCRIPTION
    )
    catalyst_sentiment_score: float = Field(
        ..., ge=0.0, le=20.0, description=CATALYST_SENTIMENT_SCORE_DESCRIPTION
    )
    # Claude-authored (2026-10-16): the reasoning and sentiment shown with the pick in the digest
    sentiment: Literal["Bullish", "Neutral", "Bearish"] = Field(..., description=SWING_SENTIMENT_DESCRIPTION)
    thesis: str = Field(..., description=SWING_THESIS_DESCRIPTION)


# Claude-authored (2026-10-20): each horizon has its own call: (system prompt, tool spec name, answer schema)
HORIZONS = ("long_term", "swing")


MIN_RISK_FRACTION = 0.01  # A stop closer than 1% of the entry is counted as 1% away when scoring the plan
# Claude-authored (2026-10-22): the plan's points by ratio when SWING_SCREEN.RISK_REWARD_POINTS isn't set
DEFAULT_RISK_REWARD_POINTS = [[1.5, 12], [2.0, 18], [2.5, 22.5], [3.0, 26], [3.5, 28.5], [4.0, 30]]


def risk_reward_points() -> List[List[float]]:
    """The [ratio, points] anchors of the risk/reward score (params_dict["SWING_SCREEN"]["RISK_REWARD_POINTS"])."""
    return (params_dict.get("SWING_SCREEN") or {}).get("RISK_REWARD_POINTS") or DEFAULT_RISK_REWARD_POINTS


def daily_atr(daily_bars: Any, days: int = 14) -> Optional[float]:
    """
    Claude-authored (2026-10-18): the average true range over the last `days` sessions, from `daily_bars` (newest
    first, as DataFetcher.recent_daily_bars gives them): the size of a normal day's move. Each day's true range is the
    largest of high - low and the gaps from the previous close. None with fewer than 4 ranges.
    """
    bars = [bar for bar in (daily_bars or []) if isinstance(bar, dict)
            and all(isinstance(bar.get(key), (int, float)) for key in ("high", "low", "close"))]
    ranges = [max(today["high"] - today["low"], abs(today["high"] - previous["close"]), abs(today["low"] - previous["close"]))
              for today, previous in zip(bars, bars[1:])][:days]
    return sum(ranges) / len(ranges) if len(ranges) >= 4 else None


def plan_risk_reward_score(entry: Any, stop: Any, target: Any, atr: Optional[float] = None) -> float:
    """
    Claude-authored (2026-10-18): risk_reward_ratio_score (0-30) for a swing plan. 2026-10-22 (re-banded at Andres's
    request): straight lines between SWING_SCREEN.RISK_REWARD_POINTS' anchors (1.5:1 -> 12, 2:1 -> 18, 2.5:1 -> 22.5,
    3:1 -> 26, 3.5:1 -> 28.5, 4:1 -> 30), from 0 at 0.5:1 up to the first, and full marks from the last; until then
    bands of 0-11 below 1.5:1, 12-24 to 2.5:1, and 25-30 from it (30 from 4.5:1). Why in code: re-scoring 37
    candidates showed the model doesn't score its own levels (a 1.0:1 plan got 24/30 and an 8:1 plan 15/30). A plan
    that isn't 0 < stop < entry < target scores 0.
    The risk is at least one `atr` (a normal day's move; daily_atr) and at least MIN_RISK_FRACTION of the entry: a
    stop tighter than that is likely hit by ordinary noise, and on 2026-10-17 a falling stock (APH) scored 29/30 from
    a stop 1.4% under the price. The top band was 3:1 until 2026-10-18; 2.5:1 is a solid 2-8 week swing trade.
    """
    if not all(isinstance(v, (int, float)) for v in (entry, stop, target)) or not 0 < stop < entry < target:
        return 0.0
    ratio = (target - entry) / max(entry - stop, entry * MIN_RISK_FRACTION, atr or 0.0)
    anchors = risk_reward_points()
    first_ratio, first_points = anchors[0]
    if ratio < first_ratio:
        return round(max(0.0, ratio - 0.5) / (first_ratio - 0.5) * first_points, 1)
    for (ratio_0, points_0), (ratio_1, points_1) in zip(anchors, anchors[1:]):
        if ratio < ratio_1:
            return round(points_0 + (ratio - ratio_0) / (ratio_1 - ratio_0) * (points_1 - points_0), 1)
    return float(anchors[-1][1])


def long_term_evaluation(answer: LongTermSubScores) -> Dict[str, Any]:
    """{"longScore", "sub_scores": {"long_term": ...}} from the long-term call's answer."""
    return {"longScore": answer.total_score, "sub_scores": {"long_term": answer.model_dump()}}


def swing_evaluation(answer: SwingSubScores, swing_setup: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Claude-authored (2026-10-20): {"swingScore", "sub_scores": {"swing": ...}} from the swing call's answer and the
    swing screen's setup: the model's three sub-scores plus risk_reward_ratio_score, plan_risk_reward_score of the
    screen's plan (with its ATR), and the plan's levels, so the picks and the digest read them where they always have.
    A candidate without a plan gets 0 for risk/reward and no levels.
    """
    swing_setup = swing_setup or {}
    plan = swing_setup.get("plan") or {}
    facts = swing_setup.get("facts") or {}
    levels = (plan.get("entry_price"), plan.get("stop_loss"), plan.get("price_target"))
    section = answer.model_dump()
    section.update(
        risk_reward_ratio_score=plan_risk_reward_score(*levels, facts.get("atr")),
        entry_price=levels[0], stop_loss=levels[1], price_target=levels[2],
        setup=swing_setup.get("setup"), stop_basis=plan.get("stop_basis"), target_basis=plan.get("target_basis"),
    )
    total = round(answer.technical_setup_score + section["risk_reward_ratio_score"] + answer.momentum_volume_score
                  + answer.catalyst_sentiment_score, 2)
    return {"swingScore": total, "sub_scores": {"swing": section}}


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


def _tool_spec(name: str, description: str, schema: type[BaseModel]) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "strict": True,  # Guarantees schema-valid tool input on models that support strict tool use
        "input_schema": _strict_schema(schema.model_json_schema()),
    }


# Claude-authored (2026-10-20): one tool per horizon (was one that recorded both)
LONG_TERM_TOOL_SPEC = _tool_spec(LONG_TERM_TOOL_NAME, LONG_TERM_TOOL_DESCRIPTION, LongTermSubScores)
SWING_TOOL_SPEC = _tool_spec(SWING_TOOL_NAME, SWING_TOOL_DESCRIPTION, SwingSubScores)
HORIZON_SETTINGS = {
    "long_term": (LONG_TERM_SYSTEM_PROMPT, LONG_TERM_TOOL_SPEC, LongTermSubScores),
    "swing": (SWING_SYSTEM_PROMPT, SWING_TOOL_SPEC, SwingSubScores),
}

# Beta header for the server-side `fallbacks: "default"` parameter (models listed in
# params_dict["CLAUDE_FALLBACK_DEFAULT_MODELS"]); same value as in src/personalized/recommendation.py.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ParallelStockEvaluator:
    # Only used for settings nothing else sets (the model and effort come from params_dict["LLM"]["UNIVERSAL"])
    DEFAULT_CONFIG = {
        "max_tokens": 16000,
        "max_concurrency": 15,
    }

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        tool_spec: Optional[Dict[str, Any]] = None,
        tool_choice: Optional[Dict[str, Any]] = None,
        horizon: str = "long_term",
    ):
        # Claude-authored (2026-10-20): one horizon per evaluator ("long_term" or "swing"), each with its own system
        # prompt, tool, and answer schema (HORIZON_SETTINGS)
        if horizon not in HORIZON_SETTINGS:
            raise ValueError(f"Unknown horizon {horizon!r}; expected one of {HORIZONS}")
        self.horizon = horizon
        system_prompt, default_tool, self.answer_model = HORIZON_SETTINGS[horizon]
        # Claude-authored (2026-10-13): CLAUDE_CONFIG supplies model settings only. Its "system_prompt" key (if
        # present) is ignored so the rubric in LLM_rec_instr (2026-10-20: its prompt for the evaluator's horizon) is always what Claude receives.
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
            "system_prompt": system_prompt,
            **{key: value for key, value in provider_config.items() if key in ("max_tokens", "max_concurrency", "effort")},
            **chosen,
        }
        self.tool_spec = tool_spec or default_tool
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

    def _result(self, symbol: str, answer: BaseModel, payload: Dict[str, Any]) -> Dict[str, Any]:
        """The evaluation of one candidate for this horizon (long_term_evaluation / swing_evaluation), logged."""
        if self.horizon == "swing":
            result = swing_evaluation(answer, payload.get("swing_setup"))
            logger.info(f"[{symbol}] Evaluated -> swingScore: {result['swingScore']:.1f} "
                        f"(risk/reward {result['sub_scores']['swing']['risk_reward_ratio_score']} from the plan)")
        else:
            result = long_term_evaluation(answer)
            logger.info(f"[{symbol}] Evaluated -> longScore: {result['longScore']:.1f}")
        return result

    async def _evaluate_with_deepseek(self, symbol: str, user_prompt: str) -> Optional[BaseModel]:
        """
        Claude-authored (2026-10-16): one candidate scored on DeepSeek, with the same tool schema and validation.
        2026-10-17: a misshapen answer is repaired, and one still invalid is asked for once more (validated_answer).
        """
        request = build_request(
            self.config["model"], self.config["effort"], self.config["system_prompt"], user_prompt, self.tool_spec
        )
        return await validated_answer(
            self.deepseek, request, self.tool_spec, self.answer_model, symbol, logger,
            on_response=lambda response: record_deepseek_response(None, "universal", self.config["model"], response),
        )

    async def _evaluate_single_ticker(
        self, symbol: str, sanitized_payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        async with self.semaphore:
            if self.key_error is not None:  # Same key, same failure: don't send the remaining tickers
                return None
            sanitized_prompt_data = json.dumps(
                sanitized_payload, indent=2, default=str
            )
            user_prompt = format_user_prompt(symbol, sanitized_prompt_data, self.horizon)

            try:
                if self.deepseek is not None:
                    answer = await self._evaluate_with_deepseek(symbol, user_prompt)
                    return self._result(symbol, answer, sanitized_payload) if answer is not None else None
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

                return self._result(symbol, self.answer_model(**tool_block.input), sanitized_payload)

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
            f"Starting parallel {self.horizon} evaluation for {len(symbols)} candidate payloads "
            f"({self.config['model']}, effort={self.config['effort']})..."
        )

        tasks = [
            self._evaluate_single_ticker(symbol, sanitized_inputs[symbol])
            for symbol in symbols
        ]

        results: List[Optional[Dict[str, Any]]] = await asyncio.gather(
            *tasks
        )
        if self.deepseek is not None:
            await self.deepseek.aclose()
        evaluations: Dict[str, Dict[str, Any]] = {}

        for symbol, result in zip(symbols, results):
            if result is None:
                continue
            # Claude-authored (2026-10-16): which LLM scored it (track record)
            evaluations[symbol] = {**result, "model": self.config["model"]}

        logger.info(
            f"Evaluation complete. Successfully scored {len(evaluations)}/{len(symbols)} tickers ({self.horizon})."
        )
        return evaluations


def run_llm_evaluation(
    sanitized_inputs: Dict[str, Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
    tool_spec: Optional[Dict[str, Any]] = None,
    issues: Optional[List[Dict[str, Any]]] = None,
    horizon: str = "long_term",
) -> Dict[str, Dict[str, Any]]:
    """
    Synchronous entry point to run the async Claude scoring pipeline.

    Claude-authored (2026-10-16): a shared-key failure (invalid, expired, out of credits, spend limit) stops the
    remaining requests and is appended to `issues` (when given) as {"source", "reason", "message"}.
    2026-10-20: scores one `horizon`: "long_term" ({ticker: {"longScore", "sub_scores": {"long_term"}, "model"}}) or
    "swing" ({ticker: {"swingScore", "sub_scores": {"swing"}, "model"}}, each payload carrying its "swing_setup").
    """
    evaluator = ParallelStockEvaluator(config=config, tool_spec=tool_spec, horizon=horizon)
    evaluations = asyncio.run(evaluator.evaluate_candidates(sanitized_inputs))
    if evaluator.key_error is not None and issues is not None:
        issues.append(shared_key_issue(evaluator.key_error))
    return evaluations