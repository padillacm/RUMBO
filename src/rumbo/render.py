"""Motor de plantillas mínimo, sin dependencias.

Sintaxis (subconjunto de Mustache):

- ``{{var}}``            sustituye una variable (sin espacios dentro de las llaves).
- ``{{#flag}}...{{/flag}}`` incluye el bloque si ``flag`` es verdadero.
- ``{{^flag}}...{{/flag}}`` incluye el bloque si ``flag`` es falso.

Una etiqueta de sección sola en su línea se elimina junto con su salto de línea,
para no dejar líneas vacías. ``${{ expr }}`` (GitHub Actions) no se toca porque
lleva espacios dentro de las llaves.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_STANDALONE = re.compile(r"^[ \t]*(\{\{[#^/]\w+\}\})[ \t]*\r?\n", re.MULTILINE)
_SECTION = re.compile(r"\{\{([#^])(\w+)\}\}(.*?)\{\{/\2\}\}", re.DOTALL)
_VAR = re.compile(r"\{\{(\w+)\}\}")


class TemplateError(KeyError):
    """Variable ausente en el contexto."""


def render(text: str, ctx: Mapping[str, object]) -> str:
    text = _STANDALONE.sub(r"\1", text)
    return _render(text, ctx)


def _render(text: str, ctx: Mapping[str, object]) -> str:
    def section(match: re.Match[str]) -> str:
        kind, key, body = match.groups()
        if key not in ctx:
            raise TemplateError(f"sección desconocida: {key}")
        keep = bool(ctx[key]) if kind == "#" else not ctx[key]
        return _render(body, ctx) if keep else ""

    text = _SECTION.sub(section, text)

    def var(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in ctx:
            raise TemplateError(f"variable desconocida: {key}")
        return str(ctx[key])

    return _VAR.sub(var, text)
