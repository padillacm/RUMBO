"""Prompts versionados. Cada prompt lleva un identificador de versión que queda en la traza.

Ejemplo:
    PROMPT_EXTRAER_INTENCION = Prompt(id="extraer_intencion", version="2026-01-01.1", texto="...")
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    texto: str

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"
