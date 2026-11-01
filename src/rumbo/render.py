"""Minimal, dependency-free template engine.

Syntax (a subset of Mustache):

- ``{{var}}``                 substitutes a variable (no spaces inside the braces).
- ``{{#flag}}...{{/flag}}``   keeps the block when ``flag`` is truthy.
- ``{{^flag}}...{{/flag}}``   keeps the block when ``flag`` is falsy.

A section tag alone on its line is removed together with its newline so it leaves no
blank line behind. ``${{ expr }}`` (GitHub Actions) is left untouched because it has
spaces inside the braces. There is no escaping: a literal ``{{`` cannot appear in a
template. Unknown variables, unknown sections and unbalanced section tags are errors.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_STANDALONE = re.compile(r"^[ \t]*(\{\{[#^/]\w+\}\})[ \t]*\r?\n", re.MULTILINE)
_SECTION = re.compile(r"\{\{([#^])(\w+)\}\}(.*?)\{\{/\2\}\}", re.DOTALL)
_VAR = re.compile(r"\{\{(\w+)\}\}")
_LEFTOVER = re.compile(r"\{\{[#^/]\w+\}\}")


class TemplateError(KeyError):
    """Missing variable, unknown section or unbalanced section tag."""


def render(text: str, ctx: Mapping[str, object]) -> str:
    text = _STANDALONE.sub(r"\1", text)
    out = _render(text, ctx)
    leftover = _LEFTOVER.search(out)
    if leftover:
        raise TemplateError(f"unbalanced or nested section tag: {leftover.group(0)}")
    return out


def _render(text: str, ctx: Mapping[str, object]) -> str:
    def section(match: re.Match[str]) -> str:
        kind, key, body = match.groups()
        if key not in ctx:
            raise TemplateError(f"unknown section: {key}")
        keep = bool(ctx[key]) if kind == "#" else not ctx[key]
        return _render(body, ctx) if keep else ""

    text = _SECTION.sub(section, text)

    def var(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in ctx:
            raise TemplateError(f"unknown variable: {key}")
        return str(ctx[key])

    return _VAR.sub(var, text)
