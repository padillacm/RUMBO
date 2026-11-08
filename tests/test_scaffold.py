import ast
import importlib.util
import itertools
import json
import os
import re
import subprocess
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


def test_ci_never_holds_the_model_credential() -> None:
    plan = plan_files(Config(name="x", package="x", llm=True, provider="openai", rigor="mvp"))
    ci = str(plan[".github/workflows/ci.yml"])
    nightly = str(plan[".github/workflows/nightly.yml"])
    assert "OPENAI_API_KEY" not in ci
    assert "secrets.OPENAI_API_KEY" in nightly
    assert "gitleaks-action" in ci


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


def test_claude_code_provider_has_no_sdk_and_an_adapter() -> None:
    cfg = Config(name="x", package="x", llm=True, provider="claude-code")
    assert cfg.sdk_package == ""
    plan = plan_files(cfg)
    assert "src/x/adapters/claude_code.py" in plan
    assert "npm install -g @anthropic-ai/claude-code" in str(plan[".github/workflows/nightly.yml"])


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
        line = next(ln for ln in makefile.splitlines() if ln.startswith(f"{target}:"))
        assert line.split(";", 1)[1].strip() == RUNNER + steps
        if target == "security" and cfg.rigor == "prototype":
            continue  # ADR-0011: a prototype has no security steps; the alias waits for promote
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
        assert make_lines == ["        run: make mutation"]  # mutmut needs os.fork: POSIX only
        if cfg.llm:
            assert "run: uv run python evals/run_evals.py --suite all --live" in str(nightly)


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


def test_provider_sdk_is_forbidden_in_domain() -> None:
    cfg = Config(name="x", package="x", llm=True, provider="google")
    data = tomllib.loads(str(plan_files(cfg)["pyproject.toml"]))
    purity = next(c for c in data["tool"]["importlinter"]["contracts"] if c["type"] == "forbidden")
    assert "google" in purity["forbidden_modules"]
    assert cfg.sdk_package == "google-genai"


def test_name_is_escaped_in_toml_and_python() -> None:
    cfg = Config(name="Demo 'quoted' project", package="demo", llm=True, provider="anthropic")
    plan = plan_files(cfg)
    tomllib.loads(str(plan["pyproject.toml"]))
    ast.parse(str(plan["src/demo/api/http.py"]))


@pytest.mark.parametrize("name", ['bad "name"', "back\\slash", "new\nline", ""])
def test_invalid_names(name: str) -> None:
    with pytest.raises(ValueError):
        Config(name=name, package="x", llm=False)


def test_target_path() -> None:
    cfg = Config(name="x", package="my_pkg", llm=False)
    assert target_path("dot_github/workflows/ci.yml.tmpl", cfg) == ".github/workflows/ci.yml"
    assert target_path("src/__pkg__/domain/__init__.py", cfg) == "src/my_pkg/domain/__init__.py"


@pytest.mark.parametrize("package", ["MyPkg", "1pkg", "with-dash", "class", "tests", ""])
def test_invalid_packages(package: str) -> None:
    assert not valid_package(package)
    with pytest.raises(ValueError):
        Config(name="x", package=package, llm=False)


def test_to_package() -> None:
    assert to_package("2048 game") == "app_2048_game"
    assert to_package("Mi Proyecto") == "mi_proyecto"
    assert to_package("class") == "class_app"


def test_llm_needs_valid_provider() -> None:
    with pytest.raises(ValueError):
        Config(name="x", package="x", llm=True)
    with pytest.raises(ValueError):
        Config(name="x", package="x", llm=True, provider="antropic")
    with pytest.raises(ValueError):
        Config(name="x", package="x", llm=True, provider="other", sdk_package="foo")


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


def test_scaffold_skips_existing_and_reports(simple_project: Path) -> None:
    agents = simple_project / "AGENTS.md"
    agents.write_text("mine", encoding="utf-8")
    rep = scaffold(
        Config(name="Demo", package="demo", llm=False, rigor="prototype"), simple_project
    )
    assert agents.read_text(encoding="utf-8") == "mine"
    assert "AGENTS.md" in rep.skipped and "pyproject.toml" in rep.skipped
    assert not rep.created and not rep.merged


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


def test_merge_lines_without_duplicates() -> None:
    merged, new = merge_lines(".venv/\n", ".venv/\n.env\n")
    assert new == [".env"]
    assert merged.count(".venv/") == 1


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


def test_init_declares_the_human_in_the_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from rumbo import scaffold as scaffold_module
    from rumbo.cli import main
    from rumbo.scaffold import Report

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
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
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
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


def test_service_keeps_http_without_llm_and_logs_json_only_with_it() -> None:
    plain = plan_files(_axes("service"))
    http = str(plain["src/x/api/http.py"])
    assert "FastAPI" in http and "configure_json_logging" not in http and "telemetry" not in http
    assert "uvicorn" in str(plain["infra/Dockerfile"])
    assert "configure_json_logging()" in str(
        plan_files(_axes("service", llm=True))["src/x/api/http.py"]
    )
    assert '"python", "-m", "x"' in str(plan_files(_axes("data"))["infra/Dockerfile"])


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
    assert f"mutmut on src/x/{inner}" in nightly
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


def test_layout_defaults_follow_the_kind_and_llm_forces_hexagonal() -> None:
    assert _axes("service").layout == "hexagonal"
    assert all(_axes(kind).layout == "flat" for kind in ("library", "cli", "data"))
    forced = _axes("cli", layout="flat", llm=True)
    assert (forced.layout, forced.arch, forced.inner_layer) == ("hexagonal", True, "domain")
    assert plan_files(forced).keys() >= HEXAGONAL_INITS
    assert _axes("data", llm=True).layout == "hexagonal"


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


def test_detect_package_from_the_project_name(tmp_path: Path) -> None:
    """ADR-0013: `[project].name` normalised to snake_case when its directory exists."""
    root = _legacy(tmp_path / "a", LEGACY_PYPROJECT, "src/legacy_app/__init__.py")
    assert detect_package(root, read_pyproject(root)) == "legacy_app"
    root = _legacy(tmp_path / "b", LEGACY_PYPROJECT, "legacy_app/__init__.py")  # no src/
    assert detect_package(root, read_pyproject(root)) == "legacy_app"
    dotted = LEGACY_PYPROJECT.replace("legacy-app", "Legacy.App")
    root = _legacy(tmp_path / "c", dotted, "src/legacy_app/__init__.py")
    assert detect_package(root, read_pyproject(root)) == "legacy_app"


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


def test_detect_package_ambiguity_asks_for_the_flag(tmp_path: Path) -> None:
    root = _legacy(tmp_path, LEGACY_PYPROJECT, "alpha/__init__.py", "src/beta/__init__.py")
    with pytest.raises(ValueError, match=r"alpha, beta.*--package"):
        detect_package(root, read_pyproject(root))
    # The flag skips the detection (and so the error).
    assert detect_project(root, "beta").package == "beta"


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


def test_detect_tests_dir(tmp_path: Path) -> None:
    assert detect_tests_dir(tmp_path) == "tests"  # nothing yet: the default
    (tmp_path / "test").mkdir()
    assert detect_tests_dir(tmp_path) == "test"
    (tmp_path / "tests").mkdir()
    assert detect_tests_dir(tmp_path) == "tests"  # `tests` wins when both exist


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
    plain = _rendered_checks_of(
        Config(name="Legacy", package="legacy_app", llm=False, rigor="mvp", kind="cli")
    )
    assert {k: v for k, v in checks.items() if k not in ("lint", "format", "types")} == {
        k: v for k, v in plain.items() if k not in ("lint", "format", "types")
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


def test_adoption_with_the_default_tests_dir_writes_no_tests_key() -> None:
    plan = plan_files(_adopted(tests_dir="tests"))
    rumbo = tomllib.loads(str(plan["pyproject.toml"]))["tool"]["rumbo"]
    assert rumbo["adopted"] is True and "tests" not in rumbo
    assert sorted(rel for rel in plan if rel.startswith("tests/")) == [
        "tests/architecture/test_architecture.py",
        "tests/unit/test_check_tests_not_weakened.py",
    ]


def test_adoption_keeps_the_purity_rules_when_the_layout_has_layers() -> None:
    plan = plan_files(_adopted(kind="service", layout="hexagonal"))
    arch = str(plan["test/architecture/test_architecture.py"])
    assert "FORBIDDEN_IN_INNER" in arch and "spikes" in arch
    assert not [rel for rel in plan if rel.startswith("src/")]
    assert "infra/Dockerfile" in plan  # the kind has it and no root was given


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


def test_adoption_skips_infra_when_the_directory_exists(tmp_path: Path) -> None:
    cfg = _adopted(kind="service", layout="flat")
    root = _legacy(tmp_path, LEGACY_PYPROJECT, "src/legacy_app/__init__.py", "infra/k8s.yaml")
    assert not [rel for rel in plan_files(cfg, root) if rel.startswith("infra/")]
    assert "infra/Dockerfile" in plan_files(cfg, tmp_path / "elsewhere")
    scaffold(cfg, root)
    assert not (root / "infra" / "Dockerfile").exists()
    assert (root / "docs" / "ops" / "runbook.md").is_file()  # the other ops files still come


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


def test_python_default_is_unchanged() -> None:
    cfg = Config(name="x", package="x", llm=False, kind="cli")
    assert cfg.python == "3.11"
    places = _python_places(cfg)
    assert (places["requires"], places["target"], places["rumbo"]) == (">=3.11", "py311", "3.11")
    assert places["matrix"] == '["3.11", "3.12", "3.13"]' and places["pin"] == "3.11\n"
    data = _python_places(Config(name="x", package="x", llm=False, kind="data"))
    assert data["dockerfile"] == "3.11"  # the image builds on the minimum the project promises


@pytest.mark.parametrize("python", ["3.10", "3.99", "3", "py311", "3.11.4"])
def test_unsupported_python_is_refused(python: str) -> None:
    with pytest.raises(ValueError, match=r"unsupported Python .*3\.11, 3\.12, 3\.13"):
        Config(name="x", package="x", llm=False, python=python)


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
