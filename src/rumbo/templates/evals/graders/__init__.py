"""Deterministic graders (A7). Each grader takes (output, expected) and returns a Verdict.

LLM judges (graders/judge.py) are added AFTER the first error analysis, and only once
calibrated against human labels (TPR/TNR ≥ 0.8 on ≥30 cases in evals/calibration/).
Register new graders in GRADERS.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Verdict:
    ok: bool
    detail: str = ""


Grader = Callable[[dict[str, Any], dict[str, Any]], Verdict]


def field_equality(output: dict[str, Any], expected: dict[str, Any]) -> Verdict:
    """Every field of `expected` must be equal in `output`."""
    different = [k for k, v in expected.items() if output.get(k) != v]
    return Verdict(not different, f"different fields: {different}" if different else "")


def numeric(output: dict[str, Any], expected: dict[str, Any]) -> Verdict:
    """`expected = {"field": "value", "value": 12.3, "rel_tol": 1e-3, "abs_tol": 0}`."""
    field = expected.get("field", "value")
    got = output.get(field)
    if not isinstance(got, int | float):
        return Verdict(False, f"{field!r} missing or not numeric: {got!r}")
    ok = math.isclose(
        got,
        expected["value"],
        rel_tol=expected.get("rel_tol", 1e-6),
        abs_tol=expected.get("abs_tol", 0.0),
    )
    return Verdict(ok, "" if ok else f"{got} != {expected['value']}")


def tool_arguments(output: dict[str, Any], expected: dict[str, Any]) -> Verdict:
    """Some call in `output["tool_calls"]` matches the tool name and the given arguments.

    `expected = {"tool": "query", "args": {"metric": "spot_price"}}`.
    """
    for call in output.get("tool_calls", []):
        if call.get("name") != expected["tool"]:
            continue
        args = call.get("args", {})
        if all(args.get(k) == v for k, v in expected.get("args", {}).items()):
            return Verdict(True)
    return Verdict(False, f"no call to {expected['tool']!r} with {expected.get('args')}")


def abstention(output: dict[str, Any], expected: dict[str, Any]) -> Verdict:
    """The system must abstain (not invent) when the data is missing."""
    ok = output.get("abstain") is True
    return Verdict(ok, "" if ok else "answered instead of abstaining")


GRADERS: dict[str, Grader] = {
    "field_equality": field_equality,
    "numeric": numeric,
    "tool_arguments": tool_arguments,
    "abstention": abstention,
}
