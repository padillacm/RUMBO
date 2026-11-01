"""Graders deterministas (A7). Cada grader recibe (salida, esperado) y devuelve un Veredicto.

Los jueces LLM se añaden DESPUÉS del primer análisis de errores y solo validados contra
etiquetas humanas (TPR/TNR). Registra graders nuevos en GRADERS.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Veredicto:
    ok: bool
    detalle: str = ""


Grader = Callable[[dict[str, Any], dict[str, Any]], Veredicto]


def igualdad_campos(salida: dict[str, Any], esperado: dict[str, Any]) -> Veredicto:
    """Cada campo de `esperado` debe ser igual en `salida`."""
    distintos = [k for k, v in esperado.items() if salida.get(k) != v]
    return Veredicto(not distintos, f"campos distintos: {distintos}" if distintos else "")


def numerico(salida: dict[str, Any], esperado: dict[str, Any]) -> Veredicto:
    """`esperado = {"campo": "valor", "valor": 12.3, "rel_tol": 1e-3, "abs_tol": 0}`."""
    campo = esperado.get("campo", "valor")
    obtenido = salida.get(campo)
    if not isinstance(obtenido, int | float):
        return Veredicto(False, f"{campo!r} ausente o no numérico: {obtenido!r}")
    ok = math.isclose(
        obtenido,
        esperado["valor"],
        rel_tol=esperado.get("rel_tol", 1e-6),
        abs_tol=esperado.get("abs_tol", 0.0),
    )
    return Veredicto(ok, "" if ok else f"{obtenido} != {esperado['valor']}")


def argumentos_tool(salida: dict[str, Any], esperado: dict[str, Any]) -> Veredicto:
    """Alguna llamada en `salida["tool_calls"]` coincide en nombre y en los argumentos dados.

    `esperado = {"tool": "consultar", "args": {"metrica": "PrecBolsNaci"}}`.
    """
    for llamada in salida.get("tool_calls", []):
        if llamada.get("name") != esperado["tool"]:
            continue
        args = llamada.get("args", {})
        if all(args.get(k) == v for k, v in esperado.get("args", {}).items()):
            return Veredicto(True)
    return Veredicto(False, f"ninguna llamada a {esperado['tool']!r} con {esperado.get('args')}")


def abstencion(salida: dict[str, Any], esperado: dict[str, Any]) -> Veredicto:
    """El sistema debe abstenerse (no inventar) cuando falta el dato."""
    ok = salida.get("abstencion") is True
    return Veredicto(ok, "" if ok else "respondió en lugar de abstenerse")


GRADERS: dict[str, Grader] = {
    "igualdad_campos": igualdad_campos,
    "numerico": numerico,
    "argumentos_tool": argumentos_tool,
    "abstencion": abstencion,
}
