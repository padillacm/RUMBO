from pathlib import Path

import pytest

from rumbo.scaffold import Config, scaffold


@pytest.fixture
def llm_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("llm")
    cfg = Config(name="XM Agent", package="xm_agent", llm=True, provider="anthropic", rigor="mvp")
    scaffold(cfg, root)
    return root


@pytest.fixture
def simple_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("simple")
    scaffold(Config(name="Demo", package="demo", llm=False, rigor="prototype"), root)
    return root
