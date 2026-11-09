"""Claude Code and git hooks for a RUMBO project: the "Hard rules" of AGENTS.md as sensors.

Wired by `.claude/settings.json` (copied by `rumbo init`, refreshed by `rumbo update`):

    PreToolUse   Edit|Write|MultiEdit|NotebookEdit|Bash  ->  rumbo_guard.py pre-tool
    PostToolUse  Bash                                     ->  rumbo_guard.py post-tool
    Stop                                                  ->  rumbo_guard.py stop

and by `.pre-commit-config.yaml` (ADR-0008), so any coding agent meets the same rules when it
commits, whatever editor or CLI it runs in:

    pre-commit   (git hook)  ->  rumbo_guard.py pre-commit
    commit-msg   (git hook)  ->  rumbo_guard.py commit-msg <message-file>

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
- Git hooks (deterministic, exit 1, the git convention): the actor is an agent when any variable
  of `[tool.rumbo].agent_env` is set (default: CLAUDECODE, GEMINI_CLI, CURSOR_AGENT,
  CODEX_SANDBOX_NETWORK_DISABLED), the human when `git config rumbo.human` is `true` in the
  clone (`rumbo init` sets it), and unknown otherwise, which counts as an agent. For an agent,
  `pre-commit` refuses a staged protected file (pyproject.toml and uv.lock excepted, as above)
  and a staged document whose `<!-- rumbo:template` mark was in HEAD and is gone; `commit-msg`
  refuses `[tests-change-approved]`. The human's commits, the root commit of a repository (the
  harness itself, nothing to protect yet) and a run with nothing staged (`pre-commit run
  --all-files` in CI) pass silently. The Bash heuristics also block
  `git config` on `rumbo.human`, so the agent cannot declare itself human.
- Signed documents (ADR-0017): a numbered ADR or spec committed without the template mark is
  signed by the human and read-only for the agent: an edit is blocked, a change or deletion
  through the shell is reported after the command (with any removed template mark), and the
  git hook refuses it. New documents and documents still under the mark stay editable.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

BLOCK = 2  # Claude Code hook protocol: the stderr message is shown to the agent
GIT_BLOCK = 1  # git hook convention
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
# ADR-0017: a numbered ADR or spec committed without the template mark is signed by the human
# and read-only for the agent; the 0000 templates and documents under the mark are not.
SIGNED_DOC = re.compile(r"^(?:specs|docs/adr)/(?!0000-)\d{4}-[^/]+\.md$")
CODE_SUFFIXES = (".py", ".toml", ".jsonl")
# Environment variables that mark a coding agent's shell; `[tool.rumbo].agent_env` overrides.
DEFAULT_AGENT_ENV = ("CLAUDECODE", "GEMINI_CLI", "CURSOR_AGENT", "CODEX_SANDBOX_NETWORK_DISABLED")
HUMAN_HINT = (
    "no agent marker in the environment and rumbo.human is not set in this clone, so the "
    "commit is treated as an agent's. A human declares themself once per clone with: "
    "git config rumbo.human true"
)

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
    (
        re.compile(r"\bgit\b[^|;&]*\brumbo\.human\b"),
        "only the human declares rumbo.human (git config); the agent leaves it alone",
    ),
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


def is_signed(root: Path, rel: str) -> bool:
    """A numbered ADR or spec whose committed version no longer carries the mark (ADR-0017).

    One `git show`, and only for paths that match the pattern: ordinary edits stay free of
    subprocesses. A new document (absent from HEAD) is not signed.
    """
    if not SIGNED_DOC.match(rel):
        return False
    head = _blob(root, f"HEAD:{rel}")
    return head is not None and TEMPLATE_MARK not in head


def _signed_reason(files: str) -> str:
    return (
        f"{files}: signed by the human (committed without the template mark), so read-only "
        "for the agent. Propose the change to the human, or write a new document that "
        "supersedes it (rumbo new adr|spec)."
    )


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
    if is_signed(root, rel):
        return _signed_reason(rel)
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


def changed_documents(root: Path, changed: list[str]) -> tuple[list[str], list[str]]:
    """(documents whose mark was in HEAD and is gone, signed ADRs/specs changed or deleted).

    ADR-0017: the deterministic check behind what a shell command can do to a document.
    """
    unmarked: list[str] = []
    signed: list[str] = []
    for rel in changed:
        if not rel.endswith(".md"):
            continue
        head = _blob(root, f"HEAD:{rel}")
        if head is None:
            continue  # a new (untracked) document: nothing in HEAD to compare with
        if TEMPLATE_MARK not in head:
            if SIGNED_DOC.match(rel):
                signed.append(rel)
            continue
        try:
            current = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # deleted or unreadable: not a removed mark
        if TEMPLATE_MARK not in current:
            unmarked.append(rel)
    return unmarked, signed


def check_post_bash(root: Path) -> str | None:
    changed = changed_files(root)
    touched = [f for f in changed if is_protected(f) and f not in BASH_MAY_CHANGE]
    unmarked, signed = changed_documents(root, changed)
    reasons: list[str] = []
    if touched:
        reasons.append(
            "protected file(s) changed since the last commit: "
            + ", ".join(touched)
            + ". Revert them (git checkout -- <file>, or delete the new file) and ask the human "
            "to make that change; the agent does not modify the harness."
        )
    if unmarked:
        reasons.append(
            "the template mark was removed since the last commit from: "
            + ", ".join(unmarked)
            + ". Only the human removes it; restore the first line (git checkout -- <file>), or "
            "ask the human to commit the signature if they made it."
        )
    if signed:
        reasons.append(
            _signed_reason(", ".join(signed))
            + " Revert the change (git checkout -- <file>), or ask the human to commit it if "
            "they made it."
        )
    return " ".join(reasons) or None


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


# ---------------------------------------------------------------- git hooks (ADR-0008)
def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Runs git in the project (output decoded as UTF-8); None when git is not installed."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return None


def agent_env_names(root: Path) -> tuple[str, ...]:
    """`[tool.rumbo].agent_env` from pyproject.toml, else the default markers."""
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_AGENT_ENV
    names = data.get("tool", {}).get("rumbo", {}).get("agent_env")
    if isinstance(names, list) and all(isinstance(n, str) for n in names):
        return tuple(str(n) for n in names)
    return DEFAULT_AGENT_ENV


def actor(root: Path) -> str:
    """'agent' (a marker is set), 'human' (`git config rumbo.human` is true) or 'unknown'."""
    if any(os.environ.get(name) for name in agent_env_names(root)):
        return "agent"
    proc = _git(root, "config", "--get", "rumbo.human")
    if proc is not None and proc.returncode == 0 and proc.stdout.strip() == "true":
        return "human"
    return "unknown"


def has_head(root: Path) -> bool:
    """False in a repository with no commit yet (the next commit is the root commit)."""
    proc = _git(root, "rev-parse", "--verify", "-q", "HEAD")
    return proc is not None and proc.returncode == 0


def staged_files(root: Path) -> list[str]:
    proc = _git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMRD")
    if proc is None or proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line]


def _blob(root: Path, spec: str) -> str | None:
    """`git show <spec>` as text, or None when the object does not exist (no HEAD, new file)."""
    proc = _git(root, "show", spec)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout


def _unmarked(root: Path, staged: list[str]) -> list[str]:
    """Staged files whose template mark was in HEAD and is gone from the index."""
    removed: list[str] = []
    for rel in staged:
        head = _blob(root, f"HEAD:{rel}")
        if head is None or TEMPLATE_MARK not in head:
            continue
        index = _blob(root, f":{rel}")
        if index is not None and TEMPLATE_MARK not in index:
            removed.append(rel)
    return removed


def check_pre_commit(root: Path) -> str | None:
    """Reason to refuse the commit (git pre-commit hook), or None to let it through."""
    who = actor(root)
    if who == "human":
        return None
    if not has_head(root):
        # The root commit IS the harness `rumbo init` just wrote (on the human's request, often
        # from inside the agent's session): there is no previous state to protect yet.
        return None
    staged = staged_files(root)
    if not staged:
        return None
    reason: str | None = None
    protected = [f for f in staged if is_protected(f) and f not in BASH_MAY_CHANGE]
    if protected:
        reason = (
            "protected file(s) staged: "
            + ", ".join(protected)
            + ". They are protected by AGENTS.md (hard rules): the agent does not edit the "
            "Makefile, pyproject.toml, CI, infra/, hooks, the sensors or .env files. Unstage them "
            "(git restore --staged <file>) and ask the human to make that change."
        )
    elif signed := [f for f in staged if is_signed(root, f)]:
        reason = _signed_reason(", ".join(signed))
    elif unmarked := _unmarked(root, staged):
        reason = (
            ", ".join(unmarked)
            + f": the '{TEMPLATE_MARK} ...' mark is removed only by the human, when the "
            "document is complete. Restore the first line before committing."
        )
    if reason and who == "unknown":
        reason += f"\n({HUMAN_HINT})"
    return reason


def check_commit_msg(root: Path, message_file: str | None) -> str | None:
    """Reason to refuse the commit message (git commit-msg hook), or None to let it through."""
    who = actor(root)
    if who == "human":
        return None
    if message_file is None:
        return "usage: rumbo_guard.py commit-msg <message-file>"
    try:
        message = Path(message_file).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"cannot read the commit message file {message_file}: {exc}"
    if APPROVAL_MARK not in message:
        return None
    reason = f"only the human writes {APPROVAL_MARK}; remove it from the commit message"
    if who == "unknown":
        reason += f"\n({HUMAN_HINT})"
    return reason


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    event = argv[0] if argv else "pre-tool"
    if event in ("pre-commit", "commit-msg"):  # git hooks: cwd is the repository root
        root = Path.cwd()
        if event == "pre-commit":
            refusal = check_pre_commit(root)
        else:
            refusal = check_commit_msg(root, argv[1] if len(argv) > 1 else None)
        if refusal:
            print(f"blocked by scripts/rumbo_guard.py: {refusal}", file=sys.stderr)
            return GIT_BLOCK
        return 0
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
