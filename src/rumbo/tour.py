"""``rumbo tour``: a guided, pedagogical review of a git range, one commit at a time.

The review is built from the diff, never from what the commit message claims. For each
commit it gives the reviewer:

- the intent (subject + body) and a kind: behavioral · structural · tests · docs · mechanical;
- risk hotspots first (CI, secrets, dependencies, infra, weakened tests);
- a reading order: tests first, then the files named by the subject, then the largest
  diff — never alphabetical (Fregnan et al. 2022; Rahman et al. ICSE 2026);
- a review budget against ~300 changed lines per sitting (lockfiles excluded);
- the commands that verify the change;
- three "explanation test" questions the reviewer should be able to answer before
  approving, with hints folded away (comprehension gate; Anthropic 2026 RCT: explaining
  back and conceptual questions preserve understanding, delegation erodes it).

Standard library + git only. Every git call whose output is parsed fixes its own format
(`GIT`, `LOG`, `SHOW`, `DIFF`, `PATCH` below), so the guide does not depend on the reviewer's
git configuration; every path and ref printed inside a command is quoted for the shell.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from rumbo.project import DEFAULT_TESTS, GitMissing
from rumbo.project import tests_dir as project_tests_dir

BUDGET_LINES = 300
LOCKFILES = ("uv.lock", "poetry.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock")
KIND_BY_PREFIX = {
    "feat": "behavioral",
    "fix": "behavioral",
    "perf": "behavioral",
    "refactor": "structural",
    "style": "structural",
    "test": "tests",
    "docs": "docs",
    "chore": "mechanical",
    "ci": "mechanical",
    "build": "mechanical",
}
RISK_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("CI / workflows (can weaken every other sensor)", re.compile(r"^\.github/")),
    ("pre-commit hooks", re.compile(r"^\.pre-commit-config\.yaml$")),
    ("dependencies", re.compile(r"^(pyproject\.toml|requirements.*\.txt|package\.json)$")),
    (
        "infra / deployment",
        re.compile(r"^(infra/|Dockerfile|docker-compose|fly\.toml|render\.yaml)"),
    ),
    ("secrets / environment", re.compile(r"^(\.env|.*secrets?.*|.*credential.*)$", re.I)),
    ("auth / security code", re.compile(r"(auth|security|permission|token|crypto)", re.I)),
    ("the anti-cheat sensor itself", re.compile(r"check_tests_not_weakened")),
    (
        "agent guides (AGENTS.md / CLAUDE.md / commands)",
        re.compile(r"^(AGENTS\.md|CLAUDE\.md|\.claude/)"),
    ),
)
WEAKENING = re.compile(
    r"^-\s*assert\b|^\+.*\b(pytest\.mark\.(skip|skipif|xfail)|pytest\.skip\()", re.M
)


@dataclass
class FileChange:
    path: str
    added: int
    removed: int
    status: str = "M"  # A, M, D (a moved file is a D and an A: renames are not followed)
    # The project's tests directory ([tool.rumbo].tests) as git names it, from the repository top.
    tests_dir: str = DEFAULT_TESTS

    @property
    def lines(self) -> int:
        return self.added + self.removed

    @property
    def is_test(self) -> bool:
        """In the project's tests directory, under any `tests/`, or named as pytest collects."""
        name = PurePosixPath(self.path).name
        return (
            self.path.startswith((f"{self.tests_dir}/", "tests/"))
            or "/tests/" in self.path
            or name.startswith("test_")
            or name.endswith("_test.py")
        )

    @property
    def is_doc(self) -> bool:
        return self.path.endswith((".md", ".rst", ".txt")) or self.path.startswith("docs/")

    @property
    def is_lock(self) -> bool:
        return PurePosixPath(self.path).name in LOCKFILES


@dataclass
class Commit:
    sha: str
    subject: str
    body: str
    author: str
    files: list[FileChange] = field(default_factory=list)
    weakening_hunks: list[str] = field(default_factory=list)
    merge: bool = False  # more than one parent: reverted, like diffed, against the first

    @property
    def short(self) -> str:
        return self.sha[:10]

    @property
    def lines(self) -> int:
        return sum(f.lines for f in self.files if not f.is_lock)

    @property
    def kind(self) -> str:
        match = re.match(r"^(\w+)(\(.+?\))?!?:", self.subject)
        if match and match.group(1) in KIND_BY_PREFIX:
            return KIND_BY_PREFIX[match.group(1)]
        if self.files and all(f.is_test for f in self.files):
            return "tests"
        if self.files and all(f.is_doc for f in self.files):
            return "docs"
        return "behavioral"

    def risks(self) -> list[str]:
        found: list[str] = []
        for label, pattern in RISK_RULES:
            hits = [f.path for f in self.files if pattern.search(f.path)]
            if hits:
                found.append(f"{label}: {', '.join(hits[:4])}")
        deleted_tests = [f.path for f in self.files if f.is_test and f.status == "D"]
        if deleted_tests:
            found.append(f"deleted test files: {', '.join(deleted_tests)}")
        if self.weakening_hunks:
            found.append(
                "removed asserts or added skip/xfail in: " + ", ".join(self.weakening_hunks[:4])
            )
        return found

    def reading_order(self) -> list[FileChange]:
        tokens = {t for t in re.findall(r"[a-z0-9_]{4,}", self.subject.lower())}

        def key(f: FileChange) -> tuple[int, int, int]:
            named = any(t in f.path.lower() for t in tokens)
            return (0 if f.is_test else 1, 0 if named else 1, -f.lines)

        return sorted((f for f in self.files if not f.is_lock), key=key)


# What makes the output of git the same on every machine, whatever the user configured: paths
# as they are (not octal-quoted), no colour, messages in UTF-8.
GIT = ("git", "-c", "core.quotepath=off", "-c", "color.ui=never")
LOG = ("-c", "i18n.logOutputEncoding=UTF-8", "log", "--reverse", "--no-show-signature")
# One diff per commit, against its first parent (a merge is what it brought into the branch);
# a moved file is a deleted and an added one, so its removed asserts are seen.
SHOW = ("show", "--format=", "--no-show-signature", "--diff-merges=first-parent")
DIFF = ("--no-color", "--no-ext-diff", "--no-textconv", "--no-renames")
PATCH = ("--src-prefix=a/", "--dst-prefix=b/", "--unified=0")
_QUOTED = re.compile(r"\\([0-7]{3}|.)")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


def _git(args: list[str], root: Path) -> str:
    try:
        proc = subprocess.run(
            [*GIT, *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise GitMissing(exc) from exc
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


BASE_CANDIDATES = ("origin/main", "origin/master", "origin/HEAD", "main", "master")


def resolve_base(base: str | None, root: Path) -> str | None:
    """The given base if git knows it; otherwise the first known default; None if nothing fits.

    Raises GitMissing when git cannot be started.
    """
    candidates = [base] if base else []
    candidates += [c for c in BASE_CANDIDATES if c != base]
    for candidate in candidates:
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "--verify", "-q", f"{candidate}^{{commit}}"],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            raise GitMissing(exc) from exc
        if proc.returncode == 0:
            return candidate
    return None


def _unquote(name: str) -> str:
    """A path git wrote in C quotes: it has a quote, a backslash or a control character."""
    raw = bytearray()
    position = 0
    body = name[1:-1] if name.endswith('"') else name[1:]
    for escape in _QUOTED.finditer(body):
        raw += body[position : escape.start()].encode("utf-8", "replace")
        code = escape.group(1)
        raw += bytes([int(code, 8)]) if len(code) == 3 else _ESCAPES.get(code, code).encode()
        position = escape.end()
    raw += body[position:].encode("utf-8", "replace")
    return raw.decode("utf-8", "replace")


def _patch_path(text: str, prefix: str) -> str:
    """The path of a `--- a/…` or `+++ b/…` line of a patch; "" for `/dev/null`."""
    if text == "/dev/null":
        return ""
    text = text.removesuffix("\t")  # git ends a name that has a space with a tab
    if text.startswith('"'):
        text = _unquote(text)
    return text.removeprefix(prefix)


def _weakened(patch: str, tests: set[str]) -> list[str]:
    """The test files of a patch that lose an assert or gain a skip, in the order of the patch.

    Only the header of a file names it: inside a hunk a removed line may itself begin with
    `-- a/`. The removed lines of a deleted file (`+++ /dev/null`) belong to the file deleted.
    """
    found: list[str] = []
    old = new = ""
    header = True
    for line in patch.split("\n"):
        if line.startswith("diff --git "):
            header, old, new = True, "", ""
        elif line.startswith("@@"):
            header = False
        elif header and line.startswith("--- "):
            old = _patch_path(line[4:], "a/")
        elif header and line.startswith("+++ "):
            new = _patch_path(line[4:], "b/")
        elif not header and WEAKENING.search(line):
            path = new or old
            if path in tests and path not in found:
                found.append(path)
    return found


def load_commits(base: str, head: str, root: Path) -> list[Commit]:
    # Five fields per commit, each ended by NUL: no message can contain one, so no message
    # can hide its commit or shift the fields.
    log = _git([*LOG, "--format=%H%x00%s%x00%b%x00%an%x00%P%x00", f"{base}..{head}"], root)
    parts = log.split("\0")
    # The tests directory is the project's, seen from the top of the repository: the paths of
    # `git show` start there, and the project may be one directory of it.
    prefix = _git(["rev-parse", "--show-prefix"], root).strip()
    tests_dir = prefix + project_tests_dir(root)
    commits: list[Commit] = []
    show = [*SHOW, *DIFF]
    for i in range(0, len(parts) - 4, 5):
        sha, subject, body, author, parents = (part.strip() for part in parts[i : i + 5])
        commit = Commit(sha, subject, body, author, merge=len(parents.split()) > 1)
        named = _git([*show, "--name-status", "-z", sha], root).split("\0")
        pairs = zip(named[0::2], named[1::2], strict=False)  # status, path, …, and a last ""
        statuses = {path: status.strip()[:1] for status, path in pairs}
        for entry in _git([*show, "--numstat", "-z", sha], root).split("\0"):
            cols = entry.lstrip("\n").split("\t", 2)
            if len(cols) != 3:
                continue
            added = int(cols[0]) if cols[0].isdigit() else 0
            removed = int(cols[1]) if cols[1].isdigit() else 0
            status = statuses.get(cols[2], "M")
            commit.files.append(FileChange(cols[2], added, removed, status, tests_dir))
        test_paths = {f.path for f in commit.files if f.is_test}
        if test_paths:
            patch = _git([*show, *PATCH, sha], root)
            commit.weakening_hunks = _weakened(patch, test_paths)
        commits.append(commit)
    return commits


def _verify_commands(commit: Commit) -> list[str]:
    # A path comes from the branch under review: it goes into a command as one quoted word.
    tests = [
        shlex.quote(f.path)
        for f in commit.files
        if f.is_test and f.status != "D" and f.path.endswith(".py")
    ]
    sha = shlex.quote(commit.short)
    cmds = [f"git show --stat {sha}"]
    if tests:
        cmds.append(f"uv run pytest -q {' '.join(tests[:3])}")
    if commit.kind in ("behavioral", "structural", "mechanical"):
        cmds.append("uv run python scripts/rumbo_check.py")
    if commit.kind == "behavioral":
        # git refuses to revert a merge without naming the parent it keeps (`-m 1`).
        mainline = "-m 1 " if commit.merge else ""
        cmds.append(
            f"git revert --no-commit {mainline}{sha} && uv run python scripts/rumbo_check.py "
            "test; git revert --abort"
        )
    return cmds


def _questions(commit: Commit) -> list[tuple[str, str]]:
    order = commit.reading_order()
    tests = [f.path for f in order if f.is_test]
    sources = [f.path for f in order if not f.is_test and not f.is_doc]
    biggest = sources[0] if sources else (order[0].path if order else "the change")
    body_hint = (
        commit.body.splitlines()[0] if commit.body else "(the commit body is empty: ask the author)"
    )
    questions = [
        (
            f"Which test or sensor proves that «{commit.subject}» works? Point to the exact test.",
            "Tests touched here: "
            + (", ".join(tests) if tests else "none — that is a finding in itself")
            + ".",
        ),
        (
            f"If the change to `{biggest}` were reverted, what observable behavior would change, "
            "and how would you notice?",
            "Run the revert command from the verification list. "
            f"Author's stated intent: {body_hint}",
        ),
        (
            "Name one alternative to this approach and why it was not chosen. "
            "Is that reason written down (commit body, ADR, decisions log)?",
            "If you cannot find the reason, the change carries intent debt: ask for an ADR "
            "or a line in docs/decisions.md before approving.",
        ),
    ]
    if commit.kind == "structural":
        questions[1] = (
            "This commit claims to change structure only. Which lines, if any, change behavior?",
            "Structural and behavioral changes should be separate commits (Tidy First). "
            "Run the test suite on this commit alone: it must stay green with no test edits.",
        )
    return questions


def render_markdown(commits: list[Commit], base: str, head: str, since: str | None = None) -> str:
    total = sum(c.lines for c in commits)
    sittings = max(1, -(-total // BUDGET_LINES))
    out = [
        f"# Guided review: `{base}..{head}`",
        "",
        f"{len(commits)} commit(s), {total} changed lines (lockfiles excluded) → "
        f"about **{sittings} sitting(s)** of ≤{BUDGET_LINES} lines. Review in order; stop when "
        "the budget runs out and come back fresh.",
        "",
        "How to use this: read the risk hotspots first, then the files in the given order, run "
        "the verification commands, and only then answer the three questions **without "
        "looking at the hints**. If you cannot answer, do not approve yet: that is the "
        "explanation test of RUMBO's human compass.",
        "",
    ]
    if since:
        out += [
            f"Re-review? Compare with the previous version of the series: "
            f"`git range-diff {shlex.quote(base)} {shlex.quote(since)} {shlex.quote(head)}`.",
            "",
        ]
    for i, c in enumerate(commits, 1):
        out += [
            f"## {i}. {c.subject}",
            "",
            f"`{c.short}` · {c.author} · kind: **{c.kind}** · {c.lines} lines, "
            f"{len(c.files)} files",
            "",
        ]
        if c.body:
            out += ["> " + "\n> ".join(c.body.splitlines()[:8]), ""]
        risks = c.risks()
        out.append("**Risk hotspots** (read these first):")
        out += [f"- ⚠️ {r}" for r in risks] or [
            "- none detected by path rules (still read the tests)"
        ]
        out.append("")
        out.append(
            "**Reading order** (tests first, then what the subject names, then the largest):"
        )
        for n, f in enumerate(c.reading_order(), 1):
            tag = "test" if f.is_test else ("doc" if f.is_doc else "code")
            status = {"A": "new", "D": "deleted", "R": "renamed"}.get(f.status, "")
            out.append(
                f"{n}. `{f.path}` ({tag}{', ' + status if status else ''}, +{f.added}/−{f.removed})"
            )
        if c.lines > BUDGET_LINES:
            out.append(
                f"\n> Over budget ({c.lines} > {BUDGET_LINES}): ask for a split, or review "
                "only the tests and sample the rest."
            )
        out += ["", "**Verify:**", "```bash", *_verify_commands(c), "```", ""]
        out.append("**Explanation test** (answer before approving):")
        for q, hint in _questions(c):
            out += [
                f"- {q}",
                "  <details><summary>hint</summary>",
                "",
                f"  {hint}",
                "",
                "  </details>",
            ]
        out.append("")
    out += [
        "## Before you approve",
        "",
        "- [ ] I read the risk hotspots and the tests of every commit.",
        "- [ ] I ran the verification commands (or CI did, and I read its output).",
        "- [ ] I answered the explanation questions without the hints.",
        "- [ ] Structural and behavioral changes are in separate commits.",
        "- [ ] I can explain this change in three sentences to someone who has not seen it.",
        "",
        'Record the decision: `rumbo decision "Approved <range>" --why "..."`',
        "",
    ]
    return "\n".join(out)
