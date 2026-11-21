"""The Claude Code guard hook: AGENTS.md hard rules as sensors."""

import io
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from rumbo.scaffold import Config, plan_files
from rumbo.sensors import guard


@pytest.fixture(autouse=True)
def _outside_any_claude_code_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` prefers CLAUDE_PROJECT_DIR over the payload's cwd (guard.py). When the suite
    itself runs inside a Claude Code hook that variable points at this repository, and a
    tmp_path becomes "outside the project". Each test starts without it."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


def _edit(path: str, old: str = "a", new: str = "b") -> dict[str, object]:
    return {"file_path": path, "old_string": old, "new_string": new}


# Covers F-0001.R1 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
@pytest.mark.parametrize(
    "rel",
    [
        "Makefile",
        "pyproject.toml",
        ".github/workflows/ci.yml",
        "infra/Dockerfile",
        "scripts/check_tests_not_weakened.py",
        "scripts/rumbo_guard.py",
        "scripts/rumbo_check.py",
        ".env",
        ".env.local",
        "evals/run_evals.py",
        ".claude/settings.local.json",
        ".claude/settings.json",
    ],
)
def test_protected_files_are_blocked(tmp_path: Path, rel: str) -> None:
    reason = guard.check_edit("Edit", _edit(str(tmp_path / rel)), tmp_path)
    assert reason and "protected" in reason


# Covers F-0001.R1 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_relative_paths_are_normalised(tmp_path: Path) -> None:
    assert guard.check_edit("Edit", _edit("src/../Makefile"), tmp_path)
    assert guard.check_edit("Edit", _edit("./.github/workflows/ci.yml"), tmp_path)
    assert guard.check_edit("Edit", _edit("src/demo/x.py"), tmp_path) is None


# Covers F-0001.R10 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_ordinary_files_and_files_outside_the_project_are_allowed(tmp_path: Path) -> None:
    inside = _edit(str(tmp_path / "src/demo/domain/x.py"))
    assert guard.check_edit("Edit", inside, tmp_path) is None
    outside = {"file_path": "/elsewhere/file.py", "content": ""}
    assert guard.check_edit("Write", outside, tmp_path) is None
    assert guard.check_edit("Edit", {}, tmp_path) is None


# Covers F-0001.R2 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_template_mark_cannot_be_removed_by_edit_or_write(tmp_path: Path) -> None:
    doc = tmp_path / "specs" / "0001-x.md"
    doc.parent.mkdir()
    doc.write_text("<!-- rumbo:template — delete me -->\n# F-0001\n", encoding="utf-8")
    edit = _edit(str(doc), "<!-- rumbo:template — delete me -->\n# F-0001", "# F-0001")
    assert "human" in (guard.check_edit("Edit", edit, tmp_path) or "")
    multi = {"file_path": str(doc), "edits": [edit]}
    assert guard.check_edit("MultiEdit", multi, tmp_path)
    write = {"file_path": str(doc), "content": "# F-0001\nrewritten\n"}
    assert guard.check_edit("Write", write, tmp_path)
    # Keeping the mark is fine; a brand-new numbered document must carry it too (ADR-0025).
    keep = {"file_path": str(doc), "content": "<!-- rumbo:template -->\n# F-0001\nmore\n"}
    assert guard.check_edit("Write", keep, tmp_path) is None
    new = {"file_path": str(tmp_path / "specs" / "0002-y.md"), "content": "# F-0002\n"}
    assert "ADR-0025" in (guard.check_edit("Write", new, tmp_path) or "")


# Covers F-0001.R3 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
@pytest.mark.parametrize(
    "command",
    [
        'git commit -m "fix [tests-change-approved]"',
        "git commit --no-verify -m x",
        "git commit -n -m x",
        "git commit -anm x",
        "git -c user.name=x commit -am x -n",
        "git merge --no-verify feature",
        "git push --no-verify origin feature",
        "SKIP=gitleaks git commit -m x",
        "git push --force origin main",
        "git push -f",
        "git push origin main --force-with-lease",
        "git push origin +main",
        "git reset --hard HEAD~1",
        "git config core.hooksPath /dev/null",
        "git -c core.hooksPath=/dev/null commit -m x",
        "uv run pre-commit uninstall",
        "rm .git/hooks/pre-commit",
        "git config rumbo.human true",
        "git config --unset rumbo.human",
        "git config --global rumbo.human false",
        "git -c rumbo.human=true commit -m x",
        "rm -rf /",
        "rm -rf ~",
        "rm -rf ~/",
        "rm -rf .",
        "rm -rf *",
        "rm -r -f /",
        "rm --recursive --force /",
        "rm -rf -- /",
        "rm -rf $HOME",
        "echo x > Makefile",
        "echo x >| Makefile",
        "echo x >> ./scripts/check_tests_not_weakened.py",
        "sed -i 's/strict/off/' pyproject.toml",
        "cat x | tee .github/workflows/ci.yml",
        "cat x | tee -a infra/Dockerfile",
        "echo k > .env.local",
        "echo x > .claude/settings.local.json",
    ],
)
def test_dangerous_commands_are_blocked(command: str) -> None:
    assert guard.check_bash(command)


# Covers F-0001.R10 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
@pytest.mark.parametrize(
    "command",
    [
        "make check-brief",
        "git push -u origin feature",
        "git commit -m 'feat: x'",
        "rm -rf build/",
        "rm -rf ./build",
        "rm -rf /tmp/scratch",
        "rm -rf $TMPDIR/x",
        "uv add httpx",
        "echo x > notes.txt",
        "echo x >> Makefile.md",
        "echo x > pyproject.toml.bak",
        "echo x > .environment",
        "sed -i 's/Makefile/x/' README.md",
        "cat x | tee out.txt && cat .envrc",
        "git log | tee log.txt; grep infra/ x",
        "git commit -m 'docs: explain the no-verify rule'",
        "git push origin HEAD",
        "grep -n Makefile README.md",
        "git log -n 5",
        "git log --oneline -n 3 -- src",
        "git clean -n",
        "git tag -n",
        "git rev-list -n 1 HEAD",
        "git stash list -n 3",
        "git push -n origin feature",
        "git commit -uno -m x",
        "git config --get user.email",
        "git config user.name rumbo",
    ],
)
def test_ordinary_commands_are_allowed(command: str) -> None:
    assert guard.check_bash(command) is None


# Covers F-0001.R4 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_post_bash_detects_protected_files_changed_since_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # What `uv add rich` leaves behind (ADR-0019): `git show HEAD:pyproject.toml` answers the
    # committed version, the working tree has one more dependency.
    head = '[project]\nname = "demo"\ndependencies = ["httpx"]\n'
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["httpx", "rich"]\n', encoding="utf-8"
    )

    def fake_git(status: str) -> object:
        return lambda cmd, **_: subprocess.CompletedProcess(
            cmd, 0, head if cmd[1] == "show" else status, ""
        )

    status = " M Makefile\0?? .github/workflows/x.yml\0 M pyproject.toml\0 M src/demo/a.py\0"
    monkeypatch.setattr(guard.subprocess, "run", fake_git(status))
    reason = guard.check_post_bash(tmp_path) or ""
    assert "Makefile" in reason and ".github/workflows/x.yml" in reason
    assert "pyproject.toml" not in reason  # uv add is allowed to change its dependency tables
    # Stop refuses first for the protected files, before running make
    code, message = guard.check_stop({}, tmp_path)
    assert code == guard.BLOCK and "protected" in message
    monkeypatch.setattr(guard.subprocess, "run", fake_git(" M pyproject.toml\0 M uv.lock\0"))
    assert guard.check_post_bash(tmp_path) is None


# Covers F-0001.R11 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_git_missing_never_crashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_git(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("git")

    monkeypatch.setattr(guard.subprocess, "run", no_git)
    assert guard.changed_files(tmp_path) == []
    assert guard.check_stop({}, tmp_path) == (0, "")


# Covers F-0001.R5 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_stop_runs_make_check_only_when_code_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 0, " M src/demo/x.py\0?? notes.md\0", "")
        return subprocess.CompletedProcess(cmd, 1, "ruff failed\nE501 line too long\n", "")

    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    monkeypatch.delenv("RUMBO_GUARD_SKIP_CHECK", raising=False)
    code, message = guard.check_stop({}, tmp_path)
    assert code == guard.BLOCK
    assert "E501" in message and calls[-1] == ["make", "check-brief"]
    # A second Stop in the same turn never loops.
    assert guard.check_stop({"stop_hook_active": True}, tmp_path) == (0, "")
    # Nothing changed: nothing to run.
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 0, "?? notes.md\0", ""),
    )
    assert guard.check_stop({}, tmp_path) == (0, "")


FAKE_RUNNER = """\
import os
import sys
from pathlib import Path

Path("ran.txt").write_text(" ".join(sys.argv[1:]), encoding="utf-8")
code = int(os.environ["FAKE_CHECK_EXIT"])
print("ok   lint")
print(f"rumbo check: fake exit {code}")
print("last line, on stderr", file=sys.stderr)
sys.exit(code)
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project with a fake scripts/rumbo_check.py and one changed code file."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "rumbo_check.py").write_text(FAKE_RUNNER, encoding="utf-8")
    monkeypatch.setattr(guard, "changed_files", lambda _root: ["src/demo/x.py", "notes.md"])
    monkeypatch.delenv("RUMBO_GUARD_SKIP_CHECK", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


def _stop(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object] | None = None) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload or {})))
    return guard.main(["stop"])


# Covers F-0001.R5 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_stop_passes_when_the_runner_is_green(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FAKE_CHECK_EXIT", "0")
    assert _stop(monkeypatch) == 0
    out, err = capsys.readouterr()
    assert out == "" and err == ""
    assert (project / "ran.txt").read_text(encoding="utf-8") == "--brief"


# Covers F-0001.R5 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_stop_blocks_with_the_tail_when_the_runner_is_red(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FAKE_CHECK_EXIT", "1")
    assert _stop(monkeypatch) == guard.BLOCK
    out, err = capsys.readouterr()
    assert out == ""
    assert "rumbo check is red after changing 1 file(s)" in err
    assert ".rumbo-check.log" in err
    assert "rumbo check: fake exit 1" in err and "last line, on stderr" in err
    assert "repair" not in err  # a red check is the agent's to fix


# Covers F-0001.R6 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
@pytest.mark.parametrize("exit_code", ["3", "4"])
def test_stop_reports_a_broken_environment_to_the_human(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exit_code: str,
) -> None:
    monkeypatch.setenv("FAKE_CHECK_EXIT", exit_code)
    assert _stop(monkeypatch) == 0
    out, err = capsys.readouterr()
    assert err == ""
    hook = json.loads(out)  # the JSON object is the only thing on stdout
    assert hook["decision"] == "block"
    reason, human = hook["reason"], hook["systemMessage"]
    assert "environment is broken" in reason
    assert "do NOT try to repair it" in reason and "no installs, no config edits" in reason
    assert "Report the failing step" in reason and "then stop" in reason
    assert f"rumbo check: fake exit {exit_code}" in reason and "last line, on stderr" in reason
    assert "environment is broken" in human and f"exit {exit_code}" in human
    assert "fake exit" not in human  # the human gets one or two lines, not the log


# Covers F-0001.R6 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_stop_without_runner_or_make_asks_for_rumbo_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []

    def no_make(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        raise FileNotFoundError("make")

    monkeypatch.setattr(guard, "changed_files", lambda _root: ["src/demo/x.py"])
    monkeypatch.setattr(guard.subprocess, "run", no_make)
    monkeypatch.delenv("RUMBO_GUARD_SKIP_CHECK", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert _stop(monkeypatch) == 0
    out, err = capsys.readouterr()
    # The guard asks git first (G10, ADR-0024); make is the only other command, run once.
    assert err == "" and [c for c in calls if c[0] != "git"] == [["make", "check-brief"]]
    hook = json.loads(out)
    assert hook["decision"] == "block"
    assert "rumbo update" in hook["reason"] and "rumbo update" in hook["systemMessage"]
    assert "scripts/rumbo_check.py is missing" in hook["reason"]
    assert "do NOT try to repair it" in hook["reason"]


def test_stop_hook_active_and_skip_env_short_circuit_the_runner(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FAKE_CHECK_EXIT", "3")
    assert _stop(monkeypatch, {"stop_hook_active": True}) == 0
    monkeypatch.setenv("RUMBO_GUARD_SKIP_CHECK", "1")
    assert _stop(monkeypatch) == 0
    out, err = capsys.readouterr()
    assert out == "" and err == ""
    assert not (project / "ran.txt").exists()


def test_main_reads_the_event_from_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    edit = _edit(str(tmp_path / "Makefile"))
    payload = {"cwd": str(tmp_path), "tool_name": "Edit", "tool_input": edit}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert guard.main(["pre-tool"]) == guard.BLOCK
    assert "protected" in capsys.readouterr().err
    payload = {"cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": "make check"}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert guard.main(["pre-tool"]) == 0
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 0, " M infra/Dockerfile\0", ""),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert guard.main(["post-tool"]) == guard.BLOCK
    assert "infra/Dockerfile" in capsys.readouterr().err


def test_generated_project_wires_the_guard() -> None:
    plan = plan_files(Config(name="Demo", package="demo", llm=False, rigor="prototype"))
    assert Path(str(plan["scripts/rumbo_guard.py"])).name == "guard.py"
    settings_path = Path(str(plan[".claude/settings.json"]))
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    commands = [
        h["command"]
        for event in settings["hooks"].values()
        for entry in event
        for h in entry["hooks"]
    ]
    assert any('rumbo_guard.py" pre-tool' in c for c in commands)
    assert any('rumbo_guard.py" post-tool' in c for c in commands)
    assert any('rumbo_guard.py" stop' in c for c in commands)
    assert all("$CLAUDE_PROJECT_DIR" in c for c in commands)
    assert settings["hooks"]["PostToolUse"][0]["matcher"] == "Bash|PowerShell"
    assert "Bash(make check)" in settings["permissions"]["allow"]
    assert "Bash(uv run python scripts/rumbo_check.py *)" in settings["permissions"]["allow"]
    assert "Bash(rumbo check *)" in settings["permissions"]["allow"]
    assert "Edit(./.claude/**)" in settings["permissions"]["deny"]
    # The threat model's lethal-trifecta legs: no secrets read, no ad-hoc network calls.
    for rule in ("Read(./.env)", "Bash(curl *)", "Bash(wget *)"):
        assert rule in settings["permissions"]["deny"], rule


# ------------------------------------------------------------- git hooks (ADR-0008)
def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def _quiet_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No agent marker (this suite may itself run inside an agent shell) and no user git config."""
    for name in guard.DEFAULT_AGENT_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp git repository with one commit: a protected file and a document with the mark."""
    _quiet_env(monkeypatch, tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@x")
    _git(root, "config", "user.name", "t")
    (root / "Makefile").write_text("check: ; echo\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "brief.md").write_text(
        f"{guard.TEMPLATE_MARK} -->\n# Brief\n", encoding="utf-8"
    )
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


# Covers F-0001.R9 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
@pytest.mark.parametrize("marker", guard.DEFAULT_AGENT_ENV)
def test_actor_is_an_agent_when_a_marker_is_set(
    repo: Path, monkeypatch: pytest.MonkeyPatch, marker: str
) -> None:
    assert guard.actor(repo) == "unknown"
    monkeypatch.setenv(marker, "1")
    assert guard.actor(repo) == "agent"
    _git(repo, "config", "rumbo.human", "true")
    assert guard.actor(repo) == "agent"  # the marker wins over the clone's declaration


# Covers F-0001.R9 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_actor_is_the_human_who_declared_rumbo_human(repo: Path) -> None:
    assert guard.actor(repo) == "unknown"
    _git(repo, "config", "rumbo.human", "true")
    assert guard.actor(repo) == "human"
    _git(repo, "config", "rumbo.human", "false")
    assert guard.actor(repo) == "unknown"


# Covers F-0001.R9 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_default_agent_markers_have_a_source() -> None:
    """ADR-0021: no marker is written from memory; each default names where it was confirmed."""
    assert tuple(guard.AGENT_ENV_SOURCES) == guard.DEFAULT_AGENT_ENV
    assert all(url.startswith("https://") for url in guard.AGENT_ENV_SOURCES.values())
    verified = {"CLAUDECODE", "GEMINI_CLI", "CURSOR_AGENT", "CODEX_SANDBOX_NETWORK_DISABLED"}
    verified |= {"CODEX_THREAD_ID", "CODEX_CI", "ROO_ACTIVE", "CLINE_ACTIVE"}
    assert set(guard.DEFAULT_AGENT_ENV) == verified


# Covers F-0001.R9 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_agent_markers_come_from_pyproject_when_declared(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert guard.agent_env_names(repo) == guard.DEFAULT_AGENT_ENV  # no pyproject.toml
    # ADR-0026: harness settings are read from HEAD, so each version is the human's commit.
    _commit_pyproject(repo, '[tool.rumbo]\nagent_env = ["MY_AGENT"]\n')
    assert guard.agent_env_names(repo) == ("MY_AGENT",)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert guard.actor(repo) == "unknown"  # the list replaces the default, it does not extend it
    monkeypatch.setenv("MY_AGENT", "1")
    assert guard.actor(repo) == "agent"
    _commit_pyproject(repo, '[tool.rumbo]\nagent_env = "MY_AGENT"\n')
    assert guard.agent_env_names(repo) == guard.DEFAULT_AGENT_ENV  # not a list: ignored
    _commit_pyproject(repo, "not toml [")
    assert guard.agent_env_names(repo) == guard.DEFAULT_AGENT_ENV


# Covers F-0001.R7 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_pre_commit_refuses_a_staged_protected_file_from_an_agent(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "Makefile").write_text("check: ; echo changed\n", encoding="utf-8")
    _git(repo, "add", "Makefile")
    monkeypatch.setenv("CLAUDECODE", "1")
    reason = guard.check_pre_commit(repo) or ""
    assert "Makefile" in reason and "protected" in reason and "ask the human" in reason
    assert "rumbo.human" not in reason  # a known agent is not told how to declare itself human
    monkeypatch.chdir(repo)
    assert guard.main(["pre-commit"]) == guard.GIT_BLOCK == 1
    err = capsys.readouterr().err
    assert err.startswith("blocked by scripts/rumbo_guard.py:") and "Makefile" in err
    # An unknown actor is treated as an agent and learns how a human declares themself.
    monkeypatch.delenv("CLAUDECODE")
    reason = guard.check_pre_commit(repo) or ""
    assert "Makefile" in reason and "git config rumbo.human true" in reason
    # The human's own commit goes through, silently.
    _git(repo, "config", "rumbo.human", "true")
    assert guard.check_pre_commit(repo) is None
    assert guard.main(["pre-commit"]) == 0
    assert capsys.readouterr() == ("", "")


# Covers F-0001.R7 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_pre_commit_allows_uv_writes_and_lists_every_other_protected_file(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_CLI", "1")
    # ADR-0019: a uv write is a change to the dependency tables of a pyproject.toml that HEAD
    # already has (a new pyproject.toml is a protected change), so the project commits one first.
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-qm", "chore: the project's pyproject.toml")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["httpx"]\n', encoding="utf-8"
    )
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    assert guard.check_pre_commit(repo) is None
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("on: push\n", encoding="utf-8")
    (repo / ".env").write_text("KEY=v\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "rumbo_guard.py").write_text("# tampered\n", encoding="utf-8")
    _git(repo, "add", "-A")
    reason = guard.check_pre_commit(repo) or ""
    listed = reason.split("protected file(s) staged: ", 1)[1].split(". They are", 1)[0]
    assert listed == ".env, .github/workflows/ci.yml, scripts/rumbo_guard.py"


# Covers F-0001.R7 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_pre_commit_refuses_removing_the_template_mark(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_AGENT", "1")
    brief = repo / "docs" / "brief.md"
    brief.write_text(f"{guard.TEMPLATE_MARK} -->\n# Brief\nmore\n", encoding="utf-8")
    _git(repo, "add", "docs")
    assert guard.check_pre_commit(repo) is None  # editing under the mark is fine
    brief.write_text("# Brief\nfinished\n", encoding="utf-8")
    _git(repo, "add", "docs")
    reason = guard.check_pre_commit(repo) or ""
    assert reason.startswith("docs/brief.md:") and "human" in reason
    # Only HEAD and the index count: restoring the mark in the working tree without staging
    # does not help, staging it does.
    brief.write_text(f"{guard.TEMPLATE_MARK} -->\n# Brief\nfinished\n", encoding="utf-8")
    assert guard.check_pre_commit(repo)
    _git(repo, "add", "docs")
    assert guard.check_pre_commit(repo) is None
    # A new document without the mark and a deleted document are not "removing the mark".
    (repo / "docs" / "new.md").write_text("# New\n", encoding="utf-8")
    _git(repo, "add", "docs")
    _git(repo, "rm", "-qf", "docs/brief.md")
    assert guard.check_pre_commit(repo) is None


# Covers F-0001.R7 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_pre_commit_exempts_the_root_commit_and_guards_from_the_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet_env(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDECODE", "1")
    root = tmp_path / "fresh"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _git(root, "add", "notes.md")
    assert guard.check_pre_commit(root) is None
    # The root commit is the harness itself: a protected file in it is not an alteration.
    (root / "Makefile").write_text("check: ; echo\n", encoding="utf-8")
    _git(root, "add", "Makefile")
    assert guard.check_pre_commit(root) is None
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "commit", "-qm", "chore: initial RUMBO harness")
    # From the second commit on, the same file is refused to an agent.
    (root / "Makefile").write_text("check: ; echo changed\n", encoding="utf-8")
    _git(root, "add", "Makefile")
    assert "Makefile" in (guard.check_pre_commit(root) or "")


# Covers F-0001.R10 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_pre_commit_passes_silently_with_nothing_staged(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    (repo / "Makefile").write_text("unstaged change\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    assert guard.main(["pre-commit"]) == 0  # what `pre-commit run --all-files` sees in CI
    assert capsys.readouterr() == ("", "")


# Covers F-0001.R8 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_commit_msg_refuses_the_approval_mark_from_an_agent(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("test: widen the cases [tests-change-approved]\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_SANDBOX_NETWORK_DISABLED", "1")
    reason = guard.check_commit_msg(repo, str(message)) or ""
    assert "only the human writes [tests-change-approved]" in reason
    monkeypatch.chdir(repo)
    assert guard.main(["commit-msg", str(message)]) == guard.GIT_BLOCK
    assert "only the human writes" in capsys.readouterr().err
    message.write_text("feat: ordinary message\n", encoding="utf-8")
    assert guard.main(["commit-msg", str(message)]) == 0
    assert capsys.readouterr() == ("", "")
    message.write_text("x [tests-change-approved]\n", encoding="utf-8")
    monkeypatch.delenv("CODEX_SANDBOX_NETWORK_DISABLED")
    assert "git config rumbo.human true" in (guard.check_commit_msg(repo, str(message)) or "")
    _git(repo, "config", "rumbo.human", "true")
    assert guard.check_commit_msg(repo, str(message)) is None
    # A missing message file is a wiring error, reported to the agent rather than ignored.
    monkeypatch.setenv("CLAUDECODE", "1")
    assert guard.main(["commit-msg"]) == guard.GIT_BLOCK
    assert "usage" in capsys.readouterr().err


# Covers F-0001.R11 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_git_hooks_without_git_never_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_git(cmd: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError("git")

    _quiet_env(monkeypatch, tmp_path)
    monkeypatch.setattr(guard.subprocess, "run", no_git)
    assert guard.actor(tmp_path) == "unknown"
    assert guard.staged_files(tmp_path) == []
    assert guard.check_pre_commit(tmp_path) is None


# Covers F-0001.R12 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_pre_tool_decides_within_the_latency_budget(tmp_path: Path) -> None:
    """PreToolUse is pure decision logic: no network, no subprocess, p95 under 100 ms."""
    import time

    samples: list[float] = []
    for n in range(60):
        start = time.perf_counter()
        guard.check_edit("Edit", {"file_path": f"src/app_{n}.py"}, tmp_path)
        guard.check_edit("Write", {"file_path": "Makefile", "content": "x"}, tmp_path)
        guard.check_bash("uv run pytest -q tests/unit")
        guard.check_bash("git commit --no-verify -m x")
        samples.append(time.perf_counter() - start)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    assert p95 < 0.1, f"p95 of four PreToolUse decisions took {p95 * 1000:.1f} ms"


# ---------------------------------------------------------------- signed documents (ADR-0017)
def _commit_documents(repo: Path) -> None:
    """A signed spec, a signed ADR, a spec still under the mark and both 0000 templates."""
    marked = f"{guard.TEMPLATE_MARK} -->\n# Draft\n"
    for rel, text in {
        "specs/0001-query.md": "# F-0001: Query\n- R1: The system shall answer.\n",
        "specs/0002-draft.md": marked,
        "specs/0000-template.md": marked,
        "docs/adr/0001-storage.md": "# ADR-0001: Storage\nStatus: accepted\n",
        "docs/adr/0000-template.md": marked,
    }.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "docs: the human signs a spec and an ADR")


# Covers F-0001.R14 and F-0001.R15 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_editing_a_signed_spec_or_adr_is_blocked_and_drafting_is_not(repo: Path) -> None:
    _commit_documents(repo)
    for rel in ("specs/0001-query.md", "docs/adr/0001-storage.md"):
        reason = guard.check_edit("Edit", _edit(rel), repo) or ""
        assert rel in reason and "signed by the human" in reason and "supersedes" in reason
        assert guard.check_edit("Write", {"file_path": rel, "content": "x"}, repo)
    # Drafting stays free: a document under the mark, a new one, and the 0000 templates.
    for rel in (
        "specs/0002-draft.md",
        "specs/0003-new.md",
        "docs/adr/0002-new.md",
        "specs/0000-template.md",
        "docs/adr/0000-template.md",
        "docs/brief.md",
    ):
        # Not the default "a" -> "b": simulated (G13), that edit breaks the word "template".
        assert guard.check_edit("Edit", _edit(rel, "# ", "## "), repo) is None, rel


# Covers F-0001.R13 and F-0001.R14 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_post_bash_reports_a_removed_mark_and_a_changed_signed_document(repo: Path) -> None:
    _commit_documents(repo)
    assert guard.check_post_bash(repo) is None
    # A new document and an edit under the mark are not reported.
    (repo / "specs" / "0003-new.md").write_text("# New\n", encoding="utf-8")
    (repo / "specs" / "0002-draft.md").write_text(
        f"{guard.TEMPLATE_MARK} -->\n# Draft\nmore\n", encoding="utf-8"
    )
    assert guard.check_post_bash(repo) is None
    # `sed -i 1d`: the mark that was in HEAD is gone from the working tree.
    (repo / "docs" / "brief.md").write_text("# Brief\n", encoding="utf-8")
    reason = guard.check_post_bash(repo) or ""
    assert "docs/brief.md" in reason and "template mark" in reason
    assert "specs/0002-draft.md" not in reason
    _git(repo, "checkout", "--", "docs/brief.md")
    # A signed spec rewritten and a signed ADR deleted through the shell.
    (repo / "specs" / "0001-query.md").write_text("# F-0001: Query\n", encoding="utf-8")
    (repo / "docs" / "adr" / "0001-storage.md").unlink()
    reason = guard.check_post_bash(repo) or ""
    assert "specs/0001-query.md" in reason and "docs/adr/0001-storage.md" in reason
    assert "signed" in reason
    # Stop refuses for the same reason before running the check.
    code, message = guard.check_stop({}, repo)
    assert code == guard.BLOCK and "signed" in message


# Covers F-0001.R14 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_pre_commit_refuses_a_staged_change_to_a_signed_document(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _commit_documents(repo)
    monkeypatch.setenv("GEMINI_CLI", "1")
    (repo / "specs" / "0003-new.md").write_text(
        f"{guard.TEMPLATE_MARK} -->\n# New\n", encoding="utf-8"
    )
    (repo / "specs" / "0002-draft.md").write_text(
        f"{guard.TEMPLATE_MARK} -->\n# Draft\nmore\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    assert guard.check_pre_commit(repo) is None  # drafting is free
    (repo / "docs" / "adr" / "0001-storage.md").write_text("# rewritten\n", encoding="utf-8")
    _git(repo, "add", "-A")
    reason = guard.check_pre_commit(repo) or ""
    assert "docs/adr/0001-storage.md" in reason and "signed" in reason
    # The declared human edits a signed document by hand and commits it.
    monkeypatch.delenv("GEMINI_CLI")
    _git(repo, "config", "rumbo.human", "true")
    assert guard.check_pre_commit(repo) is None


# ------------------------------------------------- the tables of pyproject.toml (ADR-0019)
PYPROJECT_HEAD = """\
[project]
name = "demo"
version = "0.1.0"
dependencies = ["httpx"]

[build-system]
requires = ["hatchling"]

[tool.rumbo.checks]
tests = "uv run pytest -q"

[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
addopts = "--cov-fail-under=80"
"""
# What `uv add`, `uv add --optional`, `uv add --dev` and a git source leave behind, reformatted
# and with a comment: only the four free keys differ from PYPROJECT_HEAD.
UV_ADD = (
    PYPROJECT_HEAD.replace('["httpx"]', '[\n    "httpx",\n    "rich",\n]')
    + """
# added by uv
[project.optional-dependencies]
cli = ["typer"]

[dependency-groups]
dev = ["pytest"]

[tool.uv]
package = true

[tool.uv.sources]
rich = { git = "https://example.com/rich.git" }
"""
)
# One change outside the dependency tables each: the dotted path the guard names, (old, new).
OUTSIDE: dict[str, tuple[str, str]] = {
    "tool.rumbo": ('tests = "uv run pytest -q"', 'tests = "true"'),
    "tool.ruff": ("line-length = 100", "line-length = 320"),
    "tool.pytest": ("--cov-fail-under=80", "--cov-fail-under=0"),
    "project.version": ('version = "0.1.0"', 'version = "9.9.9"'),
}


def _commit_pyproject(repo: Path, text: str = PYPROJECT_HEAD) -> Path:
    """The human's commit of pyproject.toml (the tmp repository has no hooks installed)."""
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(text, encoding="utf-8")
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-qm", "chore: the human's pyproject.toml")
    return pyproject


# Covers F-0001.R4 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_pyproject_may_change_only_in_its_dependency_tables(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pyproject = _commit_pyproject(repo)
    assert guard.check_post_bash(repo) is None
    # `uv add`: the dependency tables and uv.lock change, nothing is reported.
    pyproject.write_text(UV_ADD, encoding="utf-8")
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    assert guard.pyproject_change(repo, staged=False) == []
    assert guard.check_post_bash(repo) is None
    assert guard.changed_protected_files(repo) == []
    for path, (old, new) in OUTSIDE.items():
        assert old in PYPROJECT_HEAD, path
        # Alone, and mixed with the uv write: the table outside the allow list is named.
        for text in (PYPROJECT_HEAD, UV_ADD):
            pyproject.write_text(text.replace(old, new), encoding="utf-8")
            assert guard.pyproject_change(repo, staged=False) == [path]
            reason = guard.check_post_bash(repo) or ""
            assert f"outside its dependency tables: {path}. " in reason, path
            assert "protected" in reason and "restore" in reason and "ask the human" in reason
            assert "project.dependencies" in reason and "tool.uv" in reason  # what may change
    assert guard.changed_protected_files(repo) == ["pyproject.toml"]
    # Stop refuses for the same reason before running the check.
    code, message = guard.check_stop({}, repo)
    assert code == guard.BLOCK and "project.version" in message
    # An allow list, not a deny list: tables nobody listed, a removed table, the build system.
    everything = UV_ADD.replace("[tool.ruff]\nline-length = 100\n", "")
    for old, new in OUTSIDE.values():
        everything = everything.replace(old, new)
    everything = everything.replace('["hatchling"]', '["setuptools"]')
    pyproject.write_text(everything + "\n[tool.coverage.report]\nfail_under = 0\n", "utf-8")
    assert guard.pyproject_change(repo, staged=False) == [
        "build-system.requires",
        "project.version",
        "tool.coverage",
        "tool.pytest",
        "tool.ruff",
        "tool.rumbo",
    ]
    # A pyproject.toml without a [tool] table gains [tool.uv]: still only a dependency change.
    _git(repo, "checkout", "--", "pyproject.toml")
    bare = _commit_pyproject(repo, '[project]\nname = "demo"\n')
    bare.write_text('[project]\nname = "demo"\n\n[tool.uv]\npackage = true\n', encoding="utf-8")
    assert guard.check_post_bash(repo) is None
    bare.write_text('[project]\nname = "demo"\n\n[tool.ruff]\n', encoding="utf-8")
    assert guard.pyproject_change(repo, staged=False) == ["tool.ruff"]

    # The extra git and TOML work is paid only when pyproject.toml is among the changed files.
    def never(_root: Path, _staged: bool) -> list[str]:
        raise AssertionError("pyproject.toml did not change")

    monkeypatch.setattr(guard, "pyproject_change", never)
    monkeypatch.setattr(guard, "changed_files", lambda _root: ["src/demo/x.py", "uv.lock"])
    assert guard.check_post_bash(repo) is None


# Covers F-0001.R7 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_pre_commit_refuses_pyproject_changes_outside_the_dependency_tables(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pyproject = _commit_pyproject(repo)
    monkeypatch.setenv("CURSOR_AGENT", "1")
    # `uv add` and the commit of its result: the agent's, with no approval step.
    pyproject.write_text(UV_ADD, encoding="utf-8")
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    assert guard.pyproject_change(repo, staged=True) == []
    assert guard.check_pre_commit(repo) is None
    for path, (old, new) in OUTSIDE.items():
        # Alone, and mixed with the uv write: refused, and the table is named.
        for text in (PYPROJECT_HEAD, UV_ADD):
            pyproject.write_text(text.replace(old, new), encoding="utf-8")
            _git(repo, "add", "pyproject.toml")
            assert guard.pyproject_change(repo, staged=True) == [path]
            reason = guard.check_pre_commit(repo) or ""
            assert f"outside its dependency tables: {path}. " in reason, path
            assert "protected" in reason and "ask the human" in reason
            assert "rumbo.human" not in reason
    # Only HEAD and the index count: restoring the working tree without staging does not
    # help, staging it does; and a change that is not staged is not this commit's.
    pyproject.write_text(UV_ADD, encoding="utf-8")
    assert "project.version" in (guard.check_pre_commit(repo) or "")
    _git(repo, "add", "pyproject.toml")
    assert guard.check_pre_commit(repo) is None
    pyproject.write_text(UV_ADD.replace("line-length = 100", "line-length = 320"), "utf-8")
    assert guard.check_pre_commit(repo) is None
    # With another protected file, both are listed.
    (repo / "Makefile").write_text("check: ; echo changed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    reason = guard.check_pre_commit(repo) or ""
    assert "protected file(s) staged: Makefile. " in reason
    assert "outside its dependency tables: tool.ruff. " in reason
    _git(repo, "restore", "--staged", "Makefile")
    # An unknown actor is refused too and learns how a human declares themself; the declared
    # human (rumbo.human true, no marker) commits any table.
    monkeypatch.delenv("CURSOR_AGENT")
    reason = guard.check_pre_commit(repo) or ""
    assert "tool.ruff" in reason and "git config rumbo.human true" in reason
    _git(repo, "config", "rumbo.human", "true")
    assert guard.check_pre_commit(repo) is None
    # The root commit is the harness itself: its pyproject.toml has nothing to be compared with.
    monkeypatch.setenv("CURSOR_AGENT", "1")
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _git(fresh, "init", "-q", "-b", "main")
    (fresh / "pyproject.toml").write_text(PYPROJECT_HEAD, encoding="utf-8")
    _git(fresh, "add", "pyproject.toml")
    assert guard.pyproject_change(fresh, staged=True) == []
    assert guard.check_pre_commit(fresh) is None
    _git(fresh, "config", "user.email", "t@example.com")
    _git(fresh, "config", "user.name", "t")
    _git(fresh, "commit", "-qm", "chore: initial RUMBO harness")
    # From the second commit on, the tables are guarded.
    (fresh / "pyproject.toml").write_text(
        PYPROJECT_HEAD.replace("--cov-fail-under=80", "--cov-fail-under=0"), encoding="utf-8"
    )
    _git(fresh, "add", "pyproject.toml")
    assert "tool.pytest" in (guard.check_pre_commit(fresh) or "")


# Covers F-0001.R4 and F-0001.R7 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_an_unparseable_or_new_pyproject_is_a_protected_change(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    new, broken, deleted = (
        "pyproject.toml (new file)",
        "pyproject.toml (cannot be parsed)",
        "pyproject.toml (deleted)",
    )

    def both_layers(label: str) -> None:
        """The working tree after a shell command, then the same content staged by an agent."""
        assert guard.pyproject_change(repo, staged=False) == [label]
        reason = guard.check_post_bash(repo) or ""
        assert label in reason and "ask the human" in reason
        _git(repo, "add", "-A")
        assert guard.pyproject_change(repo, staged=True) == [label]
        reason = guard.check_pre_commit(repo) or ""
        assert label in reason and "ask the human" in reason

    # New: HEAD exists and has no pyproject.toml, so there is nothing to compare with.
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(UV_ADD, encoding="utf-8")
    both_layers(new)
    _git(repo, "commit", "-qm", "chore: the human's pyproject.toml")
    assert guard.check_post_bash(repo) is None
    # Deleted: the whole verification contract is gone.
    pyproject.unlink()
    both_layers(deleted)
    _git(repo, "reset", "-q", "--hard", "HEAD")
    # The current version does not parse, even if only a dependency line broke it.
    pyproject.write_text(UV_ADD.replace('"rich",', '"rich" "textual",'), encoding="utf-8")
    both_layers(broken)
    # The version in HEAD does not parse: the guard cannot tell what a repair changes.
    _git(repo, "commit", "-qm", "chore: a broken pyproject.toml")
    pyproject.write_text(UV_ADD, encoding="utf-8")
    both_layers(broken)
    # None of it binds the declared human.
    monkeypatch.delenv("CLAUDECODE")
    _git(repo, "config", "rumbo.human", "true")
    assert guard.check_pre_commit(repo) is None

    # Without git there is no HEAD and no commit to protect: the action goes through (R11).
    def no_git(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("git")

    monkeypatch.setattr(guard.subprocess, "run", no_git)
    assert guard.pyproject_change(repo, staged=False) == []
    assert guard.pyproject_change(repo, staged=True) == []


# ------------------------------------------- the audit before v0.4.1 (G1..G16, V6, P2)
def _hook(
    monkeypatch: pytest.MonkeyPatch,
    event: str,
    payload: object,
    raw: bytes | None = None,
) -> int:
    """Runs a Claude Code hook the way Windows hands it the event: UTF-8 bytes on a stdin whose
    text layer decodes as cp1252."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if raw is None else raw
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(data), encoding="cp1252"))
    return guard.main([event])


def _template_settings() -> dict[str, Any]:
    plan = plan_files(Config(name="Demo", package="demo", llm=False, rigor="prototype"))
    return json.loads(Path(str(plan[".claude/settings.json"])).read_text(encoding="utf-8"))


def _bypass_commit(repo: Path, message: str = "x") -> str:
    """Commits the whole working tree with plumbing: no hook runs, as with `commit-tree` plus
    `update-ref`, a cherry-picked stash or a disabled hook."""
    _git(repo, "add", "-A")
    tree = _git(repo, "write-tree").strip()
    sha = _git(repo, "commit-tree", tree, "-p", "HEAD", "-m", message).strip()
    _git(repo, "update-ref", "HEAD", sha)
    return sha


def _clean(repo: Path) -> bool:
    return _git(repo, "status", "--porcelain") == ""


# Covers F-0001.R3 and F-0001.R4 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_every_shell_tool_is_judged_like_bash(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """G1: a command sent through the PowerShell tool meets the same rules as one sent to Bash."""
    assert guard.SHELL_TOOLS == ("Bash", "PowerShell")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    for tool in guard.SHELL_TOOLS:
        payload = {"tool_name": tool, "tool_input": {"command": "git commit --no-verify -m x"}}
        assert _hook(monkeypatch, "pre-tool", payload) == guard.BLOCK, tool
        assert "--no-verify" in capsys.readouterr().err
    (repo / "Makefile").write_text("check: ; true\n", encoding="utf-8")
    for tool in guard.SHELL_TOOLS:
        payload = {"tool_name": tool, "tool_input": {"command": "make check"}}
        assert _hook(monkeypatch, "post-tool", payload) == guard.BLOCK, tool
        assert "Makefile" in capsys.readouterr().err
    hooks = _template_settings()["hooks"]
    for event in ("PreToolUse", "PostToolUse"):
        assert set(guard.SHELL_TOOLS) <= set(hooks[event][0]["matcher"].split("|")), event


# Covers F-0001.R3 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
@pytest.mark.parametrize(
    "command",
    [
        # G1: PowerShell spellings on a protected target.
        "Set-Content -Path Makefile -Value 'check: ; true'",
        "'x' | Out-File pyproject.toml",
        "Add-Content .github\\workflows\\ci.yml 'x'",
        "Remove-Item .git\\hooks\\pre-commit",
        "Remove-Item -Force .claude\\settings.json",
        "'x' > .\\scripts\\rumbo_guard.py",
        "$env:SKIP = 'rumbo-guard'; git commit -m x",
        "$env:GIT_CONFIG_COUNT = 1; git commit -m x",
        "Remove-Item -Recurse -Force .",
        # G2: the files that shadow pyproject.toml.
        "echo '[lint]' > ruff.toml",
        "echo '[pytest]' >> pytest.ini",
        "printf x | tee tests/ruff.toml",
        "sed -i s/a/b/ setup.cfg",
        # G3: the git directory.
        "echo 'exit 0' > .git/hooks/pre-commit",
        "printf '[rumbo]\\n\\thuman = true\\n' >> .git/config",
        "mv .git/hooks/pre-commit .git/hooks/pre-commit.off",
        "chmod -x .git/hooks/pre-commit .git/hooks/commit-msg",
        "unlink .git/hooks/pre-commit",
        "rm -rf .git",
        # G4: spellings of the hook bypass.
        "git commit -m x --no-verify;echo done",
        "(cd . && git commit -m x --no-verify)",
        "git commit -m x --no-verify&& git push",
        'git commit -m x "--no-verify"',
        "git commit -m x '--no-verify'",
        "git commit -m x --no-verif",
        "git commit -m x --no-veri",
        "git commit -m x -n; echo done",
        "git commit -m x -n&&echo",
        "git commit -m x '-n'",
        "git --git-dir .git commit -n -m x",
        "git -c alias.ci='commit --no-verify' ci -m x",
        "git -c alias.ci='commit -n' ci -m x",
        "git config alias.ci 'commit -n'",
        "git -c core.hookspath=/dev/null commit -m x",
        "git config core.hookspath /dev/null",
        "git config CORE.HOOKSPATH /dev/null",
        "git --config-env=core.hooksPath=NOWHERE commit -m x",
        "git -c include.path=/tmp/evil.cfg commit -m x",
        "git config RUMBO.HUMAN true",
        "git config Rumbo.Human true",
        "git config set rumbo.human true",
        "git config rumbo.human true get",
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/x git commit -m x",
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=rumbo.human GIT_CONFIG_VALUE_0=true git commit -m x",
        "GIT_CONFIG_GLOBAL=/tmp/evil.cfg git commit -m x",
        "export GIT_CONFIG_SYSTEM=/tmp/evil.cfg",
        "export SKIP=rumbo-guard && git commit -m x",
        "export SKIP=rumbo-guard\ngit commit -m x",
        # G16: the remaining spellings.
        "git -C . reset --hard HEAD~1",
        "git reset -q --hard",
        "git reset HEAD~3 --hard",
        "git reset --har",
        "git -C . push --force origin main",
        "git -c x=y push -f",
        "git push -uf origin main",
        "git push --force-with-lease=main origin main",
        "git push origin main --force;",
        "git push --mirror",
        "rm -rf ./",
        "rm -rf ./*",
        "rm -rf build .",
        'rm -rf "$HOME"',
        "rm -rf ~/*",
        "rm -rf /*",
        "rm -rf ..",
        "rm -rf $(pwd)",
        'echo x >"Makefile"',
        "echo x > 'pyproject.toml'",
        "echo x > MAKEFILE",
        "echo K=1 > .ENV",
        "sed -e s/a/b/ -i Makefile",
        "sed --in-place s/a/b/ Makefile",
        "perl -pi -e s/a/b/ Makefile",
        "tee 'Makefile' < x",
        # V6: the index is the project's configuration; only the human passes another one.
        "rumbo verify-dep httpx --index https://evil.example/simple",
        "rumbo verify-dep --index=https://evil.example/simple httpx",
        "uv run rumbo verify-dep httpx --ind https://evil.example/simple",
        "UV_INDEX_URL=https://evil.example/simple rumbo verify-dep httpx",
        # P2: `git diff` is pre-approved, and with --no-index it prints any file.
        "git diff --no-index /dev/null .env",
        "git -C . diff --no-index -- /dev/null .env.local",
        "git diff /tmp/empty .env",
        "git diff -- ../outside.txt './.env.production'",
    ],
)
def test_bypass_spellings_are_blocked(command: str) -> None:
    assert guard.check_bash(command)


# Covers F-0001.R10 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
@pytest.mark.parametrize(
    "command",
    [
        # G16: commands that only look like a rule.
        "git log --grep commit -n 5",
        'git log --grep="commit" --oneline -n 3',
        "git show --stat commit -n 1",
        "git log --grep core.hooksPath",
        "git grep -n rumbo.human -- docs",
        "git config --get rumbo.human",
        "git config --get core.hooksPath",
        "git config get rumbo.human",
        "git config --list",
        'git commit -m "docs: explain --no-verify"',
        "git commit --no-verbose -m x",
        "git merge --no-verify-signatures feature",
        "git push origin feature && echo ' -f '",
        "git push --follow-tags origin feature",
        "SKIP=slow pytest; git status",
        "git rm -r --cached .",
        "git reset --soft HEAD~1",
        "git diff --no-indent-heuristic",
        "git diff --stat main...HEAD",
        "git diff HEAD~1 -- .env.example .envrc src/environment.py",
        "sed -i s/a/b/ src/x.py && cat Makefile",
        "tee out.log < Makefile",
        "cat .git/config",
        "ls .git/hooks",
        "cat .env.example > /dev/null",
        "diff <(echo a) Makefile > .envrc.diff",
        "echo x > .envrc",
        "echo x > .gitignore",
        "echo x >> .gitattributes",
        "ls > infra_listing.txt",
        "uv run pytest -k 'not slow' > .github_out.txt",
        "echo x > docs/ruff.toml.md",
        "rumbo verify-dep httpx rich",
        "uv run rumbo verify-dep index-helper",
        "Get-Content Makefile",
        "Remove-Item -Recurse -Force build",
        "Set-Content notes.txt 'x'",
    ],
)
def test_commands_that_only_look_dangerous_are_allowed(command: str) -> None:
    assert guard.check_bash(command) is None


SHADOW_FILES = [
    "ruff.toml",
    ".ruff.toml",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "mypy.ini",
    ".mypy.ini",
    "pyrightconfig.json",
    ".coveragerc",
    ".importlinter",
    "uv.toml",
    "GNUmakefile",
    "makefile",
    "tests/ruff.toml",
    "src/demo/pytest.ini",
    "Ruff.TOML",
]


# Covers F-0001.R1 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
@pytest.mark.parametrize("rel", SHADOW_FILES)
def test_files_that_shadow_pyproject_are_protected(tmp_path: Path, rel: str) -> None:
    """ADR-0023 (G2): a tool reads these before, or instead of, pyproject.toml and the Makefile."""
    assert guard.is_protected(rel)
    reason = guard.check_edit("Write", {"file_path": rel, "content": "x"}, tmp_path)
    assert reason and "protected" in reason


# Covers F-0001.R1, F-0001.R4, F-0001.R7 and F-0001.R10.
def test_a_new_shadow_file_is_caught_after_the_command_and_at_commit(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0023 (G2): the layers behind the edit rule; fixtures and near names stay free."""
    monkeypatch.setenv("CLAUDECODE", "1")
    (repo / "tests").mkdir()
    for rel in ("tests/conftest.py", "conftest.py", ".python-version", "ruff.toml.md"):
        (repo / rel).write_text("x = 1\n", encoding="utf-8")
        assert guard.check_edit("Write", {"file_path": rel, "content": "x"}, repo) is None, rel
    _git(repo, "add", "-A")
    assert guard.check_post_bash(repo) is None and guard.check_pre_commit(repo) is None
    (repo / "ruff.toml").write_text('[lint]\nignore = ["E", "F"]\n', encoding="utf-8")
    (repo / "tests" / "pytest.ini").write_text("[pytest]\naddopts = -q\n", encoding="utf-8")
    reason = guard.check_post_bash(repo) or ""
    assert "ruff.toml" in reason and "tests/pytest.ini" in reason and "protected" in reason
    _git(repo, "add", "-A")
    reason = guard.check_pre_commit(repo) or ""
    assert "protected file(s) staged: ruff.toml, tests/pytest.ini. " in reason


# Covers F-0001.R1 and F-0001.R10 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_the_git_directory_is_protected_and_its_neighbours_are_not(repo: Path) -> None:
    """G3: `.git/` holds the hooks and the clone's configuration."""
    for rel in (
        ".git/config",
        ".git/hooks/pre-commit",
        ".git/hooks/commit-msg",
        ".git/info/exclude",
    ):
        assert guard.is_protected(rel), rel
        reason = guard.check_edit("Write", {"file_path": rel, "content": "x"}, repo) or ""
        assert "protected" in reason, rel
    absolute = str(repo / ".git" / "hooks" / "pre-commit")
    assert guard.check_edit("Write", {"file_path": absolute, "content": "x"}, repo)
    assert guard.check_edit("Edit", _edit(".github/workflows/ci.yml"), repo)
    for rel in (".gitignore", ".gitattributes", ".gitkeep", "docs/.gitkeep", "src/git/x.py"):
        assert not guard.is_protected(rel), rel
        assert guard.check_edit("Write", {"file_path": rel, "content": "x"}, repo) is None, rel
    # A project in a sub-directory of the repository: the hooks live above the project root.
    project = repo / "services" / "api"
    project.mkdir(parents=True)
    above = {"file_path": "../../.git/hooks/pre-commit", "content": "exit 0\n"}
    assert "protected" in (guard.check_edit("Write", above, project) or "")
    assert (
        guard.check_edit("Write", {"file_path": "../../notes.md", "content": "x"}, project) is None
    )


# Covers F-0001.R4 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_a_commit_that_skipped_the_hook_is_judged_after_the_command(repo: Path) -> None:
    """ADR-0024 (G5): the guard remembers HEAD before a shell command and judges what moved it."""
    _commit_documents(repo)
    pyproject = _commit_pyproject(repo)
    guard.record_pre_shell(repo)
    state = repo / _git(repo, "rev-parse", "--git-path", "rumbo-guard-head").strip()
    assert state.is_file() and ".git" in state.parts and _clean(repo)  # never in the working tree
    # An ordinary commit: code, a draft under the mark, the dependency tables.
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "specs" / "0002-draft.md").write_text(
        f"{guard.TEMPLATE_MARK} -->\n# Draft\nmore\n", encoding="utf-8"
    )
    pyproject.write_text(UV_ADD, encoding="utf-8")
    _bypass_commit(repo)
    assert guard.check_post_shell(repo) is None
    # A commit no hook saw: the tree is clean, so `git status` has nothing to say.
    guard.record_pre_shell(repo)
    old = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "Makefile").write_text("check: ; true\n", encoding="utf-8")
    pyproject.write_text(UV_ADD.replace("line-length = 100", "line-length = 320"), "utf-8")
    (repo / "specs" / "0001-query.md").write_text("# rewritten\n", encoding="utf-8")
    (repo / "docs" / "brief.md").write_text("# Brief, finished\n", encoding="utf-8")
    (repo / "specs" / "0009-new.md").write_text("# F-0009\n", encoding="utf-8")
    new = _bypass_commit(repo)
    assert _clean(repo) and guard.check_post_bash(repo) is None
    reason = guard.check_post_shell(repo) or ""
    assert "HEAD moved" in reason and old[:9] in reason and new[:9] in reason
    assert "protected file(s) changed: Makefile" in reason
    assert "outside its dependency tables: tool.ruff" in reason
    assert "specs/0001-query.md" in reason and "signed by the human" in reason
    assert "docs/brief.md" in reason and "specs/0009-new.md" in reason and "ADR-0025" in reason
    # Reported once: the state now holds the new HEAD.
    assert guard.check_post_shell(repo) is None
    # Without a recorded HEAD, or without git, there is no information and nothing to report.
    state.unlink()
    (repo / "Makefile").write_text("check: ; false\n", encoding="utf-8")
    _bypass_commit(repo)
    assert guard.check_post_shell(repo) is None
    state.write_text("not json", encoding="utf-8")
    assert guard.check_post_shell(repo) is None


# Covers F-0001.R5 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_stop_verifies_code_committed_since_the_last_green_head(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0024 (G12): committing red code and then stopping no longer skips the verification."""
    monkeypatch.delenv("RUMBO_GUARD_SKIP_CHECK", raising=False)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "rumbo_check.py").write_text(FAKE_RUNNER, encoding="utf-8")
    (repo / ".gitignore").write_text("ran.txt\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "chore: the human adds the runner")
    ran = repo / "ran.txt"
    monkeypatch.setenv("FAKE_CHECK_EXIT", "1")
    # No recorded value yet: record this HEAD and behave as before (a clean tree runs nothing).
    assert guard.check_stop({}, repo) == (0, "") and not ran.exists()
    # Red code, committed: the tree is clean and the verification runs all the same.
    (repo / "app.py").write_text("x = 'red'\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "feat: red code")
    code, message = guard.check_stop({}, repo)
    assert code == guard.BLOCK and "rumbo check is red after changing 1 file(s)" in message
    # A red run is not recorded: the next Stop verifies again, until it is green.
    ran.unlink()
    assert guard.check_stop({}, repo)[0] == guard.BLOCK and ran.exists()
    monkeypatch.setenv("FAKE_CHECK_EXIT", "0")
    ran.unlink()
    assert guard.check_stop({}, repo) == (0, "") and ran.exists()
    # Green at this HEAD: nothing runs again, nor after a commit that touches no code.
    ran.unlink()
    assert guard.check_stop({}, repo) == (0, "") and not ran.exists()
    (repo / "docs" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "docs: notes")
    monkeypatch.setenv("FAKE_CHECK_EXIT", "1")
    assert guard.check_stop({}, repo) == (0, "") and not ran.exists()
    # A green HEAD that git no longer knows is no information: behave as before.
    state = repo / _git(repo, "rev-parse", "--git-path", "rumbo-guard-head").strip()
    state.write_text(json.dumps({"green": "0" * 40}), encoding="utf-8")
    assert guard.check_stop({}, repo) == (0, "") and not ran.exists()


# Covers F-0001.R4 and F-0001.R7 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_git_mv_does_not_hide_the_source_path(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G6: with rename detection the protected source of a `git mv` was reported as its target."""
    _commit_documents(repo)
    _git(repo, "mv", "Makefile", "build.mk")
    _git(repo, "mv", "specs/0001-query.md", "specs/0009-query.md")
    assert {"Makefile", "build.mk", "specs/0001-query.md"} <= set(guard.changed_files(repo))
    reason = guard.check_post_bash(repo) or ""
    assert "Makefile" in reason and "specs/0001-query.md" in reason
    monkeypatch.setenv("CLAUDECODE", "1")
    assert {"Makefile", "build.mk", "specs/0001-query.md"} <= set(guard.staged_files(repo))
    assert "protected file(s) staged: Makefile. " in (guard.check_pre_commit(repo) or "")
    _git(repo, "reset", "-q", "--", "Makefile", "build.mk")
    assert "specs/0001-query.md" in (guard.check_pre_commit(repo) or "")


# Covers F-0001.R4, F-0001.R7, F-0001.R13 and F-0001.R14.
def test_names_with_spaces_or_non_ascii_reach_every_layer(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G11: git quotes such names unless asked for NUL-terminated output."""
    spec, spaced, note = "specs/0002-diseño.md", "specs/0003-two words.md", "docs/mi nota.md"
    (repo / "specs").mkdir()
    (repo / spec).write_text("# signed\n", encoding="utf-8")
    (repo / spaced).write_text("# signed\n", encoding="utf-8")
    (repo / note).write_text(f"{guard.TEMPLATE_MARK} -->\n# Nota\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "docs: names that git quotes")
    workflow = ".github/my workflow.yml"
    (repo / ".github").mkdir()
    (repo / workflow).write_text("on: push\n", encoding="utf-8")
    (repo / spec).write_text("# rewritten\n", encoding="utf-8")
    (repo / spaced).write_text("# rewritten\n", encoding="utf-8")
    (repo / note).write_text("# Nota, finished\n", encoding="utf-8")
    assert {spec, spaced, note, workflow} <= set(guard.changed_files(repo))
    reason = guard.check_post_bash(repo) or ""
    assert all(name in reason for name in (spec, spaced, note, workflow))
    assert "signed" in (guard.check_edit("Edit", _edit(spec), repo) or "")
    monkeypatch.setenv("CLAUDECODE", "1")
    _git(repo, "add", "-A")
    assert {spec, spaced, note, workflow} <= set(guard.staged_files(repo))
    assert workflow in (guard.check_pre_commit(repo) or "")
    _git(repo, "reset", "-q", "--", ".github")
    reason = guard.check_pre_commit(repo) or ""
    assert spec in reason and spaced in reason and "signed" in reason
    _git(repo, "reset", "-q", "--", "specs")
    assert (guard.check_pre_commit(repo) or "").startswith(f"{note}:")


# Covers F-0001.R1 and F-0001.R3 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_the_payload_is_read_as_utf8_whatever_the_locale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """G11: on Windows the text layer of stdin is cp1252, where bytes such as 0x81 (the second
    byte of an accented capital in UTF-8) are undefined: the hook crashed, and a crash allows."""
    content = "# \u00c1rea \u201cok\u201d \u00cdndice\n"
    edit = {"file_path": str(tmp_path / "Makefile"), "content": content}
    payload = {"cwd": str(tmp_path), "tool_name": "Write", "tool_input": edit}
    assert _hook(monkeypatch, "pre-tool", payload) == guard.BLOCK
    assert "protected" in capsys.readouterr().err
    command = "git commit --no-verify -m '\u00cdndice'"
    payload = {"cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": command}}
    assert _hook(monkeypatch, "pre-tool", payload) == guard.BLOCK
    # A name outside cp1252 in the message never breaks the answer either.
    edit = {"file_path": str(tmp_path / ".github" / "流程.yml"), "content": "x"}
    payload = {"cwd": str(tmp_path), "tool_name": "Write", "tool_input": edit}
    assert _hook(monkeypatch, "pre-tool", payload) == guard.BLOCK
    assert ".github/流程.yml" in capsys.readouterr().err


# Covers F-0001.R9 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_agent_env_is_read_from_head_not_from_the_working_tree(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G7 (ADR-0026): an unstaged edit of pyproject.toml cannot un-recognise the agent."""
    pyproject = _commit_pyproject(repo, '[tool.rumbo]\nagent_env = ["MY_AGENT"]\n')
    monkeypatch.setenv("MY_AGENT", "1")
    pyproject.write_text("[tool.rumbo]\nagent_env = []\n", encoding="utf-8")
    assert guard.agent_env_names(repo) == ("MY_AGENT",) and guard.actor(repo) == "agent"
    _git(repo, "add", "pyproject.toml")  # staged is not committed either
    assert guard.agent_env_names(repo) == ("MY_AGENT",)
    pyproject.unlink()
    assert guard.agent_env_names(repo) == ("MY_AGENT",)
    # HEAD has no pyproject.toml: the defaults, whatever the working tree says.
    _git(repo, "rm", "-q", "--cached", "pyproject.toml")
    _git(repo, "commit", "-qm", "chore: no pyproject.toml")
    pyproject.write_text("[tool.rumbo]\nagent_env = []\n", encoding="utf-8")
    assert guard.agent_env_names(repo) == guard.DEFAULT_AGENT_ENV
    # Only a repository with no commit falls back to the working tree: there is no base yet.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _git(fresh, "init", "-q", "-b", "main")
    (fresh / "pyproject.toml").write_text('[tool.rumbo]\nagent_env = ["MINE"]\n', encoding="utf-8")
    assert guard.agent_env_names(fresh) == ("MINE",)


# Covers F-0001.R7 and F-0001.R14 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_a_staged_type_change_counts(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G8: a protected file turned into a symlink is status T, which the filter left out."""
    _commit_documents(repo)
    monkeypatch.setenv("CLAUDECODE", "1")
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input="evil.mk",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _git(repo, "update-index", "--cacheinfo", f"120000,{blob},Makefile")
    assert _git(repo, "diff", "--cached", "--name-status").split() == ["T", "Makefile"]
    assert guard.staged_files(repo) == ["Makefile"]
    assert "protected file(s) staged: Makefile. " in (guard.check_pre_commit(repo) or "")
    _git(repo, "reset", "-q")
    _git(repo, "update-index", "--cacheinfo", f"120000,{blob},specs/0001-query.md")
    reason = guard.check_pre_commit(repo) or ""
    assert "specs/0001-query.md" in reason and "signed" in reason


# Covers F-0001.R7 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_an_orphan_branch_is_not_a_root_commit(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G9: the root-commit exemption is for a repository with no commit on any ref."""
    _commit_documents(repo)
    _commit_pyproject(repo)
    monkeypatch.setenv("CLAUDECODE", "1")
    _git(repo, "checkout", "-q", "--orphan", "tmp")
    (repo / "Makefile").write_text("check: ; true\n", encoding="utf-8")
    (repo / "specs" / "0001-query.md").write_text("# rewritten\n", encoding="utf-8")
    _git(repo, "add", "-A")
    assert not guard.has_head(repo) and guard.has_commits(repo)
    assert guard.pyproject_change(repo, staged=True) == ["pyproject.toml (new file)"]
    reason = guard.check_pre_commit(repo) or ""
    assert "Makefile" in reason and "pyproject.toml (new file)" in reason
    _git(repo, "rm", "-q", "--cached", "Makefile", "pyproject.toml")
    assert "specs/0001-query.md" in (guard.check_pre_commit(repo) or "")


# Covers F-0001.R4 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_protected_files_hidden_from_git_status_are_found(repo: Path) -> None:
    """G10: index flags and ignore rules make `git status` blind; the guard looks itself, and
    reports what appears or changes while a shell command runs (G-3: never what was there)."""
    _commit_documents(repo)
    pyproject = _commit_pyproject(repo)
    _git(repo, "update-index", "--skip-worktree", "pyproject.toml", "specs/0001-query.md")
    _git(repo, "update-index", "--assume-unchanged", "Makefile")
    guard.record_pre_shell(repo)
    assert guard.check_post_shell(repo) is None  # the flags alone change nothing
    guard.record_pre_shell(repo)
    pyproject.write_text(PYPROJECT_HEAD.replace("line-length = 100", "line-length = 320"), "utf-8")
    (repo / "Makefile").write_text("check: ; true\n", encoding="utf-8")
    (repo / "specs" / "0001-query.md").write_text("# rewritten\n", encoding="utf-8")
    assert _clean(repo)
    reason = guard.check_post_shell(repo) or ""
    assert "Makefile (hidden from git status)" in reason
    assert "outside its dependency tables: tool.ruff" in reason
    assert "specs/0001-query.md" in reason and "signed" in reason
    # Reported once, like the local settings; Stop does not judge what git status cannot see.
    assert guard.check_post_shell(repo) is None
    # The dependency tables stay free, hidden or not.
    guard.record_pre_shell(repo)
    pyproject.write_text(UV_ADD, encoding="utf-8")
    _git(repo, "update-index", "--no-assume-unchanged", "Makefile")
    _git(repo, "update-index", "--no-skip-worktree", "specs/0001-query.md")
    _git(repo, "checkout", "--", "Makefile", "specs/0001-query.md")
    assert guard.check_post_shell(repo) is None
    # A new file that an ignore rule hides: what verifies locally is flagged, local files are not.
    (repo / ".git" / "info" / "exclude").write_text(
        "ruff.toml\nGNUmakefile\n.env\n.claude/\nscratch/\n", encoding="utf-8"
    )
    guard.record_pre_shell(repo)
    for rel in ("ruff.toml", "docs/ruff.toml", "GNUmakefile", ".env", "scratch/ruff.toml"):
        (repo / rel).parent.mkdir(exist_ok=True)
        (repo / rel).write_text("x\n", encoding="utf-8")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.local.json").write_text("{}\n", encoding="utf-8")
    assert _clean(repo)
    reason = guard.check_post_shell(repo) or ""
    listed = reason.split("since the last commit: ", 1)[1].split(". If you", 1)[0]
    assert listed == ", ".join(
        f"{rel} (hidden from git status)" for rel in ("GNUmakefile", "docs/ruff.toml", "ruff.toml")
    )


# Covers F-0001.R4 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_local_settings_changed_during_a_command_are_reported(repo: Path) -> None:
    """G10: .claude/settings.local.json is ignored and legitimately local, so only a change
    made while a shell command ran is reported, and never the permissions the human grants."""
    (repo / ".git" / "info" / "exclude").write_text(".claude/\n", encoding="utf-8")
    local = repo / ".claude" / "settings.local.json"
    guard.record_pre_shell(repo)
    local.parent.mkdir()
    local.write_text('{"permissions": {"allow": ["Bash(ls *)"]}}', encoding="utf-8")
    assert guard.check_post_shell(repo) is None  # what "always allow" writes on the human's click
    guard.record_pre_shell(repo)
    local.write_text('{"permissions": {"allow": []}, "disableAllHooks": true}', encoding="utf-8")
    reason = guard.check_post_shell(repo) or ""
    assert ".claude/settings.local.json" in reason and "ask the human" in reason
    assert guard.check_post_shell(repo) is None  # reported once
    guard.record_pre_shell(repo)
    local.write_text("not json", encoding="utf-8")
    assert ".claude/settings.local.json" in (guard.check_post_shell(repo) or "")
    guard.record_pre_shell(repo)
    local.unlink()
    assert ".claude/settings.local.json" in (guard.check_post_shell(repo) or "")


# Covers F-0001.R2 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_an_edit_is_simulated_to_see_whether_the_mark_survives(tmp_path: Path) -> None:
    """G13: an Edit whose old_string is only a part of the mark removed it unseen."""
    doc = tmp_path / "docs" / "brief.md"
    doc.parent.mkdir()
    doc.write_text(f"{guard.TEMPLATE_MARK} — delete me -->\n# Brief\ntext\n", encoding="utf-8")
    path = str(doc)
    part = _edit(path, "<!-- rumbo:temp", "<!-- temp")
    assert "human" in (guard.check_edit("Edit", part, tmp_path) or "")
    assert guard.check_edit("Edit", _edit(path, "rumbo:template — delete me", "x"), tmp_path)
    multi = {"file_path": path, "edits": [_edit(path, "# Brief", "# B"), _edit(path, "<!-- ", "")]}
    assert guard.check_edit("MultiEdit", multi, tmp_path)
    # Edits that leave the mark where it is, or rewrite its line and keep it.
    assert guard.check_edit("Edit", _edit(path, "text", "more text"), tmp_path) is None
    assert guard.check_edit("Edit", _edit(path, "delete me", "remove me"), tmp_path) is None
    harmless = {"file_path": path, "edits": [_edit(path, "# Brief", "# B"), "not an edit"]}
    assert guard.check_edit("MultiEdit", harmless, tmp_path) is None
    # replace_all is honoured: one of two occurrences replaced leaves a mark, both leave none.
    doc.write_text("<!-- rumbo:template -->\n# Brief\nand <!-- rumbo:template too\n", "utf-8")
    once = _edit(path, "rumbo:temp", "rumbo:done")
    assert guard.check_edit("Edit", once, tmp_path) is None
    assert guard.check_edit("Edit", {**once, "replace_all": True}, tmp_path)


# Covers F-0001.R7, F-0001.R14 and F-0001.R15.
def test_an_agent_cannot_create_a_numbered_document_without_the_mark(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0025 (G14): without the mark, the document counted as signed after its first commit."""
    _commit_documents(repo)
    marked = f"{guard.TEMPLATE_MARK} -->\n# Draft\n"
    for rel in ("specs/0009-new.md", "docs/adr/0009-new.md"):
        reason = guard.check_edit("Write", {"file_path": rel, "content": "# New\n"}, repo) or ""
        assert rel in reason and "ADR-0025" in reason and "rumbo new" in reason
        assert guard.check_edit("Write", {"file_path": rel, "content": marked}, repo) is None
    # Other documents, and the templates, are not numbered documents.
    for rel in ("docs/notes.md", "specs/README.md", "specs/0000-notes.md"):
        assert guard.check_edit("Write", {"file_path": rel, "content": "# x\n"}, repo) is None
    # At commit: an added numbered document is refused without the mark, accepted with it.
    monkeypatch.setenv("CLAUDECODE", "1")
    (repo / "specs" / "0009-new.md").write_text("# New\n", encoding="utf-8")
    (repo / "docs" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _git(repo, "add", "-A")
    reason = guard.check_pre_commit(repo) or ""
    assert "specs/0009-new.md" in reason and "ADR-0025" in reason
    (repo / "specs" / "0009-new.md").write_text(marked, encoding="utf-8")
    _git(repo, "add", "-A")
    assert guard.check_pre_commit(repo) is None
    # The human signs a new document in the commit that adds it.
    (repo / "specs" / "0009-new.md").write_text("# New\n", encoding="utf-8")
    _git(repo, "add", "-A")
    monkeypatch.delenv("CLAUDECODE")
    _git(repo, "config", "rumbo.human", "true")
    assert guard.check_pre_commit(repo) is None


# Covers F-0001.R1 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_windows_spellings_of_a_local_path_are_normalised() -> None:
    """G15: forms that pathlib places outside the project root (a pure function, any platform)."""
    hosts = ("localhost", "127.0.0.1", "box")
    for spelling in (
        "\\\\?\\C:\\proj\\Makefile",
        "\\\\.\\C:\\proj\\Makefile",
        "//?/C:/proj/Makefile",
        "\\\\localhost\\C$\\proj\\Makefile",
        "\\\\127.0.0.1\\c$\\proj\\Makefile",
        "\\\\?\\UNC\\BOX\\C$\\proj\\Makefile",
    ):
        assert guard._windows_path(spelling, hosts).lower() == "c:\\proj\\makefile", spelling
    # Another host, a volume or a device stays as it is: it cannot be placed with certainty.
    for spelling in ("\\\\server\\share\\proj\\Makefile", "\\\\?\\Volume{0a1b}\\proj\\Makefile"):
        assert guard._windows_path(spelling, hosts).startswith("\\\\"), spelling
    assert guard._windows_path("C:\\proj\\src\\x.py", hosts) == "C:\\proj\\src\\x.py"


# Covers F-0001.R1 and F-0001.R10 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_a_path_that_cannot_be_placed_is_blocked_by_its_name(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G15: when the guard cannot tell whether a path is in the project, a protected name blocks."""

    def unplaceable(_file_path: str, _root: Path) -> tuple[list[Path], bool]:
        return [], False

    assert guard.check_edit("Write", {"file_path": "Makefile\0x", "content": "x"}, repo)
    monkeypatch.setattr(guard, "_candidates", unplaceable)
    for name in ("Makefile", "pyproject.toml", "ruff.toml", ".env.local", "rumbo_guard.py"):
        edit = {"file_path": f"\\\\server\\share\\proj\\{name}", "content": "x"}
        assert "protected" in (guard.check_edit("Write", edit, repo) or ""), name
    for path in ("//server/share/proj/.github/workflows/ci.yml", "//server/share/.git/config"):
        assert guard.check_edit("Write", {"file_path": path, "content": "x"}, repo), path
    edit = {"file_path": "\\\\server\\share\\proj\\src\\x.py", "content": "x"}
    assert guard.check_edit("Write", edit, repo) is None


# Covers F-0001.R1 and F-0001.R10 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_only_real_env_files_are_protected(tmp_path: Path) -> None:
    """G16: `.env` was a prefix, so `.envrc` and `.environment.yml` were protected by accident."""
    for rel in (".env", ".env.local", ".env.production", ".ENV", ".Env.local"):
        assert guard.is_protected(rel), rel
    for rel in (".envrc", ".environment.yml", ".env/bin/python", "src/.envoy.py"):
        assert not guard.is_protected(rel), rel
        assert guard.check_edit("Write", {"file_path": rel, "content": "x"}, tmp_path) is None, rel
    # Names Windows opens as the protected file: a trailing dot or space, a data stream.
    for rel in ("Makefile.", "Makefile ", "Makefile::$DATA", ".claude /settings.json"):
        assert guard.is_protected(rel), rel


# Covers F-0001.R12 and F-0001.R13 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_changed_documents_are_read_in_one_git_call(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G16: one `git show` per changed .md cost 1.3 s for 60 files; now one `git cat-file`."""
    _commit_documents(repo)
    for n in range(40):
        (repo / "docs" / f"note-{n}.md").write_text("# new\n", encoding="utf-8")
    (repo / "docs" / "brief.md").write_text("# Brief, finished\n", encoding="utf-8")
    (repo / "specs" / "0001-query.md").write_text("# rewritten\n", encoding="utf-8")
    calls: list[list[str]] = []
    run = subprocess.run

    def counting(cmd: list[str], **kwargs: Any) -> Any:
        calls.append(cmd)
        return run(cmd, **kwargs)

    monkeypatch.setattr(guard.subprocess, "run", counting)
    reason = guard.check_post_bash(repo) or ""
    assert "docs/brief.md" in reason and "specs/0001-query.md" in reason
    assert len(calls) <= 4, calls


# Covers F-0001.R11 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b'"text"',
        b"null",
        b"not json",
        b"",
        b'{"tool_name":"Write","tool_input":"Makefile"}',
        b'{"tool_name":"MultiEdit","tool_input":{"file_path":"notes.md","edits":["x"]}}',
        b'{"tool_name":"Bash","tool_input":{"command":["ls"]}}',
        b'\xef\xbb\xbf{"tool_name":"Bash","tool_input":{"command":"ls"}}',
    ],
)
def test_a_payload_of_another_shape_never_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    raw: bytes,
) -> None:
    """G16: a payload that is not a JSON object is an empty one; nothing to judge, no traceback."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("RUMBO_GUARD_SKIP_CHECK", "1")
    for event in ("pre-tool", "post-tool", "stop"):
        assert _hook(monkeypatch, event, None, raw=raw) == 0, event
    assert capsys.readouterr() == ("", "")


# Covers F-0001.R11 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_an_internal_error_never_reads_as_allow(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Claude Code treats any exit code other than 0 and 2 as a non-blocking error: a traceback
    would let the action through. Each hook says what it could not do instead."""

    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    monkeypatch.delenv("RUMBO_GUARD_SKIP_CHECK", raising=False)
    for name in ("check_edit", "check_bash", "check_post_shell", "check_stop"):
        monkeypatch.setattr(guard, name, broken)
    edit = {"tool_name": "Edit", "tool_input": _edit("src/x.py")}
    shell = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    # Before the tool: refused, because the guard could not judge the call.
    for payload in (edit, shell):
        assert _hook(monkeypatch, "pre-tool", payload) == guard.BLOCK
        err = capsys.readouterr().err
        assert "internal error" in err and "boom" in err and "human" in err
    # After the tool: reported to the agent (nothing can be undone by blocking).
    assert _hook(monkeypatch, "post-tool", shell) == guard.BLOCK
    assert "internal error" in capsys.readouterr().err
    # Stop: the environment route, so the agent reports it instead of trying to repair it.
    assert _hook(monkeypatch, "stop", {}) == 0
    out, err = capsys.readouterr()
    hook = json.loads(out)
    assert err == "" and hook["decision"] == "block" and "boom" in hook["reason"]
    assert "do NOT try to repair it" in hook["reason"] and "guard" in hook["systemMessage"]
    # Git hooks: a refusal with a readable line, never a traceback.
    monkeypatch.setattr(guard, "check_pre_commit", broken)
    monkeypatch.chdir(repo)
    assert guard.main(["pre-commit"]) == guard.GIT_BLOCK
    err = capsys.readouterr().err
    assert err.startswith("blocked by scripts/rumbo_guard.py: internal error") and "boom" in err


# Covers F-0001.R4 and F-0001.R13 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_a_project_in_a_sub_directory_of_the_repository_is_judged(repo: Path) -> None:
    """git reports paths from the top of the repository; the guard's rules are relative to the
    project root, which may be a sub-directory of it."""
    project = repo / "services" / "api"
    (project / "docs").mkdir(parents=True)
    (project / "Makefile").write_text("check: ; echo\n", encoding="utf-8")
    (project / "docs" / "brief.md").write_text(f"{guard.TEMPLATE_MARK} -->\n# B\n", "utf-8")
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feat: a service in a sub-directory")
    assert guard.check_post_bash(project) is None
    (project / "Makefile").write_text("check: ; true\n", encoding="utf-8")
    (project / "docs" / "brief.md").write_text("# B, finished\n", encoding="utf-8")
    (project / "app.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")  # another project: not this one's
    assert sorted(guard.changed_files(project)) == ["Makefile", "app.py", "docs/brief.md"]
    reason = guard.check_post_bash(project) or ""
    assert "changed since the last commit: Makefile. " in reason and "docs/brief.md" in reason


# Covers F-0001.R10 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_the_template_pre_approves_nothing_that_defeats_the_guard() -> None:
    """P2 and V6: `make rumbo RUMBO=<any binary>` and `rumbo decision` (a gate signature) ask
    the human; `git diff` and `rumbo verify-dep` stay pre-approved behind a guard rule."""
    allow = _template_settings()["permissions"]["allow"]
    assert "Bash(make rumbo *)" not in allow and "Bash(rumbo decision *)" not in allow
    assert not [rule for rule in allow if rule.startswith(("Bash(make rumbo", "Bash(rumbo dec"))]
    assert "Bash(git diff *)" in allow and "Bash(rumbo verify-dep *)" in allow
    assert "--no-index" in (guard.check_bash("git diff --no-index /dev/null .env") or "")
    assert "--index" in (guard.check_bash("rumbo verify-dep x --index http://h/simple") or "")


# Covers F-0001.R3 and F-0001.R12 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_a_command_too_long_to_judge_is_refused_not_timed_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hook that runs out of time lets the call through, and the rules cost the square of the
    longest simple command: padding must not buy a bypass. The work is bounded and fails closed.
    """
    import time

    padding = "sed rm tee git config push reset diff " * 4000  # 150 000 characters, one line
    start = time.perf_counter()
    reason = guard.check_bash(f"git commit -m '{padding}' --no-verify") or ""
    assert "cannot be judged in time" in reason and time.perf_counter() - start < 2
    # Many lines are many short commands: a long script on stdin is judged, and quickly.
    script = "python - <<'EOF'\n" + "print('git commit, sed and tee are only words here')\n" * 4000
    start = time.perf_counter()
    assert guard.check_bash(script + "EOF") is None and time.perf_counter() - start < 2
    assert guard.check_bash(script + "EOF\ngit commit --no-verify -m x")
    # Options repeated without end are read once (no exponential backtracking).
    start = time.perf_counter()
    assert guard.check_bash("git " + "--x=--y " * 600 + "status") is None
    assert guard.check_bash("git " + "-c a=b " * 600 + "commit -n -m x")
    assert time.perf_counter() - start < 2
    # Past the time budget the answer is a refusal, never silence.
    monkeypatch.setattr(guard, "RULES_BUDGET", -1.0)
    assert "could not be judged" in (guard.check_bash("ls") or "")


# ------------------------------------------- the audit of the rewrite (G-1..G-5, F-1)
NL = "\n"
HEREDOC_COMMIT = (
    "git commit -m \"$(cat <<'EOF'"
    + NL
    + "feat: thing"
    + NL
    + NL
    + "Body line."
    + NL
    + "EOF"
    + NL
    + ')"'
)
# (the command, whether origin/main's guard blocked it). Measured against origin/main at f6bf966;
# every form it blocked is blocked, and the two it let through are blocked now as well.
MULTI_LINE_FORMS: list[tuple[str, bool]] = [
    (HEREDOC_COMMIT + " --no-verify", True),
    (HEREDOC_COMMIT + " -n", True),
    (HEREDOC_COMMIT.replace("commit -m", "commit --no-verify -m"), True),
    ('git commit -m "feat: thing' + NL + NL + 'Body line." --no-verify', True),
    ('git commit -m "feat: thing' + NL + NL + 'Body line." -n', True),
    ("git commit -m x \\" + NL + "  --no-verify", True),
    ("git commit -m x \\" + NL + "  -n", True),
    ("git push \\" + NL + "  --force-with-lease \\" + NL + "  origin feature", True),
    ("git push origin feature \\" + NL + "    -f", True),
    ("git config \\" + NL + "  core.hooksPath /dev/null", True),
    ("git \\" + NL + "  -c core.hooksPath=/dev/null \\" + NL + "  commit -m x", True),
    ("git reset \\" + NL + "  --hard HEAD~1", False),
    ("SKIP=rumbo-guard \\" + NL + "  git commit -m x", True),
    ("echo x | tee \\" + NL + "  Makefile", True),
    ("sed -i 's/a/b/' \\" + NL + "  pyproject.toml", True),
    ("echo x > \\" + NL + "  Makefile", False),
    ("git commit -m x `" + NL + "  --no-verify", True),
    ("git push `" + NL + "  --force origin main", True),
    ("git commit -m x \\\r\n  --no-verify", True),
    ("git push origin feature \\\r\n  --force", True),
    ('git commit -m "subject\r\n\r\nbody" --no-verify', True),
]


# Covers F-0001.R3 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
@pytest.mark.parametrize(("command", "blocked_on_main"), MULTI_LINE_FORMS)
def test_a_forbidden_flag_on_another_line_is_still_blocked(
    command: str, blocked_on_main: bool
) -> None:
    """G-1: the rules read one simple command at a time, and a simple command does not end at a
    line break inside quotes, inside `$(...)` or after a continuation. The rewrite stopped at
    every line break and let all of these through; origin/main blocked the ones marked True."""
    assert blocked_on_main in (True, False)
    assert guard.check_bash(command), command


# Covers F-0001.R3 and F-0001.R10 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_the_shell_view_joins_what_the_shell_joins_and_nothing_else() -> None:
    """G-1: separators inside quotes do not end the command either (a `;` in the message hid
    the flag after it); separate lines stay separate commands, so a script is not one command."""
    for command in (
        'git commit -m "feat: a; b" --no-verify',
        'git commit -m "a | b && c" -n',
        "git commit -m 'one" + NL + "two; three' --no-verify",
        'git commit -m "$(printf a; printf b)" --no-verify',
        'git commit -m "it\'s fine" \\' + NL + " --no-verify",
        "# it's a comment" + NL + "git commit -m x \\" + NL + " --no-verify",
        "cat > notes.txt <<'EOF'" + NL + "it's text" + NL + "EOF" + NL + "git push \\" + NL + " -f",
        'git commit -m "unterminated' + NL + "--no-verify",
    ):
        assert guard.check_bash(command), command
    for command in (
        "git commit -m x" + NL + "grep -n TODO src/app.py",
        "git status" + NL + "rm -rf build" + NL + "git log -n 5",
        "git push origin feature" + NL + "ls -f",
        "python - <<'EOF'" + NL + "print('git commit')" + NL + "EOF" + NL + "ls -n",
        "cat > notes.txt <<'EOF'" + NL + "it's text" + NL + "EOF" + NL + "git log -n 3",
        HEREDOC_COMMIT,
        HEREDOC_COMMIT + NL + "git push origin feature",
        "gh pr create --title x --body \"$(cat <<'EOF'"
        + NL
        + "## Summary"
        + NL
        + "EOF"
        + NL
        + ')"',
    ):
        assert guard.check_bash(command) is None, command


# Covers F-0001.R10 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
@pytest.mark.parametrize(
    "command",
    [
        'git commit -m "chore: rm unused target from Makefile"',
        "grep -n 'rm -rf' Makefile",
        "grep -rn 'Remove-Item' .github/",
        "grep -c 'rm' Makefile",
        "git log --grep 'mv hooks' -- .git/hooks",
        'git commit -m "docs: rm -r of old dirs" .',
    ],
)
def test_a_writer_word_inside_an_argument_is_not_a_command(command: str) -> None:
    """A commit message or a grep pattern that names `rm` and a protected file on one line."""
    assert guard.check_bash(command) is None


# Covers F-0001.R3 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
@pytest.mark.parametrize(
    "command",
    [
        "rm Makefile",
        "cd src && rm -f .pre-commit-config.yaml",
        "sudo rm pyproject.toml",
        "FORCE=1 rm ruff.toml",
        "bash -c 'rm Makefile'",
        "(rm -f .claude/settings.json)",
        "true; Remove-Item .github\\workflows\\ci.yml",
        "find . -name x -exec rm Makefile ';'",
        "echo x | xargs rm -f Makefile",
        "ls && mv .git/hooks/pre-commit /tmp/x",
        "foo rm .git/hooks/pre-commit",
        "echo rm -rf /",
    ],
)
def test_a_writer_at_the_start_of_a_command_is_blocked(command: str) -> None:
    assert guard.check_bash(command)


# Covers F-0001.R4 and F-0001.R5 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_a_state_that_cannot_be_read_is_unknown_not_empty(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G-2: parallel hooks collide on the state file on Windows. A failed read used to count as
    an empty state, which was saved back without the green HEAD: Stop then skipped the check."""
    monkeypatch.delenv("RUMBO_GUARD_SKIP_CHECK", raising=False)
    monkeypatch.setattr(guard, "_RETRY_PAUSES", (0.0, 0.0, 0.0))
    (repo / "scripts").mkdir()
    (repo / "scripts" / "rumbo_check.py").write_text(FAKE_RUNNER, encoding="utf-8")
    (repo / ".gitignore").write_text("ran.txt\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "chore: the human adds the runner")
    monkeypatch.setenv("FAKE_CHECK_EXIT", "0")
    assert guard.check_stop({}, repo) == (0, "")
    state = repo / _git(repo, "rev-parse", "--git-path", "rumbo-guard-head").strip()
    green = json.loads(state.read_text(encoding="utf-8"))["green"]
    ran = repo / "ran.txt"
    read_bytes = Path.read_bytes
    failures = {"left": 0}

    def locked(self: Path) -> bytes:
        if self.name == state.name and failures["left"] > 0:
            failures["left"] -= 1
            raise PermissionError(13, "sharing violation")
        return read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", locked)
    # A read that fails once is retried: nothing is lost.
    failures["left"] = 1
    guard.record_pre_shell(repo)
    assert json.loads(read_bytes(state))["green"] == green
    # A read that keeps failing: the hooks neither report nor save over what they could not read.
    failures["left"] = 10**6
    guard.record_pre_shell(repo)
    assert guard.check_post_shell(repo) is None
    assert json.loads(read_bytes(state))["green"] == green
    # Stop with an unreadable state verifies (nothing changed, and the runner still runs).
    monkeypatch.setenv("FAKE_CHECK_EXIT", "1")
    code, message = guard.check_stop({}, repo)
    assert code == guard.BLOCK and "rumbo check is red" in message and ran.exists()
    failures["left"] = 0
    # A replace that fails (the other hook holds the file) is retried, and no temp file stays.
    replace = os.replace
    refusals = {"left": 2}

    def busy(source: object, target: object) -> None:
        if refusals["left"] > 0:
            refusals["left"] -= 1
            raise PermissionError(13, "access denied")
        replace(source, target)  # type: ignore[arg-type]

    monkeypatch.setattr(guard.os, "replace", busy)
    state.write_text(json.dumps({"green": green}), encoding="utf-8")
    guard.record_pre_shell(repo)
    after = json.loads(read_bytes(state))
    assert after["green"] == green and after["head"] == green
    refusals["left"] = 10**6
    guard.record_pre_shell(repo)
    assert guard.check_post_shell(repo) is None
    assert json.loads(read_bytes(state))["green"] == green
    assert [p.name for p in state.parent.iterdir() if p.name.endswith(".tmp")] == []


# Covers F-0001.R5 and F-0001.R11 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
@pytest.mark.parametrize(
    "content",
    [b"\xff\xfe\x00garbage", b"", b"[]", b"[" * 100_000, b'{"green": 5, "head": {}}', b'"text"'],
)
def test_a_hostile_state_file_blocks_nothing_and_makes_stop_verify(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], content: bytes
) -> None:
    """G-2: a state that cannot be decoded (deeply nested JSON raised RecursionError and every
    shell call was refused) is unknown: no report, no refusal, and Stop runs the check."""
    monkeypatch.delenv("RUMBO_GUARD_SKIP_CHECK", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    (repo / "scripts").mkdir()
    (repo / "scripts" / "rumbo_check.py").write_text(FAKE_RUNNER, encoding="utf-8")
    (repo / ".gitignore").write_text("ran.txt\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "chore: the human adds the runner")
    state = repo / _git(repo, "rev-parse", "--git-path", "rumbo-guard-head").strip()
    shell = {"tool_name": "Bash", "tool_input": {"command": "git status"}}
    for event in ("pre-tool", "post-tool"):
        state.write_bytes(content)
        assert _hook(monkeypatch, event, shell) == 0, event
    assert capsys.readouterr() == ("", "")
    state.write_bytes(content)
    monkeypatch.setenv("FAKE_CHECK_EXIT", "1")
    assert guard.check_stop({}, repo)[0] == guard.BLOCK and (repo / "ran.txt").exists()
    # Green heals it: the state is valid again and the next Stop has nothing to verify.
    monkeypatch.setenv("FAKE_CHECK_EXIT", "0")
    assert guard.check_stop({}, repo) == (0, "")
    (repo / "ran.txt").unlink()
    assert guard.check_stop({}, repo) == (0, "") and not (repo / "ran.txt").exists()
    assert (
        json.loads(state.read_text(encoding="utf-8"))["green"]
        == _git(repo, "rev-parse", "HEAD").strip()
    )


# Covers F-0001.R4 and F-0001.R5 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_parallel_shell_hooks_keep_the_green_head(repo: Path) -> None:
    """G-2: three hooks at once, as when the agent sends three shell calls in one message."""
    import threading

    state = repo / _git(repo, "rev-parse", "--git-path", "rumbo-guard-head").strip()
    green = _git(repo, "rev-parse", "HEAD").strip()
    state.write_text(json.dumps({"green": green}), encoding="utf-8")
    errors: list[Exception] = []

    def hooks() -> None:
        try:
            for _ in range(8):
                guard.record_pre_shell(repo)
                assert guard.check_post_shell(repo) is None
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=hooks) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert json.loads(state.read_text(encoding="utf-8"))["green"] == green
    assert [p.name for p in state.parent.iterdir() if p.name.endswith(".tmp")] == []


# Covers F-0001.R4 and F-0001.R5 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_a_hidden_file_that_was_there_before_is_never_reported(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G-3: the human's own ignored pyrightconfig.json was reported after every command and at
    every Stop, which returned before the runner; and the agent may not remove the file."""
    monkeypatch.delenv("RUMBO_GUARD_SKIP_CHECK", raising=False)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "rumbo_check.py").write_text(FAKE_RUNNER, encoding="utf-8")
    (repo / ".gitignore").write_text("ran.txt\npyrightconfig.json\nuv.toml\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "chore: the editor configuration stays local")
    (repo / "pyrightconfig.json").write_text("{}\n", encoding="utf-8")
    (repo / "uv.toml").write_text("offline = true\n", encoding="utf-8")
    assert _clean(repo)
    # The first call ever has no baseline: it records what is there and stays silent.
    assert guard.check_post_shell(repo) is None
    for _ in range(2):
        guard.record_pre_shell(repo)
        assert guard.check_post_shell(repo) is None
    assert guard.check_post_bash(repo) is None
    # Stop goes on to the runner: red code is refused for being red, not for that file.
    (repo / "app.py").write_text("x = 'red'\n", encoding="utf-8")
    monkeypatch.setenv("FAKE_CHECK_EXIT", "1")
    code, message = guard.check_stop({}, repo)
    assert code == guard.BLOCK and "rumbo check is red" in message and (repo / "ran.txt").exists()
    assert "pyrightconfig.json" not in message
    # What changes or appears while a command runs is the command's, and is reported once.
    guard.record_pre_shell(repo)
    (repo / "pyrightconfig.json").write_text('{"typeCheckingMode": "off"}\n', encoding="utf-8")
    reason = guard.check_post_shell(repo) or ""
    assert "pyrightconfig.json (hidden from git status)" in reason and "uv.toml" not in reason
    assert guard.check_post_shell(repo) is None
    # A change between two commands is not a command's: the human edited their own file.
    (repo / "uv.toml").write_text("offline = false\n", encoding="utf-8")
    guard.record_pre_shell(repo)
    assert guard.check_post_shell(repo) is None


# Covers F-0001.R4 and F-0001.R10 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_a_repository_with_no_commit_has_nothing_to_protect_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G-4: after `git init` and `rumbo init`, every post-tool and Stop told the agent to revert
    or delete the harness. The first commit is exempt in the git hook; so is the tree before it."""
    _quiet_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RUMBO_GUARD_SKIP_CHECK", "1")
    root = tmp_path / "fresh"
    (root / ".claude").mkdir(parents=True)
    (root / "specs").mkdir()
    _git(root, "init", "-q", "-b", "main")
    for rel in ("Makefile", "pyproject.toml", ".claude/settings.json", "specs/0001-x.md"):
        (root / rel).write_text("x = 1\n", encoding="utf-8")
    assert guard.check_post_bash(root) is None
    guard.record_pre_shell(root)
    assert guard.check_post_shell(root) is None
    assert guard.check_stop({}, root) == (0, "")
    _git(root, "add", "-A")
    assert guard.check_post_bash(root) is None and guard.check_stop({}, root) == (0, "")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "commit", "-qm", "chore: initial RUMBO harness")
    # From the first commit on the tree is guarded, on an orphan branch too.
    (root / "Makefile").write_text("x = 2\n", encoding="utf-8")
    assert "Makefile" in (guard.check_post_bash(root) or "")
    assert guard.check_stop({}, root)[0] == guard.BLOCK
    _git(root, "checkout", "-q", "--orphan", "other")
    assert "Makefile" in (guard.check_post_bash(root) or "")


# Covers F-0001.R7 and F-0001.R14 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_an_agent_concludes_a_merge_that_brings_the_humans_protected_changes(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G-5: `git merge main` with a conflict in the code, where main changed the Makefile and
    signed a document. What is staged as it is in MERGE_HEAD comes from the merged branch."""
    _commit_documents(repo)
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "app.py").write_text("x = 'feature'\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "feat: the agent's work")
    _git(repo, "checkout", "-q", "main")
    (repo / "app.py").write_text("x = 'main'\n", encoding="utf-8")
    (repo / "Makefile").write_text("check: ; echo release\n", encoding="utf-8")
    (repo / "specs" / "0001-query.md").write_text("# F-0001, amended\n", encoding="utf-8")
    (repo / "docs" / "brief.md").write_text("# Brief, signed\n", encoding="utf-8")
    (repo / "docs" / "adr" / "0007-new.md").write_text("# ADR-0007, signed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "chore: the human's release")
    _git(repo, "checkout", "-q", "feature")
    merge = subprocess.run(["git", "merge", "main"], cwd=repo, capture_output=True, check=False)
    assert merge.returncode != 0 and (repo / ".git" / "MERGE_HEAD").is_file()
    (repo / "app.py").write_text("x = 'merged'\n", encoding="utf-8")
    _git(repo, "add", "-A")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert guard.check_pre_commit(repo) is None
    # Content that is in neither parent is the agent's, during a merge like at any other time.
    (repo / "Makefile").write_text("check: ; true\n", encoding="utf-8")
    _git(repo, "add", "-A")
    assert "protected file(s) staged: Makefile. " in (guard.check_pre_commit(repo) or "")
    _git(repo, "checkout", "MERGE_HEAD", "--", "Makefile")
    (repo / "specs" / "0001-query.md").write_text("# F-0001, the agent's\n", encoding="utf-8")
    _git(repo, "add", "-A")
    reason = guard.check_pre_commit(repo) or ""
    assert "specs/0001-query.md" in reason and "signed" in reason and "Makefile" not in reason
    _git(repo, "checkout", "MERGE_HEAD", "--", "specs/0001-query.md")
    assert guard.check_pre_commit(repo) is None
    # Without a merge in progress the same staged content is refused.
    _git(repo, "merge", "--abort")
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    (repo / "Makefile").write_text("check: ; echo release\n", encoding="utf-8")
    _git(repo, "add", "-A")
    assert "Makefile" in (guard.check_pre_commit(repo) or "")


# Covers F-0001.R4, F-0001.R13 and F-0001.R14.
def test_the_agent_is_never_told_to_undo_what_may_be_the_humans_work(repo: Path) -> None:
    """F-1: after the human runs `rumbo update`, the refreshed files differ from HEAD until he
    commits. The agent's next command was told to revert them; every reason now offers both
    routes, and names the human's first-hand."""
    _commit_documents(repo)
    pyproject = _commit_pyproject(repo)
    assert guard.check_post_bash(repo) is None
    (repo / "Makefile").write_text("check: ; echo updated\n", encoding="utf-8")
    protected = guard.check_post_bash(repo) or ""
    pyproject.write_text(PYPROJECT_HEAD.replace("line-length = 100", "line-length = 88"), "utf-8")
    (repo / "docs" / "brief.md").write_text("# Brief, signed\n", encoding="utf-8")
    (repo / "specs" / "0001-query.md").write_text("# F-0001, amended\n", encoding="utf-8")
    everything = guard.check_post_bash(repo) or ""
    assert "Revert them" not in everything
    assert "If you made this change, revert it" in protected
    assert "if the human made it (for example `rumbo update`)" in protected
    assert "ask them to commit it and do not revert it" in protected
    subjects = ("protected file(s) changed", "outside its dependency tables")
    subjects += ("template mark was removed", "signed by the human")
    assert all(subject in everything for subject in subjects)
    assert everything.count("do not revert it") == 4


# Covers F-0001.R1 and F-0001.R14 (specs/0001-the-agent-cannot-cheat-its-way-to-green.md).
def test_a_signed_document_is_signed_whatever_the_case_of_its_path(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a case-insensitive file system `SPECS/0001-x.md` opens the signed spec. Windows
    resolves the path to its real case; macOS keeps the typed one, so the rule compares without
    case and looks the committed name up by what the directory lists."""
    _commit_documents(repo)
    assert guard.SIGNED_DOC.match("SPECS/0001-Query.MD") and guard.SIGNED_DOC.match(
        "Docs/ADR/0001-x.md"
    )
    assert not guard.SIGNED_DOC.match("specs/0000-template.md")
    if not (repo / "SPECS").exists():
        pytest.skip("a case-sensitive file system: SPECS/ is another directory")
    # What POSIX `resolve()` returns on macOS: the path as typed.
    monkeypatch.setattr(guard, "_candidates", lambda file_path, root: ([root / file_path], True))
    for typed in ("SPECS/0001-query.md", "specs/0001-QUERY.md", "Docs/Adr/0001-Storage.MD"):
        reason = guard.check_edit("Edit", _edit(typed, "# ", "## "), repo) or ""
        assert "signed by the human" in reason, typed
    assert guard.check_edit("Edit", _edit("SPECS/0002-draft.md", "# ", "## "), repo) is None
    new = {"file_path": "SPECS/0009-new.md", "content": "# F-0009\n"}
    assert "ADR-0025" in (guard.check_edit("Write", new, repo) or "")
