"""Fitness functions of the template findings of the second audit (P1, P3..P14, S12).

ADR-0030 (graders receive the case and may be asynchronous) and ADR-0031 (the offline guard
has a production switch) name tests of this file. Every configuration of `CONFIGS` is rendered
without `uv`: the files are parsed, the properties of each fix are asserted on the rendered text,
and the modules that changed are executed (the adapter against a stand-in `claude` executable,
the eval runner as a subprocess, the generated project's own unit tests with pytest).
"""

from __future__ import annotations

import asyncio
import functools
import importlib
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from rumbo.scaffold import RIGOR_FILES, Config, dev_dependencies, plan_files

PACKAGE = "tplpkg"


def _cfg(**overrides: Any) -> Config:
    return Config(**{"name": "Demo", "package": PACKAGE, "llm": False, **overrides})


CONFIGS: dict[str, Config] = {
    "service-proto": _cfg(rigor="prototype"),
    "service-mvp": _cfg(rigor="mvp"),
    "service-flat-mvp": _cfg(rigor="mvp", layout="flat"),
    "service-llm-anthropic-prod": _cfg(llm=True, provider="anthropic", rigor="production"),
    "service-llm-claude-code-mvp-mypy": _cfg(
        llm=True, provider="claude-code", rigor="mvp", types="mypy"
    ),
    "library-hex-prod": _cfg(kind="library", layout="hexagonal", rigor="production"),
    "library-flat-mvp": _cfg(kind="library", rigor="mvp"),
    "cli-llm-claude-code-prod": _cfg(
        kind="cli", llm=True, provider="claude-code", rigor="production"
    ),
    "cli-proto": _cfg(kind="cli", rigor="prototype"),
    "data-mvp": _cfg(kind="data", rigor="mvp"),
    "data-llm-prod-mypy": _cfg(
        kind="data", llm=True, provider="anthropic", rigor="production", types="mypy"
    ),
    "adopted-cli-root-mvp": _cfg(kind="cli", rigor="mvp", adopted=True, tests_dir="test", src="."),
    "adopted-service-llm-prod": _cfg(
        llm=True, provider="anthropic", rigor="production", adopted=True
    ),
    "adopted-service-flat-proto": _cfg(rigor="prototype", layout="flat", adopted=True),
}
LLM = CONFIGS["service-llm-anthropic-prod"]
CLAUDE_CODE = CONFIGS["service-llm-claude-code-mvp-mypy"]
DATA = CONFIGS["data-mvp"]
every_config = pytest.mark.parametrize("name", list(CONFIGS))


@functools.cache
def _plan(name: str) -> dict[str, str]:
    return {
        rel: item if isinstance(item, str) else item.read_text(encoding="utf-8")
        for rel, item in plan_files(CONFIGS[name]).items()
    }


def _write(cfg: Config, root: Path, only: str = "") -> Path:
    for rel, item in plan_files(cfg).items():
        if not rel.startswith(only):
            continue
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        text = item if isinstance(item, str) else item.read_text(encoding="utf-8")
        target.write_text(text, encoding="utf-8", newline="\n")
    return root


def _jobs(workflow: str) -> dict[str, str]:
    """The jobs of a rendered workflow, by name (there is no YAML parser in the stdlib)."""
    jobs: dict[str, str] = {}
    name = ""
    for line in workflow.split("\njobs:\n", 1)[1].splitlines():
        match = re.match(r"^  ([\w-]+):\s*$", line)
        if match:
            name = match.group(1)
            jobs[name] = ""
        elif name:
            jobs[name] += line + "\n"
    return jobs


def _checks(name: str) -> dict[str, str]:
    return tomllib.loads(_plan(name)["pyproject.toml"])["tool"]["rumbo"]["checks"]


# What the sensor step and the hygiene step of an adopted project compare with.
BASE_REF = "${{ github.event.pull_request.base.sha || github.event.before }}"


def _targets(makefile: str) -> dict[str, str]:
    found = (re.match(r"^([A-Za-z][\w-]*):\s*;\s*(.*)$", line) for line in makefile.splitlines())
    return {match.group(1): match.group(2) for match in found if match}


# --- every rendered file is well formed ------------------------------------------------------


@every_config
def test_every_rendered_file_parses(name: str, tmp_path: Path) -> None:
    root = _write(CONFIGS[name], tmp_path)
    files = sorted(p for p in root.rglob("*") if p.is_file())
    assert len(files) > 20
    for file in files:
        rel = file.relative_to(root).as_posix()
        text = file.read_text(encoding="utf-8")
        assert "{{" not in text.replace("${{", "") or rel.startswith("scripts/"), rel
        if file.suffix == ".py":
            compile(text, rel, "exec")
        elif file.suffix == ".toml":
            tomllib.loads(text)
        elif file.suffix == ".json":
            json.loads(text)
        elif file.suffix == ".jsonl":
            assert all(isinstance(json.loads(ln), dict) for ln in text.splitlines() if ln), rel
        elif file.suffix in (".yml", ".yaml"):
            assert "\t" not in text, rel
            assert not any(ln.lstrip().startswith(":") for ln in text.splitlines()), rel
            assert not any(ln != ln.rstrip() for ln in text.splitlines()), rel
            assert text.endswith("\n") and not text.endswith("\n\n"), rel
    for workflow in sorted((root / ".github" / "workflows").glob("*.yml")):
        jobs = _jobs(workflow.read_text(encoding="utf-8"))
        assert jobs, workflow.name
        for job, body in jobs.items():
            assert "    runs-on: " in body and "    steps:\n" in body, (workflow.name, job)
            for uses in re.findall(r"uses: (\S+)", body):
                assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", uses), (workflow.name, uses)


# --- S12, P6, P8: the CI workflow -------------------------------------------------------------


@every_config
def test_ci_reruns_when_the_approval_label_changes(name: str) -> None:
    """S12: `labeled` and `unlabeled` re-run the sensor; it still receives the base."""
    ci = _plan(name)[".github/workflows/ci.yml"]
    trigger = ci.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    lines = [ln for ln in trigger.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert lines == [
        "  push:",
        "  pull_request:",
        "    types: [opened, synchronize, reopened, labeled, unlabeled]",
    ]
    check = _jobs(ci)["check"]
    assert (
        f"          RUMBO_BASE_REF: {BASE_REF}\n"
        "        run: uv run python scripts/check_tests_not_weakened.py --strict\n"
    ) in check


@every_config
def test_sbom_artifact_has_one_name_per_matrix_leg(name: str) -> None:
    """P6: two legs of the Python matrix never upload an artifact with the same name."""
    cfg = CONFIGS[name]
    ci = _plan(name)[".github/workflows/ci.yml"]
    names = [ln.strip() for ln in ci.splitlines() if ln.strip().startswith("name: sbom")]
    if cfg.rigor != "production":
        assert names == [] and "sbom" not in ci.lower()
    elif cfg.kind in ("library", "cli"):
        assert "        python: " in ci  # the matrix exists
        assert names == ["name: sbom-py${{ matrix.python }}"]
    else:
        assert "matrix.python" not in ci and "strategy:" not in ci
        assert names == ["name: sbom"]
    assert ("sbom" in ci.lower()) is (cfg.rigor == "production")  # what gate G3 looks for


@every_config
def test_adopted_ci_runs_pre_commit_on_the_changed_files_only(name: str) -> None:
    """P8: the hygiene step of an adopted project does not run ruff over the legacy tree."""
    cfg = CONFIGS[name]
    check = _jobs(_plan(name)[".github/workflows/ci.yml"])["check"]
    runs = [ln.strip() for ln in check.splitlines() if "pre-commit run" in ln]
    if cfg.rigor == "prototype":
        assert runs == []
    elif cfg.adopted:
        assert runs == [
            'uv run pre-commit run --from-ref "$base" --to-ref HEAD --show-diff-on-failure'
        ]
        assert "--all-files" not in check
        hygiene = check.split("Hygiene (pre-commit, changed files)", 1)[1]
        assert f"RUMBO_BASE_REF: {BASE_REF}" in hygiene  # the same base as the sensor step
        candidates = '"$RUMBO_BASE_REF" origin/main origin/master origin/HEAD main master'
        assert f"for ref in {candidates}; do" in hygiene  # then the runner's {base} candidates
    else:
        assert runs == ["run: uv run pre-commit run --all-files --show-diff-on-failure"]


# --- P7: the nightly ---------------------------------------------------------------------------


@every_config
def test_nightly_runs_mutation_only_where_mutmut_is_installed(name: str) -> None:
    """P7: no `mutation` job below production; the nightly always has a job to run."""
    cfg = CONFIGS[name]
    nightly = _plan(name).get(".github/workflows/nightly.yml")
    if cfg.rigor == "prototype":
        assert nightly is None
        return
    assert nightly is not None
    jobs = _jobs(nightly)
    mutates = cfg.arch and cfg.rigor == "production"
    assert ("mutation" in jobs) is mutates
    assert ("mutmut" in dev_dependencies(cfg)) or "make mutation" not in nightly
    assert ("make mutation" in nightly) is mutates
    assert "run: uv run python scripts/rumbo_check.py audit" in jobs["audit"]
    assert "audit" in _checks(name)  # the step the job runs exists at this rigor
    expected = ["audit"]
    expected += ["mutation"] if mutates else []
    expected += ["full-check"] if cfg.adopted else []
    expected += ["evals-live"] if cfg.llm else []
    assert list(jobs) == expected


# --- P11: dependabot ---------------------------------------------------------------------------


@every_config
def test_dependabot_lists_docker_only_when_the_project_has_infra(name: str) -> None:
    """P11: the docker ecosystem at /infra exists for service and data, not for library or cli."""
    cfg = CONFIGS[name]
    plan = _plan(name)
    assert ".github/dependabot.yml" in RIGOR_FILES  # `rumbo update` still renders it by this path
    assert ".github/dependabot.yml.tmpl" not in plan
    if cfg.rigor == "prototype":
        assert ".github/dependabot.yml" not in plan
        return
    dependabot = plan[".github/dependabot.yml"]
    ecosystems = re.findall(r"^  - package-ecosystem: (\S+)$", dependabot, re.M)
    has_infra = any(rel.startswith("infra/") for rel in plan)
    assert has_infra is (cfg.kind in ("service", "data"))
    assert ecosystems == ["uv", "github-actions", *(["docker"] if has_infra else [])]
    assert ("directory: /infra" in dependabot) is has_infra
    assert dependabot.count("default-days: 7") == len(ecosystems)


# --- P3, P5, P14: pyproject.toml ----------------------------------------------------------------


@every_config
def test_security_step_reads_tool_bandit(name: str) -> None:
    """P3: without `-c pyproject.toml` bandit never reads `[tool.bandit]`."""
    cfg = CONFIGS[name]
    pyproject = tomllib.loads(_plan(name)["pyproject.toml"])
    assert pyproject["tool"]["bandit"] == {"skips": ["B101"]}
    checks = pyproject["tool"]["rumbo"]["checks"]
    if cfg.rigor == "prototype":
        assert "security" not in checks
        return
    scanned = f"{cfg.code_root} evals" if cfg.evals else cfg.code_root
    assert checks["security"] == f"uv run bandit -c pyproject.toml -q -r {scanned}"
    assert checks["security-scripts"] == "uv run bandit -q -r scripts -s B404,B603,B607"


@pytest.mark.skipif(importlib.util.find_spec("bandit") is None, reason="bandit is not installed")
def test_rendered_code_passes_bandit_with_the_rendered_configuration(tmp_path: Path) -> None:
    """P3: the template's own code is green, and an assert in evals/ is allowed by the table."""
    root = _write(CLAUDE_CODE, tmp_path)
    graders = root / "evals" / "graders" / "__init__.py"
    graders.write_text(
        graders.read_text(encoding="utf-8") + "\nassert GRADERS, 'the registry is empty'\n",
        encoding="utf-8",
    )
    step = _checks("service-llm-claude-code-mvp-mypy")["security"].split()
    assert step[:3] == ["uv", "run", "bandit"]
    command = [sys.executable, "-m", "bandit", *step[3:]]
    configured = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=120)
    assert configured.returncode == 0, configured.stdout + configured.stderr
    command.remove("-c")
    command.remove("pyproject.toml")
    unconfigured = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=120)
    assert unconfigured.returncode == 1 and "B101" in unconfigured.stdout  # the table mattered


@every_config
def test_packaged_kinds_limit_the_sdist(name: str) -> None:
    """P5: the published sdist carries the package and its tests, not specs, docs or CI."""
    cfg = CONFIGS[name]
    targets = tomllib.loads(_plan(name)["pyproject.toml"])["tool"]["hatch"]["build"]["targets"]
    assert targets["wheel"] == {"packages": [cfg.package_path]}
    if cfg.kind in ("library", "cli"):
        assert targets["sdist"] == {"only-include": [cfg.code_root, cfg.tests_dir]}
    else:
        assert "sdist" not in targets


@every_config
def test_type_checker_excludes_spikes_and_the_sensors_for_an_explicit_path(name: str) -> None:
    """P14: `rumbo check --full` passes `.`, which replaces `include`/`files`; the exclusions
    are what still holds: throwaway code under spikes/ (ADR-0020) and the sensors RUMBO copies
    into scripts/, which the human cannot edit, are never type-checked in the project."""
    cfg = CONFIGS[name]
    plan = _plan(name)
    tool = tomllib.loads(plan["pyproject.toml"])["tool"]
    sensors = sorted(rel for rel in plan if rel.startswith("scripts/") and rel.endswith(".py"))
    assert len(sensors) == 3
    if cfg.types == "pyright":
        assert tool["pyright"]["include"] == [cfg.code_root, cfg.tests_dir]
        defaults = ["**/node_modules", "**/__pycache__", "**/.*"]  # pyright's own
        assert tool["pyright"]["exclude"][:4] == [*defaults, "spikes"]
        assert sorted(tool["pyright"]["exclude"][4:]) == sensors
    else:
        assert tool["mypy"]["files"] == [cfg.code_root, cfg.tests_dir]
        spikes, scripts = tool["mypy"]["exclude"]
        assert re.search(spikes, "spikes/try_it.py")
        assert not re.search(spikes, "src/spikes_report.py")
        assert all(re.search(scripts, sensor) for sensor in sensors)
        assert not re.search(scripts, "scripts/deploy.py")  # the project's own scripts count
    assert tool["ruff"]["extend-exclude"] == ["spikes"] and tool["ruff"]["force-exclude"] is True
    comment = "# the first that exists of origin/main, origin/master, origin/HEAD, main and master."
    assert comment in plan["pyproject.toml"]  # what the runner's {base} does today
    assert "origin/main if it exists, else main" not in plan["pyproject.toml"]


# --- P12: the Makefile, the README and .env.example agree ---------------------------------------


@every_config
def test_makefile_targets_exist_only_where_their_steps_do(name: str) -> None:
    """P12: `make security` is not offered at prototype, where its three steps do not exist."""
    cfg = CONFIGS[name]
    targets = _targets(_plan(name)["Makefile"])
    checks = _checks(name)
    assert ("security" in targets) is (cfg.rigor != "prototype")
    runner = "uv run python scripts/rumbo_check.py"
    for target, recipe in targets.items():
        if recipe.startswith(runner) and target != "check-brief":
            steps = recipe.removeprefix(runner).split()
            assert all(step in checks for step in steps), (target, steps)
    assert ("evals-live" in targets) is cfg.llm
    assert ("evals" in targets) is cfg.evals


@pytest.mark.parametrize("name", [n for n, c in CONFIGS.items() if c.llm])
def test_evals_live_loads_dot_env_and_the_documents_say_so(name: str) -> None:
    """P12: the README tells the human to create `.env`; `make evals-live` is what reads it."""
    cfg = CONFIGS[name]
    plan = _plan(name)
    k = "3" if cfg.rigor == "production" else "1"
    assert _targets(plan["Makefile"])["evals-live"] == (
        "uv run $(if $(wildcard .env),--env-file .env) python evals/run_evals.py "
        f"--suite all --live --k {k}"
    )
    readme = plan["README.md"]
    assert "copy `.env.example` to `.env`" in readme and "`make evals-live` loads it" in readme
    assert "uv run --env-file .env python evals/run_evals.py --suite all --live" in readme
    example = plan[".env.example"]
    assert "`make evals-live` loads it" in example and "uv run --env-file .env" in example
    assert (f"\n{cfg.sdk_env_key}=\n" in example) is (cfg.provider != "claude-code")
    # The nightly has no .env: the credential comes from the protected environment.
    nightly = plan[".github/workflows/nightly.yml"]
    assert f"run: uv run python evals/run_evals.py --suite all --live --k {k}\n" in nightly
    assert "--env-file" not in nightly


# --- P4 (ADR-0031): the offline guard has a production switch -----------------------------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Config], Path]]:
    """Renders a configuration and makes its package and its `graders` importable."""

    def build(cfg: Config) -> Path:
        root = _write(cfg, tmp_path / "project")
        monkeypatch.syspath_prepend(str(root / "src"))
        monkeypatch.syspath_prepend(str(root / "evals"))
        return root

    for variable in ("RUMBO_LIVE", "RUMBO_EVALS_LIVE"):
        monkeypatch.delenv(variable, raising=False)
    yield build
    for module in [m for m in sys.modules if m.split(".")[0] in (PACKAGE, "graders")]:
        del sys.modules[module]


def _module(dotted: str) -> ModuleType:
    return importlib.import_module(dotted)


def test_model_is_unreachable_without_a_live_variable(
    project: Callable[[Config], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0031 fitness: offline is the default; either variable set to exactly 1 opens it."""
    project(LLM)
    ports = _module(f"{PACKAGE}.app.ports")
    assert ports.live() is False
    with pytest.raises(ports.OfflineError) as offline:
        ports.require_live("the model")
    assert "RUMBO_LIVE=1" in str(offline.value) and "RUMBO_EVALS_LIVE=1" in str(offline.value)
    for value in ("", "0", "true", "yes", " 1"):
        monkeypatch.setenv("RUMBO_LIVE", value)
        assert ports.live() is False, value
    monkeypatch.setenv("RUMBO_LIVE", "1")
    assert ports.live() is True
    ports.require_live()
    monkeypatch.delenv("RUMBO_LIVE")
    monkeypatch.setenv("RUMBO_EVALS_LIVE", "1")
    assert ports.live() is True
    monkeypatch.delenv("RUMBO_EVALS_LIVE")
    assert ports.live() is False
    # The skeleton entry point abstains offline and the stub adapter refuses to be called.
    entrypoint = _module(f"{PACKAGE}.app.entrypoint")
    answer = asyncio.run(entrypoint.answer({"question": "anything"}))
    assert answer["abstain"] is True and answer["reason"].startswith("offline")
    complete = _module(f"{PACKAGE}.adapters.llm").default_completion()
    with pytest.raises(ports.OfflineError):
        asyncio.run(complete("hello"))


@every_config
def test_production_switch_is_set_where_the_product_is_deployed_and_documented(name: str) -> None:
    """ADR-0031 fitness: the image sets RUMBO_LIVE=1; the check and CI never do."""
    cfg = CONFIGS[name]
    plan = _plan(name)
    dockerfile = plan.get("infra/Dockerfile")
    if dockerfile is not None:
        assert ("\nENV RUMBO_LIVE=1\n" in dockerfile) is cfg.llm
        assert "RUMBO_EVALS_LIVE" not in dockerfile
    for workflow in ("ci.yml", "nightly.yml"):
        assert "RUMBO_LIVE" not in plan.get(f".github/workflows/{workflow}", "")
    assert "RUMBO_LIVE" not in plan["pyproject.toml"] and "RUMBO_LIVE" not in plan["Makefile"]
    if not cfg.llm:  # only the runner of a data pipeline names it: to remove it
        # RUMBO.md is the method manual, the same text for every project: it explains the switch.
        named = [rel for rel, text in plan.items() if "RUMBO_LIVE" in text and rel != "RUMBO.md"]
        assert named == (["evals/run_evals.py"] if cfg.evals else [])
        return
    assert (
        "\nRUMBO_LIVE=\n" in plan[".env.example"]
        and "\nRUMBO_EVALS_LIVE=\n" in (plan[".env.example"])
    )
    assert "`RUMBO_LIVE=1`" in plan["README.md"]
    assert "`RUMBO_LIVE=1`" in plan["AGENTS.md"]
    if "docs/ops/runbook.md" in plan:
        assert "RUMBO_LIVE=1" in plan["docs/ops/runbook.md"]
    if not cfg.adopted:
        ports = plan[f"src/{PACKAGE}/app/ports.py"]
        assert 'LIVE_VARIABLES = ("RUMBO_LIVE", "RUMBO_EVALS_LIVE")' in ports


# --- the eval runner, executed -------------------------------------------------------------------

ENTRIES = '''
"""Entry points for the rendered eval runner."""

import os
from typing import Any


def sync_entry(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("question") == "ping":
        return {"answer": "pong"}
    return {"abstain": True, "reason": "no data"}


def echo_entry(request: dict[str, Any]) -> dict[str, Any]:
    return {"answer": "pong", "echo": request.get("question")}


def live_probe(request: dict[str, Any]) -> dict[str, Any]:
    names = [n for n in ("RUMBO_LIVE", "RUMBO_EVALS_LIVE") if os.environ.get(n)]
    return {"answer": "pong", "live": names}


def boom(request: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("down")
'''

EXTRA_GRADERS = """

def echoes_input(
    output: dict[str, Any], expected: dict[str, Any], case_input: dict[str, Any]
) -> Verdict:
    ok = output.get("echo") == case_input["question"]
    return Verdict(ok, "" if ok else f"the case input did not arrive: {case_input!r}")


async def slow_equality(output: dict[str, Any], expected: dict[str, Any]) -> Verdict:
    import asyncio

    await asyncio.sleep(0)
    return field_equality(output, expected)


GRADERS["echoes_input"] = echoes_input
GRADERS["slow_equality"] = slow_equality
"""


def _evals(cfg: Config, root: Path) -> Path:
    _write(cfg, root, only="evals/")
    (root / "evals" / "entries.py").write_text(ENTRIES, encoding="utf-8")
    return root / "evals" / "run_evals.py"


def _run(runner: Path, *args: str, env: dict[str, str] | None = None) -> Any:
    return subprocess.run(
        [sys.executable, "-I", str(runner), *args],
        capture_output=True,
        text=True,
        cwd=runner.parent.parent,
        timeout=120,
        env={**os.environ, **(env or {})},
    )


def _reports(root: Path) -> list[Path]:
    return sorted((root / "evals" / "reports").glob("*.json"))


def test_runner_passes_the_case_input_and_awaits_asynchronous_graders(tmp_path: Path) -> None:
    """ADR-0030 fitness: two-argument graders unchanged, three-argument and coroutine ones run."""
    runner = _evals(DATA, tmp_path)
    graders = tmp_path / "evals" / "graders" / "__init__.py"
    graders.write_text(graders.read_text(encoding="utf-8") + EXTRA_GRADERS, encoding="utf-8")
    cases = [
        {"id": "two-args", "grader": "field_equality", "expected": {"answer": "pong"}},
        {"id": "with-input", "grader": "echoes_input", "expected": {}},
        {"id": "coroutine", "grader": "slow_equality", "expected": {"answer": "pong"}},
        {"id": "coroutine-fails", "grader": "slow_equality", "expected": {"answer": "no"}},
    ]
    lines = [json.dumps({**case, "input": {"question": f"q-{case['id']}"}}) for case in cases]
    (tmp_path / "evals" / "datasets" / "adr30.jsonl").write_text("\n".join(lines) + "\n", "utf-8")
    result = _run(runner, "--suite", "adr30", "--entry", "entries:echo_entry", "--min-pass", "0.7")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[:4] == [
        "PASS two-args (1/1) ",
        "PASS with-input (1/1) ",
        "PASS coroutine (1/1) ",
        "FAIL coroutine-fails (0/1) different fields: ['answer']",
    ]
    assert "cases=4 k=1  pass@1=0.750  pass^1=0.750" in result.stdout


def test_runner_refuses_k_below_one_and_names_the_bad_dataset_line(tmp_path: Path) -> None:
    """P13: `--k 0` divided by zero; a malformed line gave a traceback without the file."""
    runner = _evals(DATA, tmp_path)
    for k in ("0", "-2"):
        refused = _run(runner, "--suite", "smoke", "--k", k)
        assert refused.returncode == 2 and "--k must be >= 1" in refused.stderr
        assert "Traceback" not in refused.stderr
    bad = tmp_path / "evals" / "datasets" / "bad.jsonl"
    good = '{"id": "x", "input": {}, "expected": {}, "grader": "abstention"}\n'
    for content, where, what in (
        (good + "{not json\n", "bad.jsonl:2", "not valid JSON"),
        ('["a list"]\n', "bad.jsonl:1", "JSON object"),
        (good + '{"id": "y"}\n', "bad.jsonl:2", "missing fields"),
    ):
        bad.write_text(content, encoding="utf-8")
        result = _run(runner, "--suite", "bad", "--entry", "entries:sync_entry")
        assert result.returncode == 1, content
        assert where in result.stderr and what in result.stderr, result.stderr
        assert "Traceback" not in result.stderr
    bad.unlink()
    missing = _run(runner, "--suite", "nosuch", "--entry", "entries:sync_entry")
    assert missing.returncode == 1 and "nosuch.jsonl" in missing.stderr
    assert "Traceback" not in missing.stderr
    assert _reports(tmp_path) == []  # nothing ran, nothing was reported


def test_runs_started_together_write_one_report_each(tmp_path: Path) -> None:
    """P13: report names carried the second only, so concurrent runs overwrote each other."""
    runner = _evals(DATA, tmp_path)
    command = [sys.executable, "-I", str(runner), "--suite", "smoke", "--entry"]
    procs = [
        subprocess.Popen(
            [*command, "entries:sync_entry"],
            cwd=tmp_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(4)
    ]
    assert [proc.wait(timeout=120) for proc in procs] == [0, 0, 0, 0]
    reports = _reports(tmp_path)
    assert len(reports) == 4
    assert all(re.fullmatch(r"smoke-\d{8}-\d{6}-\d{9}-\d+\.json", r.name) for r in reports)
    assert all(json.loads(r.read_text(encoding="utf-8"))["suite"] == "smoke" for r in reports)
    # Sequential runs still sort by time: the newest report is the last name.
    assert _run(runner, "--suite", "smoke", "--entry", "entries:echo_entry").returncode == 1
    assert _reports(tmp_path)[-1] not in reports


def test_fingerprint_is_recorded_only_by_a_full_live_run_that_passed(tmp_path: Path) -> None:
    """P13: a failing or filtered `--live` run no longer claims the fixtures were re-recorded."""
    runner = _evals(DATA, tmp_path)
    record = tmp_path / "evals" / "fingerprint.json"
    failing = _run(runner, "--suite", "smoke", "--live", "--entry", "entries:boom")
    assert failing.returncode == 1 and not record.exists()
    assert "fingerprint not recorded" in failing.stdout
    lenient = _run(
        runner, "--suite", "smoke", "--live", "--entry", "entries:sync_entry", "--min-pass", "1.5"
    )
    assert lenient.returncode == 1 and not record.exists()
    filtered = _run(
        runner, "--suite", "smoke", "--live", "--entry", "entries:sync_entry", "--tag", "example"
    )
    assert filtered.returncode == 0 and not record.exists()
    assert "fingerprint not recorded" in filtered.stdout
    full = _run(runner, "--suite", "smoke", "--live", "--entry", "entries:sync_entry")
    assert full.returncode == 0 and record.is_file()
    assert "fingerprint not recorded" not in full.stdout
    recorded = record.read_text(encoding="utf-8")
    # A later failing live run with another entry leaves the record as it was.
    assert _run(runner, "--suite", "smoke", "--live", "--entry", "entries:boom").returncode == 1
    assert record.read_text(encoding="utf-8") == recorded


def test_offline_run_ignores_live_variables_of_the_environment(tmp_path: Path) -> None:
    """ADR-0031 fitness: without `--live` the runner removes both variables (ADR-0003 holds)."""
    runner = _evals(DATA, tmp_path)
    dataset = tmp_path / "evals" / "datasets" / "probe.jsonl"
    case = {"id": "probe", "input": {}, "expected": {"live": []}, "grader": "field_equality"}
    dataset.write_text(json.dumps(case) + "\n", encoding="utf-8")
    env = {"RUMBO_LIVE": "1", "RUMBO_EVALS_LIVE": "1"}
    offline = _run(runner, "--suite", "probe", "--entry", "entries:live_probe", env=env)
    assert offline.returncode == 0, offline.stdout + offline.stderr
    assert json.loads(_reports(tmp_path)[-1].read_text(encoding="utf-8"))["live"] is False
    case["expected"] = {"live": ["RUMBO_EVALS_LIVE"]}
    dataset.write_text(json.dumps(case) + "\n", encoding="utf-8")
    live = _run(runner, "--suite", "probe", "--entry", "entries:live_probe", "--live")
    assert live.returncode == 0, live.stdout + live.stderr


# --- P9 (ADR-0030): graders and the judge, imported ----------------------------------------------


def test_grade_calls_each_grader_the_way_its_signature_asks(
    project: Callable[[Config], Path],
) -> None:
    """ADR-0030 fitness: `grade` passes the input to who declares it and awaits coroutines."""
    project(LLM)
    graders = _module("graders")
    verdict = graders.Verdict

    def two(output: dict[str, Any], expected: dict[str, Any]) -> Any:
        return verdict(output == expected, "two")

    def three(output: dict[str, Any], expected: dict[str, Any], case_input: Any) -> Any:
        return verdict(case_input == {"question": "q"}, "three")

    async def coroutine(output: dict[str, Any], expected: dict[str, Any], case_input: Any) -> Any:
        await asyncio.sleep(0)
        return verdict(case_input == {"question": "q"}, "coroutine")

    def variadic(*args: Any) -> Any:
        return verdict(len(args) == 3, "variadic")

    assert [graders.takes_input(g) for g in (two, three, coroutine, variadic)] == [
        False,
        True,
        True,
        True,
    ]
    assert not any(graders.takes_input(g) for g in graders.GRADERS.values())  # unchanged
    for grader in (two, three, coroutine, variadic):
        result = asyncio.run(graders.grade(grader, {"a": 1}, {"a": 1}, {"question": "q"}))
        assert isinstance(result, verdict) and result.ok, result.detail
    builtin = asyncio.run(graders.grade(graders.GRADERS["abstention"], {"abstain": True}, {}, {}))
    assert builtin.ok


def test_judge_is_asynchronous_and_reads_the_question_from_the_case_input(
    project: Callable[[Config], Path],
) -> None:
    """ADR-0030 fitness: the judge awaits `complete` and grades against the case, not the answer."""
    project(LLM)
    graders = _module("graders")
    judge_module = _module("graders.judge")
    seen: list[str] = []

    async def complete(prompt: str) -> str:
        await asyncio.sleep(0)
        seen.append(prompt)
        return "pass\n"

    judge = judge_module.Judge(complete, rubric="be right")
    assert graders.takes_input(judge) and asyncio.iscoroutinefunction(judge.__call__)
    output = {"question": "the question the SYSTEM echoed", "answer": "42"}
    verdict = asyncio.run(graders.grade(judge, output, {}, {"question": "what was asked"}))
    assert verdict.ok
    question = seen[0].split("Question:\n", 1)[1].split("\n", 1)[0]
    assert json.loads(question) == "what was asked"
    assert "the question the SYSTEM echoed" not in question
    # An input without a `question` field is shown whole; a two-argument call still works.
    asyncio.run(judge(output, {}, {"query": "other shape"}))
    assert '{"query": "other shape"}' in seen[1]
    asyncio.run(judge(output, {}))
    assert '"the question the SYSTEM echoed"' in seen[2].split("Question:\n", 1)[1]
    # The calibration reads the row's input too, and is a coroutine.
    rows = judge_module.load_labeled(
        str(Path(str(judge_module.__file__)).parents[1] / "calibration" / "example.jsonl")
    )
    assert all("question" in row["input"] and "question" not in row["output"] for row in rows)
    calibration = asyncio.run(judge_module.calibrate(judge, rows))
    assert calibration.cases == len(rows) == 3
    assert all('"What is the spot price on 2025-01-01?"' in prompt for prompt in seen[3:])


# --- P13: telemetry and budget, imported ----------------------------------------------------------


def test_json_log_formatter_keeps_the_traceback_and_the_stack(
    project: Callable[[Config], Path],
) -> None:
    """P13: `logger.exception(...)` lost its traceback in the JSON line."""
    project(LLM)
    telemetry = _module(f"{PACKAGE}.app.telemetry")
    formatter = telemetry.JsonFormatter()
    try:
        raise ZeroDivisionError("division by zero in the query")
    except ZeroDivisionError:
        failed = logging.LogRecord("db", logging.ERROR, __file__, 1, "query failed", None, None)
        failed.exc_info = sys.exc_info()
    line = json.loads(formatter.format(failed))
    assert line["message"] == "query failed" and line["level"] == "ERROR"
    assert line["exception"].startswith("Traceback (most recent call last):")
    assert line["exception"].endswith("ZeroDivisionError: division by zero in the query")
    assert "\n" not in formatter.format(failed)  # still one JSON object per line
    stacked = logging.LogRecord("db", logging.INFO, __file__, 1, "where", None, None)
    stacked.stack_info = "Stack (most recent call last):\n  File x"
    assert json.loads(formatter.format(stacked))["stack"] == stacked.stack_info
    plain = logging.LogRecord("db", logging.INFO, __file__, 1, "fine", None, None)
    assert set(json.loads(formatter.format(plain))) == {"ts", "level", "logger", "message"}


def test_budget_refuses_values_that_are_not_finite_or_are_negative(
    project: Callable[[Config], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """P13: `RUMBO_BUDGET_COST_USD=nan` made a budget nothing could exceed."""
    project(LLM)
    budget = _module(f"{PACKAGE}.app.budget")
    for name in ("INPUT_TOKENS", "OUTPUT_TOKENS", "COST_USD", "SECONDS"):
        monkeypatch.delenv(f"RUMBO_BUDGET_{name}", raising=False)
    for variable in ("COST_USD", "SECONDS", "INPUT_TOKENS", "OUTPUT_TOKENS"):
        for value in ("nan", "inf", "-inf", "1e400", "-1"):
            monkeypatch.setenv(f"RUMBO_BUDGET_{variable}", value)
            with pytest.raises(ValueError, match=f"RUMBO_BUDGET_{variable}|max_"):
                budget.Budget.from_env()
        monkeypatch.delenv(f"RUMBO_BUDGET_{variable}")
    monkeypatch.setenv("RUMBO_BUDGET_INPUT_TOKENS", "2.9")
    with pytest.raises(ValueError, match=r"RUMBO_BUDGET_INPUT_TOKENS.*whole number"):
        budget.Budget.from_env()
    monkeypatch.setenv("RUMBO_BUDGET_INPUT_TOKENS", "2000")
    assert budget.Budget.from_env().max_input_tokens == 2000
    for bad in (float("nan"), float("inf"), -0.01):
        with pytest.raises(ValueError, match="max_cost_usd"):
            budget.Budget(max_cost_usd=bad)
        meter = budget.Meter()
        with pytest.raises(ValueError, match="cost_usd"):
            meter.charge(cost_usd=bad)
        assert meter.cost_usd == 0.0  # a refused charge is not added
    meter = budget.Meter()
    with pytest.raises(ValueError, match="input_tokens"):
        meter.charge(input_tokens=-5)
    assert meter.input_tokens == 0
    meter.charge(input_tokens=5, cost_usd=0.001)
    assert (meter.input_tokens, meter.cost_usd) == (5, 0.001)


@pytest.mark.parametrize("name", [n for n, c in CONFIGS.items() if c.llm and not c.adopted])
def test_completion_port_and_every_adapter_take_timeout_s(name: str) -> None:
    """P13: the port had no way to hand `meter.remaining_seconds` to the adapter."""
    cfg = CONFIGS[name]
    plan = _plan(name)
    signature = "prompt: str, *, max_tokens: int = 1024, timeout_s: float | None = None"
    assert f"self, {signature}\n    ) -> str: ..." in plan[f"src/{PACKAGE}/app/ports.py"]
    factory = plan[f"src/{PACKAGE}/adapters/llm.py"]
    if cfg.provider == "claude-code":
        adapter = plan[f"src/{PACKAGE}/adapters/claude_code.py"]
        assert f"async def complete({signature}) -> str:" in adapter
        assert "limit = DEFAULT_TIMEOUT_S if timeout_s is None else timeout_s" in adapter
        assert "async def complete(" not in factory  # the factory returns the CLI adapter
    else:
        assert f"    async def complete(\n        {signature}\n    ) -> str:" in factory
        assert "hand it `timeout_s` as the request timeout" in factory  # what to do with it


# --- P1, P10: the Claude Code adapter against a stand-in executable -------------------------------

FAKE_CLI = '''
"""Stands in for `claude`: echoes what reached it (stdin, arguments, working directory)."""

import json
import os
import sys

prompt = sys.stdin.buffer.read().decode("utf-8")
seen = {"stdin": prompt, "argv": sys.argv[1:], "cwd": os.listdir("."), "where": os.getcwd()}
print(json.dumps({"result": json.dumps(seen)}))
'''


def _fake_claude(folder: Path) -> Path:
    """A `claude` on disk: an npm-style `.cmd` shim on Windows, a shell script elsewhere."""
    folder.mkdir()
    fake = folder / "fake_cli.py"
    fake.write_text(FAKE_CLI, encoding="utf-8")
    if os.name == "nt":
        shim = folder / "claude.cmd"
        shim.write_text(f'@echo off\r\n"{sys.executable}" -I "{fake}" %*\r\n', encoding="utf-8")
    else:
        shim = folder / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{fake}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    return shim


@pytest.fixture
def adapter(
    project: Callable[[Config], Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> ModuleType:
    root = project(CLAUDE_CODE)
    module = _module(f"{PACKAGE}.adapters.claude_code")
    shim = _fake_claude(tmp_path / "bin")
    monkeypatch.setattr(module.shutil, "which", lambda name: str(shim))
    monkeypatch.setenv("RUMBO_LIVE", "1")
    monkeypatch.chdir(root)  # the project: CLAUDE.md, .claude/settings.json with its hooks
    return module


def test_prompt_reaches_the_cli_on_stdin_and_never_on_the_command_line(
    adapter: ModuleType, tmp_path: Path
) -> None:
    """P1: under an npm `.cmd` shim, `cmd.exe` parsed the prompt (commands ran, %VAR% expanded)."""
    marker = tmp_path / "INJECTED.txt"
    prompt = f'hello" & echo injected>"{marker}" & rem %OS% %PATH%\n--version\nñ'
    seen = json.loads(asyncio.run(adapter.complete(prompt, max_tokens=100_000)))
    assert seen["stdin"] == prompt
    assert seen["argv"] == list(adapter.ARGS)
    assert not marker.exists()
    assert all(isinstance(arg, str) for arg in adapter.ARGS)
    assert adapter.ARGS[:5] == ("-p", "--output-format", "json", "--max-turns", "1")
    assert ("--tools", "") in zip(adapter.ARGS, adapter.ARGS[1:], strict=False)  # no tool at all
    for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in adapter.ARGS
    # The run is isolated from the project: an empty directory that is gone afterwards.
    assert seen["cwd"] == []
    assert Path(seen["where"]).resolve() != Path.cwd().resolve()
    assert not Path(seen["where"]).exists()
    assert (Path.cwd() / "CLAUDE.md").is_file()  # which the CLI therefore never saw


class _Hanging:
    """A process that never answers: records whether it was killed and waited for."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.events: list[str] = []

    async def communicate(self, data: bytes | None = None) -> tuple[bytes, bytes]:
        self.events.append("started")
        await asyncio.sleep(3600)
        return b"", b""

    def kill(self) -> None:
        self.events.append("killed")
        self.returncode = -9

    async def wait(self) -> int:
        self.events.append("reaped")
        return -9


@pytest.mark.parametrize("how", ["cancelled", "timeout", "error"])
def test_cli_is_killed_and_reaped_however_the_call_ends(
    adapter: ModuleType, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    """P10: only `TimeoutError` was handled; a cancelled request left `claude -p` running."""
    process = _Hanging()

    async def spawn(*args: str, **kwargs: Any) -> _Hanging:
        assert kwargs["stdin"] == asyncio.subprocess.PIPE and os.listdir(kwargs["cwd"]) == []
        return process

    monkeypatch.setattr(adapter.asyncio, "create_subprocess_exec", spawn)

    async def cancel_midway() -> None:
        task = asyncio.create_task(adapter.complete("ping"))
        while "started" not in process.events:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    if how == "cancelled":
        asyncio.run(cancel_midway())
    elif how == "timeout":
        with pytest.raises(TimeoutError):
            asyncio.run(adapter.complete("ping", timeout_s=0.01))
    else:

        async def broken(data: bytes | None = None) -> tuple[bytes, bytes]:
            process.events.append("started")
            raise OSError("pipe closed")

        monkeypatch.setattr(process, "communicate", broken)
        with pytest.raises(OSError, match="pipe closed"):
            asyncio.run(adapter.complete("ping"))
    assert process.events == ["started", "killed", "reaped"]


# --- the generated project's own unit tests ---------------------------------------------------


@pytest.mark.parametrize(
    "name", ["service-llm-claude-code-mvp-mypy", "service-llm-anthropic-prod", "data-mvp"]
)
def test_rendered_unit_tests_pass(name: str, tmp_path: Path) -> None:
    """The tests a new project is born with are green on the rendered tree (no `uv`, offline)."""
    cfg = CONFIGS[name]
    root = _write(cfg, tmp_path)
    wanted = ["budget", "telemetry", "entrypoint", "claude_code_adapter", "judge", "graders"]
    files = [f"tests/unit/test_{w}.py" for w in wanted]
    files = [file for file in files if (root / file).is_file()]
    assert len(files) == {"claude-code": 6, "anthropic": 5, "": 1}[cfg.provider]
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "RUMBO_LIVE": "1"}  # the tests unset it
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *files, "-p", "no:cacheprovider"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert " passed" in result.stdout and "failed" not in result.stdout
