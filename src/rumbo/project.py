"""Reading the state of a project that already has the RUMBO harness."""

from __future__ import annotations

import re
import subprocess
import tomllib
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

TEMPLATE_MARK = "<!-- rumbo:template"
RIGOR_LEVELS = ("prototype", "mvp", "production")
# ADR-0010: what the project is, and how its package is organised. Orthogonal to `llm`.
KINDS = ("service", "library", "cli", "data")
LAYOUTS = ("hexagonal", "layered", "flat")
DEFAULT_LAYOUT = {"service": "hexagonal", "library": "flat", "cli": "flat", "data": "flat"}
HEXAGONAL_LAYERS = ("api", "adapters", "app", "domain")  # outermost first
# ADR-0014: the Python versions a project may promise as its minimum
# ([tool.rumbo].python), oldest first. Bumped on release only: a new entry needs that
# Python to exist, never a version copied from memory.
SUPPORTED_PYTHONS = ("3.11", "3.12", "3.13")
DEFAULT_PYTHON = SUPPORTED_PYTHONS[0]
# ADR-0015: the type checker is a setting; `ty` joins when it leaves preview.
TYPE_CHECKERS = ("pyright", "mypy")
DEFAULT_TYPE_CHECKER = "pyright"
# ADR-0020: the directory that holds the package ([tool.rumbo].src): `src/`, or the project
# root (".") when an adopted project keeps its package there.
SRC_DIRS = ("src", ".")
DEFAULT_SRC = "src"
DEFAULT_TESTS = "tests"
NOT_UTF8 = "not UTF-8 (save it as UTF-8)"
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
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


def validate_python(python: str) -> None:
    """Raises ValueError unless `python` is a supported minimum version (ADR-0014)."""
    if python not in SUPPORTED_PYTHONS:
        raise ValueError(
            f"unsupported Python {python!r}; use one of {', '.join(SUPPORTED_PYTHONS)} "
            "(the list grows with each rumbo release)"
        )


def validate_src(src: str) -> None:
    """Raises ValueError unless `src` is a supported source directory (ADR-0020)."""
    if src not in SRC_DIRS:
        raise ValueError(
            f'[tool.rumbo].src must be "src" or "." (the directory that holds the package), '
            f"got {src!r}"
        )


class NotUTF8(ValueError):
    """A file of the project is not UTF-8 text; the message names the file and the remedy."""


def read_utf8(path: Path, name: str = "") -> str:
    """The text of a UTF-8 file, without a BOM and with LF newlines; NotUTF8 otherwise.

    PowerShell 5 writes UTF-16 with `>` and an editor on Windows may write cp1252: neither is
    a reason for a traceback. A NUL means UTF-16 without a BOM, never a document.
    """
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        text = "\x00"
    if "\x00" in text:
        raise NotUTF8(f"{name or path.name}: {NOT_UTF8}")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalise_tests_dir(value: object) -> str:
    """`[tool.rumbo].tests` as a relative POSIX path: `./tests/` and backslashes are accepted."""
    parts = [x for x in str(value or "").replace("\\", "/").split("/") if x not in ("", ".")]
    return "/".join(parts) or DEFAULT_TESTS


def code_root(src: str, package: str) -> str:
    """What the tools scan (ADR-0020): `src`, or the package itself when it lives at the root."""
    return package if src == "." else src


def package_path(src: str, package: str) -> str:
    """The package directory relative to the project root (ADR-0020): `src/<pkg>` or `<pkg>`."""
    return package if src == "." else f"{src}/{package}"


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
    # ADR-0014: the minimum Python the project promises ([tool.rumbo].python).
    python: str = DEFAULT_PYTHON
    # ADR-0015: the type checker ([tool.rumbo].types); older projects use pyright.
    types: str = DEFAULT_TYPE_CHECKER
    # ADR-0020: the directory that holds the package ([tool.rumbo].src); "src" when absent.
    src: str = DEFAULT_SRC

    @classmethod
    def load(cls, root: Path) -> Project:
        root = root.resolve()
        pyproject = root / "pyproject.toml"
        if not pyproject.exists():
            raise FileNotFoundError(f"no pyproject.toml in {root}; did you run 'rumbo init'?")
        data = tomllib.loads(read_utf8(pyproject, "pyproject.toml"))
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
        python = str(cfg.get("python", DEFAULT_PYTHON))
        validate_python(python)
        types = str(cfg.get("types", DEFAULT_TYPE_CHECKER))
        if types not in TYPE_CHECKERS:
            raise ValueError(f"[tool.rumbo].types must be one of {TYPE_CHECKERS}, got {types!r}")
        src = str(cfg.get("src", DEFAULT_SRC))
        validate_src(src)
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
            tests_dir=normalise_tests_dir(cfg.get("tests")),
            python=python,
            types=types,
            src=src,
        )

    def read(self, rel: str) -> str | None:
        """The text of a file of the project; None when it is missing, NotUTF8 when unreadable."""
        path = self.root / rel
        return read_utf8(path, rel) if path.is_file() else None

    def at_least(self, rigor: str) -> bool:
        return rigor_rank(self.rigor) >= rigor_rank(rigor)

    @property
    def code_root(self) -> str:
        """What the tools scan (ADR-0020): `src`, or the package when it lives at the root."""
        return code_root(self.src, self.package)

    @property
    def package_path(self) -> str:
        """The package directory relative to the root (ADR-0020): `src/<pkg>` or `<pkg>`."""
        return package_path(self.src, self.package)

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


def normalize_name(name: str) -> str:
    """PEP 503 normalisation: lowercase, runs of `-`, `_` and `.` collapsed to one dash."""
    return re.sub(r"[-_.]+", "-", name).lower()


class GitMissing(RuntimeError):
    """git could not be started (not installed, or not on PATH): one line for the human."""

    def __init__(self, exc: OSError) -> None:
        super().__init__(f"cannot run git ({exc.strerror or exc}); is it installed and on PATH?")


def run_git(args: Sequence[str], root: Path) -> subprocess.CompletedProcess[str]:
    """`git <args>` in `root`, its output as text (UTF-8, what does not decode is replaced).

    A failing git is a return code, never an exception; a missing git (or a `root` that is not
    a directory) raises OSError, and each caller decides what that means.
    """
    return subprocess.run(  # noqa: S603 - fixed program, arguments from the callers' code
        ["git", *args],  # noqa: S607 - git from PATH, as every git hook and the sensors do
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def tests_dir(root: Path) -> str:
    """`[tool.rumbo].tests` of the project in `root`; `tests` when its state cannot be read."""
    try:
        return Project.load(root).tests_dir
    except (OSError, ValueError):  # no pyproject.toml, not TOML, no or invalid [tool.rumbo]
        return DEFAULT_TESTS


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


def prose(text: str) -> str:
    """`text` with its HTML comments and fenced code blocks blanked; line numbers are kept.

    An example inside a fence is not a requirement, a placeholder or a diagram of the document.
    """
    text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    kept: list[str] = []
    fence = ""
    for line in text.split("\n"):
        mark = _FENCE.match(line)
        if not fence:
            fence = mark.group(1) if mark else ""
            kept.append("" if mark else line)
            continue
        closes = mark is not None and mark.group(1).startswith(fence) and line.strip() == mark[1]
        fence = "" if closes else fence
        kept.append("")
    return "\n".join(kept)


def numbered_documents(folder: Path) -> list[Path]:
    """NNNN-*.md files, excluding the 0000 template."""
    if not folder.is_dir():
        return []
    return sorted(
        p for p in folder.glob("[0-9][0-9][0-9][0-9]-*.md") if not p.name.startswith("0000-")
    )
