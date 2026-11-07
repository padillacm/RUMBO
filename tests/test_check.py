"""ADR-0006 fitness function: `rumbo check` runs [tool.rumbo.checks] without a shell and tells
a verification failure (exit 1) from a broken environment (exit 3)."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rumbo.sensors import check

PY = f'"{Path(sys.executable).as_posix()}"'  # forward slashes: shlex posix mode eats backslashes


def py(code: str) -> str:
    """A step that runs a Python snippet (no double quotes inside the snippet)."""
    return f'{PY} -c "{code}"'


def exits(code: int, marker: str = "") -> str:
    """A step that records its run in order.txt and exits with ``code``."""
    record = f"open('order.txt', 'a').write('{marker};'); " if marker else ""
    return py(f"import sys; {record}sys.exit({code})")


def project(root: Path, steps: dict[str, str]) -> Path:
    lines = ["[project]", 'name = "demo"', 'version = "0"', "", "[tool.rumbo.checks]"]
    lines += [f"{name} = {json.dumps(command)}" for name, command in steps.items()]
    (root / "pyproject.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def ran(root: Path) -> list[str]:
    path = root / "order.txt"
    return path.read_text().strip(";").split(";") if path.exists() else []


def run(root: Path, *args: str) -> int:
    return check.main([*args, "--dir", str(root)])


# ------------------------------------------------------------------- order and verdicts
def test_runs_every_step_in_table_order(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(
        tmp_path, {"zeta": exits(0, "zeta"), "alpha": exits(0, "alpha"), "mid": exits(0, "mid")}
    )
    assert run(root) == 0
    assert ran(root) == ["zeta", "alpha", "mid"]
    lines = capsys.readouterr().out.splitlines()
    assert [ln for ln in lines if not ln.startswith("---")] == [
        "ok   zeta",
        "ok   alpha",
        "ok   mid",
        "rumbo check: green (3 steps)",
    ]


def test_step_output_streams_to_the_terminal(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"say": py("print('hello from the step')")})
    assert run(root) == 0
    assert "hello from the step" in capfd.readouterr().out


def test_stops_at_the_first_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, {"lint": exits(1, "lint"), "test": exits(0, "test")})
    assert run(root) == 1
    assert ran(root) == ["lint"]
    out = capsys.readouterr().out
    assert "FAIL lint (exit 1)" in out
    assert out.rstrip().endswith("rumbo check: red (lint)")


def test_keep_going_runs_everything_and_environment_wins(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(
        tmp_path, {"lint": exits(1, "lint"), "types": exits(3, "types"), "test": exits(0, "test")}
    )
    assert run(root, "--keep-going") == 3
    assert ran(root) == ["lint", "types", "test"]
    lines = capsys.readouterr().out.splitlines()
    assert "FAIL lint (exit 1)" in lines
    assert any(ln.startswith("ENV  types (") and ln.endswith("exit 3)") for ln in lines)
    assert "ok   test" in lines
    assert lines[-1] == "rumbo check: environment broken (types)"


def test_keep_going_reports_every_failing_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"a": exits(1), "b": exits(0), "c": exits(1)})
    assert run(root, "--keep-going") == 1
    assert capsys.readouterr().out.splitlines()[-1] == "rumbo check: red (a, c)"


# ----------------------------------------------------------------------- exit classes
def test_exit_code_other_than_0_or_1_is_an_environment_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"types": exits(2)})
    assert run(root) == 3
    lines = capsys.readouterr().out.splitlines()
    assert lines[-2].startswith("ENV  types (") and lines[-2].endswith("exit 2)")
    assert lines[-1] == "rumbo check: environment broken (types)"


def test_missing_executable_is_an_environment_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"lint": "no-such-tool-rumbo-xyz check .", "test": exits(0, "test")})
    assert run(root) == 3
    assert ran(root) == []
    lines = capsys.readouterr().out.splitlines()
    assert "ENV  lint (no-such-tool-rumbo-xyz: not found)" in lines
    assert lines[-1] == "rumbo check: environment broken (lint)"


def test_tool_name_skips_launchers() -> None:
    assert check._tool_name(["uv", "run", "pyright"]) == "pyright"
    assert (
        check._tool_name(["uv", "run", "python", "scripts/sensor.py", "--base", "x"]) == "sensor.py"
    )
    assert check._tool_name(["ruff", "check", "."]) == "ruff"


def test_usage_errors_never_exit_2(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as info:
        check.main(["--no-such-flag", "--dir", str(tmp_path)])
    assert info.value.code == 3


# ---------------------------------------------------------------------- configuration
@pytest.mark.parametrize(
    "command",
    [
        "ruff check . && pytest",
        "pytest | tail -40",
        "pytest > out.txt",
        "pytest 2>&1",
        "echo $HOME",
        "a ; b",
    ],
)
def test_shell_operators_are_configuration_errors(
    tmp_path: Path, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"ok": exits(0, "ok"), "shelly": command})
    assert run(root) == 3
    assert ran(root) == [], "nothing runs when the configuration is broken"
    out = capsys.readouterr().out
    assert out.startswith("rumbo check: environment broken (step shelly:")
    assert "needs a shell" in out


def test_quoted_operators_are_plain_arguments(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {"ok": py("import sys; sys.exit(0 if sys.argv[1] == 'a && b' else 1)") + " 'a && b'"},
    )
    assert run(root) == 0


@pytest.mark.parametrize(
    "toml",
    [
        "[project]\nname = 'demo'\n",
        "[tool.rumbo]\nchecks = 'uv run pytest'\n",
        "[tool.rumbo.checks]\n",
        "[tool.rumbo.checks]\nlint = ['uv', 'run', 'ruff']\n",
        "[tool.rumbo.checks]\nlint = ''\n",
        "[tool.rumbo.checks\nlint = 'x'\n",
    ],
)
def test_missing_or_malformed_table_is_an_environment_problem(
    tmp_path: Path, toml: str, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text(toml, encoding="utf-8")
    assert run(tmp_path) == 3
    assert capsys.readouterr().out.startswith("rumbo check: environment broken (")


def test_no_pyproject_is_an_environment_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path) == 3
    assert "no pyproject.toml" in capsys.readouterr().out


# ---------------------------------------------------------------------------- subsets
def test_named_steps_run_a_subset_in_table_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(
        tmp_path, {"lint": exits(0, "lint"), "types": exits(0, "types"), "test": exits(0, "test")}
    )
    assert run(root, "test", "lint") == 0
    assert ran(root) == ["lint", "test"]
    assert capsys.readouterr().out.splitlines()[-1] == "rumbo check: green (2 steps)"


def test_unknown_step_is_an_environment_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"lint": exits(0, "lint")})
    assert run(root, "lint", "nope") == 3
    assert ran(root) == []
    out = capsys.readouterr().out
    assert out.startswith("rumbo check: environment broken (unknown step 'nope'")
    assert "defines: lint" in out


# ------------------------------------------------------------------------------ {base}
def test_base_placeholder_takes_the_flag(tmp_path: Path) -> None:
    probe = py("import sys; sys.exit(0 if sys.argv[1] == 'feature' else 1)") + " {base}"
    root = project(tmp_path, {"sensor": probe})
    assert run(root, "--base", "feature") == 0
    assert run(root, "--base", "other") == 1


def test_base_placeholder_is_replaced_inside_tokens(tmp_path: Path) -> None:
    probe = (
        py("import sys; sys.exit(0 if sys.argv[1] == '--base=feature' else 1)") + " --base={base}"
    )
    assert run(project(tmp_path, {"sensor": probe}), "--base", "feature") == 0


def test_base_defaults_to_origin_main_when_it_exists(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    env_flags = [
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=t",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", *env_flags, "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp_path, check=True
    )
    assert check.resolve_base(None, tmp_path) == "main"
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=tmp_path, check=True
    )
    assert check.resolve_base(None, tmp_path) == "origin/main"
    assert check.resolve_base("v1.0", tmp_path) == "v1.0"


# ------------------------------------------------------------------------------- brief
def test_brief_success_prints_one_line_and_logs_the_rest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"say": py("print('hello'); print('world')"), "ok": exits(0)})
    assert run(root, "--brief") == 0
    log = (root / ".rumbo-check.log").read_text(encoding="utf-8")
    assert "hello\n" in log.replace("\r\n", "\n") and "ok   say" in log and "ok   ok" in log
    n = len(log.splitlines())
    assert capsys.readouterr().out == f"rumbo check: green ({n} log lines in .rumbo-check.log)\n"


def test_brief_failure_prints_the_last_40_log_lines_then_the_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    noisy = py("import sys; [print(f'line {i}') for i in range(1, 61)]; sys.exit(1)")
    root = project(tmp_path, {"noisy": noisy, "never": exits(0, "never")})
    assert run(root, "--brief") == 1
    assert ran(root) == []
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 41
    assert "line 21" not in lines and lines[0] == "line 22"
    assert lines[-2] == "FAIL noisy (exit 1)"
    assert lines[-1] == "rumbo check: red (noisy)"


def test_brief_captures_stderr_and_keeps_the_terminal_ascii(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = "import sys; sys.stderr.write('caf\\u00e9 on stderr\\n'); sys.exit(1)"
    root = project(tmp_path, {"loud": py(code)})
    assert run(root, "--brief") == 1
    out = capsys.readouterr().out
    assert "caf? on stderr" in out or "caf\\xe9 on stderr" in out or "caf? on stderr" in out
    assert out.isascii()
