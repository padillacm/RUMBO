"""Architecture fitness functions (they complement the import-linter contracts).

- Nothing in src/ imports spikes/.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "rumbo"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_src_does_not_import_spikes() -> None:
    offenders = [str(f.relative_to(ROOT)) for f in SRC.rglob("*.py") if "spikes" in _imports(f)]
    assert offenders == []
