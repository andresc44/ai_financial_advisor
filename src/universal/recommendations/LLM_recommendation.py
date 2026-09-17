import asyncio
import logging
import os
from typing import Any, Dict, List, Literal, Optional
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from anthropic import AsyncAnthropic, APIError
from pathlib import Path
import sys

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
    
from src.parameters import params_dict
from src.universal.recommendations.sanitizer import sanitize_ticker_payload

# Automatically load environment variables from .env file
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("Finnbot.LLMEvaluator")


class StockRecommendation(BaseModel):
    recommended: bool = Field(
        ..., 
        description="Set to True ONLY if the stock meets all criteria for a strong BUY entry."
    )
    current_price: float = Field(..., description="Current stock market price.")
    analyst_sentiment: Literal["Bullish", "Neutral", "Bearish"] = Field(
        ..., 
        description="Overall market and fundamental consensus sentiment."
    )
    entry_range: List[float] = Field(
        ..., 
        description="Ideal price entry range as a 2-element list: [min_entry, max_entry]."
    )
    target: float = Field(..., description="Price target based on key resistance levels.")
    stop_value: float = Field(..., description="Stop-loss price based on support levels or ATR.")
    analysis_summary: str = Field(
        ..., 
        description="A concise 1-2 sentence core thesis explaining why the trade was accepted or rejected."
    )
    trade_type: Literal["Swing", "Long-Term", "None"] = Field(
        ..., 
        description="Recommended holding timeframe."
    )


RECOMMENDATION_TOOL_SPEC = {
    "name": "record_stock_recommendation",
    "description": "Record the quantitative and qualitative trading evaluation for a candidate stock.",
    "input_schema": StockRecommendation.model_json_schema()
}


class ParallelStockEvaluator:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        # Fall back to params_dict["CLAUDE_CONFIG"] if custom config is not supplied
        self.config = config or params_dict.get("CLAUDE_CONFIG", {})
        
        # Load API key from environment (.env file)
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not found in environment variables or .env file.")

        self.client = AsyncAnthropic(api_key=api_key)
        self.semaphore = asyncio.Semaphore(self.config.get("max_concurrency", 15))

    async def _evaluate_single_ticker(
        self, 
        symbol: str, 
        raw_data: Dict[str, Any]
    ) -> Optional[StockRecommendation]:
        async with self.semaphore:
            sanitized_prompt_data = sanitize_ticker_payload(symbol, raw_data)
            user_prompt = f"Evaluate {symbol} using the provided market data context:\n\n{sanitized_prompt_data}"

            try:
                response = await self.client.messages.create(
                    model=self.config.get("model", "claude-3-5-sonnet-20241022"),
                    max_tokens=self.config.get("max_tokens", 1000),
                    temperature=self.config.get("temperature", 0.0),
                    system=self.config.get("system_prompt", ""),
                    tools=[RECOMMENDATION_TOOL_SPEC],
                    tool_choice={"type": "tool", "name": "record_stock_recommendation"},
                    messages=[{"role": "user", "content": user_prompt}]
                )

                tool_block = next((b for b in response.content if b.type == "tool_use"), None)
                if not tool_block:
                    logger.error(f"[{symbol}] Failed to extract tool output block from response.")
                    return None

                recommendation = StockRecommendation(**tool_block.input)
                logger.info(f"[{symbol}] Evaluation completed. Recommended: {recommendation.recommended}")
                return recommendation

            except APIError as e:
                logger.error(f"[{symbol}] Anthropic API Error: {e.message}")
            except Exception as e:
                logger.error(f"[{symbol}] Unexpected Error during evaluation: {str(e)}")
            
            return None

    async def evaluate_candidates(
        self, 
        llm_packet: Dict[str, Dict[str, Any]], 
        filter_recommended_only: bool = True
    ) -> Dict[str, Dict[str, Any]]:
        symbols = list(llm_packet.keys())
        logger.info(f"Starting parallel evaluation for {len(symbols)} candidates...")

        tasks = [
            self._evaluate_single_ticker(symbol, llm_packet[symbol]) 
            for symbol in symbols
        ]

        results: List[Optional[StockRecommendation]] = await asyncio.gather(*tasks)
        final_buy_list: Dict[str, Dict[str, Any]] = {}

        for symbol, result in zip(symbols, results):
            if result is None:
                continue

            if filter_recommended_only and not result.recommended:
                continue

            final_buy_list[symbol] = result.model_dump()

        logger.info(f"Evaluation complete. {len(final_buy_list)}/{len(symbols)} passed final LLM evaluation.")
        return final_buy_list


def run_llm_evaluation(
    llm_packet: Dict[str, Dict[str, Any]], 
    config: Optional[Dict[str, Any]] = None
) -> Dict[str, Dict[str, Any]]:
    """Synchronous entry point to run the async Claude evaluation pipeline."""
    evaluator = ParallelStockEvaluator(config=config)
    return asyncio.run(evaluator.evaluate_candidates(llm_packet))