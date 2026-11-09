"""ADR-0013: RUMBO carries its own harness (`rumbo init --adopt --kind cli --layout flat`)."""

import tomllib
from pathlib import Path

import pytest

from rumbo import __version__
from rumbo.cli import main
from rumbo.scaffold import CHECK_FILE, GUARD_FILE, SENSOR_FILE

ROOT = Path(__file__).resolve().parents[1]


def _rumbo_table() -> dict[str, object]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    table = data["tool"]["rumbo"]
    assert isinstance(table, dict)
    return table


def test_the_repository_declares_itself_a_flat_cli() -> None:
    table = _rumbo_table()
    assert table["kind"] == "cli" and table["layout"] == "flat" and table["adopted"] is True
    assert table["rigor"] == "production" and table["llm"] is False
    assert table["version"] == __version__


def test_the_canonical_scripts_are_copied_verbatim() -> None:
    for copy, canonical in (
        ("scripts/rumbo_check.py", CHECK_FILE),
        ("scripts/check_tests_not_weakened.py", SENSOR_FILE),
        ("scripts/rumbo_guard.py", GUARD_FILE),
    ):
        assert (ROOT / copy).read_text(encoding="utf-8") == canonical.read_text(encoding="utf-8")


def test_rumbo_doctor_passes_on_the_repository_itself(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor", "--dir", str(ROOT)]) == 0
    assert "Harness complete" in capsys.readouterr().out
