"""Mounts the RUMBO harness (Part A) in a project directory, and updates it later."""

from __future__ import annotations

import copy
import datetime as dt
import difflib
import json
import keyword
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO

from rumbo import __version__
from rumbo.project import (
    DEFAULT_PYTHON,
    DEFAULT_SRC,
    DEFAULT_TYPE_CHECKER,
    HEXAGONAL_LAYERS,
    KINDS,
    LAYOUTS,
    RIGOR_LEVELS,
    SUPPORTED_PYTHONS,
    TYPE_CHECKERS,
    Project,
    code_root,
    package_path,
    read_utf8,
    resolve_layout,
    validate_axes,
    validate_python,
    validate_src,
)
from rumbo.render import render
from rumbo.sensors.guard import EDIT_TOOLS, SHELL_TOOLS, agent_env_names

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
SENSOR_FILE = PACKAGE_DIR / "sensors" / "check_tests_not_weakened.py"
GUARD_FILE = PACKAGE_DIR / "sensors" / "guard.py"
CHECK_FILE = PACKAGE_DIR / "sensors" / "check.py"


@dataclass(frozen=True)
class Provider:
    """How a model provider is wired into a generated project."""

    sdk_package: str  # PyPI name ("" when no SDK is installed)
    sdk_module: str  # importable root module, forbidden inside domain/
    env_key: str  # environment variable holding the credential ("" = none)
    note: str = ""


PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider("anthropic", "anthropic", "ANTHROPIC_API_KEY"),
    "openai": Provider("openai", "openai", "OPENAI_API_KEY"),
    "google": Provider("google-genai", "google", "GEMINI_API_KEY"),
    "mistral": Provider("mistralai", "mistralai", "MISTRAL_API_KEY"),
    # Calls the model through the Claude Code CLI (`claude -p`), using the developer's
    # own subscription: no API key on the machine. CI (nightly) needs CLAUDE_CODE_OAUTH_TOKEN.
    "claude-code": Provider("", "", "CLAUDE_CODE_OAUTH_TOKEN", "uses the `claude` CLI"),
}
OTHER_PROVIDER = "other"

# ADR-0011: the development tools scale with rigor; `import-linter` with the layout. `uv add`
# resolves the versions, nothing is pinned here. `pre-commit` carries the guard's git hooks.
# ADR-0015: the type-checker slot holds `pyright` or `mypy` (`Config.types`).
BASE_DEV_DEPS = ["ruff", DEFAULT_TYPE_CHECKER, "pytest", "hypothesis", "pre-commit"]
ARCH_DEV_DEPS = ["import-linter"]
MVP_DEV_DEPS = ["pytest-cov", "bandit", "pip-audit"]
PRODUCTION_DEV_DEPS = ["mutmut"]
# ADR-0010: the HTTP skeleton belongs to the `service` kind; the LLM capability only adds
# `pydantic` (and the provider SDK) on top of it.
SERVICE_DEPS = ["fastapi", "uvicorn", "httpx"]
LLM_DEPS = ["pydantic"]

# Template paths (prefixes) that are only generated when the condition holds. A path must
# satisfy every prefix it matches (`evals/graders/judge.py` needs `evals` AND `llm`).
CONDITIONS: dict[str, Callable[[Config], bool]] = {
    # The LLM capability (ADR-0010: orthogonal to kind and layout; forces hexagonal).
    "dot_env.example": lambda c: c.llm,
    "src/__pkg__/prompts/": lambda c: c.llm,
    "src/__pkg__/app/entrypoint.py": lambda c: c.llm,
    "src/__pkg__/adapters/llm.py": lambda c: c.llm,
    "src/__pkg__/adapters/claude_code.py": lambda c: c.llm and c.provider == "claude-code",
    "src/__pkg__/app/ports.py": lambda c: c.llm,
    "src/__pkg__/app/budget.py": lambda c: c.llm,
    "src/__pkg__/app/telemetry.py": lambda c: c.llm,
    "evals/graders/judge.py": lambda c: c.llm,
    "evals/calibration/": lambda c: c.llm,
    "tests/unit/test_budget.py": lambda c: c.llm,
    "tests/unit/test_telemetry.py": lambda c: c.llm,
    "tests/unit/test_entrypoint.py": lambda c: c.llm,
    "tests/unit/test_prompts.py": lambda c: c.llm,
    "tests/unit/test_judge.py": lambda c: c.llm,
    "tests/unit/test_claude_code_adapter.py": lambda c: c.llm and c.provider == "claude-code",
    # Evals: LLM products and data pipelines (deterministic graders; the judge is llm-only).
    "evals/": lambda c: c.evals,
    "docs/evals/": lambda c: c.evals,
    "tests/unit/test_graders.py": lambda c: c.evals,
    # A data pipeline without a model still needs an entry the evals can call.
    "src/__pkg__/pipeline.py": lambda c: c.kind == "data" and not c.llm,
    "tests/unit/test_pipeline.py": lambda c: c.kind == "data" and not c.llm,
    # The kind: what the project is (ADR-0010 table).
    "src/__pkg__/api/http.py": lambda c: c.kind == "service",
    "tests/integration/test_http.py": lambda c: c.kind == "service",
    "infra/": lambda c: c.kind in ("service", "data"),
    "docs/ops/runbook.md": lambda c: c.kind in ("service", "data"),
    "docs/ops/slos.md": lambda c: c.kind == "service",
    "CHANGELOG.md": lambda c: c.kind in ("library", "cli"),
    "dot_github/workflows/release.yml": lambda c: c.kind in ("library", "cli"),
    # The layout: hexagonal packages come from templates, layered ones from plan_files().
    # A service keeps its `api/` boundary whatever the layout (the HTTP skeleton lives there).
    "src/__pkg__/domain/": lambda c: c.layout == "hexagonal",
    "src/__pkg__/app/": lambda c: c.layout == "hexagonal",
    "src/__pkg__/adapters/": lambda c: c.layout == "hexagonal",
    "src/__pkg__/api/": lambda c: c.layout == "hexagonal" or c.kind == "service",
    # Rigor.
    "dot_github/workflows/nightly.yml": lambda c: c.rigor != "prototype",
    "dot_github/dependabot.yml": lambda c: c.rigor != "prototype",
}

# Files the framework owns: `rumbo update` replaces them; the human never edits them.
FRAMEWORK_OWNED = (
    "RUMBO.md",
    "scripts/check_tests_not_weakened.py",
    "scripts/rumbo_guard.py",
    "scripts/rumbo_check.py",
    "docs/adr/0000-template.md",
    "specs/0000-template.md",
    "docs/postmortems/0000-template.md",
    "spikes/README.md",
)
FRAMEWORK_OWNED_PREFIXES = (".claude/commands/",)
# Files a rigor level brings (ADR-0011): `rumbo update` renders them when the level requires
# them and they do not exist yet; existing copies are the human's.
RIGOR_FILES = (".github/workflows/nightly.yml", ".github/dependabot.yml")
# Harness files the human adapts: `rumbo update` reports when they drift from the template
# (and `--diff` shows how). Documents (docs/, specs/, src/, tests/) are the human's: never listed.
HARNESS_PREFIXES = (
    "Makefile",
    "AGENTS.md",
    "CLAUDE.md",
    "SECURITY.md",
    ".github/",
    ".pre-commit-config.yaml",
    ".gitattributes",
    ".claude/",
    ".env.example",
    "infra/",
    "evals/run_evals.py",
    "evals/graders/",
)
# The architecture test is a harness file too, under the project's own tests directory
# ([tool.rumbo].tests, ADR-0020): `is_harness_file` adds it.
HARNESS_TEST = "architecture/"

_PKG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# ADR-0029: an adopted project keeps the name its package has on disk.
_ADOPTED_PKG_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Directories the harness writes into at the project root.
_HARNESS_DIRS = frozenset({"test", "tests", "src", "evals", "scripts", "spikes"})
# Sub-packages a new project may generate inside its package (ADR-0010): a package of the same
# name would hold a module named like itself (`app.app`).
_GENERATED = frozenset({"domain", "app", "api"})
_RESERVED = _HARNESS_DIRS | _GENERATED
_NAME_RE = re.compile(r"^[^\x00-\x1f\\\"]+$")
# A PyPI distribution name with optional extras (PEP 508), no version specifier: `uv add`
# resolves the version, and the name is embedded verbatim in pyproject.toml.
_SDK_PACKAGE_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?(?:\[[A-Za-z0-9._,-]+\])?$"
)
_SDK_MODULE_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")


def valid_package(
    package: str, adopted: bool = False, generated: Collection[str] = _GENERATED
) -> bool:
    """Whether `package` can be the package of a project (ADR-0029).

    A new project: snake_case, not a keyword, not a directory the harness writes into and not
    one of `generated`, the sub-packages its layout would write inside it (all three while
    the layout is not known). An adopted project keeps its own name: any identifier that is
    not a keyword.
    """
    if adopted:
        return bool(_ADOPTED_PKG_RE.match(package)) and not keyword.iskeyword(package)
    return (
        bool(_PKG_RE.match(package))
        and not keyword.iskeyword(package)
        and package not in _HARNESS_DIRS
        and package not in generated
    )


def valid_name(name: str) -> bool:
    """Project names are embedded in TOML strings and Python code: keep them plain."""
    return bool(_NAME_RE.match(name.strip())) and 1 <= len(name.strip()) <= 80


def valid_sdk_package(package: str) -> bool:
    """PyPI name, optionally with extras (`foo[bar]`); embedded in pyproject.toml and `uv add`."""
    return bool(_SDK_PACKAGE_RE.match(package)) and len(package) <= 100


def valid_sdk_module(module: str) -> bool:
    """Dotted import path; its root is embedded in pyproject.toml and test_architecture.py."""
    return bool(_SDK_MODULE_RE.match(module)) and len(module) <= 100


_TESTS_DIR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def valid_tests_dir(tests_dir: str) -> bool:
    """A plain top-level folder name (`tests`, `test`): embedded in pyproject.toml and paths."""
    return bool(_TESTS_DIR_RE.match(tests_dir)) and len(tests_dir) <= 50


def to_package(name: str) -> str:
    """Best-effort snake_case package name derived from a project name."""
    from rumbo.project import slug

    package = slug(name).replace("-", "_").strip("_") or "app"
    if package[0].isdigit():
        package = f"app_{package}"
    if keyword.iskeyword(package) or package in _RESERVED:
        package = f"{package}_app"
    return package


@dataclass
class Config:
    name: str
    package: str
    llm: bool
    provider: str = ""
    rigor: str = "mvp"
    case: str = ""
    sdk_package: str = ""
    sdk_module: str = ""
    sdk_env_key: str = ""
    # ADR-0010: what the project is and how its package is organised; "" = derived from kind.
    kind: str = "service"
    layout: str = ""
    layers: tuple[str, ...] = ()
    # ADR-0013: the harness is mounted on an existing project (no skeleton, strict steps on the
    # changed files); `tests_dir` is the detected tests directory ("tests" unless adopted).
    adopted: bool = False
    tests_dir: str = "tests"
    # ADR-0014: the minimum Python the project promises; every template that names a
    # version reads it (requires-python, ruff, the CI matrix, the Dockerfile,
    # .python-version).
    python: str = DEFAULT_PYTHON
    # ADR-0015: the type checker (`pyright` or `mypy`): dependency, `types` step, config.
    types: str = DEFAULT_TYPE_CHECKER
    # ADR-0020: the directory that holds the package: "src", or "." when an adopted project
    # keeps it at the root. Every path the tools are pointed at is derived from it.
    src: str = DEFAULT_SRC
    # ADR-0029: what an adopted project already had, decided once at adoption and stored in
    # `[tool.rumbo]`: its own CI workflows (no ci.yml is written) and its own infra/ (nothing
    # is written there). None = not decided yet: `scaffold` and `config_for` decide it.
    own_ci: bool | None = None
    own_infra: bool | None = None
    # ADR-0029: the directories pytest collects, rendered once into `[tool.pytest.ini_options]`
    # (not stored). None = the tests directory; () = no table is written, because the project
    # configures pytest itself.
    testpaths: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        validate_python(self.python)
        validate_src(self.src)
        if not valid_name(self.name):
            raise ValueError(
                f"invalid project name {self.name!r}: no quotes, backslashes or control characters"
            )
        if self.adopted and not valid_package(self.package, adopted=True):
            raise ValueError(
                f"invalid package name (a Python identifier, not a keyword): {self.package!r}"
            )
        if self.adopted and self.src == "." and self.package in _HARNESS_DIRS:
            raise ValueError(
                f"invalid package name {self.package!r}: the harness writes its own files into "
                f"{self.package}/ at the project root"
            )
        if not self.adopted and not valid_package(self.package, generated=()):
            raise ValueError(f"invalid package name (use snake_case): {self.package!r}")
        if not valid_tests_dir(self.tests_dir):
            raise ValueError(f"invalid tests directory (a plain folder name): {self.tests_dir!r}")
        if self.rigor not in RIGOR_LEVELS:
            raise ValueError(f"invalid rigor {self.rigor!r}; use one of {RIGOR_LEVELS}")
        if self.types not in TYPE_CHECKERS:
            raise ValueError(f"invalid type checker {self.types!r}; use one of {TYPE_CHECKERS}")
        if self.kind not in KINDS:
            raise ValueError(f"invalid kind {self.kind!r}; use one of {KINDS}")
        if self.layout and self.layout not in LAYOUTS:
            raise ValueError(f"invalid layout {self.layout!r}; use one of {LAYOUTS}")
        self.layers = tuple(self.layers)
        self.layout = resolve_layout(self.kind, self.layout, self.llm)
        validate_axes(self.kind, self.layout, self.layers)
        if not self.adopted and self.package in self._generated_modules():
            raise ValueError(
                f"invalid package name {self.package!r}: this kind and layout generate a module "
                f"of the same name inside it ({self.package}.{self.package}); choose another name"
            )
        if self.testpaths is not None:
            self.testpaths = tuple(self.testpaths)
        if not self.llm:
            self.provider = ""
            self.sdk_package = self.sdk_module = self.sdk_env_key = ""
            return
        if not self.provider:
            raise ValueError("an LLM product needs a provider (see --provider)")
        known = PROVIDERS.get(self.provider)
        if known:
            self.sdk_package = self.sdk_package or known.sdk_package
            self.sdk_module = self.sdk_module or known.sdk_module
            self.sdk_env_key = self.sdk_env_key or known.env_key
        elif self.provider == OTHER_PROVIDER:
            if not (self.sdk_package and self.sdk_module and self.sdk_env_key):
                raise ValueError(
                    "provider 'other' needs --sdk-package, --sdk-module and --sdk-env-key"
                )
        else:
            raise ValueError(
                f"unknown provider {self.provider!r}; use one of "
                f"{', '.join([*PROVIDERS, OTHER_PROVIDER])}"
            )
        if self.sdk_package and not valid_sdk_package(self.sdk_package):
            raise ValueError(
                f"--sdk-package must be a PyPI name (no version), got {self.sdk_package!r}"
            )
        if self.sdk_module and not valid_sdk_module(self.sdk_module):
            raise ValueError(f"--sdk-module must be an importable module, got {self.sdk_module!r}")
        self.sdk_module = self.sdk_module.split(".")[0]
        if self.sdk_env_key and not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.sdk_env_key):
            raise ValueError(f"--sdk-env-key must look like AN_ENV_VAR, got {self.sdk_env_key!r}")

    @classmethod
    def from_project(cls, project: Project) -> Config:
        return cls(
            name=project.name or project.package,
            package=project.package,
            llm=project.llm,
            provider=project.provider,
            rigor=project.rigor,
            sdk_package=project.sdk_package,
            sdk_module=project.sdk_module,
            sdk_env_key=project.sdk_env_key,
            kind=project.kind,
            layout=project.layout,
            layers=project.layers,
            adopted=project.adopted,
            tests_dir=project.tests_dir,
            python=project.python,
            types=project.types,
            src=project.src,
        )

    # Same semantics as the Project properties (ADR-0010).
    @property
    def evals(self) -> bool:
        """Datasets and graders exist for LLM products and for data pipelines."""
        return self.llm or self.kind == "data"

    @property
    def arch(self) -> bool:
        """Import-linter contracts and the purity rules apply unless the layout is flat."""
        return self.layout != "flat"

    @property
    def layer_names(self) -> tuple[str, ...]:
        """Package layers, outermost first; empty for a flat layout."""
        if self.layout == "hexagonal":
            return HEXAGONAL_LAYERS
        return self.layers if self.layout == "layered" else ()

    @property
    def inner_layer(self) -> str:
        """The pure layer (strict types, mutation target, purity contract); '' when flat."""
        names = self.layer_names
        return names[-1] if names else ""

    def _generated_modules(self) -> set[str]:
        """The reserved names this kind and layout write as sub-packages (ADR-0029)."""
        written = set(self.layer_names) | ({"api"} if self.kind == "service" else set())
        return written & _GENERATED

    @property
    def code_root(self) -> str:
        """What the tools scan (ADR-0020): `src`, or the package when it lives at the root."""
        return code_root(self.src, self.package)

    @property
    def package_path(self) -> str:
        """The package directory relative to the root (ADR-0020): `src/<pkg>` or `<pkg>`."""
        return package_path(self.src, self.package)

    @property
    def pytest_paths(self) -> tuple[str, ...]:
        """What `testpaths` lists (ADR-0029); empty when no pytest table is written."""
        return (self.tests_dir,) if self.testpaths is None else self.testpaths

    @property
    def python_matrix(self) -> tuple[str, ...]:
        """The supported Pythons from the minimum upwards: what a package's CI runs on."""
        return SUPPORTED_PYTHONS[SUPPORTED_PYTHONS.index(self.python) :]

    def domain_forbidden_modules(self) -> list[str]:
        modules = ["httpx", "requests", "fastapi"]
        if self.sdk_module and self.sdk_module not in modules:
            modules.append(self.sdk_module)
        return modules

    def context(self) -> dict[str, object]:
        forbidden = self.domain_forbidden_modules()
        return {
            "name": self.name,
            "name_toml": json.dumps(self.name),  # JSON string escaping is valid TOML
            "name_py": json.dumps(self.name),  # double quotes, as ruff format wants
            "package": self.package,
            "llm": self.llm,
            "provider": self.provider or "none",
            "claude_code": self.provider == "claude-code",
            "sdk_package": self.sdk_package,
            "sdk_module": self.sdk_module,
            "sdk_env_key": self.sdk_env_key,
            "rigor": self.rigor,
            "prototype": self.rigor == "prototype",
            "mvp": self.rigor == "mvp",
            "production": self.rigor == "production",
            "mvp_or_more": self.rigor != "prototype",
            # ADR-0010 axes, as flags and precomputed strings (the engine has no lists).
            "kind": self.kind,
            "layout": self.layout,
            "service": self.kind == "service",
            "library": self.kind == "library",
            "cli": self.kind == "cli",
            "data": self.kind == "data",
            "packaged": self.kind in ("library", "cli"),  # released to a package index
            "deployable": self.kind in ("service", "data"),  # shipped as an image
            "hexagonal": self.layout == "hexagonal",
            "layered": self.layout == "layered",
            "flat": self.layout == "flat",
            "evals": self.evals,
            "arch": self.arch,
            "inner_layer": self.inner_layer,
            # `"pkg.api", "pkg.adapters", ...` for the import-linter layers contract.
            "layers_toml": ", ".join(f'"{self.package}.{x}"' for x in self.layer_names),
            # `["api", "adapters", ...]` for [tool.rumbo].layers (written only when layered).
            "layers_toml_list": "[" + ", ".join(f'"{x}"' for x in self.layer_names) + "]",
            # ADR-0013: adoption of an existing project.
            "adopted": self.adopted,
            "tests_dir": self.tests_dir,
            "custom_tests_dir": self.tests_dir != "tests",
            # ADR-0029: what the adopted project already had, and what pytest collects.
            "own_ci_toml": "true" if self.own_ci else "false",
            "own_infra_toml": "true" if self.own_infra else "false",
            "pytest_table": bool(self.pytest_paths),
            "testpaths_toml": ", ".join(json.dumps(p) for p in self.pytest_paths),
            # ADR-0014: one Python setting feeding every place that names a version.
            "python": self.python,
            "python_nodot": self.python.replace(".", ""),
            "python_matrix": json.dumps(list(self.python_matrix)),
            # ADR-0015: the type checker, as a name and as flags.
            "types": self.types,
            "pyright": self.types == "pyright",
            "mypy": self.types == "mypy",
            # ADR-0020: where the package lives, and the two paths derived from it (a `src`
            # project renders `src` and `src/<pkg>`, as it always did).
            "src": self.src,
            "code_root": self.code_root,
            "package_path": self.package_path,
            # `"src" / "pkg"` or `"pkg"`: the same path as operands of the pathlib `/`.
            "package_path_py": " / ".join(f'"{part}"' for part in self.package_path.split("/")),
            "case": self.case or "(no concrete case yet)",
            "date": dt.date.today().isoformat(),
            "rumbo_version": __version__,
            "forbidden_toml": ", ".join(f'"{m}"' for m in forbidden),
            "forbidden_py": ", ".join(f'"{m}"' for m in forbidden),
        }


@dataclass
class Report:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    # ADR-0029: with --force, files that already equal what would be written (no backup).
    unchanged: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    differs: list[str] = field(default_factory=list)
    commands: list[tuple[str, int]] = field(default_factory=list)
    # Commands the human still has to run (ADR-0011: the `uv add --dev` of a raised rigor).
    suggested: list[str] = field(default_factory=list)
    # What adoption leaves to the human (ADR-0013: the CI steps when a workflow already exists).
    notes: list[str] = field(default_factory=list)
    # ADR-0029: what the human should look at before going on (the Python version, the pytest
    # configuration, a missing build backend, a version key that could not be rewritten).
    warnings: list[str] = field(default_factory=list)
    # Check steps whose command differs from the template of the level, and Makefile aliases
    # the template has and the project lacks: reported by `update`, never rewritten.
    drift: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Created: {len(self.created)}"]
        if self.unchanged:
            lines.append(f"Unchanged (already equal to the template): {len(self.unchanged)}")
        if self.updated:
            lines.append("Updated (framework-owned files):")
            lines += [f"  - {p}" for p in self.updated]
        if self.merged:
            lines.append("Merged (review them):")
            lines += [f"  - {p}" for p in self.merged]
        if self.skipped:
            lines.append("Already existed, left untouched (review by hand):")
            lines += [f"  - {p}" for p in self.skipped]
        if self.differs:
            lines.append("Differ from the current template (compare by hand if you want them):")
            lines += [f"  - {p}" for p in self.differs]
        failed = [c for c, rc in self.commands if rc != 0]
        if failed:
            lines.append("Commands that failed (see output above):")
            lines += [f"  - {c}" for c in failed]
        if self.drift:
            lines.append(
                "Not what a fresh project of this level has (yours are kept; edit by hand):"
            )
            lines += [f"  - {item}" for item in self.drift]
        if self.suggested:
            lines.append("Run to complete the update:")
            lines += [f"  {c}" for c in self.suggested]
        if self.warnings:
            lines.append("Look at these before you go on:")
            lines += ["  - " + w.replace("\n", "\n    ") for w in self.warnings]
        lines += self.notes
        return "\n".join(lines)


def target_path(rel: str, cfg: Config) -> str:
    parts = []
    for i, part in enumerate(rel.split("/")):
        part = part.replace("__pkg__", cfg.package)
        if part.startswith("dot_"):
            part = "." + part[4:]
        if i == 0 and part == "tests" and "/" in rel:
            part = cfg.tests_dir  # ADR-0013: the project's own tests directory
        parts.append(part)
    return "/".join(parts).removesuffix(".tmpl")


def _included(rel: str, cfg: Config) -> bool:
    rel = rel.removesuffix(".tmpl")
    return all(cond(cfg) for prefix, cond in CONDITIONS.items() if rel.startswith(prefix))


def plan_files(cfg: Config, root: Path | None = None) -> dict[str, str | Path]:
    """Returns {target path: rendered content, or a path to copy verbatim}.

    For an adopted project (ADR-0013) the skeleton is dropped; `root` lets the plan also respect
    what the project already had when it was adopted (its `infra/`, its CI workflow), as
    `[tool.rumbo]` recorded it (ADR-0029).
    """
    if cfg.adopted and root is not None:
        cfg = with_adoption_state(cfg, root)
    ctx = cfg.context()
    plan: dict[str, str | Path] = {}
    for src in sorted(TEMPLATES_DIR.rglob("*")):
        if src.is_dir() or "__pycache__" in src.parts:
            continue
        rel = src.relative_to(TEMPLATES_DIR).as_posix()
        if not _included(rel, cfg):
            continue
        target = target_path(rel, cfg)
        if rel.endswith(".tmpl"):
            plan[target] = render(src.read_text(encoding="utf-8"), ctx)
        else:
            plan[target] = src
    if cfg.layout == "layered":
        for layer in cfg.layers:
            plan[f"src/{cfg.package}/{layer}/__init__.py"] = _layer_module(cfg, layer)
    plan["scripts/check_tests_not_weakened.py"] = SENSOR_FILE
    plan["scripts/rumbo_guard.py"] = GUARD_FILE
    plan["scripts/rumbo_check.py"] = CHECK_FILE
    plan["RUMBO.md"] = rumbo_md()
    if cfg.adopted:
        plan = adoption_plan(plan, cfg)
    return plan


# ADR-0013: the only test files adoption writes (the spikes/purity rules and the sensor's tests).
ADOPTED_TESTS = ("architecture/test_architecture.py", "unit/test_check_tests_not_weakened.py")
CI_WORKFLOW = ".github/workflows/ci.yml"
CI_STEPS_NOTE = (
    "Your .github/workflows/ already has a workflow, so ci.yml was not written. Add these two "
    "steps to it (after `uv sync --locked`; the checkout needs `fetch-depth: 0`):\n"
    "  - run: uv run python scripts/rumbo_check.py\n"
    "  - run: uv run python scripts/check_tests_not_weakened.py --strict\n"
    "    env:\n"
    "      RUMBO_BASE_REF: ${{ github.event.pull_request.base.sha || github.event.before }}"
)


def existing_workflows(root: Path) -> list[str]:
    """The workflow files a project already has under .github/workflows/ (sorted names)."""
    folder = root / ".github" / "workflows"
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml"))


# The workflow files RUMBO itself generates: on a project adopted before `own_ci` was recorded,
# only another name proves that the project had a workflow of its own (ADR-0029).
RUMBO_WORKFLOWS = frozenset({"ci.yml", "nightly.yml", "release.yml"})
INFRA_FILE = "infra/Dockerfile"  # the one file RUMBO writes under infra/


def own_parts(root: Path) -> tuple[bool, bool]:
    """Whether the project has its own CI workflows and its own `infra/` (ADR-0029).

    `[tool.rumbo].own_ci` and `.own_infra` when adoption recorded them. A project that is
    being adopted now (no `adopted` key yet) is looked at as it is: any workflow, an `infra/`
    directory. A project adopted before the keys existed already holds RUMBO's own files, so
    only what RUMBO does not write counts: a workflow with another name (or no `ci.yml` beside
    the others), an `infra/` without the Dockerfile RUMBO writes; `rumbo update` then records
    the answer.
    """
    table = _rumbo_table(root / "pyproject.toml")
    workflows = existing_workflows(root)
    infra = (root / "infra").is_dir()
    if table.get("adopted"):
        named = set(workflows) - RUMBO_WORKFLOWS
        found_ci = bool(named) or (bool(workflows) and "ci.yml" not in workflows)
        found_infra = infra and not (root / INFRA_FILE).is_file()
    else:
        found_ci, found_infra = bool(workflows), infra
    own_ci, own_infra = table.get("own_ci"), table.get("own_infra")
    return (
        own_ci if isinstance(own_ci, bool) else found_ci,
        own_infra if isinstance(own_infra, bool) else found_infra,
    )


def with_adoption_state(cfg: Config, root: Path) -> Config:
    """`cfg` with `own_ci` and `own_infra` decided (ADR-0029); values already set are kept."""
    if cfg.own_ci is not None and cfg.own_infra is not None:
        return cfg
    own_ci, own_infra = own_parts(root)
    return replace(
        cfg,
        own_ci=own_ci if cfg.own_ci is None else cfg.own_ci,
        own_infra=own_infra if cfg.own_infra is None else cfg.own_infra,
    )


def adoption_plan(plan: dict[str, str | Path], cfg: Config) -> dict[str, str | Path]:
    """The init plan minus the skeleton an existing project already has (ADR-0013).

    Nothing under `src/`; under the tests directory only the two files of `ADOPTED_TESTS`; no
    `infra/` and no `ci.yml` when the project had its own (`cfg.own_infra`, `cfg.own_ci`).
    """
    keep_tests = {f"{cfg.tests_dir}/{rel}" for rel in ADOPTED_TESTS}
    tests_prefix = f"{cfg.tests_dir}/"
    result = {
        rel: item
        for rel, item in plan.items()
        if not rel.startswith("src/") and (not rel.startswith(tests_prefix) or rel in keep_tests)
    }
    if cfg.own_infra:
        result = {rel: item for rel, item in result.items() if not rel.startswith("infra/")}
    if cfg.own_ci:
        result.pop(CI_WORKFLOW, None)
    return result


def _layer_module(cfg: Config, layer: str) -> str:
    """The `__init__.py` of one layer of a layered package (ADR-0010), outermost first."""
    names = list(cfg.layers)
    below = names[names.index(layer) + 1 :]
    if not below:
        return f'"""Layer {layer}: the innermost one, kept pure (no I/O; strict types)."""\n'
    return f'"""Layer {layer}: may import only {", ".join(below)}."""\n'


def rumbo_md() -> Path:
    for candidate in (TEMPLATES_DIR / "RUMBO.md", PACKAGE_DIR.parents[1] / "RUMBO.md"):
        if candidate.exists():
            return candidate
    raise FileNotFoundError("RUMBO.md not found in the package")


def _content(item: str | Path) -> str:
    return item if isinstance(item, str) else item.read_text(encoding="utf-8")


def _tool_blocks(text: str) -> list[tuple[str, str]]:
    """Splits a pyproject into [tool.X...] blocks → (root 'tool.X', text)."""
    blocks: list[tuple[str, str]] = []
    current: list[str] = []
    root = ""
    for line in text.splitlines(keepends=True):
        match = re.match(r"^\[\[?\s*([\w.\-\"]+)\s*\]\]?", line)
        if match:
            if root.startswith("tool.") and current:
                blocks.append((root, "".join(current)))
            root = ".".join(match.group(1).split(".")[:2])
            current = [line]
        else:
            current.append(line)
    if root.startswith("tool.") and current:
        blocks.append((root, "".join(current)))
    return blocks


def merge_pyproject(existing: str, template: str) -> tuple[str, list[str]]:
    """Adds the RUMBO [tool.*] sections the existing pyproject lacks.

    Returns the merged text and notes (sections added, sections kept as they were).
    """
    data = tomllib.loads(existing)
    present = {f"tool.{k}" for k in data.get("tool", {})}
    backend = str(data.get("build-system", {}).get("build-backend", ""))
    added: dict[str, list[str]] = {}
    kept: set[str] = set()
    for root, block in _tool_blocks(template):
        if root == "tool.hatch" and "hatchling" not in backend:
            continue  # the project uses another build backend (or none)
        if root in present:
            kept.add(root)
        else:
            added.setdefault(root, []).append(block)
    notes = [f"+[{r}]" for r in added] + [
        f"kept your [{r}] (compare it with RUMBO.md A2)" for r in sorted(kept)
    ]
    if "tool.importlinter" in kept and "contracts" not in str(data["tool"].get("importlinter")):
        notes.append("your [tool.importlinter] has no contracts: gate G3 will fail until it does")
    if not added:
        return existing, notes
    extra = "".join(b for blocks in added.values() for b in blocks)
    merged = existing.rstrip("\n") + "\n\n# --- Added by rumbo init ---\n" + extra
    tomllib.loads(merged)  # raises if the result is not valid TOML
    return merged, notes


def merge_lines(existing: str, template: str) -> tuple[str, list[str]]:
    current = {ln.strip() for ln in existing.splitlines()}
    new = [ln for ln in template.splitlines() if ln.strip() and ln.strip() not in current]
    if not new:
        return existing, []
    merged = existing.rstrip("\n") + "\n\n# --- Added by rumbo init ---\n"
    return merged + "\n".join(new) + "\n", new


MERGEABLE = {"pyproject.toml": merge_pyproject, ".gitignore": merge_lines}


def write_text(target: Path, text: str) -> None:
    """Writes `text` as UTF-8 with LF line endings on every platform (F-0004.R7)."""
    with target.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


_write_text = write_text


def _write(target: Path, item: str | Path, rel: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_text(target, _content(item))  # also normalizes CRLF from a Windows checkout to LF
    if rel.startswith("scripts/"):
        target.chmod(0o755)


def _backup(target: Path) -> Path | None:
    """Copies `target` to the first free `.bak`, `.bak.1`, `.bak.2`... (ADR-0029).

    A backup is never overwritten: it may be the only copy of what the human wrote. When one
    of them already holds the same bytes there is nothing to keep, and no copy is made.
    """
    content = target.read_bytes()
    number = 0
    while True:
        suffix = ".bak" if number == 0 else f".bak.{number}"
        candidate = target.with_name(target.name + suffix)
        if not candidate.exists() and not candidate.is_symlink():
            shutil.copyfile(target, candidate)
            return candidate
        if candidate.is_file() and candidate.read_bytes() == content:
            return None
        number += 1


@dataclass(frozen=True)
class _Write:
    """One file `scaffold` decided to write, after reading everything it had to read."""

    rel: str
    item: str | Path  # the rendered text, the merged text, or a file to copy
    backup: bool = False  # --force over a file that differs: keep a copy first
    merged: str = ""  # what the report says of a merge ("" = a created file)


def _blocked(dest: Path, rel: str) -> str:
    """Why `rel` cannot be written under `dest` ('' when it can): a file where a directory
    is needed, or a directory where the file goes."""
    parts = rel.split("/")
    for depth in range(1, len(parts)):
        ancestor = dest.joinpath(*parts[:depth])
        if ancestor.exists() and not ancestor.is_dir():
            return f"{'/'.join(parts[:depth])} is a file, and the harness needs a directory there"
    if (dest / rel).is_dir():
        return f"{rel} is a directory, and the harness writes a file there"
    return ""


def _merge_existing(rel: str, target: Path, template: str) -> tuple[str, str, list[str]]:
    """Reads and merges one of `MERGEABLE`: (current text, merged text, notes).

    Every way the file can be unreadable is a ValueError that names it, raised before
    `scaffold` writes anything (ADR-0029).
    """
    current = read_utf8(target, rel)  # NotUTF8 names the file
    if rel == "pyproject.toml":
        try:
            data = tomllib.loads(current)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"{rel} is not valid TOML: {exc}") from exc
        tool = data.get("tool", {})
        if not isinstance(tool, dict):
            raise ValueError(f"{rel}: `tool` is not a table, so the [tool.*] tables cannot go in")
        rumbo = tool.get("rumbo")
        named = isinstance(rumbo, dict) and (rumbo.get("package") or rumbo.get("pkg"))
        if rumbo is not None and not named:
            # A table that is already there is kept as it is: a partial one (only `agent_env`,
            # say) would leave a harness that no command can load.
            raise ValueError(
                f"{rel} already has a [tool.rumbo] table without a `package` key, and an "
                "existing table is never rewritten: remove it (keep your own keys aside and "
                "put them back afterwards) and run the command again"
            )
    try:
        merged, notes = MERGEABLE[rel](current, template)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"{rel}: the [tool.*] tables RUMBO adds cannot be appended to this file ({exc}); "
            "write `tool` as `[tool.<name>]` tables, not as an inline table"
        ) from exc
    return current, merged, notes


def _plan_writes(
    plan: dict[str, str | Path], dest: Path, force: bool, report: Report
) -> list[_Write]:
    """Decides what to do with every file of the plan; reads, merges and validates, writes
    nothing. Raises ValueError for anything that would have stopped the run half-way."""
    writes: list[_Write] = []
    for rel, item in plan.items():
        target = dest / rel
        blocked = _blocked(dest, rel)
        if blocked:
            raise ValueError(blocked)
        if target.is_symlink():
            report.skipped.append(rel)  # a link is the human's: never followed, never replaced
            continue
        if not target.exists():
            writes.append(_Write(rel, item))
            continue
        if rel in MERGEABLE:
            current, merged, notes = _merge_existing(rel, target, _content(item))
            if merged == current:
                report.skipped.append(rel)
                continue
            detail = "; ".join(notes) if rel == "pyproject.toml" else f"+{len(notes)} lines"
            writes.append(_Write(rel, merged, merged=f"{rel} ({detail})"))
        elif not force:
            report.skipped.append(rel)
        elif target.read_bytes() == _content(item).encode("utf-8"):
            report.unchanged.append(rel)
        else:
            writes.append(_Write(rel, item, backup=True))
    return writes


def scaffold(cfg: Config, dest: Path, force: bool = False) -> Report:
    """Creates the harness. Existing files are merged (pyproject, .gitignore) or skipped.

    With ``force`` the other existing files that differ from what would be written are
    overwritten after a backup copy (``.bak``, ``.bak.1``...: a backup is never overwritten);
    pyproject.toml and .gitignore are always merged, never replaced.

    Everything that has to be read is read, merged and validated before the first file is
    written (ADR-0029): a ValueError leaves the directory as it was.
    """
    report = Report()
    if dest.exists() and not dest.is_dir():
        raise ValueError(f"{dest} is not a directory")
    if cfg.adopted:
        cfg = _adoption(cfg, dest, report)
    plan = plan_files(cfg, dest)
    writes = _plan_writes(plan, dest, force, report)
    _existing_pyproject_warnings(cfg, dest, report)
    _guard_notes(dest, plan, {w.rel for w in writes}, report)
    if cfg.adopted and cfg.kind == "service" and INFRA_FILE in {w.rel for w in writes}:
        entry = f"{cfg.package_path}/api/http.py"
        if not (dest / entry).is_file():
            report.warnings.append(
                f"{INFRA_FILE} starts `uvicorn {cfg.package}.api.http:app`, and {entry} does "
                "not exist: edit its CMD to start your application"
            )
    dest.mkdir(parents=True, exist_ok=True)
    for write in writes:
        target = dest / write.rel
        if write.backup:
            _backup(target)
        _write(target, write.item, write.rel)
        (report.merged if write.merged else report.created).append(write.merged or write.rel)
    return report


# --------------------------------------------- ADR-0029: what adoption finds and leaves alone
def missing_modules(cfg: Config, root: Path) -> list[str]:
    """The modules the configuration of an adopted project names and the disk does not have:
    one directory per layer (the import-linter contracts, the strict type target, the
    mutation target) and the entry the evals call."""
    base = root / cfg.package_path
    missing = [
        f"{cfg.package_path}/{layer}/" for layer in cfg.layer_names if not (base / layer).is_dir()
    ]
    if cfg.llm and not (base / "app" / "entrypoint.py").is_file():
        missing.append(f"{cfg.package_path}/app/entrypoint.py")
    if cfg.kind == "data" and not cfg.llm and not (base / "pipeline.py").is_file():
        missing.append(f"{cfg.package_path}/pipeline.py")
    return missing


PYTEST_FILES = ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini")
_PYTEST_SECTIONS = (("tox.ini", "pytest"), ("setup.cfg", "tool:pytest"))


def pytest_configuration(root: Path) -> str:
    """Where the project configures pytest ('' when nowhere), in pytest's own order of
    precedence: its own files, `[tool.pytest]` in pyproject.toml, `[pytest]` in tox.ini,
    `[tool:pytest]` in setup.cfg."""
    for name in PYTEST_FILES:
        if (root / name).is_file():
            return name
    try:
        tool = read_pyproject(root).get("tool")
    except ValueError:  # unreadable: `scaffold` reports it when it merges the file
        tool = None
    if isinstance(tool, dict) and "pytest" in tool:
        return "pyproject.toml [tool.pytest]"
    for name, section in _PYTEST_SECTIONS:
        path = root / name
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(rf"(?m)^[ \t]*\[{re.escape(section)}\]", text):
                return f"{name} [{section}]"
    return ""


# Directories where a `test_*.py` is not one of the project's tests.
_NOT_TESTS = frozenset({"node_modules", "__pycache__", "spikes", "build", "dist", "site-packages"})
_TEST_FILE = re.compile(r"^(test_.*|.*_test)\.py$")
_SCAN_DEPTH = 8


def stray_tests(root: Path, tests_dir: str) -> list[str]:
    """Directories outside `tests_dir` that hold test files (`test_*.py`, `*_test.py`), as
    relative POSIX paths; "." is the project root itself.

    A file under a directory named `tests` or `test` is reported as that directory. Hidden
    directories, virtual environments, build output and `spikes/` are not looked into.
    """
    found: set[str] = set()
    for folder, dirs, files in os.walk(root):
        parts = Path(folder).relative_to(root).parts
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".")
            and d not in _NOT_TESTS
            and not (not parts and d == tests_dir)
            and not (Path(folder) / d / "pyvenv.cfg").is_file()
            and len(parts) < _SCAN_DEPTH
        )
        if not any(_TEST_FILE.match(name) for name in files):
            continue
        named = next((i for i, part in enumerate(parts) if part in ("tests", "test")), None)
        kept = parts if named is None else parts[: named + 1]
        found.add("/".join(kept) or ".")
    return sorted(d for d in found if not any(d.startswith(f"{other}/") for other in found))


def _adoption(cfg: Config, dest: Path, report: Report) -> Config:
    """What adoption decides by looking at the project, before anything is written
    (ADR-0029): it configures only what is on disk, records once what the project already
    had, and leaves the project's pytest configuration alone."""
    if not (dest / cfg.package_path).is_dir():
        raise ValueError(
            f"--adopt needs the package to exist: there is no {cfg.package_path}/ in {dest}"
        )
    missing = missing_modules(cfg, dest)
    if missing:
        raise ValueError(
            "--adopt configures only what is on disk (ADR-0029), and this configuration names "
            f"modules that do not exist: {', '.join(missing)}. Create them first (an "
            "`__init__.py` is enough for a layer), or adopt without them: --layout flat for "
            "the layers, --no-llm or another --kind for the evals entry"
        )
    first = not _rumbo_table(dest / "pyproject.toml").get("adopted")
    cfg = with_adoption_state(cfg, dest)
    if cfg.own_ci:
        report.notes.append(CI_STEPS_NOTE)
    if not first or cfg.testpaths is not None:
        return cfg
    tests = cfg.tests_dir
    where = pytest_configuration(dest)
    strays = stray_tests(dest, tests)
    if where:
        cfg = replace(cfg, testpaths=())
        report.warnings.append(
            f"pytest is configured in {where}: RUMBO wrote no [tool.pytest.ini_options] and "
            f"left yours as it is. Make sure it collects {tests}/architecture and {tests}/unit, "
            "the two test files RUMBO wrote."
        )
    elif "." in strays:
        cfg = replace(cfg, testpaths=())
        report.warnings.append(
            "Test files were found in the project root itself: no `testpaths` was written, so "
            "pytest keeps collecting from the project root as it did."
        )
    else:
        cfg = replace(cfg, testpaths=(tests, *strays))
    if not (dest / tests).is_dir():
        report.warnings.append(
            f"No tests directory was found at the project root (tests/ or test/): RUMBO "
            f"created {tests}/ for its two test files, and [tool.rumbo].tests names it."
        )
    outside = [d for d in strays if d != "."]
    if outside:
        report.warnings.append(
            f"Test files were found outside {tests}/: {', '.join(outside)}. pytest "
            + ("still collects them" if not cfg.testpaths else "collects them (`testpaths`)")
            + f", but the anti-cheat sensor, `rumbo trace` and gate G4 watch {tests}/ only "
            "([tool.rumbo].tests names one directory): move them there to have them watched."
        )
    return cfg


def _existing_pyproject_warnings(cfg: Config, dest: Path, report: Report) -> None:
    """What RUMBO does not decide for a pyproject.toml that was already there (ADR-0029)."""
    try:
        data = read_pyproject(dest)
    except ValueError:
        return
    if not data or _rumbo_table(dest / "pyproject.toml"):
        return  # no file yet (RUMBO writes a whole one), or said when the harness was mounted
    if "build-system" not in data:
        report.warnings.append(
            f"pyproject.toml has no [build-system]: uv treats the project as not installable, "
            f"so `{cfg.package}` is not installed in the environment and `import {cfg.package}` "
            "can fail under `uv run pytest`. RUMBO did not add a build backend: that is yours "
            "to choose. One that fits this layout:\n"
            '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n\n'
            f'[tool.hatch.build.targets.wheel]\npackages = ["{cfg.package_path}"]'
        )
    if not isinstance(data.get("project"), dict):
        report.warnings.append(
            "pyproject.toml has no [project] table: `uv add` needs one (name and version) "
            "before it can add the development tools."
        )


# The three Claude Code hooks that call the guard: (event, argument, tools it must match).
GUARD_HOOKS = (
    ("PreToolUse", "pre-tool", (*EDIT_TOOLS, *SHELL_TOOLS)),
    ("PostToolUse", "post-tool", SHELL_TOOLS),
    ("Stop", "stop", ()),
)
GUARD_GIT_STAGES = ("pre-commit", "commit-msg")
SETTINGS_FILE = ".claude/settings.json"
PRE_COMMIT_FILE = ".pre-commit-config.yaml"
_GUARD_HOOK_MARK = "      - id: rumbo-guard\n"


def settings_snippet() -> str:
    """The `hooks` object of the template `.claude/settings.json`, as JSON text."""
    template = TEMPLATES_DIR / "dot_claude" / "settings.json"
    hooks = json.loads(template.read_text(encoding="utf-8"))["hooks"]
    return json.dumps({"hooks": hooks}, indent=2) + "\n"


def pre_commit_snippet() -> str:
    """The guard's two local hooks of the template `.pre-commit-config.yaml`, as an entry of
    `repos:`, and the two top-level keys they need."""
    template = (TEMPLATES_DIR / "dot_pre-commit-config.yaml.tmpl").read_text(encoding="utf-8")
    hooks = template[template.index(_GUARD_HOOK_MARK) :]
    return (
        "  - repo: local\n    hooks:\n" + hooks.rstrip("\n") + "\n"
        "default_install_hook_types: [pre-commit, commit-msg]\n"
        "default_stages: [pre-commit]\n"
    )


def _calls_guard(text: object, argument: str) -> bool:
    pattern = rf"rumbo_guard\.py[\"']?\s+{re.escape(argument)}(?![\w-])"
    return isinstance(text, str) and re.search(pattern, text) is not None


def _matcher_covers(matcher: object, tool: str) -> bool:
    """Whether a Claude Code hook matcher (a regular expression; empty or `*` = every tool)
    matches `tool`."""
    if matcher is None or matcher in ("", "*"):
        return True
    if not isinstance(matcher, str):
        return False
    try:
        return re.fullmatch(matcher, tool) is not None
    except re.error:
        return tool in matcher.split("|")


def _settings_wiring(text: str) -> tuple[list[str], list[str]]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        return [f"{SETTINGS_FILE}: not valid JSON ({exc})"], []
    hooks = data.get("hooks") if isinstance(data, dict) else None
    hooks = hooks if isinstance(hooks, dict) else {}
    problems: list[str] = []
    warnings: list[str] = []
    for event, argument, tools in GUARD_HOOKS:
        entries = hooks.get(event)
        calling = [
            entry
            for entry in (entries if isinstance(entries, list) else [])
            if isinstance(entry, dict)
            and isinstance(entry.get("hooks"), list)
            and any(
                isinstance(hook, dict) and _calls_guard(hook.get("command"), argument)
                for hook in entry["hooks"]
            )
        ]
        if not calling:
            problems.append(
                f"{SETTINGS_FILE}: no {event} hook runs scripts/rumbo_guard.py {argument}"
            )
            continue
        unmatched = [
            tool
            for tool in tools
            if not any(_matcher_covers(entry.get("matcher"), tool) for entry in calling)
        ]
        if unmatched:
            warnings.append(
                f"{SETTINGS_FILE}: the {event} hook of the guard does not match the "
                f"{', '.join(unmatched)} tool, so what an agent does through it is not checked; "
                f'set its "matcher" to "{"|".join(tools)}"'
            )
    return problems, warnings


def _pre_commit_wiring(text: str) -> list[str]:
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    return [
        f"{PRE_COMMIT_FILE}: no hook runs scripts/rumbo_guard.py {stage}"
        for stage in GUARD_GIT_STAGES
        if not any(_calls_guard(line, stage) for line in lines)
    ]


def guard_wiring(root: Path) -> tuple[list[str], list[str]]:
    """Whether the guard is wired where it has to run (ADR-0029): (problems, warnings).

    A problem: `.claude/settings.json` lacks one of the three hooks that call
    `scripts/rumbo_guard.py`, or `.pre-commit-config.yaml` does not call it for `pre-commit`
    and `commit-msg`. A warning: a hook is there but its matcher misses a tool the guard
    judges (a project created before `PowerShell` was matched). A file that does not exist
    is not judged here: `rumbo doctor` reports it as missing.
    """
    problems: list[str] = []
    warnings: list[str] = []
    settings = root / SETTINGS_FILE
    if settings.is_file():
        try:
            problems, warnings = _settings_wiring(read_utf8(settings, SETTINGS_FILE))
        except ValueError as exc:
            problems = [str(exc)]
    hooks = root / PRE_COMMIT_FILE
    if hooks.is_file():
        try:
            problems += _pre_commit_wiring(read_utf8(hooks, PRE_COMMIT_FILE))
        except ValueError as exc:
            problems.append(str(exc))
    return problems, warnings


def _guard_notes(
    dest: Path, plan: dict[str, str | Path], written: set[str], report: Report
) -> None:
    """The exact text to add to a settings.json or a pre-commit configuration that was there
    before and does not call the guard (ADR-0029): the file is left untouched."""
    problems, _ = guard_wiring(dest)

    def unwired(rel: str) -> bool:
        kept = rel in plan and rel not in written
        return kept and any(problem.startswith(rel) for problem in problems)

    if unwired(SETTINGS_FILE):
        report.notes.append(
            f"Your {SETTINGS_FILE} was left untouched, so the guard is not wired into Claude "
            "Code. Merge this into it (the three hooks call scripts/rumbo_guard.py; `rumbo "
            "doctor` checks them):\n" + settings_snippet()
        )
    if unwired(PRE_COMMIT_FILE):
        report.notes.append(
            f"Your {PRE_COMMIT_FILE} was left untouched, so the guard does not run as a git "
            "hook. Add this entry at the end of `repos:` and the two top-level keys (merge them "
            "with yours if you already set them), then run `uv run pre-commit install`:\n"
            + pre_commit_snippet()
        )


_REV_LINE = re.compile(r"^(\s*rev:\s*).*$", re.M)


def _comparable(rel: str, text: str) -> str:
    """Text used to decide whether a project file drifted from its template.

    `pre-commit autoupdate` (run by `rumbo init`) rewrites the `rev:` lines of
    .pre-commit-config.yaml on purpose: they are not drift.
    """
    if rel == ".pre-commit-config.yaml":
        return _REV_LINE.sub(r"\1<rev>", text)
    return text


def is_framework_owned(rel: str) -> bool:
    return rel in FRAMEWORK_OWNED or rel.startswith(FRAMEWORK_OWNED_PREFIXES)


def is_harness_file(rel: str, tests_dir: str = "tests") -> bool:
    """A file of the harness the human adapts (or the framework owns): its drift is reported.

    The architecture test counts under the project's own tests directory (ADR-0020).
    """
    return (
        is_framework_owned(rel)
        or rel.startswith(HARNESS_PREFIXES)
        or rel.startswith(f"{tests_dir}/{HARNESS_TEST}")
    )


class NewerHarness(ValueError):
    """The project's harness is newer than this CLI: `update` would downgrade it (ADR-0029)."""


def release_key(version: str) -> tuple[int, ...]:
    """The numeric release segments of a version, for comparison: `0.4.10` > `0.4.9`.

    Trailing zeros do not count (`1.0` equals `1.0.0`), what follows the release (`rc1`,
    `.dev0`, `+local`) is ignored, and a string without a release is `()`: older than any.
    """
    match = re.match(r"\s*v?(\d+(?:\.\d+)*)", version)
    numbers = [int(part) for part in match.group(1).split(".")] if match else []
    while numbers and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers)


def update(project: Project, dest: Path, allow_downgrade: bool = False) -> Report:
    """Refreshes the files the framework owns; never touches documents the human edits.

    Other template files that differ from the current template are only listed. A project
    whose harness is newer than this CLI is refused (NewerHarness) unless `allow_downgrade`.
    """
    if not allow_downgrade and release_key(project.harness_version) > release_key(__version__):
        raise NewerHarness(
            f"the harness of this project is version {project.harness_version}, newer than this "
            f"rumbo ({__version__}): `rumbo update` would replace its files with older ones. "
            "Upgrade rumbo, or pass --allow-downgrade to do it anyway"
        )
    cfg = config_for(project, dest)
    report = Report()
    plan = plan_files(cfg, dest)
    for rel, item in plan.items():
        target = dest / rel
        new_text = _content(item)
        if not target.exists():
            if is_framework_owned(rel) or rel.startswith(".claude/") or rel in RIGOR_FILES:
                _write(target, item, rel)
                report.created.append(rel)
            elif is_harness_file(rel, cfg.tests_dir):
                report.differs.append(f"{rel} (missing)")
            continue
        current = read_utf8(target, rel)
        if current == new_text or _comparable(rel, current) == _comparable(rel, new_text):
            continue
        if is_framework_owned(rel):
            _write(target, item, rel)
            report.updated.append(rel)
        elif rel not in MERGEABLE and is_harness_file(rel, cfg.tests_dir):
            report.differs.append(rel)
    pyproject = dest / "pyproject.toml"
    template = _content(plan["pyproject.toml"])
    _add_checks_table(pyproject, template, report)
    _add_missing_steps(pyproject, template, report)
    _add_axes_keys(pyproject, cfg, report)
    _add_python_key(pyproject, cfg, report)
    _add_src_key(pyproject, cfg, report)
    if cfg.adopted:  # ADR-0029: what the project had is recorded once, like `src`
        own = {"own_ci": cfg.own_ci, "own_infra": cfg.own_infra}
        _add_rumbo_keys(pyproject, {k: "true" if v else "false" for k, v in own.items()}, report)
    _bump_harness_version(pyproject, report)
    _report_drift(dest, plan, report)
    missing = missing_dev_dependencies(cfg, read_utf8(pyproject, "pyproject.toml"))
    if missing:
        report.suggested.append(f"uv add --dev {' '.join(missing)}")
    return report


_MAKE_TARGET = re.compile(r"(?m)^([A-Za-z0-9][\w.-]*)[ \t]*:(?!=)")


def _report_drift(dest: Path, plan: dict[str, str | Path], report: Report) -> None:
    """Lists what a fresh project of this level has and this one does not: a check step with
    another command, a Makefile alias that is missing. Nothing is rewritten: the table and
    the Makefile are the human's (ADR-0011)."""
    wanted = _rendered_checks(_content(plan["pyproject.toml"]))
    mine = _rumbo_table(dest / "pyproject.toml").get("checks")
    for step, command in wanted.items():
        if isinstance(mine, dict) and step in mine and str(mine[step]) != command:
            report.drift.append(
                f"differs from the template: {step} (yours: {mine[step]} | template: {command})"
            )
    makefile = dest / "Makefile"
    if "Makefile" not in plan or not makefile.is_file():
        return
    try:
        have = set(_MAKE_TARGET.findall(read_utf8(makefile, "Makefile")))
    except ValueError:
        return
    for alias in _MAKE_TARGET.findall(_content(plan["Makefile"])):
        if alias not in have:
            report.drift.append(f"Makefile has no `{alias}:` alias (the template has it)")


_CHECKS_HEADER = "[tool.rumbo.checks]"


def _add_checks_table(pyproject: Path, template: str, report: Report) -> None:
    """Appends the rendered ``[tool.rumbo.checks]`` to a project generated before ADR-0006.

    The runner `rumbo update` copies in needs the table; without it every Stop hook would
    report a broken environment. The steps are only added, never replaced.
    """
    current = read_utf8(pyproject, "pyproject.toml")
    try:
        rumbo = tomllib.loads(current).get("tool", {}).get("rumbo")
    except tomllib.TOMLDecodeError:
        return
    if not isinstance(rumbo, dict) or "checks" in rumbo:
        return
    block = next(
        (text for root, text in _tool_blocks(template) if text.startswith(_CHECKS_HEADER)), None
    )
    if block is None:
        return
    merged = current.rstrip("\n") + "\n\n# --- Added by rumbo update ---\n" + block
    tomllib.loads(merged)  # raises if the result is not valid TOML
    _write_text(pyproject, merged)
    report.merged.append(f"pyproject.toml (+{_CHECKS_HEADER})")


_CHECKS_HEADER_RE = re.compile(r"(?m)^\[tool\.rumbo\.checks\][ \t]*(?:#[^\n]*)?\n")


def _table_block(text: str, header: re.Match[str]) -> tuple[str, str, str]:
    """Splits ``text`` at a table header into (head incl. header, table body, rest of the file)."""
    next_header = re.compile(r"(?m)^\[").search(text, header.end())
    end = next_header.start() if next_header else len(text)
    body = text[header.end() : end].rstrip("\n") + "\n"
    return text[: header.end()], body, text[end:]


def _rendered_checks(template: str) -> dict[str, str]:
    """The steps of a rendered ``pyproject.toml`` template, in table order."""
    checks = tomllib.loads(template)["tool"]["rumbo"]["checks"]
    return {key: str(cmd) for key, cmd in checks.items()}


def check_steps(cfg: Config) -> list[str]:
    """The step names ``[tool.rumbo.checks]`` renders for this rigor, kind and layout (ADR-0011).

    Derived from the rendered template, so the table is the single source of truth.
    """
    return list(_rendered_checks(_content(plan_files(cfg)["pyproject.toml"])))


def _add_missing_steps(pyproject: Path, template: str, report: Report) -> None:
    """Appends to ``[tool.rumbo.checks]`` the steps of the current rigor it lacks (ADR-0011).

    A rigor raised by hand (or by `rumbo promote`) brings new steps; they go at the end of the
    table in template order, with the template's command. Existing steps are never replaced
    or removed: the table stays in the human's hands.
    """
    current = read_utf8(pyproject, "pyproject.toml")
    try:
        rumbo = tomllib.loads(current).get("tool", {}).get("rumbo")
    except tomllib.TOMLDecodeError:
        return
    header = _CHECKS_HEADER_RE.search(current)
    if not isinstance(rumbo, dict) or not isinstance(rumbo.get("checks"), dict) or header is None:
        return
    wanted = _rendered_checks(template)
    missing = [key for key in wanted if key not in rumbo["checks"]]
    if not missing:
        return
    head, body, tail = _table_block(current, header)
    added = "".join(f"{key} = {json.dumps(wanted[key])}\n" for key in missing)
    merged = head + body + added + ("\n" + tail if tail else "")
    tomllib.loads(merged)  # raises if the result is not valid TOML
    _write_text(pyproject, merged)
    report.merged.append(f"pyproject.toml (+checks: {', '.join(missing)})")


_RUMBO_HEADER = re.compile(r"(?m)^\[tool\.rumbo\][ \t]*(?:#[^\n]*)?\n")


def set_rigor(text: str, rigor: str) -> str:
    """Returns ``text`` with ``rigor = "…"`` in ``[tool.rumbo]`` set (or added) to ``rigor``.

    A string replace on the existing key: comments, order and the other keys are untouched.
    """
    if rigor not in RIGOR_LEVELS:
        raise ValueError(f"invalid rigor {rigor!r}; use one of {RIGOR_LEVELS}")
    header = _RUMBO_HEADER.search(text)
    if header is None:
        raise ValueError("pyproject.toml has no [tool.rumbo] section; did you run 'rumbo init'?")
    head, body, tail = _table_block(text, header)
    line = re.compile(r"(?m)^rigor[ \t]*=[ \t]*\"[^\"\n]*\"")
    if line.search(body):
        body = line.sub(f'rigor = "{rigor}"', body, count=1)
    else:
        body += f'rigor = "{rigor}"\n'
    merged = head + body + ("\n" + tail if tail else "")
    if tomllib.loads(merged)["tool"]["rumbo"].get("rigor") != rigor:
        raise ValueError("could not rewrite [tool.rumbo].rigor")
    return merged


def _add_axes_keys(pyproject: Path, cfg: Config, report: Report) -> None:
    """Writes the `kind`, `layout` (and `layers`) keys a project generated before ADR-0010
    lacks, and the `types` key of ADR-0015.

    `Project.load` inferred them (service, hexagonal, pyright); writing them makes the
    inference explicit. Keys that exist are never touched.
    """
    wanted = {"kind": f'"{cfg.kind}"', "layout": f'"{cfg.layout}"'}
    if cfg.layout == "layered":
        wanted["layers"] = str(cfg.context()["layers_toml_list"])
    wanted["types"] = f'"{cfg.types}"'  # ADR-0015
    _add_rumbo_keys(pyproject, wanted, report)


def _add_python_key(pyproject: Path, cfg: Config, report: Report) -> None:
    """Writes the `python` key a project generated before ADR-0014 lacks.

    `Project.load` defaulted it to 3.11; writing it makes the promise explicit.
    """
    _add_rumbo_keys(pyproject, {"python": f'"{cfg.python}"'}, report)


def _add_src_key(pyproject: Path, cfg: Config, report: Report) -> None:
    """Writes the `src` key a project generated before ADR-0020 lacks.

    `Project.load` defaulted it to "src"; `config_for` looked once at where an adopted
    project keeps its package. Writing it makes that the stored fact.
    """
    _add_rumbo_keys(pyproject, {"src": f'"{cfg.src}"'}, report)


def _rumbo_table(pyproject: Path) -> dict[str, object]:
    """The `[tool.rumbo]` table of a pyproject; empty when missing or unreadable."""
    try:
        data = tomllib.loads(read_utf8(pyproject, "pyproject.toml"))
    except (OSError, ValueError):  # no file, not UTF-8, not TOML
        return {}
    tool = data.get("tool")
    rumbo = tool.get("rumbo") if isinstance(tool, dict) else None
    return rumbo if isinstance(rumbo, dict) else {}


def config_for(project: Project, dest: Path) -> Config:
    """The configuration `update`, `promote` and `doctor` render with.

    A hand-edited `[tool.rumbo]` that no configuration accepts is a ValueError that says so.

    ADR-0020: a project adopted before the `src` key existed may keep its package at the root.
    While the key is missing the disk is looked at, once: `update` then writes the key and
    every later command reads it. ADR-0029: the same for what the project already had
    (`own_ci`, `own_infra`).
    """
    try:
        cfg = Config.from_project(project)
    except ValueError as exc:
        raise ValueError(f"[tool.rumbo] in pyproject.toml is not valid: {exc}") from exc
    if not cfg.adopted:
        return cfg
    if "src" not in _rumbo_table(dest / "pyproject.toml"):
        cfg.src = detect_src(dest, cfg.package)
    return with_adoption_state(cfg, dest)


def _add_rumbo_keys(pyproject: Path, wanted: dict[str, str], report: Report) -> None:
    """Appends the `[tool.rumbo]` keys of `wanted` (name -> TOML value) that are missing.

    Keys that exist are never touched; nothing is written when none is missing.
    """
    current = read_utf8(pyproject, "pyproject.toml")
    try:
        rumbo = tomllib.loads(current).get("tool", {}).get("rumbo")
    except tomllib.TOMLDecodeError:
        return
    header = _RUMBO_HEADER.search(current)
    if not isinstance(rumbo, dict) or header is None:
        return
    missing = [key for key in wanted if key not in rumbo]
    if not missing:
        return
    head, body, tail = _table_block(current, header)
    added = "".join(f"{key} = {wanted[key]}\n" for key in missing)
    merged = head + body + added + ("\n" + tail if tail else "")
    tomllib.loads(merged)  # raises if the result is not valid TOML
    _write_text(pyproject, merged)
    report.merged.append(f"pyproject.toml (+{', '.join(missing)})")


def update_diff(project: Project, dest: Path) -> str:
    """Unified diff of what `update` would change, plus the diffs of files it only reports.

    Writes nothing. Framework-owned files are marked [update replaces]; the others
    [merge by hand] so the human can pick lines from the current template.
    """
    cfg = config_for(project, dest)
    chunks: list[str] = []
    for rel, item in plan_files(cfg, dest).items():
        target = dest / rel
        new_text = _content(item)
        if rel in MERGEABLE or not is_harness_file(rel, cfg.tests_dir):
            continue
        current = read_utf8(target, rel) if target.exists() else ""
        if _comparable(rel, current) == _comparable(rel, new_text):
            continue
        # Same rule as update(): owned files are replaced; .claude/ files only when missing.
        owned = is_framework_owned(rel) or (not target.exists() and rel.startswith(".claude/"))
        label = "update replaces" if owned else "merge by hand"
        if not target.exists() and not owned:
            label = "missing; copy by hand if wanted"
        diff = difflib.unified_diff(
            current.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"{rel} (project)",
            tofile=f"{rel} (template {__version__})",
        )
        chunks.append(f"### {rel} [{label}]\n" + "".join(diff))
    return "\n".join(chunks) if chunks else "Everything matches the current templates."


_VERSION_LINE = re.compile(r"""^([ \t]*version[ \t]*=[ \t]*)(?:"[^"\n]*"|'[^'\n]*')""")


def _bump_harness_version(pyproject: Path, report: Report) -> None:
    """Sets `[tool.rumbo].version` to the version of the CLI, wherever the key is in the table.

    No pattern is trusted to find the table: each `version = "..."` line of the file is tried
    in turn, and the one kept is the one after which the file parses to the same document with
    only that key changed. When no line does (a spelling this cannot rewrite), it says so.
    """
    text = read_utf8(pyproject, "pyproject.toml")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return
    tool = data.get("tool")
    rumbo = tool.get("rumbo") if isinstance(tool, dict) else None
    if not isinstance(rumbo, dict) or rumbo.get("version") == __version__:
        return
    if "version" not in rumbo:
        _add_rumbo_keys(pyproject, {"version": json.dumps(__version__)}, report)
        return
    wanted = copy.deepcopy(data)
    wanted["tool"]["rumbo"]["version"] = __version__
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        rewritten, found = _VERSION_LINE.subn(
            lambda m: m.group(1) + json.dumps(__version__), line, count=1
        )
        if not found:
            continue
        candidate = "".join([*lines[:index], rewritten, *lines[index + 1 :]])
        try:
            parsed = tomllib.loads(candidate)
        except tomllib.TOMLDecodeError:
            continue
        if parsed == wanted:
            _write_text(pyproject, candidate)
            return
    report.warnings.append(
        f'could not rewrite [tool.rumbo].version in pyproject.toml: set it to "{__version__}" '
        "by hand"
    )


def _run(cmd: list[str], cwd: Path, report: Report) -> int:
    print(f"$ {' '.join(cmd)}", flush=True)
    try:
        rc = subprocess.run(cmd, cwd=cwd, check=False).returncode
    except FileNotFoundError:
        rc = 127
        print(f"  ({cmd[0]!r} not found)")
    report.commands.append((" ".join(cmd), rc))
    return rc


def dev_dependencies(cfg: Config) -> list[str]:
    """The development tools by rigor, layout and type checker, in a stable order.

    Every project gets the base with its type checker (ADR-0015: `pyright` or `mypy`);
    `import-linter` when the layout has layers to contract; coverage, bandit and pip-audit
    from `mvp`; mutation testing at `production` (ADR-0011).
    """
    deps = [cfg.types if dep == DEFAULT_TYPE_CHECKER else dep for dep in BASE_DEV_DEPS]
    if cfg.arch:
        deps += ARCH_DEV_DEPS
    if cfg.rigor != "prototype":
        deps += MVP_DEV_DEPS
    if cfg.rigor == "production":
        deps += PRODUCTION_DEV_DEPS
    return deps


_DEP_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def dependency_name(requirement: str) -> str:
    """The normalised distribution name of a PEP 508 requirement (`Foo_Bar[x]>=1` → `foo-bar`)."""
    match = _DEP_NAME_RE.match(requirement)
    return re.sub(r"[-_.]+", "-", match.group(1)).lower() if match else ""


def listed_dev_dependencies(pyproject_text: str) -> list[str]:
    """The development requirements a pyproject lists: PEP 735 `dev` group, else `[tool.uv]`."""
    data = tomllib.loads(pyproject_text)
    group = data.get("dependency-groups", {}).get("dev")
    if not isinstance(group, list):
        group = data.get("tool", {}).get("uv", {}).get("dev-dependencies", [])
    return [item for item in group if isinstance(item, str)] if isinstance(group, list) else []


def missing_dev_dependencies(cfg: Config, pyproject_text: str) -> list[str]:
    """The development tools of this rigor and layout the pyproject does not list yet."""
    listed = {dependency_name(req) for req in listed_dev_dependencies(pyproject_text)}
    return [dep for dep in dev_dependencies(cfg) if dependency_name(dep) not in listed]


def runtime_dependencies(cfg: Config) -> list[str]:
    """Runtime dependencies by kind and capability (ADR-0010): HTTP for a service, SDK for LLM."""
    deps: list[str] = []
    if cfg.kind == "service":
        deps += SERVICE_DEPS
    if cfg.llm:
        deps += LLM_DEPS
        if cfg.sdk_package:
            deps.append(cfg.sdk_package)
    return deps


def add_dev_dependencies(deps: list[str], dest: Path, report: Report) -> int:
    """Runs `uv add --dev …` (ADR-0011: `rumbo promote`); 127 when uv is not installed."""
    if shutil.which("uv") is None:
        print("WARNING: 'uv' is not installed; install uv and run the 'uv add' line printed below.")
        report.commands.append(("uv (not installed)", 127))
        return 127
    return _run(["uv", "add", "--dev", *deps], dest, report)


# ------------------------------------------------------ ADR-0013: adoption detection
# Top-level directories that hold an `__init__.py` without being the project's package.
NOT_A_PACKAGE = frozenset({"tests", "test", "docs", "scripts", "spikes", "evals", "infra", "src"})
WEB_FRAMEWORKS = ("fastapi", "flask", "django", "starlette")


def read_pyproject(root: Path) -> dict[str, object]:
    """The parsed `pyproject.toml` of `root`, or an empty table when there is none.

    A file that is not UTF-8 or not TOML is a ValueError that names it.
    """
    path = root / "pyproject.toml"
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(read_utf8(path, "pyproject.toml"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"pyproject.toml is not valid TOML: {exc}") from exc


def _project_table(pyproject: dict[str, object]) -> dict[str, object]:
    table = pyproject.get("project")
    return table if isinstance(table, dict) else {}


def _directories(folder: Path) -> list[str]:
    """The names of the directories directly under `folder`, as the disk spells them."""
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_dir())


def _package_dirs(folder: Path) -> list[str]:
    """Names of the importable packages directly under `folder` (not the harness folders)."""
    return [
        name
        for name in _directories(folder)
        if (folder / name / "__init__.py").is_file() and name not in NOT_A_PACKAGE
    ]


def find_package(root: Path, package: str) -> tuple[str, str] | None:
    """Where the directory of `package` is, and how the disk spells it (ADR-0029).

    `("src", name)` or `(".", name)`; None when neither `src/` nor the root holds it. The
    exact name wins; else the single directory that differs only by case, because that is
    what a case-insensitive file system answers to and a case-sensitive one does not.
    """
    places = (("src", _directories(root / "src")), (".", _directories(root)))
    for src, names in places:
        if package in names:
            return src, package
    for src, names in places:
        alike = [name for name in names if name.lower() == package.lower()]
        if len(alike) == 1:
            return src, alike[0]
    return None


def detect_package(root: Path, pyproject: dict[str, object]) -> str | None:
    """The existing package: `[project].name` when its directory exists, else the single one.

    Candidates are the top-level directories holding an `__init__.py` (and those under `src/`)
    other than the harness folders. Two or more raise ValueError: `--package` decides. The
    name returned is the one the directory has on disk (ADR-0029).
    """
    name = _project_table(pyproject).get("name")
    if isinstance(name, str) and name.strip():
        found = find_package(root, re.sub(r"[-.\s]+", "_", name.strip()).lower())
        if found is not None:
            src, package = found
            if (root / package_path(src, package) / "__init__.py").is_file():
                return package
    candidates = sorted({*_package_dirs(root), *_package_dirs(root / "src")})
    if len(candidates) > 1:
        raise ValueError(
            f"several packages found ({', '.join(candidates)}): pass --package to choose one"
        )
    return candidates[0] if candidates else None


def no_package_message(root: Path, package: str | None) -> str:
    """Why `--adopt` cannot go on without a package directory, and what layout it supports."""
    supported = (
        "RUMBO adopts a package: a directory with an `__init__.py`, at the project root or "
        "under src/"
    )
    if package:
        return (
            f"--adopt needs the package to exist: there is no {package}/ at the root of "
            f"{root} nor under src/. {supported}"
        )
    modules = sorted(
        p.name
        for p in root.glob("*.py")
        if p.is_file() and p.name not in ("setup.py", "conftest.py")
    )
    if modules:
        return (
            f"--adopt found no package in {root}, only modules ({', '.join(modules)}): a "
            f"single-module project is not supported. {supported}. Move the module into a "
            "package first"
        )
    return f"--adopt found no package in {root}. {supported}; pass --package if it is there"


def detect_kind(pyproject: dict[str, object]) -> str:
    """`cli` with console scripts, `service` with a web framework dependency, else `library`."""
    project = _project_table(pyproject)
    scripts = project.get("scripts")
    if isinstance(scripts, dict) and scripts:
        return "cli"
    deps = project.get("dependencies")
    listed = deps if isinstance(deps, list) else []
    names = {dependency_name(d) for d in listed if isinstance(d, str)}
    if any(framework in names for framework in WEB_FRAMEWORKS):
        return "service"
    return "library"


def detect_src(root: Path, package: str) -> str:
    """Where the package lives (ADR-0020): "." when it is at the root and not under `src/`."""
    if (root / "src" / package).is_dir():
        return "src"
    return "." if (root / package).is_dir() else DEFAULT_SRC


def detect_layout(root: Path, package: str) -> str:
    """`hexagonal` when the package (under `src/` or at the root) has `domain/` and `app/`."""
    base = root / package_path(detect_src(root, package), package)
    return "hexagonal" if (base / "domain").is_dir() and (base / "app").is_dir() else "flat"


def detect_tests_dir(root: Path) -> str:
    """`tests` when it exists, else `test` when it exists, else `tests`."""
    for name in ("tests", "test"):
        if (root / name).is_dir():
            return name
    return "tests"


@dataclass(frozen=True)
class Detected:
    """What `rumbo init --adopt` found in an existing project (ADR-0013); flags override it."""

    name: str
    package: str | None
    kind: str
    layout: str
    tests_dir: str
    src: str = DEFAULT_SRC  # ADR-0020: "." when the package was found at the root

    def summary(self) -> str:
        return (
            f"package={self.package or '?'} kind={self.kind} layout={self.layout} "
            f"tests={self.tests_dir}" + (f" src={self.src}" if self.src != DEFAULT_SRC else "")
        )


def detect_project(root: Path, package: str | None = None) -> Detected:
    """Detects name, package (and where it lives), kind, layout and tests directory.

    `package` skips the detection of the package; it is still given the spelling of its
    directory when there is one (ADR-0029).
    """
    pyproject = read_pyproject(root)
    project_name = _project_table(pyproject).get("name")
    name = project_name.strip() if isinstance(project_name, str) and project_name.strip() else ""
    if package:
        found = find_package(root, package)
        package = found[1] if found else package
    else:
        package = detect_package(root, pyproject)
    return Detected(
        name=name or root.name,
        package=package,
        kind=detect_kind(pyproject),
        layout=detect_layout(root, package) if package else "flat",
        tests_dir=detect_tests_dir(root),
        src=detect_src(root, package) if package else DEFAULT_SRC,
    )


# ------------------------------------------------- ADR-0029: the Python the project promises
_CLAUSE = re.compile(r"^(~=|===|==|!=|<=|>=|<|>)\s*(\d+(?:\.\d+)*)(\.\*)?$")
_MINORS = range(0, 100)  # the 3.x releases a specifier is tried against


def _clause_allows(operator: str, numbers: list[int], star: bool, python: tuple[int, int]) -> bool:
    """Whether some release of the `python` series (3.12 = every 3.12.z) satisfies one clause
    of a PEP 440 specifier. A series is enough: the harness names minors, never patches."""
    series = (numbers[0], numbers[1] if len(numbers) > 1 else 0)
    patch = numbers[2] if len(numbers) > 2 else 0
    if operator in (">=", ">"):  # `>3.11` allows 3.11.1: a later patch always exists
        return python >= series
    if operator == "<=":
        return python <= series
    if operator == "<":
        return python < series or (python == series and patch > 0)
    if operator == "~=":  # `~=3.12` is >=3.12,==3.*; `~=3.12.1` is >=3.12.1,==3.12.*
        if len(numbers) < 3:
            return python[0] == series[0] and python >= series
        return python == series
    if star:  # `==3.12.*`, `!=3.11.*`, `==3.*`: the whole series, or every 3.x
        prefix = python[0] == numbers[0] if len(numbers) == 1 else python == series
        return prefix if operator != "!=" else not prefix
    if operator == "!=":
        return True  # one release is excluded: the series still has others
    return python == series  # `==` and `===`


def requires_python_allows(specifier: str, python: str) -> bool | None:
    """Whether `python` ("3.12") satisfies a `requires-python` specifier; None when the
    specifier is not one this reads (the caller says so and decides nothing from it)."""
    major, minor = (int(part) for part in python.split(".")[:2])
    for clause in specifier.split(","):
        match = _CLAUSE.match(clause.strip().replace(" ", ""))
        if match is None:
            return None
        numbers = [int(part) for part in match.group(2).split(".")]
        if not _clause_allows(match.group(1), numbers, bool(match.group(3)), (major, minor)):
            return False
    return True


def _python_pin(root: Path) -> str:
    """The `X.Y` that `.python-version` pins ('' when there is no file or no version in it)."""
    path = root / ".python-version"
    if not path.is_file():
        return ""
    match = re.search(r"(\d+)\.(\d+)", path.read_text(encoding="utf-8", errors="replace"))
    return f"{match.group(1)}.{match.group(2)}" if match else ""


def _minor(python: str) -> tuple[int, int]:
    major, minor = python.split(".")[:2]
    return int(major), int(minor)


def resolve_python(root: Path, flag: str | None) -> tuple[str, list[str]]:
    """The minimum Python the harness is mounted with, and what to tell the human (ADR-0029).

    `--python` wins when the project's `requires-python` allows it. Else the lowest supported
    Python that `requires-python` allows; else the one `.python-version` pins (the nearest
    supported one when it is outside the list); else the default. ValueError when the flag
    contradicts `requires-python`, or when no supported Python satisfies it. A pin that
    contradicts the result is reported: the file is the project's and is never rewritten.
    """
    supported = ", ".join(SUPPORTED_PYTHONS)
    notes: list[str] = []
    value = _project_table(read_pyproject(root)).get("requires-python") if root.is_dir() else None
    requires = value.strip() if isinstance(value, str) else ""
    pin = _python_pin(root) if root.is_dir() else ""
    readable = bool(requires) and requires_python_allows(requires, DEFAULT_PYTHON) is not None
    if requires and not readable:
        notes.append(
            f'requires-python = "{requires}" is not a specifier rumbo reads: it decided '
            "nothing; check that the Python below satisfies it"
        )
    if flag:
        validate_python(flag)
        if readable and not requires_python_allows(requires, flag):
            raise ValueError(
                f'--python {flag} does not satisfy the requires-python of the project ("'
                f'{requires}"): pass a version it allows, or change requires-python first'
            )
        python = flag
    elif readable:
        allowed = [p for p in SUPPORTED_PYTHONS if requires_python_allows(requires, p)]
        if not allowed:
            raise ValueError(
                f'requires-python = "{requires}" allows none of the Pythons this rumbo '
                f"supports ({supported}): upgrade rumbo, or change requires-python"
            )
        python = allowed[0]
        lowest = next(
            (m for m in _MINORS if requires_python_allows(requires, f"3.{m}")), _minor(python)[1]
        )
        if (3, lowest) < _minor(python):
            notes.append(
                f'requires-python = "{requires}" allows Pythons older than rumbo supports '
                f"({supported}): the harness uses {python}, the lowest one that satisfies it "
                "(ruff target, CI, .python-version); requires-python is not changed"
            )
    elif pin:
        python = pin
        if pin not in SUPPORTED_PYTHONS:
            older = _minor(pin) < _minor(SUPPORTED_PYTHONS[0])
            python = SUPPORTED_PYTHONS[0] if older else SUPPORTED_PYTHONS[-1]
            notes.append(
                f".python-version pins {pin}, which this rumbo does not support ({supported}): "
                f"the harness uses {python}"
            )
    else:
        python = DEFAULT_PYTHON
    if pin and (
        _minor(pin) < _minor(python) or (readable and not requires_python_allows(requires, pin))
    ):
        notes.append(
            f".python-version pins {pin}, below the Python the harness promises ({python})"
            + (f' or outside requires-python = "{requires}"' if readable else "")
            + f": RUMBO did not rewrite it; set it to {python} or newer, or uv will refuse to "
            "sync"
        )
    return python, notes


# ADR-0008: what a human runs once per clone so the git hooks let their commits through.
HUMAN_COMMAND = ("git", "config", "rumbo.human", "true")


def is_console(stream: IO[str] | None) -> bool:
    """True when `stream` is a real terminal.

    `isatty()` alone is not enough on Windows: it is also true for the NUL device, which is
    what a process started without a terminal (an agent's subprocess, a script with
    `< /dev/null`) reads from. There the console API is asked as well.
    """
    try:
        if stream is None or not stream.isatty():
            return False
        if sys.platform == "win32":
            import ctypes
            import msvcrt

            mode = ctypes.c_ulong()
            handle = ctypes.c_void_p(msvcrt.get_osfhandle(stream.fileno()))
            return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
        return True
    except (AttributeError, ValueError, OSError):  # no stream, a closed one, not a file
        return False


def at_a_terminal() -> bool:
    """Standard input AND output are terminals: someone types and someone reads."""
    return is_console(sys.stdin) and is_console(sys.stdout)


def human_present(
    dest: Path,
    environ: Mapping[str, str] | None = None,
    isatty: Callable[[], bool] | None = None,
) -> bool:
    """True when a human is demonstrably running the command (ADR-0021).

    No agent marker in the environment (the guard's own list: `[tool.rumbo].agent_env` of the
    project, else its `DEFAULT_AGENT_ENV`) and the command runs at a terminal (`at_a_terminal`:
    an agent's subprocess has its output captured). `environ` and `isatty` default to the
    process's own.
    """
    env = os.environ if environ is None else environ
    if any(env.get(name) for name in agent_env_names(dest)):
        return False
    return bool((isatty or at_a_terminal)())


def git_prefix(root: Path) -> str | None:
    """Where `root` is inside a git work tree: "" at its top, `sub/dir/` below it, None when
    it is in none (or git is not installed).

    `(root / ".git").exists()` only knows the top: a project in a sub-directory of a
    repository is in one too (ADR-0029).
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree", "--show-prefix"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:  # no git, or `root` is not a directory
        return None
    lines = proc.stdout.splitlines()
    if proc.returncode != 0 or not lines or lines[0].strip() != "true":
        return None
    return lines[1].strip() if len(lines) > 1 else ""


def install(cfg: Config, dest: Path, report: Report, human: bool | None = None) -> None:
    """Installs dependencies with uv (latest stable versions) and the git hooks.

    `rumbo.human` is declared in the clone only for a human who is demonstrably there
    (ADR-0021; `human` overrides `human_present`). Otherwise the setting is left alone: only
    the human declares the human, and `rumbo init` prints the command in its next steps.

    ADR-0029: `pre-commit autoupdate` rewrites the revisions of a configuration, so it runs
    only on the one this run wrote (`report.created`); and git hooks belong to the top of
    the repository, so none is installed from a sub-directory of it.
    """
    if shutil.which("uv") is None:
        print("WARNING: 'uv' is not installed; install uv and run the commands in README.md.")
        report.commands.append(("uv (not installed)", 127))
        return
    _run(["uv", "add", "--dev", *dev_dependencies(cfg)], dest, report)
    runtime = runtime_dependencies(cfg)
    if runtime:
        _run(["uv", "add", *runtime], dest, report)
    prefix = git_prefix(dest)
    if prefix is None:
        print("WARNING: not a git repository; run 'git init', then 'uv run pre-commit install'.")
    elif prefix:
        print(
            f"WARNING: this project is in a sub-directory ({prefix.rstrip('/')}) of a git "
            "repository, and git hooks belong to the repository root: 'pre-commit install' was "
            "not run. Call the guard from the .pre-commit-config.yaml of the repository root "
            f"(its two hooks, run inside {prefix.rstrip('/')})."
        )
    else:
        if PRE_COMMIT_FILE in report.created:
            _run(["uv", "run", "pre-commit", "autoupdate"], dest, report)
        _run(["uv", "run", "pre-commit", "install"], dest, report)
        # ADR-0008/0021: the git hooks let the human's commits through; an agent that mounts
        # the harness never makes that declaration on the human's behalf.
        if human_present(dest) if human is None else human:
            _run(list(HUMAN_COMMAND), dest, report)
