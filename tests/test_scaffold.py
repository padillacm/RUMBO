import ast
import importlib.util
import itertools
import os
import tomllib
from pathlib import Path
from typing import Any

import pytest

from rumbo import __version__
from rumbo.project import Project
from rumbo.scaffold import (
    PROVIDERS,
    Config,
    merge_lines,
    merge_pyproject,
    plan_files,
    scaffold,
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

    assert check_line(proto) == "check: lint types test arch"
    assert check_line(prod) == "check: lint types test arch security evals"
    assert "--cov-fail-under" not in str(prod["Makefile"])


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
    agents.write_text("mine")
    rep = scaffold(
        Config(name="Demo", package="demo", llm=False, rigor="prototype"), simple_project
    )
    assert agents.read_text() == "mine"
    assert "AGENTS.md" in rep.skipped and "pyproject.toml" in rep.skipped
    assert not rep.created and not rep.merged


def test_force_overwrites_but_merges_pyproject_and_gitignore(simple_project: Path) -> None:
    pyproject = simple_project / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace("dependencies = []", 'dependencies = ["requests>=2"]')
    )
    (simple_project / ".gitignore").write_text("node_modules/\n")
    brief = simple_project / "docs" / "product" / "brief.md"
    brief.write_text("# My brief\n")
    rep = scaffold(
        Config(name="Demo", package="demo", llm=False, rigor="prototype"),
        simple_project,
        force=True,
    )
    assert '"requests>=2"' in pyproject.read_text()
    assert "node_modules/" in (simple_project / ".gitignore").read_text()
    assert "rumbo:template" in brief.read_text()
    assert (simple_project / "docs" / "product" / "brief.md.bak").read_text() == "# My brief\n"
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
    rumbo_md.write_text("old")
    brief = llm_project / "docs" / "product" / "brief.md"
    brief.write_text("# written by the human\n")
    makefile = llm_project / "Makefile"
    makefile.write_text(makefile.read_text() + "\nextra: ; echo hi\n")
    (llm_project / ".claude" / "commands" / "rumbo-r.md").unlink()
    pyproject = llm_project / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace(f'version = "{__version__}"', 'version = "0.0.1"')
    )

    rep = update(Project.load(llm_project), llm_project)
    assert rumbo_md.read_text() != "old"
    assert brief.read_text() == "# written by the human\n"
    assert "extra:" in makefile.read_text()
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
    original = config.read_text()
    bumped = original.replace("rev: v", "rev: v9.", 1)
    assert bumped != original
    config.write_text(bumped)
    assert ".pre-commit-config.yaml" not in update_diff(Project.load(llm_project), llm_project)
    rep = update(Project.load(llm_project), llm_project)
    assert ".pre-commit-config.yaml" not in rep.differs
    config.write_text(original)


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
