"""Reading the state of a project that already has the RUMBO harness."""

from __future__ import annotations

import re
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path

TEMPLATE_MARK = "<!-- rumbo:template"
RIGOR_LEVELS = ("prototype", "mvp", "production")


def rigor_rank(rigor: str) -> int:
    return RIGOR_LEVELS.index(rigor)


@dataclass(frozen=True)
class Project:
    root: Path
    package: str
    llm: bool
    rigor: str
    name: str = ""
    provider: str = ""
    sdk_package: str = ""
    sdk_module: str = ""
    sdk_env_key: str = ""
    harness_version: str = ""

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
        return cls(
            root=root,
            package=str(package),
            name=str(cfg.get("name", "")) or root.name or str(package),
            llm=bool(cfg.get("llm", False)),
            rigor=rigor,
            provider="" if provider == "none" else provider,
            sdk_package=str(cfg.get("sdk_package", "")),
            sdk_module=str(cfg.get("sdk_module", "")),
            sdk_env_key=str(cfg.get("sdk_env_key", "")),
            harness_version=str(cfg.get("version", "")),
        )

    def read(self, rel: str) -> str | None:
        path = self.root / rel
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def at_least(self, rigor: str) -> bool:
        return rigor_rank(self.rigor) >= rigor_rank(rigor)


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
