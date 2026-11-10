"""The anti-cheat sensor is tested by the same tests that get copied into every project,
plus the fitness function of ADR-0007 (idiomatic assertion counting and helper resolution).
"""

import ast
import inspect
import json
import os
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
CI_VARIABLES = ("GITHUB_EVENT_PATH", "GITHUB_BASE_REF", "RUMBO_BASE_REF")
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
