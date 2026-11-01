"""Tests de los graders deterministas de evals/."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from graders import abstencion, argumentos_tool, igualdad_campos, numerico


def test_igualdad_campos() -> None:
    assert igualdad_campos({"a": 1, "b": 2}, {"a": 1}).ok
    assert not igualdad_campos({"a": 1}, {"a": 2}).ok


def test_numerico_con_tolerancia() -> None:
    assert numerico({"valor": 100.05}, {"valor": 100.0, "rel_tol": 1e-3}).ok
    assert not numerico({"valor": 101.0}, {"valor": 100.0, "rel_tol": 1e-3}).ok
    assert not numerico({}, {"valor": 1.0}).ok


def test_argumentos_tool() -> None:
    salida = {"tool_calls": [{"name": "consultar", "args": {"metrica": "precio", "dias": 7}}]}
    assert argumentos_tool(salida, {"tool": "consultar", "args": {"metrica": "precio"}}).ok
    assert not argumentos_tool(salida, {"tool": "consultar", "args": {"metrica": "demanda"}}).ok
    assert not argumentos_tool({}, {"tool": "consultar"}).ok


def test_abstencion() -> None:
    assert abstencion({"abstencion": True}, {}).ok
    assert not abstencion({"respuesta": "42"}, {}).ok
