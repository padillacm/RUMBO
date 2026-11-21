"""`rumbo trace` and `rumbo metrics` against what people really write (audit of 2026-11-16).

Spec: specs/0005-gates-and-traceability.md. The requirement ids of the throw-away specs are
built at run time (`rid`), so `rumbo trace` on this repository does not read them as mentions of
its own requirements; the ids in the comments above each test are the real ones.
"""

import datetime as dt
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from rumbo import metrics
from rumbo.cli import main
from rumbo.gates import evaluate
from rumbo.project import Project
from rumbo.trace import render, requirements, trace, uncovered

NOT_UTF8 = "not UTF-8 (save it as UTF-8)"


def rid(spec: int, k: int) -> str:
    """`F-NNNN.Rk`, put together here so that this file never contains the id itself."""
    return f"F-{spec:04d}" + f".R{k}"


def write(root: Path, rel: str, text: str = "", encoding: str = "utf-8") -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding=encoding, newline="\n")


def project(root: Path, rigor: str = "mvp") -> Project:
    write(root, "pyproject.toml", f'[tool.rumbo]\npackage = "demo"\nrigor = "{rigor}"\n')
    return Project.load(root)


SPEC = """# F-0001: demo

## Requirements (EARS)
<!-- - R9 (event): When commented out, the system shall not read it. -->
- R1 (event): When a, the system shall b.
- R2 (unwanted, see (a) below): If c, then the system shall d.
- **R3** (state): While e, the system shall f.
* R4 (event): When g, the system shall h.
- R5 (event) When i, the system shall j.
- R6 (event): When k, the system shall l.
  - R6.1: a nested note, not a requirement
- R8 and the rest are deferred

```text
- R7 (event): an example inside a code fence, not a requirement
```

## Acceptance criteria
- R10 (event): When listed here, it is not a requirement
"""


# Covers F-0005.R11 (specs/0005-gates-and-traceability.md).
def test_requirements_accept_what_people_write_and_keep_what_they_cannot_parse(
    tmp_path: Path,
) -> None:
    """Nested parentheses, a bold id and a `*` bullet are requirements; a fenced example and a
    comment are not; a bullet that starts with an id and is not `- Rk (pattern): text` is kept
    as a line that could not be parsed, with its place in the spec."""
    write(tmp_path, "specs/0001-demo.md", SPEC.replace("\n", "\r\n"))
    found = requirements(tmp_path / "specs" / "0001-demo.md", "specs/0001-demo.md")
    parsed = {r.rid: r.text for r in found if not r.malformed}
    assert parsed == {
        "R1": "When a, the system shall b.",
        "R2": "If c, then the system shall d.",
        "R3": "While e, the system shall f.",
        "R4": "When g, the system shall h.",
        "R6": "When k, the system shall l.",
    }
    unparsed = [(r.rid, r.where, r.text) for r in found if r.malformed]
    assert unparsed == [
        ("R5", "specs/0001-demo.md:9", "- R5 (event) When i, the system shall j."),
        ("R8", "specs/0001-demo.md:12", "- R8 and the rest are deferred"),
    ]


# Covers F-0005.R11, F-0005.R12 (specs/0005-gates-and-traceability.md).
def test_trace_strict_and_g4_fail_on_a_line_that_could_not_be_parsed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A requirement the tool cannot read is reported, never dropped: `--strict` exits 1 and
    G4 fails in production although every requirement it could read has a test."""
    project(tmp_path, rigor="production")
    write(tmp_path, "specs/0001-demo.md", SPEC)
    mentions = " ".join(rid(1, k) for k in (1, 2, 3, 4, 6, 7))
    write(tmp_path, "tests/test_x.py", f"# {mentions}\ndef test_x() -> None: ...\n")
    strict = ["trace", "--strict", "--dir", str(tmp_path)]
    assert main(strict) == 1
    out = capsys.readouterr().out
    assert "5/5 requirements referenced" in out and "**missing**" not in out
    assert rid(1, 7) not in out, "the fenced example is not a requirement"
    assert "2 line(s) under 'Requirements' could not be parsed" in out
    assert "- specs/0001-demo.md:9: - R5 (event) When i, the system shall j." in out
    assert "- specs/0001-demo.md:12: - R8 and the rest are deferred" in out
    assert main(["trace", "--dir", str(tmp_path)]) == 0, "without --strict it only reports"
    capsys.readouterr()

    row = "every spec requirement is referenced by a test or eval case"
    _, results = evaluate("G4", Project.load(tmp_path), run=False)
    coverage = next(r for r in results if r.description == row)
    assert coverage.ok is False
    assert "2 line(s) could not be parsed: specs/0001-demo.md:9, specs/0001-demo.md:12" in (
        coverage.detail
    )

    fixed = SPEC.replace("- R5 (event) When", "- R5 (event): When").replace(
        "- R8 and the rest are deferred\n", ""
    )
    write(tmp_path, "specs/0001-demo.md", fixed)
    assert main(strict) == 1, "R5 is a requirement now, and nothing mentions it"
    assert f"| {rid(1, 5)} | When i, the system shall j. | **missing** |" in capsys.readouterr().out
    write(tmp_path, "tests/test_y.py", f"# {rid(1, 5)}\n")
    assert main(strict) == 0
    assert "could not be parsed" not in capsys.readouterr().out


# Covers F-0005.R11, F-0005.R12 (specs/0005-gates-and-traceability.md).
@pytest.mark.parametrize("encoding", ["utf-16", "cp1252"])
def test_trace_names_a_spec_that_is_not_utf8_instead_of_dropping_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], encoding: str
) -> None:
    """PowerShell 5 writes UTF-16 with `>`: the spec is listed as unreadable, `--strict` fails,
    and nothing raises."""
    project(tmp_path)
    good = "# F-0001: a\n\n## Requirements\n- R1 (event): When a, the system shall b.\n"
    bad = "# F-0002: facturación\n\n## Requirements\n- R1: The system shall add the IVA.\n"
    write(tmp_path, "specs/0001-a.md", good)
    write(tmp_path, "specs/0002-b.md", bad, encoding=encoding)
    write(tmp_path, "tests/test_a.py", f"# {rid(1, 1)}\n")
    reqs = trace(tmp_path)
    assert [(r.full_id, r.malformed) for r in reqs] == [(rid(1, 1), False), ("F-0002.?", True)]
    assert [r.where for r in uncovered(reqs)] == ["specs/0002-b.md"]
    assert f"- specs/0002-b.md: {NOT_UTF8}" in render(reqs)
    assert main(["trace", "--strict", "--dir", str(tmp_path)]) == 1
    assert "1/1 requirements referenced" in capsys.readouterr().out


# Covers F-0005.R11 (specs/0005-gates-and-traceability.md).
def test_trace_reads_the_test_sources_once(tmp_path: Path) -> None:
    """One pass over the sources with one pattern, not one search per requirement and line:
    300 requirements against 40 000 lines used to take over ten seconds."""
    lines = "".join(f"- R{k} (event): When x, the system shall y{k}.\n" for k in range(1, 301))
    write(tmp_path, "specs/0001-a.md", "# a\n\n## Requirements\n" + lines)
    filler = "    assert compute(value, other) == expected  # a plain line of a test\n" * 400
    for n in range(100):
        twice = f"# {rid(1, n + 1)} and again {rid(1, n + 1)}\n"
        write(tmp_path, f"tests/test_{n:03d}.py", twice + filler + f"# {rid(1, 300)}\n")
    started = time.perf_counter()
    reqs = trace(tmp_path, "tests")
    elapsed = time.perf_counter() - started
    assert elapsed < 5, f"{elapsed:.1f} s"
    by_id = {r.full_id: r.covered_by for r in reqs}
    assert by_id[rid(1, 1)] == ["tests/test_000.py:1"], "a line counts once"
    assert by_id[rid(1, 10)] == ["tests/test_009.py:1"], "R10 is not R1 or R100"
    assert len(by_id[rid(1, 300)]) == 100 and by_id[rid(1, 300)][0] == "tests/test_000.py:402"
    assert [r.rid for r in uncovered(reqs)] == [f"R{k}" for k in range(101, 300)]


# Covers F-0005.R15 (specs/0005-gates-and-traceability.md).
def test_metrics_count_nothing_for_a_file_that_is_not_utf8(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A report, a dataset, the decisions log or a spec saved as UTF-16 or cp1252 is skipped:
    `rumbo metrics` still prints and exits 0."""
    project(tmp_path)
    write(tmp_path, "docs/decisions.md", "# Decisions\n\n## 2026-11-10 — G4 signed\n")
    write(tmp_path, "specs/0001-a.md", "# a\n\n## Requirements\n- R1: The system shall a.\n")
    write(tmp_path, "evals/reports/r.json", '{"suite": "s", "pass_rate": 1}', encoding="utf-16")
    (tmp_path / "evals" / "datasets").mkdir(parents=True)
    (tmp_path / "evals" / "datasets" / "d.jsonl").write_bytes(
        b'{"id": "caf\xe9", "input": {}}\n{"id": "ok", "input": {}}\n'
    )
    m = metrics.collect(tmp_path)
    assert m.latest_eval is None and m.eval_cases == 1, "the line that is not UTF-8 is skipped"
    assert (m.gates_signed, m.specs_complete, m.requirements_total) == ({"G4": 1}, 1, 1)
    assert main(["metrics", "--dir", str(tmp_path)]) == 0
    assert "Real eval cases: 1" in capsys.readouterr().out

    write(tmp_path, "evals/datasets/d.jsonl", '{"id": "x", "input": {}}\n', encoding="utf-16")
    write(tmp_path, "docs/decisions.md", "## 2026-11-10 — G4 signed\n", encoding="utf-16")
    write(tmp_path, "specs/0001-a.md", "# a\n\n## Requirements\n- R1: ñ\n", encoding="cp1252")
    m = metrics.collect(tmp_path)
    assert (m.eval_cases, m.gates_signed, m.specs_complete) == (0, {}, 0)
    assert (m.specs_total, m.requirements_total, m.requirements_uncovered) == (1, 1, 1)
    assert main(["metrics", "--dir", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["eval_cases"] == 0


# Covers F-0005.R4, F-0005.R15 (specs/0005-gates-and-traceability.md).
def test_eval_cases_are_objects_with_an_input_and_tags_may_be_anything(tmp_path: Path) -> None:
    """Sixty lines of `{}` are not sixty cases; `"tags": null` does not crash and a tag that
    merely contains the word is not the `example` tag."""
    lines = [
        "{}",
        '{"id": "no-input"}',
        '{"id": "null-tags", "input": {"q": 1}, "tags": null}',
        '{"id": "text-tags", "input": {"q": 2}, "tags": "example"}',
        '{"id": "near", "input": {"q": 3}, "tags": ["examples-of-refunds"]}',
        '{"id": "an-example", "input": {"q": 4}, "tags": ["example"]}',
    ]
    write(tmp_path, "evals/datasets/d.jsonl", "\n".join(lines) + "\n" + "{}\n" * 60)
    assert metrics.eval_cases(tmp_path) == 3


# Covers F-0005.R15 (specs/0005-gates-and-traceability.md).
def test_metrics_days_is_bounded_with_a_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A window no calendar holds is a usage error (exit 2), not an OverflowError."""
    project(tmp_path)
    for days in ("99999999", "-1"):
        with pytest.raises(SystemExit) as usage:  # refused by the parser, as a usage error
            main(["metrics", "--dir", str(tmp_path), "--days", days])
        assert usage.value.code == 2
        captured = capsys.readouterr()
        assert "--days must be between 0 and 36500" in captured.err and captured.out == ""
    with pytest.raises(ValueError, match="--days must be between 0 and 36500, got 36501"):
        metrics.collect(tmp_path, days=36501)
    assert metrics.collect(tmp_path, days=36500).days == 36500
    assert main(["metrics", "--dir", str(tmp_path), "--days", "0"]) == 0


# Covers F-0005.R15 (specs/0005-gates-and-traceability.md).
def test_commits_and_deploys_share_the_first_instant_of_the_window(tmp_path: Path) -> None:
    """The window starts at 00:00 of its first day for the commits as for the decisions: git
    reads a bare date as "that day, at the current time"."""
    today = dt.date.today()
    first_day = today - dt.timedelta(days=30)
    before = first_day - dt.timedelta(days=1)
    project(tmp_path)
    write(
        tmp_path,
        "docs/decisions.md",
        f"# Decisions\n\n## {first_day.isoformat()} — deploy v2\n- Decided by: ci\n\n"
        f"## {before.isoformat()} — deploy v1\n- Decided by: ci\n",
    )

    def git(*args: str, date: str = "") -> None:
        identity = {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@x",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@x",
        }
        stamp = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date} if date else {}
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            env={**os.environ, **identity, **stamp},
        )

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-qm", "chore: the day before", date=f"{before.isoformat()}T23:59:59")
    write(tmp_path, "a.txt", "a\n")
    git("add", "-A")
    git("commit", "-qm", "feat: first second of the window", date=f"{first_day}T00:00:01")
    m = metrics.collect(tmp_path, days=30, today=today)
    assert (m.deploys, m.commits_total, m.commits_by_type) == (1, 1, {"feat": 1})
