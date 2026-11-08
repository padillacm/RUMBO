# ADR-0014: `verify-dep` honours a private package index, and the minimum Python is a project setting
Status: accepted    Date: 2026-11-08

## Context
`rumbo verify-dep` asks `pypi.org` whether a package exists, how old it is, how close its name
is to a popular one, and OSV for known vulnerabilities. Companies route `uv` and `pip` through
Artifactory, AWS CodeArtifact or GitLab (`UV_INDEX_URL`, `PIP_INDEX_URL`): a package can exist
only there, or there with a different content, and the verification is silently answered by a
different source than the one the project installs from. The minimum Python version is written
three times in the templates (`requires-python`, ruff's `target-version`, the CI matrix of a
library or cli, the Dockerfile's `ARG`) and cannot be chosen. The community study adds a
security requirement (Error 10): a private index URL carries credentials, and they must never
reach the console or a log.

## Drivers (non-functional requirements)
Verification against the index the project really installs from; what cannot be measured there
is said so, never guessed; credentials stripped from every printed URL; standard library only
(PEP 691 JSON with a PEP 503 HTML fallback through `html.parser`); one Python setting feeding
every place that names a version; no new A0 question.

## Options considered
1. Keep PyPI only and print a notice when an index variable is set.
2. Query the private index's simple API for existence and versions, keep age, typosquatting
   and OSV against PyPI and label them as such.
3. Re-implement the metadata checks against the private index: the simple API has no upload
   dates or project metadata, so this cannot be done faithfully.

## Decision and rationale
Option 2 for the index; a `python` setting for the version. The contract:

**Index resolution.** `--index URL` on `verify-dep`, else the first set of `UV_INDEX_URL`,
`UV_DEFAULT_INDEX`, `PIP_INDEX_URL`. When the resolved host is `pypi.org` (or nothing is set)
the behaviour is today's. Otherwise `verify-dep` requests `<index>/<normalized-name>/` with
`Accept: application/vnd.pypi.simple.v1+json` and parses the PEP 691 document (`versions` when
present, else the versions parsed from `files[].filename`); when the server answers HTML, it
parses the anchors with `html.parser` the same way. It prints `index: <url>` with the user info
removed by `urllib.parse.urlsplit` (`scheme://host[:port]/path`), and the same sanitised form is
the only one that ever appears in messages and errors. The result lines become: `exists on
<host>: yes/no`, `versions on <host>: N (latest X)`, then the PyPI lines labelled `(measured
against PyPI, not against your index)`: age, repository, typosquatting distance, OSV. A package
that exists on the index but not on PyPI is reported, not failed. Network errors are reported
per source, as today.

**Python version.** `rumbo init --python X.Y` (default `3.11`, accepted `3.11` and above up to
the newest entry of `SUPPORTED_PYTHONS`), stored as `[tool.rumbo].python`; `Project.load`
defaults it to `3.11` when absent and `rumbo update` writes the key. `SUPPORTED_PYTHONS` is one
tuple in `src/rumbo/project.py`, today `("3.11", "3.12", "3.13")`, bumped on release (the
repository rule against copying versions from memory applies: a new entry needs the release to
exist). The context exposes `python` (`3.12`), `python_nodot` (`312`) and `python_matrix` (the
JSON list of supported versions from the minimum upwards). Templates read them: `requires-python
= ">={{python}}"`, `target-version = "py{{python_nodot}}"`, `python: {{python_matrix}}` in the
CI matrix of a library or cli, `ARG PYTHON_VERSION={{python}}` in the Dockerfile, and a new
`.python-version` with `{{python}}` so `uv` pins the interpreter the project promises. The
Windows job and the single-version jobs use the minimum.

## Consequences (+/−)
+ Existence and versions are checked where the project installs from; the rest is labelled.
+ No credential ever reaches the console, a report or a log.
+ One version setting; a project that promises 3.12 tests on 3.12 and 3.13 and builds on 3.12.
− The private-index lookup cannot measure age or typosquatting; the label says so.
− A fourth place to keep in sync (`.python-version`) is now generated, not hand-written.

## Fitness function
`tests/test_cli.py`: index resolution order and `--index`; a local `http.server` serving a PEP
691 JSON document, one serving PEP 503 HTML, and one answering 404, each exercised through
`verify-dep`; credentials in the URL never appear in stdout or stderr; the PyPI lines carry the
label. `tests/test_scaffold.py`: `--python 3.12` renders `>=3.12`, `py312`, the matrix
`["3.12", "3.13"]`, `ARG PYTHON_VERSION=3.12` and `.python-version`; the default is unchanged;
an unsupported version is refused; `Project.load` defaults and `update` writes the key. The e2e
matrix gains `--kind library --no-llm --rigor mvp --python 3.12`.

## Reversibility: high
The index lookup is one function behind a flag and three environment variables; the version is
a context value with a default equal to today's.
