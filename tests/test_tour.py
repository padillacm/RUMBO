"""Tests of F-0006, the guided review of a change (`rumbo tour`).

Spec: specs/0006-guided-review-of-a-change.md. Each test names the requirement it covers in a
comment; `rumbo trace` builds the table from those mentions. The repositories are throw-away
ones under `tmp_path`.
"""

import ast
import datetime as dt
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from rumbo import tour
from rumbo.cli import main
from rumbo.tour import Commit, FileChange, load_commits, render_markdown

SHA = "0123456789abcdef0123456789abcdef01234567"
SHORT = SHA[:10]
IDENTITY = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@x",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@x",
}


class Repo:
    """A throw-away git repository; the fixture leaves it on `feature`, level with `main`."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, **IDENTITY},
        )
        return proc.stdout.strip()

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    made = Repo(tmp_path)
    made.git("init", "-q", "-b", "main")
    made.write("README.md", "# demo\n")
    made.commit("chore: base")
    made.git("checkout", "-qb", "feature")
    return made


def _commit(subject: str, files: list[FileChange], body: str = "") -> Commit:
    return Commit(SHA, subject, body, "t", files)


def _verify(commit: Commit) -> list[str]:
    text = render_markdown([commit], "main", "HEAD")
    return text.split("```bash\n", 1)[1].split("\n```", 1)[0].splitlines()


# Covers F-0006.R1 (specs/0006-guided-review-of-a-change.md).
def test_commits_are_listed_oldest_first_and_head_limits_the_range(
    repo: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    repo.write("a.py", "a = 1\n")
    repo.commit("feat: one")
    repo.write("b.py", "b = 1\n")
    repo.commit("fix: two")
    repo.write("c.py", "c = 1\n")
    repo.commit("refactor: three")
    assert main(["tour", "main", "--dir", str(repo.root)]) == 0
    captured = capsys.readouterr()
    out = captured.out
    assert captured.err == ""  # the base was found: nothing to announce
    assert out.startswith("# Guided review: `main..HEAD`")
    assert "3 commit(s)" in out and "chore: base" not in out
    assert (
        out.index("## 1. feat: one") < out.index("## 2. fix: two") < out.index("## 3. refactor: th")
    )
    assert main(["tour", "main", "--head", "HEAD~1", "--dir", str(repo.root)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Guided review: `main..HEAD~1`")
    assert "2 commit(s)" in out and "refactor: three" not in out


# Covers F-0006.R2 (specs/0006-guided-review-of-a-change.md).
def test_errors_exit_2_with_a_message_and_write_nothing(
    repo: Repo,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A directory that is not a git repository: no base can be resolved.
    plain = tmp_path_factory.mktemp("not-a-repository")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(plain.parent))
    assert main(["tour", "--out", "guide.md", "--dir", str(plain)]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("Error: ") and "Pass one" in captured.err
    assert captured.out == "" and not (plain / "guide.md").exists()
    # An empty range: `feature` has no commit of its own yet.
    assert main(["tour", "main", "--out", "guide.md", "--dir", str(repo.root)]) == 2
    captured = capsys.readouterr()
    assert captured.err.strip() == "Error: no commits in main..HEAD" and captured.out == ""
    # A head git does not know: git's own message, behind the same prefix.
    repo.write("a.py", "a = 1\n")
    repo.commit("feat: one")
    args = ["tour", "main", "--head", "nope", "--out", "guide.md", "--dir", str(repo.root)]
    assert main(args) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("Error: ") and captured.out == ""
    assert not (repo.root / "guide.md").exists()


# Covers F-0006.R3 (specs/0006-guided-review-of-a-change.md).
def test_guide_opens_with_a_header_orders_each_section_and_closes_with_the_checklist(
    repo: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "\n".join(f"body line {n}" for n in range(1, 11))
    repo.write("src/calc.py", "x = 1\n" * 4)
    repo.write("tests/test_calc.py", "def test_x():\n    assert True\n")
    sha = repo.commit("feat: calculator\n\n" + body)
    assert main(["tour", "main", "--dir", str(repo.root)]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "# Guided review: `main..HEAD`"
    assert lines[2].startswith(
        "1 commit(s), 6 changed lines (lockfiles excluded) → about **1 sitting(s)** of ≤300 lines."
    )
    marks = [
        "## 1. feat: calculator",
        f"`{sha[:10]}` · t · kind: **behavioral** · 6 lines, 2 files",
        "> body line 1",
        "**Risk hotspots** (read these first):",
        "**Reading order** (tests first, then what the subject names, then the largest):",
        "**Verify:**",
        "**Explanation test** (answer before approving):",
        "## Before you approve",
    ]
    positions = [out.index(mark) for mark in marks]
    assert positions == sorted(positions)
    assert "> body line 8" in out and "body line 9" not in out  # the first eight lines only
    assert out.count("- [ ] ") == 5
    assert out.index("## Before you approve") < out.index("Record the decision: `rumbo decision")
    # `--since` adds the re-review command to the header, and only then.
    assert "git range-diff" not in out
    assert main(["tour", "main", "--since", "v1", "--dir", str(repo.root)]) == 0
    out = capsys.readouterr().out
    assert "`git range-diff main v1 HEAD`" in out
    assert out.index("git range-diff") < out.index("## 1. feat: calculator")


# Covers F-0006.R3 and F-0006.R6 (specs/0006-guided-review-of-a-change.md).
def test_budget_excludes_lockfiles_and_flags_a_commit_over_300_lines() -> None:
    big = _commit(
        "feat: big", [FileChange("src/big.py", 301, 0, "A"), FileChange("uv.lock", 900, 0)]
    )
    text = render_markdown([big], "main", "HEAD")
    assert "1 commit(s), 301 changed lines (lockfiles excluded) → about **2 sitting(s)**" in text
    assert "kind: **behavioral** · 301 lines, 2 files" in text
    assert "uv.lock" not in text  # neither counted nor listed
    assert "> Over budget (301 > 300)" in text
    assert text.index("1. `src/big.py`") < text.index("Over budget") < text.index("**Verify:**")
    small = _commit("feat: small", [FileChange("src/a.py", 200, 100)])
    text = render_markdown([small], "main", "HEAD")
    assert "Over budget" not in text and "about **1 sitting(s)**" in text
    two = render_markdown([small, small, small], "main", "HEAD")
    assert "3 commit(s), 900 changed lines" in two and "about **3 sitting(s)**" in two


CODE = FileChange("src/a.py", 1, 0)
TEST = FileChange("tests/test_a.py", 1, 0)
DOC = FileChange("docs/a.md", 1, 0)


# Covers F-0006.R4 (specs/0006-guided-review-of-a-change.md).
@pytest.mark.parametrize(
    ("subject", "files", "kind"),
    [
        ("feat: x", [CODE], "behavioral"),
        ("fix(cli)!: x", [CODE], "behavioral"),
        ("perf: x", [CODE], "behavioral"),
        ("refactor: x", [CODE], "structural"),
        ("style: x", [CODE], "structural"),
        ("test: x", [TEST], "tests"),
        ("docs: x", [DOC], "docs"),
        ("chore: x", [CODE], "mechanical"),
        ("ci: x", [CODE], "mechanical"),
        ("build: x", [CODE], "mechanical"),
        # No known prefix: the files decide.
        ("Add more cases", [TEST, FileChange("src/test_b.py", 1, 0)], "tests"),
        ("Explain the thing", [DOC, FileChange("NOTES.txt", 1, 0)], "docs"),
        ("wip: x", [TEST], "tests"),
        ("Add things", [TEST, DOC], "behavioral"),
        ("Add things", [CODE, TEST], "behavioral"),
        ("Empty", [], "behavioral"),
    ],
)
def test_kind_comes_from_the_prefix_and_otherwise_from_the_files(
    subject: str, files: list[FileChange], kind: str
) -> None:
    commit = _commit(subject, files)
    assert commit.kind == kind
    assert f"kind: **{kind}**" in render_markdown([commit], "main", "HEAD")


# Covers F-0006.R5 (specs/0006-guided-review-of-a-change.md).
def test_risk_hotspots_follow_the_path_rules() -> None:
    paths = [
        ".github/workflows/ci.yml",
        ".pre-commit-config.yaml",
        "pyproject.toml",
        "Dockerfile",
        ".env",
        "src/app/auth.py",
        "scripts/check_tests_not_weakened.py",
        "AGENTS.md",
    ]
    commit = _commit("feat: x", [FileChange(path, 1, 0) for path in paths])
    assert commit.risks() == [
        "CI / workflows (can weaken every other sensor): .github/workflows/ci.yml",
        "pre-commit hooks: .pre-commit-config.yaml",
        "dependencies: pyproject.toml",
        "infra / deployment: Dockerfile",
        "secrets / environment: .env",
        "auth / security code: src/app/auth.py",
        "the anti-cheat sensor itself: scripts/check_tests_not_weakened.py",
        "agent guides (AGENTS.md / CLAUDE.md / commands): AGENTS.md",
    ]
    text = render_markdown([commit], "main", "HEAD")
    assert "- ⚠️ dependencies: pyproject.toml" in text
    assert text.index("- ⚠️ CI / workflows") < text.index("**Reading order**")
    # A rule names at most four of the paths it matched.
    many = _commit("ci: x", [FileChange(f".github/workflows/w{n}.yml", 1, 0) for n in range(5)])
    (risk,) = many.risks()
    assert risk.count(".yml") == 4 and "w4.yml" not in risk
    # No rule matched: the guide says so instead of leaving the list empty.
    clean = _commit("feat: x", [FileChange("src/a.py", 1, 0)])
    assert clean.risks() == []
    assert "- none detected by path rules (still read the tests)" in render_markdown(
        [clean], "main", "HEAD"
    )


# Covers F-0006.R5 (specs/0006-guided-review-of-a-change.md).
def test_risk_hotspots_name_deleted_tests_removed_asserts_and_new_skips(repo: Repo) -> None:
    repo.write("tests/test_0gone.py", "def test_gone():\n    assert 1\n")
    repo.write("tests/test_a.py", "def test_a():\n    assert 1\n    assert 2\n")
    repo.write("tests/test_b.py", "def test_b():\n    assert 1\n")
    repo.write("tests/test_c.py", "def test_c():\n    assert 1\n")
    repo.write("tests/test_ok.py", "def test_ok():\n    assert 1\n")
    repo.write("src/code.py", "def f(x):\n    assert x\n    return x\n")
    repo.commit("test: first")
    (repo.root / "tests" / "test_0gone.py").unlink()
    repo.write("tests/test_a.py", "def test_a():\n    assert 1\n")
    repo.write(
        "tests/test_b.py",
        "import pytest\n\n\n@pytest.mark.skip\ndef test_b():\n    assert 1\n",
    )
    repo.write(
        "tests/test_c.py",
        "import pytest\n\n\ndef test_c():\n    pytest.skip('later')\n    assert 1\n",
    )
    repo.write("tests/test_ok.py", "def test_ok():\n    assert 1\n    assert 2\n")
    repo.write("src/code.py", "def f(x):\n    return x\n")  # an assert removed outside the tests
    repo.commit("test: second")
    (commit,) = load_commits("HEAD~1", "HEAD", repo.root)
    assert commit.risks() == [
        "deleted test files: tests/test_0gone.py",
        "removed asserts or added skip/xfail in: tests/test_a.py, tests/test_b.py, tests/test_c.py",
    ]


# Covers F-0006.R6 (specs/0006-guided-review-of-a-change.md).
def test_reading_order_is_tests_then_named_by_the_subject_then_largest() -> None:
    commit = _commit(
        "feat: add parser cache",
        [
            FileChange("README.md", 100, 0),
            FileChange("src/zeta.py", 40, 10),
            FileChange("src/Parser.py", 5, 0, "A"),
            FileChange("tests/test_zeta.py", 1, 0, "A"),
            FileChange("src/old.py", 0, 2, "D"),
            FileChange("uv.lock", 900, 0),
        ],
    )
    assert [f.path for f in commit.reading_order()] == [
        "tests/test_zeta.py",  # a test, although it is the smallest
        "src/Parser.py",  # named by the subject ("parser"), whatever the case
        "README.md",  # then by size, largest first
        "src/zeta.py",
        "src/old.py",
    ]
    text = render_markdown([commit], "main", "HEAD")
    for line in (
        "1. `tests/test_zeta.py` (test, new, +1/−0)",
        "2. `src/Parser.py` (code, new, +5/−0)",
        "3. `README.md` (doc, +100/−0)",
        "4. `src/zeta.py` (code, +40/−10)",
        "5. `src/old.py` (code, deleted, +0/−2)",
    ):
        assert line in text
    # What counts as a test: under tests/, under any /tests/, or a file named test_*.
    nested = _commit(
        "Add things",
        [
            FileChange("src/big.py", 90, 0),
            FileChange("pkg/tests/data.json", 1, 0),
            FileChange("src/test_inline.py", 2, 0),
        ],
    )
    assert [f.path for f in nested.reading_order()] == [
        "src/test_inline.py",
        "pkg/tests/data.json",
        "src/big.py",
    ]


# Covers F-0006.R7 (specs/0006-guided-review-of-a-change.md).
def test_verification_commands_use_the_runner_never_make() -> None:
    runner = "uv run python scripts/rumbo_check.py"
    show = f"git show --stat {SHORT}"
    tests = [FileChange(f"tests/test_{name}.py", 1, 0) for name in "abcd"]
    others = [
        FileChange("tests/test_gone.py", 0, 5, "D"),  # deleted: nothing to run
        FileChange("tests/data.json", 1, 0),  # a test file pytest is not pointed at
        FileChange("src/a.py", 1, 0),
    ]
    behavioral = _verify(_commit("feat: x", [*others, *tests]))
    assert behavioral == [
        show,
        "uv run pytest -q tests/test_a.py tests/test_b.py tests/test_c.py",  # three at most
        runner,
        f"git revert --no-commit {SHORT} && {runner} test; git revert --abort",
    ]
    assert _verify(_commit("refactor: x", [CODE])) == [show, runner]
    assert _verify(_commit("chore: x", [CODE])) == [show, runner]
    assert _verify(_commit("test: x", [TEST])) == [show, "uv run pytest -q tests/test_a.py"]
    assert _verify(_commit("docs: x", [DOC])) == [show]
    for subject in ("feat: x", "refactor: x", "chore: x", "test: x", "docs: x"):
        guide = render_markdown([_commit(subject, [CODE, TEST, DOC])], "main", "HEAD")
        assert "make" not in guide


# Covers F-0006.R8 (specs/0006-guided-review-of-a-change.md).
def test_three_questions_with_folded_hints_and_a_structural_variant() -> None:
    def explanation(commit: Commit) -> tuple[list[str], str]:
        text = render_markdown([commit], "main", "HEAD")
        part = text.split("**Explanation test** (answer before approving):\n", 1)[1]
        part = part.split("## Before you approve", 1)[0]
        lines = part.splitlines()
        questions = [line for line in lines if line.startswith("- ")]
        # Every question is followed by its hint, folded.
        for question in questions:
            assert lines[lines.index(question) + 1] == "  <details><summary>hint</summary>"
        assert part.count("<details>") == part.count("</details>") == len(questions)
        return questions, part

    files = [
        FileChange("docs/guide.md", 50, 0),
        FileChange("src/parser.py", 5, 0),
        FileChange("tests/test_parser.py", 3, 0),
    ]
    feat = _commit("feat: add parser", files, body="Needed for imports.\nSecond line.")
    questions, part = explanation(feat)
    assert len(questions) == 3
    assert questions[0] == (
        "- Which test or sensor proves that «feat: add parser» works? Point to the exact test."
    )
    assert "Tests touched here: tests/test_parser.py." in part
    # The second question names the first file of the reading order that is code.
    assert questions[1].startswith("- If the change to `src/parser.py` were reverted, what ")
    assert "Author's stated intent: Needed for imports." in part and "Second line." not in part
    assert questions[2].startswith("- Name one alternative to this approach and why it was not ")
    # Without tests and without a body the hints say so.
    bare, part = explanation(_commit("feat: add parser", [FileChange("src/parser.py", 5, 0)]))
    assert len(bare) == 3
    assert "Tests touched here: none — that is a finding in itself." in part
    assert "Author's stated intent: (the commit body is empty: ask the author)" in part
    # A structural commit swaps the second question; the other two stay.
    structural, _ = explanation(_commit("refactor: split parser", files))
    assert len(structural) == 3
    assert structural[1] == (
        "- This commit claims to change structure only. Which lines, if any, change behavior?"
    )
    assert structural[0].startswith("- Which test or sensor proves that «refactor: split parser»")
    assert structural[2] == questions[2]


# Covers F-0006.R9 (specs/0006-guided-review-of-a-change.md).
def test_out_writes_the_file_or_names_it_in_a_directory(
    repo: Repo,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo.write("a.py", "a = 1\n")
    first = repo.commit("feat: one")
    repo.write("b.py", "b = 1\n")
    second = repo.commit("feat: two")
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    monkeypatch.chdir(elsewhere)
    root = str(repo.root)
    # A file: relative to --dir (not to the working directory), parents created, replaced.
    target = repo.root / "new" / "deep" / "guide.md"
    assert main(["tour", "main", "--out", "new/deep/guide.md", "--dir", root]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Wrote ") and out.strip().endswith("guide.md")
    assert "# Guided review" not in out  # the guide goes to the file, not to stdout too
    assert target.read_text(encoding="utf-8").startswith("# Guided review: `main..HEAD`")
    assert not (elsewhere / "new").exists()
    target.write_text("stale", encoding="utf-8")
    assert main(["tour", "main", "--out", "new/deep/guide.md", "--dir", root]) == 0
    assert "feat: two" in target.read_text(encoding="utf-8")
    # An existing directory, no trailing slash: <date>-<first ten of the last commit>.md.
    (repo.root / "reviews").mkdir()
    days = {dt.date.today().isoformat()}  # a set: the test may run across midnight
    assert main(["tour", "main", "--head", "HEAD~1", "--out", "reviews", "--dir", root]) == 0
    # A trailing slash names a directory even when it does not exist yet.
    assert main(["tour", "main", "--out", "fresh/", "--dir", root]) == 0
    days.add(dt.date.today().isoformat())
    (written,) = (repo.root / "reviews").iterdir()
    assert written.name in {f"{day}-{first[:10]}.md" for day in days}  # of the range, not HEAD
    assert "feat: two" not in written.read_text(encoding="utf-8")
    (written,) = (repo.root / "fresh").iterdir()
    assert written.name in {f"{day}-{second[:10]}.md" for day in days}
    # An absolute path is taken as given.
    absolute = elsewhere / "absolute.md"
    assert main(["tour", "main", "--out", str(absolute), "--dir", root]) == 0
    assert absolute.read_text(encoding="utf-8").startswith("# Guided review")
    capsys.readouterr()


# Covers F-0006.R10 (specs/0006-guided-review-of-a-change.md).
def test_the_tour_runs_only_read_only_git_commands_and_opens_no_socket(
    repo: Repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo.write("src/a.py", "a = 1\n")
    repo.write("tests/test_a.py", "def test_a():\n    assert True\n")
    repo.commit("feat: one\n\nWith a body.")
    calls: list[list[str]] = []
    real_run = subprocess.run

    def recording_run(cmd: list[str], *args: Any, **kwargs: Any) -> Any:
        calls.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    def no_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("rumbo tour opened a socket")

    monkeypatch.setattr(subprocess, "run", recording_run)
    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    # The default base (origin/main) is unknown here, so the fallback path runs too.
    assert main(["tour", "--out", "guide.md", "--dir", str(repo.root)]) == 0
    monkeypatch.undo()
    capsys.readouterr()
    assert calls and {call[0] for call in calls} == {"git"}
    assert {call[1] for call in calls} == {"rev-parse", "log", "show"}
    assert (repo.root / "guide.md").exists()


# Covers F-0006.R10 (specs/0006-guided-review-of-a-change.md).
def test_tour_imports_the_standard_library_only_and_no_network_module() -> None:
    assert tour.__file__ is not None
    tree = ast.parse(Path(tour.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "subprocess" in imported  # git is its only way out of the process
    assert imported <= sys.stdlib_module_names | {"__future__"}
    network = {"socket", "ssl", "urllib", "http", "ftplib", "smtplib", "asyncio", "xmlrpc"}
    assert not imported & network
