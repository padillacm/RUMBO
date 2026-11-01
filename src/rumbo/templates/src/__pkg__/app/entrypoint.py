"""Función de entrada que ejecutan las evals (evals/run_evals.py --entry).

ESQUELETO: reemplázalo en la primera tarea ANCLA por el workflow real. Mientras tanto
responde solo al caso de ejemplo de evals/datasets/smoke.jsonl para que el arnés esté verde.
"""

from typing import Any


def responder(entrada: dict[str, Any]) -> dict[str, Any]:
    if entrada.get("pregunta") == "ping":
        return {"respuesta": "pong"}
    return {"abstencion": True, "motivo": "sin implementar"}
