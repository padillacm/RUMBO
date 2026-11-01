# ADR-0002: Templates are rendered with a Mustache subset written in-house
Status: accepted    Date: 2026-10-30

## Context
Generated files vary by package name, LLM yes/no, provider and rigor. GitHub Actions files
contain `${{ }}` expressions that a general-purpose engine would try to interpret.

## Drivers (non-functional requirements)
No dependency (ADR-0001); templates readable as the files they produce; impossible to leave a
tag unrendered; `${{ }}` untouched.

## Options considered
1. `str.format` / f-strings: braces everywhere in YAML and TOML collide.
2. Jinja2: powerful, but a dependency and a second syntax to learn.
3. Mustache subset: `{{var}}`, `{{#flag}}…{{/flag}}`, `{{^flag}}…{{/flag}}`, standalone-line
   removal; `${{ }}` is left alone; unbalanced or nested sections raise.

## Decision and rationale
Option 3 (`src/rumbo/render.py`). Sections without nesting cover every variation we have; the
engine refuses what it cannot render instead of emitting a half-rendered file.

## Consequences (+/−)
+ 80 lines, fully tested; templates look like their output.
− No loops, no filters, no nested sections: variations that need them become separate files
  with a `CONDITIONS` entry in `scaffold.py`.

## Fitness function
`tests/test_render.py` (standalone lines, `${{ }}` untouched, errors on leftover and nested
tags) and `tests/test_scaffold.py::test_every_combination_renders` (every provider × rigor
yields valid TOML, Python and YAML).

## Reversibility: medium
