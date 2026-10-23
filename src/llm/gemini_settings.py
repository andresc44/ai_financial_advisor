# File to build the request settings every Gemini call shares: its model, how much it thinks, and an output cap.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-23, at Andres's request.

Why: about 90% of Gemini's cost was output tokens, and most of those were thinking. No call set a thinking level, so
gemini-3.6-flash thought as much as it liked (~2.9K output tokens for ~1.1K in, per holding's news).
params_dict["GEMINI_CONFIG"] now sets the effort for every Gemini call in the project (holding news and macro news)
and caps each answer's length as a safety net.

Everything is read when a request is built, not at import, so a setting changed from the settings sheet
(src/config/remote_settings.py) applies to the run that read it.
"""
import sys
from pathlib import Path
from typing import Any, Optional

from google.genai import types

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config.parameters import params_dict

# Gemini 3's thinking levels, least to most; "default" leaves it to the model (as before 2026-10-23)
EFFORTS = ("minimal", "low", "medium", "high")
DEFAULT_EFFORT = "default"


def gemini_model(job: str) -> str:
    """The model for `job` ("HOLDING_NEWS" or "MACRO_NEWS"), from params_dict["GEMINI_MODELS"]."""
    return params_dict["GEMINI_MODELS"][job]


def gemini_effort() -> str:
    """GEMINI_CONFIG's effort, or "default" when it isn't one of EFFORTS (check_config reports that)."""
    effort = str((params_dict.get("GEMINI_CONFIG") or {}).get("effort") or DEFAULT_EFFORT).strip().lower()
    return effort if effort in EFFORTS else DEFAULT_EFFORT


def max_output_tokens() -> Optional[int]:
    """GEMINI_CONFIG's output cap (thinking included), or None for the model's own limit."""
    cap = (params_dict.get("GEMINI_CONFIG") or {}).get("max_output_tokens")
    return int(cap) if cap else None


def generation_config(**fields: Any) -> types.GenerateContentConfig:
    """A GenerateContentConfig with `fields` (system_instruction, tools, response_schema, ...) plus GEMINI_CONFIG."""
    effort = gemini_effort()
    thinking = types.ThinkingConfig(thinking_level=effort.upper()) if effort != DEFAULT_EFFORT else None
    return types.GenerateContentConfig(**fields, thinking_config=thinking, max_output_tokens=max_output_tokens())


def was_cut_off(response: Any) -> bool:
    """True when the answer stopped at max_output_tokens, so its JSON is incomplete."""
    candidates = getattr(response, "candidates", None) or []
    reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    return reason == types.FinishReason.MAX_TOKENS
