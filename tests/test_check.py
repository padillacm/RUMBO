"""ADR-0006 fitness function: `rumbo check` runs [tool.rumbo.checks] without a shell and tells
a verification failure (exit 1) from a broken environment (exit 3). ADR-0018: a tool that ran
and refused the code is a failure; ADR-0020: `{changed}` never names a file under spikes/."""

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from rumbo import cli
from rumbo.sensors import check

PY = f'"{Path(sys.executable).as_posix()}"'  # forward slashes: shlex posix mode eats backslashes


def py(code: str) -> str:
    """A step that runs a Python snippet (no double quotes inside the snippet)."""
    return f'{PY} -c "{code}"'


def exits(code: int, marker: str = "") -> str:
    """A step that records its run in order.txt and exits with ``code``."""
    record = f"open('order.txt', 'a').write('{marker};'); " if marker else ""
    return py(f"import sys; {record}sys.exit({code})")


def tool(root: Path, name: str, code: int, version: int, marker: str = "") -> str:
    """A step that runs a fake tool, ``python -m <name>``: it logs its arguments in calls.txt,
    exits ``code``, and answers ``--version`` (the ADR-0018 probe) with exit ``version``."""
    record = f"open('order.txt', 'a').write('{marker};')\n" if marker else ""
    (root / f"{name}.py").write_text(
        "import sys\n"
        "open('calls.txt', 'a').write(' '.join(sys.argv[1:]) + ';')\n"
        "if '--version' in sys.argv:\n"
        f"    print('{name} 1.0, said to the probe')\n"
        f"    sys.exit({version})\n"
        f"{record}"
        f"sys.exit({code})\n",
        encoding="utf-8",
    )
    return f"{PY} -m {name}"


def calls(root: Path) -> list[str]:
    """The argument lists the fake tools received, in order."""
    path = root / "calls.txt"
    return path.read_text(encoding="utf-8").strip(";").split(";") if path.exists() else []


def project(root: Path, steps: dict[str, str]) -> Path:
    lines = ["[project]", 'name = "demo"', 'version = "0"', "", "[tool.rumbo.checks]"]
    lines += [f"{name} = {json.dumps(command)}" for name, command in steps.items()]
    (root / "pyproject.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def ran(root: Path) -> list[str]:
    path = root / "order.txt"
    return path.read_text(encoding="utf-8").strip(";").split(";") if path.exists() else []


def run(root: Path, *args: str) -> int:
    return check.main([*args, "--dir", str(root)])


# ------------------------------------------------------------------- order and verdicts
# Covers F-0002.R1, F-0002.R2 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
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


# Covers F-0002.R1 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_step_output_streams_to_the_terminal(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"say": py("print('hello from the step')")})
    assert run(root) == 0
    assert "hello from the step" in capfd.readouterr().out


# Covers F-0002.R10, F-0002.R11 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_stops_at_the_first_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, {"lint": exits(1, "lint"), "test": exits(0, "test")})
    assert run(root) == 1
    assert ran(root) == ["lint"]
    out = capsys.readouterr().out
    assert "FAIL lint (exit 1)" in out
    assert out.rstrip().endswith("rumbo check: red (lint)")


# Covers F-0002.R10 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_keep_going_runs_everything_and_environment_wins(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tool(tmp_path, "typechecker", 3, 3, "types")  # ADR-0018: not even --version works
    root = project(tmp_path, {"lint": exits(1, "lint"), "types": broken, "test": exits(0, "test")})
    assert run(root, "--keep-going") == 3
    assert ran(root) == ["lint", "types", "test"]
    lines = capsys.readouterr().out.splitlines()
    assert "FAIL lint (exit 1)" in lines
    assert any(ln.startswith("ENV  types (") and ln.endswith("exit 3)") for ln in lines)
    assert "ok   test" in lines
    assert lines[-1] == "rumbo check: environment broken (types)"


# Covers F-0002.R10 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_keep_going_reports_every_failing_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"a": exits(1), "b": exits(0), "c": exits(1)})
    assert run(root, "--keep-going") == 1
    assert capsys.readouterr().out.splitlines()[-1] == "rumbo check: red (a, c)"


# ----------------------------------------------------------------------- exit classes
# Covers F-0002.R12 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_exit_code_other_than_0_or_1_is_an_environment_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # ADR-0018: still true when the tool cannot answer --version either.
    root = project(tmp_path, {"types": tool(tmp_path, "typechecker", 2, 2)})
    assert run(root) == 3
    lines = capsys.readouterr().out.splitlines()
    assert lines[-2].startswith("ENV  types (") and lines[-2].endswith("exit 2)")
    assert lines[-1] == "rumbo check: environment broken (types)"


# Covers F-0002.R12 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_a_tool_that_ran_and_exited_2_is_red(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """ADR-0018: pytest exits 2 on a collection error. The tool started, so the code is red."""
    step = tool(tmp_path, "faketool", 2, 0) + " --cov tests"
    root = project(tmp_path, {"test": step, "never": exits(0, "never")})
    assert run(root) == 1
    assert calls(root) == ["--cov tests", "--version"], "the probe: launcher, tool, --version"
    assert ran(root) == [], "a red step stops the run like any other failure"
    out = capfd.readouterr().out
    lines = out.splitlines()
    assert "FAIL test (faketool exit 2)" in lines
    assert lines[-1] == "rumbo check: red (test)"
    assert "said to the probe" not in out, "the probe is silent"


# Covers F-0002.R12 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_the_probe_is_silent_in_the_brief_log_too(tmp_path: Path) -> None:
    root = project(tmp_path, {"test": tool(tmp_path, "faketool", 5, 0)})
    assert run(root, "--brief") == 1
    log = (root / ".rumbo-check.log").read_text(encoding="utf-8")
    assert "FAIL test (faketool exit 5)" in log and "said to the probe" not in log


# Covers F-0002.R12 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_an_environment_that_cannot_launch_the_tool_is_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0018: `uv run pytest` exits 2 as well when uv cannot build the environment; then
    `uv run pytest --version` fails too, and the agent is not handed the repair."""
    root = project(tmp_path, {"test": tool(tmp_path, "faketool", 2, 2) + " --cov"})
    assert run(root) == 3
    assert calls(root) == ["--cov", "--version"]
    lines = capsys.readouterr().out.splitlines()
    assert "ENV  test (faketool exit 2)" in lines
    assert lines[-1] == "rumbo check: environment broken (test)"


# Covers F-0002.R12 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_the_probe_stops_at_the_interpreter_for_a_script(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0018: a script of the project has no --version; the interpreter is asked instead."""
    script = ["uv", "run", "python", "scripts/x.py", "--base", "main"]
    assert check._probe_argv(script) == ["uv", "run", "python", "--version"]
    frozen = ["uv", "run", "--frozen", "pytest", "--cov"]
    assert check._probe_argv(frozen) == ["uv", "run", "--frozen", "pytest", "--version"]
    assert check._probe_argv(["python", "-m", "mypy", "."]) == ["python", "-m", "mypy", "--version"]
    assert check._probe_argv(["python", "-c", "raise SystemExit(2)"]) == ["python", "--version"]
    assert check._probe_argv(["scripts/x.py", "--strict"]) is None, "no interpreter to ask"

    write(
        tmp_path,
        "scripts/sensor.py",
        "import sys\nopen('calls.txt', 'a').write(' '.join(sys.argv[1:]) + ';')\nsys.exit(2)\n",
    )
    root = project(tmp_path, {"sensor": f"{PY} scripts/sensor.py --strict"})
    assert run(root) == 1
    assert calls(root) == ["--strict"], "the script itself is never run with --version"
    assert "FAIL sensor (sensor.py exit 2)" in capsys.readouterr().out.splitlines()


# Covers F-0002.R11 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_a_step_killed_by_a_signal_is_env_and_is_not_probed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, -9)

    monkeypatch.setattr(check.subprocess, "run", fake_run)
    result = check.run_step("test", ["uv", "run", "pytest"], tmp_path, None)
    assert (result.status, result.detail) == (check.ENV, "pytest killed by signal 9")
    assert seen == [["uv", "run", "pytest"]]


# Covers F-0002.R12 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_a_probe_that_does_not_answer_in_60_seconds_is_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timeouts: list[object] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if argv[-1] != "--version":
            return subprocess.CompletedProcess(argv, 2)
        timeouts.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(argv, 60)

    monkeypatch.setattr(check.subprocess, "run", fake_run)
    result = check.run_step("test", ["uv", "run", "pytest"], tmp_path, None)
    assert (result.status, result.detail) == (check.ENV, "pytest exit 2")
    assert timeouts == [60]


# Covers F-0002.R11 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_missing_executable_is_an_environment_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"lint": "no-such-tool-rumbo-xyz check .", "test": exits(0, "test")})
    assert run(root) == 3
    assert ran(root) == []
    lines = capsys.readouterr().out.splitlines()
    assert "ENV  lint (no-such-tool-rumbo-xyz: not found)" in lines
    assert lines[-1] == "rumbo check: environment broken (lint)"


# Covers F-0002.R12 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_tool_name_skips_launchers() -> None:
    assert check._tool_name(["uv", "run", "pyright"]) == "pyright"
    assert (
        check._tool_name(["uv", "run", "python", "scripts/sensor.py", "--base", "x"]) == "sensor.py"
    )
    assert check._tool_name(["ruff", "check", "."]) == "ruff"


# Covers F-0002.R12 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_tool_name_skips_launcher_options_and_launchers_given_by_path() -> None:
    assert check._tool_name(["uv", "run", "--frozen", "pytest", "--cov"]) == "pytest"
    assert check._tool_name([".venv/bin/python", "-m", "pytest"]) == "pytest"
    assert check._tool_name(["C:/tools/uv.exe", "run", "ruff", "check"]) == "ruff"
    assert check._tool_name(["python", "-c", "raise SystemExit(2)"]) == "python"


# Covers F-0002.R4 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_usage_errors_never_exit_2(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as info:
        check.main(["--no-such-flag", "--dir", str(tmp_path)])
    assert info.value.code == 3


# ---------------------------------------------------------------------- configuration
# Covers F-0002.R3 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
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


# Covers F-0002.R2 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_quoted_operators_are_plain_arguments(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {"ok": py("import sys; sys.exit(0 if sys.argv[1] == 'a && b' else 1)") + " 'a && b'"},
    )
    assert run(root) == 0


# Covers F-0002.R2 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_arguments_reach_the_program_without_shell_expansion(tmp_path: Path) -> None:
    write(tmp_path, "a.py")  # a shell would expand *.py to it
    expected = "['*.py', '~', '%PATH%', 'two words']"
    step = py(f"import sys; sys.exit(0 if sys.argv[1:] == {expected} else 1)")
    assert run(project(tmp_path, {"literal": step + " *.py ~ %PATH% 'two words'"})) == 0


# Covers F-0002.R4 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
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


# Covers F-0002.R4 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_no_pyproject_is_an_environment_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path) == 3
    assert "no pyproject.toml" in capsys.readouterr().out


# ---------------------------------------------------------------------------- subsets
# Covers F-0002.R1 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_named_steps_run_a_subset_in_table_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(
        tmp_path, {"lint": exits(0, "lint"), "types": exits(0, "types"), "test": exits(0, "test")}
    )
    assert run(root, "test", "lint") == 0
    assert ran(root) == ["lint", "test"]
    assert capsys.readouterr().out.splitlines()[-1] == "rumbo check: green (2 steps)"


# Covers F-0002.R4 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
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
# Covers F-0002.R5 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_base_placeholder_takes_the_flag(tmp_path: Path) -> None:
    probe = py("import sys; sys.exit(0 if sys.argv[1] == 'feature' else 1)") + " {base}"
    root = project(tmp_path, {"sensor": probe})
    assert run(root, "--base", "feature") == 0
    assert run(root, "--base", "other") == 1


# Covers F-0002.R5 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_base_placeholder_is_replaced_inside_tokens(tmp_path: Path) -> None:
    probe = (
        py("import sys; sys.exit(0 if sys.argv[1] == '--base=feature' else 1)") + " --base={base}"
    )
    assert run(project(tmp_path, {"sensor": probe}), "--base", "feature") == 0


# Covers F-0002.R5 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
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
# Covers F-0002.R13 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_brief_success_prints_one_line_and_logs_the_rest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"say": py("print('hello'); print('world')"), "ok": exits(0)})
    assert run(root, "--brief") == 0
    log = (root / ".rumbo-check.log").read_text(encoding="utf-8")
    assert "hello\n" in log.replace("\r\n", "\n") and "ok   say" in log and "ok   ok" in log
    n = len(log.splitlines())
    assert capsys.readouterr().out == f"rumbo check: green ({n} log lines in .rumbo-check.log)\n"


# Covers F-0002.R13 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
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


# Covers F-0002.R13 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_brief_captures_stderr_and_keeps_the_terminal_ascii(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = "import sys; sys.stderr.write('caf\\u00e9 on stderr\\n'); sys.exit(1)"
    root = project(tmp_path, {"loud": py(code)})
    assert run(root, "--brief") == 1
    out = capsys.readouterr().out
    assert "caf? on stderr" in out or "caf\\xe9 on stderr" in out or "caf? on stderr" in out
    assert out.isascii()


# ---------------------------------------------------------------------------- {changed}
def git(root: Path, *args: str) -> None:
    flags = ["-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", *flags, *args], cwd=root, check=True, capture_output=True)


def write(root: Path, name: str, text: str = "x = 1\n") -> None:
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text, encoding="utf-8")


def show_args() -> str:
    """A step that prints the arguments it received, in place of ``{changed}``."""
    return py("import sys; print(sys.argv[1:])") + " {changed}"


def received(out: str) -> list[str]:
    """The argument list the step printed."""
    (line,) = [ln for ln in out.splitlines() if ln.startswith("[")]
    return json.loads(line.replace("'", '"'))


@pytest.fixture
def feature_repo(tmp_path: Path) -> Path:
    """main with two .py files and a README; a feature branch with a committed .py change, a
    deleted .py, a changed .md; plus an uncommitted .py change and an untracked .py file."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    write(tmp_path, "base.py")
    write(tmp_path, "gone.py")
    write(tmp_path, "README.md", "# demo\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    git(tmp_path, "switch", "-q", "-c", "feature")
    write(tmp_path, "pkg/committed.py")
    write(tmp_path, "README.md", "# changed\n")
    (tmp_path / "gone.py").unlink()
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "feature work")
    write(tmp_path, "base.py", "x = 2\n")  # uncommitted
    write(tmp_path, "new.py")  # untracked
    write(tmp_path, "notes.md", "untracked, not python\n")
    return tmp_path


# Covers F-0002.R6 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_changed_expands_to_the_changed_python_files_sorted(
    feature_repo: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    root = project(feature_repo, {"sensor": show_args()})
    assert run(root) == 0
    out = capfd.readouterr().out
    assert received(out) == ["base.py", "new.py", "pkg/committed.py"]
    assert "ok   sensor\n" in out


# Covers F-0002.R6 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_changed_includes_files_that_are_staged_but_not_committed(
    feature_repo: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """ADR-0013 says "uncommitted": a new file added to the index and a tracked file whose
    change is staged were both missed, so `git add` hid a file from the strict steps."""
    write(feature_repo, "staged_new.py")
    write(feature_repo, "base.py", "x = 3\n")
    git(feature_repo, "add", "staged_new.py", "base.py")
    root = project(feature_repo, {"sensor": show_args()})
    assert run(root) == 0
    names = received(capfd.readouterr().out)
    assert "staged_new.py" in names
    assert "base.py" in names


# Covers F-0002.R7 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_changed_never_includes_spikes(
    feature_repo: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """ADR-0020: a file named on the command line bypasses the tools' exclusions, so the
    prototypes under spikes/ are dropped from {changed}: committed, modified or untracked."""
    write(feature_repo, "spikes/committed.py")
    git(feature_repo, "add", ".")
    git(feature_repo, "commit", "-q", "-m", "a spike")
    write(feature_repo, "spikes/committed.py", "x = 2\n")  # uncommitted
    write(feature_repo, "spikes/deep/untracked.py")
    write(feature_repo, "pkg/spikes/kept.py")  # only the project's own spikes/ is exempt
    write(feature_repo, "spikes_report.py")
    root = project(feature_repo, {"sensor": show_args()})
    assert run(root) == 0
    assert received(capfd.readouterr().out) == [
        "base.py",
        "new.py",
        "pkg/committed.py",
        "pkg/spikes/kept.py",
        "spikes_report.py",
    ]
    assert check.resolve_changed("main", root, full=True).files == ["."], "--full is unchanged"


# Covers F-0002.R6, F-0002.R7 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_only_spikes_changed_means_no_changed_files(
    feature_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(feature_repo, "switch", "-q", "--discard-changes", "main")
    (feature_repo / "new.py").unlink()
    write(feature_repo, "spikes/proto.py", "import nothing_real\n")
    root = project(feature_repo, {"lint": exits(1, "lint") + " {changed}"})
    assert run(root) == 0
    assert ran(root) == []
    assert "ok   lint (no changed files)" in capsys.readouterr().out.splitlines()


# Covers F-0002.R6 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_changed_is_relative_to_the_given_base(
    feature_repo: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    git(feature_repo, "add", ".")
    git(feature_repo, "commit", "-q", "-m", "everything")
    root = project(feature_repo, {"sensor": show_args()})
    assert run(root, "--base", "feature~1") == 0
    # pkg/committed.py is already part of feature~1; only the last commit counts
    assert received(capfd.readouterr().out) == ["base.py", "new.py"]


# Covers F-0002.R6 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_changed_paths_are_relative_to_a_subdirectory_root(
    feature_repo: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    sub = feature_repo / "sub"
    write(sub, "inner.py")
    write(feature_repo, "outer.py")
    root = project(sub, {"sensor": show_args()})
    assert run(root) == 0
    assert received(capfd.readouterr().out) == ["inner.py"]


# Covers F-0002.R6 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_changed_with_nothing_changed_skips_the_step(
    feature_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(feature_repo, "switch", "-q", "--discard-changes", "main")
    (feature_repo / "new.py").unlink()
    root = project(
        feature_repo, {"lint": exits(0, "lint") + " {changed}", "test": exits(0, "test")}
    )
    assert run(root) == 0
    assert ran(root) == ["test"], "the step with no changed files is not run"
    lines = capsys.readouterr().out.splitlines()
    assert "ok   lint (no changed files)" in lines
    assert lines[-1] == "rumbo check: green (2 steps)"


# Covers F-0002.R6, F-0002.R13 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_changed_note_reaches_the_brief_log(feature_repo: Path) -> None:
    git(feature_repo, "switch", "-q", "--discard-changes", "main")
    (feature_repo / "new.py").unlink()
    root = project(feature_repo, {"lint": exits(1) + " {changed}"})
    assert run(root, "--brief") == 0
    assert "ok   lint (no changed files)" in (root / ".rumbo-check.log").read_text(encoding="utf-8")


# Covers F-0002.R8 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_full_replaces_changed_with_the_whole_tree(
    feature_repo: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    root = project(feature_repo, {"sensor": show_args()})
    assert run(root, "--full") == 0
    out = capfd.readouterr().out
    assert received(out) == ["."]
    assert "ok   sensor\n" in out


# Covers F-0002.R8 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_full_runs_even_when_nothing_changed(feature_repo: Path) -> None:
    git(feature_repo, "switch", "-q", "--discard-changes", "main")
    root = project(feature_repo, {"lint": exits(0, "lint") + " {changed}"})
    assert run(root, "--full") == 0
    assert ran(root) == ["lint"]


# Covers F-0002.R8 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_full_does_nothing_for_tables_without_changed(tmp_path: Path) -> None:
    root = project(tmp_path, {"a": exits(0, "a"), "b": exits(0, "b")})
    assert run(root, "--full") == 0
    assert ran(root) == ["a", "b"]
    assert check.build_parser().parse_args(["--full"]).full is True


# Covers F-0002.R9 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_changed_without_commits_falls_back_to_the_whole_tree(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    write(tmp_path, "a.py")
    root = project(tmp_path, {"sensor": show_args()})
    assert run(root) == 0
    out = capfd.readouterr().out
    assert received(out) == ["."]
    assert "ok   sensor (no git base: whole tree)\n" in out


# Covers F-0002.R9 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_changed_with_an_unknown_base_falls_back_to_the_whole_tree(
    feature_repo: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    root = project(feature_repo, {"sensor": show_args()})
    assert run(root, "--base", "no-such-ref") == 0
    out = capfd.readouterr().out
    assert received(out) == ["."]
    assert "ok   sensor (no git base: whole tree)\n" in out


# Covers F-0002.R9 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_changed_fallback_keeps_the_failure_exit_codes(feature_repo: Path) -> None:
    root = project(feature_repo, {"sensor": exits(1) + " {changed}"})
    assert run(root, "--base", "no-such-ref") == 1


# Covers F-0002.R9 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_without_git_the_base_is_main_and_changed_is_the_whole_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    real_run = subprocess.run

    def no_git(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if argv[0] == "git":
            raise FileNotFoundError(2, "No such file or directory", "git")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(check.subprocess, "run", no_git)
    step = py("import sys; print(sys.argv[1:])") + " {base} {changed}"
    root = project(tmp_path, {"sensor": step, "bad": exits(1, "bad") + " {changed}"})
    assert run(root) == 1, "the fallback runs the steps and keeps their exit codes"
    assert ran(root) == ["bad"]
    out = capfd.readouterr().out
    assert received(out) == ["main", "."]
    assert "ok   sensor (no git base: whole tree)\n" in out


# Covers F-0002.R9 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_outside_a_repository_the_base_is_main_and_changed_is_the_whole_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))  # no repository above
    assert check.resolve_base(None, tmp_path) == "main"
    whole_tree = check.Changed(["."], "no git base: whole tree")
    assert check.resolve_changed("main", tmp_path, full=False) == whole_tree


# Covers F-0002.R3 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_changed_must_be_an_argument_of_its_own(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(
        tmp_path, {"ok": exits(0, "ok"), "glued": exits(0, "glued") + " --files={changed}"}
    )
    assert run(root) == 3
    assert ran(root) == [], "nothing runs when the configuration is broken"
    out = capsys.readouterr().out
    assert out.startswith("rumbo check: environment broken (step glued:")
    assert "{changed} must be an argument of its own" in out


# Covers F-0002.R6 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_changed_is_resolved_once_and_only_when_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake(base: str, root: Path, full: bool) -> check.Changed:
        calls.append(base)
        return check.Changed(["a.py"])

    monkeypatch.setattr(check, "resolve_changed", fake)
    plain = project(tmp_path, {"a": exits(0, "a")})
    assert run(plain) == 0
    assert calls == [], "git is not consulted when no step uses {changed}"

    twice = project(
        tmp_path, {"lint": exits(0, "lint") + " {changed}", "types": exits(0) + " {changed}"}
    )
    assert run(twice) == 0
    assert calls == ["main"]


# ------------------------------------------------- one file, standard library, offline
NETWORK_MODULES = frozenset(
    {
        "asyncio",
        "ftplib",
        "http",
        "imaplib",
        "poplib",
        "smtplib",
        "socket",
        "socketserver",
        "ssl",
        "urllib",
        "xmlrpc",
    }
)


# Covers F-0002.R14 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_the_runner_imports_only_the_standard_library_and_no_network_module() -> None:
    tree = ast.parse(Path(check.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative import: the file is copied alone into a project"
            imported.add((node.module or "").split(".")[0])
    assert "subprocess" in imported, "the imports were read"
    assert imported <= sys.stdlib_module_names
    assert not imported & NETWORK_MODULES


# Covers F-0002.R14 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_the_runner_runs_as_a_single_file_with_no_package_installed(tmp_path: Path) -> None:
    root = project(tmp_path, {"ok": exits(0, "ok"), "bad": exits(1, "bad")})
    write(root, "scripts/rumbo_check.py", Path(check.__file__).read_text(encoding="utf-8"))
    # -I -S: no site-packages, no PYTHON* variables, the script's directory not on sys.path
    done = subprocess.run(
        [sys.executable, "-I", "-S", "scripts/rumbo_check.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1, done.stderr
    assert ran(root) == ["ok", "bad"], "the default project root is the current directory"
    assert done.stdout.splitlines()[-1] == "rumbo check: red (bad)"


# Covers F-0002.R14 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_rumbo_check_passes_its_arguments_to_the_runner_and_returns_its_exit_code(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    show = py("import sys; print(sys.argv[1:])") + " {base} {changed}"
    root = project(tmp_path, {"a": exits(1, "a"), "show": show, "b": exits(1, "b")})
    argv = ["check", "b", "show", "a", "--keep-going", "--full", "--base", "feature"]
    assert cli.main([*argv, "--dir", str(root)]) == 1
    assert ran(root) == ["a", "b"]
    out = capfd.readouterr().out
    assert received(out) == ["feature", "."]
    assert out.splitlines()[-1] == "rumbo check: red (a, b)"

    assert cli.main(["check", "show", "--brief", "--full", "--dir", str(root)]) == 0
    assert "ok   show" in (root / ".rumbo-check.log").read_text(encoding="utf-8")
    assert cli.main(["check", "nope", "--dir", str(root)]) == 3


# ============================================================ second audit (2026-11-16)
def fake_tools(
    monkeypatch: pytest.MonkeyPatch, code: int, version: int = 0
) -> list[tuple[list[str], dict[str, Any]]]:
    """Replace subprocess.run: a step exits ``code``, the ADR-0018 probe exits ``version``.
    Returns the (argv, keyword arguments) of every process the runner started."""
    seen: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        seen.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, version if argv[-1] == "--version" else code)

    monkeypatch.setattr(check.subprocess, "run", fake_run)
    return seen


# ------------------------------------------- R1: exit 1 is red only when the tool starts
# Covers F-0002.R11, F-0002.R12 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_exit_1_from_an_environment_that_cannot_start_the_tool_is_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0022: `uv run pytest` exits 1 as well when uv cannot build the environment (offline,
    index down). Then the probe fails too and the agent is not handed the repair."""
    step = tool(tmp_path, "faketool", 1, 1) + " tests"
    root = project(tmp_path, {"test": step, "never": exits(0, "never")})
    assert run(root) == 3
    assert calls(root) == ["tests", "--version"], "exit 1 is probed too"
    assert ran(root) == []
    lines = capsys.readouterr().out.splitlines()
    assert "ENV  test (faketool exit 1)" in lines
    assert lines[-1] == "rumbo check: environment broken (test)"


# Covers F-0002.R11, F-0002.R12 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_exit_1_from_a_tool_that_starts_is_red(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"test": tool(tmp_path, "faketool", 1, 0) + " tests"})
    assert run(root) == 1
    assert calls(root) == ["tests", "--version"]
    out = capfd.readouterr().out
    assert "FAIL test (exit 1)" in out.splitlines()
    assert "said to the probe" not in out, "the probe is silent"


# Covers F-0002.R12 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_the_probe_runs_once_per_distinct_command_in_a_run(tmp_path: Path) -> None:
    """ADR-0022: the red path stays cheap. Two failing steps of the same tool ask once."""
    same = tool(tmp_path, "faketool", 1, 0)
    other = tool(tmp_path, "othertool", 1, 0)
    root = project(tmp_path, {"a": same + " one", "b": same + " two", "c": other + " three"})
    assert run(root, "--keep-going") == 1
    assert calls(root) == ["one", "--version", "two", "three", "--version"]


# Covers F-0002.R11 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_exit_1_with_nothing_to_probe_stays_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A script run by its path has no launcher in front and no `--version` to ask: nothing
    stands between the runner and it, so its exit 1 is its verdict."""
    write(tmp_path, "scripts/x.py")
    seen = fake_tools(monkeypatch, 1)
    result = check.run_step("sensor", ["scripts/x.py", "--strict"], tmp_path, None)
    assert (result.status, result.detail) == (check.RED, "exit 1")
    assert len(seen) == 1


# -------------------------------------------- R3: a crash is never a verdict on the code
# Covers F-0002.R11 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
@pytest.mark.parametrize(
    ("code", "detail"),
    [
        (139, "pytest killed by signal 11 (exit 139)"),  # POSIX shell convention: 128 + SIGSEGV
        (137, "pytest killed by signal 9 (exit 137)"),
        (129, "pytest killed by signal 1 (exit 129)"),
        (159, "pytest killed by signal 31 (exit 159)"),
        (0xC0000005, "pytest crashed (0xC0000005)"),  # Windows: access violation
        (0xC0000409, "pytest crashed (0xC0000409)"),  # Windows: stack buffer overrun
        (0xC000013A, "pytest crashed (0xC000013A)"),  # Windows: Ctrl+C
        (-1073741819, "pytest crashed (0xC0000005)"),  # the same NTSTATUS read as signed
    ],
)
def test_a_crash_code_is_env_whatever_the_probe_would_say(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, detail: str
) -> None:
    """ADR-0022: a process that died did not judge the code, even if its tool starts."""
    seen = fake_tools(monkeypatch, code, version=0)
    result = check.run_step("test", ["uv", "run", "pytest"], tmp_path, None)
    assert (result.status, result.detail) == (check.ENV, detail)
    assert [argv for argv, _ in seen] == [["uv", "run", "pytest"]], "no probe"


# Covers F-0002.R12 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
@pytest.mark.parametrize("code", [2, 5, 6, 64, 127, 128, 160, 255])
def test_codes_next_to_the_crash_ranges_still_take_the_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    seen = fake_tools(monkeypatch, code, version=0)
    result = check.run_step("test", ["uv", "run", "pytest"], tmp_path, None)
    assert (result.status, result.detail) == (check.RED, f"pytest exit {code}")
    assert [argv for argv, _ in seen][1:] == [["uv", "run", "pytest", "--version"]]


# Covers F-0002.R11 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_a_missing_project_script_is_env_before_anything_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0022: `python scripts/sensor.py` with no such file exits 2 and python starts, so
    ADR-0018 called it red. The harness is incomplete: nothing judged the code."""
    step = f"{PY} scripts/check_tests_not_weakened.py --base main"
    root = project(tmp_path, {"sensor": step, "never": exits(0, "never")})
    assert run(root) == 3
    assert ran(root) == []
    lines = capsys.readouterr().out.splitlines()
    assert "ENV  sensor (scripts/check_tests_not_weakened.py: missing)" in lines
    assert lines[-1] == "rumbo check: environment broken (sensor)"

    seen = fake_tools(monkeypatch, 0)
    result = check.run_step("s", ["uv", "run", "python", "scripts/x.py"], tmp_path, None)
    assert (result.status, result.detail) == (check.ENV, "scripts/x.py: missing")
    assert seen == [], "uv is not even started"
    module = ["python", "-m", "pytest", "tests/test_gone.py"]
    assert check._missing_script(module, tmp_path) is None, "an argument of the tool is its own"


# Covers F-0002.R12 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
@pytest.mark.parametrize(
    ("argv", "code", "detail"),
    [
        (["uv", "run", "pytest", "--cov"], 3, "pytest exit 3: internal error"),
        (["python", "-m", "pytest"], 3, "pytest exit 3: internal error"),
        (["uv", "run", "ruff", "check", "."], 2, "ruff exit 2: abnormal termination"),
        (["uv", "run", "pyright", "."], 2, "pyright exit 2: fatal error"),
        (["uv", "run", "pyright"], 3, "pyright exit 3: config file could not be read or parsed"),
        (["uvx", "pyright.EXE", "src"], 4, "pyright.EXE exit 4: illegal command-line parameters"),
    ],
)
def test_an_exit_code_the_tool_documents_as_not_a_verdict_is_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], code: int, detail: str
) -> None:
    """ADR-0022: the tool's own documentation says this code is not about the code checked."""
    seen = fake_tools(monkeypatch, code, version=0)
    result = check.run_step("step", argv, tmp_path, None)
    assert (result.status, result.detail) == (check.ENV, detail)
    assert [started for started, _ in seen] == [argv], "no probe: the code alone decides"


# Covers F-0002.R12 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["uv", "run", "pytest"], 2),  # documented "interrupted"; measured: collection error
        (["uv", "run", "pytest"], 4),  # usage error OR a conftest.py that fails to import
        (["uv", "run", "pytest"], 5),  # no tests collected: a fact about the code
        (["uv", "run", "ruff", "format", "--check", "."], 2),  # also a syntax error (measured)
        (["uv", "run", "ruff", "--config", "x.toml", "check"], 2),  # sub-command not recognised
        (["uv", "run", "mypy", "."], 2),  # mypy documents no exit codes
        (["uv", "run", "pyright"], 5),  # not in pyright's table
    ],
)
def test_an_ambiguous_or_undocumented_code_keeps_the_probe_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], code: int
) -> None:
    """ADR-0022 keeps ADR-0018 where the documentation gives the same code to bad code and to
    a bad setup: red when the tool starts, environment when it does not."""
    seen = fake_tools(monkeypatch, code, version=0)
    assert check.run_step("step", argv, tmp_path, None).status == check.RED
    assert len(seen) == 2 and seen[1][0][-1] == "--version"
    fake_tools(monkeypatch, code, version=1)
    assert check.run_step("step", argv, tmp_path, None).status == check.ENV


# Covers F-0002.R12 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_every_environment_exit_code_names_its_source() -> None:
    """ADR-0022: no exit code is written from memory; each tool's entry cites its documentation."""
    assert set(check.ENV_EXITS) == {"pytest", "ruff check", "pyright"}
    assert set(check.ENV_EXIT_SOURCES) == set(check.ENV_EXITS)
    assert all(url.startswith("https://") for url in check.ENV_EXIT_SOURCES.values())
    for codes in check.ENV_EXITS.values():
        assert codes and all(code not in (0, 1) and reason for code, reason in codes.items())
    assert "mypy" not in check.ENV_EXITS, "its documentation does not separate crashes"


# ---------------------------------------- R2: a {changed} list longer than a command line
def many_files(count: int) -> list[str]:
    return [f"src/some_package/subpackage/module_number_{i:04d}_long_name.py" for i in range(count)]


# Covers F-0002.R6 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
@pytest.mark.parametrize(
    ("windows", "count", "fits"),
    [(True, 300, True), (True, 600, False), (False, 600, True), (False, 2500, False)],
)
def test_changed_too_long_for_one_command_line_becomes_the_whole_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: bool, count: int, fits: bool
) -> None:
    """CreateProcess takes 32,767 characters: 520 changed files were "python: not found"."""
    files = many_files(count)
    monkeypatch.setattr(check, "WINDOWS", windows)
    monkeypatch.setattr(check, "resolve_changed", lambda base, root, full: check.Changed(files))
    commands = {"lint": ["uv", "run", "ruff", "check", "{changed}"]}
    step = check.plan_steps(commands, "main", tmp_path, False)["lint"]
    if fits:
        assert step.argv == ["uv", "run", "ruff", "check", *files]
        assert step.note == ""
    else:
        assert step.argv == ["uv", "run", "ruff", "check", "."]
        assert step.note == f"{count} changed files: too long for one command line, whole tree"


# Covers F-0002.R6 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_a_long_changed_list_runs_on_the_whole_tree_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    files = many_files(2500)  # over the limit of every platform
    monkeypatch.setattr(check, "resolve_changed", lambda base, root, full: check.Changed(files))
    root = project(tmp_path, {"sensor": show_args(), "bad": exits(1) + " {changed}"})
    assert run(root, "--keep-going") == 1
    out = capfd.readouterr().out
    assert received(out) == ["."]
    note = "2500 changed files: too long for one command line, whole tree"
    assert f"ok   sensor ({note})" in out.splitlines()
    assert f"FAIL bad (exit 1; {note})" in out.splitlines(), "the note is shown on red too"


# Covers F-0002.R6 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_the_step_header_shows_a_few_changed_files_then_a_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    files = [f"pkg/m{i:02d}.py" for i in range(40)]
    monkeypatch.setattr(check, "resolve_changed", lambda base, root, full: check.Changed(files))
    root = project(tmp_path, {"sensor": show_args() + " --after"})
    assert run(root) == 0
    out = capfd.readouterr().out
    assert received(out) == [*files, "--after"], "the step still receives every file"
    (header,) = [ln for ln in out.splitlines() if ln.startswith("--- sensor:")]
    assert header.endswith(" pkg/m00.py pkg/m01.py pkg/m02.py (+37 more) --after")
    assert "pkg/m03.py" not in header

    few = ["a.py", "b.py", "c.py"]
    monkeypatch.setattr(check, "resolve_changed", lambda base, root, full: check.Changed(few))
    assert run(root) == 0
    (header,) = [ln for ln in capfd.readouterr().out.splitlines() if ln.startswith("--- sensor:")]
    assert header.endswith(" a.py b.py c.py --after")


# Covers F-0002.R11 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_a_command_line_the_system_refuses_is_not_reported_as_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows raises FileNotFoundError (winerror 206) for a command line that is too long."""

    def too_long(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        error = FileNotFoundError(2, "The filename or extension is too long")
        error.__setattr__("winerror", 206)  # an attribute of OSError on Windows only
        raise error

    monkeypatch.setattr(check.subprocess, "run", too_long)
    result = check.run_step("lint", ["uv", "run", "ruff", "check", "a.py"], tmp_path, None)
    assert result.status == check.ENV
    assert "not found" not in result.detail
    assert result.detail == "ruff: command line too long for the system"


# ------------------------------------------------- R4: the base candidates and the note
# Covers F-0002.R5 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_base_tries_the_same_candidates_as_the_sensor(tmp_path: Path) -> None:
    """On a repository whose default branch is master, {changed} was always the whole tree."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    assert check.DEFAULT_BASES == ("origin/main", "origin/master", "origin/HEAD", "main", "master")
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=tmp_path, check=True)
    assert check.resolve_base(None, tmp_path) == "main", "no commit yet: nothing resolves"
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "init")
    git(tmp_path, "switch", "-q", "-c", "feature")
    assert check.resolve_base(None, tmp_path) == "master"
    git(tmp_path, "branch", "main")
    assert check.resolve_base(None, tmp_path) == "main"
    git(tmp_path, "update-ref", "refs/remotes/origin/trunk", "HEAD")
    git(tmp_path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    assert check.resolve_base(None, tmp_path) == "origin/HEAD"
    git(tmp_path, "update-ref", "refs/remotes/origin/master", "HEAD")
    assert check.resolve_base(None, tmp_path) == "origin/master"
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert check.resolve_base(None, tmp_path) == "origin/main"
    assert check.resolve_base("v1.0", tmp_path) == "v1.0"


# Covers F-0002.R5, F-0002.R6 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_changed_on_a_master_repository_is_the_changed_files(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=tmp_path, check=True)
    write(tmp_path, "legacy.py")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    git(tmp_path, "switch", "-q", "-c", "feature")
    write(tmp_path, "new.py")
    show = py("import sys; print(sys.argv[1:])") + " {base} {changed}"
    root = project(tmp_path, {"sensor": show})
    assert run(root) == 0
    out = capfd.readouterr().out
    assert received(out) == ["master", "new.py"]
    assert "ok   sensor\n" in out


# Covers F-0002.R9 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_the_whole_tree_note_is_shown_on_red_and_environment_lines_too(
    feature_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A red step on a legacy tree: the line must say that the whole tree was checked."""
    broken = tool(feature_repo, "faketool", 2, 2)
    root = project(feature_repo, {"lint": exits(1) + " {changed}", "types": broken + " {changed}"})
    assert run(root, "--base", "no-such-ref", "--keep-going") == 3
    lines = capsys.readouterr().out.splitlines()
    assert "FAIL lint (exit 1; no git base: whole tree)" in lines
    assert "ENV  types (faketool exit 2; no git base: whole tree)" in lines
    assert check.StepResult("a", check.RED, "exit 1").line == "FAIL a (exit 1)"
    assert check.StepResult("a", check.ENV, "x: not found").line == "ENV  a (x: not found)"
    assert check.StepResult("a", check.GREEN, "", "note").line == "ok   a (note)"


# ------------------------------------------------------- R5: where the program is looked up
# Covers F-0002.R2 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_a_relative_program_is_looked_up_under_the_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """`rumbo check --dir` from another directory: `tools/hello` is the project's, and on
    Windows it may be `tools/hello.cmd`."""
    root, elsewhere = tmp_path / "project", tmp_path / "elsewhere"
    elsewhere.mkdir()
    if os.name == "nt":
        write(root, "tools/hello.cmd", "@echo hello from tools\n")
    else:
        write(root, "tools/hello", "#!/bin/sh\necho hello from tools\n")
        (root / "tools" / "hello").chmod(0o755)
    project(root, {"hello": "tools/hello", "gone": "tools/nothing-here --flag"})
    monkeypatch.chdir(elsewhere)
    assert run(root) == 3
    out = capfd.readouterr().out
    assert "hello from tools" in out
    assert "ok   hello" in out.splitlines()
    assert "ENV  gone (nothing-here: not found)" in out.splitlines()


# Covers F-0002.R2 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_windows_program_extensions_are_tried_under_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.venv/Scripts/python` is `python.exe`: CreateProcess adds no extension to a path."""
    write(tmp_path, ".venv/Scripts/python.exe", "")
    write(tmp_path, "tools/lint.cmd", "")
    write(tmp_path, "bin/plain", "")
    monkeypatch.setattr(check, "WINDOWS", True)
    python = check._launch_argv([".venv/Scripts/python", "-c", "pass"], tmp_path)
    assert python == [str(tmp_path / ".venv" / "Scripts" / "python.exe"), "-c", "pass"]
    lint = check._launch_argv(["tools/lint", "src"], tmp_path)
    assert lint == [str(tmp_path / "tools" / "lint.cmd"), "src"]
    monkeypatch.setattr(check, "WINDOWS", False)
    assert check._launch_argv(["bin/plain"], tmp_path) == [str(tmp_path / "bin" / "plain")]
    with pytest.raises(check.CannotStart, match="python: not found"):
        check._launch_argv([".venv/Scripts/python"], tmp_path)


# Covers F-0002.R2 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_a_batch_shim_runs_by_its_path_and_never_through_a_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """npm, npx, pnpm are `.cmd` files on Windows: found with PATHEXT, run with shell=False."""
    shim = str(tmp_path / "bin" / "npx.cmd")
    monkeypatch.setattr(check, "WINDOWS", True)
    monkeypatch.setattr(check.shutil, "which", lambda name: shim if name == "npx" else None)
    seen = fake_tools(monkeypatch, 0)
    result = check.run_step("docs", ["npx", "markdownlint", "two words", "docs/"], tmp_path, None)
    assert result.status == check.GREEN
    ((argv, kwargs),) = seen
    assert argv == [shim, "markdownlint", "two words", "docs/"]
    assert kwargs["shell"] is False

    seen.clear()
    plain = check.run_step("lint", ["ruff", "check", "a&b"], tmp_path, None)
    assert plain.status == check.GREEN, "an .exe receives its arguments verbatim"
    assert seen[0][0] == ["ruff", "check", "a&b"], "a name the system finds itself is left alone"


# Covers F-0002.R3 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
@pytest.mark.parametrize(
    "argument", ["a&b", "a|b", "<in", ">out", "a^b", "%PATH%", 'say "hi"', "a\nb"]
)
def test_a_batch_program_refuses_what_cmd_would_interpret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argument: str
) -> None:
    """A `.cmd`/`.bat` file is run by cmd.exe whatever the caller does, and cmd.exe parses
    its command line again: such an argument is refused, not passed."""
    monkeypatch.setattr(check, "WINDOWS", True)
    monkeypatch.setattr(check.shutil, "which", lambda name: str(tmp_path / "bin" / "tool.BAT"))
    seen = fake_tools(monkeypatch, 0)
    result = check.run_step("docs", ["tool", "ok", argument], tmp_path, None)
    assert result.status == check.ENV
    assert result.detail.startswith("tool.BAT: a batch file, and cmd.exe would interpret ")
    assert seen == [], "nothing was started"


# Covers F-0002.R3 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_a_batch_program_in_a_directory_cmd_would_interpret_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`C:/R&D/bin/tool.cmd`: the path of the program is part of what cmd.exe parses."""
    monkeypatch.setattr(check, "WINDOWS", True)
    monkeypatch.setattr(check.shutil, "which", lambda name: "C:/R&D/bin/tool.cmd")
    seen = fake_tools(monkeypatch, 0)
    result = check.run_step("docs", ["tool", "docs"], tmp_path, None)
    assert result.status == check.ENV
    assert result.detail.startswith("tool.cmd: a batch file, and cmd.exe would interpret '&' in ")
    assert seen == [], "nothing was started"


# Covers F-0002.R1 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_the_lines_of_the_runner_are_ascii_whatever_a_step_is_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A changed file whose name the terminal's code page cannot encode is in the step header:
    writing it as it is raised UnicodeEncodeError on a cp1252 console."""
    name = "\u4e2d\u6587.py"
    seen = fake_tools(monkeypatch, 0)
    result = check.run_step("lint", ["uv", "run", "ruff", "check", name], tmp_path, None)
    assert result.status == check.GREEN
    assert seen[0][0][-1] == name, "the tool receives the real name"
    assert capsys.readouterr().out == "--- lint: uv run ruff check ??.py\n"


# Covers F-0002.R2, F-0002.R3 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
@pytest.mark.skipif(os.name != "nt", reason="batch files exist on Windows only")
def test_a_real_cmd_shim_on_path_runs_and_is_not_an_injection_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    bindir = tmp_path / "bin dir"
    write(bindir, "hellotool.cmd", "@echo hello from the shim [%1]\n@echo ran>>shim-ran.txt\n")
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    root = project(tmp_path, {"hello": "hellotool one"})
    assert run(root) == 0
    assert "hello from the shim [one]" in capfd.readouterr().out
    (root / "shim-ran.txt").unlink()

    root = project(tmp_path, {"hello": "hellotool 'x&echo INJECTED'"})
    assert run(root) == 3
    assert "cmd.exe would interpret" in capfd.readouterr().out
    assert not (root / "shim-ran.txt").exists()


# ------------------------------------------------ R6: nothing unexpected exits 1 (red)
# Covers F-0002.R4 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ('[tool]\nrumbo = "x"\n', "[tool.rumbo] must be a table"),
        ('tool = "x"\n', "[tool] must be a table"),
        ("[tool.rumbo]\nchecks = 5\n", "[tool.rumbo.checks] must be a non-empty table"),
        ("[[tool.rumbo.checks]]\nlint = 'x'\n", "[tool.rumbo.checks] must be a non-empty table"),
        ("[[tool.rumbo]]\nchecks = 'x'\n", "[tool.rumbo] must be a table"),
    ],
)
def test_a_table_of_the_wrong_type_is_a_configuration_error(
    tmp_path: Path, toml: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text(toml, encoding="utf-8")
    assert run(tmp_path) == 3
    out = capsys.readouterr().out
    assert out.startswith(f"rumbo check: environment broken ({message}")
    assert len(out.splitlines()) == 1


# Covers F-0002.R4, F-0002.R13 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_a_log_that_cannot_be_written_is_an_environment_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"ok": exits(0, "ok")})
    (root / ".rumbo-check.log").mkdir()  # stands for any log that cannot be opened
    assert run(root, "--brief") == 3
    assert ran(root) == []
    out = capsys.readouterr().out
    assert out.startswith("rumbo check: environment broken (cannot write .rumbo-check.log")
    assert len(out.splitlines()) == 1


# Covers F-0002.R4 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_an_unexpected_exception_exits_3_and_never_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 1 means "the code is red" to the Stop hook, the gate and CI: a bug of the runner
    must not say that."""

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("something nobody\nforesaw")

    root = project(tmp_path, {"ok": exits(0, "ok")})
    monkeypatch.setattr(check, "plan_steps", boom)
    assert run(root) == 3
    assert capsys.readouterr().out == (
        "rumbo check: environment broken (internal error: RuntimeError: something nobody foresaw)\n"
    )


# Covers F-0002.R4 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_an_interrupt_is_not_swallowed_by_the_last_resort_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    root = project(tmp_path, {"ok": exits(0, "ok")})
    monkeypatch.setattr(check, "plan_steps", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run(root)


# ----------------------------------------------------- R7: quotes, spaces and backslashes
# Covers F-0002.R2 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_a_backslash_in_a_step_is_taken_literally() -> None:
    """`python scripts\\hello.py` became `scriptshello.py`: there are no escapes, only quotes."""
    split = check.split_command
    assert split(r"python scripts\hello.py") == ["python", r"scripts\hello.py"]
    venv = r".venv\Scripts\python.exe"
    assert split(venv + " -m pytest") == [venv, "-m", "pytest"]
    assert split(r'"C:\Program Files\Python\python.exe" -c "print(1)"') == [
        r"C:\Program Files\Python\python.exe",
        "-c",
        "print(1)",
    ]
    assert split(r"tool 'a\b' \\server\share\x") == ["tool", r"a\b", r"\\server\share\x"]
    assert split(r'grep "\d+\n" file') == ["grep", r"\d+\n", "file"]


# Covers F-0002.R2 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_quotes_group_words_and_a_path_with_spaces() -> None:
    split = check.split_command
    words = ["tool", "two words", "it's here", "abc"]
    assert split("tool 'two words' \"it's here\" a'b'c") == words
    assert split('"my tools/run me.py" --flag="a b"') == ["my tools/run me.py", "--flag=a b"]
    assert split("  spaced\t out  ") == ["spaced", "out"]
    assert split("tool '' # not-a-comment") == ["tool", "", "#", "not-a-comment"]
    with pytest.raises(ValueError, match="quotation"):
        split('tool "unterminated')


# Covers F-0002.R2, F-0002.R3 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_a_step_with_native_paths_and_spaces_runs(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "my project"
    write(root, "my scripts/hello.py", "import sys\nprint('hello', sys.argv[1:])\n")
    # sys.executable and os.sep: backslashes on Windows
    native = f'"{sys.executable}" "my scripts{os.sep}hello.py" "two words"'
    project(root, {"hello": native, "open": 'tool "unterminated'})
    assert run(root) == 3, "a quote left open is a configuration error"
    assert capfd.readouterr().out.startswith("rumbo check: environment broken (step open: cannot")
    project(root, {"hello": native})
    assert run(root) == 0
    assert "hello ['two words']" in capfd.readouterr().out


# --------------------------------------- R8: dash-named files, a BOM, the runner's stdin
# Covers F-0002.R6 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_a_changed_file_whose_name_starts_with_a_dash_is_not_an_option(
    feature_repo: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    write(feature_repo, "--fix.py")
    write(feature_repo, "-x.py")
    write(feature_repo, "pkg/-inner.py")
    root = project(feature_repo, {"sensor": show_args()})
    assert run(root) == 0
    names = received(capfd.readouterr().out)
    expected = ["./--fix.py", "./-x.py", "base.py", "new.py", "pkg/-inner.py", "pkg/committed.py"]
    assert names == expected
    assert not [name for name in names if name.startswith("-")]


# Covers F-0002.R1 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_a_pyproject_saved_with_a_byte_order_mark_is_read(tmp_path: Path) -> None:
    """Windows editors and PowerShell write a UTF-8 BOM; tomllib refuses it."""
    root = project(tmp_path, {"ok": exits(0, "ok")})
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    (root / "pyproject.toml").write_text(text, encoding="utf-8-sig")
    assert (root / "pyproject.toml").read_bytes().startswith(b"\xef\xbb\xbf")
    assert run(root) == 0
    assert ran(root) == ["ok"]


# Covers F-0002.R2 (specs/0002-one-verification-runner-offline-on-three-platforms.md).
def test_steps_do_not_read_the_runner_standard_input(tmp_path: Path) -> None:
    """A step that prompts would hang the Stop hook, or eat the hook's JSON payload."""
    step = py("import sys; sys.exit(0 if sys.stdin.read() == '' else 1)")
    root = project(tmp_path, {"ask": step})
    done = subprocess.run(
        [sys.executable, check.__file__, "--dir", str(root)],
        input="consumed by the step\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout
    assert done.stdout.splitlines()[-1] == "rumbo check: green (1 steps)"
