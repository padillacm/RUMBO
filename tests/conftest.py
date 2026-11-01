from pathlib import Path

import pytest

from rumbo.scaffold import Config, scaffold


@pytest.fixture
def proyecto_llm(tmp_path: Path) -> Path:
    cfg = Config(nombre="Agente XM", pkg="agente_xm", llm=True, proveedor="anthropic", rigor="mvp")
    scaffold(cfg, tmp_path)
    return tmp_path


@pytest.fixture
def proyecto_simple(tmp_path: Path) -> Path:
    scaffold(Config(nombre="Demo", pkg="demo", llm=False, rigor="prototipo"), tmp_path)
    return tmp_path
