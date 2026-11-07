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
_TAG = re.compile(r"\{\{([#^/]?)(\w+)\}\}")


class TemplateError(KeyError):
    """Missing variable, unknown section or unbalanced section tag."""


def render(text: str, ctx: Mapping[str, object]) -> str:
    return _render(_STANDALONE.sub(r"\1", text), ctx)


def _render(text: str, ctx: Mapping[str, object]) -> str:
    """One left-to-right pass: substituted values are copied verbatim, never re-expanded."""
    out: list[str] = []
    pos = 0
    while match := _TAG.search(text, pos):
        out.append(text[pos : match.start()])
        kind, key = match.groups()
        if kind == "/":
            raise TemplateError(f"unbalanced or nested section tag: {match.group(0)}")
        if key not in ctx:
            raise TemplateError(f"unknown {'section' if kind else 'variable'}: {key}")
        if not kind:
            out.append(str(ctx[key]))
            pos = match.end()
            continue
        close = _closing_tag(text, key, match.end())
        keep = bool(ctx[key]) if kind == "#" else not ctx[key]
        if keep:
            out.append(_render(text[match.end() : close.start()], ctx))
        pos = close.end()
    out.append(text[pos:])
    return "".join(out)


def _closing_tag(text: str, key: str, start: int) -> re.Match[str]:
    """The ``{{/key}}`` that closes the section opened before ``start`` (nesting is an error)."""
    for tag in _TAG.finditer(text, start):
        kind, name = tag.groups()
        if name != key:
            continue
        if kind == "/":
            return tag
        raise TemplateError(f"unbalanced or nested section tag: {tag.group(0)}")
    raise TemplateError(f"unbalanced or nested section tag: {{{{#{key}}}}}")
