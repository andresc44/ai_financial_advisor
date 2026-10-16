# File to attach data from each pick's market data to the universal recommendations shown in the digest.
"""
Authorship: rewritten by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-16, at Andres's request.

Why: the attached values were read from the top level of each ticker's sanitized data (e.g. data["pe_trailing"]),
but sanitizer.py nests every field in a section (data["valuation_and_fundamentals"]["pe_trailing"]), so all of them
came back None. Which fields each list gets is now set in params_dict["RECOMMENDATION_FIELDS"] as
{"name in the digest": "section.field"}, so adding or removing one is a parameters.py change.
"""
import copy
import sys
from pathlib import Path
from typing import Any, Dict, Optional

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict

ALL_LISTS = "all"  # RECOMMENDATION_FIELDS key whose fields go on every list's picks
_MISSING = object()


def lookup(data: Any, path: str) -> Any:
    """The value at a dotted path like "price_action.52_week_high", or _MISSING when a part of the path doesn't exist."""
    value = data
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return _MISSING
        value = value[key]
    return value


def fields_for(list_name: str, fields: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    """The {name: path} fields for one list: the "all" entries, then that list's own (which win on the same name)."""
    return {**fields.get(ALL_LISTS, {}), **fields.get(list_name, {})}


def enrich_recommendations(
    generated_recommendations: Dict[str, Any],
    sanitized_LLM_inputs: Dict[str, Dict[str, Any]],
    fields: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Returns a copy of the picks ({"top_long_term": [...], "top_swing": [...]}) with each pick given the fields
    configured for its list in params_dict["RECOMMENDATION_FIELDS"] (or `fields`), read from that ticker's sanitized
    data. A field the data has but left empty is None. A path that doesn't exist in the data is also None, and is
    reported once per run, since it's almost certainly a typo in parameters.py. Names the scorer already set
    (ticker, longScore, swingScore) are never overwritten.
    """
    fields = params_dict.get("RECOMMENDATION_FIELDS", {}) if fields is None else fields
    enriched = copy.deepcopy(generated_recommendations)
    unknown_paths: set[tuple[str, str]] = set()
    taken_names: set[str] = set()

    for list_name, picks in enriched.items():
        wanted = fields_for(list_name, fields)
        for item in picks:
            ticker_data = sanitized_LLM_inputs.get(item.get("ticker"))
            for name, path in wanted.items():
                if name in item:  # Set by the scorer
                    taken_names.add(name)
                    continue
                value = lookup(ticker_data, path) if ticker_data is not None else None
                if value is _MISSING:
                    unknown_paths.add((name, path))
                    value = None
                item[name] = copy.deepcopy(value)  # Lists (e.g. earnings dates) shouldn't share the scored data

    for name in sorted(taken_names):
        print(f"[WARNING] RECOMMENDATION_FIELDS: '{name}' is already used by the scorer; choose another name.")
    for name, path in sorted(unknown_paths):
        print(f"[WARNING] RECOMMENDATION_FIELDS: '{name}' points to '{path}', which isn't in the sanitized data "
              "(see sanitizer.py for the sections and fields). Attached as null.")
    return enriched
