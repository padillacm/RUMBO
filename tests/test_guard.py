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
        ".env",
        ".env.local",
        "evals/run_evals.py",
    ],
)
def test_protected_files_are_blocked(tmp_path: Path, rel: str) -> None:
    reason = guard.check_edit("Edit", _edit(str(tmp_path / rel)), tmp_path)
    assert reason and "protected" in reason


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
        "git push --force origin main",
        "git push -f",
        "git push origin main --force-with-lease",
        "git reset --hard HEAD~1",
        "git config core.hooksPath /dev/null",
        "uv run pre-commit uninstall",
        "rm -rf /",
        "rm -rf ~",
        "rm -rf .",
        "rm -rf *",
        "echo x > Makefile",
        "echo x >> ./scripts/check_tests_not_weakened.py",
        "sed -i 's/strict/off/' pyproject.toml",
        "cat x | tee .github/workflows/ci.yml",
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
        "uv add httpx",
        "echo x > notes.txt",
        "grep -n Makefile README.md",
    ],
)
def test_ordinary_commands_are_allowed(command: str) -> None:
    assert guard.check_bash(command) is None


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
    assert any("rumbo_guard.py pre-tool" in c for c in commands)
    assert any("rumbo_guard.py stop" in c for c in commands)
    assert "Bash(make check)" in settings["permissions"]["allow"]
