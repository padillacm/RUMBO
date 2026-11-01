"""Fitness functions of the framework's own ADRs (docs/adr/)."""

import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "rumbo"


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_cli_has_no_runtime_dependencies() -> None:
    """ADR-0001: standard library only."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["dependencies"] == []
    stdlib = sys.stdlib_module_names | {"rumbo"}
    foreign = {
        f.relative_to(ROOT).as_posix(): sorted(_imports(f) - stdlib)
        for f in SRC.rglob("*.py")
        if "templates" not in f.parts and _imports(f) - stdlib
    }
    assert foreign == {}


def test_every_adr_has_a_fitness_function_section() -> None:
    adrs = sorted((ROOT / "docs" / "adr").glob("0*.md"))
    assert len(adrs) >= 5
    for adr in adrs:
        text = adr.read_text(encoding="utf-8")
        assert "## Fitness function" in text and "<!-- rumbo:template" not in text, adr.name
