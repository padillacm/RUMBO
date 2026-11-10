"""Gates against the state of the project: fitness functions of ADR-0018 (a verification that
did not happen fails G4) and ADR-0020 (G3 and `rumbo trace` follow the tests directory), plus the
audit fix to the checklists (ADR-0010)."""

import datetime as dt
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from rumbo import __version__, gates, metrics
from rumbo.cli import main
from rumbo.gates import GATES, Result, evaluate, report
from rumbo.project import KINDS, LAYOUTS, TEMPLATE_MARK, Project
from rumbo.trace import trace


def write(root: Path, rel: str, text: str = "") -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def bare_project(root: Path, kind: str = "service", rigor: str = "mvp", tests: str = "") -> Project:
    """A project that is only its `[tool.rumbo]` table, loaded back the way the CLI loads it."""
    lines = ["[project]", 'name = "demo"', 'version = "0.1.0"', "", "[tool.rumbo]"]
    lines += ['package = "demo"', f'kind = "{kind}"', f'rigor = "{rigor}"', "llm = false"]
    if tests:
        lines.append(f'tests = "{tests}"')
    write(root, "pyproject.toml", "\n".join(lines) + "\n")
    return Project.load(root)


def by_description(code: str, project: Project) -> dict[str, Result]:
    _, results = evaluate(code, project, run=False)
    return {r.description: r for r in results}


def git_repo(root: Path) -> Callable[..., str]:
    """`git` for a temporary repository: what is in `root` is committed on `main` and the
    branch `work` is checked out on top of it."""
    identity = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
    }

    def git(*args: str) -> str:
        proc = subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, **identity},
        )
        return proc.stdout.strip()

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-qm", "chore: harness")
    git("checkout", "-qb", "work")
    return git


# ---------------------------------------------------------------- ADR-0018: gate G4
# Covers F-0005.R2, F-0005.R10 (specs/0005-gates-and-traceability.md).
def test_g4_fails_when_the_check_could_not_be_measured(
    simple_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gate cannot be green on a verification that did not happen: the runner exiting 3, or
    a runner or sensor that could not be executed, fails G4 and names who repairs it."""
    real_run = subprocess.run
    exits: dict[str, int | None] = {}  # script name -> exit code; None: could not be executed
    ran: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        script = Path(cmd[1]).name if cmd[0] == sys.executable else ""
        if script not in exits:
            return real_run(cmd, **kwargs)
        ran.append(script)
        code = exits[script]
        if code is None:
            raise FileNotFoundError(2, "No such file or directory", cmd[0])
        return subprocess.CompletedProcess(cmd, code, "ENV  types (pyright: not found)\n", "")

    monkeypatch.setattr(gates.subprocess, "run", fake_run)
    project = Project.load(simple_project)
    runner, sensor = "rumbo check green", "no test weakened"

    def measured() -> dict[str, Result]:
        gate, results = evaluate("G4", project, run=True)
        text, passed = report(gate, results, project)
        assert not passed and "FAILED" in text
        return {r.description: r for r in results}

    exits.update({"rumbo_check.py": 3, "check_tests_not_weakened.py": 0})
    rows = measured()
    assert rows[runner].ok is False and rows[sensor].ok is True
    assert "environment broken (exit 3)" in rows[runner].detail
    assert "the human repairs it" in rows[runner].detail
    assert "pyright: not found" in rows[runner].detail, "the tail of the runner is kept"

    exits["rumbo_check.py"] = None
    rows = measured()
    assert rows[runner].ok is False
    assert "environment" in rows[runner].detail and "the human repairs it" in rows[runner].detail
    assert "is not installed" in rows[runner].detail

    exits.update({"rumbo_check.py": 0, "check_tests_not_weakened.py": None})
    rows = measured()
    assert rows[runner].ok is True
    assert rows[sensor].ok is False
    assert "environment" in rows[sensor].detail and "the human repairs it" in rows[sensor].detail

    # --no-run is still "not measured": nothing runs and nothing fails.
    ran.clear()
    gate, results = evaluate("G4", project, run=False)
    skipped = next(r for r in results if r.description == "sensor and rumbo check")
    assert skipped.ok is None and skipped.detail == "skipped (--no-run)"
    assert ran == [] and report(gate, results, project)[1] is True


# ------------------------------------------------- ADR-0020: the tests directory
# Covers F-0005.R11, F-0005.R12, F-0005.R13 (specs/0005-gates-and-traceability.md).
def test_g3_and_trace_follow_the_tests_directory(tmp_path: Path) -> None:
    """An adopted project with `tests = "test"` passes G3 and is traced in `test/`."""
    root = tmp_path / "adopted"
    project = bare_project(root, kind="library", tests="test")
    assert project.tests_dir == "test" and not project.arch
    present = "architecture test present"
    row = by_description("G3", project)[present]
    assert row.ok is False and row.detail == "missing test/architecture/test_architecture.py"
    write(root, "tests/architecture/test_architecture.py", "def test_x() -> None: ...\n")
    assert by_description("G3", project)[present].ok is False, "tests/ is not this project's"
    write(root, "test/architecture/test_architecture.py", "def test_x() -> None: ...\n")
    assert by_description("G3", project)[present].ok is True

    spec = (
        "# Spec: price\n\n## Requirements\n"
        "- R1 (event): When a date is given, the system shall return the price\n"
        "- R2 (unwanted): If the date has no data, then the system shall abstain\n"
        "- R3: The system shall name its source\n"
    )
    docstring = 'def test_price() -> None:\n    """{}: covered."""\n'
    write(root, "specs/0001-price.md", spec)
    write(root, "test/unit/test_price.py", docstring.format("F-0001.R1"))
    write(root, "tests/unit/test_decoy.py", docstring.format("F-0001.R2"))
    write(root, "evals/datasets/cases.jsonl", '{"id": "F-0001.R3-001"}\n')
    covered = {r.full_id: r.covered_by for r in trace(root)}  # called as `rumbo trace` calls it
    assert covered == {
        "F-0001.R1": ["test/unit/test_price.py:2"],
        "F-0001.R2": [],
        "F-0001.R3": ["evals/datasets/cases.jsonl:1"],
    }
    traced = by_description("G4", project)[
        "every spec requirement is referenced by a test or eval case"
    ]
    assert traced.ok is None and "1 without a test: F-0001.R2 " in traced.detail

    # A project without the key keeps reading tests/, as before.
    root = tmp_path / "generated"
    project = bare_project(root, kind="library")
    row = by_description("G3", project)[present]
    assert row.ok is False and row.detail == "missing tests/architecture/test_architecture.py"
    write(root, "tests/architecture/test_architecture.py", "def test_x() -> None: ...\n")
    assert by_description("G3", project)[present].ok is True
    write(root, "specs/0001-price.md", spec)
    write(root, "tests/unit/test_price.py", docstring.format("F-0001.R1"))
    write(root, "test/unit/test_decoy.py", docstring.format("F-0001.R2"))
    covered = {r.full_id: r.covered_by for r in trace(root)}
    assert covered["F-0001.R1"] == ["tests/unit/test_price.py:2"] and covered["F-0001.R2"] == []


# ------------------------------------------ ADR-0010: the checklists (audit finding 6)
# Covers F-0005.R5, F-0005.R6 (specs/0005-gates-and-traceability.md).
def test_checklists_follow_kind_and_layout(tmp_path: Path) -> None:
    """The human is asked to sign what the automatic checks expect of this kind and layout:
    no import-linter contracts for a flat package, no SLOs or runbook for a library or a CLI."""
    contracts = "Import-linter contracts enforced in CI"
    slos = "SLOs and alerts active"
    runbook = "Runbook with one entry per failure mode"
    canary = "Canary or feature flags per cohort; SLOs with error budget"

    def project(kind: str, layout: str, rigor: str = "production") -> Project:
        return Project(
            root=tmp_path, package="demo", llm=False, rigor=rigor, kind=kind, layout=layout
        )

    def checklist(code: str, kind: str, layout: str, rigor: str = "production") -> list[str]:
        return GATES[code].checklist(project(kind, layout, rigor))

    assert contracts not in checklist("G3", "cli", "flat")
    assert contracts in checklist("G3", "service", "hexagonal")
    assert contracts in checklist("G3", "library", "layered")
    assert "C4 levels 1–2" in checklist("G3", "cli", "flat"), "the other items stay"

    for kind in ("library", "cli"):
        asked = checklist("G5", kind, "flat")
        assert not {slos, runbook, canary} & set(asked)
        assert "Rollback rehearsed" in asked and "Automatic dependency updates" in asked
    asked = checklist("G5", "data", "flat")
    assert runbook in asked and slos not in asked and canary not in asked
    assert {slos, runbook, canary} <= set(checklist("G5", "service", "hexagonal"))
    # Rigor still filters what the kind allows.
    assert canary not in checklist("G5", "service", "hexagonal", "mvp")
    assert slos in checklist("G5", "service", "hexagonal", "mvp")
    assert not {slos, runbook} & set(checklist("G5", "service", "hexagonal", "prototype"))

    # One rule, not two: the checklist and the automatic checks agree for every combination.
    for kind in KINDS:
        for layout in LAYOUTS:
            g3 = by_description("G3", project(kind, layout))
            g5 = by_description("G5", project(kind, layout))
            where = f"{kind}/{layout}"
            assert (contracts in checklist("G3", kind, layout)) == (
                "import-linter contracts" in g3
            ), where
            asked = checklist("G5", kind, layout)
            assert (runbook in asked) == any(d.startswith("Runbook ") for d in g5), where
            assert (slos in asked) == any(d.startswith("SLOs ") for d in g5), where
            assert (canary in asked) == (slos in asked), where


# ------------------------------------------ F-0005: the report, the verdict, the signature
# Covers F-0005.R1, F-0005.R2, F-0005.R3 (specs/0005-gates-and-traceability.md).
def test_report_shows_three_states_and_only_a_failure_turns_the_gate_red(tmp_path: Path) -> None:
    """Passed, failed and unmeasured each get a mark; the header and the verdict follow the
    failures alone; the checklist is printed unticked whatever the checks say."""
    project = bare_project(tmp_path)
    gate = GATES["G1"]
    passed = Result("brief complete", True, "the detail of a passed check is not printed")
    failed = Result("spec complete", False, "first line\nsecond line")
    unmeasured = Result("diff size measured", None, "no base branch")
    sign = 'Record the signature with: rumbo decision "G1 signed" --why "..."'

    def lines_of(results: list[Result], green: bool) -> list[str]:
        text, ok = report(gate, results, project)
        lines = text.splitlines()
        assert ok is green and lines[0].startswith("✅ Gate G1 " if green else "🛑 Gate G1 ")
        assert lines[0].endswith("rigor mvp")
        assert "Human checklist (signed by the human, never by the agent):" in lines
        asked = gate.checklist(project)
        assert asked and all(f"  [ ] {item}" in lines for item in asked)
        assert "[x]" not in text.lower() and lines[-1] == sign
        return lines

    lines = lines_of([passed, failed, unmeasured], green=False)
    assert "  ✅ brief complete" in lines and "  ❌ spec complete" in lines
    assert "  ⚠️  diff size measured" in lines
    assert "       first line" in lines and "       second line" in lines
    assert "       no base branch" in lines
    assert not any("the detail of a passed check" in line for line in lines)
    assert "Automatic checks: 1 FAILED. The gate cannot pass yet." in lines
    assert not any("NOT SIGNED" in line for line in lines)

    lines = lines_of([passed, unmeasured], green=True)
    assert (
        "Automatic checks: green, but 1 could not be measured or are recommendations. "
        "GATE NOT SIGNED yet: the human ticks the checklist above."
    ) in lines

    lines = lines_of([passed], green=True)
    assert (
        "Automatic checks: green. GATE NOT SIGNED yet: the human ticks the checklist above."
    ) in lines


# Covers F-0005.R2 (specs/0005-gates-and-traceability.md).
def test_gate_exits_1_only_when_a_check_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`rumbo gate`: 1 with a failed check, 0 without one, 2 when there is no project."""
    root = tmp_path / "project"
    bare_project(root, kind="library", rigor="prototype")
    assert main(["gate", "g1", "--dir", str(root)]) == 1  # no brief; the code is case-blind
    out = capsys.readouterr().out
    assert out.startswith("🛑 Gate G1 ") and "  ❌ Brief exists" in out
    write(root, "docs/product/brief.md", "# Brief\n\nWho, what, and the metric.\n")
    assert main(["gate", "G1", "--dir", str(root)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("✅ Gate G1 ") and "GATE NOT SIGNED yet" in out
    assert main(["gate", "G1", "--dir", str(tmp_path / "nowhere")]) == 2
    assert "Gate G1" not in capsys.readouterr().out


# Covers F-0005.R3, F-0005.R15 (specs/0005-gates-and-traceability.md).
def test_decision_logs_the_signature_the_report_asks_for(tmp_path: Path) -> None:
    """The tool never signs: the human records the signature with `rumbo decision`, and
    `rumbo metrics` counts it."""
    bare_project(tmp_path)
    log = tmp_path / "docs" / "decisions.md"
    sign = ["decision", "G1 signed", "--why", "validated with five users", "--dir", str(tmp_path)]
    assert main(sign) == 2 and not log.exists(), "without the log nothing is created"
    write(tmp_path, "docs/decisions.md", "# Decisions\n")
    assert main(sign) == 0
    assert main(["decision", "deploy v1", "--who", "ci", "--dir", str(tmp_path)]) == 0
    today = dt.date.today().isoformat()
    text = log.read_text(encoding="utf-8")
    assert (
        f"\n## {today} — G1 signed\n- Decision: G1 signed\n"
        "- Why: validated with five users\n- Decided by: human\n"
    ) in text
    assert f"\n## {today} — deploy v1\n- Decision: deploy v1\n- Decided by: ci\n" in text
    m = metrics.collect(tmp_path)
    assert m.gates_signed == {"G1": 1} and m.deploys == 1


# ------------------------------------------------- F-0005: rigor, llm, the template mark
# Covers F-0005.R5, F-0005.R6 (specs/0005-gates-and-traceability.md).
def test_checks_and_checklists_follow_rigor_and_llm(tmp_path: Path) -> None:
    """An item appears from its rigor on (or only below it), and an LLM item only with `llm`;
    the "workflow vs. agent" ADR is checked and asked for under the same condition."""

    def project(rigor: str, llm: bool) -> Project:
        return Project(root=tmp_path, package="demo", llm=llm, rigor=rigor)

    def checklist(code: str, rigor: str, llm: bool = False) -> list[str]:
        return GATES[code].checklist(project(rigor, llm))

    prd = "PRD with explicit non-goals"
    criteria = "Prototype: a criteria list per feature is enough"
    traceability = "Requirement → test traceability for every feature"
    asked = checklist("G2", "prototype")
    assert criteria in asked and prd not in asked and traceability not in asked
    asked = checklist("G2", "mvp")
    assert prd in asked and criteria not in asked and traceability not in asked
    asked = checklist("G2", "production")
    assert prd in asked and criteria not in asked and traceability in asked

    plan = "Eval plan and dataset of real questions"
    assert plan in checklist("G2", "mvp", llm=True)
    assert plan not in checklist("G2", "mvp") and plan not in checklist("G2", "prototype", llm=True)

    decided = "ADR 'workflow vs. agent' decided by the human"
    checked = "ADR 'workflow vs. agent'"
    for llm in (True, False):
        assert (decided in checklist("G3", "mvp", llm=llm)) is llm
        assert (checked in by_description("G3", project("mvp", llm))) is llm


# Covers F-0005.R7 (specs/0005-gates-and-traceability.md).
def test_a_document_under_the_template_mark_is_not_complete(tmp_path: Path) -> None:
    """The mark is the signature (ADR-0017): until the human removes it, the PRD, a spec and
    an ADR are reported as not complete, whatever they contain."""
    project = bare_project(tmp_path, rigor="mvp")
    mark = TEMPLATE_MARK + " — delete this line when the document is complete. -->\n"
    documents = {
        "docs/product/prd.md": "# PRD\n\nNon-goals: none yet.\n",
        "specs/0042-price.md": (
            "# F-0042: price\n\n## Requirements (EARS)\n"
            "- R1 (event): When a date is given, the system shall return the price\n\n"
            "## Acceptance criteria\ntests/unit/test_price.py::test_price\n"
        ),
        "docs/adr/0001-store.md": "# ADR-0001: store\n\n## Fitness function\nA contract.\n",
    }
    rows = {
        "docs/product/prd.md": ("G2", "PRD complete"),
        "specs/0042-price.md": ("G2", "specs/0042-price.md: complete"),
        "docs/adr/0001-store.md": ("G3", "docs/adr/0001-store.md: complete"),
    }
    for rel, body in documents.items():
        write(tmp_path, rel, mark + body)
    for rel, (code, row) in rows.items():
        result = by_description(code, project)[row]
        assert result.ok is False and "template mark" in result.detail, rel
    for rel, body in documents.items():
        write(tmp_path, rel, body)  # what the human does to sign
    for rel, (code, row) in rows.items():
        assert by_description(code, project)[row].ok is True, rel


# ------------------------------------------------------- F-0005: gate G4 and the diff
# Covers F-0005.R8, F-0005.R9, F-0005.R13 (specs/0005-gates-and-traceability.md).
def test_g4_measures_the_diff_without_lockfiles_and_reads_the_tests_directory(
    tmp_path: Path,
) -> None:
    """The diff is counted since the merge base, lockfiles excluded; the fix lane looks for its
    regression test in `[tool.rumbo].tests`; over 300 lines the diff check is red."""
    project = bare_project(tmp_path, kind="library", tests="test")
    git = git_repo(tmp_path)
    small = "small diff (≤300 lines, lockfiles excluded)"
    regression = "fix lane: regression test added"

    write(tmp_path, "demo/calc.py", "x = 1\n")
    write(tmp_path, "tests/test_calc.py", "y = 1\n")  # not this project's tests directory
    write(tmp_path, "uv.lock", "pin\n" * 400)
    git("add", "-A")
    git("commit", "-qm", "fix: calc")
    rows = by_description("G4", project)
    assert "lane: fix" in rows
    assert not any("spec requirement" in d for d in rows), "no requirement: no coverage row"
    assert rows[small].ok is True and rows[small].detail == "2 lines vs main"
    assert rows["fix lane: diff ≤ 150 lines"].ok is True
    assert rows[regression].ok is False
    assert rows[regression].detail == "no file under test/ gained lines since main"

    write(tmp_path, "test/test_calc.py", "y = 1\n")
    git("add", "-A")
    git("commit", "-qm", "test: cover calc")
    assert by_description("G4", project)[regression].ok is True

    write(tmp_path, "demo/big.py", "x = 1\n" * 300)
    git("add", "-A")
    git("commit", "-qm", "feat: a big feature")
    rows = by_description("G4", project)
    assert "lane: normal" in rows and not any(d.startswith("fix lane:") for d in rows)
    assert rows[small].ok is False and rows[small].detail == "303 lines vs main"


# ------------------------------------------------------------- F-0005: rumbo trace
# Covers F-0005.R11, F-0005.R12 (specs/0005-gates-and-traceability.md).
def test_trace_strict_fails_without_requirements_and_passes_when_all_are_covered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--strict` is what a pipeline runs: 1 with no requirement or with one nobody mentions."""
    bare_project(tmp_path)
    plain = ["trace", "--dir", str(tmp_path)]
    strict = [*plain, "--strict"]
    requirements = "## Requirements (EARS)\n- R1 (event): When asked, the system shall answer\n"
    assert main(plain) == 0
    assert "No requirements found" in capsys.readouterr().out
    assert main(strict) == 1
    write(tmp_path, "specs/0000-template.md", "# F-000N: <name>\n\n" + requirements)
    assert main(strict) == 1
    assert "No requirements found" in capsys.readouterr().out, "the 0000 template is not a spec"

    out_of_scope = "\n## Out of scope\n- R2 (event): When listed here, it is not a requirement\n"
    write(tmp_path, "specs/0042-price.md", "# F-0042: price\n\n" + requirements + out_of_scope)
    assert main(plain) == 0
    assert main(strict) == 1
    out = capsys.readouterr().out
    assert "| F-0042.R1 | When asked, the system shall answer | **missing** |" in out
    assert "0/1 requirements" in out and "F-0042.R2" not in out

    write(
        tmp_path,
        "tests/unit/test_price.py",
        "# Covers F-0042.R1 (specs/0042-price.md).\ndef test_price() -> None: ...\n",
    )
    assert main(strict) == 0
    out = capsys.readouterr().out
    assert "| tests/unit/test_price.py:1 |" in out and "1/1 requirements" in out


# ------------------------------------------------- F-0005: ADR-0020, the source root
# Covers F-0005.R13 (specs/0005-gates-and-traceability.md).
def test_g1_scans_the_source_root_of_the_project(tmp_path: Path) -> None:
    """The spikes rule reads `src/`, or the package itself when `[tool.rumbo].src` is "."."""
    root = tmp_path / "rooted"
    write(root, "pyproject.toml", '[tool.rumbo]\npackage = "demo"\nkind = "library"\nsrc = "."\n')
    project = Project.load(root)
    row = "nothing in spikes/ is imported from demo/"
    write(root, "src/other/bad.py", "from spikes import proto\n")  # not this project's code
    assert by_description("G1", project)[row].ok is True
    write(root, "demo/bad.py", "import spikes.proto\n")
    red = by_description("G1", project)[row]
    assert red.ok is False and red.detail == "demo/bad.py"

    root = tmp_path / "default"
    project = bare_project(root, kind="library")
    row = "nothing in spikes/ is imported from src/"
    write(root, "demo/bad.py", "from spikes import proto\n")  # outside src/: not scanned
    assert by_description("G1", project)[row].ok is True
    write(root, "src/demo/bad.py", "from spikes import proto\n")
    red = by_description("G1", project)[row]
    assert red.ok is False and red.detail == "src/demo/bad.py"


# ------------------------------------------------------------ F-0005: rumbo doctor
# Covers F-0005.R14 (specs/0005-gates-and-traceability.md).
def test_doctor_tells_problems_from_warnings(
    simple_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A problem (❌) makes `rumbo doctor` exit 1; a warning (⚠️) is printed and exits 0."""

    def doctor() -> tuple[int, str]:
        code = main(["doctor", "--dir", str(simple_project)])
        return code, capsys.readouterr().out

    code, out = doctor()
    assert code == 1 and "❌ missing uv.lock" in out and "Harness complete" not in out
    write(simple_project, "uv.lock")
    code, out = doctor()
    assert code == 0 and "rumbo.human" not in out, "not a git clone: nobody to declare"
    assert "✅ Harness complete (package=demo, kind=service, layout=hexagonal, llm=False," in out
    assert "rigor=prototype)." in out and "harness version" not in out
    assert "⚠️  .github/CODEOWNERS still has the @your-handle placeholder" in out
    assert "⚠️  SECURITY.md still has the <security contact> placeholder" in out

    agents = simple_project / "AGENTS.md"
    original = agents.read_text(encoding="utf-8")
    agents.write_text("a line\n" * 101, encoding="utf-8")
    code, out = doctor()
    assert code == 1 and "❌ AGENTS.md has 101 lines (>100)" in out
    agents.write_text("a line\n" * 100, encoding="utf-8")
    assert doctor()[0] == 0
    agents.write_text(original, encoding="utf-8")

    pyproject = simple_project / "pyproject.toml"
    current = f'version = "{__version__}"'
    text = pyproject.read_text(encoding="utf-8")
    assert current in text
    pyproject.write_text(text.replace(current, 'version = "0.0.1"'), encoding="utf-8")
    code, out = doctor()
    assert code == 0 and f"harness version 0.0.1 differs from the CLI ({__version__})" in out
    assert "run 'rumbo update'" in out
    pyproject.write_text(text, encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=simple_project, check=True, capture_output=True)

    git("init", "-q")
    git("config", "rumbo.human", "false")
    code, out = doctor()
    assert code == 0 and "rumbo.human is not set in this clone" in out
    assert "git config rumbo.human true" in out
    git("config", "rumbo.human", "true")
    code, out = doctor()
    assert code == 0 and "rumbo.human" not in out


# ----------------------------------------------------------- F-0005: rumbo metrics
# Covers F-0005.R15 (specs/0005-gates-and-traceability.md).
def test_metrics_tolerate_missing_git_and_malformed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Whatever is missing or malformed is skipped and counted as nothing; no traceback."""
    root = tmp_path / "project"
    bare_project(root)
    write(
        root,
        "docs/decisions.md",
        "# Decisions\n\n"
        "## 2026-13-45 — deploy on a date that does not exist\n- Decided by: ci\n\n"
        "## 2020-01-01 — G4 signed\n- Decided by: human\n\n"
        "## 2020-01-02 — deploy v0\n- Decided by: ci\n\n"
        "## 2026-11-08 — deploy v1\n- Decided by: ci\n",
    )
    write(root, "docs/postmortems/0000-template.md", "# Postmortem\n")
    write(root, "docs/postmortems/0001-wrong-figure.md", "# Postmortem: wrong figure\n")
    write(
        root,
        "evals/datasets/cases.jsonl",
        'not json\n[1, 2]\n\n{"id": "real-1"}\n{"id": "ex-1", "tags": ["example"]}\n',
    )
    report_file = "evals/reports/run.json"
    write(root, report_file, json.dumps({"suite": "smoke", "k": 3, "pass_rate": 0.9, "cases": [1]}))
    git = git_repo(root)
    write(root, "notes.txt", "x\n")
    git("add", "-A")
    git("commit", "-qm", "tidy up")
    today = dt.date(2026, 11, 9)

    m = metrics.collect(root, days=30, today=today)
    assert (m.commits_total, m.commits_by_type) == (2, {"chore": 1, "other": 1})
    assert m.gates_signed == {"G4": 1}, "a signature counts whatever its date"
    assert (m.deploys, m.incidents) == (1, 0), "only the deploy inside the window"
    assert (m.postmortems, m.eval_cases) == (1, 1)
    assert m.latest_eval == {
        "suite": "smoke",
        "k": 3,
        "pass_rate": 0.9,
        "file": "run.json",
        "cases": 1,
    }
    assert (m.specs_total, m.requirements_total, m.requirements_uncovered) == (0, 0, 0)

    def no_git(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(metrics.subprocess, "run", no_git)
    write(root, report_file, "{not json")
    m = metrics.collect(root, days=30, today=today)
    assert (m.commits_total, m.commits_by_type) == (0, {}), "git is not installed"
    assert m.latest_eval is None and m.eval_cases == 1 and m.gates_signed == {"G4": 1}
    assert main(["metrics", "--dir", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["commits_total"] == 0
    assert main(["metrics", "--dir", str(root)]) == 0
    assert "Gates signed by the human: G4 x1" in capsys.readouterr().out
    assert main(["metrics", "--dir", str(tmp_path / "nowhere")]) == 2
