"""Reading the state of a project that already has the RUMBO harness."""

from __future__ import annotations

import re
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path

TEMPLATE_MARK = "<!-- rumbo:template"
RIGOR_LEVELS = ("prototype", "mvp", "production")
# ADR-0010: what the project is, and how its package is organised. Orthogonal to `llm`.
KINDS = ("service", "library", "cli", "data")
LAYOUTS = ("hexagonal", "layered", "flat")
DEFAULT_LAYOUT = {"service": "hexagonal", "library": "flat", "cli": "flat", "data": "flat"}
HEXAGONAL_LAYERS = ("api", "adapters", "app", "domain")  # outermost first
_LAYER_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def rigor_rank(rigor: str) -> int:
    return RIGOR_LEVELS.index(rigor)


def resolve_layout(kind: str, layout: str, llm: bool) -> str:
    """The layout to use: explicit, else derived from the kind; `llm` forces hexagonal."""
    if llm:
        return "hexagonal"
    return layout or DEFAULT_LAYOUT[kind]


def validate_axes(kind: str, layout: str, layers: tuple[str, ...]) -> None:
    """Raises ValueError when kind, layout or layers (ADR-0010) are not consistent."""
    if kind not in KINDS:
        raise ValueError(f"invalid kind {kind!r}; use one of {KINDS}")
    if layout not in LAYOUTS:
        raise ValueError(f"invalid layout {layout!r}; use one of {LAYOUTS}")
    if layout == "layered":
        if len(layers) < 2:
            raise ValueError("layout 'layered' needs at least two layers (outermost first)")
        bad = [x for x in layers if not _LAYER_RE.match(x)]
        if bad or len(set(layers)) != len(layers):
            raise ValueError(f"layers must be distinct snake_case names, got {layers!r}")
    elif layers:
        raise ValueError(f"layers are only used with layout 'layered', got {layers!r}")


@dataclass(frozen=True)
class Project:
    root: Path
    package: str
    llm: bool
    rigor: str
    name: str = ""
    kind: str = "service"
    layout: str = "hexagonal"
    layers: tuple[str, ...] = ()
    provider: str = ""
    sdk_package: str = ""
    sdk_module: str = ""
    sdk_env_key: str = ""
    harness_version: str = ""
    # ADR-0013: mounted on an existing project; its tests directory ([tool.rumbo].tests).
    adopted: bool = False
    tests_dir: str = "tests"

    @classmethod
    def load(cls, root: Path) -> Project:
        root = root.resolve()
        pyproject = root / "pyproject.toml"
        if not pyproject.exists():
            raise FileNotFoundError(f"no pyproject.toml in {root}; did you run 'rumbo init'?")
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        cfg = data.get("tool", {}).get("rumbo")
        if not isinstance(cfg, dict):
            raise ValueError(
                "pyproject.toml has no [tool.rumbo] section; did you run 'rumbo init'?"
            )
        package = cfg.get("package") or cfg.get("pkg")
        if not package:
            raise ValueError("[tool.rumbo] has no 'package' key")
        rigor = str(cfg.get("rigor", "mvp"))
        if rigor not in RIGOR_LEVELS:
            raise ValueError(f"[tool.rumbo].rigor must be one of {RIGOR_LEVELS}, got {rigor!r}")
        provider = str(cfg.get("provider", ""))
        # ADR-0010: projects generated before the axes existed are services with hexagonal
        # layers; the keys are inferred, never required.
        kind = str(cfg.get("kind", "service"))
        llm = bool(cfg.get("llm", False))
        layout = resolve_layout(kind, str(cfg.get("layout", "")), llm) if kind in KINDS else ""
        raw_layers = cfg.get("layers", [])
        layers = (
            tuple(str(x) for x in raw_layers)
            if isinstance(raw_layers, list) and all(isinstance(x, str) for x in raw_layers)
            else ()
        )
        validate_axes(kind, layout, layers)
        return cls(
            root=root,
            package=str(package),
            name=str(cfg.get("name", "")) or root.name or str(package),
            llm=llm,
            rigor=rigor,
            kind=kind,
            layout=layout,
            layers=layers,
            provider="" if provider == "none" else provider,
            sdk_package=str(cfg.get("sdk_package", "")),
            sdk_module=str(cfg.get("sdk_module", "")),
            sdk_env_key=str(cfg.get("sdk_env_key", "")),
            harness_version=str(cfg.get("version", "")),
            adopted=bool(cfg.get("adopted", False)),
            tests_dir=str(cfg.get("tests", "tests")) or "tests",
        )

    def read(self, rel: str) -> str | None:
        path = self.root / rel
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def at_least(self, rigor: str) -> bool:
        return rigor_rank(self.rigor) >= rigor_rank(rigor)

    @property
    def evals(self) -> bool:
        """Datasets and graders exist for LLM products and for data pipelines (ADR-0010)."""
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


def is_template(text: str | None) -> bool:
    """True when the document is missing or still carries the template mark."""
    return text is None or TEMPLATE_MARK in text


def slug(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return normalized[:60] or "untitled"


def section(text: str, title: str) -> str:
    """Body of a markdown section '## title' (up to the next '## '), without HTML comments."""
    pattern = re.compile(rf"^##\s+{re.escape(title)}[^\n]*\n(.*?)(?=^##\s|\Z)", re.M | re.S | re.I)
    match = pattern.search(text)
    if not match:
        return ""
    body = re.sub(r"<!--.*?-->", "", match.group(1), flags=re.S)
    return body.strip()


def numbered_documents(folder: Path) -> list[Path]:
    """NNNN-*.md files, excluding the 0000 template."""
    if not folder.is_dir():
        return []
    return sorted(
        p for p in folder.glob("[0-9][0-9][0-9][0-9]-*.md") if not p.name.startswith("0000-")
    )
