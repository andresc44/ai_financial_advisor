# File to call DeepSeek's chat API for the structured evaluations Claude otherwise does (new-stock scoring, holdings).
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-16, at Andres's request.

Why: Claude scoring cost ~$1.23 a run for ~37 candidates. Any job that takes a Claude model (params_dict["LLM"],
each user's HOLDINGS_LLM_MODEL) can take a DeepSeek model instead; src/llm_models.py decides which provider a
model belongs to, and the evaluators call this module for DeepSeek models.

API facts this relies on (api-docs.deepseek.com, checked 2026-10-16):
- POST {base_url}/chat/completions with "Authorization: Bearer <key>", OpenAI-style messages.
- Models: deepseek-flash (V4.1-Flash) and deepseek-v4-pro. The old deepseek-chat (V3) / deepseek-reasoner (R1) are
  gone; their split is now thinking mode: "thinking": {"type": "disabled"} (V3-like) or "enabled" with
  "reasoning_effort" low / high / max (R1-like). Thinking is on by default, so it is always set explicitly here.
- Function calling: tools [{"type": "function", "function": {name, description, parameters}}]. tool_choice may
  force one function only when thinking is off (a forced choice returns 400 in thinking mode). "strict" is a beta
  feature on a separate base URL, so it isn't used: the arguments are validated with Pydantic by the caller.
- Response: choices[0].message.tool_calls[].function.arguments is a JSON string that "may be invalid"; finish_reason
  is stop / length / content_filter / tool_calls / insufficient_system_resource / aborted.
- usage: prompt_cache_hit_tokens, prompt_cache_miss_tokens, completion_tokens (reasoning tokens included).
- Errors: 400 bad format, 401 wrong key, 402 insufficient balance, 422 bad parameters, 429 too fast, 500/503 server.
- GET {base_url}/user/balance returns {"is_available": bool, "balance_infos": [...]}: used as the free key check.
"""
import asyncio
import copy
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict

RETRYABLE_STATUS = {429, 500, 503}
RETRY_DELAYS_SECONDS = (2, 5, 15)  # Waits before the 2nd, 3rd, and 4th attempt


def deepseek_config() -> dict[str, Any]:
    defaults = {"base_url": "https://api.deepseek.com", "timeout_seconds": 300, "max_tokens": None}
    return {**defaults, **params_dict.get("DEEPSEEK_CONFIG", {})}


class DeepSeekError(Exception):
    """An error response from DeepSeek (after retries), with its HTTP status and message."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"DeepSeek API error {status_code}: {message}")


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)[:300]
    return str(body)[:300]


class DeepSeekClient:
    """Async client for chat completions. One per evaluator run; close with aclose()."""

    def __init__(self, api_key: str, transport: Optional[httpx.AsyncBaseTransport] = None):
        config = deepseek_config()
        self._http = httpx.AsyncClient(
            base_url=config["base_url"],
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(config["timeout_seconds"], connect=15),
            transport=transport,
        )

    async def chat(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /chat/completions. Retries 429 / 500 / 503 and network errors; raises DeepSeekError otherwise."""
        for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
            last_attempt = attempt == len(RETRY_DELAYS_SECONDS)
            try:
                response = await self._http.post("/chat/completions", json=body)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as e:
                if last_attempt:
                    raise DeepSeekError(0, f"network error: {type(e).__name__}") from e
            else:
                if response.status_code == 200:
                    return response.json()
                if response.status_code not in RETRYABLE_STATUS or last_attempt:
                    raise DeepSeekError(response.status_code, _error_message(response))
            await asyncio.sleep(RETRY_DELAYS_SECONDS[attempt])
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        await self._http.aclose()


# ------------------------------------------------------------------------------------------------- tool calls
def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """The JSON schema with every "$ref": "#/$defs/X" replaced by its definition, and "$defs" removed."""
    definitions = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                merged = {**copy.deepcopy(definitions[ref.split("/")[-1]]), **{k: v for k, v in node.items() if k != "$ref"}}
                return resolve(merged)
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


def tool_definition(tool_spec: dict[str, Any]) -> dict[str, Any]:
    """Anthropic-style {name, description, input_schema} as a DeepSeek function tool (references inlined)."""
    return {
        "type": "function",
        "function": {
            "name": tool_spec["name"],
            "description": tool_spec.get("description", ""),
            "parameters": inline_refs(tool_spec["input_schema"]),
        },
    }


def thinking_enabled(effort: Optional[str]) -> bool:
    """effort "none" (or unset) = non-thinking mode (V3-like); low / high / max = thinking mode (R1-like)."""
    return (effort or "none").lower() != "none"


def build_request(
    model: str, effort: Optional[str], system_prompt: str, user_prompt: str, tool_spec: dict[str, Any]
) -> dict[str, Any]:
    """A chat-completions body that asks for exactly one call to the tool."""
    thinking = thinking_enabled(effort)
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        "tools": [tool_definition(tool_spec)],
        # Forcing the call is only allowed with thinking off; with thinking on the prompt asks for it
        "tool_choice": "auto" if thinking else {"type": "function", "function": {"name": tool_spec["name"]}},
        "thinking": {"type": "enabled" if thinking else "disabled"},
    }
    if thinking:
        body["reasoning_effort"] = effort.lower()
    max_tokens = deepseek_config().get("max_tokens")
    if max_tokens:
        body["max_tokens"] = int(max_tokens)
    return body


@dataclass
class ToolCallResult:
    arguments: Optional[dict[str, Any]]  # The tool's input, parsed (not yet validated against the schema)
    problem: Optional[str]               # Why there are no arguments, for the log


def _json_object(text: str) -> Optional[dict[str, Any]]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        value = json.loads(cleaned)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def tool_call_arguments(response: dict[str, Any], tool_name: str) -> ToolCallResult:
    """The arguments of the call to `tool_name`, or why there aren't any (truncated, filtered, no call)."""
    choice = (response.get("choices") or [{}])[0]
    finish = choice.get("finish_reason")
    if finish == "length":
        return ToolCallResult(None, "response cut off at max_tokens (DEEPSEEK_CONFIG.max_tokens)")
    if finish == "content_filter":
        return ToolCallResult(None, "response withheld by DeepSeek's content filter")
    if finish in ("insufficient_system_resource", "aborted"):
        return ToolCallResult(None, f"DeepSeek stopped the response ({finish})")
    message = choice.get("message") or {}
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        if function.get("name") == tool_name:
            arguments = _json_object(function.get("arguments") or "")
            return ToolCallResult(arguments, None if arguments is not None else "tool arguments were not valid JSON")
    # Some replies put the JSON in the message text instead of a tool call
    arguments = _json_object(message.get("content") or "")
    return ToolCallResult(arguments, None if arguments is not None else f"model did not call {tool_name}")


def check_balance(api_key: str) -> tuple[int, Optional[dict[str, Any]]]:
    """(HTTP status, body) of GET /user/balance: a free check that the key works and has funds."""
    config = deepseek_config()
    response = httpx.get(f"{config['base_url']}/user/balance", headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, body if isinstance(body, dict) else None
