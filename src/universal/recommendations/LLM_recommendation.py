import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from anthropic import APIError, AsyncAnthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict
from src.universal.recommendations.LLM_rec_instr import (
    VALUATION_SCORE_DESCRIPTION,
    FINANCIAL_HEALTH_SCORE_DESCRIPTION,
    GROWTH_MOMENTUM_SCORE_DESCRIPTION,
    COMPETITIVE_MOAT_SCORE_DESCRIPTION,
    TECHNICAL_SETUP_SCORE_DESCRIPTION,
    RISK_REWARD_SCORE_DESCRIPTION,
    MOMENTUM_VOLUME_SCORE_DESCRIPTION,
    CATALYST_SENTIMENT_SCORE_DESCRIPTION,
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
class LongTermSubScores(BaseModel):
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
    long_term: LongTermSubScores
    swing: SwingSubScores


EVALUATION_TOOL_SPEC = {
    "name": EVALUATION_TOOL_NAME,
    "description": EVALUATION_TOOL_DESCRIPTION,
    "input_schema": StockEvaluationBreakdown.model_json_schema(),
}


class ParallelStockEvaluator:
    DEFAULT_CONFIG = {
        "model": "claude-opus-5",
        "max_tokens": 16000,
        "effort": "high",
        "system_prompt": SYSTEM_PROMPT,
        "max_concurrency": 15,
    }

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        tool_spec: Optional[Dict[str, Any]] = None,
        tool_choice: Optional[Dict[str, Any]] = None,
    ):
        self.config = {
            **self.DEFAULT_CONFIG,
            **params_dict.get("CLAUDE_CONFIG", {}),
            **(config or {}),
        }
        self.tool_spec = tool_spec or EVALUATION_TOOL_SPEC
        self.tool_choice = tool_choice or {
            "type": "tool",
            "name": self.tool_spec["name"],
        }

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not found in environment variables or .env file."
            )

        self.client = AsyncAnthropic(api_key=api_key)
        self.semaphore = asyncio.Semaphore(self.config["max_concurrency"])

    async def _evaluate_single_ticker(
        self, symbol: str, sanitized_payload: Dict[str, Any]
    ) -> Optional[StockEvaluationBreakdown]:
        async with self.semaphore:
            sanitized_prompt_data = json.dumps(
                sanitized_payload, indent=2, default=str
            )
            user_prompt = format_user_prompt(symbol, sanitized_prompt_data)

            try:
                response = await self.client.messages.create(
                    model=self.config["model"],
                    max_tokens=self.config["max_tokens"],
                    output_config={"effort": self.config["effort"]},
                    system=self.config["system_prompt"],
                    tools=[self.tool_spec],
                    tool_choice=self.tool_choice,
                    messages=[{"role": "user", "content": user_prompt}],
                )

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
                    (b for b in response.content if b.type == "tool_use"), None
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

            except APIError as e:
                logger.error(f"[{symbol}] Anthropic API Error: {e.message}")
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
            f"Starting parallel evaluation for {len(symbols)} candidate payloads..."
        )

        tasks = [
            self._evaluate_single_ticker(symbol, sanitized_inputs[symbol])
            for symbol in symbols
        ]

        results: List[Optional[StockEvaluationBreakdown]] = await asyncio.gather(
            *tasks
        )
        evaluations: Dict[str, Dict[str, Any]] = {}

        for symbol, result in zip(symbols, results):
            if result is None:
                continue

            evaluations[symbol] = {
                "swingScore": result.swing.total_score,
                "longScore": result.long_term.total_score,
                "sub_scores": result.model_dump(),
            }

        logger.info(
            f"Evaluation complete. Successfully scored {len(evaluations)}/{len(symbols)} tickers."
        )
        return evaluations


def run_llm_evaluation(
    sanitized_inputs: Dict[str, Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
    tool_spec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Synchronous entry point to run the async Claude scoring pipeline."""
    evaluator = ParallelStockEvaluator(config=config, tool_spec=tool_spec)
    return asyncio.run(evaluator.evaluate_candidates(sanitized_inputs))