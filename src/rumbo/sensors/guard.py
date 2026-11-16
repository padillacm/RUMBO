"""Claude Code and git hooks for a RUMBO project: the "Hard rules" of AGENTS.md as sensors.

Wired by `.claude/settings.json` (copied by `rumbo init`, refreshed by `rumbo update`):

    PreToolUse   Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell  ->  rumbo_guard.py pre-tool
    PostToolUse  Bash|PowerShell                                     ->  rumbo_guard.py post-tool
    Stop                                                             ->  rumbo_guard.py stop

and by `.pre-commit-config.yaml` (ADR-0008), so any coding agent meets the same rules when it
commits, whatever editor or CLI it runs in:

    pre-commit   (git hook)  ->  rumbo_guard.py pre-commit
    commit-msg   (git hook)  ->  rumbo_guard.py commit-msg <message-file>

Claude Code sends the event as JSON on stdin (read as UTF-8 bytes, whatever the locale). Exit
code 0 lets the action through; exit code 2 blocks it and the message printed on stderr is shown
to the agent, which then has to ask the human or take another route. The hook never blocks the
human: edits made by hand, outside Claude Code, do not go through it. To disable a rule locally,
override the hook in `.claude/settings.local.json` (not committed).

What it enforces, and how strongly
- Edit/Write (deterministic): protected files are not edited by the agent: the Makefile,
  pyproject.toml and the files a tool reads before it (ADR-0023: ruff.toml, pytest.ini, tox.ini,
  setup.cfg, mypy.ini, pyrightconfig.json, .coveragerc, .importlinter, uv.toml, in any directory),
  CI workflows, infra/, the pre-commit config, .claude/, the git directory, the anti-cheat
  sensor, this guard, the evals runner and `.env` files. Names are compared without case and
  without the spellings Windows folds (`Makefile.`, `Makefile::$DATA`, `\\\\?\\C:\\...`); a path
  that cannot be placed inside or outside the project is blocked when its name is a protected
  one. The `<!-- rumbo:template` mark is never removed (the edit is simulated on the file), and
  a numbered ADR or spec is never written without it (ADR-0025).
- Shell tools, before the command (heuristic, friendly early message): every tool in
  `SHELL_TOOLS` is judged alike. `[tests-change-approved]`, `--no-verify` (and the abbreviations
  git accepts) or `-n` on commit, SKIP=, git configuration through `-c`, `git config` or
  `GIT_CONFIG_*` that disables the hooks, declares the human or defines an alias, force pushes,
  `git reset --hard`, recursive rm on the tree root or home, writes into protected files or the
  git directory (redirections, sed -i, tee, Set-Content, Out-File, Remove-Item...),
  `rumbo verify-dep --index` and `git diff --no-index`. Regexes cannot win against a shell, so:
- Shell tools, after the command, and Stop (deterministic): any protected file that differs
  from HEAD (or is new) blocks with the list of files, including the ones `git status` cannot
  see: a tracked file under `--skip-worktree` or `--assume-unchanged`, and a new Makefile or
  tool configuration hidden by an ignore rule. `uv add`/`uv remove`/`uv lock` are the exception,
  and a narrow one (ADR-0019): uv.lock is free, and pyproject.toml may change only in its
  dependency tables (`project.dependencies`, `project.optional-dependencies`,
  `dependency-groups`, `tool.uv`). The version in HEAD and the current one are parsed and
  compared without those keys; anything else that differs (`tool.rumbo`, `tool.ruff`,
  `project.version`...) is reported by its dotted path, and so is a pyproject.toml that cannot
  be parsed or that HEAD does not have. The binding layer outside Claude Code is CODEOWNERS
  plus the CI sensors.
- HEAD is remembered (ADR-0024). Before a shell command the guard writes HEAD to a state file
  inside the git directory (`git rev-parse --git-path rumbo-guard-head`, never the working
  tree); after it, if HEAD moved, the difference between the two commits is judged by the rules
  of a staged change: protected files, the tables of pyproject.toml, signed documents, removed
  marks. A commit made without the hook (plumbing, a cherry-picked stash, a disabled hook) is
  reported even though the tree is clean. The same file remembers a digest of
  `.claude/settings.local.json` without its `permissions` (what the human's "always allow"
  writes): a change made while the command ran is reported.
- Stop: if Python/TOML/JSONL files changed since the last commit, or were committed since the
  last HEAD that a Stop saw green (ADR-0024), the verification runner
  (`python scripts/rumbo_check.py --brief`, ADR-0006) must be green before the agent declares
  itself done (skipped when RUMBO_GUARD_SKIP_CHECK is set). Exit 1 (a verification failure)
  blocks with the failing tail; exit 3 (a broken environment: a tool missing or crashing) does
  not hand the agent a repair job. The hook then exits 0 and prints one JSON object on stdout,
  `{"decision": "block", "reason": ..., "systemMessage": ...}`, telling the agent to report the
  failure to the human and stop, and telling the human in one line. Projects generated before
  the runner existed fall back to `make check-brief`; without `make` the same environment route
  asks for `rumbo update`.
- Git hooks (deterministic, exit 1, the git convention): the actor is an agent when any variable
  of `[tool.rumbo].agent_env` is set (default: the keys of `AGENT_ENV_SOURCES`, ADR-0021; the
  list is read from HEAD, ADR-0026), the human when `git config rumbo.human` is `true` in the
  clone (`rumbo init` sets it for a human at a terminal), and unknown otherwise, which counts as
  an agent. For an agent, `pre-commit` refuses a staged protected file (uv.lock and the
  dependency tables of pyproject.toml excepted, as above, comparing HEAD with the staged blob;
  renames are split and type changes count), a staged document whose `<!-- rumbo:template` mark
  was in HEAD and is gone, and a new numbered ADR or spec without the mark; `commit-msg`
  refuses `[tests-change-approved]`. The human's commits, the first commit of a repository with
  no commit on any ref (the harness itself, nothing to protect yet) and a run with nothing
  staged (`pre-commit run --all-files` in CI) pass silently.
- Signed documents (ADR-0017): a numbered ADR or spec committed without the template mark is
  signed by the human and read-only for the agent: an edit is blocked, a change or deletion
  through the shell is reported after the command (with any removed template mark), and the
  git hook refuses it. Documents still under the mark stay editable.

When the guard itself fails
- No information is not a failure: without git, outside a repository or with an unreadable
  state file, the checks that need it are skipped and the action goes through.
- An internal error never reads as "allow" (Claude Code treats any exit code other than 0 and 2
  as a non-blocking error). `pre-tool` refuses the call and `post-tool` reports, both with exit
  2 and the error; `stop` takes the environment route (the agent reports it and stops instead
  of repairing the guard); the git hooks refuse the commit with one readable line.
- A hook that runs out of time lets the call through, so the shell rules are bounded and fail
  closed: a single command too long to judge, or one not judged within the budget, is refused.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

BLOCK = 2  # Claude Code hook protocol: the stderr message is shown to the agent
GIT_BLOCK = 1  # git hook convention
TEMPLATE_MARK = "<!-- rumbo:template"
APPROVAL_MARK = "[tests-change-approved]"
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
# Every tool that runs a shell command (in `tool_input["command"]`) is judged like Bash.
SHELL_TOOLS = ("Bash", "PowerShell")

PROTECTED_FILES = (
    "Makefile",
    "GNUmakefile",  # make reads GNUmakefile, then makefile, before Makefile (ADR-0023)
    "makefile",
    "pyproject.toml",
    ".pre-commit-config.yaml",
    ".claude/settings.json",
    "scripts/check_tests_not_weakened.py",
    "scripts/rumbo_guard.py",
    "scripts/rumbo_check.py",
    "evals/run_evals.py",
)
# ADR-0023: files a tool reads before, or instead of, its table in pyproject.toml. Protected by
# name in any directory: ruff and pytest take the configuration nearest to the file they check.
# conftest.py is not here (an agent writes fixtures there); the anti-cheat sensor watches it.
SHADOW_CONFIGS = (
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
)
# `.git/` holds the hooks and the clone's configuration; `.github/`, `.gitignore` and
# `.gitattributes` are other names and stay as they were.
PROTECTED_PREFIXES = (".github/", "infra/", ".claude/", ".git/")
# `.env` and `.env.<name>` at the project root; `.envrc` and `.environment.yml` are not env files.
_ENV_FILE = re.compile(r"\.env(?:\.[^/]*)?")
_FILES = frozenset(name.casefold() for name in PROTECTED_FILES)
_SHADOWS = frozenset(name.casefold() for name in SHADOW_CONFIGS)
_BASENAMES = frozenset(name.rsplit("/", 1)[-1] for name in _FILES) | _SHADOWS
# Ignored and legitimately local; judged by what changes in it while a command runs.
LOCAL_SETTINGS = ".claude/settings.local.json"
# ADR-0019: what `uv add`, `uv remove` and `uv lock` write. uv.lock is not protected at all;
# in pyproject.toml only these keys may change through Bash or in an agent's commit (an allow
# list: every other table, known today or not, is protected). Edit/Write may change none.
PYPROJECT = "pyproject.toml"
PYPROJECT_FREE_KEYS = (
    ("project", "dependencies"),
    ("project", "optional-dependencies"),
    ("dependency-groups",),
    ("tool", "uv"),
)
PYPROJECT_UNPARSEABLE = f"{PYPROJECT} (cannot be parsed)"
PYPROJECT_NEW = f"{PYPROJECT} (new file)"
PYPROJECT_DELETED = f"{PYPROJECT} (deleted)"
# ADR-0017: a numbered ADR or spec committed without the template mark is signed by the human
# and read-only for the agent; the 0000 templates and documents under the mark are not.
SIGNED_DOC = re.compile(r"^(?:specs|docs/adr)/(?!0000-)\d{4}-[^/]+\.md$")
CODE_SUFFIXES = (".py", ".toml", ".jsonl")
# ADR-0024: the guard's memory, one small JSON file inside the git directory.
STATE_FILE = "rumbo-guard-head"
HIDDEN = " (hidden from git status)"
# Environment variables that mark a coding agent's shell; `[tool.rumbo].agent_env` overrides.
# ADR-0021: each one is confirmed in the tool's own documentation or source, never from memory.
AGENT_ENV_SOURCES = {
    "CLAUDECODE": "https://code.claude.com/docs/en/env-vars",
    "GEMINI_CLI": (
        "https://github.com/google-gemini/gemini-cli/blob/main/"
        "packages/core/src/services/shellExecutionService.ts"
    ),
    "CURSOR_AGENT": "https://cursor.com/docs/agent/tools/terminal",
    # Only set when the network is sandboxed; the next two are unconditional.
    "CODEX_SANDBOX_NETWORK_DISABLED": (
        "https://github.com/openai/codex/blob/main/codex-rs/core/src/spawn.rs"
    ),
    "CODEX_THREAD_ID": (
        "https://github.com/openai/codex/blob/main/codex-rs/protocol/src/shell_environment.rs"
    ),
    "CODEX_CI": (
        "https://github.com/openai/codex/blob/main/"
        "codex-rs/core/src/unified_exec/process_manager.rs"
    ),
    "ROO_ACTIVE": (
        "https://github.com/RooCodeInc/Roo-Code/blob/main/src/integrations/terminal/Terminal.ts"
    ),
    "CLINE_ACTIVE": (
        "https://github.com/cline/cline/blob/main/"
        "apps/vscode/src/hosts/vscode/terminal/VscodeTerminalRegistry.ts"
    ),
}
DEFAULT_AGENT_ENV = tuple(AGENT_ENV_SOURCES)
HUMAN_HINT = (
    "no agent marker in the environment and rumbo.human is not set in this clone, so the "
    "commit is treated as an agent's. A human declares themself once per clone with: "
    "git config rumbo.human true"
)


# ------------------------------------------------- shell rules (heuristic, before the command)
def _names(names: tuple[str, ...]) -> str:
    """Alternation of paths, either separator accepted (PowerShell writes `.github\\x`)."""
    return "|".join(re.escape(name).replace("/", r"[/\\]") for name in names)


def _word(body: str) -> str:
    """`body` as a whole shell word, bare or quoted, up to a separator or the end."""
    return rf"""(?:{body}|"{body}"|'{body}')(?=\s|$|[;&|)])"""


def _flag(body: str) -> str:
    return r"\s" + _word(body)


def _rule(pattern: str, reason: str, flags: int = 0) -> tuple[re.Pattern[str], str]:
    return re.compile(pattern, flags), reason


MAX_SIMPLE_COMMAND = 5000  # characters between two of `| ; &` or line breaks
RULES_BUDGET = 3.0  # seconds; the hook's own timeout is 20
_SEPARATOR = re.compile(r"[|;&\n]")
_SEG = r"[^|;&\n]*"  # the rest of one simple command
_ARGS = r"[^|;&\n<>]*"  # the same, up to a redirection
_WORD = r"""(?:'[^']*'|"[^"]*"|[^\s;&|'"])+"""
# `git`, its global options (the ones that take a value consume it) and the blank before the
# subcommand: `git -C . -c user.name=x commit` is a commit, `git log --grep commit` is not.
# The option list is an atomic group (one reading of it): no backtracking on a long command.
_GIT = (
    r"""\bgit(?:\.exe)?["']?(?>(?:\s+(?:(?:-[cC]|--(?:git-dir|work-tree|namespace|super-prefix"""
    r"|config-env|attr-source))(?:\s+|=)" + _WORD + r"|--?\w[\w-]*(?:=" + _WORD + r")?))*)\s+"
)
# A protected path as a whole shell token (so Makefile.md or pyproject.toml.bak do not match).
_TARGET = (
    r"""(?<![\w./\\-])["']?(?:\.[/\\])?(?:"""
    + _names(PROTECTED_FILES)
    + r"|(?:[\w.-]+[/\\])*(?:"
    + _names(SHADOW_CONFIGS)
    + r")|\.env(?:\.[\w-]+)*|(?:\.github|infra|\.claude)[/\\]\S*|\.git(?:[/\\]\S*)?)(?![\w.-])"
)
_GIT_DIR = r"""(?<![\w.-])\.git(?=[/\\]|\s|$|["';&|)])"""
# A `.env` file as an argument, in any directory; the committed examples are not secrets.
_ENV_ARGUMENT = (
    r"""["']?(?:[^\s"']*[/\\])?\.env(?!\.(?:example|sample|template|dist)\b)(?:\.[\w-]+)*["']?"""
    r"(?=\s|$|[;&|)])"
)
_ROOTISH = (
    r"""["']?(?:/\*?|~(?:[/\\]\*?)?|\.{1,2}[/\\]?|\.[/\\]\*|\*|[a-zA-Z]:[/\\]?\*?"""
    r"""|\$\{?(?:HOME|PWD|env:USERPROFILE)\}?(?:[/\\]\*?)?|\$\(pwd\))["']?(?=\s|$|[;&|)])"""
)
# Commands that write, truncate or remove their arguments, in Bash and in PowerShell.
_WRITERS = (
    r"(?:\bsed\b" + _ARGS + r"\s(?:-[a-zA-Z]*i\S*|--in-place\S*)"
    r"|\bperl\b" + _ARGS + r"\s-[a-zA-Z]*i[a-zA-Z]*"
    r"|(?<!git )\b(?:tee|rm|unlink|truncate|Set-Content|Add-Content|Clear-Content|Out-File"
    r"|Remove-Item|Rename-Item|Move-Item)\b)"
)
_MOVERS = (
    r"(?<!git )\b(?:rm|mv|cp|chmod|chown|ln|unlink|truncate|rmdir|del|Remove-Item|Move-Item"
    r"|Copy-Item|Rename-Item|New-Item|Set-Content|Add-Content|Clear-Content|Out-File)\b"
)
# `git config` that only reads: `get`/`list` as the subcommand, or a --get*/--list/-l option.
_CONFIG_READS = (
    r"(?!(?>(?:\s+--?\w[\w-]*(?:=\S+)?)*)\s+(?:get|list)\b)"
    r"(?!" + _SEG + r"\s(?:--get[\w-]*|--list|-l)(?=\s|$))"
)
_BYPASS = "git hooks are sensors; do not bypass them (--no-verify / -n)"
_PROTECTED = "that file is protected (AGENTS.md hard rules); ask the human to change it"
_NO_HOOKS = "do not disable the git hooks"
_SKIP = "SKIP= bypasses the pre-commit hooks"


def _config_rules(key: str, reason: str) -> tuple[tuple[re.Pattern[str], str], ...]:
    """`key` set for one command (`-c`, `--config-env`) or written by `git config`; git matches
    section and variable names without case."""
    return (
        _rule(
            r"\bgit\b" + _SEG + r"""\s(?:-c\s+|--config-env(?:=|\s+))["']?""" + key, reason, re.I
        ),
        _rule(_GIT + r"config\b" + _CONFIG_READS + _SEG + r"""[\s"']""" + key, reason, re.I),
    )


BASH_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    _rule(re.escape(APPROVAL_MARK), "only the human writes [tests-change-approved]"),
    # git accepts any unambiguous prefix of a long option: --no-veri and --no-verif are
    # --no-verify for `git commit` (--no-ver is ambiguous with --no-verbose).
    _rule(r"\bgit\b" + _SEG + _flag(r"--no-veri(?:fy?)?"), _BYPASS),
    # -n means --no-verify only for `git commit` (combined short flags included, -an);
    # elsewhere it is --dry-run / --max-count / --no-stat, which are harmless.
    _rule(_GIT + r"commit\b" + _SEG + _flag(r"-(?![umCcFt])[a-zA-Z]*n[a-zA-Z]*"), _BYPASS),
    _rule(r"(?:^|[\s;&|(])SKIP=\S*" + _SEG + r"\bgit\b", _SKIP),
    _rule(r"\bexport\s+(?:-\w+\s+)*SKIP\b|\$env:SKIP\s*=", _SKIP, re.I),
    _rule(
        r"(?<![\w$])GIT_CONFIG_(?:COUNT|KEY_\d+|VALUE_\d+|GLOBAL|SYSTEM|PARAMETERS)\s*=",
        "git configuration set through the environment can disable the hooks or declare the "
        "human; ask the human",
    ),
    _rule(
        _GIT
        + r"push\b"
        + _SEG
        + r"""\s["']?(?:--force[\w-]*(?:=\S*)?|--mirror|-[a-zA-Z]*f[a-zA-Z]*|\+[^\s"']+)["']?"""
        r"(?=\s|$|[;&|)])",
        "no force pushes (--force, -f, +branch)",
    ),
    _rule(
        _GIT + r"reset\b" + _SEG + _flag(r"--h(?:a(?:rd?)?)?"),
        "git reset --hard discards work; ask the human",
    ),
    *_config_rules(r"core\.hookspath\b", _NO_HOOKS),
    *_config_rules(r"include(?:if)?\.", _NO_HOOKS + " (no extra git configuration files)"),
    *_config_rules(
        r"alias\.[\w-]",
        "no git aliases from the agent: an alias can carry --no-verify past the hooks",
    ),
    *_config_rules(
        r"rumbo\.human\b",
        "only the human declares rumbo.human (git config); the agent leaves it alone",
    ),
    _rule(r"\bpre-commit\s+uninstall\b", _NO_HOOKS),
    _rule(
        _MOVERS + _SEG + _GIT_DIR,
        "the git directory holds the hooks and the clone's configuration; the agent does not "
        "change it by hand",
        re.I,
    ),
    _rule(
        r"(?<!git )\b(?:rm|rmdir|rd|ri|del|Remove-Item)\b"
        r"(?="
        + _SEG
        + r"\s(?:-[a-zA-Z]*r[a-zA-Z]*|--recursive)(?=\s|$))"
        + _SEG
        + r"\s(?:--\s+)?"
        + _ROOTISH,
        "recursive rm on the tree root, home or a glob: ask the human",
        re.I,
    ),
    _rule(r"(?:>>?|>\|)\s*" + _TARGET, _PROTECTED, re.I),
    _rule(_WRITERS + _ARGS + r"\s" + _TARGET, _PROTECTED, re.I),
    # argparse accepts any prefix of --index; the index variables do the same from outside.
    _rule(
        r"\bverify-dep\b" + _SEG + r"""\s["']?--i[\w-]*"""
        r"|\b(?:UV_INDEX_URL|UV_DEFAULT_INDEX|PIP_INDEX_URL)\s*=" + _SEG + r"\bverify-dep\b",
        "rumbo verify-dep takes the index from the project's configuration; only the human "
        "passes --index (or sets the index variables) to send it to another host",
    ),
    # `git diff` is pre-approved. With --no-index, or with one path outside the working tree
    # (measured: git then compares files, no option needed), it prints an untracked `.env`.
    _rule(
        _GIT + r"diff\b" + _SEG + r"\s(?:" + _word("--no-index") + r"|" + _ENV_ARGUMENT + r")",
        "git diff --no-index, or git diff on a .env file, prints files git does not track "
        "(secrets included); ask the human",
        re.I,
    ),
)


# ---------------------------------------------------------------- paths (Edit/Write)
def _fold(rel: str) -> str:
    """`rel` without case and without the spellings Windows opens as the same file: a trailing
    dot or space, an alternate data stream (`Makefile::$DATA`). A NUL ends the name, as it does
    for the system call behind the tool."""
    parts: list[str] = []
    for part in rel.split("\0", 1)[0].split("/"):
        name = part.split(":", 1)[0].rstrip(". ")
        parts.append(name or part)
    return "/".join(parts).casefold()


def is_protected(rel: str) -> bool:
    low = _fold(rel)
    return (
        low in _FILES
        or low == ".git"
        or low.startswith(PROTECTED_PREFIXES)
        or low.rsplit("/", 1)[-1] in _SHADOWS
        or _ENV_FILE.fullmatch(low) is not None
    )


def _verifies_locally(rel: str) -> bool:
    """A protected file that changes what the local checks do the moment it exists."""
    low = _fold(rel)
    return low in _FILES or low.rsplit("/", 1)[-1] in _SHADOWS


def _protected_name(file_path: str) -> bool:
    """Whether a path the guard could not place looks like a protected one, by its names alone."""
    parts = [_fold(part) for part in re.split(r"[\\/]+", file_path) if part]
    if not parts:
        return False
    name = parts[-1]
    return (
        name in _BASENAMES
        or name == ".git"
        or _ENV_FILE.fullmatch(name) is not None
        or any(part in (".git", ".github", ".claude") for part in parts[:-1])
    )


def _windows_path(file_path: str, hosts: tuple[str, ...]) -> str:
    """The plain form of a Windows path that names a local file another way: `\\\\?\\C:\\x` and
    `\\\\.\\C:\\x` (device prefixes), `\\\\?\\UNC\\host\\share`, and the administrative share of
    this machine (`\\\\localhost\\C$\\x`). pathlib resolves none of them to `C:\\x`. A share on
    another host, a volume or a device is returned as it is: still starting with two backslashes.
    """
    path = file_path.replace("/", "\\")
    low = path.lower()
    if low.startswith("\\\\?\\unc\\"):
        path = "\\\\" + path[8:]
    elif low.startswith(("\\\\?\\", "\\\\.\\")) and re.match(r"[a-z]:", low[4:]):
        path = path[4:]
    share = re.match(r"\\\\([^\\]+)\\([a-zA-Z])\$(?=\\|$)", path)
    if share and share.group(1).lower() in hosts:
        path = f"{share.group(2)}:{path[share.end() :] or chr(92)}"
    return path


def _candidates(file_path: str, root: Path) -> tuple[list[Path], bool]:
    """(the resolved readings of `file_path`, whether its place is known with certainty).

    One reading, except on Windows, where `/c/x` is `C:\\x` for Git Bash and `\\c\\x` on the
    current drive for everything else: both are returned. Not certain: a path that cannot be
    resolved (a NUL, a loop) and a network or device path, which may still be this directory.
    """
    texts = [file_path]
    certain = True
    if os.name == "nt":
        local = _windows_path(
            file_path,
            ("localhost", "127.0.0.1", "::1", os.environ.get("COMPUTERNAME", "").lower()),
        )
        texts = [local]
        if local.startswith("\\\\"):
            certain = False
        elif re.match(r"\\[a-zA-Z](?:\\|$)", local):
            texts.append(f"{local[1]}:{local[2:] or chr(92)}")
    paths: list[Path] = []
    for text in texts:
        try:
            path = Path(text)
            paths.append((path if path.is_absolute() else root / path).resolve())
        except (OSError, RuntimeError, ValueError):
            certain = False
    return paths, certain


def _inside(paths: list[Path], root: Path) -> str | None:
    """The first of `paths` that is in the project, relative to its root; None when none is.
    A parent that is the same directory as the root under another name (a substituted drive, a
    share) counts."""
    try:
        base = root.resolve()
    except (OSError, RuntimeError):
        return None
    for path in paths:
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            pass
        for parent in path.parents:
            try:
                if parent.samefile(base):
                    return path.relative_to(parent).as_posix()
            except (OSError, ValueError):
                continue
    return None


def _relative(file_path: str, root: Path) -> str | None:
    """Path relative to the project root (normalised), or None when it is outside the project."""
    return _inside(_candidates(file_path, root)[0], root)


def _git_dirs(root: Path) -> list[Path]:
    """The git directory of the project, found without a subprocess: `.git` in the root or in a
    parent (a project in a sub-directory of its repository), plus the directories a worktree's
    `.git` file points to."""
    try:
        here = root.resolve()
        for base in (here, *here.parents):
            dot = base / ".git"
            if dot.is_dir():
                return [dot]
            if dot.is_file():
                text = dot.read_text(encoding="utf-8", errors="replace").strip()
                if not text.startswith("gitdir:"):
                    return [dot]
                gitdir = (base / text[7:].strip()).resolve()
                dirs = [dot, gitdir]
                common = gitdir / "commondir"
                if common.is_file():
                    shared = common.read_text(encoding="utf-8", errors="replace").strip()
                    dirs.append((gitdir / shared).resolve())
                return dirs
    except (OSError, RuntimeError, ValueError):
        pass
    return []


def is_signed(root: Path, rel: str) -> bool:
    """A numbered ADR or spec whose committed version no longer carries the mark (ADR-0017).

    One `git show`, and only for paths that match the pattern: ordinary edits stay free of
    subprocesses. A new document (absent from HEAD) is not signed.
    """
    if not SIGNED_DOC.match(rel):
        return False
    head = _blob(root, f"HEAD:./{rel}")
    return head is not None and TEMPLATE_MARK not in head


def _protected_reason(rel: str) -> str:
    return (
        f"{rel} is protected by AGENTS.md (hard rules): the agent does not edit the "
        "Makefile, pyproject.toml or the tool configuration that shadows it, CI, infra/, hooks, "
        "the git directory, the sensors or .env files. Explain the change you need and ask the "
        "human to make it (why: README.md, section 'Working with the agent')."
    )


def _signed_reason(files: str) -> str:
    return (
        f"{files}: signed by the human (committed without the template mark), so read-only "
        "for the agent. Propose the change to the human, or write a new document that "
        "supersedes it (rumbo new adr|spec)."
    )


def _created_reason(files: str) -> str:
    return (
        f"{files}: a numbered ADR or spec written by an agent carries the '{TEMPLATE_MARK} ...' "
        "mark on its first line; without it the document would count as signed by the human "
        "after its first commit (ADR-0025). Start it with `rumbo new adr|spec`, or put the mark "
        "back; only the human removes it."
    )


def _removes_mark(tool_name: str, tool_input: dict[str, Any], target: Path) -> bool:
    """Whether the call leaves a file that had the mark without it.

    Edit and MultiEdit are simulated on the current content (in order, `replace_all` honoured),
    so an `old_string` that is only a part of the mark is seen. An edit that names the whole
    mark and drops it is refused even when the file cannot be read.
    """
    try:
        current = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
    except OSError:
        current = None
    if tool_name == "Write":
        content = str(tool_input.get("content", ""))
        return current is not None and TEMPLATE_MARK in current and TEMPLATE_MARK not in content
    raw: Any = tool_input.get("edits") if tool_name == "MultiEdit" else [tool_input]
    edits: list[dict[str, Any]] = (
        [edit for edit in raw if isinstance(edit, dict)] if isinstance(raw, list) else []
    )
    pairs = [(str(e.get("old_string", "")), str(e.get("new_string", "")), e) for e in edits]
    if any(TEMPLATE_MARK in old and TEMPLATE_MARK not in new for old, new, _ in pairs):
        return True
    if current is None or TEMPLATE_MARK not in current:
        return False
    after = current
    for old, new, edit in pairs:
        if old:
            after = (
                after.replace(old, new) if edit.get("replace_all") else after.replace(old, new, 1)
            )
    return TEMPLATE_MARK not in after


def check_edit(tool_name: str, tool_input: dict[str, Any], root: Path) -> str | None:
    """Reason to block an Edit/Write/MultiEdit/NotebookEdit call, or None to allow it."""
    file_path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if not file_path:
        return None
    paths, certain = _candidates(file_path, root)
    rel = _inside(paths, root)
    if rel is None:
        # Outside the project. Two things there are still the project's: its git directory
        # (above the root, or a worktree's), and a path the guard could not place for certain.
        git_dirs = _git_dirs(root)
        in_git = any(path == d or d in path.parents for path in paths for d in git_dirs)
        if in_git or (not certain and _protected_name(file_path)):
            return _protected_reason(file_path.replace("\\", "/"))
        return None
    if is_protected(rel):
        return _protected_reason(rel)
    if is_signed(root, rel):
        return _signed_reason(rel)
    if _removes_mark(tool_name, tool_input, root / rel):
        return (
            f"{rel}: the '{TEMPLATE_MARK} ...' mark is removed only by the human, when the "
            "document is complete. Leave the first line as it is."
        )
    if (
        tool_name == "Write"
        and SIGNED_DOC.match(rel)
        and TEMPLATE_MARK not in str(tool_input.get("content", ""))
    ):
        return _created_reason(rel)
    return None


def check_bash(command: str) -> str | None:
    """Reason to block a shell command (any tool of `SHELL_TOOLS`), or None to allow it.

    The rules cost time in proportion to the square of the longest simple command, and a hook
    that runs out of time lets the call through. So the work is bounded and fails closed: one
    simple command over `MAX_SIMPLE_COMMAND` characters is refused, and so is a command the
    rules could not judge within `RULES_BUDGET` seconds.
    """
    blocked = "blocked by scripts/rumbo_guard.py: "
    if max(len(part) for part in _SEPARATOR.split(command)) > MAX_SIMPLE_COMMAND:
        return (
            f"{blocked}one command of more than {MAX_SIMPLE_COMMAND} characters without a line "
            "break cannot be judged in time; write the long text to a file and pass the file"
        )
    deadline = time.monotonic() + RULES_BUDGET
    for pattern, reason in BASH_RULES:
        if pattern.search(command):
            return blocked + reason
        if time.monotonic() > deadline:
            return (
                f"{blocked}the command could not be judged within {RULES_BUDGET:g} s; split it, "
                "or write the long text to a file and pass the file"
            )
    return None


# ---------------------------------------------------------------- git, read only
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
    except OSError:
        return None


def _git_bytes(root: Path, args: list[str], data: bytes) -> bytes | None:
    """git fed `data` on stdin, its stdout as bytes; None when it is missing or fails."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root, input=data, capture_output=True, check=False
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    out: Any = proc.stdout
    return out.encode("utf-8", "replace") if isinstance(out, str) else out


def _blob(root: Path, spec: str) -> str | None:
    """`git show <spec>` as text, or None when the object does not exist (no HEAD, new file)."""
    proc = _git(root, "show", spec)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout


_OBJECT_HEADER = re.compile(rb"[0-9a-f]{40,64} (blob|tree|commit|tag) (\d+)")


def _blobs(root: Path, specs: list[str]) -> dict[str, str | None]:
    """The text of every `<rev>:<path>` in one `git cat-file --batch` (G16: one `git show` per
    file cost a process each). None for an object that does not exist or is not a file."""
    found: dict[str, str | None] = dict.fromkeys(specs)
    asked = [spec for spec in found if "\n" not in spec]
    if not asked:
        return found
    out = _git_bytes(root, ["cat-file", "--batch"], "\n".join(asked).encode("utf-8") + b"\n")
    if out is None:
        return found
    position = 0
    for spec in asked:
        end = out.find(b"\n", position)
        if end < 0:
            break
        line = out[position:end]
        header = _OBJECT_HEADER.fullmatch(line)
        position = end + 1
        if header is None:
            if line.endswith((b" missing", b" ambiguous")):
                continue
            break  # not the answer of `cat-file --batch`: no information
        size = int(header.group(2))
        if header.group(1) == b"blob":
            found[spec] = out[position : position + size].decode("utf-8", "replace")
        position += size + 1
    return found


def _prefix(root: Path) -> str:
    """The project's directory inside its repository ('' at the top, else `services/api/`):
    `git status` prints paths from the top whatever the working directory."""
    if (root / ".git").exists():
        return ""
    proc = _git(root, "rev-parse", "--show-prefix")
    if proc is None or proc.returncode != 0:
        return ""
    prefix = proc.stdout.strip("\r\n")
    return prefix if prefix.endswith("/") and "\0" not in prefix else ""


def changed_files(root: Path) -> list[str]:
    """Tracked files that differ from HEAD plus untracked files (ignored ones excluded), relative
    to the project root. NUL-terminated output, so a name with spaces or non-ASCII letters is
    not quoted; no rename detection, so a moved file is its source deleted and its target added.
    """
    proc = _git(root, "status", "--porcelain", "-z", "--no-renames", "--untracked-files=all")
    if proc is None or proc.returncode != 0:
        return []
    fields = proc.stdout.split("\0")
    files: list[str] = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4 or entry[2] != " ":
            continue
        files.append(entry[3:])
        if ("R" in entry[:2] or "C" in entry[:2]) and index < len(fields):
            files.append(fields[index])  # the source of a rename follows as its own field
            index += 1
    prefix = _prefix(root)
    if prefix:
        files = [name[len(prefix) :] for name in files if name.startswith(prefix)]
    return files


def changed_code_files(root: Path) -> list[str]:
    return [f for f in changed_files(root) if f.endswith(CODE_SUFFIXES)]


_NAME_STATUS = re.compile(r"[ACDMRTUXB]\d*")


def _name_status(root: Path, *args: str) -> list[tuple[str, str]] | None:
    """(status letter, path) pairs of a `git diff --name-status -z --no-renames ...`; None when
    git gave no answer."""
    proc = _git(root, "diff", "--name-status", "-z", "--no-renames", *args)
    if proc is None or proc.returncode != 0:
        return None
    fields = proc.stdout.split("\0")
    entries: list[tuple[str, str]] = []
    index = 0
    while index + 1 < len(fields):
        status, path = fields[index], fields[index + 1]
        index += 2
        if not _NAME_STATUS.fullmatch(status) or not path:
            return None  # not the answer of `git diff -z`: no information
        entries.append((status[0], path))
        if status[0] in "RC" and index < len(fields):  # never with --no-renames
            entries.append(("A", fields[index]))
            index += 1
    return entries


def staged_changes(root: Path) -> list[tuple[str, str]]:
    """Every staged change, type changes included (a file turned into a symlink is `T`)."""
    return _name_status(root, "--cached") or []


def staged_files(root: Path) -> list[str]:
    return [path for _status, path in staged_changes(root)]


def has_head(root: Path) -> bool:
    """False on a branch with no commit yet (a new repository, or `git checkout --orphan`)."""
    proc = _git(root, "rev-parse", "--verify", "-q", "HEAD")
    return proc is not None and proc.returncode == 0


def has_commits(root: Path) -> bool:
    """False only in a repository with no commit on any ref: the next one is its root commit."""
    proc = _git(root, "rev-list", "-n", "1", "--all")
    return proc is not None and proc.returncode == 0 and bool(proc.stdout.strip())


# ---------------------------------------------------------------- pyproject.toml (ADR-0019)
def _toml_differences(head: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Dotted paths (at most two levels) that differ, the free keys of ADR-0019 left out.

    A table missing on one side counts as an empty one, so `uv add` creating `[tool.uv]` in a
    file without a `[tool]` table is not a change to `tool`.
    """
    paths: list[str] = []
    for key in sorted(head.keys() | current.keys()):
        if (key,) in PYPROJECT_FREE_KEYS:
            continue
        old, new = head.get(key, {}), current.get(key, {})
        if isinstance(old, dict) and isinstance(new, dict):
            before: dict[str, Any] = old
            after: dict[str, Any] = new
            paths += [
                f"{key}.{sub}"
                for sub in sorted(before.keys() | after.keys())
                if (key, sub) not in PYPROJECT_FREE_KEYS and before.get(sub) != after.get(sub)
            ]
        elif old != new:
            paths.append(key)
    return paths


def _pyproject_tables(before: str, after: str | None) -> list[str]:
    if after is None:
        return [PYPROJECT_DELETED]
    try:
        return _toml_differences(tomllib.loads(before), tomllib.loads(after))
    except (tomllib.TOMLDecodeError, RecursionError):
        return [PYPROJECT_UNPARSEABLE]


def pyproject_change(root: Path, staged: bool) -> list[str]:
    """What changed in pyproject.toml outside its dependency tables (ADR-0019); empty if nothing.

    Compares the version in HEAD with the staged blob (`staged`, the git hook) or with the
    working tree (after a shell command), both parsed, without `PYPROJECT_FREE_KEYS`. Fails
    closed: a version that does not parse, a file that HEAD does not have and a deleted file
    are protected changes, under a label instead of a dotted path. Only a repository with no
    commit on any ref (or no git at all) has nothing to compare with: its root commit is exempt.

    Costs one or two `git show` and two parses: callers ask only when the file changed.
    """
    head_text = _blob(root, f"HEAD:./{PYPROJECT}")
    if head_text is None:
        return [PYPROJECT_NEW] if has_commits(root) else []
    if staged:
        current_text = _blob(root, f":./{PYPROJECT}")
    else:
        try:
            current_text = (root / PYPROJECT).read_text(encoding="utf-8")
        except FileNotFoundError:
            current_text = None
        except (OSError, UnicodeDecodeError):
            return [PYPROJECT_UNPARSEABLE]
    return _pyproject_tables(head_text, current_text)


def _protected_changes(root: Path, files: list[str], staged: bool) -> tuple[list[str], list[str]]:
    """(protected files among `files`, what `pyproject_change` reports if pyproject.toml is one).

    pyproject.toml is never in the first list: it is judged by its tables, and only when it is
    in `files`, so the usual event pays for no extra subprocess.
    """
    protected = [f for f in files if is_protected(f) and f != PYPROJECT]
    tables = pyproject_change(root, staged) if PYPROJECT in files else []
    return protected, tables


def _pyproject_reason(tables: list[str], what: str, undo: str) -> str:
    free = ", ".join(".".join(key) for key in PYPROJECT_FREE_KEYS)
    return (
        f"{PYPROJECT} {what} outside its dependency tables: "
        + ", ".join(tables)
        + f". It is a protected file: an agent changes only {free} (what uv add, uv remove "
        f"and uv lock write). Restore everything else ({undo}) and ask the human to make "
        "that change."
    )


# ------------------------------------------------- after a shell command, and Stop
_INDEX_ENTRY = re.compile(r"(\S+) (\d{6}) ([0-9a-f]{40,64}) (\d)\t(.+)", re.S)


def _blob_id(data: bytes, hex_length: int) -> str:
    digest = hashlib.sha1(usedforsecurity=False) if hex_length == 40 else hashlib.sha256()
    digest.update(b"blob %d\0" % len(data) + data)
    return digest.hexdigest()


def hidden_changes(root: Path, changed: list[str]) -> list[str]:
    """Protected files and signed documents that changed where `git status` does not look (G10).

    - A tracked file flagged `--skip-worktree` or `--assume-unchanged`: its content is hashed
      (`git hash-object`, so line-ending filters apply) and compared with the index. A flagged
      file that is missing is reported unless the flag is skip-worktree, which is how a sparse
      checkout looks.
    - A new file hidden by an ignore rule (`.gitignore`, `.git/info/exclude`): the names that
      change the local verification by existing (the Makefile family, pyproject.toml, the
      sensors, the tool configuration of ADR-0023) are looked up in every directory that holds
      tracked files. `.env*` files and `.claude/settings.local.json` are local by design.

    One `git ls-files` always; one `git hash-object` only when a flagged file exists.
    """
    proc = _git(root, "ls-files", "-v", "-s", "-z")
    if proc is None or proc.returncode != 0:
        return []
    tracked: dict[str, tuple[str, str, str]] = {}
    for entry in proc.stdout.split("\0"):
        match = _INDEX_ENTRY.fullmatch(entry)
        if match and match.group(4) == "0":
            tracked[match.group(5)] = (match.group(1), match.group(2), match.group(3))
    if not tracked:
        return []
    seen = set(changed)
    hidden: list[str] = []
    to_hash: list[str] = []
    for rel, (tag, mode, object_id) in tracked.items():
        flagged = tag.islower() or tag == "S"
        if not flagged or rel in seen or not (is_protected(rel) or SIGNED_DOC.match(rel)):
            continue
        path = root / rel
        if mode == "160000" or "\n" in rel:
            continue
        if mode == "120000" and path.is_symlink():
            if _blob_id(os.readlink(path).encode("utf-8", "surrogateescape"), len(object_id)) != (
                object_id
            ):
                hidden.append(rel)
        elif not path.exists():
            if tag.upper() != "S":
                hidden.append(rel)
        else:
            to_hash.append(rel)
    if to_hash:
        out = _git_bytes(
            root, ["hash-object", "--stdin-paths"], "\n".join(to_hash).encode("utf-8") + b"\n"
        )
        hashes = out.decode("ascii", "replace").split() if out else []
        if len(hashes) == len(to_hash):
            hidden += [
                rel for rel, now in zip(to_hash, hashes, strict=True) if now != tracked[rel][2]
            ]
    known = {rel.casefold() for rel in tracked} | {rel.casefold() for rel in changed}
    directories = {""}
    for rel in tracked:
        parts = rel.split("/")[:-1]
        directories.update("/".join(parts[: depth + 1]) for depth in range(len(parts)))
    for directory in directories:
        try:
            names = os.listdir(root / directory) if directory else os.listdir(root)
        except OSError:
            continue
        for name in names:
            rel = f"{directory}/{name}" if directory else name
            if _verifies_locally(rel) and rel.casefold() not in known:
                hidden.append(rel)
    return sorted(set(hidden))


def changed_protected_files(root: Path) -> list[str]:
    """Protected files changed since HEAD (the deterministic check behind the Bash heuristics)."""
    changed = changed_files(root)
    changed += hidden_changes(root, changed)
    protected, tables = _protected_changes(root, changed, staged=False)
    return protected + ([PYPROJECT] if tables else [])


def changed_documents(root: Path, changed: list[str]) -> tuple[list[str], list[str]]:
    """(documents whose mark was in HEAD and is gone, signed ADRs/specs changed or deleted).

    ADR-0017: the deterministic check behind what a shell command can do to a document. The
    committed versions come from one `git cat-file --batch`.
    """
    documents = [rel for rel in changed if rel.endswith(".md")]
    heads = _blobs(root, [f"HEAD:./{rel}" for rel in documents])
    unmarked: list[str] = []
    signed: list[str] = []
    for rel in documents:
        head = heads.get(f"HEAD:./{rel}")
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
    """What the working tree holds that an agent may not change, or None (also run by Stop)."""
    changed = changed_files(root)
    hidden = hidden_changes(root, changed)
    everything = changed + hidden
    touched, tables = _protected_changes(root, everything, staged=False)
    unmarked, signed = changed_documents(root, everything)
    reasons: list[str] = []
    if touched:
        reasons.append(
            "protected file(s) changed since the last commit: "
            + ", ".join(f + HIDDEN if f in hidden else f for f in touched)
            + ". Revert them (git checkout -- <file>, or delete the new file) and ask the human "
            "to make that change; the agent does not modify the harness."
        )
    if tables:
        reasons.append(
            _pyproject_reason(
                tables,
                "changed since the last commit",
                f"git checkout -- {PYPROJECT}, then repeat the uv command if there was one",
            )
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
    if hidden and reasons:
        reasons.append(
            "Some of them are hidden from git status by an index flag (skip-worktree, "
            "assume-unchanged) or by an ignore rule: undo that too."
        )
    return " ".join(reasons) or None


# ---------------------------------------------------------------- the guard's memory (ADR-0024)
_SHA = re.compile(r"[0-9a-f]{40,64}")


def _where(root: Path) -> tuple[Path | None, str | None]:
    """(the state file inside the git directory, HEAD); None for what git does not answer.

    One `git rev-parse`. On a branch with no commit the file has a place and HEAD is None.
    """
    proc = _git(root, "rev-parse", "--git-path", STATE_FILE, "HEAD")
    if proc is None:
        return None, None
    lines = proc.stdout.split("\n")
    if len(lines) < 2 or not lines[0].endswith(STATE_FILE) or "\0" in lines[0]:
        return None, None
    return root / lines[0], lines[1] if _SHA.fullmatch(lines[1]) else None


def _load_state(state_file: Path | None) -> dict[str, Any]:
    if state_file is None:
        return {}
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _save_state(state_file: Path | None, state: dict[str, Any]) -> None:
    if state_file is None:
        return
    scratch = state_file.with_name(f"{state_file.name}.{os.getpid()}.tmp")
    try:
        scratch.write_text(json.dumps(state), encoding="utf-8")
        os.replace(scratch, state_file)
    except OSError:
        pass  # a read-only git directory: the guard works without memory


def _recorded(state: dict[str, Any], key: str) -> str | None:
    value = state.get(key)
    return value if isinstance(value, str) and _SHA.fullmatch(value) else None


def _local_settings(root: Path) -> str:
    """A digest of `.claude/settings.local.json` without `permissions`: the human's "always
    allow" adds entries there while a command waits, and that is not the agent's change. A
    missing file is an empty one."""
    try:
        raw = (root / LOCAL_SETTINGS).read_bytes()
    except OSError:
        raw = b"{}"
    try:
        settings = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        settings = None
    if isinstance(settings, dict):
        settings.pop("permissions", None)
        raw = json.dumps(settings, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def record_pre_shell(root: Path) -> None:
    """Before a shell command: remember HEAD and the local settings (ADR-0024)."""
    state_file, head = _where(root)
    if state_file is None:
        return
    state = _load_state(state_file)
    state.pop("head", None)
    if head:
        state["head"] = head
    state["local"] = _local_settings(root)
    _save_state(state_file, state)


def _judge_documents(
    root: Path, entries: list[tuple[str, str]], old: str, new: str
) -> tuple[list[str], list[str], list[str]]:
    """(marks removed, signed documents changed or deleted, numbered documents created without
    the mark) between the trees `old` and `new`; `new` is '' for the index. One git call."""
    paths = [path for _status, path in entries]
    blobs = _blobs(root, [f"{rev}:./{path}" for path in paths for rev in (old, new)])
    unmarked: list[str] = []
    signed: list[str] = []
    created: list[str] = []
    for path in paths:
        before, after = blobs.get(f"{old}:./{path}"), blobs.get(f"{new}:./{path}")
        numbered = SIGNED_DOC.match(path) is not None
        if before is None:
            if numbered and after is not None and TEMPLATE_MARK not in after:
                created.append(path)
        elif TEMPLATE_MARK not in before:
            if numbered:
                signed.append(path)
        elif after is not None and TEMPLATE_MARK not in after:
            unmarked.append(path)
    return unmarked, signed, created


def check_commits(root: Path, old: str, new: str) -> str | None:
    """What changed between two commits that an agent may not commit, or None (ADR-0024).

    The rules of a staged change, applied to `git diff old new`: nothing here went through the
    pre-commit hook for certain. A git failure (an unknown commit) is no information.
    """
    entries = _name_status(root, "--relative", old, new)
    if not entries:
        return None
    paths = [path for _status, path in entries]
    parts: list[str] = []
    if protected := [f for f in paths if is_protected(f) and f != PYPROJECT]:
        parts.append("protected file(s) changed: " + ", ".join(protected) + ".")
    if PYPROJECT in paths:
        texts = _blobs(root, [f"{old}:./{PYPROJECT}", f"{new}:./{PYPROJECT}"])
        before, after = texts[f"{old}:./{PYPROJECT}"], texts[f"{new}:./{PYPROJECT}"]
        if tables := [PYPROJECT_NEW] if before is None else _pyproject_tables(before, after):
            parts.append(_pyproject_reason(tables, "changed", "in a new commit"))
    documents = [(status, path) for status, path in entries if path.endswith((".md", ".tmpl"))]
    unmarked, signed, created = _judge_documents(root, documents, old, new)
    if signed:
        parts.append(_signed_reason(", ".join(signed)))
    if unmarked:
        parts.append(
            "the template mark was removed from: "
            + ", ".join(unmarked)
            + ". Only the human removes it."
        )
    if created:
        parts.append(_created_reason(", ".join(created)))
    if not parts:
        return None
    return (
        f"HEAD moved from {old[:9]} to {new[:9]} during this command, and the difference "
        "between the two commits holds changes an agent does not commit: "
        + " ".join(parts)
        + f" If the commits are yours, undo them (git reset --soft {old[:9]} keeps the work in "
        "the index), take those changes out and ask the human to make them. If they are the "
        "human's (a pull, a merge, another branch checked out), there is nothing to undo: say "
        "so and go on."
    )


def check_post_shell(root: Path) -> str | None:
    """After a shell command: what moved HEAD, what changed in the local settings, and the
    working tree (`check_post_bash`). Each finding is reported once; the state is then renewed.
    """
    state_file, head = _where(root)
    state = _load_state(state_file)
    reasons: list[str] = []
    before = _recorded(state, "head")
    if before and head and before != head and (moved := check_commits(root, before, head)):
        reasons.append(moved)
    tree = check_post_bash(root)
    if tree:
        reasons.append(tree)
    local = _local_settings(root)
    if state.get("local") not in (None, local) and LOCAL_SETTINGS not in (tree or ""):
        reasons.append(
            f"{LOCAL_SETTINGS} changed while this command ran (outside its permissions list): "
            "local settings can switch the hooks off. Restore the file and ask the human to "
            "make that change."
        )
    if state_file is not None:
        state.pop("head", None)
        if head:
            state["head"] = head
        state["local"] = local
        _save_state(state_file, state)
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
            encoding="utf-8",
            errors="replace",
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
            ["make", "check-brief"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
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

    Protected files intact first, then the verification runner if code files changed since the
    last commit or were committed since the last HEAD that a Stop saw green (ADR-0024). That
    HEAD is recorded when the runner is green, and when there is nothing to verify; with no
    recorded value, or one git cannot compare, only the uncommitted code counts, as before.
    """
    if payload.get("stop_hook_active"):
        return 0, ""  # a previous Stop hook already asked for a fix: do not loop
    if touched := check_post_bash(root):
        return BLOCK, touched
    if os.environ.get("RUMBO_GUARD_SKIP_CHECK"):
        return 0, ""
    code_files = set(changed_code_files(root))
    state_file, head = _where(root)
    state = _load_state(state_file)
    green = _recorded(state, "green")
    if head and green and green != head:
        committed = _name_status(root, "--relative", green, head) or []
        code_files.update(path for _status, path in committed if path.endswith(CODE_SUFFIXES))
    code, message = run_verification(root, len(code_files)) if code_files else (0, "")
    if head and code == 0 and not message and green != head:
        state["green"] = head
        _save_state(state_file, state)
    return code, message


# ---------------------------------------------------------------- git hooks (ADR-0008)
def agent_env_names(root: Path) -> tuple[str, ...]:
    """`[tool.rumbo].agent_env` as committed, else the default markers.

    ADR-0026: harness settings are read from the base, here `HEAD:pyproject.toml`, so an edit
    of the working tree (staged or not) cannot empty the list and make the agent an unknown
    actor. The working tree is read only in a repository with no commit on any ref, where
    there is no base yet.
    """
    text = _blob(root, f"HEAD:./{PYPROJECT}")
    if text is None:
        if has_commits(root):
            return DEFAULT_AGENT_ENV
        try:
            text = (root / PYPROJECT).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return DEFAULT_AGENT_ENV
    try:
        data: Any = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError):
        return DEFAULT_AGENT_ENV
    for key in ("tool", "rumbo", "agent_env"):
        data = data.get(key) if isinstance(data, dict) else None
    if isinstance(data, list) and all(isinstance(name, str) for name in data):
        return tuple(str(name) for name in data)
    return DEFAULT_AGENT_ENV


def actor(root: Path) -> str:
    """'agent' (a marker is set), 'human' (`git config rumbo.human` is true) or 'unknown'."""
    if any(os.environ.get(name) for name in agent_env_names(root)):
        return "agent"
    proc = _git(root, "config", "--get", "rumbo.human")
    if proc is not None and proc.returncode == 0 and proc.stdout.strip() == "true":
        return "human"
    return "unknown"


def check_pre_commit(root: Path) -> str | None:
    """Reason to refuse the commit (git pre-commit hook), or None to let it through."""
    who = actor(root)
    if who == "human":
        return None
    if not has_commits(root):
        # The root commit IS the harness `rumbo init` just wrote (on the human's request, often
        # from inside the agent's session): there is no previous state to protect yet. A branch
        # without commits in a repository that has them (`git checkout --orphan`) is not one.
        return None
    staged = staged_changes(root)
    if not staged:
        return None
    files = [path for _status, path in staged]
    reason: str | None = None
    protected, tables = _protected_changes(root, files, staged=True)
    refusals: list[str] = []
    if protected:
        refusals.append(
            "protected file(s) staged: "
            + ", ".join(protected)
            + ". They are protected by AGENTS.md (hard rules): the agent does not edit the "
            "Makefile, pyproject.toml, CI, infra/, hooks, the sensors or .env files. Unstage them "
            "(git restore --staged <file>) and ask the human to make that change."
        )
    if tables:
        refusals.append(
            _pyproject_reason(tables, "is staged with changes", f"git restore --staged {PYPROJECT}")
        )
    if refusals:
        reason = " ".join(refusals)
    else:
        unmarked, signed, created = _judge_documents(root, staged, "HEAD", "")
        if signed:
            reason = _signed_reason(", ".join(signed))
        elif unmarked:
            reason = (
                ", ".join(unmarked)
                + f": the '{TEMPLATE_MARK} ...' mark is removed only by the human, when the "
                "document is complete. Restore the first line before committing."
            )
        elif created:
            reason = _created_reason(", ".join(created))
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


# ---------------------------------------------------------------- entry point
def _read_payload() -> dict[str, Any]:
    """The hook event: JSON on stdin, read as UTF-8 bytes (on Windows the text layer of stdin
    decodes with the ANSI code page and fails on valid UTF-8). Anything that is not a JSON
    object is an empty event: there is nothing in it to judge."""
    stream = sys.stdin
    if stream is None or stream.isatty():
        return {}
    try:
        buffer = getattr(stream, "buffer", None)
        raw: Any = buffer.read() if buffer is not None else stream.read()
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        payload = json.loads(text.removeprefix("\ufeff"))
    except (OSError, ValueError, RecursionError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _say(message: str, stream: Any) -> None:
    """Prints as UTF-8 whatever the locale: a file name outside the code page must not turn a
    refusal into a crash."""
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        print(message, file=stream)
        return
    stream.flush()
    buffer.write((message + "\n").encode("utf-8", "replace"))
    buffer.flush()


def _run(event: str, argv: list[str]) -> int:
    if event in ("pre-commit", "commit-msg"):  # git hooks: cwd is the repository root
        root = Path.cwd()
        if event == "pre-commit":
            refusal = check_pre_commit(root)
        else:
            refusal = check_commit_msg(root, argv[1] if len(argv) > 1 else None)
        if refusal:
            _say(f"blocked by scripts/rumbo_guard.py: {refusal}", sys.stderr)
            return GIT_BLOCK
        return 0
    payload = _read_payload()
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or str(payload.get("cwd") or Path.cwd()))
    if event == "stop":
        code, message = check_stop(payload, root)
        if message:  # exit 2: the reason on stderr; exit 0: the hook JSON, alone on stdout
            _say(message, sys.stderr if code else sys.stdout)
        return code
    tool_name = str(payload.get("tool_name") or "")
    raw_input = payload.get("tool_input")
    tool_input: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
    reason: str | None = None
    if event == "post-tool":
        # An event that does not say which tool ran is checked too: the matcher chose it.
        reason = check_post_shell(root) if tool_name in SHELL_TOOLS or not tool_name else None
    elif tool_name in EDIT_TOOLS:
        reason = check_edit(tool_name, tool_input, root)
    elif tool_name in SHELL_TOOLS:
        reason = check_bash(str(tool_input.get("command", "")))
        if reason is None:
            record_pre_shell(root)
    if reason:
        _say(reason, sys.stderr)
        return BLOCK
    return 0


def _internal_error(event: str, error: Exception) -> int:
    """The guard's own failure, said in the hook's terms instead of a traceback (see the module
    docstring, "When the guard itself fails")."""
    what = f"internal error ({type(error).__name__}: {error})"
    bug = "This is a bug in the guard, not in your change: report it to the human"
    if event in ("pre-commit", "commit-msg"):
        _say(
            f"blocked by scripts/rumbo_guard.py: {what}. The commit could not be judged, so it "
            f"is refused. {bug}.",
            sys.stderr,
        )
        return GIT_BLOCK
    if event == "stop":
        code, message = _environment(
            f"scripts/rumbo_guard.py could not run its Stop checks: {what}.",
            f"The guard failed at Stop with an {what}; the agent was told to report it and stop.",
        )
        _say(message, sys.stdout)
        return code
    if event == "post-tool":
        _say(
            f"scripts/rumbo_guard.py: {what}. What this command changed could not be checked. "
            f"{bug} before going on.",
            sys.stderr,
        )
        return BLOCK
    _say(
        f"blocked by scripts/rumbo_guard.py: {what}. The call could not be judged, so it is "
        f"refused. {bug}, who can override the hook in .claude/settings.local.json.",
        sys.stderr,
    )
    return BLOCK


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    event = argv[0].lower() if argv else "pre-tool"
    try:
        return _run(event, argv)
    except Exception as error:  # an exit code other than 0 or 2 would read as "allow"
        return _internal_error(event, error)


if __name__ == "__main__":
    sys.exit(main())
