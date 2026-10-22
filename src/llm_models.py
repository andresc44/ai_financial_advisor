# File to decide which LLM provider (Anthropic Claude or DeepSeek) a model belongs to, and each job's model and effort.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-16, at Andres's request.

Any job that took a Claude model can take a DeepSeek model instead. The provider follows from the model name:
models in params_dict["ALLOWED_CLAUDE_MODELS"] use Anthropic, models in params_dict["ALLOWED_DEEPSEEK_MODELS"] use
DeepSeek (src/deepseek_client.py). Each model lists the efforts it accepts:
- Claude: low / medium / high / xhigh / max (output_config.effort).
- DeepSeek: none (non-thinking, like the old V3 / deepseek-chat) or low / high / max (thinking, like the old R1 /
  deepseek-reasoner).

The jobs (params_dict["LLM"]):
- "UNIVERSAL": new-stock scoring with the shared key (ANTHROPIC_API_KEY or DEEPSEEK_API_KEY).
- "HOLDINGS": each user's holdings evaluation, with their own key. Since 2026-10-22 it applies to every user (the
  per-user <NAME>_HOLDINGS_LLM_MODEL / _EFFORT are ignored; src/personalized/user_settings.get_llm_settings).
2026-10-17: UNIVERSAL_LLM_MODEL / UNIVERSAL_LLM_EFFORT in .env override LLM["UNIVERSAL"], so each machine can pick
the scoring model without editing parameters.py (ENV_OVERRIDES).
"""
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict

load_dotenv(dotenv_path=project_root / ".env")

ANTHROPIC = "Anthropic"  # Provider names, as ApiKeyUnusable and the usage ledger use them
DEEPSEEK = "DeepSeek"
FALLBACK_MODEL = "claude-sonnet-5-5"
# Claude-authored (2026-10-17): .env keys that override params_dict["LLM"][job]: (model key, effort key). HOLDINGS
# has none: it is set per user (<NAME>_HOLDINGS_LLM_MODEL).
ENV_OVERRIDES: dict[str, tuple[str, str]] = {"UNIVERSAL": ("UNIVERSAL_LLM_MODEL", "UNIVERSAL_LLM_EFFORT")}


def allowed_models() -> dict[str, list[str]]:
    """{model: efforts it accepts} for every selectable model of both providers."""
    return {**params_dict.get("ALLOWED_CLAUDE_MODELS", {}), **params_dict.get("ALLOWED_DEEPSEEK_MODELS", {})}


def provider_of(model: Optional[str]) -> str:
    """"DeepSeek" for models in ALLOWED_DEEPSEEK_MODELS (or named deepseek-*), else "Anthropic"."""
    if model in params_dict.get("ALLOWED_DEEPSEEK_MODELS", {}) or str(model or "").startswith("deepseek"):
        return DEEPSEEK
    return ANTHROPIC


def default_effort(model: str) -> str:
    """The provider's default effort (CLAUDE_CONFIG / DEEPSEEK_CONFIG "effort"), if the model accepts it."""
    config = params_dict.get("DEEPSEEK_CONFIG" if provider_of(model) == DEEPSEEK else "CLAUDE_CONFIG", {})
    efforts = allowed_models().get(model, [])
    effort = config.get("effort")
    if effort in efforts:
        return effort
    return "none" if provider_of(model) == DEEPSEEK else ("high" if "high" in efforts else (efforts or ["high"])[0])


def job_settings(job: str) -> dict[str, str]:
    """
    {"provider", "model", "effort"} for params_dict["LLM"][job] ("UNIVERSAL" or "HOLDINGS"). An unknown model falls
    back to FALLBACK_MODEL and an effort the model doesn't accept to the provider's default, with a warning, so a
    typo in parameters.py can't send an invalid request (src/check_config.py reports both).
    2026-10-17: the job's ENV_OVERRIDES keys in .env win over parameters.py. A model there that isn't allowed is
    ignored with a warning; one without an effort key gets parameters.py's effort if it's the same model, else the
    provider's default.
    """
    settings = dict(params_dict.get("LLM", {}).get(job, {}))
    models = allowed_models()
    model_key, effort_key = ENV_OVERRIDES.get(job, (None, None))
    env_model = (os.environ.get(model_key) or "").strip() if model_key else ""
    env_effort = (os.environ.get(effort_key) or "").strip().lower() if effort_key else ""
    if env_model and env_model not in models:
        print(f"[WARNING] {model_key}={env_model!r} in .env is not in ALLOWED_CLAUDE_MODELS / ALLOWED_DEEPSEEK_MODELS. "
              f"Using LLM[{job!r}] from parameters.py.")
    elif env_model and env_model != settings.get("model"):
        settings = {"model": env_model, "effort": None}  # parameters.py's effort was chosen for another model
    if env_effort:
        settings["effort"] = env_effort
    model = settings.get("model") or FALLBACK_MODEL
    if model not in models:
        print(f"[WARNING] LLM[{job!r}] model {model!r} is not in ALLOWED_CLAUDE_MODELS / ALLOWED_DEEPSEEK_MODELS. "
              f"Using {FALLBACK_MODEL}.")
        model = FALLBACK_MODEL
    effort = settings.get("effort")
    if effort not in models.get(model, []):
        if effort is not None:
            where = f"{effort_key} in .env" if env_effort else f"LLM[{job!r}]"
            print(f"[WARNING] {where} effort {effort!r} is not accepted by {model}. Using {default_effort(model)!r}.")
        effort = default_effort(model)
    return {"provider": provider_of(model), "model": model, "effort": effort}
