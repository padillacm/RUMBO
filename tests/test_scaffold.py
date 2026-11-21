import ast
import dataclasses
import importlib.util
import itertools
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from rumbo import __version__
from rumbo.project import HEXAGONAL_LAYERS, KINDS, Project
from rumbo.scaffold import (
    CI_STEPS_NOTE,
    PROVIDERS,
    Config,
    Detected,
    Report,
    _included,
    check_steps,
    dependency_name,
    detect_kind,
    detect_layout,
    detect_package,
    detect_project,
    detect_tests_dir,
    dev_dependencies,
    existing_workflows,
    merge_lines,
    merge_pyproject,
    missing_dev_dependencies,
    plan_files,
    read_pyproject,
    runtime_dependencies,
    scaffold,
    set_rigor,
    target_path,
    to_package,
    update,
    valid_package,
)

PROVIDER_CASES = [(False, "", {}), *((True, p, {}) for p in PROVIDERS)]
PROVIDER_CASES.append(
    (True, "other", {"sdk_package": "foo-sdk", "sdk_module": "foo_sdk", "sdk_env_key": "FOO_KEY"})
)
COMBINATIONS = [
    Config(name="Project", package="package", llm=llm, provider=prov, rigor=rigor, **extra)
    for (llm, prov, extra), rigor in itertools.product(
        PROVIDER_CASES, ["prototype", "mvp", "production"]
    )
]


# Covers F-0004.R2 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize("cfg", COMBINATIONS, ids=lambda c: f"{c.provider or 'no-llm'}-{c.rigor}")
def test_every_combination_renders(cfg: Config) -> None:
    plan = plan_files(cfg)
    for path, item in plan.items():
        assert "__pkg__" not in path and "dot_" not in path
        if not isinstance(item, str):
            continue
        assert "{{" not in item.replace("${{", ""), path
        assert "secrets. " not in item, path
        if path.endswith(".py"):
            ast.parse(item, filename=path)
        if path.endswith(".toml"):
            tomllib.loads(item)
        if path.endswith(".yml"):
            assert not any(line.lstrip().startswith(":") for line in item.splitlines()), path
    data = tomllib.loads(str(plan["pyproject.toml"]))
    assert data["tool"]["rumbo"]["package"] == "package"
    assert data["tool"]["rumbo"]["rigor"] == cfg.rigor
    assert data["tool"]["rumbo"]["llm"] is cfg.llm


# Covers F-0004.R5 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_ci_never_holds_the_model_credential() -> None:
    plan = plan_files(Config(name="x", package="x", llm=True, provider="openai", rigor="mvp"))
    ci = str(plan[".github/workflows/ci.yml"])
    nightly = str(plan[".github/workflows/nightly.yml"])
    assert "OPENAI_API_KEY" not in ci
    assert "secrets.OPENAI_API_KEY" in nightly
    assert "gitleaks-action" in ci


# Covers F-0004.R5 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_llm_only_files() -> None:
    with_llm = plan_files(Config(name="x", package="x", llm=True, provider="anthropic"))
    without = plan_files(Config(name="x", package="x", llm=False))
    llm_only = {
        "evals/run_evals.py",
        "evals/datasets/smoke.jsonl",
        "src/x/prompts/__init__.py",
        "src/x/app/entrypoint.py",
        "src/x/adapters/llm.py",
        "src/x/app/ports.py",
        "src/x/app/budget.py",
        "src/x/app/telemetry.py",
        "tests/unit/test_budget.py",
        "tests/unit/test_telemetry.py",
        "docs/evals/plan.md",
        ".env.example",
    }
    assert llm_only <= with_llm.keys()
    assert not llm_only & without.keys()
    assert "Budget abstentions spike" in str(with_llm["docs/ops/runbook.md"])
    assert "cost.usd" not in str(without["docs/ops/slos.md"])
    assert "src/x/adapters/claude_code.py" not in with_llm


# Covers F-0004.R5 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_claude_code_provider_has_no_sdk_and_an_adapter() -> None:
    cfg = Config(name="x", package="x", llm=True, provider="claude-code")
    assert cfg.sdk_package == ""
    plan = plan_files(cfg)
    assert "src/x/adapters/claude_code.py" in plan
    assert "npm install -g @anthropic-ai/claude-code" in str(plan[".github/workflows/nightly.yml"])


# Covers F-0004.R6 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_rigor_controls_nightly_dependabot_and_check() -> None:
    proto = plan_files(Config(name="x", package="x", llm=False, rigor="prototype"))
    prod = plan_files(
        Config(name="x", package="x", llm=True, provider="openai", rigor="production")
    )
    assert ".github/workflows/nightly.yml" not in proto
    assert ".github/dependabot.yml" not in proto
    assert ".github/workflows/nightly.yml" in prod

    def check_line(plan: dict) -> str:  # type: ignore[type-arg]
        return next(ln for ln in str(plan["Makefile"]).splitlines() if ln.startswith("check:"))

    # ADR-0006: `make check` is an alias; what runs is decided by [tool.rumbo.checks].
    assert check_line(proto) == check_line(prod) == RUNNER_ALIAS.format("check")
    assert "evals:" not in str(proto["Makefile"]) and "evals:" in str(prod["Makefile"])
    proto_steps = tomllib.loads(str(proto["pyproject.toml"]))["tool"]["rumbo"]["checks"]
    prod_steps = tomllib.loads(str(prod["pyproject.toml"]))["tool"]["rumbo"]["checks"]
    assert proto_steps["test"] == "uv run pytest"
    assert prod_steps["test"] == "uv run pytest --cov --cov-report=term-missing:skip-covered"
    assert "evals" not in proto_steps and "evals" in prod_steps
    assert "--cov-fail-under" not in str(prod["pyproject.toml"])


RUNNER = "uv run python scripts/rumbo_check.py"
RUNNER_ALIAS = "{}:" + " " * 7 + "; " + RUNNER  # `check:       ; uv run python ...`
SECURITY_STEPS = ["security", "security-scripts", "audit"]
CHECK_STEPS = ["lint", "format", "types", "test", "arch", *SECURITY_STEPS]  # mvp and above


def expected_steps(cfg: Config) -> list[str]:
    """ADR-0006 order, scaled by rigor, kind and layout (ADR-0010, ADR-0011)."""
    return [
        "lint",
        "format",
        "types",
        "test",
        *(["arch"] if cfg.arch else []),
        *(SECURITY_STEPS if cfg.rigor != "prototype" else []),
        *(["evals"] if cfg.evals else []),
        "sensor",
    ]


# Covers F-0004.R6 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize("cfg", COMBINATIONS, ids=lambda c: f"{c.provider or 'no-llm'}-{c.rigor}")
def test_checks_table_lists_the_steps_in_order(cfg: Config) -> None:
    """ADR-0006 fitness: every project has [tool.rumbo.checks], same steps, same order."""
    plan = plan_files(cfg)
    checks = tomllib.loads(str(plan["pyproject.toml"]))["tool"]["rumbo"]["checks"]
    assert list(checks) == expected_steps(cfg) == check_steps(cfg)  # tomllib keeps file order
    assert all(isinstance(cmd, str) and cmd.strip() for cmd in checks.values())
    assert checks["sensor"] == "uv run python scripts/check_tests_not_weakened.py --base {base}"
    if cfg.rigor != "prototype":
        assert ("evals" in checks["security"]) is cfg.llm
    assert ("--cov" in checks["test"]) is (cfg.rigor != "prototype")
    # Every alias in the Makefile delegates to the runner; the step names exist in the table.
    makefile = str(plan["Makefile"])
    for target, steps in {
        "check": "",
        "check-brief": " --brief",
        "lint": " lint format",
        "types": " types",
        "test": " test",
        "arch": " arch",
        "security": " security security-scripts audit",
        "sensor": " sensor",
    }.items():
        lines = [ln for ln in makefile.splitlines() if ln.startswith(f"{target}:")]
        if target == "security" and cfg.rigor == "prototype":
            assert lines == []  # no alias for steps the table does not have yet
            continue
        assert lines[0].split(";", 1)[1].strip() == RUNNER + steps
        assert all(step in checks for step in steps.split() if step != "--brief")


@pytest.mark.parametrize("cfg", COMBINATIONS, ids=lambda c: f"{c.provider or 'no-llm'}-{c.rigor}")
def test_generated_ci_runs_the_runner_on_linux_and_windows_without_make(cfg: Config) -> None:
    """ADR-0006 fitness: a Windows job runs `python scripts/rumbo_check.py`; no `make` in CI."""
    plan = plan_files(cfg)
    ci = str(plan[".github/workflows/ci.yml"])
    jobs_section = ci.split("\njobs:\n", 1)[1]
    jobs = [ln.strip().rstrip(":") for ln in jobs_section.splitlines() if re.match(r"^  \S+:$", ln)]
    assert jobs == ["check", "check-windows", "secrets"]
    windows = ci.split("  check-windows:", 1)[1].split("\n  secrets:", 1)[0]
    assert "runs-on: windows-latest" in windows
    assert "uv sync --locked" in windows
    assert "run: uv run python scripts/rumbo_check.py" in windows
    assert ci.count("run: uv run python scripts/rumbo_check.py") == 2  # ubuntu and windows
    assert not re.search(r"\bmake\b", ci)
    assert "--strict" in ci  # the separate strict sensor step stays on ubuntu
    if cfg.rigor == "production":
        assert "uv export --no-dev --no-emit-project --format cyclonedx1.5" in ci
    nightly = plan.get(".github/workflows/nightly.yml")
    if nightly is not None:
        make_lines = [ln for ln in str(nightly).splitlines() if re.search(r"\bmake\b", ln)]
        mutation = ["        run: make mutation"]  # mutmut needs os.fork: POSIX only
        assert make_lines == (mutation if cfg.rigor == "production" else [])
        if cfg.llm:
            assert "run: uv run python evals/run_evals.py --suite all --live" in str(nightly)


# Covers F-0004.R7 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_runner_is_copied_byte_identical_and_protected(simple_project: Path) -> None:
    from rumbo.scaffold import CHECK_FILE
    from rumbo.sensors import guard

    runner = simple_project / "scripts" / "rumbo_check.py"
    assert runner.read_text(encoding="utf-8") == CHECK_FILE.read_text(encoding="utf-8")
    assert CHECK_FILE.name == "check.py" and CHECK_FILE.parent.name == "sensors"
    assert os.name == "nt" or runner.stat().st_mode & 0o111  # no exec bits on Windows
    assert "scripts/rumbo_check.py" in guard.PROTECTED_FILES
    attributes = (simple_project / ".gitattributes").read_text(encoding="utf-8")
    assert attributes.endswith("* text=auto eol=lf\n")


# Covers F-0004.R11 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_update_refreshes_the_runner(llm_project: Path) -> None:
    from rumbo.scaffold import CHECK_FILE

    runner = llm_project / "scripts" / "rumbo_check.py"
    runner.write_text("# stale copy\n", encoding="utf-8")
    rep = update(Project.load(llm_project), llm_project)
    assert "scripts/rumbo_check.py" in rep.updated
    assert runner.read_text(encoding="utf-8") == CHECK_FILE.read_text(encoding="utf-8")
    runner.unlink()
    rep = update(Project.load(llm_project), llm_project)
    assert "scripts/rumbo_check.py" in rep.created and runner.exists()


# Covers F-0004.R11 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_update_adds_the_checks_table_to_an_older_project(llm_project: Path) -> None:
    pyproject = llm_project / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    start = original.index("[tool.rumbo.checks]")
    end = original.index("[tool.ruff]")
    pyproject.write_text(original[:start] + original[end:], encoding="utf-8")
    assert "checks" not in tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["rumbo"]
    try:
        rep = update(Project.load(llm_project), llm_project)
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        assert list(data["tool"]["rumbo"]["checks"]) == [*CHECK_STEPS, "evals", "sensor"]
        assert "pyproject.toml (+[tool.rumbo.checks])" in rep.merged
        # A second update changes nothing: the human's steps are never replaced.
        rep = update(Project.load(llm_project), llm_project)
        assert not rep.merged
    finally:
        pyproject.write_text(original, encoding="utf-8")


# Covers F-0004.R5 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_provider_sdk_is_forbidden_in_domain() -> None:
    cfg = Config(name="x", package="x", llm=True, provider="google")
    data = tomllib.loads(str(plan_files(cfg)["pyproject.toml"]))
    purity = next(c for c in data["tool"]["importlinter"]["contracts"] if c["type"] == "forbidden")
    assert "google" in purity["forbidden_modules"]
    assert cfg.sdk_package == "google-genai"


# Covers F-0004.R1 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_name_is_escaped_in_toml_and_python() -> None:
    cfg = Config(name="Demo 'quoted' project", package="demo", llm=True, provider="anthropic")
    plan = plan_files(cfg)
    tomllib.loads(str(plan["pyproject.toml"]))
    ast.parse(str(plan["src/demo/api/http.py"]))


# Covers F-0004.R1 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize("name", ['bad "name"', "back\\slash", "new\nline", ""])
def test_invalid_names(name: str) -> None:
    with pytest.raises(ValueError):
        Config(name=name, package="x", llm=False)


# Covers F-0004.R2 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_target_path() -> None:
    cfg = Config(name="x", package="my_pkg", llm=False)
    assert target_path("dot_github/workflows/ci.yml.tmpl", cfg) == ".github/workflows/ci.yml"
    assert target_path("src/__pkg__/domain/__init__.py", cfg) == "src/my_pkg/domain/__init__.py"


# Covers F-0004.R1 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize("package", ["MyPkg", "1pkg", "with-dash", "class", "tests", ""])
def test_invalid_packages(package: str) -> None:
    assert not valid_package(package)
    with pytest.raises(ValueError):
        Config(name="x", package=package, llm=False)


# Covers F-0004.R1 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_to_package() -> None:
    assert to_package("2048 game") == "app_2048_game"
    assert to_package("Mi Proyecto") == "mi_proyecto"
    assert to_package("class") == "class_app"


# Covers F-0004.R1 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_llm_needs_valid_provider() -> None:
    with pytest.raises(ValueError):
        Config(name="x", package="x", llm=True)
    with pytest.raises(ValueError):
        Config(name="x", package="x", llm=True, provider="antropic")
    with pytest.raises(ValueError):
        Config(name="x", package="x", llm=True, provider="other", sdk_package="foo")


# Covers F-0004.R1 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize(
    ("sdk_package", "sdk_module"),
    [
        ('foo"', "foo"),  # breaks the TOML string
        ("--upgrade", "foo"),  # would be an option for `uv add`
        ("foo>=1", "foo"),  # versions are resolved by uv add, never written by hand
        ("foo", 'x"'),
        ("foo", "x; import os"),
        ("foo", "1x"),
    ],
)
def test_other_provider_sdk_names_are_validated(sdk_package: str, sdk_module: str) -> None:
    with pytest.raises(ValueError, match="--sdk-"):
        Config(
            name="x",
            package="x",
            llm=True,
            provider="other",
            sdk_package=sdk_package,
            sdk_module=sdk_module,
            sdk_env_key="X_KEY",
        )


# Covers F-0004.R5 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_other_provider_accepts_extras_and_dotted_modules() -> None:
    cfg = Config(
        name="x",
        package="x",
        llm=True,
        provider="other",
        sdk_package="some-sdk[async]",
        sdk_module="some_sdk.client",
        sdk_env_key="X_KEY",
    )
    assert (cfg.sdk_package, cfg.sdk_module) == ("some-sdk[async]", "some_sdk")
    plan = plan_files(cfg)
    tomllib.loads(str(plan["pyproject.toml"]))
    ast.parse(str(plan["tests/architecture/test_architecture.py"]))


# Covers F-0004.R8 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_scaffold_skips_existing_and_reports(simple_project: Path) -> None:
    agents = simple_project / "AGENTS.md"
    agents.write_text("mine", encoding="utf-8")
    rep = scaffold(
        Config(name="Demo", package="demo", llm=False, rigor="prototype"), simple_project
    )
    assert agents.read_text(encoding="utf-8") == "mine"
    assert "AGENTS.md" in rep.skipped and "pyproject.toml" in rep.skipped
    assert not rep.created and not rep.merged


# Covers F-0004.R8 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_force_overwrites_but_merges_pyproject_and_gitignore(simple_project: Path) -> None:
    pyproject = simple_project / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            "dependencies = []", 'dependencies = ["requests>=2"]'
        ),
        encoding="utf-8",
    )
    (simple_project / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    brief = simple_project / "docs" / "product" / "brief.md"
    brief.write_text("# My brief\n", encoding="utf-8")
    rep = scaffold(
        Config(name="Demo", package="demo", llm=False, rigor="prototype"),
        simple_project,
        force=True,
    )
    assert '"requests>=2"' in pyproject.read_text(encoding="utf-8")
    assert "node_modules/" in (simple_project / ".gitignore").read_text(encoding="utf-8")
    assert "rumbo:template" in brief.read_text(encoding="utf-8")
    assert (simple_project / "docs" / "product" / "brief.md.bak").read_text(
        encoding="utf-8"
    ) == "# My brief\n"
    assert "docs/product/brief.md" in rep.created


def test_sensor_is_copied_and_executable(simple_project: Path) -> None:
    sensor = simple_project / "scripts" / "check_tests_not_weakened.py"
    assert sensor.exists()
    assert os.name == "nt" or sensor.stat().st_mode & 0o111  # no exec bits on Windows


# Covers F-0004.R8 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_merge_pyproject_keeps_existing_sections() -> None:
    existing = '[project]\nname = "x"\n\n[tool.ruff]\nline-length = 120\n'
    template = (
        '[tool.ruff]\nline-length = 100\n\n[tool.ruff.lint]\nselect = ["E"]\n\n'
        '[tool.rumbo]\npackage = "x"\n\n[tool.hatch.build.targets.wheel]\npackages = ["src/x"]\n'
    )
    merged, notes = merge_pyproject(existing, template)
    data = tomllib.loads(merged)
    assert data["tool"]["ruff"] == {"line-length": 120}
    assert data["tool"]["rumbo"] == {"package": "x"}
    assert "hatch" not in data["tool"]  # no hatchling build backend declared
    assert any("kept" in n for n in notes)


# Covers F-0004.R8 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_merge_lines_without_duplicates() -> None:
    merged, new = merge_lines(".venv/\n", ".venv/\n.env\n")
    assert new == [".env"]
    assert merged.count(".venv/") == 1


# Covers F-0004.R11 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_update_refreshes_owned_files_only(llm_project: Path) -> None:
    rumbo_md = llm_project / "RUMBO.md"
    rumbo_md.write_text("old", encoding="utf-8")
    brief = llm_project / "docs" / "product" / "brief.md"
    brief.write_text("# written by the human\n", encoding="utf-8")
    makefile = llm_project / "Makefile"
    makefile.write_text(
        makefile.read_text(encoding="utf-8") + "\nextra: ; echo hi\n", encoding="utf-8"
    )
    (llm_project / ".claude" / "commands" / "rumbo-r.md").unlink()
    pyproject = llm_project / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            f'version = "{__version__}"', 'version = "0.0.1"'
        ),
        encoding="utf-8",
    )

    rep = update(Project.load(llm_project), llm_project)
    assert rumbo_md.read_text(encoding="utf-8") != "old"
    assert brief.read_text(encoding="utf-8") == "# written by the human\n"
    assert "extra:" in makefile.read_text(encoding="utf-8")
    assert (llm_project / ".claude" / "commands" / "rumbo-r.md").exists()
    assert "RUMBO.md" in rep.updated and ".claude/commands/rumbo-r.md" in rep.created
    assert "Makefile" in rep.differs
    assert Project.load(llm_project).harness_version == __version__


def test_config_round_trips_through_project(llm_project: Path) -> None:
    project = Project.load(Path(str(llm_project)))
    cfg = Config.from_project(project)
    assert (cfg.name, cfg.package, cfg.provider, cfg.rigor) == (
        "XM Agent",
        "xm_agent",
        "anthropic",
        "mvp",
    )
    assert (
        plan_files(cfg).keys()
        == plan_files(
            Config(name="XM Agent", package="xm_agent", llm=True, provider="anthropic", rigor="mvp")
        ).keys()
    )


# Covers F-0004.R11, F-0004.R12 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_pre_commit_rev_bumps_are_not_drift(llm_project: Path) -> None:
    from rumbo.scaffold import update, update_diff

    config = llm_project / ".pre-commit-config.yaml"
    original = config.read_text(encoding="utf-8")
    bumped = original.replace("rev: v", "rev: v9.", 1)
    assert bumped != original
    config.write_text(bumped, encoding="utf-8")
    assert ".pre-commit-config.yaml" not in update_diff(Project.load(llm_project), llm_project)
    rep = update(Project.load(llm_project), llm_project)
    assert ".pre-commit-config.yaml" not in rep.differs
    config.write_text(original, encoding="utf-8")


ARCH_TEST = "tests/architecture/test_architecture.py"


def _load_arch_test(root: Path) -> Any:
    """Render the architecture test into a tmp project and import it from there."""
    cfg = Config(name="Demo", package="demo", llm=False, rigor="prototype")
    arch = root / ARCH_TEST
    arch.parent.mkdir(parents=True, exist_ok=True)
    arch.write_text(str(plan_files(cfg)[ARCH_TEST]), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("generated_arch_test", arch)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def _domain_module(root: Path, source: str) -> Path:
    domain = root / "src" / "demo" / "domain"
    domain.mkdir(parents=True, exist_ok=True)
    module = domain / "sample.py"
    module.write_text(source, encoding="utf-8")
    return module


def _domain_check(root: Path, source: str) -> list[str]:
    """Violations the generated architecture test finds in one domain module of a tmp project."""
    loaded = _load_arch_test(root)
    return loaded._domain_violations(_domain_module(root, source))


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import PurePath\n",
        "from pathlib import PurePosixPath as P, PureWindowsPath\n",
        "from io import StringIO\n",
        "from io import BytesIO, StringIO\n",
        "def open(name):\n    return name\n\n\nopen('x')\n",
        "open = print\nopen('x')\n",
        "from codecs import open\n\nopen('x')\n",
        "import codecs as open\n\nopen.lookup('x')\n",
        "class Door:\n    def open(self) -> None: ...\n\n\nDoor().open()\n",
    ],
)
def test_architecture_domain_allows_pure_code(tmp_path: Path, source: str) -> None:
    assert _domain_check(tmp_path, source) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import pathlib\n", "imports pathlib"),
        ("import io\n", "imports io"),
        ("from pathlib import Path\n", "imports pathlib.Path"),
        ("from pathlib import PurePath, Path as P\n", "imports pathlib.Path"),
        ("from io import open_code\n", "imports io.open_code"),
        ("from io import *\n", "imports io.*"),
        ("import os\n", "imports os"),
        ("open('x')\n", "calls the builtin open()"),
        ("def f() -> None:\n    with open('x') as fh:\n        fh.read()\n", "open()"),
        ("import builtins\n\nbuiltins.open('x')\n", "calls the builtin open()"),
        ("import pathlib\n\npathlib.Path('x').open()\n", "imports pathlib"),
        # a method named open does not shadow the builtin at module level
        ("class Door:\n    def open(self) -> None: ...\n\n\nopen('x')\n", "open()"),
    ],
)
def test_architecture_domain_rejects_io(tmp_path: Path, source: str, expected: str) -> None:
    assert any(expected in v for v in _domain_check(tmp_path, source))


def test_architecture_failure_names_file_and_offender(tmp_path: Path) -> None:
    loaded = _load_arch_test(tmp_path)
    _domain_module(tmp_path, "from pathlib import Path\nopen('x')\n")
    with pytest.raises(AssertionError) as err:
        loaded.test_domain_has_no_io()
    message = str(err.value)
    assert "sample.py" in message
    assert "pathlib.Path" in message
    assert "open()" in message


# ------------------------------------------------------------- git hooks (ADR-0008)
def test_pre_commit_config_wires_the_guard_as_git_hooks(simple_project: Path) -> None:
    config = (simple_project / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "default_install_hook_types: [pre-commit, commit-msg]" in config
    assert "default_stages: [pre-commit]" in config  # file fixers never see the commit message
    guard_hook = config.split("- id: rumbo-guard\n", 1)[1].split("- id: rumbo-guard-commit-msg")[0]
    assert "entry: uv run python scripts/rumbo_guard.py pre-commit" in guard_hook
    for line in (
        "language: system",
        "always_run: true",
        "pass_filenames: false",
        "stages: [pre-commit]",
    ):
        assert line in guard_hook
    msg_hook = config.split("- id: rumbo-guard-commit-msg\n", 1)[1]
    assert "entry: uv run python scripts/rumbo_guard.py commit-msg" in msg_hook
    assert "language: system" in msg_hook and "stages: [commit-msg]" in msg_hook
    for existing in ("id: ruff-check", "id: gitleaks", "id: uv-lock", "id: lint-imports"):
        assert existing in config


class _Stdin:
    """Stands in for `sys.stdin`: a terminal or a pipe."""

    def __init__(self, terminal: bool) -> None:
        self.terminal = terminal

    def isatty(self) -> bool:
        return self.terminal


def _init_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal: bool
) -> list[list[str]]:
    """Prepares `rumbo init` (with install) for a test: uv is faked, git is real and isolated,
    no agent marker is in the environment and stdin is (or is not) a terminal. Returns the list
    the commands of `install` are recorded in."""
    from rumbo import scaffold as scaffold_module
    from rumbo.scaffold import Report
    from rumbo.sensors.guard import DEFAULT_AGENT_ENV

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for marker in DEFAULT_AGENT_ENV:  # the suite itself may be run by an agent
        monkeypatch.delenv(marker, raising=False)
    monkeypatch.setattr("sys.stdin", _Stdin(terminal))
    monkeypatch.setattr("rumbo.scaffold.at_a_terminal", lambda: terminal)
    commands: list[list[str]] = []
    real_run = scaffold_module._run

    def fake_run(cmd: list[str], cwd: Path, report: Report) -> int:
        commands.append(cmd)
        if cmd[0] == "uv":  # uv add / pre-commit need the network; not under test here
            report.commands.append((" ".join(cmd), 0))
            return 0
        return real_run(cmd, cwd, report)

    monkeypatch.setattr(scaffold_module, "_run", fake_run)
    monkeypatch.setattr(scaffold_module.shutil, "which", lambda _name: "uv")
    return commands


def _git_repo(root: Path) -> Path:
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def _rumbo_human(repo: Path) -> str:
    """The value of `rumbo.human` in the clone ('' when it is not set)."""
    declared = subprocess.run(
        ["git", "config", "--get", "rumbo.human"], cwd=repo, capture_output=True, text=True
    )
    return declared.stdout.strip()


# Covers F-0004.R15 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_init_declares_the_human_in_the_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0008, as narrowed by ADR-0021: the scenario is a human at a terminal."""
    from rumbo.cli import main

    commands = _init_without_network(tmp_path, monkeypatch, terminal=True)
    repo = _git_repo(tmp_path / "repo")
    init = ["init", "--yes", "--name", "Demo", "--package", "demo", "--no-llm", "--rigor", "mvp"]
    assert main([*init, "--dir", str(repo)]) == 0
    assert commands.index(["uv", "run", "pre-commit", "install"]) < commands.index(
        ["git", "config", "rumbo.human", "true"]
    )
    declared = subprocess.run(
        ["git", "config", "--get", "rumbo.human"], cwd=repo, capture_output=True, text=True
    )
    assert declared.stdout.strip() == "true"
    capsys.readouterr()
    # doctor is quiet while the clone declares its human, and says how to fix it otherwise
    (repo / "uv.lock").write_text("", encoding="utf-8")
    assert main(["doctor", "--dir", str(repo)]) == 0
    assert "rumbo.human" not in capsys.readouterr().out
    subprocess.run(["git", "config", "--unset", "rumbo.human"], cwd=repo, check=True)
    assert main(["doctor", "--dir", str(repo)]) == 0  # a warning, not a failure
    assert "git config rumbo.human true" in capsys.readouterr().out
    # Outside a git repository nothing is declared and the existing warning stays.
    plain = tmp_path / "plain"
    plain.mkdir()
    commands.clear()
    assert main([*init, "--dir", str(plain)]) == 0
    assert not any(cmd[0] == "git" for cmd in commands)
    assert "not a git repository" in capsys.readouterr().out


# Covers F-0004.R15 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_init_declares_rumbo_human_only_for_an_interactive_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0021 fitness: `rumbo init` sets `rumbo.human` only when a human is demonstrably
    there (no agent marker in the environment and stdin is a terminal). Otherwise it leaves the
    setting alone and prints the one command the human runs, in its next steps."""
    from rumbo.cli import main
    from rumbo.scaffold import HUMAN_COMMAND, human_present
    from rumbo.sensors.guard import DEFAULT_AGENT_ENV

    declare = list(HUMAN_COMMAND)
    hint = "1. git config rumbo.human true"  # the human's first step in the clone
    init = ["init", "--yes", "--name", "Demo", "--package", "demo", "--no-llm", "--rigor", "mvp"]
    assert declare == ["git", "config", "rumbo.human", "true"]

    # 1. A terminal and no marker: a human is there, the clone is declared, nothing to add.
    commands = _init_without_network(tmp_path, monkeypatch, terminal=True)
    human = _git_repo(tmp_path / "human")
    assert main([*init, "--dir", str(human)]) == 0
    assert declare in commands
    assert _rumbo_human(human) == "true"
    out = capsys.readouterr().out
    assert hint not in out and "Next steps (A8):\n  1. uv run python" in out

    # 2. A terminal but an agent marker: not set, and the next steps say what the human runs.
    monkeypatch.setenv(DEFAULT_AGENT_ENV[0], "1")
    commands.clear()
    agent = _git_repo(tmp_path / "agent")
    assert main([*init, "--dir", str(agent)]) == 0
    assert declare not in commands
    assert _rumbo_human(agent) == ""
    out = capsys.readouterr().out
    assert f"Next steps (A8):\n  {hint}" in out
    assert "  2. uv run python scripts/rumbo_check.py" in out  # the other steps are renumbered
    # `--no-install` declares nothing either: the command comes after the hooks are installed.
    bare = _git_repo(tmp_path / "bare")
    assert main([*init, "--no-install", "--dir", str(bare)]) == 0
    assert _rumbo_human(bare) == ""
    out = capsys.readouterr().out
    assert "  3. git config rumbo.human true" in out and "  4. uv run python scripts/" in out

    # 3. No marker but no terminal (a pipe, CI, an agent with no known marker): not set either.
    monkeypatch.delenv(DEFAULT_AGENT_ENV[0])
    monkeypatch.setattr("sys.stdin", _Stdin(terminal=False))
    monkeypatch.setattr("rumbo.scaffold.at_a_terminal", lambda: False)
    commands.clear()
    piped = _git_repo(tmp_path / "piped")
    assert main([*init, "--dir", str(piped)]) == 0
    assert declare not in commands
    assert _rumbo_human(piped) == ""
    assert f"Next steps (A8):\n  {hint}" in capsys.readouterr().out

    # A declaration the human already made is left alone, and then there is nothing to say.
    subprocess.run(declare, cwd=piped, check=True)
    assert main([*init, "--dir", str(piped)]) == 0
    assert _rumbo_human(piped) == "true"
    assert hint not in capsys.readouterr().out

    # The two conditions, one by one (both are injectable); a project's own `agent_env` counts.
    def terminal() -> bool:
        return True

    assert human_present(human, environ={}, isatty=terminal) is True
    assert human_present(human, environ={}, isatty=lambda: False) is False
    for marker in DEFAULT_AGENT_ENV:
        assert human_present(human, environ={marker: "1"}, isatty=terminal) is False
    pyproject = human / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(
        text.replace("[tool.rumbo]\n", '[tool.rumbo]\nagent_env = ["MY_AGENT"]\n', 1),
        encoding="utf-8",
    )
    assert human_present(human, environ={"MY_AGENT": "1"}, isatty=terminal) is False
    assert human_present(human, environ={DEFAULT_AGENT_ENV[0]: "1"}, isatty=terminal) is True


# Covers F-0004.R15 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_the_null_device_is_not_a_terminal() -> None:
    """ADR-0021: on Windows `isatty()` is true for NUL, where a process without a terminal
    reads from; `rumbo init < /dev/null` declared the human. A closed or missing stream and
    one that is not a file are not terminals either."""
    import io

    from rumbo.scaffold import is_console

    with open(os.devnull, encoding="utf-8") as null:
        assert is_console(null) is False
    closed = open(os.devnull, encoding="utf-8")  # noqa: SIM115
    closed.close()
    assert is_console(closed) is False
    assert is_console(None) is False
    assert is_console(io.StringIO("y\n")) is False


# ------------------------------------------------------- kinds and layouts (ADR-0010)
def _axes(kind: str, layout: str = "", layers: tuple[str, ...] = (), llm: bool = False) -> Config:
    return Config(
        name="x",
        package="x",
        llm=llm,
        provider="anthropic" if llm else "",
        rigor="mvp",
        kind=kind,
        layout=layout,
        layers=layers,
    )


HTTP_FILES = {"src/x/api/http.py", "tests/integration/test_http.py"}
OPS_FILES = {"infra/Dockerfile", "docs/ops/runbook.md"}
SLOS_FILES = {"docs/ops/slos.md"}
RELEASE_FILES = {"CHANGELOG.md", ".github/workflows/release.yml"}
EVALS_FILES = {
    "evals/run_evals.py",
    "evals/graders/__init__.py",
    "evals/datasets/smoke.jsonl",
    "docs/evals/plan.md",
    "tests/unit/test_graders.py",
}
JUDGE_FILES = {
    "evals/graders/judge.py",
    "evals/calibration/example.jsonl",
    "tests/unit/test_judge.py",
}
PIPELINE_FILES = {"src/x/pipeline.py", "tests/unit/test_pipeline.py"}
HEXAGONAL_INITS = {f"src/x/{layer}/__init__.py" for layer in HEXAGONAL_LAYERS}
COLUMNS = HTTP_FILES | OPS_FILES | SLOS_FILES | EVALS_FILES | JUDGE_FILES
# The release files are owned by another ADR-0010 slice: the condition is checked by path.
RELEASE_TEMPLATES = ("CHANGELOG.md", "dot_github/workflows/release.yml.tmpl")


# Covers F-0004.R3 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_generates_exactly_its_column(kind: str) -> None:
    """ADR-0010 fitness: the plan holds what the table says for the kind and nothing else."""
    cfg = _axes(kind)
    plan = plan_files(cfg)
    expected = {
        "service": HTTP_FILES | OPS_FILES | SLOS_FILES,
        "library": set(),
        "cli": set(),
        "data": OPS_FILES | EVALS_FILES | PIPELINE_FILES,
    }[kind]
    assert plan.keys() & (COLUMNS | PIPELINE_FILES) == expected
    assert all(_included(rel, cfg) is (kind in ("library", "cli")) for rel in RELEASE_TEMPLATES)
    assert not plan.keys() & RELEASE_FILES or kind in ("library", "cli")
    if kind in ("library", "cli"):
        assert not any(p.startswith(("infra/", "docs/ops/", "src/x/api/")) for p in plan)
    data = tomllib.loads(str(plan["pyproject.toml"]))
    assert ("scripts" in data["project"]) is (kind == "cli")
    if kind == "cli":
        assert data["project"]["scripts"] == {"x": "x.__main__:main"}
    assert data["tool"]["rumbo"]["kind"] == kind
    assert data["tool"]["rumbo"]["layout"] == cfg.layout
    assert "layers" not in data["tool"]["rumbo"]
    checks = data["tool"]["rumbo"]["checks"]
    assert ("evals" in checks) is (kind == "data")
    targets = [ln.split(":")[0] for ln in str(plan["Makefile"]).splitlines() if ":" in ln]
    assert ("evals" in targets) is (kind == "data")
    assert "evals-live" not in targets  # no model: nothing to run live
    package_init = str(plan["src/x/__init__.py"])
    assert ('version("x")' in package_init and "__all__" in package_init) is (
        kind in ("library", "cli")
    )
    main = str(plan["src/x/__main__.py"])
    assert "def main() -> int:" in main and "configure_json_logging" not in main


# Covers F-0004.R3, F-0004.R5 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_data_gets_evals_without_a_model_and_the_judge_only_with_one() -> None:
    offline = plan_files(_axes("data"))
    assert offline.keys() >= EVALS_FILES and not JUDGE_FILES & offline.keys()
    assert "x.pipeline:run" in str(offline["evals/run_evals.py"])
    assert "src/x/app/entrypoint.py" not in offline
    with_model = plan_files(_axes("data", llm=True))
    assert with_model.keys() >= EVALS_FILES | JUDGE_FILES
    assert "x.app.entrypoint:answer" in str(with_model["evals/run_evals.py"])
    assert "src/x/pipeline.py" not in with_model
    assert "tests/unit/test_graders.py" not in plan_files(_axes("library"))


# Covers F-0004.R3 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_service_keeps_http_without_llm_and_logs_json_only_with_it() -> None:
    plain = plan_files(_axes("service"))
    http = str(plain["src/x/api/http.py"])
    assert "FastAPI" in http and "configure_json_logging" not in http and "telemetry" not in http
    assert "uvicorn" in str(plain["infra/Dockerfile"])
    assert "configure_json_logging()" in str(
        plan_files(_axes("service", llm=True))["src/x/api/http.py"]
    )
    assert '"python", "-m", "x"' in str(plan_files(_axes("data"))["infra/Dockerfile"])


# Covers F-0004.R4 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize(
    ("layout", "layers", "inner"),
    [("hexagonal", (), "domain"), ("layered", ("web", "core"), "core"), ("flat", (), "")],
)
def test_each_layout_has_its_contracts_or_none(
    layout: str, layers: tuple[str, ...], inner: str
) -> None:
    """ADR-0010 fitness: contracts, strict/only_mutate target and arch step follow the layout."""
    cfg = _axes("library", layout=layout, layers=layers)
    assert (cfg.arch, cfg.inner_layer) == (layout != "flat", inner)
    plan = plan_files(cfg)
    data = tomllib.loads(str(plan["pyproject.toml"]))
    tool = data["tool"]
    checks = tool["rumbo"]["checks"]
    makefile = str(plan["Makefile"])
    arch_test = str(plan["tests/architecture/test_architecture.py"])
    pre_commit = str(plan[".pre-commit-config.yaml"])
    nightly = str(plan[".github/workflows/nightly.yml"])
    assert "def test_src_does_not_import_spikes" in arch_test
    src_files = sorted(p for p in plan if p.startswith("src/x/"))
    if layout == "flat":
        assert "importlinter" not in tool and "mutmut" not in tool
        assert "strict" not in tool["pyright"]
        assert "arch" not in checks
        assert "arch:" not in makefile and "mutation:" not in makefile
        assert "_domain_violations" not in arch_test
        assert "lint-imports" not in pre_commit
        assert "mutation" not in nightly and "rumbo_check.py audit" in nightly
        assert src_files == ["src/x/__init__.py", "src/x/__main__.py"]
        return
    expected_layers = list(HEXAGONAL_LAYERS) if layout == "hexagonal" else list(layers)
    contracts = {c["type"]: c for c in tool["importlinter"]["contracts"]}
    assert contracts["layers"]["layers"] == [f"x.{layer}" for layer in expected_layers]
    assert contracts["forbidden"]["source_modules"] == [f"x.{inner}"]
    assert tool["pyright"]["strict"] == [f"src/x/{inner}"]
    assert tool["mutmut"]["only_mutate"] == [f"src/x/{inner}/*"]
    assert checks["arch"] == "uv run lint-imports"
    assert "arch:" in makefile and f"find src/x/{inner}" in makefile
    assert f'INNER = SRC / "{inner}"' in arch_test
    assert "lint-imports" in pre_commit
    production = plan_files(dataclasses.replace(cfg, rigor="production"))
    assert f"mutmut on src/x/{inner}" in str(production[".github/workflows/nightly.yml"])
    assert src_files == sorted(
        [
            "src/x/__init__.py",
            "src/x/__main__.py",
            *(f"src/x/{x}/__init__.py" for x in expected_layers),
        ]
    )
    if layout == "layered":
        assert tool["rumbo"]["layers"] == ["web", "core"]
        assert "may import only core" in str(plan["src/x/web/__init__.py"])
        assert "innermost" in str(plan["src/x/core/__init__.py"])
        for path in (f"src/x/{x}/__init__.py" for x in expected_layers):
            ast.parse(str(plan[path]), filename=path)


# Covers F-0004.R4 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_layout_defaults_follow_the_kind_and_llm_forces_hexagonal() -> None:
    assert _axes("service").layout == "hexagonal"
    assert all(_axes(kind).layout == "flat" for kind in ("library", "cli", "data"))
    forced = _axes("cli", layout="flat", llm=True)
    assert (forced.layout, forced.arch, forced.inner_layer) == ("hexagonal", True, "domain")
    assert plan_files(forced).keys() >= HEXAGONAL_INITS
    assert _axes("data", llm=True).layout == "hexagonal"


# Covers F-0004.R4 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "monolith"},
        {"kind": "library", "layout": "onion"},
        {"kind": "library", "layout": "layered"},  # no layers
        {"kind": "library", "layout": "layered", "layers": ("only",)},  # < 2
        {"kind": "library", "layout": "layered", "layers": ("Web", "core")},  # not snake_case
        {"kind": "library", "layout": "layered", "layers": ("core", "core")},  # duplicates
        {"kind": "library", "layout": "flat", "layers": ("web", "core")},  # layers need layered
        {"kind": "service", "layers": ("web", "core")},  # hexagonal has fixed layers
    ],
)
def test_axes_validation(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        Config(name="x", package="x", llm=False, **kwargs)


def test_an_adopted_project_is_given_no_runtime_dependency() -> None:
    """ADR-0029 fitness: the runtime dependencies serve the skeleton RUMBO writes. An adopted
    project gets no skeleton; a Flask or Django service was being handed FastAPI."""
    for kind, llm in (("service", False), ("service", True), ("cli", True), ("data", False)):
        provider = "anthropic" if llm else ""
        adopted = Config(
            name="Legacy",
            package="legacy_app",
            llm=llm,
            provider=provider,
            kind=kind,
            layout="flat",
            adopted=True,
        )
        assert runtime_dependencies(adopted) == []
        assert "pytest" in dev_dependencies(adopted)  # the tools of the harness still come
    fresh = Config(name="New", package="new_app", llm=False, kind="service")
    assert runtime_dependencies(fresh) == ["fastapi", "uvicorn", "httpx"]


# Covers F-0004.R3, F-0004.R4, F-0004.R5 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_service_dependencies_do_not_depend_on_llm() -> None:
    assert runtime_dependencies(_axes("service")) == ["fastapi", "uvicorn", "httpx"]
    assert runtime_dependencies(_axes("service", llm=True)) == [
        "fastapi",
        "uvicorn",
        "httpx",
        "pydantic",
        "anthropic",
    ]
    assert runtime_dependencies(_axes("cli", llm=True)) == ["pydantic", "anthropic"]
    assert runtime_dependencies(_axes("library")) == []
    assert "import-linter" in dev_dependencies(_axes("service"))
    assert "import-linter" not in dev_dependencies(_axes("cli"))
    assert dev_dependencies(_axes("cli", layout="hexagonal")) == dev_dependencies(_axes("service"))


# Covers F-0004.R11 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_project_load_infers_service_hexagonal_and_update_writes_the_keys(
    llm_project: Path,
) -> None:
    """ADR-0010 migration: older projects load unchanged; `rumbo update` makes the axes explicit."""
    pyproject = llm_project / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    assert 'kind = "service"' in original and 'layout = "hexagonal"' in original
    stripped = re.sub(r"(?m)^(kind|layout) = .*\n", "", original)
    pyproject.write_text(stripped, encoding="utf-8")
    try:
        rumbo = tomllib.loads(stripped)["tool"]["rumbo"]
        assert "kind" not in rumbo and "layout" not in rumbo
        project = Project.load(llm_project)
        assert (project.kind, project.layout, project.layers) == ("service", "hexagonal", ())
        assert (
            plan_files(Config.from_project(project)).keys()
            == plan_files(
                Config(
                    name="XM Agent", package="xm_agent", llm=True, provider="anthropic", rigor="mvp"
                )
            ).keys()
        )
        rep = update(project, llm_project)
        assert "pyproject.toml (+kind, layout)" in rep.merged
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        assert data["tool"]["rumbo"]["kind"] == "service"
        assert data["tool"]["rumbo"]["layout"] == "hexagonal"
        assert "layers" not in data["tool"]["rumbo"]
        assert data["tool"]["rumbo"]["checks"]  # the other keys and tables are untouched
        assert data["tool"]["rumbo"]["package"] == "xm_agent"
        # Idempotent: a second update adds nothing.
        rep = update(Project.load(llm_project), llm_project)
        assert not rep.merged
    finally:
        pyproject.write_text(original, encoding="utf-8")


# Covers F-0004.R11 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_update_writes_layers_for_a_layered_project(tmp_path: Path) -> None:
    cfg = _axes("library", layout="layered", layers=("web", "core"))
    scaffold(cfg, tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(re.sub(r"(?m)^(kind|layers) = .*\n", "", text), encoding="utf-8")
    # Without `layers`, a layered project does not load: `update` cannot run without the key…
    with pytest.raises(ValueError, match="at least two layers"):
        Project.load(tmp_path)
    # …but a Config rebuilt with the layers writes it (and kind) back, keeping `layout`.
    from rumbo.scaffold import _add_axes_keys

    report = Report()
    _add_axes_keys(pyproject, cfg, report)
    assert report.merged == ["pyproject.toml (+kind, layers)"]
    loaded = Project.load(tmp_path)
    assert (loaded.kind, loaded.layout, loaded.layers) == ("library", "layered", ("web", "core"))
    assert plan_files(Config.from_project(loaded)).keys() == plan_files(cfg).keys()


def test_config_round_trips_the_axes_through_project(tmp_path: Path) -> None:
    cfg = _axes("cli", llm=True)
    scaffold(cfg, tmp_path)
    loaded = Config.from_project(Project.load(tmp_path))
    assert (loaded.kind, loaded.layout, loaded.layers, loaded.llm) == ("cli", "hexagonal", (), True)
    assert plan_files(loaded).keys() == plan_files(cfg).keys()


# --------------------------------------------------------------------------- ADR-0011
BASE_TOOLS = ["ruff", "pyright", "pytest", "hypothesis", "pre-commit"]
MVP_TOOLS = ["pytest-cov", "bandit", "pip-audit"]


def _rigor(rigor: str, kind: str = "service", layout: str = "", llm: bool = False) -> Config:
    return Config(
        name="x",
        package="x",
        llm=llm,
        provider="anthropic" if llm else "",
        rigor=rigor,
        kind=kind,
        layout=layout,
    )


# Covers F-0004.R6 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_dev_dependencies_scale_with_rigor_kind_and_layout() -> None:
    """ADR-0011 fitness: a prototype installs what a prototype verifies; nothing is pinned."""
    assert dev_dependencies(_rigor("prototype")) == [*BASE_TOOLS, "import-linter"]
    assert dev_dependencies(_rigor("prototype", kind="cli")) == BASE_TOOLS  # flat: no contracts
    assert dev_dependencies(_rigor("mvp")) == [*BASE_TOOLS, "import-linter", *MVP_TOOLS]
    assert dev_dependencies(_rigor("mvp", kind="library")) == [*BASE_TOOLS, *MVP_TOOLS]
    assert dev_dependencies(_rigor("production")) == [
        *BASE_TOOLS,
        "import-linter",
        *MVP_TOOLS,
        "mutmut",
    ]
    assert dev_dependencies(_rigor("production", kind="data", layout="flat")) == [
        *BASE_TOOLS,
        *MVP_TOOLS,
        "mutmut",
    ]
    for rigor in ("prototype", "mvp", "production"):
        for kind in KINDS:
            deps = dev_dependencies(_rigor(rigor, kind=kind))
            assert len(deps) == len(set(deps)) and "pre-commit" in deps  # the guard's git hooks
            assert ("pytest-cov" in deps) is (rigor != "prototype")
            assert ("mutmut" in deps) is (rigor == "production")
            assert all(not re.search(r"[<>=!~]", dep) for dep in deps)  # `uv add` resolves
    # The LLM capability adds nothing to the development tools; the kind decides the runtime.
    assert dev_dependencies(_rigor("mvp", llm=True)) == dev_dependencies(_rigor("mvp"))
    assert runtime_dependencies(_rigor("prototype")) == ["fastapi", "uvicorn", "httpx"]
    assert runtime_dependencies(_rigor("production", kind="library")) == []


# Covers F-0004.R6 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize("kind", KINDS)
def test_check_steps_per_rigor(kind: str) -> None:
    """ADR-0011 fitness: security, security-scripts and audit arrive with mvp, in ADR-0006 order."""
    proto, mvp, prod = (
        check_steps(_rigor(r, kind=kind)) for r in ("prototype", "mvp", "production")
    )
    assert not set(SECURITY_STEPS) & set(proto)
    assert set(SECURITY_STEPS) <= set(mvp) and mvp == prod
    assert [s for s in mvp if s not in SECURITY_STEPS] == proto  # the rest is unchanged
    assert mvp == expected_steps(_rigor("mvp", kind=kind))
    assert mvp.index("audit") < mvp.index("sensor")
    if kind == "data":
        assert mvp.index("audit") < mvp.index("evals") < mvp.index("sensor")


def _dev_group(pyproject: Path, deps: list[str]) -> None:
    group = "\n[dependency-groups]\ndev = [" + ", ".join(json.dumps(d) for d in deps) + "]\n"
    pyproject.write_text(pyproject.read_text(encoding="utf-8") + group, encoding="utf-8")


# Covers F-0004.R11 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_update_appends_the_steps_of_a_raised_rigor_once(simple_project: Path) -> None:
    """ADR-0011 fitness: a hand-raised rigor gets its steps appended once, plus the uv line."""
    pyproject = simple_project / "pyproject.toml"
    before = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["rumbo"]["checks"]
    assert list(before) == ["lint", "format", "types", "test", "arch", "sensor"]
    _dev_group(
        pyproject, ["ruff>=0.5", "Pyright", "pytest", "hypothesis", "pre_commit", "import-linter"]
    )
    pyproject.write_text(set_rigor(pyproject.read_text(encoding="utf-8"), "mvp"), encoding="utf-8")
    assert Project.load(simple_project).rigor == "mvp"
    assert not (simple_project / ".github" / "workflows" / "nightly.yml").exists()

    rep = update(Project.load(simple_project), simple_project)
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    checks = data["tool"]["rumbo"]["checks"]
    # Appended at the end of the table, in template order; the existing steps are untouched.
    assert list(checks) == [*before, *SECURITY_STEPS]
    assert {k: checks[k] for k in before} == before  # `test` keeps "uv run pytest": never replaced
    template = tomllib.loads(str(plan_files(_rigor("mvp"))["pyproject.toml"]))
    assert {k: checks[k] for k in SECURITY_STEPS} == {
        k: template["tool"]["rumbo"]["checks"][k] for k in SECURITY_STEPS
    }
    assert "pyproject.toml (+checks: security, security-scripts, audit)" in rep.merged
    assert data["tool"]["ruff"]["line-length"] == 100  # the rest of the file is intact
    # The level's files are rendered; the tools it needs are suggested (versions ignored).
    assert (
        ".github/workflows/nightly.yml" in rep.created and ".github/dependabot.yml" in rep.created
    )
    assert rep.suggested == ["uv add --dev pytest-cov bandit pip-audit"]
    assert (
        "Run to complete the update:\n  uv add --dev pytest-cov bandit pip-audit" in rep.summary()
    )
    # Idempotent: the second update adds and suggests nothing new once the tools are listed.
    text = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(
        text.replace(
            '"import-linter"]', '"import-linter", "pytest-cov>=5", "bandit[toml]", "pip-audit"]'
        ),
        encoding="utf-8",
    )
    rep = update(Project.load(simple_project), simple_project)
    assert not rep.merged and not rep.suggested and not rep.created
    assert list(
        tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["rumbo"]["checks"]
    ) == [
        *before,
        *SECURITY_STEPS,
    ]


# Covers F-0004.R11 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_missing_dev_dependencies_reads_both_groups_and_ignores_versions() -> None:
    cfg = _rigor("production", kind="library")  # flat: no import-linter
    base = 'name = "x"\n[tool.rumbo]\npackage = "x"\n'
    assert missing_dev_dependencies(cfg, base) == dev_dependencies(cfg)
    pep735 = base + '[dependency-groups]\ndev = ["Ruff==0.8", "pyright", "PyTest", "mutmut"]\n'
    assert missing_dev_dependencies(cfg, pep735) == ["hypothesis", "pre-commit", *MVP_TOOLS]
    legacy = base + '[tool.uv]\ndev-dependencies = ["pre_commit>=3", "hypothesis", "pip_audit"]\n'
    assert missing_dev_dependencies(cfg, legacy) == [
        "ruff",
        "pyright",
        "pytest",
        "pytest-cov",
        "bandit",
        "mutmut",
    ]
    assert dependency_name("Foo_Bar.baz[extra] >= 1.0; python_version > '3'") == "foo-bar-baz"
    assert dependency_name("") == ""


# Covers F-0004.R13 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_set_rigor_rewrites_only_the_key() -> None:
    cfg = Config(
        name="x",
        package="x",
        llm=False,
        rigor="prototype",
        kind="library",
        layout="layered",
        layers=("web", "core"),
    )
    text = str(plan_files(cfg)["pyproject.toml"])
    assert 'layers = ["web", "core"]' in text and 'rigor = "prototype"' in text  # `[` before rigor
    new = set_rigor(text, "production")
    assert new == text.replace('rigor = "prototype"', 'rigor = "production"')  # one line changed
    assert tomllib.loads(new)["tool"]["rumbo"]["rigor"] == "production"
    # A project without the key (defaults to mvp when loading) gets it added inside [tool.rumbo].
    without = text.replace('rigor = "prototype"\n', "")
    added = set_rigor(without, "mvp")
    assert tomllib.loads(added)["tool"]["rumbo"]["rigor"] == "mvp"
    assert added.index('rigor = "mvp"') < added.index("[tool.rumbo.checks]")
    with pytest.raises(ValueError, match="invalid rigor"):
        set_rigor(text, "ultimate")
    with pytest.raises(ValueError, match=re.escape("has no [tool.rumbo] section")):
        set_rigor('[project]\nname = "x"\n', "mvp")


# ------------------------------------------------------------- ADR-0013: adoption
LEGACY_PYPROJECT = (
    '[project]\nname = "legacy-app"\nversion = "0.1.0"\ndependencies = []\n\n'
    '[project.scripts]\nlegacy = "legacy_app:main"\n'
)


def _legacy(root: Path, pyproject: str = LEGACY_PYPROJECT, *files: str) -> Path:
    """A pre-existing project: a pyproject and empty files at the given relative paths."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    return root


def _adopted(
    tests_dir: str = "test", kind: str = "cli", layout: str = "", llm: bool = False
) -> Config:
    return Config(
        name="Legacy",
        package="legacy_app",
        llm=llm,
        provider="anthropic" if llm else "",
        rigor="mvp",
        kind=kind,
        layout=layout,
        adopted=True,
        tests_dir=tests_dir,
    )


# Covers F-0004.R9 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_detect_package_from_the_project_name(tmp_path: Path) -> None:
    """ADR-0013: `[project].name` normalised to snake_case when its directory exists."""
    root = _legacy(tmp_path / "a", LEGACY_PYPROJECT, "src/legacy_app/__init__.py")
    assert detect_package(root, read_pyproject(root)) == "legacy_app"
    root = _legacy(tmp_path / "b", LEGACY_PYPROJECT, "legacy_app/__init__.py")  # no src/
    assert detect_package(root, read_pyproject(root)) == "legacy_app"
    dotted = LEGACY_PYPROJECT.replace("legacy-app", "Legacy.App")
    root = _legacy(tmp_path / "c", dotted, "src/legacy_app/__init__.py")
    assert detect_package(root, read_pyproject(root)) == "legacy_app"


# Covers F-0004.R9 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_detect_package_from_the_single_directory(tmp_path: Path) -> None:
    """ADR-0013: without a matching name, the one directory with an __init__.py that is not a
    harness folder (tests, test, docs, scripts, spikes, evals, infra, src)."""
    harness = [f"{d}/__init__.py" for d in ("tests", "test", "docs", "scripts", "spikes", "evals")]
    root = _legacy(tmp_path / "a", LEGACY_PYPROJECT, "mypkg/__init__.py", *harness)
    assert detect_package(root, read_pyproject(root)) == "mypkg"
    root = _legacy(tmp_path / "b", LEGACY_PYPROJECT, "src/other/__init__.py", "src/__init__.py")
    assert detect_package(root, read_pyproject(root)) == "other"
    root = _legacy(tmp_path / "c", LEGACY_PYPROJECT, "docs/__init__.py", "plain/module.py")
    assert detect_package(root, read_pyproject(root)) is None
    assert detect_package(tmp_path / "d", {}) is None  # no pyproject, no directories


# Covers F-0004.R9 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_detect_package_ambiguity_asks_for_the_flag(tmp_path: Path) -> None:
    root = _legacy(tmp_path, LEGACY_PYPROJECT, "alpha/__init__.py", "src/beta/__init__.py")
    with pytest.raises(ValueError, match=r"alpha, beta.*--package"):
        detect_package(root, read_pyproject(root))
    # The flag skips the detection (and so the error).
    assert detect_project(root, "beta").package == "beta"


# Covers F-0004.R9 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize(
    ("pyproject", "kind"),
    [
        (LEGACY_PYPROJECT, "cli"),
        ('[project]\nname = "x"\ndependencies = ["FastAPI>=0.100"]\n', "service"),
        ('[project]\nname = "x"\ndependencies = ["requests", "Flask"]\n', "service"),
        ('[project]\nname = "x"\ndependencies = ["Django>=4,<6"]\n', "service"),
        (
            '[project]\nname = "x"\ndependencies = ["starlette[full] ; python_version>\'3\'"]\n',
            "service",
        ),
        (
            '[project]\nname = "x"\ndependencies = ["fastapi"]\n[project.scripts]\nx = "x:m"\n',
            "cli",
        ),
        ('[project]\nname = "x"\ndependencies = ["requests"]\n', "library"),
        (
            '[project]\nname = "x"\ndependencies = []\n\n[project.scripts]\n',
            "library",
        ),  # empty table
        ("", "library"),
    ],
    ids=[
        "scripts",
        "fastapi",
        "flask",
        "django",
        "starlette",
        "scripts-win",
        "plain",
        "no-scripts",
        "empty",
    ],
)
def test_detect_kind(pyproject: str, kind: str) -> None:
    assert detect_kind(tomllib.loads(pyproject)) == kind


# Covers F-0004.R9 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_detect_layout(tmp_path: Path) -> None:
    hexagonal = _legacy(
        tmp_path / "hex",
        LEGACY_PYPROJECT,
        "src/legacy_app/domain/__init__.py",
        "src/legacy_app/app/__init__.py",
    )
    assert detect_layout(hexagonal, "legacy_app") == "hexagonal"
    half = _legacy(tmp_path / "half", LEGACY_PYPROJECT, "src/legacy_app/domain/__init__.py")
    assert detect_layout(half, "legacy_app") == "flat"
    assert (
        detect_layout(
            _legacy(tmp_path / "flat", LEGACY_PYPROJECT, "src/legacy_app/__init__.py"), "legacy_app"
        )
        == "flat"
    )


# Covers F-0004.R9 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_detect_tests_dir(tmp_path: Path) -> None:
    assert detect_tests_dir(tmp_path) == "tests"  # nothing yet: the default
    (tmp_path / "test").mkdir()
    assert detect_tests_dir(tmp_path) == "test"
    (tmp_path / "tests").mkdir()
    assert detect_tests_dir(tmp_path) == "tests"  # `tests` wins when both exist


# Covers F-0004.R9 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_detect_project_puts_it_together(tmp_path: Path) -> None:
    root = _legacy(tmp_path, LEGACY_PYPROJECT, "src/legacy_app/__init__.py", "test/test_x.py")
    detected = detect_project(root)
    assert (detected.name, detected.package, detected.kind) == ("legacy-app", "legacy_app", "cli")
    assert (detected.layout, detected.tests_dir) == ("flat", "test")
    assert detected.summary() == "package=legacy_app kind=cli layout=flat tests=test"
    bare = tmp_path / "bare"
    bare.mkdir()
    assert detect_project(bare) == Detected(
        name="bare", package=None, kind="library", layout="flat", tests_dir="tests"
    )


# Covers F-0004.R10 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_adoption_plan_writes_the_harness_and_no_skeleton() -> None:
    """ADR-0013 fitness: nothing under src/, only two test files, `adopted = true`, `{changed}`."""
    cfg = _adopted()
    plan = plan_files(cfg)
    assert not [rel for rel in plan if rel.startswith("src/")]
    assert not [rel for rel in plan if rel.startswith("tests/")]  # the detected directory is `test`
    assert sorted(rel for rel in plan if rel.startswith("test/")) == [
        "test/architecture/test_architecture.py",
        "test/unit/test_check_tests_not_weakened.py",
    ]
    for rel in (
        "AGENTS.md",
        "Makefile",
        "scripts/rumbo_check.py",
        "RUMBO.md",
        ".github/workflows/ci.yml",
    ):
        assert rel in plan
    pyproject = str(plan["pyproject.toml"])
    rumbo = tomllib.loads(pyproject)["tool"]["rumbo"]
    assert rumbo["adopted"] is True and rumbo["tests"] == "test"
    checks = rumbo["checks"]
    assert checks["lint"] == "uv run ruff check {changed}"
    assert checks["format"] == "uv run ruff format --check {changed}"
    assert checks["types"] == "uv run pyright {changed}"
    # ADR-0029: bandit too judges the files a change touches, not the legacy tree.
    assert checks["security"] == "uv run bandit -c pyproject.toml -q -r {changed}"
    plain = _rendered_checks_of(
        Config(name="Legacy", package="legacy_app", llm=False, rigor="mvp", kind="cli")
    )
    on_changed = ("lint", "format", "types", "security")
    assert {k: v for k, v in checks.items() if k not in on_changed} == {
        k: v for k, v in plain.items() if k not in on_changed
    }
    assert list(checks) == list(plain) == check_steps(cfg)
    assert "--full" in pyproject and "{changed}` is the changed .py files" in pyproject
    # The tool tables follow the detected tests directory.
    tool = tomllib.loads(pyproject)["tool"]
    assert tool["pytest"]["ini_options"]["testpaths"] == ["test"]
    assert tool["pyright"]["include"] == ["src", "test"]
    assert "test/**" in tool["ruff"]["lint"]["per-file-ignores"]
    # AGENTS.md says it in one line; the nightly measures the whole tree.
    agents = str(plan["AGENTS.md"])
    full_lines = [ln for ln in agents.splitlines() if "--full" in ln]
    assert full_lines == [
        "uv run python scripts/rumbo_check.py --full   # whole tree (legacy debt); CI and the hook "
        "check only what you changed"
    ]
    assert len(agents.splitlines()) < 100
    nightly = str(plan[".github/workflows/nightly.yml"])
    assert "  full-check:" in nightly
    assert "run: uv run python scripts/rumbo_check.py --full" in nightly
    assert (
        nightly.count("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1") == 2
    )  # audit + full


def _rendered_checks_of(cfg: Config) -> dict[str, str]:
    return tomllib.loads(str(plan_files(cfg)["pyproject.toml"]))["tool"]["rumbo"]["checks"]


# Covers F-0004.R10 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_adoption_with_the_default_tests_dir_writes_no_tests_key() -> None:
    plan = plan_files(_adopted(tests_dir="tests"))
    rumbo = tomllib.loads(str(plan["pyproject.toml"]))["tool"]["rumbo"]
    assert rumbo["adopted"] is True and "tests" not in rumbo
    assert sorted(rel for rel in plan if rel.startswith("tests/")) == [
        "tests/architecture/test_architecture.py",
        "tests/unit/test_check_tests_not_weakened.py",
    ]


# Covers F-0004.R10 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_adoption_keeps_the_purity_rules_when_the_layout_has_layers() -> None:
    plan = plan_files(_adopted(kind="service", layout="hexagonal"))
    arch = str(plan["test/architecture/test_architecture.py"])
    assert "FORBIDDEN_IN_INNER" in arch and "spikes" in arch
    assert not [rel for rel in plan if rel.startswith("src/")]
    assert "infra/Dockerfile" in plan  # the kind has it and no root was given


# Covers F-0004.R10 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_init_plan_is_unchanged_without_adoption() -> None:
    cfg = Config(name="Legacy", package="legacy_app", llm=False, rigor="mvp", kind="cli")
    plan = plan_files(cfg)
    pyproject = str(plan["pyproject.toml"])
    rumbo = tomllib.loads(pyproject)["tool"]["rumbo"]
    assert "adopted" not in rumbo and "tests" not in rumbo
    assert rumbo["checks"]["lint"] == "uv run ruff check ." and "{changed}" not in pyproject
    assert "full-check" not in str(plan[".github/workflows/nightly.yml"])
    assert "--full" not in str(plan["AGENTS.md"])
    assert "src/legacy_app/__main__.py" in plan and "tests/unit/test_smoke.py" in plan


# Covers F-0004.R10 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_adoption_skips_ci_when_a_workflow_exists_and_prints_the_steps(tmp_path: Path) -> None:
    """ADR-0013: an existing workflow is the human's; the report lists the two steps to add."""
    cfg = _adopted()
    root = _legacy(
        tmp_path, LEGACY_PYPROJECT, "src/legacy_app/__init__.py", ".github/workflows/existing.yaml"
    )
    assert existing_workflows(root) == ["existing.yaml"]
    assert ".github/workflows/ci.yml" not in plan_files(cfg, root)
    assert ".github/workflows/ci.yml" in plan_files(cfg)  # without a root nothing is known
    report = scaffold(cfg, root)
    assert not (root / ".github" / "workflows" / "ci.yml").exists()
    assert report.notes == [CI_STEPS_NOTE]
    summary = report.summary()
    assert "ci.yml was not written" in summary
    assert "uv run python scripts/rumbo_check.py" in summary
    assert "uv run python scripts/check_tests_not_weakened.py --strict" in summary
    assert "RUMBO_BASE_REF" in summary
    # Without a workflow, ci.yml is written as for any project and there is nothing to note.
    other = _legacy(tmp_path / "other", LEGACY_PYPROJECT, "src/legacy_app/__init__.py")
    report = scaffold(cfg, other)
    assert (other / ".github" / "workflows" / "ci.yml").is_file() and report.notes == []


# Covers F-0004.R10 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_adoption_skips_infra_when_the_directory_exists(tmp_path: Path) -> None:
    cfg = _adopted(kind="service", layout="flat")
    root = _legacy(tmp_path, LEGACY_PYPROJECT, "src/legacy_app/__init__.py", "infra/k8s.yaml")
    assert not [rel for rel in plan_files(cfg, root) if rel.startswith("infra/")]
    assert "infra/Dockerfile" in plan_files(cfg, tmp_path / "elsewhere")
    scaffold(cfg, root)
    assert not (root / "infra" / "Dockerfile").exists()
    assert (root / "docs" / "ops" / "runbook.md").is_file()  # the other ops files still come


# Covers F-0004.R10, F-0004.R11 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_adopted_project_round_trips_and_updates_without_a_skeleton(tmp_path: Path) -> None:
    cfg = _adopted()
    root = _legacy(
        tmp_path,
        LEGACY_PYPROJECT,
        "src/legacy_app/__init__.py",
        "test/test_x.py",
        ".github/workflows/existing.yml",
    )
    scaffold(cfg, root)
    project = Project.load(root)
    assert (project.adopted, project.tests_dir, project.kind) == (True, "test", "cli")
    again = Config.from_project(project)
    assert (again.adopted, again.tests_dir) == (True, "test")
    assert not (root / "src" / "legacy_app" / "__main__.py").exists()
    assert not (root / "tests").exists()
    report = update(project, root)
    assert not [
        rel for rel in report.created + report.differs if rel.startswith(("src/", "tests/"))
    ]
    assert not any("ci.yml" in rel for rel in report.differs)
    assert not (root / "src" / "legacy_app" / "__main__.py").exists()


# ------------------------------------------- the layout the project already has (ADR-0020)
# Covers F-0004.R9, F-0004.R10, F-0004.R11
# (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_adopting_a_root_package_points_every_tool_at_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0020 fitness: a package at the root (no `src/`) and tests in `test/` are adopted as
    they are; `[tool.rumbo].src` records it and every tool is pointed at the package."""
    from rumbo.cli import main

    root = _legacy(
        tmp_path / "legacy", LEGACY_PYPROJECT, "legacy_app/__init__.py", "test/test_x.py"
    )
    detected = detect_project(root)
    assert (detected.package, detected.src, detected.tests_dir) == ("legacy_app", ".", "test")
    assert detected.summary() == "package=legacy_app kind=cli layout=flat tests=test src=."
    adopt = ["init", "--adopt", "--yes", "--no-install", "--no-llm", "--rigor", "mvp"]
    assert main([*adopt, "--dir", str(root)]) == 0
    assert "adopted=true tests=test src=.\n" in capsys.readouterr().out

    # The key, and the project state that reads it.
    tool = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]
    assert tool["rumbo"]["src"] == "." and tool["rumbo"]["tests"] == "test"
    project = Project.load(root)
    assert (project.src, project.code_root, project.package_path) == (
        ".",
        "legacy_app",
        "legacy_app",
    )
    cfg = Config.from_project(project)
    assert (cfg.src, cfg.code_root, cfg.package_path) == (".", "legacy_app", "legacy_app")
    # The checks table, the type checker, coverage: the package, never `src`. Bandit
    # judges the changed files of an adopted project, like the other strict tools.
    assert tool["rumbo"]["checks"]["security"] == "uv run bandit -c pyproject.toml -q -r {changed}"
    # Globs: a plain name is matched anywhere in a path by bandit (measured).
    assert tool["bandit"]["exclude_dirs"] == [
        f"{prefix}{name}/*"
        for name in (".venv", "scripts", "spikes", "test")
        for prefix in ("./", "")
    ]
    assert tool["pyright"]["include"] == ["legacy_app", "test"]
    assert tool["coverage"]["run"]["source"] == ["legacy_app"]
    assert tool["ruff"]["force-exclude"] is True and tool["ruff"]["extend-exclude"] == ["spikes"]
    rendered = (root / "pyproject.toml").read_text(encoding="utf-8")
    settings = [ln for ln in rendered.splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in settings if '"src' in ln or "src/" in ln or " src" in ln]
    # The architecture test looks at the package where it is, and finds what is wrong there.
    arch = root / "test" / "architecture" / "test_architecture.py"
    text = arch.read_text(encoding="utf-8")
    assert 'SRC = ROOT / "legacy_app"\n' in text and '"src"' not in text
    assert "- Nothing in legacy_app/ imports spikes/." in text
    spec = importlib.util.spec_from_file_location("root_package_arch_test", arch)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    assert root / "legacy_app" == loaded.SRC
    loaded.test_src_does_not_import_spikes()
    (root / "legacy_app" / "bad.py").write_text("import spikes\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        loaded.test_src_does_not_import_spikes()
    # What the harness tells the agent follows too.
    assert "- Package: legacy_app (flat" in (root / "AGENTS.md").read_text(encoding="utf-8")

    # A raised rigor appends its steps with the same paths; `update` is otherwise a no-op.
    assert not update(project, root).merged
    proto = _legacy(tmp_path / "proto", LEGACY_PYPROJECT, "legacy_app/__init__.py")
    assert main([*adopt[:-1], "prototype", "--dir", str(proto)]) == 0
    pyproject = proto / "pyproject.toml"
    pyproject.write_text(set_rigor(pyproject.read_text(encoding="utf-8"), "mvp"), encoding="utf-8")
    report = update(Project.load(proto), proto)
    assert "pyproject.toml (+checks: security, security-scripts, audit)" in report.merged
    checks = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["rumbo"]["checks"]
    assert checks["security"] == "uv run bandit -c pyproject.toml -q -r {changed}"

    # A project adopted before the key existed: `update` looks once and writes what it finds.
    pyproject.write_text(
        re.sub(r'(?m)^src = "\."\n', "", pyproject.read_text(encoding="utf-8")), encoding="utf-8"
    )
    older = Project.load(proto)
    assert older.src == "src"  # a project without the key loads as "src"
    assert "pyproject.toml (+src)" in update(older, proto).merged
    assert Project.load(proto).src == "."
    assert not update(Project.load(proto), proto).merged  # once

    # With layers and with mypy, the strict layer and the mutation paths follow the package.
    layered = Config(
        name="Legacy",
        package="legacy_app",
        llm=False,
        rigor="production",
        kind="service",
        adopted=True,
        tests_dir="test",
        src=".",
    )
    tool = tomllib.loads(str(plan_files(layered)["pyproject.toml"]))["tool"]
    assert tool["pyright"]["strict"] == ["legacy_app/domain"]
    assert tool["mutmut"]["source_paths"] == ["legacy_app/"]
    assert tool["mutmut"]["only_mutate"] == ["legacy_app/domain/*"]
    mypy = dataclasses.replace(layered, types="mypy")
    tool = tomllib.loads(str(plan_files(mypy)["pyproject.toml"]))["tool"]
    assert tool["mypy"]["files"] == ["legacy_app", "test"]
    assert tool["mypy"]["overrides"][0]["module"] == "legacy_app.domain.*"
    # The layout is detected where the package is.
    hexagonal = _legacy(
        tmp_path / "hex",
        LEGACY_PYPROJECT,
        "legacy_app/__init__.py",
        "legacy_app/domain/__init__.py",
        "legacy_app/app/__init__.py",
    )
    assert detect_layout(hexagonal, "legacy_app") == "hexagonal"


# Covers F-0004.R10, F-0004.R11 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_a_src_project_renders_as_before(simple_project: Path, tmp_path: Path) -> None:
    """ADR-0020 fitness: with the package under `src/` (every `rumbo init`, and an adopted
    project that has it there) the configuration names the same paths it always did."""
    for cfg in (
        Config(name="Demo", package="demo", llm=False, rigor="production"),
        Config(name="Demo", package="demo", llm=False, rigor="production", adopted=True),
    ):
        assert (cfg.src, cfg.code_root, cfg.package_path) == ("src", "src", "src/demo")
        plan = plan_files(cfg)
        pyproject = str(plan["pyproject.toml"])
        scanned = "{changed}" if cfg.adopted else "src"  # ADR-0029: the files of a change
        for literal in (
            f'security = "uv run bandit -c pyproject.toml -q -r {scanned}"\n',
            'include = ["src", "tests"]\n',
            'strict = ["src/demo/domain"]\n',
            'source = ["src/demo"]\n',
            'source_paths = ["src/"]\n',
            'only_mutate = ["src/demo/domain/*"]\n',
            'packages = ["src/demo"]\n',
            '\nsrc = "src"\n',
        ):
            assert literal in pyproject, literal
        arch = str(plan["tests/architecture/test_architecture.py"])
        assert 'SRC = ROOT / "src" / "demo"\n' in arch
        assert "- Nothing in src/ imports spikes/." in arch
        assert "- Layers: src/demo/{domain,app,adapters,api}" in str(plan["AGENTS.md"])
    mypy = Config(name="Demo", package="demo", llm=False, rigor="mvp", types="mypy")
    assert 'files = ["src", "tests"]\n' in str(plan_files(mypy)["pyproject.toml"])
    llm = Config(name="Demo", package="demo", llm=True, provider="anthropic", rigor="mvp")
    security = 'security = "uv run bandit -c pyproject.toml -q -r src evals"\n'
    assert security in str(plan_files(llm)["pyproject.toml"])
    runner = str(plan_files(llm)["evals/run_evals.py"])
    assert 'prompts = root / "src" / "demo" / "prompts"\n' in runner
    # The one new line of `[tool.ruff]`: a project's exclusions hold for a file named by path.
    ruff = tomllib.loads(str(plan_files(mypy)["pyproject.toml"]))["tool"]["ruff"]
    assert ruff["force-exclude"] is True and ruff["extend-exclude"] == ["spikes"]

    # An adopted project with its package under src/ is detected as such, and says nothing new.
    legacy = _legacy(tmp_path / "legacy", LEGACY_PYPROJECT, "src/legacy_app/__init__.py")
    assert detect_project(legacy).src == "src"
    assert "src=" not in detect_project(legacy).summary()

    # A project generated before the key existed loads as "src"; `update` writes it, once.
    pyproject_file = simple_project / "pyproject.toml"
    original = pyproject_file.read_text(encoding="utf-8")
    assert tomllib.loads(original)["tool"]["rumbo"]["src"] == "src"
    try:
        pyproject_file.write_text(re.sub(r"(?m)^src = .*\n", "", original), encoding="utf-8")
        project = Project.load(simple_project)
        assert (project.src, project.code_root, project.package_path) == ("src", "src", "src/demo")
        assert "pyproject.toml (+src)" in update(project, simple_project).merged
        rumbo = tomllib.loads(pyproject_file.read_text(encoding="utf-8"))["tool"]["rumbo"]
        assert rumbo["src"] == "src"
        assert not update(Project.load(simple_project), simple_project).merged
        # Only "src" and "." are directories the harness knows how to point the tools at.
        pyproject_file.write_text(original.replace('src = "src"', 'src = "lib"'), encoding="utf-8")
        with pytest.raises(ValueError, match=r"\[tool\.rumbo\]\.src"):
            Project.load(simple_project)
    finally:
        pyproject_file.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match=r"\[tool\.rumbo\]\.src"):
        Config(name="Demo", package="demo", llm=False, src="lib")


# Covers F-0004.R10 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_target_path_follows_the_tests_dir() -> None:
    cfg = _adopted(tests_dir="test")
    assert target_path("tests/unit/test_x.py.tmpl", cfg) == "test/unit/test_x.py"
    assert (
        target_path("tests/architecture/test_architecture.py.tmpl", cfg)
        == "test/architecture/test_architecture.py"
    )
    assert (
        target_path("src/__pkg__/tests.py", cfg) == "src/legacy_app/tests.py"
    )  # only the first part
    assert (
        target_path("tests/unit/test_x.py", Config(name="x", package="x", llm=False))
        == "tests/unit/test_x.py"
    )


@pytest.mark.parametrize("tests_dir", ["", "../x", "a/b", "tests dir", "x" * 51])
def test_invalid_tests_dir(tests_dir: str) -> None:
    with pytest.raises(ValueError, match="tests directory"):
        Config(name="x", package="x", llm=False, adopted=True, tests_dir=tests_dir)


# --------------------------------------------------------------------------- ADR-0014
def _python_places(cfg: Config) -> dict[str, str]:
    """What each place that names a Python version renders to."""
    plan = plan_files(cfg)
    pyproject = str(plan["pyproject.toml"])
    requires = re.search(r'(?m)^requires-python = "(.*)"$', pyproject)
    target = re.search(r'(?m)^target-version = "(.*)"$', pyproject)
    ci = str(plan.get(".github/workflows/ci.yml", ""))
    matrix = re.search(r"(?m)^\s+python: (\[.*\])$", ci)
    dockerfile = re.search(r"(?m)^ARG PYTHON_VERSION=(.*)$", str(plan.get("infra/Dockerfile", "")))
    assert requires and target
    return {
        "requires": requires.group(1),
        "target": target.group(1),
        "rumbo": tomllib.loads(pyproject)["tool"]["rumbo"]["python"],
        "matrix": matrix.group(1) if matrix else "",
        "dockerfile": dockerfile.group(1) if dockerfile else "",
        "pin": str(plan[".python-version"]),
    }


# Covers F-0004.R14 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_python_setting_renders_every_place_that_names_a_version() -> None:
    library = _python_places(
        Config(name="x", package="x", llm=False, kind="library", python="3.12")
    )
    assert library == {
        "requires": ">=3.12",
        "target": "py312",
        "rumbo": "3.12",
        "matrix": '["3.12", "3.13"]',
        "dockerfile": "",  # a library ships no image
        "pin": "3.12\n",
    }
    service = _python_places(
        Config(name="x", package="x", llm=False, kind="service", python="3.12")
    )
    assert service["dockerfile"] == "3.12" and service["matrix"] == ""  # single-version jobs
    newest = _python_places(Config(name="x", package="x", llm=False, kind="cli", python="3.13"))
    assert newest["matrix"] == '["3.13"]' and newest["target"] == "py313"


# Covers F-0004.R14 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_python_default_is_unchanged() -> None:
    cfg = Config(name="x", package="x", llm=False, kind="cli")
    assert cfg.python == "3.11"
    places = _python_places(cfg)
    assert (places["requires"], places["target"], places["rumbo"]) == (">=3.11", "py311", "3.11")
    assert places["matrix"] == '["3.11", "3.12", "3.13"]' and places["pin"] == "3.11\n"
    data = _python_places(Config(name="x", package="x", llm=False, kind="data"))
    assert data["dockerfile"] == "3.11"  # the image builds on the minimum the project promises


# Covers F-0004.R14 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize("python", ["3.10", "3.99", "3", "py311", "3.11.4"])
def test_unsupported_python_is_refused(python: str) -> None:
    with pytest.raises(ValueError, match=r"unsupported Python .*3\.11, 3\.12, 3\.13"):
        Config(name="x", package="x", llm=False, python=python)


# Covers F-0004.R14 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_project_load_reads_the_python_key_and_validates_it(tmp_path: Path) -> None:
    scaffold(Config(name="x", package="x", llm=False, kind="library", python="3.12"), tmp_path)
    project = Project.load(tmp_path)
    assert project.python == "3.12"
    assert Config.from_project(project).python == "3.12"
    assert plan_files(Config.from_project(project)) == plan_files(
        Config(name="x", package="x", llm=False, kind="library", python="3.12")
    )
    pyproject = tmp_path / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(text.replace('python = "3.12"', 'python = "3.10"'), encoding="utf-8")
    with pytest.raises(ValueError, match=r"unsupported Python '3.10'"):
        Project.load(tmp_path)


# Covers F-0004.R11, F-0004.R14 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_project_load_defaults_python_and_update_writes_the_key_once(simple_project: Path) -> None:
    """ADR-0014 migration: older projects load as 3.11; `rumbo update` writes the promise."""
    pyproject = simple_project / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    assert 'python = "3.11"' in original
    stripped = re.sub(r"(?m)^python = .*\n", "", original)
    pyproject.write_text(stripped, encoding="utf-8")
    assert "python" not in tomllib.loads(stripped)["tool"]["rumbo"]
    project = Project.load(simple_project)
    assert project.python == "3.11"
    rep = update(project, simple_project)
    assert "pyproject.toml (+python)" in rep.merged
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["tool"]["rumbo"]["python"] == "3.11"
    assert data["tool"]["rumbo"]["checks"] and data["tool"]["rumbo"]["package"] == "demo"
    # Idempotent: a second update adds nothing; the key is written exactly once.
    rep = update(Project.load(simple_project), simple_project)
    assert not rep.merged
    assert pyproject.read_text(encoding="utf-8").count("\npython = ") == 1


# Covers F-0004.R8, F-0004.R14 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_an_existing_python_version_file_is_kept(tmp_path: Path) -> None:
    (tmp_path / ".python-version").write_text("3.13\n", encoding="utf-8")
    rep = scaffold(Config(name="x", package="x", llm=False, python="3.11"), tmp_path)
    assert ".python-version" in rep.skipped
    assert (tmp_path / ".python-version").read_text(encoding="utf-8") == "3.13\n"
    # `update` neither creates nor reports it: the pin is the project's own file.
    (tmp_path / ".python-version").unlink()
    rep = update(Project.load(tmp_path), tmp_path)
    assert not (tmp_path / ".python-version").exists()
    assert not any(".python-version" in x for x in rep.created + rep.differs)


# --------------------------------------------------------------- ADR-0015: type checker
# The flags of mypy --strict that work per module; `strict = true` itself applies globally.
MYPY_STRICT_FLAGS = {
    "disallow_any_generics",
    "disallow_subclassing_any",
    "disallow_untyped_calls",
    "disallow_untyped_defs",
    "disallow_incomplete_defs",
    "disallow_untyped_decorators",
    "warn_return_any",
    "no_implicit_reexport",
    "strict_equality",
    "extra_checks",
}


def _types(types: str, kind: str = "service", layout: str = "", llm: bool = False) -> Config:
    return Config(
        name="x",
        package="x",
        llm=llm,
        provider="anthropic" if llm else "",
        rigor="mvp",
        kind=kind,
        layout=layout,
        types=types,
    )


# Covers F-0004.R14 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize(
    ("kind", "layout", "llm"),
    [("service", "", True), ("cli", "flat", False), ("data", "flat", False)],
)
def test_types_mypy_renders_the_mypy_configuration(kind: str, layout: str, llm: bool) -> None:
    """ADR-0015 fitness: [tool.mypy] (with the overrides) instead of [tool.pyright], `mypy` in
    the dev dependencies and `uv run mypy` as the `types` step."""
    cfg = _types("mypy", kind=kind, layout=layout, llm=llm)
    ctx = cfg.context()
    assert (ctx["types"], ctx["pyright"], ctx["mypy"]) == ("mypy", False, True)
    plan = plan_files(cfg)
    text = str(plan["pyproject.toml"])
    tool = tomllib.loads(text)["tool"]
    assert tool["rumbo"]["types"] == "mypy"
    assert tool["rumbo"]["checks"]["types"] == "uv run mypy"
    assert "uv run pyright" not in text and "pyright" not in tool
    mypy = tool["mypy"]
    assert mypy["files"] == ["src", "tests"]
    assert mypy["python_version"] == "3.11"
    for key in ("check_untyped_defs", "warn_unused_ignores", "warn_redundant_casts"):
        assert mypy[key] is True
    assert mypy["no_implicit_optional"] is True
    assert mypy.get("mypy_path") == ("evals" if cfg.evals else None)
    if cfg.arch:
        [override] = mypy["overrides"]
        assert override["module"] == f"x.{cfg.inner_layer}.*"
        assert "strict" not in override  # mypy would apply it to every module
        assert {k for k, v in override.items() if v is True} == MYPY_STRICT_FLAGS
    else:
        assert "overrides" not in mypy
    deps = dev_dependencies(cfg)
    assert "mypy" in deps and "pyright" not in deps
    with_pyright = dev_dependencies(_types("pyright", kind=kind, layout=layout, llm=llm))
    assert deps == [("mypy" if d == "pyright" else d) for d in with_pyright]
    assert check_steps(cfg) == check_steps(_types("pyright", kind=kind, layout=layout, llm=llm))


# Covers F-0004.R14 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_types_mypy_adopted_checks_the_changed_files_in_the_tests_dir() -> None:
    cfg = Config(
        name="Legacy",
        package="legacy_app",
        llm=False,
        rigor="mvp",
        kind="cli",
        adopted=True,
        tests_dir="test",
        types="mypy",
    )
    tool = tomllib.loads(str(plan_files(cfg)["pyproject.toml"]))["tool"]
    assert tool["rumbo"]["checks"]["types"] == "uv run mypy {changed}"
    assert tool["mypy"]["files"] == ["src", "test"]


# Covers F-0004.R14 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize("cfg", COMBINATIONS, ids=lambda c: f"{c.provider or 'no-llm'}-{c.rigor}")
def test_every_combination_renders_with_mypy(cfg: Config) -> None:
    with_mypy = dataclasses.replace(cfg, types="mypy")
    plan = plan_files(with_mypy)
    tool = tomllib.loads(str(plan["pyproject.toml"]))["tool"]
    assert "mypy" in tool and "pyright" not in tool
    assert plan.keys() == plan_files(cfg).keys()  # the setting changes configuration, not files


# Covers F-0004.R14 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_default_type_checker_is_pyright_and_agents_md_does_not_change() -> None:
    cfg = _axes("service")
    assert cfg.types == "pyright"
    ctx = cfg.context()
    assert (ctx["types"], ctx["pyright"], ctx["mypy"]) == ("pyright", True, False)
    plan = plan_files(cfg)
    text = str(plan["pyproject.toml"])
    tool = tomllib.loads(text)["tool"]
    assert tool["rumbo"]["types"] == "pyright"
    assert tool["rumbo"]["checks"]["types"] == "uv run pyright"
    assert "uv run mypy" not in text and "mypy" not in tool
    assert tool["pyright"]["strict"] == ["src/x/domain"]
    assert dev_dependencies(cfg) == [*BASE_TOOLS, "import-linter", *MVP_TOOLS]
    # AGENTS.md names the checker only through the runner; both checkers are pre-approved.
    with_mypy = plan_files(_types("mypy"))
    assert str(plan["AGENTS.md"]) == str(with_mypy["AGENTS.md"])
    allowed = json.loads(Path(plan[".claude/settings.json"]).read_text(encoding="utf-8"))
    assert {"Bash(uv run pyright *)", "Bash(uv run mypy *)"} <= set(allowed["permissions"]["allow"])


# Covers F-0004.R14 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_unknown_type_checker_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="type checker 'ty'"):
        _types("ty")
    scaffold(_axes("cli"), tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert text.count('types = "pyright"') == 1
    pyproject.write_text(text.replace('types = "pyright"', 'types = "ty"'), encoding="utf-8")
    with pytest.raises(ValueError, match=r"\[tool\.rumbo\]\.types must be one of"):
        Project.load(tmp_path)


# Covers F-0004.R11, F-0004.R14 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_project_load_defaults_to_pyright_and_update_writes_the_key_once(tmp_path: Path) -> None:
    """ADR-0015 migration: a project generated before the setting loads as pyright; `rumbo
    update` writes the key once and never touches the `types` step."""
    scaffold(_axes("cli"), tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    stripped = re.sub(r'(?m)^types = "pyright"\n', "", text)
    assert stripped != text
    pyproject.write_text(stripped, encoding="utf-8")
    assert "types" not in tomllib.loads(stripped)["tool"]["rumbo"]
    project = Project.load(tmp_path)
    assert project.types == "pyright"
    assert Config.from_project(project).types == "pyright"
    rep = update(project, tmp_path)
    assert "pyproject.toml (+types)" in rep.merged
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["tool"]["rumbo"]["types"] == "pyright"
    assert data["tool"]["rumbo"]["checks"]["types"] == "uv run pyright"
    rep = update(Project.load(tmp_path), tmp_path)
    assert not rep.merged  # idempotent


# Covers F-0004.R14 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_types_round_trips_through_project(tmp_path: Path) -> None:
    cfg = _types("mypy", kind="cli", layout="flat")
    scaffold(cfg, tmp_path)
    project = Project.load(tmp_path)
    assert project.types == "mypy"
    loaded = Config.from_project(project)
    assert loaded.types == "mypy"
    assert str(plan_files(loaded)["pyproject.toml"]) == str(plan_files(cfg)["pyproject.toml"])
    listed = 'name = "x"\n[dependency-groups]\ndev = ["mypy", "ruff"]\n'
    assert missing_dev_dependencies(cfg, listed) == [
        "pytest",
        "hypothesis",
        "pre-commit",
        *MVP_TOOLS,
    ]


# ------------------------------------- specs/0004: line endings and the standard library
# Covers F-0004.R7 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_every_file_is_written_with_lf_line_endings(tmp_path: Path) -> None:
    """A text-mode write on Windows ends every line with CRLF, and a template checked out with
    CRLF would carry it into the project: created, merged and updated files are all LF."""
    from rumbo.scaffold import _write

    root = tmp_path / "project"
    report = scaffold(Config(name="Demo", package="demo", llm=True, provider="anthropic"), root)
    assert {"pyproject.toml", "AGENTS.md", "scripts/rumbo_guard.py"} <= set(report.created)
    assert not [rel for rel in report.created if b"\r" in (root / rel).read_bytes()]
    # A late key that `update` appends to pyproject.toml.
    pyproject = root / "pyproject.toml"
    pyproject.write_bytes(re.sub(rb"(?m)^python = .*\n", b"", pyproject.read_bytes()))
    assert "pyproject.toml (+python)" in update(Project.load(root), root).merged
    assert b"\r" not in pyproject.read_bytes()
    # A merge into a file the project already had.
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / ".gitignore").write_bytes(b"node_modules/\n")
    report = scaffold(Config(name="Demo", package="demo", llm=False), existing)
    assert any(item.startswith(".gitignore (+") for item in report.merged)
    ignored = (existing / ".gitignore").read_bytes()
    assert ignored.startswith(b"node_modules/\n") and b"\r" not in ignored
    # A source checked out with CRLF (a Windows clone of the framework) is normalised.
    source = tmp_path / "source.md"
    source.write_bytes(b"one\r\ntwo\r\n")
    _write(root / "copied.md", source, "copied.md")
    assert (root / "copied.md").read_bytes() == b"one\ntwo\n"


# Covers F-0004.R2 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_the_scaffolder_imports_only_the_standard_library() -> None:
    """ADR-0001 for the modules of this feature (init, update, promote and the template engine);
    `tests/test_fitness.py` holds the same rule for the whole package."""
    import rumbo

    package = Path(rumbo.__file__).parent
    allowed = sys.stdlib_module_names | {"rumbo"}
    for module in ("cli.py", "scaffold.py", "render.py", "project.py"):
        imported: set[str] = set()
        for node in ast.walk(ast.parse((package / module).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        assert imported and imported <= allowed, (module, sorted(imported - allowed))


# ----------------- ADR-0029: adoption respects what the project has, and destroys nothing
def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _tool(root: Path) -> dict[str, Any]:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]


def _at_root(cfg: Config) -> Config:
    """The same adopted configuration for a package that lives at the project root."""
    return dataclasses.replace(cfg, src=".")


# Covers F-0004.R8 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_force_never_overwrites_a_backup_and_skips_identical_files(tmp_path: Path) -> None:
    """ADR-0029 fitness (C1): the second `--force` lost the human's text, because the first
    backup was overwritten with the template the first run had just written."""
    cfg = Config(name="Demo", package="demo", llm=False, rigor="mvp")
    scaffold(cfg, tmp_path)
    # Nothing was edited: nothing to overwrite, nothing to back up.
    report = scaffold(cfg, tmp_path, force=True)
    assert not [rel for rel in _files(tmp_path) if ".bak" in rel]
    assert not report.created and "AGENTS.md" in report.unchanged
    assert "Unchanged" in report.summary()

    decisions = tmp_path / "docs" / "decisions.md"
    signed = "# Decisions\n\n## 2026-11-01 — G1 signed\n- Decided by: Carlos\n"
    decisions.write_text(signed, encoding="utf-8")
    report = scaffold(cfg, tmp_path, force=True)
    assert report.created == ["docs/decisions.md"]
    assert (tmp_path / "docs" / "decisions.md.bak").read_text(encoding="utf-8") == signed
    # The second run finds the template it wrote: identical, so the backup is not touched.
    scaffold(cfg, tmp_path, force=True)
    assert (tmp_path / "docs" / "decisions.md.bak").read_text(encoding="utf-8") == signed
    # A second text of the human's gets the next free name; the first one stays.
    decisions.write_text("# Decisions\n\nsecond text\n", encoding="utf-8")
    scaffold(cfg, tmp_path, force=True)
    assert (tmp_path / "docs" / "decisions.md.bak").read_text(encoding="utf-8") == signed
    assert "second text" in (tmp_path / "docs" / "decisions.md.bak.1").read_text(encoding="utf-8")
    # A text that a backup already holds is not copied again.
    decisions.write_text(signed, encoding="utf-8")
    scaffold(cfg, tmp_path, force=True)
    assert sorted(p.name for p in decisions.parent.glob("decisions.md.bak*")) == [
        "decisions.md.bak",
        "decisions.md.bak.1",
    ]


# Covers F-0004.R8 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize(
    ("rel", "content", "named"),
    [
        ("pyproject.toml", b"[project\nname='x'\n", "pyproject.toml"),
        ("pyproject.toml", b"tool = {black = {line-length = 88}}\n", "pyproject.toml"),
        ("pyproject.toml", "[project]\nname = 'caf\xe9'\n".encode("cp1252"), "pyproject.toml"),
        ("pyproject.toml", b'[tool.rumbo]\nagent_env = ["MY_AGENT"]\n', "[tool.rumbo] table"),
        (".gitignore", ".venv\n".encode("utf-16"), ".gitignore"),
        (".gitignore", "# caf\xe9\n.venv\n".encode("cp1252"), ".gitignore"),
        ("scripts", b"a file where the harness needs a directory\n", "scripts"),
        ("docs/adr/0000-template.md/keep", b"a directory where a file goes\n", "0000-template.md"),
    ],
    ids=[
        "toml",
        "inline-tool",
        "cp1252-toml",
        "partial-table",
        "utf16",
        "cp1252",
        "file-as-dir",
        "dir-as-file",
    ],
)
def test_init_reads_everything_before_it_writes_anything(
    tmp_path: Path, rel: str, content: bytes, named: str
) -> None:
    """ADR-0029 fitness (C11): an error found half-way left a partial harness behind."""
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    before = _files(tmp_path)
    with pytest.raises(ValueError, match=re.escape(named)):
        scaffold(Config(name="Demo", package="demo", llm=False), tmp_path)
    assert _files(tmp_path) == before


# Covers F-0004.R8 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_init_merges_a_pyproject_with_a_byte_order_mark_and_refuses_a_file_as_target(
    tmp_path: Path,
) -> None:
    bom = tmp_path / "bom"
    bom.mkdir()
    (bom / "pyproject.toml").write_bytes(b"\xef\xbb\xbf[project]\nname = 'x'\nversion = '1'\n")
    report = scaffold(Config(name="Demo", package="demo", llm=False), bom)
    assert any(item.startswith("pyproject.toml (") for item in report.merged)
    assert Project.load(bom).package == "demo"
    notes = tmp_path / "notes.txt"
    notes.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        scaffold(Config(name="Demo", package="demo", llm=False), notes)
    assert notes.read_text(encoding="utf-8") == "x"


# Covers F-0004.R10 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize(
    ("rel", "content", "where"),
    [
        ("pytest.ini", "[pytest]\naddopts = -ra\n", "pytest.ini"),
        ("tox.ini", "[tox]\nenvlist = py\n\n[pytest]\nmarkers =\n    slow: slow\n", "tox.ini"),
        ("setup.cfg", "[metadata]\nname = legacy\n\n[tool:pytest]\naddopts = -ra\n", "setup.cfg"),
    ],
)
def test_adoption_writes_no_pytest_configuration_over_the_projects_own(
    tmp_path: Path, rel: str, content: str, where: str
) -> None:
    """ADR-0029 fitness (C2): `[tool.pytest.ini_options]` outranks setup.cfg and tox.ini, and
    `--strict-markers` turned the project's own markers into errors."""
    from rumbo.scaffold import pytest_configuration

    root = _legacy(tmp_path, LEGACY_PYPROJECT, "src/legacy_app/__init__.py", "test/test_x.py")
    assert pytest_configuration(root) == ""
    (root / rel).write_text(content, encoding="utf-8")
    assert where in pytest_configuration(root)
    report = scaffold(_adopted(), root)
    assert "pytest" not in _tool(root)
    assert any(where in warning and "pytest" in warning for warning in report.warnings)
    assert (root / rel).read_text(encoding="utf-8") == content


# Covers F-0004.R10 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_adoption_keeps_a_pytest_table_in_pyproject_and_ignores_lookalikes(tmp_path: Path) -> None:
    from rumbo.scaffold import pytest_configuration

    own = LEGACY_PYPROJECT + '\n[tool.pytest.ini_options]\naddopts = "-ra"\n'
    root = _legacy(tmp_path / "own", own, "src/legacy_app/__init__.py", "test/test_x.py")
    assert "pyproject.toml" in pytest_configuration(root)
    scaffold(_adopted(), root)
    assert _tool(root)["pytest"] == {"ini_options": {"addopts": "-ra"}}
    # A tox.ini or a setup.cfg without a pytest section configures nothing.
    plain = _legacy(tmp_path / "plain", LEGACY_PYPROJECT, "src/legacy_app/__init__.py")
    (plain / "tox.ini").write_text("[tox]\nenvlist = py\n", encoding="utf-8")
    (plain / "setup.cfg").write_text("[metadata]\nname = legacy\n", encoding="utf-8")
    assert pytest_configuration(plain) == ""


# Covers F-0004.R9, F-0004.R10 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_adoption_points_pytest_at_the_tests_it_finds(tmp_path: Path) -> None:
    """ADR-0029 fitness (C2): `testpaths = ["tests"]` stopped collecting the tests that live
    elsewhere, and the suite went green without them."""
    from rumbo.scaffold import stray_tests

    # Tests in `test/` and inside the package: both are collected, the sensor watches `test/`.
    root = _legacy(
        tmp_path / "both",
        LEGACY_PYPROJECT,
        "src/legacy_app/__init__.py",
        "test/test_x.py",
        "src/legacy_app/tests/unit/test_core.py",
        "spikes/test_idea.py",
        ".venv/lib/test_vendored.py",
    )
    assert stray_tests(root, "test") == ["src/legacy_app/tests"]
    report = scaffold(_adopted(), root)
    pytest_table = _tool(root)["pytest"]["ini_options"]
    assert pytest_table == {"testpaths": ["test", "src/legacy_app/tests"]}  # no --strict-markers
    assert _tool(root)["rumbo"]["tests"] == "test"
    assert any("src/legacy_app/tests" in w and "sensor" in w for w in report.warnings)

    # No tests directory at the root: said, never assumed.
    none = _legacy(tmp_path / "none", LEGACY_PYPROJECT, "legacy_app/__init__.py")
    report = scaffold(_at_root(_adopted(tests_dir="tests")), none)
    assert _tool(none)["pytest"]["ini_options"] == {"testpaths": ["tests"]}
    assert any("no tests directory" in w.lower() for w in report.warnings)

    # Test files at the root itself: no `testpaths` narrows what pytest collects.
    flat = _legacy(tmp_path / "flat", LEGACY_PYPROJECT, "legacy_app/__init__.py", "test_all.py")
    assert stray_tests(flat, "tests") == ["."]
    report = scaffold(_at_root(_adopted(tests_dir="tests")), flat)
    assert "pytest" not in _tool(flat)
    assert any("project root" in w for w in report.warnings)

    # A project that is not adopted keeps the configuration it always had.
    fresh = tomllib.loads(
        str(plan_files(Config(name="x", package="x", llm=False))["pyproject.toml"])
    )
    assert fresh["tool"]["pytest"]["ini_options"] == {
        "testpaths": ["tests"],
        "addopts": "-q --strict-markers",
    }


# Covers F-0004.R14 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
@pytest.mark.parametrize(
    ("requires", "pin", "flag", "expected"),
    [
        ("", "", None, "3.11"),
        (">=3.12", "", None, "3.12"),
        (">=3.12,<4", "", None, "3.12"),
        (">= 3.12 , < 4", "", None, "3.12"),
        ("~=3.12", "", None, "3.12"),
        ("==3.12.*", "", None, "3.12"),
        (">3.11", "", None, "3.11"),  # PEP 440: 3.11.1 is greater than 3.11
        (">=3.13", "", None, "3.13"),
        (">=3.9", "", None, "3.11"),
        (">=3.8,!=3.11.*", "", None, "3.12"),
        ("", "3.12\n", None, "3.12"),
        ("", "3.12.4\n", None, "3.12"),
        ("", "cpython-3.13\n", None, "3.13"),
        ("", "3.9\n", None, "3.11"),
        ("", "3.99\n", None, "3.13"),
        (">=3.12", "3.13\n", None, "3.12"),  # the pin may be newer than the promise
        (">=3.11", "", "3.13", "3.13"),  # the flag wins
        ("", "3.13\n", "3.12", "3.12"),
    ],
)
def test_the_python_version_is_detected(
    tmp_path: Path, requires: str, pin: str, flag: str | None, expected: str
) -> None:
    """ADR-0029 fitness (C3): a project with `requires-python = ">=3.13"` got 3.11."""
    from rumbo.scaffold import resolve_python

    if requires:
        (tmp_path / "pyproject.toml").write_text(
            f'[project]\nname = "x"\nversion = "1"\nrequires-python = "{requires}"\n',
            encoding="utf-8",
        )
    if pin:
        (tmp_path / ".python-version").write_text(pin, encoding="utf-8")
    assert resolve_python(tmp_path, flag)[0] == expected


# Covers F-0004.R14 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_a_python_that_contradicts_the_project_is_refused_or_reported(tmp_path: Path) -> None:
    from rumbo.scaffold import resolve_python

    pyproject = tmp_path / "pyproject.toml"

    def requires(spec: str) -> None:
        pyproject.write_text(
            f'[project]\nname = "x"\nversion = "1"\nrequires-python = "{spec}"\n',
            encoding="utf-8",
        )

    requires(">=3.13")
    with pytest.raises(ValueError, match=r"--python 3\.11.*>=3\.13"):
        resolve_python(tmp_path, "3.11")
    requires(">=3.99")
    with pytest.raises(ValueError, match=r">=3\.99.*3\.11, 3\.12, 3\.13"):
        resolve_python(tmp_path, None)
    requires(">=3.8,<3.11")
    with pytest.raises(ValueError, match=r">=3\.8,<3\.11"):
        resolve_python(tmp_path, None)
    # Older than supported: said, and the nearest supported one is used because it satisfies it.
    requires(">=3.9")
    python, notes = resolve_python(tmp_path, None)
    assert python == "3.11" and any(">=3.9" in note and "3.11" in note for note in notes)
    # A pin below the result is reported, never left silently inconsistent.
    requires(">=3.13")
    (tmp_path / ".python-version").write_text("3.11\n", encoding="utf-8")
    python, notes = resolve_python(tmp_path, None)
    assert python == "3.13" and any(".python-version" in note and "3.11" in note for note in notes)
    (tmp_path / ".python-version").write_text("3.13\n", encoding="utf-8")
    assert resolve_python(tmp_path, None) == ("3.13", [])
    # A specifier it cannot read is said, and decides nothing.
    requires("^3.12")
    python, notes = resolve_python(tmp_path, None)
    assert python == "3.13" and any("^3.12" in note for note in notes)  # the pin decided


# Covers F-0004.R11 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_update_refuses_a_harness_newer_than_the_cli(simple_project: Path) -> None:
    """ADR-0029 fitness (C5): an older CLI replaced a newer guard without a word."""
    from rumbo.scaffold import NewerHarness, release_key

    assert release_key("0.4.10") > release_key("0.4.9") > release_key("0.4")
    assert release_key("1.0.0rc1") == release_key("1.0") and release_key("dev") == ()
    pyproject = simple_project / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    guard = simple_project / "scripts" / "rumbo_guard.py"
    newer = guard.read_text(encoding="utf-8") + "# a newer guard\n"
    try:
        pyproject.write_text(
            original.replace(f'version = "{__version__}"', 'version = "99.0.0"', 1),
            encoding="utf-8",
        )
        guard.write_text(newer, encoding="utf-8")
        with pytest.raises(NewerHarness, match=rf"99\.0\.0.*{re.escape(__version__)}"):
            update(Project.load(simple_project), simple_project)
        assert guard.read_text(encoding="utf-8") == newer
        assert Project.load(simple_project).harness_version == "99.0.0"
        report = update(Project.load(simple_project), simple_project, allow_downgrade=True)
        assert "scripts/rumbo_guard.py" in report.updated
        assert Project.load(simple_project).harness_version == __version__
    finally:
        pyproject.write_text(original, encoding="utf-8")


def test_pre_approvals_of_older_harnesses_that_defeat_the_guard_are_named(tmp_path: Path) -> None:
    """`rumbo update` keeps an existing .claude/settings.json, so a project made before v0.4.1
    still pre-approves `make rumbo *` and `rumbo decision *`: `doctor` has to say so."""
    from rumbo.scaffold import guard_wiring

    fresh = tmp_path / "fresh"
    scaffold(Config(name="Demo", package="demo", llm=False), fresh)
    settings = fresh / ".claude" / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert guard_wiring(fresh) == ([], [])  # the template of this version pre-approves neither
    data["permissions"]["allow"] += ["Bash(make rumbo *)", "Bash(rumbo decision *)"]
    settings.write_text(json.dumps(data), encoding="utf-8")
    problems, warnings = guard_wiring(fresh)
    assert problems == [] and len(warnings) == 2
    assert '"Bash(make rumbo *)"' in warnings[0] and "permissions.allow" in warnings[0]
    assert '"Bash(rumbo decision *)"' in warnings[1] and "as the human" in warnings[1]
    # A permissions object of another shape is not judged, and never crashes.
    data["permissions"] = ["Bash(make rumbo *)"]
    settings.write_text(json.dumps(data), encoding="utf-8")
    assert guard_wiring(fresh) == ([], [])


# Covers F-0004.R10, F-0004.R11 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_the_guard_wiring_is_checked_and_the_snippets_are_printed(tmp_path: Path) -> None:
    """ADR-0029 fitness (C6): adopting over an existing settings.json or pre-commit
    configuration left the guard unwired, and nothing said so."""
    from rumbo.scaffold import guard_wiring
    from rumbo.sensors.guard import SHELL_TOOLS

    assert SHELL_TOOLS == ("Bash", "PowerShell")
    fresh = tmp_path / "fresh"
    scaffold(Config(name="Demo", package="demo", llm=False), fresh)
    assert guard_wiring(fresh) == ([], [])
    settings = fresh / ".claude" / "settings.json"
    text = settings.read_text(encoding="utf-8")
    # A project created before the PowerShell tool was matched: a warning names tool and event.
    settings.write_text(text.replace("|PowerShell", ""), encoding="utf-8")
    problems, warnings = guard_wiring(fresh)
    assert problems == [] and len(warnings) == 2
    assert all("PowerShell" in w for w in warnings)
    assert "PreToolUse" in warnings[0] and "PostToolUse" in warnings[1]
    # A hook that is gone is a problem; so is a file that is not JSON.
    data = json.loads(text)
    del data["hooks"]["Stop"]
    settings.write_text(json.dumps(data), encoding="utf-8")
    assert guard_wiring(fresh)[0] == [
        ".claude/settings.json: no Stop hook runs scripts/rumbo_guard.py stop"
    ]
    settings.write_text("{not json", encoding="utf-8")
    assert "not valid JSON" in guard_wiring(fresh)[0][0]
    settings.write_text(text, encoding="utf-8")
    hooks = fresh / ".pre-commit-config.yaml"
    config = hooks.read_text(encoding="utf-8")
    hooks.write_text(config.replace("rumbo_guard.py commit-msg", "other.py"), encoding="utf-8")
    assert guard_wiring(fresh)[0] == [
        ".pre-commit-config.yaml: no hook runs scripts/rumbo_guard.py commit-msg"
    ]

    # Adoption over the project's own two files: untouched, and the report says what to add.
    root = _legacy(tmp_path / "legacy", LEGACY_PYPROJECT, "src/legacy_app/__init__.py")
    own_settings = '{"permissions": {"allow": []}}\n'
    own_hooks = (
        "repos:\n  - repo: https://github.com/psf/black\n    rev: 23.1.0\n"
        "    hooks:\n      - id: black\n"
    )
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(own_settings, encoding="utf-8")
    (root / ".pre-commit-config.yaml").write_text(own_hooks, encoding="utf-8")
    report = scaffold(_adopted(tests_dir="tests"), root)
    assert (root / ".claude" / "settings.json").read_text(encoding="utf-8") == own_settings
    assert (root / ".pre-commit-config.yaml").read_text(encoding="utf-8") == own_hooks
    summary = report.summary()
    assert '"PreToolUse"' in summary and '"matcher": "Bash|PowerShell"' in summary
    assert 'rumbo_guard.py\\" stop' in summary
    assert "- id: rumbo-guard\n" in summary and "- id: rumbo-guard-commit-msg\n" in summary
    assert "default_install_hook_types: [pre-commit, commit-msg]" in summary
    problems, _ = guard_wiring(root)
    assert len(problems) == 5  # three hooks of settings.json, two of pre-commit
    # The snippets are the fix: pasted, the wiring is complete; and they are not printed again.
    from rumbo.scaffold import pre_commit_snippet, settings_snippet

    assert settings_snippet() in summary and pre_commit_snippet() in summary
    pasted = json.loads(own_settings) | json.loads(settings_snippet())
    (root / ".claude" / "settings.json").write_text(json.dumps(pasted), encoding="utf-8")
    (root / ".pre-commit-config.yaml").write_text(
        own_hooks + pre_commit_snippet(), encoding="utf-8"
    )
    assert guard_wiring(root) == ([], [])
    again = scaffold(_adopted(tests_dir="tests"), root).summary()
    assert settings_snippet() not in again and pre_commit_snippet() not in again


# Covers F-0004.R15 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_install_updates_only_its_own_hook_configuration_and_knows_a_sub_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0029 fitness (C6, C11): `pre-commit autoupdate` rewrote the revisions of a
    configuration RUMBO did not write; a project in a sub-directory of a repository was told
    it was not in one."""
    from rumbo.cli import main
    from rumbo.scaffold import git_prefix

    commands = _init_without_network(tmp_path, monkeypatch, terminal=False)
    autoupdate = ["uv", "run", "pre-commit", "autoupdate"]
    install = ["uv", "run", "pre-commit", "install"]
    init = ["init", "--yes", "--name", "Demo", "--package", "demo", "--no-llm", "--rigor", "mvp"]
    own = _git_repo(tmp_path / "own")
    (own / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    assert main([*init, "--dir", str(own)]) == 0
    assert autoupdate not in commands and install in commands
    commands.clear()
    fresh = _git_repo(tmp_path / "fresh")
    assert main([*init, "--dir", str(fresh)]) == 0
    assert commands.index(autoupdate) < commands.index(install)
    capsys.readouterr()

    mono = _git_repo(tmp_path / "mono")
    billing = mono / "services" / "billing"
    billing.mkdir(parents=True)
    assert (git_prefix(mono), git_prefix(billing)) == ("", "services/billing/")
    assert git_prefix(tmp_path) is None
    commands.clear()
    assert main([*init, "--dir", str(billing)]) == 0
    out = capsys.readouterr().out
    assert "not a git repository" not in out
    assert "repository root" in out and "services/billing" in out
    assert autoupdate not in commands and install not in commands
    assert not (mono / ".git" / "hooks" / "pre-commit").exists()


# Covers F-0004.R1, F-0004.R9 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_reserved_names_are_only_the_ones_that_collide() -> None:
    """ADR-0029 fitness (C8): `app`, `api` and `domain` were refused for every project."""
    for package in ("app", "api", "domain"):
        with pytest.raises(ValueError, match="package"):
            Config(name="x", package=package, llm=False)  # a service: hexagonal layers
        adopted = Config(name="x", package=package, llm=False, adopted=True, layout="flat")
        assert adopted.package == package
        assert valid_package(package, adopted=True) and not valid_package(package)
    assert Config(name="x", package="domain", llm=False, kind="library").layout == "flat"
    with pytest.raises(ValueError, match="package"):
        Config(name="x", package="api", llm=False, kind="service", layout="flat")  # api/http.py
    with pytest.raises(ValueError, match="package"):
        Config(
            name="x", package="app", llm=False, kind="cli", layout="layered", layers=("app", "x")
        )
    # An adopted project keeps its spelling; it is still an identifier and not a keyword.
    camel = Config(name="x", package="MyPkg", llm=False, adopted=True, layout="flat")
    assert camel.package == "MyPkg"
    for package in ("class", "My-Pkg", "1pkg", "", "pkg.sub"):
        assert not valid_package(package, adopted=True)
        with pytest.raises(ValueError, match="package"):
            Config(name="x", package=package, llm=False, adopted=True, layout="flat")
    # A package at the root named like a directory the harness writes into is still refused.
    with pytest.raises(ValueError, match="scripts"):
        Config(name="x", package="scripts", llm=False, adopted=True, layout="flat", src=".")


# Covers F-0004.R10 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_adoption_configures_only_what_is_on_disk(tmp_path: Path) -> None:
    """ADR-0029 fitness (C7): layers, an evals entry and a package that were never written."""
    from rumbo.scaffold import find_package, missing_modules

    root = _legacy(tmp_path / "flat", LEGACY_PYPROJECT, "legacy_app/__init__.py")
    flat = _at_root(_adopted(tests_dir="tests"))
    assert missing_modules(flat, root) == []
    hexagonal = _at_root(_adopted(tests_dir="tests", layout="hexagonal"))
    assert missing_modules(hexagonal, root) == [
        "legacy_app/api/",
        "legacy_app/adapters/",
        "legacy_app/app/",
        "legacy_app/domain/",
    ]
    before = _files(root)
    with pytest.raises(ValueError, match=r"legacy_app/api/, legacy_app/adapters/"):
        scaffold(hexagonal, root)
    llm = _at_root(_adopted(tests_dir="tests", llm=True))
    assert "legacy_app/app/entrypoint.py" in missing_modules(llm, root)
    data = _at_root(_adopted(tests_dir="tests", kind="data"))
    assert missing_modules(data, root) == ["legacy_app/pipeline.py"]
    with pytest.raises(ValueError, match=r"legacy_app/pipeline\.py"):
        scaffold(data, root)
    # A package that is not there at all.
    with pytest.raises(ValueError, match="src/legacy_app"):
        scaffold(_adopted(tests_dir="tests"), root)
    assert _files(root) == before
    # With the modules on disk the same configuration is accepted.
    (root / "legacy_app" / "pipeline.py").write_text("def run(x):\n    return x\n", "utf-8")
    assert missing_modules(data, root) == []
    scaffold(data, root)
    assert "evals" in _tool(root)["rumbo"]["checks"]

    # The package is found with the spelling it has on disk.
    camel = _legacy(tmp_path / "camel", LEGACY_PYPROJECT, "MyPkg/__init__.py")
    assert find_package(camel, "mypkg") == (".", "MyPkg")
    assert find_package(camel, "MyPkg") == (".", "MyPkg")
    assert find_package(camel, "other") is None
    under = _legacy(tmp_path / "under", LEGACY_PYPROJECT, "src/legacy_app/__init__.py")
    assert find_package(under, "legacy_app") == ("src", "legacy_app")


# Covers F-0004.R8, F-0004.R10 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_a_pyproject_without_a_build_system_is_said_not_fixed(tmp_path: Path) -> None:
    """ADR-0029 fitness (C7): without `[build-system]` uv does not install the package."""
    root = _legacy(tmp_path / "none", LEGACY_PYPROJECT, "legacy_app/__init__.py", "tests/t.py")
    report = scaffold(_at_root(_adopted(tests_dir="tests")), root)
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert "build-system" not in data and "hatch" not in data["tool"]
    warning = next(w for w in report.warnings if "[build-system]" in w)
    assert "not installed" in warning and 'packages = ["legacy_app"]' in warning
    backend = (
        '\n[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n'
    )
    built = _legacy(tmp_path / "built", LEGACY_PYPROJECT + backend, "legacy_app/__init__.py")
    report = scaffold(_at_root(_adopted(tests_dir="tests")), built)
    assert not [w for w in report.warnings if "[build-system]" in w]


def test_requirements_that_pyproject_does_not_declare_are_said_not_copied(tmp_path: Path) -> None:
    """A project that keeps its dependencies in requirements.txt was adopted in silence with
    `dependencies = []`: its tests could not import them on day one."""
    cfg = _at_root(_adopted(tests_dir="tests"))
    root = _legacy(tmp_path / "req", LEGACY_PYPROJECT, "legacy_app/__init__.py", "tests/t.py")
    (root / "requirements.txt").write_text("flask\n", encoding="utf-8")
    (root / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    report = scaffold(cfg, root)
    warning = next(w for w in report.warnings if "requirements" in w)
    assert "requirements-dev.txt, requirements.txt found" in warning
    assert "`uv add -r requirements-dev.txt`" in warning
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["dependencies"] == []  # said, not copied
    # Dependencies that pyproject.toml already declares: nothing to say.
    declared = LEGACY_PYPROJECT.replace("dependencies = []", 'dependencies = ["flask"]')
    other = _legacy(tmp_path / "ok", declared, "legacy_app/__init__.py", "tests/t.py")
    (other / "requirements.txt").write_text("flask\n", encoding="utf-8")
    assert not [w for w in scaffold(cfg, other).warnings if "requirements" in w]


# Covers F-0004.R10, F-0004.R11 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_what_the_project_already_had_is_decided_once_at_adoption(tmp_path: Path) -> None:
    """ADR-0029 fitness (C9): RUMBO's own ci.yml and infra/ counted as the project's, so their
    drift and their deletion went unreported."""
    from rumbo.scaffold import config_for

    cfg = _at_root(_adopted(tests_dir="tests", kind="service", layout="flat"))
    root = _legacy(tmp_path / "bare", LEGACY_PYPROJECT, "legacy_app/__init__.py", "tests/t.py")
    report = scaffold(cfg, root)
    ci = root / ".github" / "workflows" / "ci.yml"
    dockerfile = root / "infra" / "Dockerfile"
    assert ci.is_file() and dockerfile.is_file() and report.notes == []
    rumbo = _tool(root)["rumbo"]
    assert (rumbo["own_ci"], rumbo["own_infra"]) == (False, False)
    # Later commands read the decision: they do not look at the files RUMBO wrote itself.
    stored = config_for(Project.load(root), root)
    assert (stored.own_ci, stored.own_infra) == (False, False)
    ci.write_text(ci.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    differs = update(Project.load(root), root).differs
    assert ".github/workflows/ci.yml" in differs and "infra/Dockerfile" in differs
    ci.unlink()
    assert ".github/workflows/ci.yml" in plan_files(stored, root)
    assert ".github/workflows/ci.yml (missing)" in update(Project.load(root), root).differs
    report = scaffold(cfg, root)  # the command again: ci.yml comes back, with no note
    assert ci.is_file() and report.notes == []

    # A project with its own workflow and its own infra/: recorded, and still true later.
    own = _legacy(
        tmp_path / "own",
        LEGACY_PYPROJECT,
        "legacy_app/__init__.py",
        "tests/t.py",
        ".github/workflows/build.yml",
        "infra/k8s.yaml",
    )
    report = scaffold(cfg, own)
    rumbo = _tool(own)["rumbo"]
    assert (rumbo["own_ci"], rumbo["own_infra"]) == (True, True)
    assert report.notes == [CI_STEPS_NOTE]
    assert not (own / ".github" / "workflows" / "ci.yml").exists()
    assert not (own / "infra" / "Dockerfile").exists()
    (own / ".github" / "workflows" / "build.yml").unlink()  # the human removes their workflow
    stored = config_for(Project.load(own), own)
    assert ".github/workflows/ci.yml" not in plan_files(stored, own)
    assert not [rel for rel in plan_files(stored, own) if rel.startswith("infra/")]

    # Adopted before the keys existed: the names decide, and `update` writes the keys once.
    for project in (root, own):
        pyproject = project / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        pyproject.write_text(re.sub(r"(?m)^own_(ci|infra) = .*\n", "", text), encoding="utf-8")
    (own / ".github" / "workflows" / "build.yml").write_text("name: build\n", encoding="utf-8")
    assert "own_ci" not in _tool(root)["rumbo"]
    older = config_for(Project.load(root), root)
    assert (older.own_ci, older.own_infra) == (False, False)  # only RUMBO's own file names
    older = config_for(Project.load(own), own)
    assert (older.own_ci, older.own_infra) == (True, True)  # build.yml; infra/ has no Dockerfile
    assert "pyproject.toml (+own_ci, own_infra)" in update(Project.load(own), own).merged
    assert (_tool(own)["rumbo"]["own_ci"], _tool(own)["rumbo"]["own_infra"]) == (True, True)
    assert not update(Project.load(own), own).merged


# Covers F-0004.R11 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_update_follows_the_tests_directory_and_finds_the_version_key(tmp_path: Path) -> None:
    """ADR-0029 fitness (C10): `tests/architecture/` was hard-coded, and an array before
    `version` hid the key from the regular expression that rewrote it."""
    from rumbo.scaffold import is_harness_file

    assert is_harness_file("tests/architecture/test_architecture.py")
    assert is_harness_file("test/architecture/test_architecture.py", "test")
    assert not is_harness_file("tests/architecture/test_architecture.py", "test")
    root = _legacy(tmp_path, LEGACY_PYPROJECT, "src/legacy_app/__init__.py", "test/test_x.py")
    scaffold(_adopted(), root)
    arch = root / "test" / "architecture" / "test_architecture.py"
    arch.write_text(arch.read_text(encoding="utf-8") + "# mine\n", encoding="utf-8")
    assert "test/architecture/test_architecture.py" in update(Project.load(root), root).differs

    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    current = f'version = "{__version__}"\n'
    assert text.count(current) == 1
    arrays = 'agent_env = ["MY_AGENT"]\nagent_authors = [\n    "bot@example.com",\n]\n'
    text = (
        text.replace(current, "")
        .replace("[tool.rumbo]\n", "[tool.rumbo]\n" + arrays, 1)
        .replace("adopted = true\n", 'adopted = true\nversion = "0.0.1"  # the harness\n', 1)
    )
    pyproject.write_text(text, encoding="utf-8")
    assert Project.load(root).harness_version == "0.0.1"
    report = update(Project.load(root), root)
    assert Project.load(root).harness_version == __version__
    assert f'version = "{__version__}"  # the harness\n' in pyproject.read_text(encoding="utf-8")
    assert tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"] == "0.1.0"
    assert not [w for w in report.warnings if "version" in w]
    # A spelling it cannot rewrite is reported, not skipped in silence.
    text = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(
        text.replace(f'version = "{__version__}"  # the harness', "version = '''0.0.1'''"),
        encoding="utf-8",
    )
    report = update(Project.load(root), root)
    assert Project.load(root).harness_version == "0.0.1"
    assert any("[tool.rumbo].version" in w and __version__ in w for w in report.warnings)


# Covers F-0004.R11, F-0004.R13 (specs/0004-mount-adopt-update-and-promote-the-harness.md).
def test_update_reports_steps_and_aliases_that_differ_from_the_template(
    simple_project: Path,
) -> None:
    """A rigor raised later keeps the `test` step of the old level and a Makefile without the
    new aliases: `update` says so and rewrites neither."""
    assert update(Project.load(simple_project), simple_project).drift == []
    pyproject = simple_project / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    makefile = (simple_project / "Makefile").read_bytes()
    try:
        pyproject.write_text(set_rigor(original, "mvp"), encoding="utf-8")
        report = update(Project.load(simple_project), simple_project)
        assert any(item.startswith("differs from the template: test ") for item in report.drift)
        assert not [item for item in report.drift if "template: lint" in item]
        assert any("Makefile" in item and "security" in item for item in report.drift)
        assert "differs from the template: test" in report.summary()
        assert _tool(simple_project)["rumbo"]["checks"]["test"] == "uv run pytest"
        assert (simple_project / "Makefile").read_bytes() == makefile
    finally:
        pyproject.write_text(original, encoding="utf-8")


def test_agents_md_leaves_rumbo_decision_to_the_human() -> None:
    """`rumbo decision` records a decision as the human and is no longer pre-approved for an
    agent: the agent proposes the text, the human runs the command."""
    for cfg in (Config(name="x", package="x", llm=False), _adopted()):
        agents = str(plan_files(cfg)["AGENTS.md"])
        lines = [line for line in agents.splitlines() if "rumbo decision" in line]
        assert len(lines) == 2
        assert all("propose" in line.lower() and "human" in line for line in lines)
        assert "log `rumbo decision" not in agents and len(agents.splitlines()) < 100
    settings = plan_files(Config(name="x", package="x", llm=False))[".claude/settings.json"]
    assert isinstance(settings, Path)
    allowed = json.loads(settings.read_text(encoding="utf-8"))["permissions"]["allow"]
    assert "Bash(rumbo decision *)" not in allowed
