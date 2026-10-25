# File to let the Claude evaluations share one cached copy of their instructions instead of paying for them every call.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-24, at Andres's request, after the Claude
Console reported a low prompt cache hit rate.

Why: every holdings evaluation (and every universal idea, when it runs on Claude) sends the same system prompt and
tool definition (~3.2K tokens for holdings) before the stock's own data, and none of it was cached, so each call paid
full price for it. Three changes, each off with params_dict["CLAUDE_PROMPT_CACHE"] = False:
- cached_system(): the system prompt carries a cache breakpoint. The tools come before it in the prompt, so the
  breakpoint caches both. Cache reads cost 0.1x the input price; the write that creates the entry costs 1.25x, and
  the entry lives 5 minutes from each use (a run's calls are seconds apart).
- first_then_rest(): the first stock runs alone, then the others at once. Sent all together, every call misses the
  cache at the same moment and pays the 1.25x write; this way one call writes and the rest read.
- cached_user(): a re-review (HOLDINGS_RECHECK) sends exactly the same prompt again, so its user message is cached
  too, and the re-reviews run one after the other so the second reads what the first wrote.
The instructions must stay byte-identical between calls for any of this to work: nothing that changes per call (a
date, a stock) may go in the system prompt or the tool definition.
"""
import asyncio
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence, TypeVar

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config.parameters import params_dict

T = TypeVar("T")
R = TypeVar("R")
EPHEMERAL = {"type": "ephemeral"}  # The 5-minute cache


def enabled() -> bool:
    """Whether Claude calls use the prompt cache (params_dict["CLAUDE_PROMPT_CACHE"], on by default)."""
    return bool(params_dict.get("CLAUDE_PROMPT_CACHE", True))


def cached_system(system_prompt: str) -> Any:
    """The `system` value: the prompt as one block with a cache breakpoint, or the plain string when caching is off."""
    if not enabled():
        return system_prompt
    return [{"type": "text", "text": system_prompt, "cache_control": EPHEMERAL}]


def cached_user(user_prompt: str) -> Any:
    """A user message's `content` with a cache breakpoint at its end, for a prompt that will be sent again as is."""
    if not enabled():
        return user_prompt
    return [{"type": "text", "text": user_prompt, "cache_control": EPHEMERAL}]


async def first_then_rest(items: Sequence[T], run: Callable[[T], Awaitable[R]]) -> list[R]:
    """
    run(item) for every item, in order: the first alone, so its call writes the cached instructions, then the rest
    at once, reading them. With caching off, all at once (as before).
    """
    if not items:
        return []
    if not enabled() or len(items) == 1:
        return list(await asyncio.gather(*(run(item) for item in items)))
    first = await run(items[0])
    return [first, *await asyncio.gather(*(run(item) for item in items[1:]))]
