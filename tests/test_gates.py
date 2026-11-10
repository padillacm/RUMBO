"""Gates against the state of the project: fitness functions of ADR-0018 (a verification that
did not happen fails G4) and ADR-0020 (G3 and `rumbo trace` follow the tests directory), plus the
audit fixes to the checklists (ADR-0010) and to G2 (PRD features without a spec)."""

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from rumbo import gates
from rumbo.gates import GATES, Result, evaluate, report
from rumbo.project import KINDS, LAYOUTS, Project
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


# ---------------------------------------------------------------- ADR-0018: gate G4
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


# ------------------------------------------------ G2: PRD features (audit finding 8)
def _prd_rows(project: Project) -> list[Result]:
    _, results = evaluate("G2", project, run=False)
    return [r for r in results if "PRD feature" in r.description]


@pytest.mark.parametrize("rigor", ["prototype", "mvp", "production"])
def test_g2_reports_prd_features_without_a_spec(tmp_path: Path, rigor: str) -> None:
    """Reported, never required: the features of the PRD that nobody has specified yet."""
    project = bare_project(tmp_path, rigor=rigor)
    prd = "docs/product/prd.md"
    table = (
        "## Requirements\n"
        "| Feature | What |\n|---|---|\n"
        "| F-0001 | Spot price |\n"
        "| F-0002 | Forecast, builds on F-0001 |\n"
        "| F-0003 | Export |\n"
        "| F-0002 | listed twice |\n"
    )
    write(tmp_path, "specs/0000-template.md", "# Spec\n")
    write(tmp_path, "specs/0001-spot-price.md", "# Spec: spot price\n")
    assert _prd_rows(project) == [], "no PRD: nothing to report"
    write(tmp_path, prd, "<!-- rumbo:template -->\n# PRD\n\n" + table)
    assert _prd_rows(project) == [], "a template PRD lists nothing yet"
    write(tmp_path, prd, "# PRD\n\n## Requirements\nF-0002 is described in prose only.\n")
    assert _prd_rows(project) == [], "no feature id in a table"

    write(tmp_path, prd, "# PRD\n\n" + table)
    (row,) = _prd_rows(project)
    assert row.description == (
        "PRD features without a spec: F-0002, F-0003 (write each when the feature is next touched)"
    )
    assert row.ok is None, "unmeasured: shown with a warning, never a failure"
    gate, results = evaluate("G2", project, run=False)
    assert f"⚠️  {row.description}" in report(gate, results, project)[0]

    write(tmp_path, "specs/0002-forecast.md", "# Spec: forecast\n")
    write(tmp_path, "specs/0003-export.md", "# Spec: export\n")
    (row,) = _prd_rows(project)
    assert (row.description, row.ok) == ("every PRD feature has a spec", True)

    write(tmp_path, prd, "# PRD\n\n| Feature | What |\n|---|---|\n| F-0000 | the template |\n")
    (row,) = _prd_rows(project)
    assert row.ok is None and "F-0000" in row.description, "specs/0000-* is not a spec"
