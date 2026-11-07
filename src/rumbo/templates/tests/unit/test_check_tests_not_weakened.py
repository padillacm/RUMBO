"""Tests of the anti-cheat sensor (scripts/check_tests_not_weakened.py)."""

import importlib.util
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_tests_not_weakened", ROOT / "scripts" / "check_tests_not_weakened.py"
)
assert _spec and _spec.loader
sensor = importlib.util.module_from_spec(_spec)
sys.modules["check_tests_not_weakened"] = sensor
_spec.loader.exec_module(sensor)

TEST_A = (
    "import pytest\n\n"
    "def test_one():\n    assert 1 + 1 == 2\n    assert len('ab') == 2\n\n"
    "class TestX:\n    def test_two(self):\n        assert True\n\n"
    "@pytest.mark.parametrize('x', [1, 2, 3])\n"
    "def test_three(x):\n    assert x\n"
)
BASE: dict[str, str | None] = {"tests/test_a.py": TEST_A, "tests/data.json": None}


def _problems(current: Mapping[str, str | None]) -> list[str]:
    return sensor.compare(sensor.analyze(BASE), sensor.analyze(current))


def test_no_change_no_problems() -> None:
    inv = sensor.analyze(BASE)
    assert _problems(dict(BASE)) == []
    assert inv.asserts == 4
    assert inv.tests["tests/test_a.py::TestX.test_two"].constant_asserts == 1
    assert inv.tests["tests/test_a.py::test_three"].param_cases == 3


def test_detects_deleted_test() -> None:
    current = dict(
        BASE,
        **{
            "tests/test_a.py": TEST_A.replace(
                "    def test_two(self):\n        assert True\n", "    pass\n"
            )
        },
    )
    assert any("TestX.test_two" in p for p in _problems(current))


def test_detects_fewer_asserts_in_a_test() -> None:
    current = dict(
        BASE,
        **{"tests/test_a.py": TEST_A.replace("    assert len('ab') == 2\n", "")},
    )
    assert any("test_one: asserts dropped" in p for p in _problems(current))


def test_detects_constant_assert_swap() -> None:
    swapped = TEST_A.replace(
        "    assert 1 + 1 == 2\n    assert len('ab') == 2\n", "    assert True\n    assert True\n"
    )
    assert any("always-true" in p for p in _problems(dict(BASE, **{"tests/test_a.py": swapped})))


def test_detects_skip_marker_and_parametrize_cut() -> None:
    skipped = TEST_A.replace("def test_one():", "@pytest.mark.skip\ndef test_one():")
    assert any("skip" in p for p in _problems(dict(BASE, **{"tests/test_a.py": skipped})))
    cut = TEST_A.replace("[1, 2, 3]", "[1]")
    assert any("parametrize" in p for p in _problems(dict(BASE, **{"tests/test_a.py": cut})))


def test_detects_deleted_file() -> None:
    assert _problems({"tests/test_a.py": TEST_A}) == ["test file deleted: tests/data.json"]


def test_adding_tests_is_fine() -> None:
    current = dict(BASE, **{"tests/test_b.py": "def test_new():\n    assert 1\n"})
    assert _problems(current) == []


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args], cwd=repo, check=True, capture_output=True
    )


def test_end_to_end_with_git(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "human@example.com")
    _git(tmp_path, "config", "user.name", "Human")
    (tmp_path / "tests").mkdir()
    test_file = tmp_path / "tests" / "test_x.py"
    test_file.write_text("def test_x():\n    assert 1\n    assert 2\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feature")

    test_file.write_text("def test_x():\n    assert 1\n")
    _git(tmp_path, "commit", "-qam", "weaken")
    assert sensor.main(["--root", str(tmp_path), "--base", "main"]) == 1

    # an agent-authored approval does not count
    _git(
        tmp_path,
        "commit",
        "-q",
        "--amend",
        "-m",
        "weaken [tests-change-approved]\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
    )
    assert sensor.main(["--root", str(tmp_path), "--base", "main"]) == 1

    _git(tmp_path, "commit", "-q", "--amend", "-m", "weaken [tests-change-approved]")
    assert sensor.main(["--root", str(tmp_path), "--base", "main"]) == 0


def test_strict_without_base_fails(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    assert sensor.main(["--root", str(tmp_path), "--strict", "--base", "does-not-exist"]) == 2
