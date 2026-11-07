"""Versioned prompts. Each prompt carries a version identifier that ends up in the trace.

Example:
    EXTRACT_INTENT = Prompt(id="extract_intent", version="2026-01-01.1", text="...")
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    text: str

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"
