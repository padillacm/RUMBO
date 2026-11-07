"""The anti-cheat sensor is tested by the same tests that get copied into every project,
plus the fitness function of ADR-0007 (idiomatic assertion counting and helper resolution).
"""

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from textwrap import dedent

import pytest

from rumbo.sensors import check_tests_not_weakened as sensor


def test_sensor_tests_pass_in_generated_project(simple_project: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-v",
            "-p",
            "no:cacheprovider",
            "tests/unit/test_check_tests_not_weakened.py",
        ],
        cwd=simple_project,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert " passed" in result.stdout and "failed" not in result.stdout


# ------------------------------------------------------------- ADR-0007: counted forms
Snapshot = Mapping[str, str | None]


def _problems(base: Snapshot, current: Snapshot) -> list[str]:
    return sensor.compare(sensor.analyze(base), sensor.analyze(current))


def _asserts(snapshot: Snapshot, test: str = "tests/test_a.py::test_one") -> int:
    return sensor.analyze(snapshot).tests[test].asserts


def _test_one(*lines: str) -> str:
    body = "".join(f"    {line}\n" for line in lines)
    return f"import pytest\n\ndef test_one(m):\n    assert m\n{body}"


COUNTED_FORMS: dict[str, tuple[str, ...]] = {
    "pytest.raises": ("with pytest.raises(ValueError):", "    int('x')"),
    "pytest.warns": ("with pytest.warns(UserWarning):", "    warn('w')"),
    "pytest.deprecated_call": ("with pytest.deprecated_call():", "    old()"),
    "two withitems": ("with pytest.raises(ValueError), pytest.warns(UserWarning):", "    f()"),
    "pytest.fail": ("if not m:", "    pytest.fail('nope')"),
    "mock.assert_called_once_with": ("m.assert_called_once_with(1)",),
    "mock on nested receiver": ("m.return_value.assert_awaited_once()",),
    **{f"mock.{name}": (f"m.{name}()",) for name in sorted(sensor.MOCK_ASSERT_NAMES)},
}


@pytest.mark.parametrize("form", list(COUNTED_FORMS), ids=list(COUNTED_FORMS))
def test_each_counted_form_is_seen_when_removed(form: str) -> None:
    base = {"tests/test_a.py": _test_one(*COUNTED_FORMS[form])}
    current = {"tests/test_a.py": _test_one()}
    expected = 3 if form == "two withitems" else 2
    assert _asserts(base) == expected
    assert _problems(base, current) == [
        f"tests/test_a.py::test_one: asserts dropped from {expected} to 1",
        f"total asserts in the tests directory dropped from {expected} to 1",
    ]


def test_self_assert_methods_and_fail_are_counted() -> None:
    source = dedent(
        """
        import unittest

        class TestX(unittest.TestCase):
            def test_one(self):
                self.assertEqual(1, 1)
                self.assertTrue(True)
                with self.assertRaises(ValueError):
                    int('x')
                if False:
                    self.fail('never')
        """
    )
    base = {"tests/test_a.py": source}
    current = {"tests/test_a.py": source.replace("        self.assertTrue(True)\n", "")}
    assert _asserts(base, "tests/test_a.py::TestX.test_one") == 4
    assert any("TestX.test_one: asserts dropped from 4 to 3" in p for p in _problems(base, current))


def test_pytest_contexts_count_only_with_the_pytest_prefix() -> None:
    """A bare ``raises(...)`` is not pytest's: a hollow local one must not pass for an assertion."""
    source = dedent(
        """
        from contextlib import nullcontext as raises

        def test_one():
            with raises():
                pass
        """
    )
    assert _asserts({"tests/test_a.py": source}) == 0


def test_deleting_a_pytest_raises_block_is_reported() -> None:
    source = dedent(
        """
        import pytest

        def test_one():
            value = int('1')
            assert value == 1
            with pytest.raises(ValueError):
                int('x')
        """
    )
    base = {"tests/test_a.py": source}
    current = {
        "tests/test_a.py": source.replace(
            "    with pytest.raises(ValueError):\n        int('x')\n", ""
        )
    }
    assert _problems(base, current) == [
        "tests/test_a.py::test_one: asserts dropped from 2 to 1",
        "total asserts in the tests directory dropped from 2 to 1",
    ]


def test_constant_asserts_are_still_flagged() -> None:
    base = {"tests/test_a.py": "def test_one():\n    assert 1 == 1\n"}
    current = {"tests/test_a.py": "def test_one():\n    assert True\n"}
    assert any("always-true" in p for p in _problems(base, current))


# --------------------------------------------------------- ADR-0007: helper resolution
INLINE = dedent(
    """
    def test_one():
        x = 2
        assert x == 2
        assert x > 1
        assert x < 3
        assert x
    """
)
HELPER = dedent(
    """
    def check(x):
        assert x == 2
        assert x > 1
        assert x < 3
        assert x
    """
)
HOLLOW = "\ndef check(x):\n    pass\n"
CONSTANT = "\ndef check(x):\n" + "    assert True\n" * 4


def _caller(prelude: str = "", call: str = "check(x)") -> str:
    return f"{prelude}\ndef test_one():\n    x = 2\n    {call}\n"


EXTRACTED: dict[str, dict[str, str | None]] = {
    "same file": {"tests/test_a.py": HELPER + _caller()},
    "conftest.py in the same directory": {
        "tests/conftest.py": HELPER,
        "tests/test_a.py": _caller(),
    },
    "conftest.py in an ancestor directory": {
        "tests/conftest.py": HELPER,
        "tests/unit/test_a.py": _caller(),
    },
    "from tests.helpers import check": {
        "tests/helpers.py": HELPER,
        "tests/test_a.py": _caller("from tests.helpers import check\n"),
    },
    "from .helpers import check": {
        "tests/helpers.py": HELPER,
        "tests/test_a.py": _caller("from .helpers import check\n"),
    },
    "from helpers import check": {
        "tests/helpers.py": HELPER,
        "tests/test_a.py": _caller("from helpers import check\n"),
    },
    "import helpers": {
        "tests/helpers.py": HELPER,
        "tests/test_a.py": _caller("import helpers\n", "helpers.check(x)"),
    },
    "import tests.helpers as h": {
        "tests/helpers.py": HELPER,
        "tests/test_a.py": _caller("import tests.helpers as h\n", "h.check(x)"),
    },
    "from tests import helpers": {
        "tests/helpers.py": HELPER,
        "tests/test_a.py": _caller("from tests import helpers\n", "helpers.check(x)"),
    },
    "from ..helpers import check (package)": {
        "tests/helpers/__init__.py": HELPER,
        "tests/unit/test_a.py": _caller("from ..helpers import check\n"),
    },
}


def _test_path(snapshot: Snapshot) -> str:
    return next(p for p in snapshot if p.endswith("test_a.py"))


@pytest.mark.parametrize("layout", list(EXTRACTED), ids=list(EXTRACTED))
def test_extracting_asserts_into_a_helper_is_accepted(layout: str) -> None:
    current = EXTRACTED[layout]
    test_path = _test_path(current)
    base: dict[str, str | None] = {test_path: INLINE}
    for path in current:
        base.setdefault(path, "")
    assert _problems(base, current) == []
    assert _asserts(current, f"{test_path}::test_one") == 4
    assert sensor.analyze(current).asserts == 4, "the total must not change with the refactor"
    # and inlining the helper again is just as fine
    assert _problems(current, base) == []


@pytest.mark.parametrize("layout", list(EXTRACTED), ids=list(EXTRACTED))
def test_hollowing_a_helper_is_reported_for_its_callers(layout: str) -> None:
    base = EXTRACTED[layout]
    hollow = {path: (content or "").replace(HELPER, HOLLOW) for path, content in base.items()}
    assert hollow != base
    assert _problems(base, hollow) == [
        f"{_test_path(base)}::test_one: asserts dropped from 4 to 0",
        "total asserts in the tests directory dropped from 4 to 0",
    ]
    # a helper hollowed into constant asserts is caught as always-true
    constant = {path: (content or "").replace(HELPER, CONSTANT) for path, content in base.items()}
    assert any("always-true" in p for p in _problems(base, constant))


def test_helper_with_pytest_raises_counts_under_the_same_rules() -> None:
    source = dedent(
        """
        import pytest

        def check(f):
            with pytest.raises(ValueError):
                f()
            assert f

        def test_one():
            check(int)
        """
    )
    assert _asserts({"tests/test_a.py": source}) == 2


def test_helper_cycle_terminates_and_counts_zero() -> None:
    source = dedent(
        """
        def h1(x):
            assert x
            h2(x)

        def h2(x):
            assert x
            h1(x)

        def test_one():
            h1(1)
        """
    )
    assert _asserts({"tests/test_a.py": source}) == 2  # h1 + h2; the call back into h1 is 0


def test_resolution_depth_is_bounded_at_three() -> None:
    def chain(depth_with_assert: int) -> dict[str, str | None]:
        helpers = ""
        for i in range(1, 5):
            body = "    assert x\n" if i == depth_with_assert else "    pass\n"
            nxt = f"    h{i + 1}(x)\n" if i < 4 else ""
            helpers += f"def h{i}(x):\n{body}{nxt}\n"
        return {"tests/test_a.py": helpers + "def test_one():\n    h1(1)\n"}

    assert _asserts(chain(3)) == 1
    assert _asserts(chain(4)) == 0


def test_unresolvable_calls_count_zero() -> None:
    source = dedent(
        """
        from somewhere_else import assert_ok
        import other

        def test_one():
            assert_ok(1)
            other.assert_ok(1)
            unknown(1)
        """
    )
    assert _asserts({"tests/test_a.py": source}) == 0


def test_unreached_helpers_and_module_level_asserts_still_count_in_the_total() -> None:
    source = dedent(
        """
        assert __name__

        def unused_helper():
            assert 1
            assert 2

        def test_one():
            assert 3
        """
    )
    inv = sensor.analyze({"tests/test_a.py": source})
    assert inv.tests["tests/test_a.py::test_one"].asserts == 1
    assert inv.asserts == 4


def test_skip_inside_a_resolved_helper_counts_as_a_skip() -> None:
    source = dedent(
        """
        import pytest

        def check(x):
            assert x

        def test_one():
            check(1)
        """
    )
    base = {"tests/test_a.py": source}
    skipping = source.replace("    assert x\n", "    pytest.skip('x')\n    assert x\n")
    assert any("skip" in p for p in _problems(base, {"tests/test_a.py": skipping}))


# ------------------------------------------------------------ ADR-0007: tests directory
def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args], cwd=repo, check=True, capture_output=True
    )


def test_tests_directory_defaults_then_pyproject_then_flag(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    assert sensor.tests_directory(tmp_path) == "tests"
    pyproject.write_text("[tool.rumbo]\ntests = 'spec/'\n", encoding="utf-8")
    assert sensor.tests_directory(tmp_path) == "spec"
    assert sensor.tests_directory(tmp_path, "checks\\unit") == "checks/unit"
    pyproject.write_text("[tool.rumbo\n", encoding="utf-8")
    assert sensor.tests_directory(tmp_path) == "tests"
    pyproject.write_text("[tool.other]\nx = 1\n", encoding="utf-8")
    assert sensor.tests_directory(tmp_path) == "tests"


def test_configured_tests_directory_is_honoured_end_to_end(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "human@example.com")
    _git(tmp_path, "config", "user.name", "Human")
    (tmp_path / "pyproject.toml").write_text('[tool.rumbo]\ntests = "spec"\n', encoding="utf-8")
    (tmp_path / "spec").mkdir()
    (tmp_path / "tests").mkdir()
    spec_file = tmp_path / "spec" / "test_x.py"
    spec_file.write_text("def test_x():\n    assert 1\n    assert 2\n", encoding="utf-8")
    (tmp_path / "tests" / "test_y.py").write_text("def test_y():\n    assert 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feature")
    spec_file.write_text("def test_x():\n    assert 1\n", encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "weaken spec")

    root = ["--root", str(tmp_path), "--base", "main"]
    assert sensor.main(root) == 1
    assert sensor.main([*root, "--tests-dir", "tests"]) == 0
    assert sensor.main([*root, "--tests-dir", "spec"]) == 1
