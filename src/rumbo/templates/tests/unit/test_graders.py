"""Tests of the deterministic graders in evals/."""

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from graders import (
    GRADERS,
    Verdict,
    abstention,
    field_equality,
    grade,
    numeric,
    takes_input,
    tool_arguments,
)


def test_field_equality() -> None:
    assert field_equality({"a": 1, "b": 2}, {"a": 1}).ok
    assert not field_equality({"a": 1}, {"a": 2}).ok


def test_numeric_with_tolerance() -> None:
    assert numeric({"value": 100.05}, {"value": 100.0, "rel_tol": 1e-3}).ok
    assert not numeric({"value": 101.0}, {"value": 100.0, "rel_tol": 1e-3}).ok
    assert not numeric({}, {"value": 1.0}).ok


def test_tool_arguments() -> None:
    output = {"tool_calls": [{"name": "query", "args": {"metric": "price", "days": 7}}]}
    assert tool_arguments(output, {"tool": "query", "args": {"metric": "price"}}).ok
    assert not tool_arguments(output, {"tool": "query", "args": {"metric": "demand"}}).ok
    assert not tool_arguments({}, {"tool": "query"}).ok


def test_abstention() -> None:
    assert abstention({"abstain": True}, {}).ok
    assert not abstention({"answer": "42"}, {}).ok


def test_grade_runs_two_argument_graders_as_they_are() -> None:
    assert not any(takes_input(grader) for grader in GRADERS.values())
    assert asyncio.run(grade(field_equality, {"a": 1}, {"a": 1}, {"question": "ignored"})).ok
    assert not asyncio.run(grade(abstention, {"answer": "42"}, {})).ok


def test_grade_passes_the_case_input_and_awaits_coroutines() -> None:
    def same_language(output: dict[str, Any], expected: dict[str, Any], case: Any) -> Verdict:
        return Verdict(output.get("lang") == case["lang"], "answered in another language")

    async def slow(output: dict[str, Any], expected: dict[str, Any]) -> Verdict:
        await asyncio.sleep(0)
        return field_equality(output, expected)

    assert takes_input(same_language) and not takes_input(slow)
    assert asyncio.run(grade(same_language, {"lang": "es"}, {}, {"lang": "es"})).ok
    assert not asyncio.run(grade(same_language, {"lang": "en"}, {}, {"lang": "es"})).ok
    assert asyncio.run(grade(slow, {"a": 1}, {"a": 1})).ok
