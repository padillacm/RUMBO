import json
import os
import subprocess
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
    assert "provider anthropic" in (tmp_path / "docs" / "decisions.md").read_text()


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
    (llm_project / "uv.lock").write_text("")
    assert main(["doctor", "--dir", str(llm_project)]) == 0
    (llm_project / ".claude" / "commands" / "rumbo-tour.md").unlink()
    assert main(["doctor", "--dir", str(llm_project)]) == 1
    assert "rumbo-tour.md" in capsys.readouterr().out


def test_new_numbers_and_titles(simple_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    d = str(simple_project)
    assert main(["new", "adr", "Use DuckDB", "--dir", d]) == 0
    assert main(["new", "adr", r"Workflow vs agent C:\temp \1", "--dir", d]) == 0
    second = simple_project / "docs" / "adr" / "0002-workflow-vs-agent-c-temp-1.md"
    text = second.read_text()
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
    text = (simple_project / "docs" / "decisions.md").read_text()
    assert "Use DuckDB" in text and "Why: time series" in text


def _complete(path: Path) -> None:
    path.write_text("\n".join(path.read_text().splitlines()[1:]) + "\n")


def test_gate_g1(simple_project: Path) -> None:
    project = Project.load(simple_project)
    _, results = evaluate("G1", project)
    assert any(r.ok is False for r in results)
    _complete(simple_project / "docs" / "product" / "brief.md")
    _, results = evaluate("G1", project)
    assert all(r.ok for r in results)
    (simple_project / "src" / "demo" / "bad.py").write_text("from spikes import proto\n")
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
        spec.read_text().replace(
            "- R1 (event): When …, the system shall …",
            "- R1: When the user asks, the system shall answer with the source.",
        )
    )
    _, results = evaluate("G2", Project.load(llm_project))
    assert next(r for r in results if r.description.endswith("EARS requirements")).ok is True


def test_gate_g3_workflow_adr_by_title(llm_project: Path) -> None:
    d = str(llm_project)
    assert main(["new", "adr", "CI pipeline", "--dir", d]) == 0
    (llm_project / "docs" / "adr" / "0001-ci-pipeline.md").write_text(
        "# ADR-0001: CI pipeline\nThe workflow is kept by the agent.\n## Fitness function\nx\n"
    )
    _, results = evaluate("G3", Project.load(llm_project))
    assert next(r for r in results if r.description == "ADR 'workflow vs. agent'").ok is False
    assert main(["new", "adr", "Workflow, not agent", "--dir", d]) == 0
    _, results = evaluate("G3", Project.load(llm_project))
    assert next(r for r in results if r.description == "ADR 'workflow vs. agent'").ok is True
    assert next(r for r in results if r.description == "import-linter contracts").ok is True


def test_gate_g2_skips_malformed_dataset_lines(llm_project: Path) -> None:
    dataset = llm_project / "evals" / "datasets" / "odd.jsonl"
    dataset.write_text('["a", "b"]\n"text"\n42\nnot json\n{"id": "odd-1", "input": {}}\n')
    try:
        _, results = evaluate("G2", Project.load(llm_project))
    finally:
        dataset.unlink()
    cases = next(r for r in results if "real cases in evals/datasets/" in r.description)
    assert "found 1 " in cases.detail  # only the object line counts; nothing crashed


def test_gate_g3_tolerates_empty_or_comment_only_adrs(llm_project: Path) -> None:
    adrs = llm_project / "docs" / "adr"
    (adrs / "0007-empty.md").write_text("")
    (adrs / "0008-stub.md").write_text("<!-- a one-line comment -->")
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

    monkeypatch.setattr(gates.subprocess, "run", fake_run)
    _, results = evaluate("G4", Project.load(simple_project), run=True)
    check = next(r for r in results if r.description == "make check green")
    assert check.ok is None and "not installed" in check.detail


def test_diff_size_is_unmeasured_when_head_is_the_base(tmp_path: Path) -> None:
    from rumbo.gates import diff_size

    env = {**os.environ, "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig")}
    (tmp_path / "gitconfig").write_text("[user]\n\tname = t\n\temail = t@x\n")

    def git(cwd: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)

    origin, clone = tmp_path / "origin", tmp_path / "clone"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "a.py").write_text("x = 1\n" * 500)
    git(origin, "add", ".")
    git(origin, "commit", "-q", "-m", "big")
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    # origin/main is HEAD: a 500-line commit on main must not read as a 0-line diff.
    size, info = diff_size(clone)
    assert size is None and "HEAD is already on 'origin/main'" in info
    # On a branch with one commit the diff is measured against origin/main.
    git(clone, "checkout", "-q", "-b", "feature")
    (clone / "b.py").write_text("y = 2\n" * 3)
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
    (tmp_path / "pyproject.toml").write_text("[tool.rumbo]\nllm = false\n")
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
        "def avg(xs):\n    return sum(xs) / len(xs)\n"
    )
    (simple_project / "tests" / "unit" / "test_calc.py").write_text(
        "from demo.domain.calc import avg\n\ndef test_avg():\n    assert avg([2, 4]) == 3\n"
    )
    (simple_project / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")
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
        spec.read_text().replace(
            "- Types / schemas: <name, fields, invariants>",
            "- Types / schemas: Quote(price: Decimal, at: datetime)",
        )
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
        spec.read_text().replace(
            "- R1 (event): When …, the system shall …\n"
            "- R2 (unwanted): If …, then the system shall …",
            "- R1 (event): When a date is given, the system shall return the spot price\n"
            "- R2 (unwanted): If the date has no data, then the system shall abstain",
        )
    )
    test = llm_project / "tests" / "unit" / "test_spot.py"
    test.write_text('def test_spot_price() -> None:\n    """F-0001.R1: price for a date."""\n')
    assert main(["trace", "--dir", str(llm_project)]) == 0
    out = capsys.readouterr().out
    assert "| F-0001.R1 |" in out and "tests/unit/test_spot.py:2" in out
    assert "| F-0001.R2 |" in out and "**missing**" in out and "1/2 requirements" in out
    assert main(["trace", "--dir", str(llm_project), "--strict"]) == 1
    capsys.readouterr()
    # F-0001.R10 must not count as a reference to R1
    test.write_text('def test_other() -> None:\n    """F-0001.R10 and F-0001.R2."""\n')
    main(["trace", "--dir", str(llm_project)])
    out = capsys.readouterr().out
    assert "| F-0001.R1 | When a date is given, the system shall return the spot price |" in out
    assert "| F-0001.R2 |" in out and out.count("**missing**") == 1

    row = "every spec requirement is referenced by a test or eval case"
    _, results = evaluate("G4", Project.load(llm_project), run=False)
    assert next(r for r in results if r.description == row).ok is None  # mvp: recommendation
    pyproject = llm_project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('rigor = "mvp"', 'rigor = "production"'))
    _, results = evaluate("G4", Project.load(llm_project), run=False)
    assert next(r for r in results if r.description == row).ok is False
    pyproject.write_text(pyproject.read_text().replace('rigor = "production"', 'rigor = "mvp"'))
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
    original = entry.read_text()
    entry.write_text(original.replace("Meter(", "object("))
    _, results = evaluate("G5", Project.load(llm_project))
    assert next(r for r in results if r.description == row).ok is False  # mvp: required
    entry.write_text(original)


def test_eval_add_appends_a_case_and_logs_it(
    llm_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["eval", "add", "regressions", "--dir", str(llm_project), "--grader", "abstention"]
    rc = main([*args, "--input", '{"question": "x"}', "--expected", "{}", "--source", "trace 1"])
    assert rc == 0
    dataset = llm_project / "evals" / "datasets" / "regressions.jsonl"
    case = json.loads(dataset.read_text().splitlines()[0])
    assert case["id"] == "regressions-001" and case["source"] == "trace 1"
    assert "regressions-001" in (llm_project / "evals" / "error_analysis" / "log.md").read_text()
    # unknown grader, invalid JSON and duplicate ids are usage errors
    assert main([*args[:-2], "--grader", "nope", "--input", "{}", "--expected", "{}"]) == 2
    assert "registered" in capsys.readouterr().err
    assert main([*args, "--input", "not json", "--expected", "{}"]) == 2
    assert main([*args, "--input", "{}", "--expected", "{}", "--id", "regressions-001"]) == 2
    assert len(dataset.read_text().splitlines()) == 1
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
    decisions = parse_decisions((simple_project / "docs" / "decisions.md").read_text())
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
    original = makefile.read_text()
    makefile.write_text(original + "extra: ; echo hi\n")
    brief = llm_project / "docs" / "product" / "brief.md"
    brief_original = brief.read_text()
    brief.write_text("# mine\n")
    assert main(["update", "--diff", "--dir", str(llm_project)]) == 0
    out = capsys.readouterr().out
    assert "### Makefile [merge by hand]" in out and "-extra: ; echo hi" in out
    assert "brief.md" not in out  # human documents are never compared
    assert makefile.read_text().endswith("extra: ; echo hi\n")  # nothing written
    makefile.write_text(original)
    brief.write_text(brief_original)
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
    original = graders_file.read_text()
    assert cli.known_graders(llm_project) == {
        "field_equality",
        "numeric",
        "tool_arguments",
        "abstention",
    }
    graders_file.write_text('GRADERS: dict[str, object] = {"abstention": object}\n')
    assert cli.known_graders(llm_project) == {"abstention"}
    graders_file.write_text("GRADERS = build()\n")
    assert cli.known_graders(llm_project) is None
    graders_file.write_text(original)


def test_eval_add_escapes_the_log_table(llm_project: Path) -> None:
    args = ["eval", "add", "pipes", "--dir", str(llm_project), "--grader", "abstention"]
    rc = main([*args, "--input", "{}", "--expected", "{}", "--note", "a|b\nc", "--source", "x|y"])
    assert rc == 0
    log = (llm_project / "evals" / "error_analysis" / "log.md").read_text()
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
        "## Acceptance criteria\n"
    )
    (simple_project / "tests" / "unit" / "test_edge.py").write_text(
        'def test_x() -> None:\n    """F-0001.R10a is not R10; F-0001.R1. ends a sentence"""\n'
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
    original = tm.read_text()
    tm.write_text("\n".join(original.splitlines()[1:]) + "\n")  # human removes the mark
    _, results = evaluate("G3", Project.load(llm_project))
    by_desc = {r.description: r for r in results}
    assert by_desc["threat model: lethal trifecta assessed"].ok is True
    tm.write_text(original)


def test_no_arguments_prints_help_and_a_hint(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    out = capsys.readouterr().out
    assert "rumbo init" in out and "rumbo doctor" in out


def test_doctor_warns_about_placeholders(
    simple_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (simple_project / "uv.lock").write_text("")
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
    (tmp_path / "a.txt").write_text("a")
    git("add", "a.txt")
    git("commit", "-qm", "chore: a")
    git("checkout", "-qb", "feature")
    (tmp_path / "b.txt").write_text("b")
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
