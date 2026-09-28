import copy
from typing import Any, Dict


def enrich_recommendations(
    generated_recommendations: Dict[str, Any],
    sanitized_LLM_inputs: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Enriches stock recommendations with supplementary fundamental and technical metrics

    extracted from sanitized_LLM_inputs context payload.

    - top_long_term entries receive: 'pe_trailing', 'recommendation_key'
    - top_swing entries receive: '52_week_high', '50_day_average'
    """
    # Create a deep copy to avoid mutating the original input dict
    enriched = copy.deepcopy(generated_recommendations)

    # Enrich Long-Term Recommendations
    for item in enriched.get("top_long_term", []):
        ticker = item.get("ticker")
        if ticker and ticker in sanitized_LLM_inputs:
            ticker_data = sanitized_LLM_inputs[ticker]
            item["pe_trailing"] = ticker_data.get("pe_trailing")
            item["recommendation_key"] = ticker_data.get("recommendation_key")

    # Enrich Swing Recommendations
    for item in enriched.get("top_swing", []):
        ticker = item.get("ticker")
        if ticker and ticker in sanitized_LLM_inputs:
            ticker_data = sanitized_LLM_inputs[ticker]
            item["52_week_high"] = ticker_data.get("52_week_high")
            item["50_day_average"] = ticker_data.get("50_day_average")

    return enriched