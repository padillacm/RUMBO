"""Lectura del estado de un proyecto RUMBO ya montado."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

MARCA_PLANTILLA = "<!-- rumbo:plantilla"


@dataclass(frozen=True)
class Proyecto:
    raiz: Path
    pkg: str
    llm: bool
    rigor: str

    @classmethod
    def cargar(cls, raiz: Path) -> Proyecto:
        pyproject = raiz / "pyproject.toml"
        if not pyproject.exists():
            raise FileNotFoundError(f"No hay pyproject.toml en {raiz}; ¿ejecutaste 'rumbo init'?")
        datos = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        cfg = datos.get("tool", {}).get("rumbo")
        if cfg is None:
            raise ValueError("pyproject.toml no tiene [tool.rumbo]; ¿ejecutaste 'rumbo init'?")
        return cls(raiz, cfg["pkg"], bool(cfg.get("llm", False)), cfg.get("rigor", "mvp"))

    def leer(self, rel: str) -> str | None:
        p = self.raiz / rel
        return p.read_text(encoding="utf-8") if p.is_file() else None


def es_plantilla(texto: str | None) -> bool:
    """True si el documento no existe o aún conserva la marca de plantilla."""
    return texto is None or MARCA_PLANTILLA in texto


def slug(texto: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:60] or "sin-titulo"


def seccion(texto: str, titulo: str) -> str:
    """Contenido de una sección markdown '## titulo' (hasta la siguiente '## ')."""
    patron = re.compile(rf"^##\s+{re.escape(titulo)}[^\n]*\n(.*?)(?=^##\s|\Z)", re.M | re.S | re.I)
    m = patron.search(texto)
    if not m:
        return ""
    cuerpo = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S)
    return cuerpo.strip()


def documentos_numerados(carpeta: Path) -> list[Path]:
    """Archivos NNNN-*.md excluyendo la plantilla 0000."""
    if not carpeta.is_dir():
        return []
    return sorted(
        p for p in carpeta.glob("[0-9][0-9][0-9][0-9]-*.md") if not p.name.startswith("0000-")
    )
