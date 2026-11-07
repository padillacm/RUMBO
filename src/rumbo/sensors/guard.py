"""Claude Code hooks for a RUMBO project: the "Hard rules" of AGENTS.md turned into sensors.

Wired by `.claude/settings.json` (copied by `rumbo init`, refreshed by `rumbo update`):

    PreToolUse   Edit|Write|MultiEdit|NotebookEdit|Bash  ->  rumbo_guard.py pre-tool
    PostToolUse  Bash                                     ->  rumbo_guard.py post-tool
    Stop                                                  ->  rumbo_guard.py stop

Claude Code sends the event as JSON on stdin. Exit code 0 lets the action through; exit code 2
blocks it and the message printed on stderr is shown to the agent, which then has to ask the
human or take another route. The hook never blocks the human: edits made by hand, outside
Claude Code, do not go through it. To disable a rule locally, override the hook in
`.claude/settings.local.json` (not committed).

What it enforces, and how strongly
- Edit/Write (deterministic): protected files are not edited by the agent (Makefile,
  pyproject.toml, CI workflows, infra/, the pre-commit config, .claude/, the anti-cheat sensor,
  this guard, the evals runner, `.env*`), and the `<!-- rumbo:template` mark is never removed.
- Bash, before the command (heuristic, friendly early message): `[tests-change-approved]`,
  `--no-verify`/`-n` on commit, SKIP=, force pushes, `git reset --hard`, disabling git hooks,
  `rm -rf` on the tree root or home, redirections/sed/tee into protected files. Regexes cannot
  win against a shell, so:
- Bash, after the command, and Stop (deterministic): any protected file that differs from HEAD
  (or is new) blocks with the list of files. `uv add`/`uv lock` are the exception: pyproject.toml
  and uv.lock may change through Bash. The binding layer outside Claude Code is CODEOWNERS plus
  the CI sensors.
- Stop: if Python/TOML/JSONL files changed since the last commit, the verification runner
  (`python scripts/rumbo_check.py --brief`, ADR-0006) must be green before the agent declares
  itself done (skipped when RUMBO_GUARD_SKIP_CHECK is set). Exit 1 (a verification failure)
  blocks with the failing tail; exit 3 (a broken environment: a tool missing or crashing) does
  not hand the agent a repair job. The hook then exits 0 and prints one JSON object on stdout,
  `{"decision": "block", "reason": ..., "systemMessage": ...}`, telling the agent to report the
  failure to the human and stop, and telling the human in one line. Projects generated before
  the runner existed fall back to `make check-brief`; without `make` the same environment route
  asks for `rumbo update`.
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
    "scripts/rumbo_check.py",
    "evals/run_evals.py",
)
PROTECTED_PREFIXES = (".github/", "infra/", ".claude/", ".env")
# Bash may change these (uv add / uv lock); Edit/Write may not.
BASH_MAY_CHANGE = ("pyproject.toml", "uv.lock")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
CODE_SUFFIXES = (".py", ".toml", ".jsonl")

# A protected path as a whole shell token (so Makefile.md or pyproject.toml.bak do not match).
_TARGET = (
    r"(?<![\w./-])(?:\./)?(?:"
    + "|".join(re.escape(f) for f in PROTECTED_FILES)
    + r"|\.env(?:\.\w+)*|\.github/\S*|infra/\S*|\.claude/\S*)(?![\w.-])"
)
_ROOTISH = r"(?:/|~/?|\.|\*|\$\{?(?:HOME|PWD)\}?/?)(?=\s|$|[;&|])"
BASH_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(re.escape(APPROVAL_MARK)), "only the human writes [tests-change-approved]"),
    (
        re.compile(r"\bgit\b[^|;&]*\s--no-verify(?=\s|$)"),
        "git hooks are sensors; do not bypass them (--no-verify / -n)",
    ),
    (
        # -n means --no-verify only for `git commit` (combined short flags included, -an);
        # elsewhere it is --dry-run / --max-count / --no-stat, which are harmless.
        re.compile(r"\bgit\b[^|;&]*\bcommit\b[^|;&]*\s-(?![umCcFt])[a-zA-Z]*n[a-zA-Z]*(?=\s|$)"),
        "git hooks are sensors; do not bypass them (--no-verify / -n)",
    ),
    (re.compile(r"(^|[\s;&|])SKIP=\S*[^|;&]*\bgit\b"), "SKIP= bypasses the pre-commit hooks"),
    (
        re.compile(r"\bgit\s+push\b[^|;&]*\s(--force(-with-lease)?|-f|\+\S+)(?=\s|$)"),
        "no force pushes (--force, -f, +branch)",
    ),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "git reset --hard discards work; ask the human"),
    (re.compile(r"\bgit\b[^|;&]*\bcore\.hooksPath\b"), "do not disable the git hooks"),
    (re.compile(r"\bpre-commit\s+uninstall\b"), "do not disable the git hooks"),
    (re.compile(r"\brm\b[^|;&]*\.git/hooks"), "do not delete the git hooks"),
    (
        re.compile(
            r"\brm\b(?:\s+-{1,2}[\w-]+)*?(?:\s-\w*[rR]\w*|\s--recursive)(?:\s+-{1,2}[\w-]+)*"
            r"\s+(?:--\s+)?" + _ROOTISH
        ),
        "recursive rm on the tree root, home or a glob: ask the human",
    ),
    (
        re.compile(r"(?:>>?|>\|)\s*" + _TARGET),
        "that file is protected (AGENTS.md hard rules); ask the human to change it",
    ),
    (
        re.compile(r"\b(?:sed\s+-i\S*|tee(?:\s+-\w+)*)\b[^|;&]*\s" + _TARGET),
        "that file is protected (AGENTS.md hard rules); ask the human to change it",
    ),
)


def _relative(file_path: str, root: Path) -> str | None:
    """Path relative to the project root (normalised), or None when it is outside the project."""
    path = Path(file_path)
    if not path.is_absolute():
        path = root / path
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
            "Explain the change you need and ask the human to make it (why: README.md, "
            "section 'Working with the agent')."
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


def changed_files(root: Path) -> list[str]:
    """Tracked files that differ from HEAD plus untracked files (ignored ones excluded)."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if proc.returncode != 0:
        return []
    return [line[3:].split(" -> ")[-1].strip() for line in proc.stdout.splitlines() if line]


def changed_code_files(root: Path) -> list[str]:
    return [f for f in changed_files(root) if f.endswith(CODE_SUFFIXES)]


def changed_protected_files(root: Path) -> list[str]:
    """Protected files changed since HEAD (the deterministic check behind the Bash heuristics)."""
    return [f for f in changed_files(root) if is_protected(f) and f not in BASH_MAY_CHANGE]


def check_post_bash(root: Path) -> str | None:
    touched = changed_protected_files(root)
    if not touched:
        return None
    return (
        "protected file(s) changed since the last commit: "
        + ", ".join(touched)
        + ". Revert them (git checkout -- <file>, or delete the new file) and ask the human to "
        "make that change; the agent does not modify the harness."
    )


def _tail(proc: subprocess.CompletedProcess[str]) -> str:
    return "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-40:])


def _red(runner: str, changed: int, tail: str) -> tuple[int, str]:
    return BLOCK, (
        f"{runner} is red after changing {changed} file(s). Fix it before declaring the "
        f"task done (full log in .rumbo-check.log):\n{tail}"
    )


def _environment(agent: str, human: str, tail: str = "") -> tuple[int, str]:
    """Exit 0 with the hook JSON: block the agent with a report-to-human reason, tell the human."""
    reason = (
        f"{agent} This is the verification environment, not your code: do NOT try to repair it "
        "(no installs, no config edits, no changes to pyproject.toml or the harness). Report "
        "the failing step and the output below to the human, then stop."
    )
    if tail:
        reason += f"\n{tail}"
    return 0, json.dumps({"decision": "block", "reason": reason, "systemMessage": human})


def run_verification(root: Path, changed: int) -> tuple[int, str]:
    """`scripts/rumbo_check.py --brief` (ADR-0006), else `make check-brief`, else ask for an update.

    Exit 0 green; exit 1 red (BLOCK with the tail); exit 3 or anything else is a broken
    environment reported to the human through the hook JSON, never a repair job for the agent.
    """
    runner = root / "scripts" / "rumbo_check.py"
    if runner.is_file():
        proc = subprocess.run(
            [sys.executable, "scripts/rumbo_check.py", "--brief"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return 0, ""
        if proc.returncode == 1:
            return _red("rumbo check", changed, _tail(proc))
        return _environment(
            f"rumbo check could not verify the project (exit {proc.returncode}): the "
            "verification environment is broken.",
            f"rumbo check: the verification environment is broken (exit {proc.returncode}); "
            "the agent was told to report it and stop. Details in .rumbo-check.log.",
            _tail(proc),
        )
    try:
        proc = subprocess.run(
            ["make", "check-brief"], cwd=root, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return _environment(
            "the project cannot be verified: scripts/rumbo_check.py is missing and make is not "
            "installed. Ask the human to run `rumbo update`, which adds the runner.",
            "The guard found neither scripts/rumbo_check.py nor make: run `rumbo update` to add "
            "the verification runner; the agent was told to report it and stop.",
        )
    if proc.returncode == 0:
        return 0, ""
    return _red("make check", changed, _tail(proc))


def check_stop(payload: dict[str, Any], root: Path) -> tuple[int, str]:
    """(exit code, message), mirroring the hook protocol: with BLOCK the message is the stderr
    reason; with 0 a non-empty message is the JSON object printed on stdout (environment route).

    Protected files intact first, then the verification runner if code files changed.
    """
    if payload.get("stop_hook_active"):
        return 0, ""  # a previous Stop hook already asked for a fix: do not loop
    if touched := check_post_bash(root):
        return BLOCK, touched
    if os.environ.get("RUMBO_GUARD_SKIP_CHECK"):
        return 0, ""
    changed = changed_code_files(root)
    if not changed:
        return 0, ""
    return run_verification(root, len(changed))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    event = argv[0] if argv else "pre-tool"
    try:
        payload: dict[str, Any] = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except (json.JSONDecodeError, OSError):
        payload = {}
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or str(payload.get("cwd") or Path.cwd()))
    if event == "stop":
        code, message = check_stop(payload, root)
        if message:  # exit 2: the reason on stderr; exit 0: the hook JSON, alone on stdout
            print(message, file=sys.stderr if code else sys.stdout)
        return code
    tool_name = str(payload.get("tool_name", ""))
    tool_input: dict[str, Any] = dict(payload.get("tool_input") or {})
    reason: str | None = None
    if event == "post-tool":
        reason = check_post_bash(root) if tool_name == "Bash" else None
    elif tool_name in EDIT_TOOLS:
        reason = check_edit(tool_name, tool_input, root)
    elif tool_name == "Bash":
        reason = check_bash(str(tool_input.get("command", "")))
    if reason:
        print(reason, file=sys.stderr)
        return BLOCK
    return 0


if __name__ == "__main__":
    sys.exit(main())
