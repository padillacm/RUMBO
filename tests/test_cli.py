import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from rumbo import cli
from rumbo.cli import main
from rumbo.gates import evaluate
from rumbo.project import TEMPLATE_MARK, Project, is_template, section, slug


def test_init_non_interactive(tmp_path: Path) -> None:
    rc = main(
        [
            "init",
            "--dir",
            str(tmp_path),
            "--yes",
            "--no-install",
            "--name",
            "My Project",
            "--llm",
            "--provider",
            "anthropic",
            "--rigor",
            "production",
        ]
    )
    assert rc == 0
    project = Project.load(tmp_path)
    assert (project.package, project.llm, project.rigor) == ("my_project", True, "production")
    assert "provider anthropic" in (tmp_path / "docs" / "decisions.md").read_text(encoding="utf-8")


def test_init_yes_requires_a0_answers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["init", "--dir", str(tmp_path), "--yes", "--no-install", "--rigor", "mvp"]) == 2
    assert "--llm or --no-llm" in capsys.readouterr().err
    assert main(["init", "--dir", str(tmp_path), "--yes", "--no-install", "--no-llm"]) == 2
    assert "--rigor" in capsys.readouterr().err


def test_init_llm_without_provider_fails(tmp_path: Path) -> None:
    assert (
        main(["init", "--dir", str(tmp_path), "--yes", "--no-install", "--llm", "--rigor", "mvp"])
        == 2
    )


def test_init_rejects_bad_package(tmp_path: Path) -> None:
    assert (
        main(
            [
                "init",
                "--dir",
                str(tmp_path),
                "--yes",
                "--no-llm",
                "--rigor",
                "mvp",
                "--package",
                "Bad-Name",
            ]
        )
        == 2
    )


def test_doctor(llm_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (llm_project / "uv.lock").write_text("", encoding="utf-8")
    assert main(["doctor", "--dir", str(llm_project)]) == 0
    (llm_project / ".claude" / "commands" / "rumbo-tour.md").unlink()
    assert main(["doctor", "--dir", str(llm_project)]) == 1
    assert "rumbo-tour.md" in capsys.readouterr().out


def test_new_numbers_and_titles(simple_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    d = str(simple_project)
    assert main(["new", "adr", "Use DuckDB", "--dir", d]) == 0
    assert main(["new", "adr", r"Workflow vs agent C:\temp \1", "--dir", d]) == 0
    second = simple_project / "docs" / "adr" / "0002-workflow-vs-agent-c-temp-1.md"
    text = second.read_text(encoding="utf-8")
    assert text.splitlines()[1] == r"# ADR-0002: Workflow vs agent C:\temp \1"
    assert TEMPLATE_MARK in text
    assert main(["new", "spec", "Spot price query", "--dir", d]) == 0
    assert (simple_project / "specs" / "0001-spot-price-query.md").exists()
    assert main(["new", "adr", "Use DuckDB", "--dir", d]) == 0
    assert "same title" in capsys.readouterr().out
    assert main(["new", "postmortem", "Wrong figure", "--dir", d]) == 0
    assert main(["new", "adr", "  ", "--dir", d]) == 2


def test_decision(simple_project: Path) -> None:
    assert (
        main(["decision", "Use DuckDB", "--why", "time series", "--dir", str(simple_project)]) == 0
    )
    text = (simple_project / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert "Use DuckDB" in text and "Why: time series" in text


def _complete(path: Path) -> None:
    path.write_text(
        "\n".join(path.read_text(encoding="utf-8").splitlines()[1:]) + "\n", encoding="utf-8"
    )


def test_gate_g1(simple_project: Path) -> None:
    project = Project.load(simple_project)
    _, results = evaluate("G1", project)
    assert any(r.ok is False for r in results)
    _complete(simple_project / "docs" / "product" / "brief.md")
    _, results = evaluate("G1", project)
    assert all(r.ok for r in results)
    (simple_project / "src" / "demo" / "bad.py").write_text(
        "from spikes import proto\n", encoding="utf-8"
    )
    _, results = evaluate("G1", project)
    assert any(r.ok is False and "spikes" in r.description for r in results)


def test_gate_g2_scales_with_rigor(llm_project: Path, simple_project: Path) -> None:
    _, results = evaluate("G2", Project.load(llm_project))
    by_desc = {r.description: r for r in results}
    assert by_desc["PRD complete"].ok is False
    assert any("≥50 real cases" in d for d in by_desc)
    _, results = evaluate("G2", Project.load(simple_project))
    assert {r.description: r.ok for r in results}["PRD complete"] is None  # prototype: optional


def test_gate_g2_ears_ignores_template_placeholders(llm_project: Path) -> None:
    assert main(["new", "spec", "Query", "--dir", str(llm_project)]) == 0
    spec = llm_project / "specs" / "0001-query.md"
    _complete(spec)
    _, results = evaluate("G2", Project.load(llm_project))
    ears = next(r for r in results if r.description.endswith("EARS requirements"))
    assert ears.ok is False
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "- R1 (event): When …, the system shall …",
            "- R1: When the user asks, the system shall answer with the source.",
        ),
        encoding="utf-8",
    )
    _, results = evaluate("G2", Project.load(llm_project))
    assert next(r for r in results if r.description.endswith("EARS requirements")).ok is True


def test_gate_g3_workflow_adr_by_title(llm_project: Path) -> None:
    d = str(llm_project)
    assert main(["new", "adr", "CI pipeline", "--dir", d]) == 0
    (llm_project / "docs" / "adr" / "0001-ci-pipeline.md").write_text(
        "# ADR-0001: CI pipeline\nThe workflow is kept by the agent.\n## Fitness function\nx\n",
        encoding="utf-8",
    )
    _, results = evaluate("G3", Project.load(llm_project))
    assert next(r for r in results if r.description == "ADR 'workflow vs. agent'").ok is False
    assert main(["new", "adr", "Workflow, not agent", "--dir", d]) == 0
    _, results = evaluate("G3", Project.load(llm_project))
    assert next(r for r in results if r.description == "ADR 'workflow vs. agent'").ok is True
    assert next(r for r in results if r.description == "import-linter contracts").ok is True


def test_gate_g2_skips_malformed_dataset_lines(llm_project: Path) -> None:
    dataset = llm_project / "evals" / "datasets" / "odd.jsonl"
    dataset.write_text(
        '["a", "b"]\n"text"\n42\nnot json\n{"id": "odd-1", "input": {}}\n', encoding="utf-8"
    )
    try:
        _, results = evaluate("G2", Project.load(llm_project))
    finally:
        dataset.unlink()
    cases = next(r for r in results if "real cases in evals/datasets/" in r.description)
    assert "found 1 " in cases.detail  # only the object line counts; nothing crashed


def test_gate_g3_tolerates_empty_or_comment_only_adrs(llm_project: Path) -> None:
    adrs = llm_project / "docs" / "adr"
    (adrs / "0007-empty.md").write_text("", encoding="utf-8")
    (adrs / "0008-stub.md").write_text("<!-- a one-line comment -->", encoding="utf-8")
    try:
        _, results = evaluate("G3", Project.load(llm_project))
    finally:
        (adrs / "0007-empty.md").unlink()
        (adrs / "0008-stub.md").unlink()
    by_desc = {r.description: r for r in results}
    assert by_desc["docs/adr/0007-empty.md: complete"].ok is True  # no template mark
    assert by_desc["docs/adr/0007-empty.md: fitness function"].ok is None  # mvp: recommended
    assert by_desc["docs/adr/0008-stub.md: complete"].ok is True


def test_gate_g4_reports_unmeasured_instead_of_green(
    simple_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["gate", "G4", "--no-run", "--dir", str(simple_project)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "could not be measured" in out and "NOT SIGNED" in out


def test_gate_g4_without_make_is_unmeasured_not_a_traceback(
    simple_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rumbo import gates

    real_run = subprocess.run

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "make":
            raise FileNotFoundError(2, "No such file or directory", "make")
        return real_run(cmd, **kwargs)

    # A project generated before ADR-0006 has no runner: G4 falls back to `make check`.
    runner = simple_project / "scripts" / "rumbo_check.py"
    original = runner.read_bytes()
    runner.unlink()
    monkeypatch.setattr(gates.subprocess, "run", fake_run)
    try:
        _, results = evaluate("G4", Project.load(simple_project), run=True)
    finally:
        runner.write_bytes(original)
    check = next(r for r in results if r.description == "rumbo check green")
    assert check.ok is None and "not installed" in check.detail


@pytest.mark.parametrize(
    ("exit_code", "ok", "detail"),
    [(0, True, ""), (1, False, "FAIL lint (exit 1)"), (3, None, "environment broken (exit 3)")],
)
def test_gate_g4_runs_the_runner_and_tells_red_from_broken(
    simple_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    ok: bool | None,
    detail: str,
) -> None:
    from rumbo import gates

    real_run = subprocess.run
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[-1] == "scripts/rumbo_check.py":
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, exit_code, "FAIL lint (exit 1)\n", "")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(gates.subprocess, "run", fake_run)
    _, results = evaluate("G4", Project.load(simple_project), run=True)
    assert calls == [[sys.executable, "scripts/rumbo_check.py"]]  # ADR-0006: the script, not make
    check = next(r for r in results if r.description == "rumbo check green")
    assert check.ok is ok
    assert detail in check.detail
    assert not any("make check" in r.description for r in results)


def test_gate_g3_requires_ci_to_run_the_runner(llm_project: Path) -> None:
    ci = llm_project / ".github" / "workflows" / "ci.yml"
    original = ci.read_text(encoding="utf-8")
    assert "scripts/rumbo_check.py" in original
    _, results = evaluate("G3", Project.load(llm_project))
    assert next(r for r in results if r.description == "CI runs rumbo check").ok is True
    ci.write_text(original.replace("scripts/rumbo_check.py", "make check"), encoding="utf-8")
    try:
        _, results = evaluate("G3", Project.load(llm_project))
    finally:
        ci.write_text(original, encoding="utf-8")
    check = next(r for r in results if r.description == "CI runs rumbo check")
    assert check.ok is False and "ADR-0006" in check.detail


def test_diff_size_is_unmeasured_when_head_is_the_base(tmp_path: Path) -> None:
    from rumbo.gates import diff_size

    env = {**os.environ, "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig")}
    (tmp_path / "gitconfig").write_text("[user]\n\tname = t\n\temail = t@x\n", encoding="utf-8")

    def git(cwd: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)

    origin, clone = tmp_path / "origin", tmp_path / "clone"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "a.py").write_text("x = 1\n" * 500, encoding="utf-8")
    git(origin, "add", ".")
    git(origin, "commit", "-q", "-m", "big")
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    # origin/main is HEAD: a 500-line commit on main must not read as a 0-line diff.
    size, info = diff_size(clone)
    assert size is None and "HEAD is already on 'origin/main'" in info
    # On a branch with one commit the diff is measured against origin/main.
    git(clone, "checkout", "-q", "-b", "feature")
    (clone / "b.py").write_text("y = 2\n" * 3, encoding="utf-8")
    git(clone, "add", ".")
    git(clone, "commit", "-q", "-m", "small")
    assert diff_size(clone) == (3, "origin/main")


def test_gate_g5_prototype_does_not_require_dependabot(simple_project: Path) -> None:
    _, results = evaluate("G5", Project.load(simple_project))
    assert all(r.ok is not False for r in results if "dependency updates" in r.description)


def test_verify_dep_handles_bad_json_and_typos(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers: dict[str, Any] = {
        "reqeusts": {
            "info": {"name": "reqeusts"},
            "releases": {"1.0": [{"upload_time_iso_8601": "2026-10-01T00:00:00Z"}]},
        },
        "ghost": None,
    }
    monkeypatch.setattr(cli, "pypi_info", lambda name: answers[name])
    monkeypatch.setattr(cli, "osv_vulnerabilities", lambda name: 0)
    assert main(["verify-dep", "reqeusts", "ghost"]) == 1
    out = capsys.readouterr().out
    assert "looks like a popular package: requests" in out
    assert "does NOT exist" in out


def test_verify_dep_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(name: str) -> None:
        raise ValueError("bad url")

    monkeypatch.setattr(cli, "pypi_info", boom)
    assert main(["verify-dep", "my package"]) == 2


def test_project_load_errors(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.rumbo]\nllm = false\n", encoding="utf-8")
    with pytest.raises(ValueError):
        Project.load(tmp_path)
    assert main(["gate", "G1", "--dir", str(tmp_path)]) == 2


def test_tour(simple_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=simple_project,
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "h@example.com")
    git("config", "user.name", "H")
    git("add", "-A")
    git("commit", "-qm", "chore: harness")
    git("checkout", "-qb", "feature")
    (simple_project / "src" / "demo" / "domain" / "calc.py").write_text(
        "def avg(xs):\n    return sum(xs) / len(xs)\n", encoding="utf-8"
    )
    (simple_project / "tests" / "unit" / "test_calc.py").write_text(
        "from demo.domain.calc import avg\n\ndef test_avg():\n    assert avg([2, 4]) == 3\n",
        encoding="utf-8",
    )
    (simple_project / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "feat: average calculator\n\nNeeded for the weekly report.")
    rc = main(["tour", "main", "--dir", str(simple_project)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "feat: average calculator" in out and "kind: **behavioral**" in out
    assert "CI / workflows" in out
    assert out.index("tests/unit/test_calc.py") < out.index("src/demo/domain/calc.py")
    assert "Explanation test" in out and "<details>" in out
    assert main(["tour", "main", "--out", "docs/reviews/x.md", "--dir", str(simple_project)]) == 0
    assert (simple_project / "docs" / "reviews" / "x.md").exists()
    # A directory (trailing slash, or existing) names the file <date>-<sha>.md.
    assert main(["tour", "main", "--out", "docs/reviews/", "--dir", str(simple_project)]) == 0
    written = [p.name for p in (simple_project / "docs" / "reviews").glob("20*-*.md")]
    assert len(written) == 1 and re.fullmatch(r"\d{4}-\d{2}-\d{2}-[0-9a-f]{7,}\.md", written[0])
    assert main(["tour", "feature", "--dir", str(simple_project)]) == 2


def test_helpers() -> None:
    assert slug("¿Precio de Bolsa?") == "precio-de-bolsa"
    assert slug("***") == "untitled"
    assert is_template(None)
    assert section("## Fitness function\n<!-- c -->\n\n## Other\nx", "Fitness function") == ""
    assert section("## Fitness function\ncontract X\n", "Fitness function") == "contract X"
    json.dumps({"ok": True})


def test_gate_g2_data_contracts_recommended_then_required(llm_project: Path) -> None:
    assert main(["new", "spec", "Contracts", "--dir", str(llm_project)]) == 0
    _, results = evaluate("G2", Project.load(llm_project))
    contracts = next(
        r for r in results if r.description.endswith("data contracts and file ownership")
    )
    assert contracts.ok is None  # mvp: recommendation
    spec = llm_project / "specs" / "0001-contracts.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "- Types / schemas: <name, fields, invariants>",
            "- Types / schemas: Quote(price: Decimal, at: datetime)",
        ),
        encoding="utf-8",
    )
    _, results = evaluate("G2", Project.load(llm_project))
    assert (
        next(r for r in results if r.description.endswith("data contracts and file ownership")).ok
        is True
    )


def test_trace_and_gate_g4_requirements(
    llm_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", "spec", "Spot price", "--dir", str(llm_project)]) == 0
    spec = llm_project / "specs" / "0001-spot-price.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "- R1 (event): When …, the system shall …\n"
            "- R2 (unwanted): If …, then the system shall …",
            "- R1 (event): When a date is given, the system shall return the spot price\n"
            "- R2 (unwanted): If the date has no data, then the system shall abstain",
        ),
        encoding="utf-8",
    )
    test = llm_project / "tests" / "unit" / "test_spot.py"
    test.write_text(
        'def test_spot_price() -> None:\n    """F-0001.R1: price for a date."""\n', encoding="utf-8"
    )
    assert main(["trace", "--dir", str(llm_project)]) == 0
    out = capsys.readouterr().out
    assert "| F-0001.R1 |" in out and "tests/unit/test_spot.py:2" in out
    assert "| F-0001.R2 |" in out and "**missing**" in out and "1/2 requirements" in out
    assert main(["trace", "--dir", str(llm_project), "--strict"]) == 1
    capsys.readouterr()
    # F-0001.R10 must not count as a reference to R1
    test.write_text(
        'def test_other() -> None:\n    """F-0001.R10 and F-0001.R2."""\n', encoding="utf-8"
    )
    main(["trace", "--dir", str(llm_project)])
    out = capsys.readouterr().out
    assert "| F-0001.R1 | When a date is given, the system shall return the spot price |" in out
    assert "| F-0001.R2 |" in out and out.count("**missing**") == 1

    row = "every spec requirement is referenced by a test or eval case"
    _, results = evaluate("G4", Project.load(llm_project), run=False)
    assert next(r for r in results if r.description == row).ok is None  # mvp: recommendation
    pyproject = llm_project / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace('rigor = "mvp"', 'rigor = "production"'),
        encoding="utf-8",
    )
    _, results = evaluate("G4", Project.load(llm_project), run=False)
    assert next(r for r in results if r.description == row).ok is False
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace('rigor = "production"', 'rigor = "mvp"'),
        encoding="utf-8",
    )
    test.unlink()
    spec.unlink()


def test_gate_g5_requires_budget_and_telemetry_for_llm_products(
    llm_project: Path, simple_project: Path
) -> None:
    row = "per-request budget and GenAI telemetry wired into the entry point"
    _, results = evaluate("G5", Project.load(llm_project))
    assert next(r for r in results if r.description == row).ok is True
    _, simple_results = evaluate("G5", Project.load(simple_project))
    assert all(r.description != row for r in simple_results)
    entry = llm_project / "src" / "xm_agent" / "app" / "entrypoint.py"
    original = entry.read_text(encoding="utf-8")
    entry.write_text(original.replace("Meter(", "object("), encoding="utf-8")
    _, results = evaluate("G5", Project.load(llm_project))
    assert next(r for r in results if r.description == row).ok is False  # mvp: required
    entry.write_text(original, encoding="utf-8")


def test_eval_add_appends_a_case_and_logs_it(
    llm_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["eval", "add", "regressions", "--dir", str(llm_project), "--grader", "abstention"]
    rc = main([*args, "--input", '{"question": "x"}', "--expected", "{}", "--source", "trace 1"])
    assert rc == 0
    dataset = llm_project / "evals" / "datasets" / "regressions.jsonl"
    case = json.loads(dataset.read_text(encoding="utf-8").splitlines()[0])
    assert case["id"] == "regressions-001" and case["source"] == "trace 1"
    assert "regressions-001" in (llm_project / "evals" / "error_analysis" / "log.md").read_text(
        encoding="utf-8"
    )
    # unknown grader, invalid JSON and duplicate ids are usage errors
    assert main([*args[:-2], "--grader", "nope", "--input", "{}", "--expected", "{}"]) == 2
    assert "registered" in capsys.readouterr().err
    assert main([*args, "--input", "not json", "--expected", "{}"]) == 2
    assert main([*args, "--input", "{}", "--expected", "{}", "--id", "regressions-001"]) == 2
    assert len(dataset.read_text(encoding="utf-8").splitlines()) == 1
    dataset.unlink()


def test_eval_add_rejects_the_reserved_suite_name(
    llm_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["eval", "add", "all", "--dir", str(llm_project), "--grader", "abstention"]
    assert main([*args, "--input", "{}", "--expected", "{}"]) == 2
    assert "reserved" in capsys.readouterr().err
    assert not (llm_project / "evals" / "datasets" / "all.jsonl").exists()


def test_eval_add_needs_an_llm_project(simple_project: Path) -> None:
    args = ["eval", "add", "s", "--dir", str(simple_project), "--grader", "x"]
    assert main([*args, "--input", "{}", "--expected", "{}"]) == 2


def test_metrics_reads_decisions_specs_and_evals(
    simple_project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rumbo.metrics import collect, parse_decisions

    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(simple_project.parent))  # hermetic
    main(["new", "spec", "Alpha", "--dir", str(simple_project)])
    main(["decision", "F-0001 closed", "--why", "2 sessions", "--dir", str(simple_project)])
    main(["decision", "G2 signed", "--dir", str(simple_project)])
    main(["decision", "deploy v0.1", "--who", "ci", "--dir", str(simple_project)])
    main(["decision", "incident: wrong figure", "--dir", str(simple_project)])
    decisions = parse_decisions(
        (simple_project / "docs" / "decisions.md").read_text(encoding="utf-8")
    )
    assert [d.title for d in decisions][-2:] == ["deploy v0.1", "incident: wrong figure"]
    assert decisions[-2].who == "ci" and decisions[1].why == "2 sessions"

    m = collect(simple_project)
    assert m.specs_total == 1 and m.specs_closed == ["F-0001"]
    assert m.sessions_per_spec == [2] and m.gates_signed == {"G2": 1}
    assert (m.deploys, m.incidents) == (1, 1)
    assert m.requirements_total == 2 and m.requirements_uncovered == 2
    assert m.commits_total == 0  # not a git repository: no crash
    assert main(["metrics", "--dir", str(simple_project)]) == 0
    out = capsys.readouterr().out
    assert "median 2" in out and "change failure rate ≈ 100%" in out and "0/2" in out
    assert main(["metrics", "--dir", str(simple_project), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["median_sessions_per_spec"] == 2
    (simple_project / "specs" / "0001-alpha.md").unlink()


def test_update_diff_shows_harness_drift_only(
    llm_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    makefile = llm_project / "Makefile"
    original = makefile.read_text(encoding="utf-8")
    makefile.write_text(original + "extra: ; echo hi\n", encoding="utf-8")
    brief = llm_project / "docs" / "product" / "brief.md"
    brief_original = brief.read_text(encoding="utf-8")
    brief.write_text("# mine\n", encoding="utf-8")
    assert main(["update", "--diff", "--dir", str(llm_project)]) == 0
    out = capsys.readouterr().out
    assert "### Makefile [merge by hand]" in out and "-extra: ; echo hi" in out
    assert "brief.md" not in out  # human documents are never compared
    assert makefile.read_text(encoding="utf-8").endswith("extra: ; echo hi\n")  # nothing written
    makefile.write_text(original, encoding="utf-8")
    brief.write_text(brief_original, encoding="utf-8")
    assert main(["update", "--diff", "--dir", str(llm_project)]) == 0
    assert "Everything matches" in capsys.readouterr().out


def test_decisions_parsing_tolerates_hand_written_headings(tmp_path: Path) -> None:
    from rumbo.metrics import parse_decisions

    text = (
        "## 2026-10-30 - F-0001 closed\r\n- why: 3 sessions\r\n- Decided by: human\r\n"
        "##  2026-10-31 – deploy v1\n- Decided by: ci\n"
        "## 2026-11-01 —incident: wrong figure\n- Why: bad fixture\n"
    )
    decisions = parse_decisions(text)
    assert [d.title for d in decisions] == ["F-0001 closed", "deploy v1", "incident: wrong figure"]
    assert decisions[0].why == "3 sessions" and decisions[1].who == "ci"


def test_known_graders_reads_the_dict_with_the_ast(llm_project: Path) -> None:
    graders_file = llm_project / "evals" / "graders" / "__init__.py"
    original = graders_file.read_text(encoding="utf-8")
    assert cli.known_graders(llm_project) == {
        "field_equality",
        "numeric",
        "tool_arguments",
        "abstention",
    }
    graders_file.write_text(
        'GRADERS: dict[str, object] = {"abstention": object}\n', encoding="utf-8"
    )
    assert cli.known_graders(llm_project) == {"abstention"}
    graders_file.write_text("GRADERS = build()\n", encoding="utf-8")
    assert cli.known_graders(llm_project) is None
    graders_file.write_text(original, encoding="utf-8")


def test_eval_add_escapes_the_log_table(llm_project: Path) -> None:
    args = ["eval", "add", "pipes", "--dir", str(llm_project), "--grader", "abstention"]
    rc = main([*args, "--input", "{}", "--expected", "{}", "--note", "a|b\nc", "--source", "x|y"])
    assert rc == 0
    log = (llm_project / "evals" / "error_analysis" / "log.md").read_text(encoding="utf-8")
    assert "| x\\|y | a\\|b c |" in log
    (llm_project / "evals" / "datasets" / "pipes.jsonl").unlink()


def test_trace_ignores_nested_items_and_lookalikes(simple_project: Path) -> None:
    from rumbo.trace import trace

    main(["new", "spec", "Edge", "--dir", str(simple_project)])
    spec = simple_project / "specs" / "0001-edge.md"
    spec.write_text(
        "# F-0001: Edge\n## Requirements (EARS)\n"
        "- R1 (event): When a, the system shall b | c\n"
        "  - R1.1: a nested note\n"
        "- R10 (state): While x, the system shall y\n"
        "- R11 (event: login): When z, the system shall w\n"
        "- R12: The system shall v: always\n"
        "## Acceptance criteria\n",
        encoding="utf-8",
    )
    (simple_project / "tests" / "unit" / "test_edge.py").write_text(
        'def test_x() -> None:\n    """F-0001.R10a is not R10; F-0001.R1. ends a sentence"""\n',
        encoding="utf-8",
    )
    reqs = trace(simple_project)
    assert [r.rid for r in reqs] == ["R1", "R10", "R11", "R12"]
    assert reqs[0].covered_by and not reqs[1].covered_by
    assert reqs[2].text == "When z, the system shall w"  # colon inside the parenthetical
    assert reqs[3].text == "The system shall v: always"  # colon inside the text
    from rumbo.trace import render

    assert "shall b \\| c" in render(reqs)
    spec.unlink()
    (simple_project / "tests" / "unit" / "test_edge.py").unlink()


def test_gate_header_is_green_when_no_check_failed(simple_project: Path) -> None:
    from rumbo.gates import report

    gate, results = evaluate("G4", Project.load(simple_project), run=False)
    text, ok = report(gate, results, Project.load(simple_project))
    assert ok and text.startswith("✅ Gate G4") and "NOT SIGNED" in text
    gate, results = evaluate("G2", Project.load(simple_project))  # no spec yet: a failure
    text, ok = report(gate, results, Project.load(simple_project))
    assert not ok and text.startswith("🛑 Gate G2")


def test_gate_g3_ignores_sections_of_untouched_templates(llm_project: Path) -> None:
    _, results = evaluate("G3", Project.load(llm_project))
    by_desc = {r.description: r for r in results}
    assert by_desc["threat model: lethal trifecta assessed"].ok is False  # mark still there (mvp)
    assert by_desc["threat model: OWASP LLM/Agentic checklist filled"].ok is False
    assert by_desc["C4 has Mermaid diagrams"].ok is False
    tm = llm_project / "docs" / "security" / "threat-model.md"
    original = tm.read_text(encoding="utf-8")
    tm.write_text(
        "\n".join(original.splitlines()[1:]) + "\n", encoding="utf-8"
    )  # human removes the mark
    _, results = evaluate("G3", Project.load(llm_project))
    by_desc = {r.description: r for r in results}
    assert by_desc["threat model: lethal trifecta assessed"].ok is True
    tm.write_text(original, encoding="utf-8")


def test_no_arguments_prints_help_and_a_hint(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    out = capsys.readouterr().out
    assert "rumbo init" in out and "rumbo doctor" in out


def test_doctor_warns_about_placeholders(
    simple_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (simple_project / "uv.lock").write_text("", encoding="utf-8")
    main(["doctor", "--dir", str(simple_project)])
    out = capsys.readouterr().out
    assert "@your-handle placeholder" in out and "<security contact> placeholder" in out
    (simple_project / "uv.lock").unlink()


def test_tour_falls_back_to_a_known_base(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from rumbo.tour import resolve_base

    identity = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
    }

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            env={**os.environ, **identity},
        )

    git("init", "-q", "-b", "main")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-qm", "chore: a")
    git("checkout", "-qb", "feature")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    git("add", "b.txt")
    git("commit", "-qm", "feat: b")
    assert resolve_base("origin/main", tmp_path) == "main"  # no remote: falls back to main
    assert resolve_base("nope", tmp_path) == "main"
    assert main(["tour", "--dir", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert "using 'main'" in captured.err and "feat: b" in captured.out
    git("branch", "-D", "main")
    assert resolve_base("origin/main", tmp_path) is None
    assert main(["tour", "--dir", str(tmp_path)]) == 2
    assert "Pass one" in capsys.readouterr().err


def test_check_subcommand_parses_and_delegates_to_the_runner(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = cli.build_parser().parse_args(
        ["check", "lint", "test", "--brief", "--keep-going", "--base", "x"]
    )
    assert args.steps == ["lint", "test"] and args.brief and args.keep_going and args.base == "x"
    assert args.dir == "."
    python = Path(sys.executable).as_posix()
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.rumbo.checks]\nok = \'"{python}" -c "import sys; sys.exit(0)"\'\n', encoding="utf-8"
    )
    assert main(["check", "--brief", "--dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out.startswith("rumbo check: green (")
    assert (tmp_path / ".rumbo-check.log").is_file()


# ----------------------------------------------------------------- ADR-0009: fix lane
def _git_in(root: Path) -> Callable[..., str]:
    """A `git` runner for a temporary repository with a fixed identity (ADR-0009 tests)."""
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

    return git


def _branch_repo(root: Path) -> tuple[Callable[..., str], str]:
    """Commits everything in `root` on `main`, checks out `fix`; returns (git, merge base)."""
    git = _git_in(root)
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-qm", "chore: harness")
    git("checkout", "-qb", "fix")
    return git, git("rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("subjects", "expected"),
    [
        (["fix: null average", "test: cover it", "refactor(calc): extract", "docs: note"], "fix"),
        (["fix: null average", "feat: median"], "normal"),
        (["fix!: drop the old field"], "normal"),
        (["fix(calc)!: drop the old field"], "normal"),
        (["Fixed the average"], "normal"),
        ([], "normal"),
    ],
)
def test_lane_is_fix_only_when_every_commit_is_a_fix_type(
    tmp_path: Path, subjects: list[str], expected: str
) -> None:
    """ADR-0009: the lane is recognised from Conventional Commit types in base..HEAD."""
    from rumbo.gates import lane, merge_base

    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    git, base = _branch_repo(tmp_path)
    for n, subject in enumerate(subjects):
        (tmp_path / f"f{n}.txt").write_text("x\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-qm", subject)
    assert lane(tmp_path, base) == expected
    if subjects:
        assert merge_base(tmp_path) == (base, "main")
    else:
        found, info = merge_base(tmp_path)  # empty range: HEAD is the base
        assert found is None and "HEAD is already on 'main'" in info


def _fix_lane_results(root: Path) -> dict[str, Any]:
    _, results = evaluate("G4", Project.load(root), run=False)
    return {r.description: r for r in results}


def test_gate_g4_fix_lane_is_green_for_a_tested_small_fix(simple_project: Path) -> None:
    """ADR-0009: regression test added, diff ≤ 150, no new dependency; heading names the lane."""
    from rumbo.gates import report

    git, _ = _branch_repo(simple_project)
    (simple_project / "src" / "demo" / "domain" / "calc.py").write_text(
        "def avg(xs):\n    return sum(xs) / len(xs) if xs else 0.0\n", encoding="utf-8"
    )
    (simple_project / "tests" / "unit" / "test_calc.py").write_text(
        '"""Bug #12: avg([]) raised ZeroDivisionError."""\n'
        "from demo.domain.calc import avg\n\n\ndef test_avg_empty():\n    assert avg([]) == 0.0\n",
        encoding="utf-8",
    )
    git("add", "-A")
    git("commit", "-qm", "fix: avg of an empty list")
    by_desc = _fix_lane_results(simple_project)
    assert by_desc["lane: fix"].ok is True
    assert by_desc["fix lane: regression test added"].ok is True
    assert by_desc["fix lane: diff ≤ 150 lines"].ok is True
    assert by_desc["fix lane: no new runtime dependency"].ok is True
    assert by_desc["small diff (≤300 lines, lockfiles excluded)"].ok is True
    gate, results = evaluate("G4", Project.load(simple_project), run=False)
    text, ok = report(gate, results, Project.load(simple_project))
    assert ok and text.splitlines()[0].endswith("· lane: fix")
    assert "  ✅ lane: fix" not in text  # shown in the heading, not as a check
    # One feat commit returns the PR to the normal lane: the extra rules disappear.
    (simple_project / "src" / "demo" / "domain" / "median.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    git("add", "-A")
    git("commit", "-qm", "feat: median")
    by_desc = _fix_lane_results(simple_project)
    assert "lane: normal" in by_desc
    assert not any(d.startswith("fix lane:") for d in by_desc)


def test_gate_g4_fix_lane_requires_a_regression_test(simple_project: Path) -> None:
    """ADR-0009 rule 1: a fix that adds no line under the tests directory is red."""
    git, _ = _branch_repo(simple_project)
    (simple_project / "src" / "demo" / "domain" / "calc.py").write_text("x = 2\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "fix: untested patch")
    by_desc = _fix_lane_results(simple_project)
    red = by_desc["fix lane: regression test added"]
    assert red.ok is False and "tests/" in red.detail
    assert by_desc["fix lane: diff ≤ 150 lines"].ok is True


def test_gate_g4_fix_lane_limits_the_diff_to_150_lines(simple_project: Path) -> None:
    """ADR-0009 rule 2: half the normal limit; the normal 300-line rule still passes."""
    git, _ = _branch_repo(simple_project)
    (simple_project / "tests" / "unit" / "test_big.py").write_text(
        "x = 1\n" * 200, encoding="utf-8"
    )
    git("add", "-A")
    git("commit", "-qm", "test: a very long regression test")
    by_desc = _fix_lane_results(simple_project)
    assert by_desc["fix lane: regression test added"].ok is True
    red = by_desc["fix lane: diff ≤ 150 lines"]
    assert red.ok is False and red.detail.startswith("200 lines")
    assert by_desc["small diff (≤300 lines, lockfiles excluded)"].ok is True


def test_gate_g4_fix_lane_forbids_a_new_runtime_dependency(simple_project: Path) -> None:
    """ADR-0009 rule 3: [project].dependencies is compared by normalised name."""
    pyproject = simple_project / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    assert "dependencies = []" in original
    pyproject.write_text(
        original.replace("dependencies = []", 'dependencies = ["Typing_Extensions>=4"]'),
        encoding="utf-8",
    )
    git, _ = _branch_repo(simple_project)
    (simple_project / "tests" / "unit" / "test_dep.py").write_text("x = 1\n", encoding="utf-8")
    # Same distribution, spelled differently and with an extra: not a new dependency.
    pyproject.write_text(
        original.replace("dependencies = []", 'dependencies = ["typing-extensions[x] >= 4.1"]'),
        encoding="utf-8",
    )
    git("add", "-A")
    git("commit", "-qm", "fix: pin")
    assert _fix_lane_results(simple_project)["fix lane: no new runtime dependency"].ok is True
    pyproject.write_text(
        original.replace(
            "dependencies = []", 'dependencies = ["typing-extensions>=4", "Requests>=2"]'
        ),
        encoding="utf-8",
    )
    git("add", "-A")
    git("commit", "-qm", "fix: use requests")
    red = _fix_lane_results(simple_project)["fix lane: no new runtime dependency"]
    assert red.ok is False and red.detail.endswith("added since main: requests")


def test_gate_g4_without_a_merge_base_is_the_normal_lane(simple_project: Path) -> None:
    """ADR-0009: no git history means the normal lane and no fix-lane result."""
    by_desc = _fix_lane_results(simple_project)
    assert "lane: normal" in by_desc and by_desc["diff size measured"].ok is None
    assert not any(d.startswith("fix lane:") for d in by_desc)


def test_init_generates_the_fix_command_and_doctor_accepts_it(tmp_path: Path) -> None:
    """ADR-0009: `/rumbo-fix` is part of the harness and `rumbo doctor` still passes."""
    rc = main(
        ["init", "--dir", str(tmp_path), "--yes", "--no-install", "--no-llm", "--rigor", "mvp"]
    )
    assert rc == 0
    command = tmp_path / ".claude" / "commands" / "rumbo-fix.md"
    assert command.is_file()
    text = command.read_text(encoding="utf-8")
    assert text.startswith("---\n") and "argument-hint: <bug>" in text
    assert "regression test" in text and "≤ 150" in text and "/rumbo-ancla" in text
    assert "/rumbo-fix" in (tmp_path / ".claude" / "commands" / "rumbo-ancla.md").read_text(
        encoding="utf-8"
    )
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "/rumbo-fix" in agents and len(agents.splitlines()) < 100
    assert "/rumbo-fix" in (tmp_path / "README.md").read_text(encoding="utf-8")
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    assert main(["doctor", "--dir", str(tmp_path)]) == 0


# ----------------------------------------------------------- ADR-0010: kinds and layouts
def template_context(**overrides: object) -> dict[str, object]:
    """Every variable `ci.yml.tmpl`, `release.yml.tmpl` and `CHANGELOG.md.tmpl` use.

    This is the contract with `Config.context()` in scaffold.py: the rigor flags it always had
    and the ADR-0010 axis flags (`service`, `library`, `cli`, `data`, `hexagonal`, `layered`,
    `flat`, `evals`, `arch`).
    """
    ctx: dict[str, object] = {
        "name": "Demo",
        "package": "demo",
        "rumbo_version": "0.3.0",
        "date": "2026-11-07",
        "prototype": False,
        "mvp": True,
        "production": False,
        "mvp_or_more": True,
        "service": False,
        "library": False,
        "cli": False,
        "data": False,
        "hexagonal": False,
        "layered": False,
        "flat": False,
        "evals": False,
        "arch": False,
        "packaged": False,  # library or cli (released to a package index)
        "deployable": False,  # service or data (shipped as an image)
    }
    ctx.update(overrides)
    return ctx


def _render_template(rel: str, **overrides: object) -> str:
    from rumbo.render import render
    from rumbo.scaffold import TEMPLATES_DIR

    return render((TEMPLATES_DIR / rel).read_text(encoding="utf-8"), template_context(**overrides))


def _kind_project(
    root: Path, kind: str, rigor: str, layout: str = "", llm: bool = False
) -> Project:
    """A bare project with exactly the ops files ADR-0010 lists for `kind`, all complete."""
    layout = layout or {"service": "hexagonal"}.get(kind, "flat")
    (root / "src" / "demo").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "dependabot.yml").write_text("version: 2\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    flags = {
        kind: True,
        "packaged": kind in ("library", "cli"),
        "deployable": kind in ("service", "data"),
        "production": rigor == "production",
        "mvp_or_more": rigor != "prototype",
    }
    (root / ".github" / "workflows" / "ci.yml").write_text(
        _render_template("dot_github/workflows/ci.yml.tmpl", **flags), encoding="utf-8"
    )
    if kind in ("library", "cli"):
        (root / "CHANGELOG.md").write_text(_render_template("CHANGELOG.md.tmpl"), encoding="utf-8")
        (root / ".github" / "workflows" / "release.yml").write_text(
            _render_template("dot_github/workflows/release.yml.tmpl"), encoding="utf-8"
        )
    else:
        (root / "docs" / "ops").mkdir(parents=True)
        (root / "docs" / "ops" / "runbook.md").write_text(
            "# Runbook\n\nRestart it.\n", encoding="utf-8"
        )
        if kind == "service":
            (root / "docs" / "ops" / "slos.md").write_text(
                "# SLOs\n\np95 < 300 ms.\n", encoding="utf-8"
            )
            (root / "infra").mkdir()
            (root / "infra" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    if kind == "data":
        (root / "evals" / "datasets").mkdir(parents=True)
        (root / "evals" / "datasets" / "cases.jsonl").write_text(
            json.dumps({"id": "real-001", "input": {"rows": 3}, "expected": {"rows": 3}}) + "\n",
            encoding="utf-8",
        )
    return Project(root=root, package="demo", llm=llm, rigor=rigor, kind=kind, layout=layout)


def _by_description(code: str, project: Project) -> dict[str, bool | None]:
    _, results = evaluate(code, project, run=False)
    return {r.description: r.ok for r in results}


def test_gate_g2_counts_eval_cases_for_a_data_project_without_llm(tmp_path: Path) -> None:
    data = _by_description("G2", _kind_project(tmp_path / "data", "data", "prototype"))
    assert "≥20 real cases in evals/datasets/ (rigor prototype)" in data
    assert data["Eval plan exists"] is None  # prototype: recommended, not required
    service = _by_description("G2", _kind_project(tmp_path / "svc", "service", "prototype"))
    assert not any("real cases" in d or d.startswith("Eval plan") for d in service)


def test_gate_g3_flat_layout_asks_for_the_architecture_test_not_the_contracts(
    tmp_path: Path,
) -> None:
    project = _kind_project(tmp_path, "cli", "mvp", layout="flat")
    rows = _by_description("G3", project)
    assert "import-linter contracts" not in rows
    assert rows["architecture test present"] is False
    (tmp_path / "tests" / "architecture").mkdir(parents=True)
    (tmp_path / "tests" / "architecture" / "test_architecture.py").write_text(
        "def test_x(): ...\n", encoding="utf-8"
    )
    assert _by_description("G3", project)["architecture test present"] is True


def test_gate_g3_hexagonal_layout_requires_the_two_contracts(tmp_path: Path) -> None:
    project = _kind_project(tmp_path, "service", "mvp", layout="hexagonal")
    rows = _by_description("G3", project)
    assert "architecture test present" not in rows
    assert rows["import-linter contracts"] is False
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        '[[tool.importlinter.contracts]]\nname = "layers"\n'
        '[[tool.importlinter.contracts]]\nname = "pure"\n',
        encoding="utf-8",
    )
    assert _by_description("G3", project)["import-linter contracts"] is True


def test_gate_g5_service_keeps_the_ops_trio(tmp_path: Path) -> None:
    rows = _by_description("G5", _kind_project(tmp_path, "service", "mvp"))
    assert rows["Runbook complete"] and rows["SLOs complete"] and rows["Dockerfile in infra/"]
    assert rows["automatic dependency updates"] is True
    assert not any(
        "CHANGELOG" in d or "Trusted Publishing" in d or "Python versions" in d for d in rows
    )


@pytest.mark.parametrize("kind", ["library", "cli"])
def test_gate_g5_library_and_cli_pass_with_the_generated_release_files(
    tmp_path: Path, kind: str
) -> None:
    rows = _by_description("G5", _kind_project(tmp_path, kind, "production"))
    assert rows == {
        "CHANGELOG.md with an 'Unreleased' section": True,
        "release workflow publishes with Trusted Publishing (no PyPI token secret)": True,
        "CI runs on at least two Python versions": True,
        "automatic dependency updates": True,
        "SBOM generated in CI": True,
    }


def test_gate_g5_library_red_cases(tmp_path: Path) -> None:
    changelog = "CHANGELOG.md with an 'Unreleased' section"
    release = "release workflow publishes with Trusted Publishing (no PyPI token secret)"
    matrix = "CI runs on at least two Python versions"
    project = _kind_project(tmp_path, "library", "mvp")
    _, results = evaluate("G5", project, run=False)
    assert all(r.ok is True for r in results)

    (tmp_path / "CHANGELOG.md").unlink()
    _, results = evaluate("G5", project, run=False)
    bad = next(r for r in results if r.description == changelog)
    assert bad.ok is False and "missing CHANGELOG.md" in bad.detail
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [0.1.0] - 2026-11-07\n", encoding="utf-8"
    )
    assert _by_description("G5", project)[changelog] is False
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## Unreleased\n", encoding="utf-8"
    )  # bare heading ok
    assert _by_description("G5", project)[changelog] is True

    workflow = tmp_path / ".github" / "workflows" / "release.yml"
    good = workflow.read_text(encoding="utf-8")
    workflow.write_text(
        good + "        with:\n          password: ${{ secrets.PYPI_API_TOKEN }}\n",
        encoding="utf-8",
    )
    assert _by_description("G5", project)[release] is False
    workflow.write_text(good.replace("id-token: write", "id-token: none"), encoding="utf-8")
    assert _by_description("G5", project)[release] is False
    workflow.unlink()
    assert _by_description("G5", project)[release] is False
    workflow.write_text(good, encoding="utf-8")

    ci = tmp_path / ".github" / "workflows" / "ci.yml"
    ci.write_text(
        _render_template("dot_github/workflows/ci.yml.tmpl", service=True), encoding="utf-8"
    )
    assert "matrix:" not in ci.read_text(encoding="utf-8")
    assert _by_description("G5", project)[matrix] is False
    ci.write_text(
        "jobs:\n  check:\n    strategy:\n      matrix:\n        python:\n"
        "          - '3.12'\n          - '3.13'\n",
        encoding="utf-8",
    )
    assert _by_description("G5", project)[matrix] is True
    ci.write_text(
        "jobs:\n  check:\n    strategy:\n      matrix:\n        python-version: ['3.13']\n",
        encoding="utf-8",
    )
    assert _by_description("G5", project)[matrix] is False

    # prototype: the same misses are recommendations, never failures
    prototype = Project(
        root=tmp_path, package="demo", llm=False, rigor="prototype", kind="library", layout="flat"
    )
    rows = _by_description("G5", prototype)
    assert rows[matrix] is None and rows["automatic dependency updates"] is True


def test_gate_g5_data_requires_runbook_and_datasets_but_only_recommends_a_dockerfile(
    tmp_path: Path,
) -> None:
    datasets = "datasets present in evals/datasets/"
    project = _kind_project(tmp_path, "data", "mvp")
    rows = _by_description("G5", project)
    assert rows["Runbook complete"] is True and rows[datasets] is True
    assert rows["Dockerfile in infra/"] is None  # recommended: missing is not a failure
    assert "SLOs complete" not in rows and "CHANGELOG.md with an 'Unreleased' section" not in rows
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    assert _by_description("G5", project)["Dockerfile in infra/"] is True

    (tmp_path / "evals" / "datasets" / "cases.jsonl").write_text(
        json.dumps({"id": "example-001", "tags": ["example"]}) + "\n", encoding="utf-8"
    )
    assert _by_description("G5", project)[datasets] is False
    (tmp_path / "docs" / "ops" / "runbook.md").unlink()
    assert _by_description("G5", project)["Runbook exists"] is False

    prototype = Project(
        root=tmp_path, package="demo", llm=False, rigor="prototype", kind="data", layout="flat"
    )
    rows = _by_description("G5", prototype)
    assert rows[datasets] is None and rows["Runbook exists"] is None
    production = Project(
        root=tmp_path, package="demo", llm=False, rigor="production", kind="data", layout="flat"
    )
    assert "SBOM generated in CI" in _by_description("G5", production)


PINNED = re.compile(r"uses: ([\w./-]+)@([0-9a-f]{40}) # v\d+\.\d+\.\d+$", re.M)


def test_release_template_uses_trusted_publishing_with_pinned_actions() -> None:
    text = _render_template("dot_github/workflows/release.yml.tmpl")
    assert 'tags: ["v*"]' in text and text.index("permissions:\n  contents: read") < text.index(
        "jobs:"
    )
    from rumbo.gates import PYPI_SECRET

    assert "id-token: write" in text and not PYPI_SECRET.search(text)  # no token secret anywhere
    pins = dict(PINNED.findall(text))
    assert pins == {
        "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
        "astral-sh/setup-uv": "68559e99cc807c1eb7e9238fd43a15bba460eec2",
        "pypa/gh-action-pypi-publish": "7245906fb61fa33a13554f9d5fb856fe61758086",
    }
    assert len(re.findall(r"^\s+(?:- )?uses: ", text, re.M)) == len(pins)  # every action is pinned
    assert "uv build" in text and "['project']['version']" in text and "$GITHUB_REF_NAME" in text
    assert "Trusted Publish" in text and "Workflow name:      release.yml" in text


def test_changelog_template_starts_with_an_unreleased_section() -> None:
    text = _render_template("CHANGELOG.md.tmpl", rumbo_version="9.9.9", date="2030-01-02")
    assert text.startswith("# Changelog") and "## [Unreleased]" in text
    assert "RUMBO 9.9.9 on 2030-01-02" in text


@pytest.mark.parametrize(
    ("kind", "versions"), [("library", 3), ("cli", 3), ("service", 0), ("data", 0)]
)
def test_ci_template_runs_a_python_matrix_only_for_packages(kind: str, versions: int) -> None:
    from rumbo.gates import _python_matrix

    flags: dict[str, object] = {kind: True, "packaged": kind in ("library", "cli")}
    text = _render_template("dot_github/workflows/ci.yml.tmpl", **flags)
    assert _python_matrix(text) == versions
    assert ("UV_PYTHON: ${{ matrix.python }}" in text) is (versions > 0)
    assert text.count("runs-on: windows-latest") == 1 and "matrix.os" not in text


# --------------------------------------------------------------------------- ADR-0011
def _checks(root: Path) -> dict[str, str]:
    import tomllib

    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return data["tool"]["rumbo"]["checks"]


def _list_dev_tools(root: Path, tools: list[str]) -> None:
    """What `uv add --dev` would have written had the project been installed."""
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    group = "\n[dependency-groups]\ndev = [" + ", ".join(f'"{t}"' for t in tools) + "]\n"
    if "[dependency-groups]" in text:
        text = text[: text.index("\n[dependency-groups]")]
    pyproject.write_text(text + group, encoding="utf-8")


PROTOTYPE_TOOLS = ["ruff", "pyright", "pytest", "hypothesis", "pre-commit", "import-linter"]


def test_promote_refuses_equal_and_lower_rigor(
    llm_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0011 fitness: promote only raises; lowering is a hand edit, by design."""
    before = (llm_project / "pyproject.toml").read_text(encoding="utf-8")
    assert main(["promote", "mvp", "--dir", str(llm_project), "--no-install"]) == 1
    assert "already at rigor 'mvp'" in capsys.readouterr().err
    assert main(["promote", "prototype", "--dir", str(llm_project), "--no-install"]) == 1
    err = capsys.readouterr().err
    assert "cannot lower the rigor from 'mvp' to 'prototype'" in err and "hand edit" in err
    assert (llm_project / "pyproject.toml").read_text(encoding="utf-8") == before
    assert main(["promote", "mvp", "--dir", str(llm_project / "nowhere")]) == 2  # usage error


def test_promote_prototype_to_mvp_then_production(
    simple_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0011 fitness: rigor written, steps appended, nightly and dependabot rendered, uv line."""
    nightly = simple_project / ".github" / "workflows" / "nightly.yml"
    dependabot = simple_project / ".github" / "dependabot.yml"
    untouched = {p: (simple_project / p).read_bytes() for p in ("AGENTS.md", "Makefile")}
    assert Project.load(simple_project).rigor == "prototype"
    assert not nightly.exists() and not dependabot.exists()
    assert list(_checks(simple_project)) == ["lint", "format", "types", "test", "arch", "sensor"]
    _list_dev_tools(simple_project, PROTOTYPE_TOOLS)

    assert main(["promote", "mvp", "--dir", str(simple_project), "--no-install"]) == 0
    out = capsys.readouterr().out
    assert "Promoting Demo from prototype to mvp" in out
    assert "Run to complete the update:\n  uv add --dev pytest-cov bandit pip-audit" in out
    assert "rumbo gate" in out
    project = Project.load(simple_project)
    assert project.rigor == "mvp" and project.package == "demo"  # the other keys are intact
    assert nightly.exists() and dependabot.exists()
    checks = _checks(simple_project)
    assert list(checks) == [
        "lint",
        "format",
        "types",
        "test",
        "arch",
        "sensor",
        "security",
        "security-scripts",
        "audit",
    ]
    assert checks["test"] == "uv run pytest"  # existing steps are never replaced
    assert checks["audit"] == "uv run pip-audit --skip-editable"

    # mvp → production: no new steps (the table is the same), mutmut is the new tool.
    _list_dev_tools(simple_project, [*PROTOTYPE_TOOLS, "pytest-cov", "bandit", "pip-audit"])
    assert main(["promote", "production", "--dir", str(simple_project), "--no-install"]) == 0
    out = capsys.readouterr().out
    assert "Promoting Demo from mvp to production" in out
    assert "Run to complete the update:\n  uv add --dev mutmut" in out
    assert Project.load(simple_project).rigor == "production"
    assert list(_checks(simple_project)) == list(checks)
    # Promote never touches AGENTS.md or the Makefile (ADR-0011); `update` only reports them.
    assert {p: (simple_project / p).read_bytes() for p in untouched} == untouched
    assert "AGENTS.md" in out  # listed under "Differ from the current template"


def test_promote_runs_uv_add_unless_no_install(
    simple_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path, report: Any) -> int:
        calls.append(cmd)
        report.commands.append((" ".join(cmd), 0))
        return 0

    from rumbo import scaffold as scaffold_module

    monkeypatch.setattr(scaffold_module, "_run", fake_run)
    monkeypatch.setattr(scaffold_module.shutil, "which", lambda name: "/usr/bin/uv")
    _list_dev_tools(simple_project, PROTOTYPE_TOOLS)
    assert main(["promote", "mvp", "--dir", str(simple_project)]) == 0
    assert calls == [["uv", "add", "--dev", "pytest-cov", "bandit", "pip-audit"]]


# ------------------------------------------------------------------ ADR-0013: adoption
LEGACY_PYPROJECT = (
    '[project]\nname = "legacy-app"\nversion = "0.1.0"\ndependencies = []\n\n'
    '[project.scripts]\nlegacy = "legacy_app:main"\n'
)
ADOPT = ["init", "--adopt", "--yes", "--no-llm", "--rigor", "mvp", "--no-install"]


def _legacy_project(root: Path) -> Path:
    """A project that existed before RUMBO: a cli package under src/, tests in test/, its own CI."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(LEGACY_PYPROJECT, encoding="utf-8")
    (root / "src" / "legacy_app").mkdir(parents=True)
    (root / "src" / "legacy_app" / "__init__.py").write_text(
        "def main() -> None:\n    pass\n", encoding="utf-8"
    )
    (root / "test").mkdir()
    (root / "test" / "test_legacy.py").write_text(
        "def test_ok() -> None:\n    assert 1\n", encoding="utf-8"
    )
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "existing.yml").write_text(
        "name: existing\n", encoding="utf-8"
    )
    return root


def test_init_adopt_detects_the_project_and_writes_only_the_harness(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0013 fitness: `rumbo init --adopt --yes` on a pre-existing project."""
    root = _legacy_project(tmp_path)
    assert main([*ADOPT, "--dir", str(root)]) == 0
    out = capsys.readouterr().out
    assert "package=legacy_app kind=cli layout=flat tests=test" in out
    assert "ci.yml was not written" in out
    assert "uv run python scripts/check_tests_not_weakened.py --strict" in out
    project = Project.load(root)
    assert (project.package, project.kind, project.layout) == ("legacy_app", "cli", "flat")
    assert (project.adopted, project.tests_dir, project.rigor) == (True, "test", "mvp")
    # The harness, not the skeleton.
    for rel in (
        "AGENTS.md",
        "RUMBO.md",
        "Makefile",
        "scripts/rumbo_check.py",
        "scripts/rumbo_guard.py",
        "test/architecture/test_architecture.py",
        "test/unit/test_check_tests_not_weakened.py",
        ".github/workflows/nightly.yml",
        "CHANGELOG.md",
    ):
        assert (root / rel).is_file(), rel
    for rel in (
        "src/legacy_app/__main__.py",
        "tests",
        "test/unit/test_smoke.py",
        "test/property",
        ".github/workflows/ci.yml",
    ):
        assert not (root / rel).exists(), rel
    assert (root / "src" / "legacy_app" / "__init__.py").read_text(
        encoding="utf-8"
    ) == "def main() -> None:\n    pass\n"
    checks = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["rumbo"]
    assert checks["adopted"] is True and checks["tests"] == "test"
    assert checks["checks"]["types"] == "uv run pyright {changed}"
    # Doctor uses the adoption plan: nothing under src/ or tests/ is "missing", nor ci.yml.
    (root / "uv.lock").write_text("", encoding="utf-8")
    assert main(["doctor", "--dir", str(root)]) == 0
    assert "Harness complete" in capsys.readouterr().out


def test_init_adopt_flags_win_over_detection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _legacy_project(tmp_path)
    (root / "src" / "other").mkdir()
    (root / "src" / "other" / "__init__.py").write_text("", encoding="utf-8")
    # Two packages (legacy_app by name wins: no ambiguity); --package and --kind still decide.
    assert main([*ADOPT, "--dir", str(root), "--package", "other", "--kind", "library"]) == 0
    assert "package=other kind=library" in capsys.readouterr().out.split("Mounting")[1]
    project = Project.load(root)
    assert (project.package, project.kind, project.adopted) == ("other", "library", True)


def test_init_adopt_ambiguous_package_asks_for_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _legacy_project(tmp_path)
    (root / "pyproject.toml").write_text(
        LEGACY_PYPROJECT.replace("legacy-app", "renamed"), encoding="utf-8"
    )
    (root / "src" / "other").mkdir()
    (root / "src" / "other" / "__init__.py").write_text("", encoding="utf-8")
    assert main([*ADOPT, "--dir", str(root)]) == 2
    err = capsys.readouterr().err
    assert "legacy_app, other" in err and "--package" in err
    assert not (root / "AGENTS.md").exists()
    assert main([*ADOPT, "--dir", str(root), "--package", "other"]) == 0


def test_init_adopt_interactive_defaults_are_the_detected_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0013: in A0 the detected values are the answers in brackets (Enter accepts them)."""
    root = _legacy_project(tmp_path)

    class Tty:
        def isatty(self) -> bool:
            return True

    prompts: list[str] = []

    def accept_default(prompt: str) -> str:
        prompts.append(prompt)
        return ""

    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr("builtins.input", accept_default)
    assert main(["init", "--adopt", "--no-install", "--dir", str(root)]) == 0
    assert any("[legacy-app]" in p for p in prompts)
    assert any("[legacy_app]" in p for p in prompts)
    assert any("[cli]" in p for p in prompts)
    assert not any("already has" in p for p in prompts)  # adoption expects an existing tree
    project = Project.load(root)
    assert (project.package, project.kind, project.tests_dir, project.rigor) == (
        "legacy_app",
        "cli",
        "test",
        "mvp",
    )
    assert project.adopted and not project.llm
    assert "package=legacy_app kind=cli layout=flat tests=test" in capsys.readouterr().out


def test_init_adopt_needs_an_existing_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*ADOPT, "--dir", str(tmp_path / "nowhere")]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_check_full_is_passed_through_to_the_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0013: `rumbo check --full` hands `--full` to the runner, which expands {changed}."""
    captured: list[list[str]] = []

    def fake_main(argv: list[str] | None = None) -> int:
        captured.append(list(argv or []))
        return 0

    monkeypatch.setattr("rumbo.sensors.check.main", fake_main)
    assert main(["check", "--full", "--dir", str(tmp_path)]) == 0
    assert main(["check", "lint", "--brief", "--dir", str(tmp_path)]) == 0
    assert captured == [
        ["--dir", str(tmp_path), "--full"],
        ["lint", "--dir", str(tmp_path), "--brief"],
    ]
    assert cli.build_parser().parse_args(["check", "--full"]).full is True
