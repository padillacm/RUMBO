"""The anti-cheat sensor is tested by the same tests that get copied into every project,
plus the fitness function of ADR-0007 (idiomatic assertion counting and helper resolution).
"""

import ast
import inspect
import io
import json
import os
import runpy
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from textwrap import dedent

import pytest

from rumbo.cli import main as rumbo
from rumbo.sensors import check_tests_not_weakened as sensor


# Covers F-0003.R14 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# Covers F-0003.R5, F-0003.R9 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# Covers F-0003.R9 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# Covers F-0003.R9 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# Covers F-0003.R5 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# Covers F-0003.R6 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# Covers F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# Covers F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# Covers F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# Covers F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# Covers F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# Covers F-0003.R9, F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# Covers F-0003.R5 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# Covers F-0003.R7 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# ----------------------------------------------- ADR-0007: helper methods of a test class
def _method_test(*members: str, bases: str = "", prelude: str = "") -> str:
    """A module with ``class TestX`` made of those members (already dedented, one per def)."""
    body = "\n".join(
        "".join(f"    {line}\n" for line in dedent(member).strip("\n").splitlines())
        for member in members
    )
    return f"{prelude}\nclass TestX{bases}:\n{body}"


TEST_X = "tests/test_a.py::TestX.test_one"


# Covers F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_helper_methods_are_resolved_on_self_cls_and_the_class_name() -> None:
    source = _method_test(
        """
        def _on_self(self, x):
            assert x
        """,
        """
        @classmethod
        def _on_cls(cls, x):
            assert x
            cls._static(x)
        """,
        """
        @staticmethod
        def _static(x):
            assert x
            assert x == 1
        """,
        """
        async def test_one(self):
            self._on_self(1)
            self._on_cls(1)
            TestX._static(1)
        """,
    )
    inv = sensor.analyze({"tests/test_a.py": source})
    assert inv.tests[TEST_X].asserts == 6
    assert inv.asserts == 6, "a reached method is not counted again outside the tests"


# Covers F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_self_call_resolves_only_inside_a_class_and_only_to_its_methods() -> None:
    source = dedent(
        """
        def check(x):
            assert x

        def test_one(self):
            self.check(1)

        class TestX:
            def test_one(self):
                self.check(1)
                self.other.check(1)
        """
    )
    inv = sensor.analyze({"tests/test_a.py": source})
    assert inv.tests["tests/test_a.py::test_one"].asserts == 0
    assert inv.tests[TEST_X].asserts == 0
    assert inv.asserts == 1


# Covers F-0003.R9 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_self_assert_method_still_counts_one_and_is_not_resolved() -> None:
    """``self.assert*`` is a counted form (ADR-0007, rule 3): one, whatever its body holds."""
    source = _method_test(
        """
        def assertValid(self, x):
            assert x
            assert x == 1
            self.assertEqual(x, 1)
        """,
        """
        def test_one(self):
            self.assertValid(1)
            self.assertEqual(1, 1)
        """,
        bases="(unittest.TestCase)",
        prelude="import unittest\n",
    )
    assert _asserts({"tests/test_a.py": source}, TEST_X) == 2


# Covers F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_helper_methods_keep_the_cycle_guard_and_the_depth_limit() -> None:
    cycle = _method_test(
        """
        def _h1(self, x):
            assert x
            self._h2(x)
        """,
        """
        def _h2(self, x):
            assert x
            self._h1(x)
        """,
        """
        def test_one(self):
            self._h1(1)
        """,
    )
    assert _asserts({"tests/test_a.py": cycle}, TEST_X) == 2

    def chain(depth_with_assert: int) -> dict[str, str | None]:
        members = [
            f"def _h{i}(self, x):\n"
            + ("    assert x\n" if i == depth_with_assert else "    pass\n")
            + (f"    self._h{i + 1}(x)\n" if i < 4 else "")
            for i in range(1, 5)
        ]
        return {"tests/test_a.py": _method_test(*members, "def test_one(self):\n    self._h1(1)\n")}

    assert _asserts(chain(3), TEST_X) == 1
    assert _asserts(chain(4), TEST_X) == 0


# Covers F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_inherited_helper_methods_follow_the_bases_inside_the_tests_directory() -> None:
    checks = dedent(
        """
        class Root:
            def _verify(self, x):
                assert x
                self._extra(x)

            def _extra(self, x):
                pass

        class Checks(Root):
            pass
        """
    )
    test = _method_test(
        """
        def _extra(self, x):
            assert x == 1
            assert x > 0
        """,
        """
        def test_one(self):
            self._verify(1)
        """,
        bases="(Checks)",
    )
    layouts = {
        "same module": {"tests/test_a.py": checks + test},
        "from tests.checks import Checks": {
            "tests/checks.py": checks,
            "tests/test_a.py": "from tests.checks import Checks\n" + test,
        },
        "from .checks import Checks as Base": {
            "tests/checks.py": checks,
            "tests/test_a.py": "from .checks import Checks as Base\n"
            + test.replace("(Checks)", "(Base)"),
        },
        "import checks": {
            "tests/checks.py": checks,
            "tests/test_a.py": "import checks\n" + test.replace("(Checks)", "(checks.Checks)"),
        },
    }
    for name, snapshot in layouts.items():
        # Root._verify (1) and, through ``self``, the override of _extra in TestX (2)
        assert _asserts(snapshot, TEST_X) == 3, name
        assert sensor.analyze(snapshot).asserts == 3, name
    outside = {"tests/test_a.py": "from somewhere_else import Checks\n" + test}
    assert _asserts(outside, TEST_X) == 0
    looped = "class A(B):\n    pass\n\nclass B(A):\n    pass\n" + test.replace("(Checks)", "(A)")
    assert _asserts({"tests/test_a.py": looped}, TEST_X) == 0


# Covers F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_moving_asserts_between_a_test_method_and_its_helper_method_is_accepted() -> None:
    asserts = ("assert x == 2", "assert x > 1", "assert x < 3", "assert x")
    inline = _method_test(
        "def test_one(self):\n    x = 2\n" + "".join(f"    {a}\n" for a in asserts)
    )
    extracted = _method_test(
        "def _verify(self, x):\n" + "".join(f"    {a}\n" for a in asserts),
        "def test_one(self):\n    x = 2\n    self._verify(x)\n",
    )
    gutted = _method_test(
        "def _verify(self, x):\n    pass\n", "def test_one(self):\n    x = 2\n    self._verify(x)\n"
    )
    base, current = {"tests/test_a.py": inline}, {"tests/test_a.py": extracted}
    assert _problems(base, current) == []
    assert _problems(current, base) == []
    assert _problems(current, {"tests/test_a.py": gutted}) == [
        "tests/test_a.py::TestX.test_one: asserts dropped from 4 to 0",
        "total asserts in the tests directory dropped from 4 to 0",
    ]


# ------------------------------------------------------------ ADR-0007: tests directory
def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args], cwd=repo, check=True, capture_output=True
    )


# Covers F-0003.R1 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# Covers F-0003.R1 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
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


# ----------------------------------- F-0003: what is reported besides a lost assertion
TWO_TESTS = dedent(
    """
    def test_one(x):
        assert x == 1

    class TestX:
        def test_two(self, x):
            assert x == 2
    """
)
ONE_TEST = dedent(
    """
    import unittest

    import pytest
    from pytest import mark

    def test_one(x):
        assert x == 1
    """
)
SKIPPED = "tests/test_a.py::test_one: gained skip/skipif/xfail markers or pytest.skip() calls"


# Covers F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_deleted_file_and_a_deleted_or_renamed_test_are_reported() -> None:
    base = {"tests/test_a.py": TWO_TESTS, "tests/data.json": None}
    assert _problems(base, {"tests/test_a.py": TWO_TESTS}) == ["test file deleted: tests/data.json"]
    renamed = {**base, "tests/test_a.py": TWO_TESTS.replace("def test_two", "def test_2")}
    assert _problems(base, renamed) == ["test deleted or renamed: tests/test_a.py::TestX.test_two"]
    moved = {"tests/test_b.py": TWO_TESTS, "tests/data.json": None}
    assert _problems(base, moved) == [
        "test file deleted: tests/test_a.py",
        "test deleted or renamed: tests/test_a.py::TestX.test_two",
        "test deleted or renamed: tests/test_a.py::test_one",
    ]
    added = {**base, "tests/test_b.py": "def test_new(x):\n    assert x\n"}
    assert _problems(base, added) == []


# Covers F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_test_file_that_no_longer_parses_is_reported() -> None:
    base = {"tests/test_a.py": "def test_one(x):\n    assert x == 1\n"}
    broken = {"tests/test_a.py": "def test_one(x:\n    assert x == 1\n"}
    problems = _problems(base, broken)
    assert "test deleted or renamed: tests/test_a.py::test_one" in problems
    assert any(p.startswith("parse error: tests/test_a.py: cannot parse") for p in problems)


# Covers F-0003.R5 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_drop_in_the_total_alone_is_reported() -> None:
    test = "def test_one(x):\n    assert x == 1\n"
    base = {"tests/test_a.py": "def unused(x):\n    assert x\n    assert x > 0\n\n\n" + test}
    assert _problems(base, {"tests/test_a.py": test}) == [
        "total asserts in the tests directory dropped from 3 to 1"
    ]


# Covers F-0003.R6 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize(
    ("line", "always_true"),
    [
        ("assert True", True),
        ("assert 'not empty'", True),
        ("assert (x == 1, 'the message')", True),
        ("assert 0", False),
        ("assert x == 2", False),
    ],
)
def test_truthy_constants_and_tuples_are_always_true_asserts(line: str, always_true: bool) -> None:
    base = {"tests/test_a.py": "def test_one(x):\n    assert x == 1\n"}
    current = {"tests/test_a.py": f"def test_one(x):\n    {line}\n"}
    reported = "tests/test_a.py::test_one: gained always-true asserts (assert True / tuple)"
    assert _problems(base, current) == ([reported] if always_true else [])


# Covers F-0003.R7 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize(
    "decorator",
    [
        "@pytest.mark.skip",
        "@pytest.mark.skip(reason='later')",
        "@pytest.mark.skipif(True, reason='not here')",
        "@pytest.mark.xfail",
        "@mark.xfail(strict=False)",
        "@unittest.skip('later')",
    ],
)
def test_a_skip_or_xfail_marker_on_a_test_is_reported(decorator: str) -> None:
    base = {"tests/test_a.py": ONE_TEST}
    current = {"tests/test_a.py": ONE_TEST.replace("def test_one", f"{decorator}\ndef test_one")}
    assert current != base
    assert _problems(base, current) == [SKIPPED]


# Covers F-0003.R7 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("call", ["pytest.skip('later')", "pytest.xfail('known')"])
def test_a_skip_or_xfail_call_in_a_test_is_reported(call: str) -> None:
    base = {"tests/test_a.py": ONE_TEST}
    current = {"tests/test_a.py": ONE_TEST.replace("    assert", f"    {call}\n    assert")}
    assert current != base
    assert _problems(base, current) == [SKIPPED]


# Covers F-0003.R8 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_shorter_parametrize_list_is_reported() -> None:
    source = dedent(
        """
        import pytest

        @pytest.mark.parametrize("x", [1, 2])
        @pytest.mark.parametrize("y", (1, 2, 3))
        def test_one(x, y):
            assert x <= y
        """
    )
    base = {"tests/test_a.py": source}
    assert sensor.analyze(base).tests["tests/test_a.py::test_one"].param_cases == 6
    cut = {"tests/test_a.py": source.replace("(1, 2, 3)", "(1, 2)")}
    assert _problems(base, cut) == [
        "tests/test_a.py::test_one: parametrize cases dropped from 6 to 4"
    ]
    longer = {"tests/test_a.py": source.replace("[1, 2]", "[1, 2, 3]")}
    assert _problems(base, longer) == []


# ------------------------------------------- F-0003: the base, the approval, the exit codes
Identity = tuple[str, str]
HUMAN: Identity = ("Human", "human@example.com")
CI_VARIABLES = (
    "GITHUB_EVENT_PATH",
    "GITHUB_BASE_REF",
    "RUMBO_BASE_REF",
    "GITHUB_ACTIONS",
    "GITHUB_EVENT_NAME",
)
# The import cannot be satisfied: the sensor reads the tests, it never imports them.
STRONG = (
    "import a_module_that_is_not_installed as m\n\n\n"
    "def test_x():\n    assert m.one() == 1\n    assert m.two() == 2\n"
)
WEAK = STRONG.replace("    assert m.two() == 2\n", "")
DROPPED = "tests/test_x.py::test_x: asserts dropped from 2 to 1"


def _commit(
    repo: Path, message: str, author: Identity = HUMAN, committer: Identity = HUMAN
) -> None:
    """Commit the tracked changes (or nothing) with that message and those identities."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author[0],
        "GIT_AUTHOR_EMAIL": author[1],
        "GIT_COMMITTER_NAME": committer[0],
        "GIT_COMMITTER_EMAIL": committer[1],
    }
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-a", "--allow-empty", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
        env=env,
    )


def _rev(repo: Path, ref: str) -> str:
    done = subprocess.run(
        ["git", "rev-parse", ref], cwd=repo, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


@pytest.fixture
def feature(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository on the branch ``feature``, cut from ``main``; its one test has two asserts."""
    for name in CI_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", HUMAN[1])
    _git(tmp_path, "config", "user.name", HUMAN[0])
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(STRONG, encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feature")
    return tmp_path


def _weaken(repo: Path) -> None:
    (repo / "tests" / "test_x.py").write_text(WEAK, encoding="utf-8")


def _run(repo: Path, *options: str) -> int:
    return sensor.main(["--root", str(repo), "--base", "main", *options])


# Covers F-0003.R4, F-0003.R5 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_base_file_or_a_commit_message_outside_the_locale_encoding_is_still_read(
    feature: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """git speaks UTF-8; decoded with the locale encoding (cp1252 on Windows) a base test file
    holding a byte that encoding lacks (the 0x9D of a cross mark) was dropped from the base
    inventory, so emptying its tests passed, and such a byte in a commit message crashed the
    approval check."""
    marked = STRONG + "# ❌ Í: not in cp1252 as UTF-8 bytes" + chr(10)
    _git(feature, "checkout", "-q", "main")
    (feature / "tests" / "test_x.py").write_text(marked, encoding="utf-8")
    _commit(feature, "base with a cross mark")
    _git(feature, "checkout", "-q", "feature")
    _git(feature, "merge", "-q", "--ff-only", "main")
    (feature / "tests" / "test_x.py").write_text(WEAK, encoding="utf-8")
    _commit(feature, "docs: a message with ❌ and Í in it")
    assert _run(feature) == 1
    assert DROPPED in capsys.readouterr().out

    seen: list[dict[str, object]] = []
    real_run = subprocess.run

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(kwargs)
        return real_run(*args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(sensor.subprocess, "run", spy)
    assert _run(feature) == 1
    assert seen, "the sensor asked git"
    assert all(k.get("encoding") == "utf-8" and k.get("errors") == "replace" for k in seen)


# Covers F-0003.R1, F-0003.R11 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_an_uncommitted_weakening_is_reported_and_the_exit_code_tells(
    feature: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _rev(feature, "main")[:12]
    assert _run(feature) == 0
    assert f"OK: no test weakened relative to {base}.\n" in capsys.readouterr().out
    _weaken(feature)  # not committed: the working tree is what the sensor reads
    assert _run(feature) == 1
    out = capsys.readouterr().out
    assert f"Tests weakened relative to {base}:\n  - {DROPPED}\n" in out
    assert f"a HUMAN approves it by adding {sensor.APPROVAL_MARK}" in out
    with pytest.raises(SystemExit) as usage:
        _run(feature, "--no-such-option")
    assert usage.value.code == 2


# Covers F-0003.R2 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_the_base_is_the_merge_base_with_the_first_candidate_that_resolves(
    feature: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fork = _rev(feature, "main")
    _git(feature, "checkout", "-q", "main")
    later = feature / "tests" / "test_later.py"
    later.write_text("def test_later(x):\n    assert x\n", encoding="utf-8")
    _git(feature, "add", "-A")
    _commit(feature, "test: added on main after the branch was cut")
    _git(feature, "checkout", "-q", "feature")
    _commit(feature, "feat: work")
    head = _rev(feature, "HEAD")
    # the merge base, not the tip of main: the test added there later is not "deleted" here
    assert not later.exists()
    assert sensor.resolve_base("main", feature) == (fork, [])
    assert _run(feature) == 0

    # --base, then RUMBO_BASE_REF, then origin/<GITHUB_BASE_REF>, then the default refs
    _git(feature, "update-ref", "refs/remotes/origin/release", head)
    monkeypatch.setenv("GITHUB_BASE_REF", "release")
    assert sensor.resolve_base(None, feature) == (head, [])
    monkeypatch.setenv("RUMBO_BASE_REF", "main")
    assert sensor.resolve_base(None, feature) == (fork, [])
    assert sensor.resolve_base("HEAD", feature) == (head, [])
    monkeypatch.setenv("RUMBO_BASE_REF", "0" * 40)  # the all-zero SHA is no base
    assert sensor.resolve_base(None, feature) == (head, [])
    monkeypatch.delenv("RUMBO_BASE_REF")
    monkeypatch.delenv("GITHUB_BASE_REF")
    assert sensor.resolve_base(None, feature) == (fork, [])  # `main`, the first default there is

    # a --base that does not resolve is reported and the next candidates are tried
    assert sensor.resolve_base("no-such-ref", feature) == (
        fork,
        ["explicit base 'no-such-ref' is not reachable; trying defaults"],
    )
    # origin/main comes before main; a default that is HEAD itself earns a warning
    _git(feature, "update-ref", "refs/remotes/origin/main", head)
    base, warnings = sensor.resolve_base(None, feature)
    assert base == head
    assert len(warnings) == 1 and "base 'origin/main' is HEAD itself" in warnings[0]


# Covers F-0003.R3, F-0003.R11 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_without_a_base_the_sensor_warns_and_only_strict_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in CI_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    _git(tmp_path, "init", "-q", "-b", "trunk")  # no commit and none of the default refs
    assert sensor.main(["--root", str(tmp_path)]) == 0
    assert "WARNING: no base to compare against" in capsys.readouterr().out
    assert sensor.main(["--root", str(tmp_path), "--strict"]) == 2
    assert sensor.main(["--root", str(tmp_path), "--strict", "--base", "no-such-ref"]) == 2
    out = capsys.readouterr().out
    assert "WARNING: explicit base 'no-such-ref' is not reachable" in out
    assert "OK" not in out


# Covers F-0003.R11, F-0003.R12, F-0003.R13 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_the_mark_approves_in_a_commit_of_the_human_and_not_in_one_an_agent_co_authored(
    feature: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _weaken(feature)
    _commit(feature, "test: weaken")
    assert _run(feature) == 1
    co_authored = "Co-Authored-By: Claude <noreply@anthropic.com>"
    _commit(feature, f"test: drop an assert {sensor.APPROVAL_MARK}\n\n{co_authored}")
    ignored = _rev(feature, "HEAD")[:10]
    capsys.readouterr()
    assert _run(feature) == 1
    out = capsys.readouterr().out
    assert f"(ignoring {sensor.APPROVAL_MARK} in {ignored}: commit authored by an agent)" in out

    _commit(feature, f"test: the second assert checked nothing\n\n{sensor.APPROVAL_MARK}")
    approved = _rev(feature, "HEAD")[:10]
    assert _run(feature) == 0
    out = capsys.readouterr().out
    assert f"  - {DROPPED}\n" in out  # an approved weakening is still listed
    assert f"Approved by a human: commit {approved} by {HUMAN[1]}." in out


# Covers F-0003.R12 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_the_mark_counts_only_in_a_commit_between_the_base_and_head(feature: Path) -> None:
    _commit(feature, f"chore: an earlier approval {sensor.APPROVAL_MARK}")
    _weaken(feature)
    _commit(feature, "test: weaken")
    assert sensor.main(["--root", str(feature), "--base", "HEAD~1"]) == 1
    assert _run(feature) == 0


# Covers F-0003.R12 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_the_pull_request_label_approves_the_change(
    feature: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def labelled(*names: str) -> str:
        return json.dumps({"pull_request": {"labels": [{"name": name} for name in names]}})

    _weaken(feature)
    event = feature / "event.json"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")  # the label counts only there (S12)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert _run(feature) == 1  # the file does not exist
    event.write_text(labelled("bug"), encoding="utf-8")
    assert _run(feature) == 1
    event.write_text("{ not json", encoding="utf-8")
    assert _run(feature) == 1
    event.write_text(json.dumps({"action": "push"}), encoding="utf-8")
    assert _run(feature) == 1
    capsys.readouterr()
    event.write_text(labelled("bug", sensor.APPROVAL_LABEL), encoding="utf-8")
    assert _run(feature) == 0
    out = capsys.readouterr().out
    assert f"  - {DROPPED}\n" in out
    assert "Approved by a human: pull request label 'tests-change-approved'." in out


def _approval(
    repo: Path, author: Identity = HUMAN, committer: Identity = HUMAN, trailer: str = ""
) -> str | None:
    """What the sensor makes of the mark in a new commit with those identities and trailer."""
    _commit(repo, f"change {sensor.APPROVAL_MARK}\n\n{trailer}\n", author, committer)
    return sensor.human_approval("HEAD~1", repo)


def _identity_with(marker: str) -> Identity:
    """A name and an email whose ``Name <email>`` contains the marker."""
    if "@" in marker:
        return "Some Agent", marker
    return f"Some {marker}", "agent@example.com"


# Covers F-0003.R13 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_an_approval_an_agent_took_part_in_is_ignored(feature: Path) -> None:
    for marker in sensor.DEFAULT_AGENT_AUTHORS:
        assert _approval(feature, author=_identity_with(marker)) is None, marker
    for marker in sensor.DEFAULT_AGENT_TRAILERS:
        assert _approval(feature, trailer=f"{marker} something") is None, marker
    agent: Identity = ("Claude", "NoReply@Anthropic.com")  # matched whatever the case
    assert _approval(feature, author=agent) is None
    assert _approval(feature, committer=agent) is None
    assert _approval(feature, trailer=f"co-authored-by: {agent[0]} <{agent[1]}>") is None
    assert _approval(feature, trailer="Co-authored-by: A Bot <a-bot[bot]@example.com>") is None
    assert _approval(feature, trailer="Co-authored-by: Ana <ana@example.com>") is not None
    assert _approval(feature) == f"commit {_rev(feature, 'HEAD')[:10]} by {HUMAN[1]}"


# Covers F-0003.R13 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_the_project_adds_agent_identities_in_pyproject(feature: Path) -> None:
    pyproject = feature / "pyproject.toml"
    robot: Identity = ("Robo Coder", "robo@agents.example")
    assert _approval(feature, author=robot) is not None
    pyproject.write_text(
        '[tool.rumbo]\nagent_authors = ["ROBO@agents.example", "(pair)", 7, " "]\n',
        encoding="utf-8",
    )
    assert sensor.agent_authors(feature) == (
        *sensor.DEFAULT_AGENT_AUTHORS,
        "ROBO@agents.example",
        "(pair)",
    )
    assert _approval(feature, author=robot) is None
    assert _approval(feature, committer=robot) is None
    assert _approval(feature, trailer="Co-authored-by: Bo (Pair) <bo@example.com>") is None
    assert _approval(feature, author=("Claude", "noreply@anthropic.com")) is None
    assert _approval(feature) is not None
    # a value that is not a list adds nothing: the defaults only, never a crash
    pyproject.write_text('[tool.rumbo]\nagent_authors = "robo"\n', encoding="utf-8")
    assert sensor.agent_authors(feature) == sensor.DEFAULT_AGENT_AUTHORS
    assert _approval(feature, author=robot) is not None


# ----------------------------------------- ADR-0027: what counts as a test that runs (S3, S7, S8)
T = "tests/test_calc.py"
CALC = "def test_add(x):\n    assert x == 4\n    assert x > 3\n"
CALC_GONE = f"test deleted or renamed: {T}::test_add"
CALC_CLASS = "class TestCalc:\n    def test_add(self, x):\n        assert x == 4\n"


def _indented(source: str, by: str = "    ") -> str:
    return "".join(f"{by}{line}\n" if line else "\n" for line in source.splitlines())


CALC_CONDITIONAL = f"{T}::test_add: moved under a condition: review"
# where the test goes → (the file, the name it is inventoried under there, what is reported)
NOT_COLLECTED: dict[str, tuple[str, str, str]] = {
    "if TYPE_CHECKING": (
        "import typing\n\nif typing.TYPE_CHECKING:\n" + _indented(CALC),
        "test_add",
        CALC_CONDITIONAL,
    ),
    "try": ("try:\n" + _indented(CALC) + "finally:\n    pass\n", "test_add", CALC_CONDITIONAL),
    "with": ("with open(__file__):\n" + _indented(CALC), "test_add", CALC_CONDITIONAL),
    "a function": ("def never():\n" + _indented(CALC), "never.test_add", CALC_GONE),
    "a class pytest does not collect": (
        "class Checks:\n" + _indented(CALC),
        "Checks.test_add",
        CALC_GONE,
    ),
    "a collected class inside one that is not": (
        "class Checks:\n    class TestCalc:\n" + _indented(CALC, " " * 8),
        "Checks.TestCalc.test_add",
        CALC_GONE,
    ),
}


# Covers F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("where", list(NOT_COLLECTED), ids=list(NOT_COLLECTED))
def test_a_test_moved_where_pytest_does_not_collect_it_is_reported_as_deleted(where: str) -> None:
    """ADR-0027: every `test*` function is inventoried, wherever it is, so that none is lost;
    one that leaves the place where pytest collected it for sure is reported: as deleted when
    its qualified name changes, for review when it goes under a condition."""
    source, name, reported = NOT_COLLECTED[where]
    moved = source + "\n\ndef test_placeholder():\n    pass\n"
    inv = sensor.analyze({T: moved})
    assert sorted(inv.tests) == sorted([f"{T}::{name}", f"{T}::test_placeholder"])
    assert inv.asserts == 2, "its asserts still count in the total of the directory"
    assert _problems({T: CALC}, {T: moved}) == [reported]


COND = dedent(
    """
    import sys

    import pytest

    if sys.platform != "win32":
        def test_posix():
            assert f(1) == 1
            assert f(2) == 2

        @pytest.mark.parametrize("x", [1, 2, 3])
        def test_cases(x):
            assert f(x) == x

        class TestModern:
            def test_m(self):
                assert f(3) == 3
                assert f(4) == 4

    try:
        import numpy as np
    except ImportError:
        np = None
    else:
        def test_np():
            assert np.add(1, 2) == 3
            assert np.add(2, 2) == 4

    with open(__file__):
        def test_w():
            assert f(5) == 5
            assert f(6) == 6

    class TestIO:
        def test_read(self):
            assert f(7) == 7

        if sys.platform != "win32":
            def test_chmod(self):
                assert f(8) == 8
                assert f(9) == 9
    """
)
COND_NAMES = ["TestIO.test_chmod", "TestIO.test_read", "TestModern.test_m"]
COND_NAMES += ["test_cases", "test_np", "test_posix", "test_w"]
# what the change does to a conditional test → (old text, new text, what is reported)
COND_WEAKENED: dict[str, tuple[str, str, str]] = {
    "assert True": (
        "assert f(2) == 2",
        "assert True",
        "test_posix: gained always-true asserts (assert True / tuple)",
    ),
    "an assert removed": (
        "        assert f(2) == 2\n",
        "",
        "test_posix: asserts dropped from 2 to 1",
    ),
    "the test deleted": (
        "    def test_posix():\n        assert f(1) == 1\n        assert f(2) == 2\n\n",
        "",
        "test deleted or renamed",
    ),
    "a skip": (
        "        assert f(1) == 1\n",
        "        pytest.skip('later')\n        assert f(1) == 1\n",
        "test_posix: gained skip/skipif/xfail markers or pytest.skip() calls",
    ),
    "fewer cases": ("[1, 2, 3]", "[1]", "test_cases: parametrize cases dropped from 3 to 1"),
    "in a class under the if": (
        "assert f(4) == 4",
        "assert True",
        "TestModern.test_m: gained always-true asserts (assert True / tuple)",
    ),
    "in the else of a try": (
        "        assert np.add(2, 2) == 4\n",
        "",
        "test_np: asserts dropped from 2 to 1",
    ),
    "under a with": ("        assert f(6) == 6\n", "", "test_w: asserts dropped from 2 to 1"),
    "under an if of the class": (
        "            assert f(9) == 9\n",
        "",
        "TestIO.test_chmod: asserts dropped from 2 to 1",
    ),
}
# what a normal change also brings: asserts added somewhere else, so the total hides nothing
COMPENSATION = {"tests/test_new.py": "def test_n():\n" + "    assert g()\n" * 6}


# Covers F-0003.R4, F-0003.R5, F-0003.R6 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("how", list(COND_WEAKENED), ids=list(COND_WEAKENED))
def test_a_test_under_a_condition_is_inventoried_and_compared_like_any_other(how: str) -> None:
    """pytest runs a test defined under `if sys.platform...` or in the `else` of a `try`
    whenever the condition holds: the sensor had stopped looking at them."""
    old, new, reported = COND_WEAKENED[how]
    inv = sensor.analyze({T: COND})
    assert sorted(inv.tests) == [f"{T}::{name}" for name in COND_NAMES]
    assert all(info.conditional for name, info in inv.tests.items() if "test_read" not in name)
    weakened = COND.replace(old, new)
    assert weakened != COND
    problems = _problems({T: COND}, {T: weakened, **COMPENSATION})
    assert len(problems) == 1 and reported in problems[0], problems
    assert _problems({T: COND}, {T: COND, **COMPENSATION}) == [], "conditional on both sides"


EXTERNAL_BASE = dedent(
    """
    from myproj.testing import BaseTest
    from tests_support import IntegrationSuite


    class LoginTests(BaseTest):
        def test_ok(self):
            self.assertEqual(f(1), 1)
            self.assertEqual(f(2), 2)

        def test_bad(self):
            self.assertEqual(f(0), 0)


    class OrdersSuite(IntegrationSuite, object):
        def test_a(self):
            self.assertEqual(f(1), 1)
            self.assertEqual(f(2), 2)
    """
)


# Covers F-0003.R4, F-0003.R5, F-0003.R6 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_class_whose_base_comes_from_outside_the_tests_is_inventoried() -> None:
    """`class LoginTests(BaseTest)` with `BaseTest` in the package under test: pytest runs it
    (it is a `unittest.TestCase`), whatever its name. An unknown base is assumed to be one."""
    base = {T: EXTERNAL_BASE}
    inv = sensor.analyze(base)
    names = ["LoginTests.test_bad", "LoginTests.test_ok", "OrdersSuite.test_a"]
    assert sorted(inv.tests) == [f"{T}::{name}" for name in names]
    assert all(info.collected for info in inv.tests.values())
    emptied = EXTERNAL_BASE.replace("self.assertEqual(f(2), 2)", "assert True")
    assert _problems(base, {T: emptied, **COMPENSATION}) == [
        f"{T}::LoginTests.test_ok: gained always-true asserts (assert True / tuple)",
        f"{T}::OrdersSuite.test_a: gained always-true asserts (assert True / tuple)",
    ]
    shorter = EXTERNAL_BASE.replace("        self.assertEqual(f(2), 2)\n", "", 1)
    assert _problems(base, {T: shorter, **COMPENSATION}) == [
        f"{T}::LoginTests.test_ok: asserts dropped from 2 to 1"
    ]
    deleted = EXTERNAL_BASE.replace(
        "    def test_bad(self):\n        self.assertEqual(f(0), 0)\n", ""
    )
    assert _problems(base, {T: deleted, **COMPENSATION}) == [
        f"test deleted or renamed: {T}::LoginTests.test_bad"
    ]
    # a base defined in the tests directory is followed: collected when it is, or may be
    local = {
        "tests/support.py": "import unittest\n\n\nclass Base(unittest.TestCase):\n    pass\n\n\n"
        "class Plain:\n    pass\n",
        T: "from tests.support import Base, Plain\n\n\nclass Logins(Base):\n"
        "    def test_a(self):\n        self.assertTrue(f())\n\n\n"
        "class Helpers(Plain):\n    def test_b(self):\n        assert f()\n",
    }
    collected = {name: info.collected for name, info in sensor.analyze(local).tests.items()}
    assert collected == {f"{T}::Logins.test_a": True, f"{T}::Helpers.test_b": False}


UNBOUND: dict[str, tuple[str, str, str]] = {
    "del": (CALC, CALC + "\n\ndel test_add\n", "test removed with del: test_add"),
    "del under an if": (
        CALC,
        CALC + "\n\nif True:\n    del test_add\n",
        "test removed with del: test_add",
    ),
    "assignment": (CALC, CALC + "\n\ntest_add = None\n", "test name bound again: test_add"),
    "import": (
        CALC,
        CALC + "\n\nfrom os import getcwd as test_add\n",
        "test name bound again: test_add",
    ),
    "definition under an if": (
        CALC,
        CALC + "\n\nif True:\n" + _indented(CALC),
        "test name bound again: test_add",
    ),
    "__test__ in the module": (
        CALC,
        "__test__ = False\n\n" + CALC,
        "__test__ set to a false value",
    ),
    "__test__ in the class": (
        CALC_CLASS,
        CALC_CLASS.replace("class TestCalc:\n", "class TestCalc:\n    __test__ = False\n\n"),
        "__test__ set to a false value",
    ),
    "__test__ from outside": (
        CALC_CLASS,
        CALC_CLASS + "\n\nTestCalc.__test__ = False\n",
        "__test__ set to a false value",
    ),
    "del in the class": (
        CALC_CLASS,
        CALC_CLASS + "\n    del test_add\n",
        "test removed with del: TestCalc.test_add",
    ),
    "del of the class": (
        CALC_CLASS,
        CALC_CLASS + "\n\ndel TestCalc\n",
        "test removed with del: TestCalc",
    ),
    "del of the method from outside": (
        CALC_CLASS,
        CALC_CLASS + "\n\ndel TestCalc.test_add\n",
        "test removed with del: TestCalc.test_add",
    ),
    "method assigned from outside": (
        CALC_CLASS,
        CALC_CLASS + "\n\nTestCalc.test_add = print\n",
        "test name bound again: TestCalc.test_add",
    ),
}


# Covers F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("how", list(UNBOUND), ids=list(UNBOUND))
def test_unbinding_a_test_after_its_definition_is_reported(how: str) -> None:
    base, current, flag = UNBOUND[how]
    assert _problems({T: base}, {T: current}) == [f"{T}: {flag}"]
    assert _problems({T: current}, {T: current}) == [], "only what the change adds is reported"
    assert _problems({}, {T: current}) == [], "a new file weakens no test of the base"


# Covers F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_dunder_test_and_del_are_reported_only_where_the_base_had_tests() -> None:
    """`__test__ = False` on a helper class whose name starts with Test is how pytest is told
    it is not a test class: nothing of the base stops running."""
    factory = (
        "\n\nclass TestClientFactory:\n    __test__ = False\n\n"
        "    def build(self):\n        return 1\n"
    )
    assert _problems({T: CALC}, {T: CALC + factory}) == []
    outside = CALC + "\n\nclass Thing:\n    pass\n\n\nThing.__test__ = False\n"
    assert _problems({T: CALC}, {T: outside}) == []
    helpers = "def build():\n    return 1\n"
    module = {T: CALC, "tests/helpers.py": helpers}
    assert _problems(module, {**module, "tests/helpers.py": "__test__ = False\n" + helpers}) == []
    late = CALC + "\n\ndef test_new(x):\n    assert x\n\n\ndel test_new\n"
    assert _problems({T: CALC}, {T: late}) == [], "a test the change adds and removes itself"
    tidy = CALC + "\n\nvalue = [1]\ndel value, value[0]\n"
    assert _problems({T: CALC}, {T: tidy}) == [], "a del of something that is not a test"


DECORATOR_PRELUDE = dedent(
    """
    import functools
    from os.path import *
    from unittest import mock
    from unittest.mock import patch

    import helpers
    import pytest
    from hypothesis import given, settings
    from tenacity import retry

    from conftest import stable


    def local(fn):
        return lambda: None


    classmethod = local
    gag = lambda fn: None
    slow = pytest.mark.slow
    cases = pytest.mark.parametrize("y", [1, 2])
    retry_thrice = retry(3)
    first, second = retry, retry
    registry = {"slow": pytest.mark.slow}


    """
)
LOCAL = " [defined in the tests]"
REVIEWED_DECORATORS: dict[str, str] = {
    "@stable": "stable" + LOCAL,
    "@local": "local" + LOCAL,
    "@classmethod": "classmethod" + LOCAL,
    "@gag": "gag" + LOCAL,
    "@local(1)(2)": "local" + LOCAL,
    "@helpers.with_db": "helpers.with_db" + LOCAL,
    "@(lambda fn: None)": "<lambda>" + LOCAL,
}
KNOWN_DECORATORS = [
    "@pytest.mark.slow",
    "@pytest.mark.usefixtures('db')",
    "@pytest.mark.parametrize('y', [1, 2])",
    "@pytest.fixture",
    "@mock.patch('os.getcwd')",
    "@patch('os.getcwd')",
    "@patch.object(functools, 'wraps')",
    "@patch.dict('os.environ', {})",
    "@given(x=...)",
    "@settings(max_examples=5)",
    "@functools.wraps(print)",
    "@staticmethod",
    # whatever comes from outside the tests directory: the author of the change did not write it
    "@retry(3)",
    "@unknown.deco",
    "@functools.lru_cache",
    # a name of the module that stands for one of those
    "@slow",
    "@cases",
    "@retry_thrice",
    # what cannot be followed is not taken for something written here: a name unpacked from a
    # tuple, an item of a table, a name that a star import brings
    "@first",
    "@registry['slow']",
    "@abspath",
]


# Covers F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("decorator", list(REVIEWED_DECORATORS))
def test_a_decorator_added_to_an_existing_test_is_reported_for_review(decorator: str) -> None:
    """A decorator can replace the test with anything: one written in the tests directory, which
    is what the author of the change could have written to do it, is reported."""
    conftest = "def stable(fn):\n    return lambda: None\n"
    helper = "def with_db(fn):\n    return fn\n"
    base = {T: DECORATOR_PRELUDE + CALC, "tests/conftest.py": conftest, "tests/helpers.py": helper}
    current = {**base, T: DECORATOR_PRELUDE + f"{decorator}\n" + CALC}
    name = REVIEWED_DECORATORS[decorator]
    assert _problems(base, current) == [f"{T}::test_add: decorator added: review (@{name})"]
    assert _problems(current, base) == [], "removing it is not a weakening"
    assert _problems(current, current) == []


# Covers F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("decorator", KNOWN_DECORATORS)
def test_a_known_decorator_added_to_an_existing_test_is_accepted(decorator: str) -> None:
    base = {T: DECORATOR_PRELUDE.replace("classmethod = local\n", "") + CALC}
    current = {T: base[T].replace(CALC, f"{decorator}\n{CALC}")}
    assert current != base
    assert _problems(base, current) == []


# Covers F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_an_import_is_not_taken_for_a_directory_of_the_tests_with_the_same_name() -> None:
    """`tests/hypothesis/` holds the property tests: `from hypothesis import given` there is
    still the library, because that directory does not define `given`."""
    path = "tests/hypothesis/test_p.py"
    old = "def test_p(x=1):\n    assert x\n"
    new = "from hypothesis import given, strategies as st\n\n\n@given(st.integers())\n" + old
    package = {"tests/hypothesis/__init__.py": ""}
    assert _problems({**package, path: old}, {**package, path: new}) == []
    shadow = {"tests/hypothesis/__init__.py": "def given(*a):\n    return lambda fn: None\n"}
    assert _problems({**shadow, path: old}, {**shadow, path: new}) == [
        f"{path}::test_p: decorator added: review (@given{LOCAL})"
    ]


# Covers F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_decorator_added_to_an_existing_test_class_is_reported_and_a_new_test_is_free() -> None:
    base = {T: DECORATOR_PRELUDE + CALC_CLASS}
    current = {T: DECORATOR_PRELUDE + "@local\n" + CALC_CLASS}
    assert _problems(base, current) == [
        f"{T}::TestCalc: decorator added: review (@local [defined in the tests])"
    ]
    added = {T: base[T] + "\n\n@local\ndef test_new(x):\n    assert x\n"}
    assert _problems(base, added) == []


CAMEL = dedent(
    """
    import unittest


    class CalcTest(unittest.TestCase):
        def testAdd(self):
            self.assertEqual(2 + 2, 4)
            self.assertEqual(1 + 2, 3)

        def testName(self):
            self.assertTrue(callable(len))
    """
)


# Covers F-0003.R4, F-0003.R7 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_the_prefix_of_a_test_is_test_as_in_pytest_with_or_without_the_underscore() -> None:
    inv = sensor.analyze({T: CAMEL})
    assert inv.tests[f"{T}::CalcTest.testAdd"].asserts == 2
    assert inv.tests[f"{T}::CalcTest.testName"].asserts == 1
    skipped = CAMEL.replace("    def testAdd", "    @unittest.skip('later')\n    def testAdd")
    assert _problems({T: CAMEL}, {T: skipped}) == [
        f"{T}::CalcTest.testAdd: gained skip/skipif/xfail markers or pytest.skip() calls"
    ]
    offset = CAMEL.replace(
        "    def testAdd(self):\n        self.assertEqual(2 + 2, 4)\n"
        "        self.assertEqual(1 + 2, 3)\n\n",
        "",
    ) + ("        self.assertIsNotNone(len)\n        self.assertTrue(len)\n")
    assert sensor.analyze({T: offset}).asserts == 3, "the total hides nothing here"
    assert _problems({T: CAMEL}, {T: offset}) == [f"test deleted or renamed: {T}::CalcTest.testAdd"]
    # a unittest class is collected whatever its name; without its base it no longer is
    plain = CAMEL.replace("(unittest.TestCase)", "")
    assert [info.collected for info in sensor.analyze({T: plain}).tests.values()] == [False, False]
    assert _problems({T: CAMEL}, {T: plain}) == [
        f"{T}::CalcTest.testAdd: no longer where pytest collects it: review",
        f"{T}::CalcTest.testName: no longer where pytest collects it: review",
    ]


CONTRACT = dedent(
    """
    class Contract:
        impl = None

        def test_add(self):
            assert type(self).impl(2, 2) == 4
            assert type(self).impl(1, 2) == 3
    """
)
BUILTIN = "\n\nclass TestBuiltin(Contract):\n    impl = staticmethod(lambda a, b: a + b)\n"
SUBCLASS = "\n\nclass TestCalc(Contract):\n    impl = staticmethod(max)\n"
INHERITED_GONE = f"test deleted or renamed: {T}::TestCalc.test_add"


# Covers F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_tests_inherited_from_a_base_class_are_inventoried_for_each_collected_class() -> None:
    base = {T: CONTRACT + BUILTIN + SUBCLASS}
    inv = sensor.analyze(base)
    inherited = [f"{T}::TestBuiltin.test_add", f"{T}::TestCalc.test_add"]
    assert sorted(inv.tests) == [f"{T}::Contract.test_add", *inherited]
    assert inv.tests[f"{T}::TestCalc.test_add"].asserts == 2
    assert inv.asserts == 2, "a test counts in the total once, where it is written"
    # the class that inherited the tests is deleted, or stops inheriting them
    assert _problems(base, {T: CONTRACT + BUILTIN}) == [INHERITED_GONE]
    orphan = {T: CONTRACT + BUILTIN + SUBCLASS.replace("(Contract)", "")}
    assert INHERITED_GONE in _problems(base, orphan)
    shadowed = {T: CONTRACT + BUILTIN + SUBCLASS + "    test_add = None\n"}
    assert INHERITED_GONE in _problems(base, shadowed)
    # a test of the class itself, also one under a condition, is the one that counts
    own = SUBCLASS + "    if impl:\n        def test_add(self):\n            assert self.impl\n"
    mine = sensor.analyze({T: CONTRACT + BUILTIN + own}).tests[f"{T}::TestCalc.test_add"]
    assert (mine.asserts, mine.conditional) == (1, True)
    # the base class lives in another module of the tests directory
    split = {
        "tests/contract.py": CONTRACT,
        T: "from tests.contract import Contract\n" + BUILTIN + SUBCLASS,
    }
    assert sorted(sensor.analyze(split).tests) == [
        "tests/contract.py::Contract.test_add",
        *inherited,
    ]
    emptied = {**split, "tests/contract.py": "class Contract:\n    impl = None\n"}
    assert INHERITED_GONE in _problems(split, emptied)


# ------------------------------------------------- ADR-0027: skips wherever they are (S4)
SKIP_BASE = dedent(
    """
    import unittest
    from unittest import SkipTest

    import pytest
    import pytest as pt
    from pytest import skip, xfail as known


    def test_one(self, x):
        assert x == 1
    """
)
SKIPS_IN_A_TEST = [
    "skip('later')",
    "known('bug')",
    "pt.skip('later')",
    "pytest.importorskip('numpy')",
    "self.skipTest('later')",
    "raise unittest.SkipTest('later')",
    "raise SkipTest",
    "raise pytest.skip.Exception('later')",
]
SKIP_DECORATORS = [
    "@unittest.skipIf(True, 'later')",
    "@unittest.skipUnless(False, 'later')",
    "@unittest.expectedFailure",
    "@pytest.mark.parametrize('x', [pytest.param(1, marks=pytest.mark.skip)])",
    "@pytest.mark.parametrize('x', [pytest.param(1, marks=[pytest.mark.xfail(strict=False)])])",
]
SKIPS_OUTSIDE_THE_TESTS = {
    "module-level skip": "pytest.skip('wip', allow_module_level=True)\n",
    "module-level importorskip": "numpy = pytest.importorskip('numpy')\n",
    "module-level SkipTest": "raise unittest.SkipTest('wip')\n",
    "pytestmark": "pytestmark = pytest.mark.skip(reason='wip')\n",
    "pytestmark list": "pytestmark = [pytest.mark.usefixtures('db'), pytest.mark.xfail]\n",
    "fixture": "@pytest.fixture(autouse=True)\ndef _database():\n    pytest.skip('no database')\n",
    "imported skip in an autouse fixture": (
        "@pytest.fixture(scope='module', autouse=True)\ndef database():\n    skip('no database')\n"
    ),
    "setup_module": "def setup_module(module):\n    raise unittest.SkipTest('no database')\n",
    "setup_function": "def setup_function(function):\n    pytest.skip('no database')\n",
}
OUTSIDE = "tests/test_a.py: skip/xfail forms outside the tests grew from 0 to 1"
# What skips only the tests that ask for it: an existing test has to change to use it, and
# that change is judged where it happens.
SKIPS_ON_REQUEST = {
    "a fixture a test has to request": (
        "@pytest.fixture\ndef database():\n    skip('no database')\n"
    ),
    "a marker kept in a name": "needs_docker = pytest.mark.skipif(not DOCKER, reason='docker')\n",
    "a helper function": "def need_docker():\n    if not DOCKER:\n        pytest.skip('docker')\n",
    "a new class with its set-up": (
        "class Base(unittest.TestCase):\n    def setUp(self):\n        self.skipTest('x')\n"
    ),
    "a new class under a skip": "@pytest.mark.skip\nclass TestLater:\n    pass\n",
    "a new unittest class under a skip": (
        "@unittest.skip('later')\nclass Later(unittest.TestCase):\n    pass\n"
    ),
    "a new class with its own pytestmark": (
        "class TestLater:\n    pytestmark = pytest.mark.skip\n\n"
        "    def check(self):\n        return 1\n"
    ),
    "a fixture that says it is not autouse": (
        "@pytest.fixture(scope='module', autouse=False)\ndef database():\n    skip('no database')\n"
    ),
    "a skip that a lambda of the module would make": "later = lambda: pytest.skip('later')\n",
}
SKIP_CLASS = dedent(
    """
    import unittest

    import pytest


    class TestOrders(unittest.TestCase):
        def test_one(self):
            self.assertTrue(f())
    """
)
SKIPPED_CLASS: dict[str, tuple[str, str]] = {
    "a decorator": ("class TestOrders", "@pytest.mark.skip(reason='later')\nclass TestOrders"),
    "a unittest decorator": ("class TestOrders", "@unittest.skip('later')\nclass TestOrders"),
    "pytestmark": (
        "    def test_one",
        "    pytestmark = [pytest.mark.xfail]\n\n    def test_one",
    ),
    "setUp": (
        "    def test_one",
        "    def setUp(self):\n        self.skipTest('later')\n\n    def test_one",
    ),
    "setup_method": (
        "    def test_one",
        "    def setup_method(self, method):\n        pytest.skip('later')\n\n    def test_one",
    ),
}


# Covers F-0003.R7 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("line", SKIPS_IN_A_TEST)
def test_every_way_of_skipping_from_inside_a_test_is_counted(line: str) -> None:
    base = {"tests/test_a.py": SKIP_BASE}
    current = {"tests/test_a.py": SKIP_BASE.replace("    assert", f"    {line}\n    assert")}
    assert current != base
    assert _problems(base, current) == [SKIPPED]


# Covers F-0003.R7 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("decorator", SKIP_DECORATORS)
def test_unittest_skips_and_skipped_parameters_are_counted(decorator: str) -> None:
    base = {"tests/test_a.py": SKIP_BASE}
    current = {"tests/test_a.py": SKIP_BASE.replace("def test_one", f"{decorator}\ndef test_one")}
    assert current != base
    assert _problems(base, current) == [SKIPPED]


# Covers F-0003.R7, F-0003.R8 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_mark_kept_in_a_name_of_the_tests_counts_as_the_mark_it_stands_for() -> None:
    """`needs_docker = pytest.mark.skipif(...)` is how suites name their marks: the name is
    no decorator to review, and it is no way around the count of skips either."""
    markers = "import pytest\n\nlater = pytest.mark.skip(reason='later')\n"
    names = "wip = pytest.mark.xfail\ncases = pytest.mark.parametrize('x', [1, 2, 3])\n"
    source = "import pytest\nfrom tests.markers import later\n\n" + names + "\n\n{mark}" + PLAIN
    files = {"tests/markers.py": markers, "tests/test_a.py": source.format(mark="@cases\n")}
    assert sensor.analyze(files).tests["tests/test_a.py::test_one"].param_cases == 3
    for mark in ("@later\n", "@wip\n"):
        marked = {**files, "tests/test_a.py": source.format(mark=mark + "@cases\n")}
        assert _problems(files, marked) == [SKIPPED], mark
    fewer = {**files, "tests/test_a.py": files["tests/test_a.py"].replace("[1, 2, 3]", "[1]")}
    assert _problems(files, fewer) == [
        "tests/test_a.py::test_one: parametrize cases dropped from 3 to 1"
    ]


FIXTURE_CONFTEST = "import pytest\n\n\n@pytest.fixture\ndef db():\n    return connect()\n"
FIXTURE_TESTS = dedent(
    """
    import pytest


    def test_one(db):
        assert db.ping()


    class TestOrders:
        def test_two(self):
            assert f()
    """
)
SKIPPING_DB = "@pytest.fixture\ndef db():\n    pytest.skip('no database')\n"
GATE = "@pytest.fixture\ndef _gate():\n    pytest.skip('later')\n"
TWO = (
    "tests/test_a.py::TestOrders.test_two: gained skip/skipif/xfail markers or pytest.skip() calls"
)
# how a fixture that skips reaches a test of the base → (conftest.py, test file, who is skipped)
FIXTURE_SKIPS: dict[str, tuple[str, str, list[str]]] = {
    "the fixture it requests starts to skip": (
        FIXTURE_CONFTEST.replace("    return", "    pytest.skip('no database')\n    return"),
        FIXTURE_TESTS,
        [SKIPPED],
    ),
    "a fixture of the same name in the file": (
        FIXTURE_CONFTEST,
        FIXTURE_TESTS + "\n\n" + SKIPPING_DB,
        [SKIPPED],
    ),
    "through another fixture": (
        FIXTURE_CONFTEST.replace("def db():", "def db(_gate):") + "\n\n" + GATE,
        FIXTURE_TESTS,
        [SKIPPED],
    ),
    "usefixtures on the test": (
        FIXTURE_CONFTEST + "\n\n" + GATE,
        FIXTURE_TESTS.replace(
            "    def test_two", "    @pytest.mark.usefixtures('_gate')\n    def test_two"
        ),
        [TWO],
    ),
    "usefixtures on the class": (
        FIXTURE_CONFTEST + "\n\n" + GATE,
        FIXTURE_TESTS.replace(
            "class TestOrders", "@pytest.mark.usefixtures('_gate')\nclass TestOrders"
        ),
        [TWO],
    ),
    "pytestmark of the module": (
        FIXTURE_CONFTEST,
        FIXTURE_TESTS + "\n\npytestmark = pytest.mark.usefixtures('_gate')\n\n\n" + GATE,
        [TWO, SKIPPED],
    ),
    "a fixture of the class": (
        FIXTURE_CONFTEST,
        FIXTURE_TESTS.replace(
            "    def test_two",
            _indented(GATE).replace("_gate():", "_gate(self):")
            + "\n    @pytest.mark.usefixtures('_gate')\n    def test_two",
        ),
        [TWO],
    ),
}


# Covers F-0003.R7 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("how", list(FIXTURE_SKIPS), ids=list(FIXTURE_SKIPS))
def test_a_fixture_that_skips_is_counted_for_the_tests_that_use_it(how: str) -> None:
    """A fixture skips the tests that request it, by parameter or by `usefixtures`: the skip is
    theirs. So a new fixture for a new test costs nothing, and making an existing fixture skip,
    or shadowing it with one that does, is reported where it bites."""
    conftest, tests, reported = FIXTURE_SKIPS[how]
    base = {"tests/conftest.py": FIXTURE_CONFTEST, "tests/test_a.py": FIXTURE_TESTS}
    current = {"tests/conftest.py": conftest, "tests/test_a.py": tests}
    assert current != base
    assert _problems(base, current) == reported
    assert _problems(current, current) == []
    fresh = "\n\n@pytest.fixture\ndef redis():\n    pytest.skip('no redis')\n\n\n"
    added = {
        **base,
        "tests/test_a.py": FIXTURE_TESTS + fresh + "def test_r(redis):\n    assert redis\n",
    }
    assert _problems(base, added) == [], "a new fixture for a new test"


# Covers F-0003.R7 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_hook_in_a_plugin_module_of_the_tests_is_reported_like_one_in_a_conftest() -> None:
    """`pytest_plugins = ["plugin"]` loads `tests/plugin.py`: its hooks are conftest hooks."""
    hook = "def pytest_collection_modifyitems(items):\n    del items[:]\n"
    base = {T: CALC}
    plugin = {"tests/conftest.py": "pytest_plugins = ['plugin']\n", "tests/plugin.py": hook}
    assert _problems(base, {**base, **plugin}) == [
        "tests/plugin.py: pytest hook added: review (pytest_collection_modifyitems)"
    ]
    assert _problems({**base, **plugin}, {**base, **plugin}) == []
    setup = {"tests/plugin.py": "def pytest_configure(config):\n    config.x = 1\n"}
    assert _problems(base, {**base, **setup}) == []
    assert _problems({"README.md": None}, {"README.md": None, **base, **plugin}) == []


# Covers F-0003.R7 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("form", list(SKIPS_OUTSIDE_THE_TESTS), ids=list(SKIPS_OUTSIDE_THE_TESTS))
def test_a_skip_outside_the_tests_is_counted_for_its_file(form: str) -> None:
    base = {"tests/test_a.py": SKIP_BASE}
    current = {"tests/test_a.py": SKIP_BASE + "\n\n" + SKIPS_OUTSIDE_THE_TESTS[form]}
    assert _problems(base, current) == [OUTSIDE]
    assert _problems(current, current) == []
    assert _problems(current, base) == [], "removing a skip is not a weakening"


# Covers F-0003.R7 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("form", list(SKIPS_ON_REQUEST), ids=list(SKIPS_ON_REQUEST))
def test_a_skip_that_no_existing_test_meets_without_changing_is_not_reported(form: str) -> None:
    """The file-level count is for what skips the tests of the base without touching them."""
    base = {"tests/test_a.py": SKIP_BASE}
    current = {"tests/test_a.py": SKIP_BASE + "\n\n" + SKIPS_ON_REQUEST[form]}
    assert _problems(base, current) == []
    assert _problems(current, base) == []


# Covers F-0003.R7 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("how", list(SKIPPED_CLASS), ids=list(SKIPPED_CLASS))
def test_a_skip_added_to_an_existing_test_class_is_reported_for_the_class(how: str) -> None:
    old, new = SKIPPED_CLASS[how]
    base = {"tests/test_a.py": SKIP_CLASS}
    current = {"tests/test_a.py": SKIP_CLASS.replace(old, new)}
    assert current != base
    assert _problems(base, current) == [
        "tests/test_a.py::TestOrders: gained skip/skipif/xfail markers or pytest.skip() calls"
    ]
    assert _problems(current, current) == []
    assert _problems(current, base) == []


# Covers F-0003.R7 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_new_test_or_a_new_file_may_carry_its_own_skips() -> None:
    base = {"tests/test_a.py": SKIP_BASE}
    new_test = "\n\n@pytest.mark.skipif(True, reason='windows')\ndef test_new(x):\n    assert x\n"
    assert _problems(base, {"tests/test_a.py": SKIP_BASE + new_test}) == []
    new_file = (
        "import pytest\n\nnumpy = pytest.importorskip('numpy')\n\n\n"
        "def test_n():\n    assert numpy\n"
    )
    assert _problems(base, {**base, "tests/test_numpy.py": new_file}) == []
    assert _problems(base, {**base, "tests/helpers.py": "import pytest\npytest.skip('x')\n"}) == []
    # a skip moved from the test into a helper the test calls is the same skip, not a new one
    inline = SKIP_BASE.replace("    assert", "    pytest.skip('no git')\n    assert")
    helper = "\n\ndef need_git():\n    pytest.skip('no git')\n"
    extracted = SKIP_BASE.replace("    assert", "    need_git()\n    assert") + helper
    assert _problems({"tests/test_a.py": inline}, {"tests/test_a.py": extracted}) == []
    assert _problems(base, {"tests/test_a.py": extracted}) == [SKIPPED]
    # but the `__init__.py` of a package is imported before every test of the package
    package = "import pytest\n\npytest.skip('wip', allow_module_level=True)\n"
    assert _problems(base, {**base, "tests/__init__.py": package}) == [
        "tests/__init__.py: skip/xfail forms outside the tests grew from 0 to 1"
    ]
    # and `__test__` is also the name of the doctest table of a module
    doctests = SKIP_BASE + "\n\n__test__ = {'extra': 'a doctest'}\n__test__: bool = True\n"
    assert _problems(base, {"tests/test_a.py": doctests}) == []


CONFTEST_HOOKS: dict[str, tuple[str, list[str]]] = {
    "autouse fixture that skips": (
        "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef _staging_database():\n"
        "    pytest.skip('needs the staging database')\n",
        ["skip/xfail forms outside the tests grew from 0 to 1"],
    ),
    "pytest_pyfunc_call": (
        "def pytest_pyfunc_call(pyfuncitem):\n    return True\n",
        ["conftest hook added: review (pytest_pyfunc_call)"],
    ),
    "pytest_collection_modifyitems": (
        "import pytest\n\n\ndef pytest_collection_modifyitems(items):\n    for item in items:\n"
        "        item.add_marker(pytest.mark.skip(reason='flaky'))\n",
        ["conftest hook added: review (pytest_collection_modifyitems)"],
    ),
    "pytest_runtest_setup": (
        "import pytest\n\n\ndef pytest_runtest_setup(item):\n    pytest.skip('later')\n",
        ["conftest hook added: review (pytest_runtest_setup)"],
    ),
    "pytest_runtest_makereport": (
        "def pytest_runtest_makereport(item, call):\n    return None\n",
        ["conftest hook added: review (pytest_runtest_makereport)"],
    ),
    "pytest_ignore_collect": (
        "def pytest_ignore_collect(collection_path, config):\n    return True\n",
        ["conftest hook added: review (pytest_ignore_collect)"],
    ),
    "collect_ignore": (
        "collect_ignore = ['test_calc.py']\n",
        ["conftest hook added: review (collect_ignore)"],
    ),
    "collect_ignore_glob": (
        "collect_ignore_glob = ['test_*.py']\n",
        ["conftest hook added: review (collect_ignore_glob)"],
    ),
    # what configures the run without choosing which tests run or how they end
    "pytest_plugins": ("pytest_plugins = ['pytester']\n", []),
    "pytest_configure": (
        "def pytest_configure(config):\n    config.addinivalue_line('markers', 'slow: slow')\n",
        [],
    ),
    "pytest_addoption": ("def pytest_addoption(parser):\n    parser.addoption('--db-url')\n", []),
    "pytest_report_header": ("def pytest_report_header(config):\n    return 'x'\n", []),
    "autouse fixture that does not skip": (
        "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef _env(monkeypatch):\n"
        "    monkeypatch.setenv('TZ', 'UTC')\n",
        [],
    ),
}


# Covers F-0003.R7 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("hook", list(CONFTEST_HOOKS), ids=list(CONFTEST_HOOKS))
@pytest.mark.parametrize("conftest", ["tests/conftest.py", "tests/unit/conftest.py", "conftest.py"])
def test_a_hook_added_to_a_conftest_is_reported_for_review(hook: str, conftest: str) -> None:
    """Only the hooks that change which tests run or how they end, and only in a conftest.py
    that governs a test file the base already had."""
    source, flags = CONFTEST_HOOKS[hook]
    base = {T: CALC, "tests/unit/test_u.py": CALC}
    expected = [f"{conftest}: {flag}" for flag in flags]
    assert _problems(base, {**base, conftest: source}) == expected, "a new conftest.py"
    harmless = "import pytest\n\n\n@pytest.fixture\ndef database():\n    return {}\n"
    assert (
        _problems({**base, conftest: harmless}, {**base, conftest: harmless + source}) == expected
    )
    assert _problems(base, {**base, conftest: harmless}) == []
    assert _problems({**base, conftest: source}, {**base, conftest: source}) == []
    # in a directory the change creates, or with the first tests of a project, it governs
    # nothing that could be weakened
    fresh = {"tests/integration/conftest.py": source, "tests/integration/test_i.py": CALC}
    assert _problems(base, {**base, **fresh}) == []
    first = {"README.md": None}
    assert _problems(first, {**first, T: CALC, "tests/conftest.py": source}) == []


# Covers F-0003.R8 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_parametrize_list_of_empty_starred_literals_has_no_case() -> None:
    def cases(values: str) -> int | None:
        source = f"import pytest\n\n@pytest.mark.parametrize('x', {values})\n{CALC}"
        return sensor.analyze({T: source}).tests[f"{T}::test_add"].param_cases

    assert cases("[(1,)]") == 1
    assert cases("[*()]") == 0
    assert cases("[*[], *()]") == 0
    assert cases("[*[1, 2], 3]") == 3
    assert cases("[*CASES]") == 1, "an unknown starred value is one case, not zero"
    assert cases("CASES") is None
    before = f"import pytest\n\n@pytest.mark.parametrize('x', [(4,)])\n{CALC}"
    after = before.replace("[(4,)]", "[*()]")
    assert _problems({T: before}, {T: after}) == [
        f"{T}::test_add: parametrize cases dropped from 1 to 0"
    ]


# ------------------------------- ADR-0027: asserts that cannot run or cannot fail (S6, S10)
TWO_ASSERTS = "import contextlib\nfrom contextlib import suppress\n\nimport pytest\n\n\n{head}"
PLAIN = "def test_one(x):\n    assert x == 1\n    assert x == 2\n"
HELD = "        assert x == 1\n        assert x == 2\n"
TRIED = "def test_one(x):\n    try:\n" + HELD
LOOPED = "def test_one(x):\n    for _ in range(3):\n        try:\n" + _indented(HELD)
MUTED: dict[str, str] = {
    "except AssertionError": TRIED + "    except AssertionError:\n        pass\n",
    "except Exception": TRIED + "    except Exception as exc:\n        '''Ignored.'''\n",
    "except BaseException": TRIED + "    except BaseException:\n        ...\n",
    "except a tuple": TRIED + "    except (KeyError, AssertionError):\n        pass\n",
    "bare except": TRIED + "    except:\n        pass\n",
    "a loop that goes on": LOOPED + "        except AssertionError:\n            continue\n",
    "contextlib.suppress": (
        f"def test_one(x):\n    with contextlib.suppress(AssertionError):\n{HELD}"
    ),
    "suppress": f"def test_one(x):\n    with suppress(KeyError, Exception):\n{HELD}",
    "a test that yields": PLAIN + "    yield\n",
}
STILL_COUNTED: dict[str, tuple[str, int]] = {
    "the handler raises again": (
        TRIED + "    except AssertionError:\n        print(x)\n        raise\n",
        2,
    ),
    # a handler that does something with the failure is a decision of the test, not a gag
    "the handler reports": (TRIED + "    except Exception as exc:\n        print(exc)\n", 2),
    "the handler records": (
        "def test_one(x):\n    errors = []\n    try:\n"
        + HELD
        + "    except AssertionError as exc:\n        errors.append(exc)\n    assert not errors\n",
        3,
    ),
    "a retry that sleeps": (
        LOOPED
        + "            break\n        except AssertionError:\n            time.sleep(0.1)\n"
        + "    else:\n        raise AssertionError('timeout')\n",
        2,
    ),
    "a loop that goes on to a final assert": (
        LOOPED + "        except AssertionError:\n            continue\n    assert x\n",
        3,
    ),
    "a handler that defines something": (
        TRIED + "    except AssertionError:\n        def later():\n            raise\n",
        2,
    ),
    "the handler fails the test": (
        TRIED + "    except Exception:\n        pytest.fail('no')\n",
        3,
    ),
    "another exception": (TRIED + "    except ValueError:\n        pass\n", 2),
    "the else of a try": (
        "def test_one(x):\n    try:\n        x = 1\n    except Exception:\n        pass\n"
        f"    else:\n{HELD}",
        2,
    ),
    "suppress of another exception": (
        f"def test_one(x):\n    with contextlib.suppress(KeyError):\n{HELD}",
        2,
    ),
    "suppress of nothing": (f"def test_one(x):\n    with suppress():\n{HELD}", 2),
}


# Covers F-0003.R5, F-0003.R9 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("how", list(MUTED), ids=list(MUTED))
def test_an_assert_that_cannot_fail_counts_zero(how: str) -> None:
    base = {"tests/test_a.py": TWO_ASSERTS.format(head=PLAIN)}
    current = {"tests/test_a.py": TWO_ASSERTS.format(head=MUTED[how])}
    assert _asserts(current) == 0
    assert sensor.analyze(current).asserts == 2, "what is written still counts in the total"
    assert _problems(base, current) == ["tests/test_a.py::test_one: asserts dropped from 2 to 0"]
    # and taking them out afterwards is reported too, as it was before these rules existed
    assert _problems(current, {"tests/test_a.py": "def test_one(x):\n    f(x)\n"}) == [
        "tests/test_a.py::test_one: asserts dropped from 2 to 0, counting those that cannot fail",
        "total asserts in the tests directory dropped from 2 to 0",
    ]


# Covers F-0003.R9 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("how", list(STILL_COUNTED), ids=list(STILL_COUNTED))
def test_an_assert_that_can_still_fail_keeps_counting(how: str) -> None:
    source, expected = STILL_COUNTED[how]
    base = {"tests/test_a.py": TWO_ASSERTS.format(head=PLAIN)}
    current = {"tests/test_a.py": TWO_ASSERTS.format(head=source)}
    assert _asserts(current) == expected
    assert _problems(base, current) == []


CHECKED = "import asyncio\nfrom contextlib import contextmanager\n\n\n{helper}\n\n{test}"
CHECK = "def check(x):\n    assert x\n"
TWICE = "def test_one():\n    check(1)\n    check(2)\n"
NEVER_RUN: dict[str, tuple[str, str]] = {
    "a helper turned generator": (CHECK + "    yield\n", TWICE),
    "a helper turned coroutine and never awaited": ("async " + CHECK, TWICE),
    "a coroutine helper in an async test, not awaited": ("async " + CHECK, "async " + TWICE),
    "a helper under a swallowing try": (
        CHECK,
        "def test_one():\n    try:\n        check(1)\n        check(2)\n"
        "    except Exception:\n        pass\n",
    ),
}
STILL_RUN: dict[str, tuple[str, str]] = {
    "awaited": (
        "async " + CHECK,
        "async def test_one():\n    await check(1)\n    await check(2)\n",
    ),
    "gathered": (
        "async " + CHECK,
        "async def test_one():\n    await asyncio.gather(check(1), check(2))\n",
    ),
    "asyncio.run": (
        "async " + CHECK,
        "def test_one():\n    asyncio.run(check(1))\n    asyncio.run(check(2))\n",
    ),
    "a context manager entered with with": (
        "@contextmanager\ndef check(x):\n    yield\n    assert x\n",
        "def test_one():\n    with check(1):\n        pass\n"
        "    with check(2), open(__file__):\n        pass\n",
    ),
    # whatever takes the result of the call may run it: only a discarded call is sure not to
    "a generator a for consumes": (
        "def check(x):\n    assert x\n    yield x\n",
        "def test_one():\n    for _ in check(1):\n        pass\n    list(check(2))\n",
    ),
    "tasks of a group": (
        "async " + CHECK,
        "async def test_one():\n    async with asyncio.TaskGroup() as tg:\n"
        "        tg.create_task(check(1))\n        tg.create_task(check(2))\n",
    ),
    "tasks gathered later": (
        "async " + CHECK,
        "async def test_one():\n    tasks = [asyncio.create_task(check(i)) for i in (1, 2)]\n"
        "    await asyncio.gather(*tasks)\n    check(3).close()\n",
    ),
}


# Covers F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("how", list(NEVER_RUN), ids=list(NEVER_RUN))
def test_a_helper_whose_body_never_runs_counts_zero(how: str) -> None:
    helper, test = NEVER_RUN[how]
    base = {"tests/test_a.py": CHECKED.format(helper=CHECK, test=TWICE)}
    current = {"tests/test_a.py": CHECKED.format(helper=helper, test=test)}
    assert _asserts(base) == 2
    assert _asserts(current) == 0
    assert "tests/test_a.py::test_one: asserts dropped from 2 to 0" in _problems(base, current)


# Covers F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("how", list(STILL_RUN), ids=list(STILL_RUN))
def test_a_helper_that_is_awaited_or_entered_keeps_counting(how: str) -> None:
    helper, test = STILL_RUN[how]
    base = {"tests/test_a.py": CHECKED.format(helper=CHECK, test=TWICE)}
    current = {"tests/test_a.py": CHECKED.format(helper=helper, test=test)}
    assert _asserts(current) == 2
    assert _problems(base, current) == []
    assert sensor.analyze(current).asserts == 2


# Covers F-0003.R9, F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_asserts_in_a_nested_function_count_only_when_the_test_calls_it() -> None:
    """Since the second audit: they count unless the test never mentions the function again.
    Calling it is one way of using it; handing it to the code under test is another."""
    base = {"tests/test_a.py": PLAIN}
    nested = "def test_one(x):\n    def later():\n" + HELD
    assert _asserts({"tests/test_a.py": nested}) == 0
    assert sensor.analyze({"tests/test_a.py": nested}).asserts == 2, "they stay in the total"
    assert _problems(base, {"tests/test_a.py": nested}) == [
        "tests/test_a.py::test_one: asserts dropped from 2 to 0"
    ]
    called = {"tests/test_a.py": nested + "\n    later()\n"}
    assert _asserts(called) == 2
    assert sensor.analyze(called).asserts == 2
    assert _problems(base, called) == []
    assert _problems(called, {"tests/test_a.py": nested + "\n    print(later)\n"}) == []
    # a nested function that the others of the test use counts once, where it is written
    siblings = (
        "import asyncio\n\n\ndef test_one(x):\n    async def one():\n        assert x\n\n"
        "    async def both():\n        await asyncio.gather(one(), one())\n\n"
        "    asyncio.run(both())\n    assert x\n"
    )
    assert _asserts({"tests/test_a.py": siblings}) == 2
    assert sensor.analyze({"tests/test_a.py": siblings}).asserts == 2
    # a function, a lambda or a class the test never mentions again: zero, kept in the total
    for hidden in (
        "def test_one(m):\n    unused = lambda: m.assert_called_once()\n",
        "def test_one(m):\n    lambda: m.assert_called_once()\n",
        "def test_one(x):\n    class Later:\n        assert x\n",
    ):
        assert _asserts({"tests/test_a.py": hidden}) == 0, hidden
        assert sensor.analyze({"tests/test_a.py": hidden}).asserts == 1, hidden
    # a lambda with two names, or one whose name the test uses, is in use
    for used in (
        "def test_one(m):\n    first = second = lambda: m.assert_called_once()\n",
        "def test_one(m):\n    check = lambda: m.assert_called_once()\n    run(check)\n",
    ):
        assert _asserts({"tests/test_a.py": used}) == 1, used


# Covers F-0003.R9, F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_call_of_what_is_not_a_name_resolves_to_no_helper() -> None:
    """`checks[0](x)` and `make()(x)` may run asserts: the sensor cannot know which."""
    source = "def check(x):\n    assert x\n\n\ndef test_one(x):\n    {call}\n"
    assert _asserts({"tests/test_a.py": source.format(call="check(x)")}) == 1
    for call in ("[check][0](x)", "(lambda: check)()(x)", "(check if x else check)(x)"):
        assert _asserts({"tests/test_a.py": source.format(call=call)}) == 0, call


# Covers F-0003.R5 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_fixture_named_like_a_test_may_yield() -> None:
    """`test_client` is a usual name for a fixture: pytest runs it up to its `yield` and after
    it. Only a test function that yields is not run."""
    fixture = "import pytest\n\n\n@pytest.fixture\ndef test_client(db):\n    c = make(db)\n"
    base = {"tests/conftest.py": fixture + "    assert c\n    return c\n"}
    current = {"tests/conftest.py": fixture + "    assert c\n    yield c\n    c.close()\n"}
    assert _asserts(current, "tests/conftest.py::test_client") == 1
    assert _problems(base, current) == []


USED_ELSEWHERE: dict[str, str] = {
    "a callback": (
        "def test_one(x):\n    def on_event(e):\n" + HELD + "    bus.subscribe(on_event)\n"
    ),
    "a thread target": (
        "def test_one(x):\n    def worker():\n"
        + HELD
        + "    t = threading.Thread(target=worker)\n    t.start(); t.join()\n"
    ),
    "the main of another event loop": (
        "def test_one(x):\n    async def main():\n" + HELD + "    trio.run(main)\n"
    ),
    "a side effect": (
        "def test_one(x, mocker):\n    def check(r):\n"
        + HELD
        + "    mocker.patch('m.send', side_effect=check)\n"
    ),
    "the callable of pytest.raises": (
        "def test_one(x):\n    def boom():\n" + HELD + "    pytest.raises(ValueError, boom)\n"
    ),
    "a function returned or kept": (
        "def test_one(x):\n    def check():\n" + HELD + "    checks = [check]\n    return checks\n"
    ),
    "a decorator": (
        "def test_one(x):\n    def twice(fn):\n"
        + HELD
        + "        return fn\n\n    @twice\n    def inner():\n        pass\n\n    inner()\n"
    ),
    "a function its decorator registers": (
        "def test_one(x, app):\n    @app.route('/x')\n    def handler():\n"
        + HELD
        + "    app.get('/x')\n"
    ),
    "a handler class": (
        "def test_one(x):\n    class Handler:\n        def handle(self, r):\n"
        + _indented(HELD)
        + "    run(Handler())\n"
    ),
    "a lambda handed over": (
        "def test_one(x, m):\n"
        "    run(lambda r: m.assert_called_once_with(r), lambda: pytest.fail('no'))\n"
    ),
}


# Covers F-0003.R9, F-0003.R10 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("how", list(USED_ELSEWHERE), ids=list(USED_ELSEWHERE))
def test_asserts_in_a_function_the_test_hands_over_keep_counting(how: str) -> None:
    """A callback, a thread target, a `side_effect`, the `main` of `trio.run`: the code under
    test runs them. They counted 0, so emptying them passed and moving asserts there failed."""
    base = {"tests/test_a.py": TWO_ASSERTS.format(head=PLAIN)}
    current = {"tests/test_a.py": TWO_ASSERTS.format(head=USED_ELSEWHERE[how])}
    assert _asserts(current) == 2
    assert _problems(base, current) == [], "moving the asserts there is not a weakening"
    emptied = {"tests/test_a.py": current["tests/test_a.py"].replace(HELD, "        pass\n")}
    if emptied == current:  # deeper or inline: the handler class, the lambdas
        emptied = {"tests/test_a.py": TWO_ASSERTS.format(head="def test_one(x, m):\n    run()\n")}
    assert _problems(current, {**emptied, **COMPENSATION}) == [
        "tests/test_a.py::test_one: asserts dropped from 2 to 0"
    ]


# ------------------------------------- S1, S2, S11: what is read, and from where (ADR-0026)
Files = Mapping[str, str | bytes | None]


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty repository on ``main``, away from the variables of a CI run."""
    for name in CI_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", HUMAN[1])
    _git(tmp_path, "config", "user.name", HUMAN[0])
    _git(tmp_path, "config", "core.autocrlf", "false")
    return tmp_path


def _write(root: Path, files: Files) -> None:
    """Write those files under ``root``; ``None`` deletes the file."""
    for name, content in files.items():
        path = root / name
        if content is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))


def _snapshot(root: Path, files: Files, message: str = "base", author: Identity = HUMAN) -> str:
    """Write those files, commit everything and return the commit."""
    _write(root, files)
    _git(root, "add", "-A")
    _commit(root, message, author, author)
    return _rev(root, "HEAD")


def _commit_index(root: Path, message: str) -> None:
    """Commit what the index holds, without looking at the working tree."""
    _git(root, "commit", "-q", "-m", message)


# Covers F-0003.R1 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_project_in_a_subdirectory_of_the_repository_is_compared(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`git ls-tree` answers relative to the directory it runs in and `git show <rev>:<path>`
    from the top of the repository: the base inventory was empty and the verdict always OK."""
    base = _snapshot(repo, {"pkg/tests/test_x.py": STRONG, "pkg/tests/data.json": "{}"})
    project = repo / "pkg"
    inv = sensor.base_inventory(base, project)
    assert inv.files == {"tests/test_x.py", "tests/data.json"}
    assert inv.tests["tests/test_x.py::test_x"].asserts == 2
    assert sensor.main(["--root", str(project), "--base", base, "--strict"]) == 0
    _write(repo, {"pkg/tests/test_x.py": WEAK})
    capsys.readouterr()
    assert sensor.main(["--root", str(project), "--base", base, "--strict"]) == 1
    assert f"  - {DROPPED}\n" in capsys.readouterr().out


# Covers F-0003.R1, F-0003.R11 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("command", ["show", "ls-tree"])
def test_a_base_that_cannot_be_read_stops_the_run_instead_of_passing(
    feature: Path, command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A base test file that cannot be read is not "no tests at the base": exit 2."""
    real = sensor._git

    def failing(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if args[0] == command and "pyproject.toml" not in args[-1]:
            return subprocess.CompletedProcess(args, 128, "", "fatal: no such object\n")
        return real(args, cwd)

    _weaken(feature)
    monkeypatch.setattr(sensor, "_git", failing)
    assert _run(feature) == 2
    out = capsys.readouterr().out
    assert "ERROR: cannot" in out and "at the base" in out and "fatal: no such object" in out
    assert "OK" not in out
    assert _run(feature, "--strict") == 2


# Covers F-0003.R1, F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_file_name_outside_ascii_and_a_byte_order_mark_are_read_on_both_sides(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    name = "tests/test_señal.py"
    bom = b"\xef\xbb\xbf"
    base = _snapshot(repo, {name: STRONG, "tests/test_x.py": bom + STRONG.encode()})
    assert sensor.base_inventory(base, repo).files == {name, "tests/test_x.py"}
    assert sensor.main(["--root", str(repo), "--base", base]) == 0
    assert "OK: no test weakened" in capsys.readouterr().out
    _write(repo, {name: WEAK, "tests/test_x.py": bom + WEAK.encode()})
    assert sensor.main(["--root", str(repo), "--base", base]) == 1
    out = capsys.readouterr().out
    assert f"  - {name}::test_x: asserts dropped from 2 to 1\n" in out
    assert f"  - {DROPPED}\n" in out
    assert "deleted" not in out and "parse error" not in out


# Covers F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_file_that_did_not_parse_at_the_base_either_is_a_note_not_a_problem(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = "def (:\n"
    files: dict[str, str | None] = {"tests/test_x.py": STRONG, "tests/data/broken_input.py": broken}
    assert _problems(files, files) == []
    # changed and still unreadable: the sensor cannot tell what the change did to it
    edited = _problems(files, {**files, "tests/data/broken_input.py": "def ((:\n"})
    assert len(edited) == 1 and "changed, and this Python cannot parse it" in edited[0]
    new = _problems(files, {**files, "tests/data/other.py": broken})
    assert len(new) == 1 and new[0].startswith("parse error: tests/data/other.py: cannot parse")
    assert sensor.analyze({"tests/deep.py": "x = " + "(" * 500 + "1" + ")" * 500}).tests == {}

    base = _snapshot(repo, files)
    assert sensor.main(["--root", str(repo), "--base", base]) == 0
    out = capsys.readouterr().out
    assert "NOTE: tests/data/broken_input.py: cannot parse" in out
    assert "it did not parse at the base either" in out


# Covers F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_file_this_python_cannot_parse_is_a_problem_once_it_changes(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Tests written for a newer Python than the one that runs the sensor (or a sample that
    is broken on purpose): unchanged it is a note; changed, the asserts it lost are invisible."""
    newer = "def test_n():\n    f() as a syntax of the future\n"
    asserts = "    assert g() == 1\n    assert h() == 2\n"
    assert sensor.analyze({"tests/test_n.py": newer + asserts}).errors, "no Python parses this"
    base = _snapshot(repo, {"tests/test_x.py": STRONG, "tests/test_n.py": newer + asserts})
    arguments = ["--root", str(repo), "--base", base, "--strict"]
    assert sensor.main(arguments) == 0
    assert "NOTE: tests/test_n.py: cannot parse" in capsys.readouterr().out
    _write(repo, {"tests/test_n.py": (newer + asserts).replace("\n", "\r\n")})
    assert sensor.main(arguments) == 0, "the same text with other line endings is not a change"
    _write(repo, {"tests/test_n.py": newer + "    pass\n"})
    capsys.readouterr()
    assert sensor.main(arguments) == 1
    out = capsys.readouterr().out
    assert "  - parse error: tests/test_n.py: cannot parse" in out
    assert "changed, and this Python cannot parse it" in out
    assert "run the sensor with the project's Python" in out


PYPROJECT = '[project]\nname = "x"\nversion = "0"\n\n[tool.rumbo]\n'
OK_TEST = "def test_ok():\n    assert 1 + 1 == 2\n"


# Covers F-0003.R1 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_the_tests_directory_is_the_one_the_base_configured(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0026: narrowing `[tool.rumbo].tests` in the change under test hides nothing."""
    base = _snapshot(
        repo,
        {
            "pyproject.toml": PYPROJECT + 'tests = "tests"\n',
            "tests/test_x.py": STRONG,
            "tests/unit/test_ok.py": OK_TEST,
        },
    )
    assert sensor.tests_directories(repo, base) == ["tests"]
    _write(repo, {"pyproject.toml": PYPROJECT + 'tests = "tests/unit"\n', "tests/test_x.py": None})
    assert sensor.tests_directory(repo) == "tests/unit", "what the working tree says"
    assert sensor.tests_directories(repo, base) == ["tests", "tests/unit"]
    assert sensor.tests_directories(repo, base, "tests\\unit") == ["tests/unit"]
    arguments = ["--root", str(repo), "--base", base]
    assert sensor.main(arguments) == 1
    out = capsys.readouterr().out
    assert "  - test file deleted: tests/test_x.py\n" in out
    assert "  - test deleted or renamed: tests/test_x.py::test_x\n" in out
    assert "  - [tool.rumbo].tests changed from 'tests' to 'tests/unit': 1 file(s)" in out
    # the setting alone, with every file still there, needs the approval too
    _write(repo, {"tests/test_x.py": STRONG})
    assert sensor.main(arguments) == 1
    out = capsys.readouterr().out
    assert "[tool.rumbo].tests changed from 'tests' to 'tests/unit'" in out
    assert "leave the tests directory (first: tests/test_x.py)" in out and "deleted" not in out
    assert sensor.main([*arguments, "--tests-dir", "tests/unit"]) == 0


# Covers F-0003.R1 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_tests_setting_that_leaves_no_file_of_the_base_out_is_compared_not_reported(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Adoption: the base has no `[tool.rumbo]` and its tests live in `spec/`. Both directories
    are inventoried on both sides, so the first comparison already sees `spec/`."""
    base = _snapshot(repo, {"spec/test_x.py": STRONG, "README.md": "x\n"})
    _write(repo, {"pyproject.toml": PYPROJECT + 'tests = "spec"\n'})
    assert sensor.tests_directories(repo, base) == ["tests", "spec"]
    arguments = ["--root", str(repo), "--base", base]
    assert sensor.main(arguments) == 0
    out = capsys.readouterr().out
    assert "NOTE: [tool.rumbo].tests changed from 'tests' to 'spec'" in out
    _write(repo, {"spec/test_x.py": WEAK})
    assert sensor.main(arguments) == 1
    assert "  - spec/test_x.py::test_x: asserts dropped from 2 to 1\n" in capsys.readouterr().out


# Covers F-0003.R13 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_an_agent_identity_of_the_base_cannot_be_dropped_by_the_change(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0026: `agent_authors` is the union of the base's list and the working tree's."""
    devin: Identity = ("Devin", "devin@bots.example")
    listed = PYPROJECT + 'agent_authors = ["devin@bots"]\n'
    base = _snapshot(repo, {"pyproject.toml": listed, "tests/test_x.py": STRONG})
    _snapshot(repo, {"tests/test_x.py": WEAK}, f"test: simplify {sensor.APPROVAL_MARK}", devin)
    arguments = ["--root", str(repo), "--base", base]
    assert sensor.main(arguments) == 1
    _snapshot(repo, {"pyproject.toml": PYPROJECT + "agent_authors = []\n"}, "chore: tidy", devin)
    capsys.readouterr()
    assert sensor.main(arguments) == 1
    assert "commit authored by an agent" in capsys.readouterr().out
    assert sensor.agent_authors(repo) == sensor.DEFAULT_AGENT_AUTHORS
    assert sensor.agent_authors(repo, base) == (*sensor.DEFAULT_AGENT_AUTHORS, "devin@bots")
    # an identity the change adds counts at once
    _write(repo, {"pyproject.toml": PYPROJECT + 'agent_authors = ["robo"]\n'})
    assert sensor.agent_authors(repo, base)[-2:] == ("devin@bots", "robo")


# Covers F-0003.R1 and F-0003.R13 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_pyproject_with_a_byte_order_mark_is_read_like_any_other(repo: Path) -> None:
    """PowerShell 5 writes UTF-8 with a byte order mark, which tomllib refuses: the settings of
    such a file are read, from the base and from the working tree, not silently dropped."""
    bom = "﻿"
    assert sensor._setting(bom + PYPROJECT + 'tests = "spec"\n', "tests") == "spec"
    listed = bom + PYPROJECT + 'tests = "spec"\nagent_authors = ["devin@bots"]\n'
    base = _snapshot(repo, {"pyproject.toml": listed, "spec/test_x.py": STRONG})
    assert sensor.tests_directory(repo) == "spec"
    assert sensor.tests_directories(repo, base) == ["spec"]
    assert sensor.agent_authors(repo)[-1] == "devin@bots"
    assert sensor.agent_authors(repo, base)[-1] == "devin@bots"


# Covers F-0003.R7 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_conftest_above_the_tests_directory_is_inventoried(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """pytest loads the `conftest.py` of every directory from the root down to the tests."""
    base = _snapshot(repo, {"pkg/tests/unit/test_x.py": STRONG, "pkg/pyproject.toml": PYPROJECT})
    project = repo / "pkg"
    arguments = ["--root", str(project), "--base", base, "--tests-dir", "tests/unit"]
    assert sensor.main(arguments) == 0
    hook = "def pytest_pyfunc_call(pyfuncitem):\n    return True\n"
    for conftest in ("conftest.py", "tests/conftest.py"):
        _write(project, {conftest: hook})
        capsys.readouterr()
        assert sensor.main(arguments) == 1
        out = capsys.readouterr().out
        assert f"  - {conftest}: conftest hook added: review (pytest_pyfunc_call)\n" in out
        _write(project, {conftest: None})
    _snapshot(repo, {"pkg/conftest.py": hook}, "a hook the base already has")
    assert sensor.main(["--root", str(project), "--base", "HEAD", "--tests-dir", "tests/unit"]) == 0
    assert "conftest.py" in sensor.base_inventory("HEAD", project, "tests/unit").files


# ------------------------------------------------- S5, S9, S12: the base and the approval
# Covers F-0003.R2 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_an_unreachable_rumbo_base_ref_is_reported_like_an_unreachable_base(
    feature: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gone = "1234567890abcdef1234567890abcdef12345678"
    _commit(feature, "feat: work")
    monkeypatch.setenv("RUMBO_BASE_REF", gone)
    assert sensor.resolve_base(None, feature) == (
        _rev(feature, "main"),
        [f"RUMBO_BASE_REF {gone!r} is not reachable; trying defaults"],
    )
    monkeypatch.setenv("GITHUB_BASE_REF", "release")
    _, warnings = sensor.resolve_base("no-such-ref", feature)
    assert warnings == [
        "explicit base 'no-such-ref' is not reachable; trying defaults",
        f"RUMBO_BASE_REF {gone!r} is not reachable; trying defaults",
        "GITHUB_BASE_REF 'origin/release' is not reachable; trying defaults",
    ]


# Covers F-0003.R2, F-0003.R3 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_the_upstream_of_the_branch_is_not_a_base(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """After `git push -u` the upstream is HEAD itself: as a base it compared nothing."""
    _git(repo, "checkout", "-q", "-b", "develop")
    _snapshot(repo, {"tests/test_x.py": STRONG})
    _git(repo, "checkout", "-q", "-b", "feature")
    _snapshot(repo, {"tests/test_x.py": WEAK}, "test: weaken")
    _git(repo, "update-ref", "refs/remotes/origin/feature", "HEAD")
    _git(repo, "config", "remote.origin.url", str(repo))
    _git(repo, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    _git(repo, "config", "branch.feature.remote", "origin")
    _git(repo, "config", "branch.feature.merge", "refs/heads/feature")
    assert _rev(repo, "@{upstream}") == _rev(repo, "HEAD")
    assert "@{upstream}" not in sensor.DEFAULT_BASES
    assert sensor.resolve_base(None, repo) == (None, [])
    assert sensor.main(["--root", str(repo), "--strict"]) == 2
    assert "WARNING: no base to compare against" in capsys.readouterr().out
    assert sensor.main(["--root", str(repo), "--strict", "--base", "develop"]) == 1


# Covers F-0003.R2, F-0003.R3 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_strict_refuses_a_default_base_that_is_head_itself(
    feature: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A weakening already committed on the default branch compared with itself and passed."""
    _git(feature, "checkout", "-q", "main")
    _weaken(feature)
    _commit(feature, "test: weaken, straight on main")
    root = ["--root", str(feature)]
    assert sensor.main(root) == 0, "without --strict it still warns and goes on"
    assert "WARNING: base 'main' is HEAD itself" in capsys.readouterr().out
    assert sensor.main([*root, "--strict"]) == 2
    out = capsys.readouterr().out
    assert "ERROR: nothing to compare" in out and "OK" not in out
    # a base somebody named is theirs to choose, HEAD included
    assert sensor.main([*root, "--strict", "--base", "main"]) == 0
    assert sensor.main([*root, "--strict", "--base", "HEAD~1"]) == 1
    monkeypatch.setenv("RUMBO_BASE_REF", "HEAD~1")
    assert sensor.main([*root, "--strict"]) == 1
    # the all-zero SHA is how a CI run says "this ref is new": nothing came before it
    monkeypatch.setenv("RUMBO_BASE_REF", "0" * 40)
    assert sensor.main([*root, "--strict"]) == 0


# Covers F-0003.R2, F-0003.R3 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
@pytest.mark.parametrize("event", ["workflow_dispatch", "schedule"])
def test_a_run_nobody_pushed_for_has_nothing_to_compare_and_passes(
    feature: Path, event: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A manual or scheduled run on the default branch: by design there is no change to judge."""
    _git(feature, "checkout", "-q", "main")
    root = ["--root", str(feature), "--strict"]
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    assert sensor.main(root) == 2
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    capsys.readouterr()
    assert sensor.main(root) == 0
    out = capsys.readouterr().out
    assert "NOTE: nothing to compare" in out and event in out
    # the same run on a branch that has commits of its own is compared as ever
    _git(feature, "checkout", "-q", "feature")
    _weaken(feature)
    _commit(feature, "test: weaken")
    assert sensor.main(root) == 1


# Covers F-0003.R2, F-0003.R3 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_work_not_committed_yet_is_compared_even_when_the_base_is_head(
    feature: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """On the default branch with no base named, `--strict` used to stop with "nothing to
    compare" although the working tree held a weakening."""
    _git(feature, "checkout", "-q", "main")
    root = ["--root", str(feature), "--strict"]
    assert sensor.main(root) == 2
    _weaken(feature)
    capsys.readouterr()
    assert sensor.main(root) == 1
    out = capsys.readouterr().out
    assert f"  - {DROPPED}\n" in out and "ERROR" not in out


# Covers F-0003.R2, F-0003.R3 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_new_ref_with_no_base_at_all_passes_with_a_warning(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The first push of a branch in a repository whose default branch is neither `main` nor
    `master`: the all-zero SHA says the ref is new, and no default name resolves. That is not
    an error of the change. `RUMBO_DEFAULT_BRANCH` gives the sensor the name to compare with."""
    _git(repo, "checkout", "-q", "-b", "develop")
    _snapshot(repo, {"tests/test_x.py": STRONG})
    _git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "branch", "-q", "-D", "develop")
    _snapshot(repo, {"tests/test_x.py": WEAK}, "test: weaken")
    root = ["--root", str(repo), "--strict"]
    assert sensor.main(root) == 2, "no base and nobody said the ref is new"
    assert "RUMBO_DEFAULT_BRANCH" in capsys.readouterr().out, "the message says what to set"
    monkeypatch.setenv("RUMBO_BASE_REF", "0" * 40)
    assert sensor.main(root) == 0
    out = capsys.readouterr().out
    assert "WARNING: no base to compare against" in out and "new ref" in out
    monkeypatch.setenv("RUMBO_DEFAULT_BRANCH", "develop")
    assert sensor.resolve_base(None, repo) == (_rev(repo, "origin/develop"), [])
    assert sensor.main(root) == 1
    monkeypatch.setenv("RUMBO_DEFAULT_BRANCH", "no-such-branch")
    _, warnings = sensor.resolve_base(None, repo)
    assert warnings == [
        "RUMBO_DEFAULT_BRANCH 'origin/no-such-branch' is not reachable; trying defaults"
    ]


# Covers F-0003.R2, F-0003.R3 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_base_that_is_not_in_the_clone_says_what_to_do(
    feature: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A force push (the commit before it is gone) or a shallow clone: the sensor cannot
    compare, and that stays an error; the message names the way out."""
    _git(feature, "checkout", "-q", "main")
    monkeypatch.setenv("RUMBO_BASE_REF", "1234567890" * 4)
    assert sensor.main(["--root", str(feature), "--strict"]) == 2
    out = capsys.readouterr().out
    assert "fetch-depth: 0" in out and "pull request" in out


# Covers F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_submodule_or_a_link_under_the_tests_is_not_a_deleted_file(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """git lists a submodule and a symbolic link as one entry each; on disk they are a
    directory. Both were "test file deleted" on every run."""
    _write(repo, {"tests/test_x.py": STRONG, "shared/data.json": "{}"})
    vendored = repo / "tests" / "vendor" / "lib"
    _write(vendored, {"test_v.py": "def test_v():\n    assert 1\n", "broken.py": "def (:\n"})
    _git(vendored, "init", "-q", "-b", "main")
    _git(vendored, "add", "-A")
    _git(vendored, "-c", "user.name=x", "-c", "user.email=x@example.com", "commit", "-qm", "sub")
    _git(repo, "add", "tests/test_x.py", "shared")
    _git(
        repo,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{_rev(vendored, 'HEAD')},tests/vendor/lib",
    )
    link = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input="../shared",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"120000,{link},tests/shared")
    _commit_index(repo, "base with a submodule and a link under tests")
    (repo / "tests" / "shared").mkdir()  # what a checkout without the link privilege leaves
    base = _rev(repo, "HEAD")
    inv = sensor.base_inventory(base, repo)
    assert inv.files == {"tests/test_x.py", "tests/shared"}
    assert sorted(sensor.worktree_inventory(repo).tests) == ["tests/test_x.py::test_x"]
    arguments = ["--root", str(repo), "--base", base]
    assert sensor.main(arguments) == 0
    assert "OK: no test weakened" in capsys.readouterr().out
    (repo / "tests" / "shared").rmdir()
    assert sensor.main(arguments) == 1
    assert "  - test file deleted: tests/shared\n" in capsys.readouterr().out


# Covers F-0003.R12, F-0003.R13 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_commit_message_cannot_forge_the_commit_of_a_human(feature: Path) -> None:
    """The log was split on the control characters 0x1E and 0x1F, which a message may hold: an
    agent's message could carry a whole record "written by the human". NUL cannot be in one."""
    agent: Identity = ("Claude", "noreply@anthropic.com")
    human = f"{HUMAN[0]} <{HUMAN[1]}>"
    forged = "\x1f".join(["0" * 40, HUMAN[1], human, human, f"fix {sensor.APPROVAL_MARK}"])
    _weaken(feature)
    _commit(feature, f"test: tidy\n\nnotes\x1e{forged}\x1e", agent, agent)
    assert sensor.human_approval("main", feature) is None
    assert _run(feature) == 1
    _commit(feature, f"test: the assert was wrong {sensor.APPROVAL_MARK}\n\nSecond line.")
    assert sensor.human_approval("main", feature) == (
        f"commit {_rev(feature, 'HEAD')[:10]} by {HUMAN[1]}"
    )
    assert _run(feature) == 0


# Covers F-0003.R12 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_the_label_counts_only_in_a_pull_request_run_of_github_actions(
    feature: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A JSON file and one variable were enough to approve from any shell."""
    _weaken(feature)
    event = feature / "event.json"
    labels = {"pull_request": {"labels": [{"name": sensor.APPROVAL_LABEL}]}}
    event.write_text(json.dumps(labels), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert _run(feature) == 1
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    assert _run(feature) == 1
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert _run(feature) == 0
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request_target")
    assert _run(feature) == 0
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    assert _run(feature) == 1
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    event.write_text(json.dumps({"pull_request": {"labels": None}}), encoding="utf-8")
    assert _run(feature) == 1, "a malformed event approves nothing and breaks nothing"


# ------------------------------------------------- what is read, at the edges of a project
# Covers F-0003.R1, F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_tests_directory_that_is_the_whole_project_leaves_out_what_is_not_its_own(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`tests = "."` (the tests live next to the code): the virtual environment, the caches
    and a directory that is a link are on disk under it and are not tests of the project."""
    setting = PYPROJECT + 'tests = "."\n'
    base = _snapshot(repo, {"pyproject.toml": setting, "app/tests.py": STRONG, "conftest.py": ""})
    broken = "def (:\n"
    _write(
        repo,
        {
            ".venv/pyvenv.cfg": "home = somewhere\n",
            ".venv/Lib/site-packages/pkg/test_p.py": broken,
            "app/__pycache__/tests.cpython-313.py": broken,
        },
    )
    link = repo / "shared"
    try:
        os.symlink(repo / "app", link, target_is_directory=True)
    except OSError:  # no privilege to create links here: a directory that says it is one
        link.mkdir()
        real = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "shared" or real(self))
    inv = sensor.worktree_inventory(repo, ".")
    assert inv.files == {"pyproject.toml", "app/tests.py", "conftest.py", "shared"}
    assert sorted(inv.tests) == ["app/tests.py::test_x"] and inv.errors == []
    assert sensor.main(["--root", str(repo), "--base", base, "--strict"]) == 0
    assert "OK: no test weakened" in capsys.readouterr().out


# Covers F-0003.R4 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_file_the_parser_gives_up_on_is_reported_like_one_it_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expression nested deeper than this Python's parser goes is not a syntax error, and
    it is not a crash of the sensor either."""
    real = ast.parse

    def parse(source: str, filename: str = "<unknown>") -> ast.Module:
        if filename.endswith("deep.py"):
            raise RecursionError("maximum recursion depth exceeded")
        return real(source, filename)

    monkeypatch.setattr(sensor.ast, "parse", parse)
    inv = sensor.analyze({"tests/deep.py": CALC, T: CALC})
    assert inv.errors == ["tests/deep.py: cannot parse (RecursionError)"]
    assert sorted(inv.tests) == [f"{T}::test_add"]
    assert _problems({T: CALC}, {T: CALC, "tests/deep.py": CALC}) == [
        "parse error: tests/deep.py: cannot parse (RecursionError)"
    ]


# Covers F-0003.R1, F-0003.R13 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_a_pyproject_that_is_not_text_gives_the_defaults(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_bytes(b"[tool.rumbo]\ntests = '\xff\xfe'\n")
    assert sensor.tests_directory(tmp_path) == "tests"
    assert sensor.agent_authors(tmp_path) == sensor.DEFAULT_AGENT_AUTHORS


# Covers F-0003.R11, F-0003.R14 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_the_script_runs_as_a_program_and_on_any_output_stream(
    feature: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Copied into a project it is run as `python scripts/check_tests_not_weakened.py`; and
    whatever stands for standard output may lack `reconfigure` (a pipe wrapper, a StringIO)."""
    script = inspect.getsourcefile(sensor)
    assert script is not None
    arguments = ["--root", str(feature), "--base", "main"]
    monkeypatch.setattr(sys, "argv", [script, *arguments])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(script, run_name="__main__")
    assert stopped.value.code == 0
    assert "OK: no test weakened" in capsys.readouterr().out
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    assert sensor.main(arguments) == 0
    assert "OK: no test weakened" in stream.getvalue()


# ------------------------------------------------ F-0003: one script, standard library only
# Covers F-0003.R14 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_the_sensor_imports_only_the_standard_library() -> None:
    tree = ast.parse(inspect.getsource(sensor))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0] if node.level == 0 else "(relative import)"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported - sys.stdlib_module_names == set()


# Covers F-0003.R14 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_the_generated_project_gets_the_sensor_verbatim(simple_project: Path) -> None:
    copy = simple_project / "scripts" / "check_tests_not_weakened.py"
    assert copy.read_text(encoding="utf-8") == inspect.getsource(sensor)


# Covers F-0003.R14 (specs/0003-tests-cannot-be-weakened-unnoticed.md).
def test_rumbo_check_tests_passes_its_arguments_to_the_sensor(feature: Path) -> None:
    _weaken(feature)
    arguments = ["check-tests", "--root", str(feature), "--base", "main"]
    assert rumbo(arguments) == 1
    assert rumbo([*arguments, "--tests-dir", "no-tests-here"]) == 0
