"""The Claude Code guard hook: AGENTS.md hard rules as sensors."""

import io
import json
import subprocess
from pathlib import Path

import pytest

from rumbo.scaffold import Config, plan_files
from rumbo.sensors import guard


def _edit(path: str, old: str = "a", new: str = "b") -> dict[str, object]:
    return {"file_path": path, "old_string": old, "new_string": new}


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


def test_relative_paths_are_normalised(tmp_path: Path) -> None:
    assert guard.check_edit("Edit", _edit("src/../Makefile"), tmp_path)
    assert guard.check_edit("Edit", _edit("./.github/workflows/ci.yml"), tmp_path)
    assert guard.check_edit("Edit", _edit("src/demo/x.py"), tmp_path) is None


def test_ordinary_files_and_files_outside_the_project_are_allowed(tmp_path: Path) -> None:
    inside = _edit(str(tmp_path / "src/demo/domain/x.py"))
    assert guard.check_edit("Edit", inside, tmp_path) is None
    outside = {"file_path": "/elsewhere/file.py", "content": ""}
    assert guard.check_edit("Write", outside, tmp_path) is None
    assert guard.check_edit("Edit", {}, tmp_path) is None


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
    # Keeping the mark is fine, and so is writing a brand-new document without it.
    keep = {"file_path": str(doc), "content": "<!-- rumbo:template -->\n# F-0001\nmore\n"}
    assert guard.check_edit("Write", keep, tmp_path) is None
    new = {"file_path": str(tmp_path / "specs" / "0002-y.md"), "content": "# F-0002\n"}
    assert guard.check_edit("Write", new, tmp_path) is None


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


def test_post_bash_detects_protected_files_changed_since_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status = " M Makefile\n?? .github/workflows/x.yml\n M pyproject.toml\n M src/demo/a.py\n"
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 0, status, ""),
    )
    reason = guard.check_post_bash(tmp_path) or ""
    assert "Makefile" in reason and ".github/workflows/x.yml" in reason
    assert "pyproject.toml" not in reason  # uv add is allowed to change it
    # Stop refuses first for the protected files, before running make
    code, message = guard.check_stop({}, tmp_path)
    assert code == guard.BLOCK and "protected" in message
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 0, " M pyproject.toml\n", ""),
    )
    assert guard.check_post_bash(tmp_path) is None


def test_git_missing_never_crashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_git(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("git")

    monkeypatch.setattr(guard.subprocess, "run", no_git)
    assert guard.changed_files(tmp_path) == []
    assert guard.check_stop({}, tmp_path) == (0, "")


def test_stop_runs_make_check_only_when_code_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 0, " M src/demo/x.py\n?? notes.md\n", "")
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
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 0, "?? notes.md\n", ""),
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


def test_stop_passes_when_the_runner_is_green(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FAKE_CHECK_EXIT", "0")
    assert _stop(monkeypatch) == 0
    out, err = capsys.readouterr()
    assert out == "" and err == ""
    assert (project / "ran.txt").read_text(encoding="utf-8") == "--brief"


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
    assert err == "" and calls == [["make", "check-brief"]]
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
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 0, " M infra/Dockerfile\n", ""),
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
    assert settings["hooks"]["PostToolUse"][0]["matcher"] == "Bash"
    assert "Bash(make check)" in settings["permissions"]["allow"]
    assert "Bash(uv run python scripts/rumbo_check.py *)" in settings["permissions"]["allow"]
    assert "Bash(rumbo check *)" in settings["permissions"]["allow"]
    assert "Edit(./.claude/**)" in settings["permissions"]["deny"]


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


@pytest.mark.parametrize("marker", guard.DEFAULT_AGENT_ENV)
def test_actor_is_an_agent_when_a_marker_is_set(
    repo: Path, monkeypatch: pytest.MonkeyPatch, marker: str
) -> None:
    assert guard.actor(repo) == "unknown"
    monkeypatch.setenv(marker, "1")
    assert guard.actor(repo) == "agent"
    _git(repo, "config", "rumbo.human", "true")
    assert guard.actor(repo) == "agent"  # the marker wins over the clone's declaration


def test_actor_is_the_human_who_declared_rumbo_human(repo: Path) -> None:
    assert guard.actor(repo) == "unknown"
    _git(repo, "config", "rumbo.human", "true")
    assert guard.actor(repo) == "human"
    _git(repo, "config", "rumbo.human", "false")
    assert guard.actor(repo) == "unknown"


def test_agent_markers_come_from_pyproject_when_declared(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert guard.agent_env_names(repo) == guard.DEFAULT_AGENT_ENV  # no pyproject.toml
    pyproject = repo / "pyproject.toml"
    pyproject.write_text('[tool.rumbo]\nagent_env = ["MY_AGENT"]\n', encoding="utf-8")
    assert guard.agent_env_names(repo) == ("MY_AGENT",)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert guard.actor(repo) == "unknown"  # the list replaces the default, it does not extend it
    monkeypatch.setenv("MY_AGENT", "1")
    assert guard.actor(repo) == "agent"
    pyproject.write_text('[tool.rumbo]\nagent_env = "MY_AGENT"\n', encoding="utf-8")
    assert guard.agent_env_names(repo) == guard.DEFAULT_AGENT_ENV  # not a list: ignored
    pyproject.write_text("not toml [", encoding="utf-8")
    assert guard.agent_env_names(repo) == guard.DEFAULT_AGENT_ENV


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


def test_pre_commit_allows_uv_writes_and_lists_every_other_protected_file(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_CLI", "1")
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
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


def test_pre_commit_passes_silently_with_nothing_staged(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    (repo / "Makefile").write_text("unstaged change\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    assert guard.main(["pre-commit"]) == 0  # what `pre-commit run --all-files` sees in CI
    assert capsys.readouterr() == ("", "")


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


def test_git_hooks_without_git_never_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_git(cmd: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError("git")

    _quiet_env(monkeypatch, tmp_path)
    monkeypatch.setattr(guard.subprocess, "run", no_git)
    assert guard.actor(tmp_path) == "unknown"
    assert guard.staged_files(tmp_path) == []
    assert guard.check_pre_commit(tmp_path) is None
