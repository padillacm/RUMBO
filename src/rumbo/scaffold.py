"""Mounts the RUMBO harness (Part A) in a project directory, and updates it later."""

from __future__ import annotations

import datetime as dt
import difflib
import json
import keyword
import re
import shutil
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from rumbo import __version__
from rumbo.project import (
    HEXAGONAL_LAYERS,
    KINDS,
    LAYOUTS,
    RIGOR_LEVELS,
    Project,
    resolve_layout,
    validate_axes,
)
from rumbo.render import render

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

DEV_DEPS = [
    "ruff",
    "pyright",
    "pytest",
    "pytest-cov",
    "hypothesis",
    "import-linter",
    "pre-commit",
    "pip-audit",
    "bandit",
    "mutmut",
]
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
    "tests/architecture/",
)

_PKG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_RESERVED = {"test", "tests", "src", "evals", "scripts", "spikes", "domain", "app", "api"}
_NAME_RE = re.compile(r"^[^\x00-\x1f\\\"]+$")
# A PyPI distribution name with optional extras (PEP 508), no version specifier: `uv add`
# resolves the version, and the name is embedded verbatim in pyproject.toml.
_SDK_PACKAGE_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?(?:\[[A-Za-z0-9._,-]+\])?$"
)
_SDK_MODULE_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")


def valid_package(package: str) -> bool:
    return (
        bool(_PKG_RE.match(package)) and not keyword.iskeyword(package) and package not in _RESERVED
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

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        if not valid_name(self.name):
            raise ValueError(
                f"invalid project name {self.name!r}: no quotes, backslashes or control characters"
            )
        if not valid_package(self.package):
            raise ValueError(f"invalid package name (use snake_case): {self.package!r}")
        if self.rigor not in RIGOR_LEVELS:
            raise ValueError(f"invalid rigor {self.rigor!r}; use one of {RIGOR_LEVELS}")
        if self.kind not in KINDS:
            raise ValueError(f"invalid kind {self.kind!r}; use one of {KINDS}")
        if self.layout and self.layout not in LAYOUTS:
            raise ValueError(f"invalid layout {self.layout!r}; use one of {LAYOUTS}")
        self.layers = tuple(self.layers)
        self.layout = resolve_layout(self.kind, self.layout, self.llm)
        validate_axes(self.kind, self.layout, self.layers)
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
    merged: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    differs: list[str] = field(default_factory=list)
    commands: list[tuple[str, int]] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Created: {len(self.created)}"]
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
        return "\n".join(lines)


def target_path(rel: str, cfg: Config) -> str:
    parts = []
    for part in rel.split("/"):
        part = part.replace("__pkg__", cfg.package)
        if part.startswith("dot_"):
            part = "." + part[4:]
        parts.append(part)
    return "/".join(parts).removesuffix(".tmpl")


def _included(rel: str, cfg: Config) -> bool:
    rel = rel.removesuffix(".tmpl")
    return all(cond(cfg) for prefix, cond in CONDITIONS.items() if rel.startswith(prefix))


def plan_files(cfg: Config) -> dict[str, str | Path]:
    """Returns {target path: rendered content, or a path to copy verbatim}."""
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
    return plan


def _layer_module(cfg: Config, layer: str) -> str:
    """The `__init__.py` of one layer of a layered package (ADR-0010), outermost first."""
    names = list(cfg.layers)
    below = names[names.index(layer) + 1 :]
    if not below:
        return f'"""Layer {layer}: the innermost one, kept pure (no I/O; pyright strict)."""\n'
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


def _write_text(target: Path, text: str) -> None:
    with target.open("w", encoding="utf-8", newline="\n") as f:  # LF on every platform
        f.write(text)


def _write(target: Path, item: str | Path, rel: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_text(target, _content(item))  # also normalizes CRLF from a Windows checkout to LF
    if rel.startswith("scripts/"):
        target.chmod(0o755)


def scaffold(cfg: Config, dest: Path, force: bool = False) -> Report:
    """Creates the harness. Existing files are merged (pyproject, .gitignore) or skipped.

    With ``force`` the other existing files are overwritten (a ``.bak`` copy is kept);
    pyproject.toml and .gitignore are always merged, never replaced.
    """
    report = Report()
    dest.mkdir(parents=True, exist_ok=True)
    for rel, item in plan_files(cfg).items():
        target = dest / rel
        if target.exists():
            if rel in MERGEABLE:
                current = target.read_text(encoding="utf-8")
                merged, notes = MERGEABLE[rel](current, _content(item))
                if merged != current:
                    _write_text(target, merged)
                    detail = "; ".join(notes) if rel == "pyproject.toml" else f"+{len(notes)} lines"
                    report.merged.append(f"{rel} ({detail})")
                else:
                    report.skipped.append(rel)
                continue
            if not force:
                report.skipped.append(rel)
                continue
            shutil.copyfile(target, target.with_name(target.name + ".bak"))
        _write(target, item, rel)
        report.created.append(rel)
    return report


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


def is_harness_file(rel: str) -> bool:
    return is_framework_owned(rel) or rel.startswith(HARNESS_PREFIXES)


def update(project: Project, dest: Path) -> Report:
    """Refreshes the files the framework owns; never touches documents the human edits.

    Other template files that differ from the current template are only listed.
    """
    cfg = Config.from_project(project)
    report = Report()
    for rel, item in plan_files(cfg).items():
        target = dest / rel
        new_text = _content(item)
        if not target.exists():
            if is_framework_owned(rel) or rel.startswith(".claude/"):
                _write(target, item, rel)
                report.created.append(rel)
            elif is_harness_file(rel):
                report.differs.append(f"{rel} (missing)")
            continue
        current = target.read_text(encoding="utf-8")
        if current == new_text or _comparable(rel, current) == _comparable(rel, new_text):
            continue
        if is_framework_owned(rel):
            _write(target, item, rel)
            report.updated.append(rel)
        elif rel not in MERGEABLE and is_harness_file(rel):
            report.differs.append(rel)
    _add_checks_table(dest / "pyproject.toml", _content(plan_files(cfg)["pyproject.toml"]), report)
    _add_axes_keys(dest / "pyproject.toml", cfg, report)
    _bump_harness_version(dest / "pyproject.toml")
    return report


_CHECKS_HEADER = "[tool.rumbo.checks]"


def _add_checks_table(pyproject: Path, template: str, report: Report) -> None:
    """Appends the rendered ``[tool.rumbo.checks]`` to a project generated before ADR-0006.

    The runner `rumbo update` copies in needs the table; without it every Stop hook would
    report a broken environment. The steps are only added, never replaced.
    """
    current = pyproject.read_text(encoding="utf-8")
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


_RUMBO_HEADER = re.compile(r"(?m)^\[tool\.rumbo\][ \t]*(?:#[^\n]*)?\n")


def _add_axes_keys(pyproject: Path, cfg: Config, report: Report) -> None:
    """Writes the `kind`, `layout` (and `layers`) keys a project generated before ADR-0010 lacks.

    `Project.load` inferred them (service, hexagonal); writing them makes the inference explicit.
    Keys that exist are never touched.
    """
    current = pyproject.read_text(encoding="utf-8")
    try:
        rumbo = tomllib.loads(current).get("tool", {}).get("rumbo")
    except tomllib.TOMLDecodeError:
        return
    header = _RUMBO_HEADER.search(current)
    if not isinstance(rumbo, dict) or header is None:
        return
    wanted = {"kind": f'"{cfg.kind}"', "layout": f'"{cfg.layout}"'}
    if cfg.layout == "layered":
        wanted["layers"] = str(cfg.context()["layers_toml_list"])
    missing = [key for key in wanted if key not in rumbo]
    if not missing:
        return
    next_header = re.compile(r"(?m)^\[").search(current, header.end())
    end = next_header.start() if next_header else len(current)
    block = current[header.end() : end].rstrip("\n") + "\n"
    added = "".join(f"{key} = {wanted[key]}\n" for key in missing)
    tail = current[end:]
    merged = current[: header.end()] + block + added + ("\n" + tail if tail else "")
    tomllib.loads(merged)  # raises if the result is not valid TOML
    _write_text(pyproject, merged)
    report.merged.append(f"pyproject.toml (+{', '.join(missing)})")


def update_diff(project: Project, dest: Path) -> str:
    """Unified diff of what `update` would change, plus the diffs of files it only reports.

    Writes nothing. Framework-owned files are marked [update replaces]; the others
    [merge by hand] so the human can pick lines from the current template.
    """
    cfg = Config.from_project(project)
    chunks: list[str] = []
    for rel, item in plan_files(cfg).items():
        target = dest / rel
        new_text = _content(item)
        if rel in MERGEABLE or not is_harness_file(rel):
            continue
        current = target.read_text(encoding="utf-8") if target.exists() else ""
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


def _bump_harness_version(pyproject: Path) -> None:
    text = pyproject.read_text(encoding="utf-8")
    new = re.sub(
        r'(?m)^(\[tool\.rumbo\][^\[]*?^version = )"[^"]*"',
        rf'\1"{__version__}"',
        text,
        count=1,
    )
    if new != text:
        _write_text(pyproject, new)


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
    """The development tools; import-linter only when the layout has layers to contract."""
    return [dep for dep in DEV_DEPS if dep != "import-linter" or cfg.arch]


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


def install(cfg: Config, dest: Path, report: Report) -> None:
    """Installs dependencies with uv (latest stable versions), the git hooks and `rumbo.human`."""
    if shutil.which("uv") is None:
        print("WARNING: 'uv' is not installed; install uv and run the commands in README.md.")
        report.commands.append(("uv (not installed)", 127))
        return
    _run(["uv", "add", "--dev", *dev_dependencies(cfg)], dest, report)
    runtime = runtime_dependencies(cfg)
    if runtime:
        _run(["uv", "add", *runtime], dest, report)
    if (dest / ".git").exists():
        _run(["uv", "run", "pre-commit", "autoupdate"], dest, report)
        _run(["uv", "run", "pre-commit", "install"], dest, report)
        # ADR-0008: the human runs `rumbo init`; the git hooks let their commits through.
        _run(["git", "config", "rumbo.human", "true"], dest, report)
    else:
        print("WARNING: not a git repository; run 'git init', then 'uv run pre-commit install'.")
