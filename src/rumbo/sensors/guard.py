"""Claude Code hooks for a RUMBO project: the "Hard rules" of AGENTS.md turned into sensors.

Wired by `.claude/settings.json` (copied by `rumbo init`, refreshed by `rumbo update`):

    PreToolUse  Edit|Write|MultiEdit|NotebookEdit|Bash  ->  python scripts/rumbo_guard.py pre-tool
    Stop                                                 ->  python scripts/rumbo_guard.py stop

Claude Code sends the event as JSON on stdin. Exit code 0 lets the action through; exit code 2
blocks it and the message printed on stderr is shown to the agent, which then has to ask the
human or take another route. The hook never blocks the human: edits made by hand, outside
Claude Code, do not go through it. To disable a rule locally, override the hook in
`.claude/settings.local.json` (not committed).

What it enforces
- Protected files are not edited by the agent: Makefile, pyproject.toml, CI workflows, infra/,
  the pre-commit config, the anti-cheat sensor, this guard, the evals runner, `.env*`.
  (`uv add` still works: it edits pyproject.toml through Bash, which only the Bash rules see.)
- The `<!-- rumbo:template` mark is never removed by the agent (the human removes it).
- No `[tests-change-approved]` written by the agent, no `--no-verify`, no force push, no
  `git reset --hard`, no disabling of git hooks, no `rm -rf` on the tree root or home, and no
  shell redirections into the protected files.
- Stop: if Python/TOML/JSONL files changed since the last commit, `make check-brief` must be
  green before the agent declares itself done (skipped when RUMBO_GUARD_SKIP_CHECK is set).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

BLOCK = 2
TEMPLATE_MARK = "<!-- rumbo:template"
APPROVAL_MARK = "[tests-change-approved]"

PROTECTED_FILES = (
    "Makefile",
    "pyproject.toml",
    ".pre-commit-config.yaml",
    ".claude/settings.json",
    "scripts/check_tests_not_weakened.py",
    "scripts/rumbo_guard.py",
    "evals/run_evals.py",
)
PROTECTED_PREFIXES = (".github/", "infra/", ".env")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
CODE_SUFFIXES = (".py", ".toml", ".jsonl")

_PROTECTED_RE = "|".join(
    re.escape(p) for p in (*PROTECTED_FILES, *(f"{p}" for p in PROTECTED_PREFIXES))
)
BASH_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(re.escape(APPROVAL_MARK)), "only the human writes [tests-change-approved]"),
    (re.compile(r"\bgit\b.*\s--no-verify\b"), "git hooks are sensors; do not bypass them"),
    (re.compile(r"\bgit\s+push\b.*\s(--force(-with-lease)?|-f)\b"), "no force pushes"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "git reset --hard discards work; ask the human"),
    (re.compile(r"\bgit\s+config\b.*\bcore\.hooksPath\b"), "do not disable the git hooks"),
    (re.compile(r"\bpre-commit\s+uninstall\b"), "do not disable the git hooks"),
    (
        re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+(/|~|\.\s|\.$|\*|\$)"),
        "rm -rf on the tree root, home or a glob: ask the human",
    ),
    (
        re.compile(rf"(>>?|\bsed\s+-i\b.*|\btee\b.*)\s*(\./)?({_PROTECTED_RE})"),
        "that file is protected (AGENTS.md hard rules); ask the human to change it",
    ),
)


def _relative(file_path: str, root: Path) -> str | None:
    """Path relative to the project root, or None when the file is outside the project."""
    path = Path(file_path)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def is_protected(rel: str) -> bool:
    return rel in PROTECTED_FILES or rel.startswith(PROTECTED_PREFIXES)


def _removes_mark(tool_name: str, tool_input: dict[str, Any], target: Path) -> bool:
    if tool_name == "Write":
        content = str(tool_input.get("content", ""))
        if not target.exists():
            return False
        try:
            current = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return TEMPLATE_MARK in current and TEMPLATE_MARK not in content
    edits: list[dict[str, Any]] = (
        list(tool_input.get("edits") or []) if tool_name == "MultiEdit" else [tool_input]
    )
    return any(
        TEMPLATE_MARK in str(e.get("old_string", ""))
        and TEMPLATE_MARK not in str(e.get("new_string", ""))
        for e in edits
    )


def check_edit(tool_name: str, tool_input: dict[str, Any], root: Path) -> str | None:
    """Reason to block an Edit/Write/MultiEdit/NotebookEdit call, or None to allow it."""
    file_path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if not file_path:
        return None
    rel = _relative(file_path, root)
    if rel is None:
        return None
    if is_protected(rel):
        return (
            f"{rel} is protected by AGENTS.md (hard rules): the agent does not edit the "
            "Makefile, pyproject.toml, CI, infra/, hooks, the sensors or .env files. "
            "Explain the change you need and ask the human to make it."
        )
    if _removes_mark(tool_name, tool_input, root / rel):
        return (
            f"{rel}: the '{TEMPLATE_MARK} ...' mark is removed only by the human, when the "
            "document is complete. Leave the first line as it is."
        )
    return None


def check_bash(command: str) -> str | None:
    """Reason to block a Bash command, or None to allow it."""
    for pattern, reason in BASH_RULES:
        if pattern.search(command):
            return f"blocked by scripts/rumbo_guard.py: {reason}"
    return None


def changed_code_files(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    files = [line[3:].split(" -> ")[-1].strip() for line in proc.stdout.splitlines() if line]
    return [f for f in files if f.endswith(CODE_SUFFIXES)]


def check_stop(payload: dict[str, Any], root: Path) -> tuple[int, str]:
    """(exit code, message). Runs `make check-brief` when code changed since the last commit."""
    if payload.get("stop_hook_active"):
        return 0, ""  # a previous Stop hook already asked for a fix: do not loop
    if os.environ.get("RUMBO_GUARD_SKIP_CHECK"):
        return 0, ""
    changed = changed_code_files(root)
    if not changed:
        return 0, ""
    try:
        proc = subprocess.run(
            ["make", "check-brief"], cwd=root, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return 0, "rumbo_guard: 'make' not found; run make check by hand"
    if proc.returncode == 0:
        return 0, ""
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-40:])
    return BLOCK, (
        f"make check is red after changing {len(changed)} file(s). Fix it before declaring the "
        f"task done (full log in .rumbo-check.log):\n{tail}"
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    event = argv[0] if argv else "pre-tool"
    try:
        payload: dict[str, Any] = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except (json.JSONDecodeError, OSError):
        payload = {}
    root = Path(str(payload.get("cwd") or Path.cwd()))
    if event == "stop":
        code, message = check_stop(payload, root)
        if message:
            print(message, file=sys.stderr)
        return code
    tool_name = str(payload.get("tool_name", ""))
    tool_input: dict[str, Any] = dict(payload.get("tool_input") or {})
    reason: str | None = None
    if tool_name in EDIT_TOOLS:
        reason = check_edit(tool_name, tool_input, root)
    elif tool_name == "Bash":
        reason = check_bash(str(tool_input.get("command", "")))
    if reason:
        print(reason, file=sys.stderr)
        return BLOCK
    return 0


if __name__ == "__main__":
    sys.exit(main())
