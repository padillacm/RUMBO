"""Tests of the deterministic graders in evals/."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from graders import abstention, field_equality, numeric, tool_arguments


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
