<!-- rumbo:template — delete this line when the document is complete. Only the HUMAN removes it; `rumbo gate` checks it. -->
# F-0007: Dependencies verified before they are added (`rumbo verify-dep`)
Story: As a developer working with a coding agent I want every package name checked before it is
added (does it exist, how old is it, does it imitate a popular name, does it have known
vulnerabilities, is it on the index my project installs from) so that a hallucinated or
typosquatted name is caught before `uv add`, and the decision to add it stays with me.

Scope: the `verify-dep` subcommand of the CLI: `cmd_verify_dep` and the block under
`# verify-dep` in `src/rumbo/cli.py` (`POPULAR_PACKAGES` to `cmd_verify_dep`) and its `argparse`
entry. The command reports and exits; it adds nothing and blocks nothing.
Decisions: ADR-0001 (standard library only), ADR-0014 (private index). Written retroactively:
it describes the code as it is on 2026-11-09, and the last section lists what that code does
not handle.

## Requirements (EARS)
<!-- Ubiquitous: The system shall… · Event: When…, the system shall… · State: While…, the system shall…
     Unwanted: If…, then the system shall… · Optional: Where…, the system shall… -->
- R1 (unwanted): If PyPI answers 404 for a requested name and no private index is in use, then `verify-dep` shall print `<name>: does NOT exist on PyPI. Hallucinated name? (slopsquatting)`, make no further request about that name and give it the verdict 1.
- R2 (event): When PyPI knows the requested name, `verify-dep` shall print the package's canonical name, the version and summary PyPI reports, the day of its earliest file upload with its age in days, its repository URL and its number of releases, and shall add a warning when that age is under 90 days, when no repository is declared, and when the canonical name differs from the requested one after PEP 503 normalisation.
- R3 (unwanted): If the PEP 503-normalised name is within Levenshtein distance 2 of a name in `POPULAR_PACKAGES` and is not that name, then `verify-dep` shall warn `looks like a popular package: <names> (typosquatting?)`; the name compared is the canonical name of a package PyPI knows, or the requested name when a private index is in use and PyPI does not know it.
- R4 (event): When PyPI knows the requested name, `verify-dep` shall ask OSV (`POST https://api.osv.dev/v1/query`, ecosystem `PyPI`) about the canonical name and warn with the number of vulnerabilities returned; if OSV cannot be reached or does not answer JSON, it shall print `(OSV not reachable: vulnerability check skipped)` and leave the verdict unchanged.
- R5 (optional): Where `--index URL` is given, or else where one of `UV_INDEX_URL`, `UV_DEFAULT_INDEX`, `PIP_INDEX_URL` is set and not empty (the first in that order), `verify-dep` shall use that URL as the private index, unless its host is `pypi.org`, in which case it shall behave as if none were set.
- R6 (state): While a private index is in use, `verify-dep` shall first request `<index>/<normalised-name>/` asking for PEP 691 JSON and accepting PEP 503 HTML, read the versions from `versions`, else from the file names in `files`, else from the file names in the HTML anchors, and print `index: <url>`, then `exists on <host>: yes` with `versions on <host>: N (latest X)` or, on a 404, `exists on <host>: no` (verdict 1); it shall then run the PyPI checks of R2 to R4 with `(measured against PyPI, not against your index)` at the end of every line that reports one of them, and report a name PyPI does not know as `not on PyPI`, which is not a failure.
- R7 (ubiquitous): `verify-dep` shall never write the user name or the password of an index URL to stdout or stderr: the index is printed as `scheme://host[:port]/path`, an error message is printed with the raw URL replaced by that form, and the credentials are sent only to the index, as a Basic `Authorization` header.
- R8 (unwanted): If a source cannot be read (a connection error, a timeout, an HTTP status other than 404, a PyPI body that is not JSON, an index URL that is neither `http` nor `https`), then `verify-dep` shall print `could not query <source> (<reason>). Verify by hand.`, give the package the verdict 2 and go on, without a traceback, with the PyPI checks of that package when it is the index that failed and with the next package in every case; every request shall carry a 15-second timeout, and a PyPI document with missing fields shall be printed with `?` and `(not declared)` in their place.
- R9 (ubiquitous): The only project data `verify-dep` shall put in a request is the package name: `GET https://pypi.org/pypi/<name>/json` with the name percent-encoded into one path segment, `POST https://api.osv.dev/v1/query` with `{"package": {"name": <canonical name>, "ecosystem": "PyPI"}}` and, where an index is in use, `GET <index>/<normalised-name>/` with the credentials of R7; the PyPI and OSV requests shall be refused unless their URL is `https`, and no other code of the CLI shall call `urlopen`.
- R10 (ubiquitous): `verify-dep` shall examine every package named on the command line and exit with the worst verdict among them (0 when nothing was flagged, 1 when a package does not exist or carries a warning, 2 when a source could not be queried), and whatever the verdict its last line shall be `Reminder: the human approves every new dependency; justify it in the PR.`

## Acceptance criteria
<!-- pytest tests / Hypothesis properties that verify them (path::test). A test covers a requirement
     by mentioning its full id, e.g. `F-000N.R1`, in its docstring; `rumbo trace` builds the table. -->
In `tests/test_cli.py` (marked *cli*; the tests that came with the code) and
`tests/test_verify_dep.py` (the rest; written with this spec). Each test carries its requirement
id in a comment. No test reaches the network: they replace `pypi_info` and
`osv_vulnerabilities`, or `urllib.request.urlopen`, or serve an index on `127.0.0.1`.

| Requirement | Tests |
|---|---|
| R1 | `test_verify_dep_handles_bad_json_and_typos` (*cli*), `test_a_name_pypi_answers_404_for_does_not_exist` |
| R2 | `test_first_release_is_the_earliest_upload_and_under_90_days_it_warns`, `test_package_facts_are_printed_and_no_repository_or_another_canonical_name_warns` |
| R3 | `test_verify_dep_handles_bad_json_and_typos` (*cli*), `test_look_alikes_are_popular_names_within_two_edits_of_the_normalised_name`, `test_a_look_alike_unknown_to_pypi_still_warns_when_an_index_is_used` |
| R4 | `test_vulnerabilities_known_to_osv_are_counted_as_a_warning`, `test_osv_out_of_reach_is_said_and_does_not_change_the_exit_code` |
| R5 | `test_index_resolution_order` (*cli*), `test_verify_dep_resolves_the_index_from_the_environment_and_the_flag_wins` (*cli*) |
| R6 | `test_verify_dep_reads_a_pep691_index` (*cli*), `test_verify_dep_reads_a_pep503_html_index` (*cli*), `test_verify_dep_reports_a_404_as_absent_from_the_index` (*cli*), `test_simple_api_helpers` (*cli*), `test_with_an_index_the_normalised_name_goes_there_first_and_pypi_lines_are_labelled`, `test_a_look_alike_unknown_to_pypi_still_warns_when_an_index_is_used` |
| R7 | `test_verify_dep_never_prints_the_index_credentials` (*cli*), `test_simple_api_helpers` (*cli*), `test_an_index_that_times_out_is_reported_without_its_credentials` |
| R8 | `test_verify_dep_network_error` (*cli*), `test_verify_dep_handles_bad_json_and_typos` (*cli*), `test_verify_dep_never_prints_the_index_credentials` (*cli*), `test_pypi_that_cannot_be_read_is_exit_2_with_a_message_not_a_traceback`, `test_a_pypi_document_with_missing_fields_is_shown_as_unknown`, `test_an_index_that_times_out_is_reported_without_its_credentials`, `test_an_index_url_that_is_not_http_is_refused_without_a_request`, `test_only_the_package_name_travels_to_pypi_and_osv_over_https` |
| R9 | `test_only_the_package_name_travels_to_pypi_and_osv_over_https`, `test_a_name_is_quoted_into_one_path_segment_and_plain_http_is_refused`, `test_with_an_index_the_normalised_name_goes_there_first_and_pypi_lines_are_labelled`, `test_an_index_url_that_is_not_http_is_refused_without_a_request`, `test_urlopen_is_called_only_by_the_two_verify_dep_lookups` |
| R10 | `test_exit_code_is_the_worst_verdict_and_the_reminder_closes_every_run` |

No end-to-end run: `scripts/e2e.sh` does not call `verify-dep`, and no test queries the real
PyPI, OSV or a real private index.

## Data contracts and ownership
<!-- What two sessions (or two agents) must agree on BEFORE writing code. Omitting explicit data
     structures is what breaks parallel work (docs/method.md, section 3.1). -->
- Types / schemas: the command line `rumbo verify-dep NAME... [--index URL]`. From the PyPI JSON document: `info.name`, `info.version`, `info.summary`, `info.project_urls` (the first key matching `source|repo|code|github|home`, any case), `info.home_page` as the fallback repository, `releases: {version: [{upload_time_iso_8601}]}`. From OSV: `vulns: list`, counted. From the index: PEP 691 `versions: list` or `files[].filename`, or the text of the `<a>` elements of a PEP 503 page; a version is read from a wheel name (second `-` field) or an sdist name (after the last `-`, suffix `.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tgz` or `.zip`). `POPULAR_PACKAGES: tuple[str, ...]`, 56 normalised names fixed in the code. `INDEX_ENV_VARS = ("UV_INDEX_URL", "UV_DEFAULT_INDEX", "PIP_INDEX_URL")`.
- Signatures: `cmd_verify_dep(args) -> int`; `resolve_index(flag: str | None, env: Mapping[str, str]) -> str | None`; `sanitize_index(url: str) -> str`; `index_versions(index: str, name: str) -> list[str] | None` (None on a 404; `ValueError` when the scheme is not http(s)); `parse_simple(body: str, content_type: str) -> list[str]`; `latest_version(versions: list[str]) -> str | None`; `pypi_info(name: str) -> dict[str, Any] | None` (None on a 404); `osv_vulnerabilities(name: str) -> int | None` (None when OSV is out of reach); `_lookalikes(name: str) -> list[str]`. `NETWORK_ERRORS` is the tuple of exceptions that become "could not query".
- Shared invariants: exit codes 0 nothing flagged, 1 flagged, 2 could not query, the highest wins; everything is printed on stdout; the sanitised form is the only form of an index URL that is printed; with an index, the index answers existence and versions and PyPI answers the rest, each line saying which (ADR-0014); the index lookup failing does not skip the PyPI checks, and PyPI failing skips the OSV query; standard library only (`urllib`, `html.parser`, ADR-0001).
- Files owned by this spec (one writer): the `verify-dep` block and `argparse` entry of `src/rumbo/cli.py`, `tests/test_verify_dep.py`, the tests of `tests/test_cli.py` listed above with the `index` fixture they use.
- Interfaces touched that other specs depend on: the command name, which the generated permissions allow the agent to run (`Bash(rumbo verify-dep *)` in `src/rumbo/templates/dot_claude/settings.json`, F-0004) and which RUMBO.md, the generated `AGENTS.md`, the pull request template, the `rumbo-ancla`, `rumbo-o` and `rumbo-review` commands and the manual G4 item "New dependencies verified" quote (F-0005). No code reads its output or its exit code.

## Evals (LLM products)
<!-- case IDs, threshold -->
Not applicable: `verify-dep` calls no model.

## NFR
At most 3 requests per package (index, PyPI, OSV), one after another, each with a 15 s timeout
(R8) · 0 dependencies · 0 network calls in the test suite · cost 0 USD. No latency target: the
time is the registries'. The PRD sets no number for this feature.

## Out of scope
Deciding: the command prints facts and a verdict, the human approves (R10); nothing in the guard,
the runner or the gates runs it or requires its exit code. Age, repository, look-alike and OSV
measured on the private index (the simple API does not carry them, ADR-0014). An index declared
anywhere but `--index` and the three variables (`UV_EXTRA_INDEX_URL`, `[[tool.uv.index]]` in
`pyproject.toml`, `uv.toml`, `pip.conf`): none is read. Vulnerabilities by version: the OSV count
is for the package, every version together. Transitive dependencies, licences, maintainers,
download counts, ecosystems other than PyPI. Keeping `POPULAR_PACKAGES` current.

Not verified by any test, so not stated as a requirement: what happens on an HTTP redirect
(`urllib` follows it with its default handler), TLS certificate checking (`urllib` defaults),
and the transport of a plain-`http` index, which the threat model accepts as a risk.

## Questions raised while writing the spec (for the human; behaviour today, not requirements)
- A PyPI answer that is JSON of the wrong shape ends in a traceback, not in verdict 2: a list
  instead of an object, `info` that is not an object, a release whose file list is `null`, an
  upload date that is not ISO (`src/rumbo/cli.py:810-820`, outside the `try`). R8 covers a body
  that is not JSON and fields that are missing, nothing more.
- OSV: a protocol error (`http.client.HTTPException`, absent from the `except` at
  `src/rumbo/cli.py:584`) and `{"vulns": null}` (`:586`) end in a traceback.
- `--index` with a URL `urlsplit` rejects (`http://[bad/`) ends in a traceback
  (`src/rumbo/cli.py:606`); with an invalid port (`host:99999`) the port is dropped and the
  request, credentials included, goes to the default port (`:616-619`).
- OSV out of reach leaves the verdict at 0 (R4): a package can pass with the vulnerability check
  skipped. Intended?
- With an index in use, the name of every package, private ones included, is still sent to
  `pypi.org` and, when PyPI knows it, to `api.osv.dev`; there is no switch to stay on the index.
- With a plain-`http` index that carries credentials, the Basic header travels in clear; the
  accepted risk in `docs/security/threat-model.md` names only the package name.
- `pip` and `six` are within two edits of each other: `verify-dep pip` warns that it looks like
  `six`, and the reverse, so two popular packages get verdict 1.
