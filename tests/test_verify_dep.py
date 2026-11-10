"""`rumbo verify-dep` (F-0007) with the HTTP layer faked: no test here opens a connection.

`urllib.request.urlopen` is replaced for every test by `_Network`, which answers by URL, records
what was sent and fails the test on a request it was not told about.
"""

import ast
import base64
import datetime as dt
import email.message
import http.client
import io
import json
import types
import urllib.error
import urllib.request
from pathlib import Path
from typing import Self

import pytest

from rumbo import cli
from rumbo.cli import main

PYPI = "https://pypi.org/pypi/{}/json"
OSV = "https://api.osv.dev/v1/query"
INDEX = "https://index.example/simple/"
REMINDER = "Reminder: the human approves every new dependency; justify it in the PR."
TODAY = dt.date(2026, 6, 30)

Answer = str | BaseException


class _Response(io.BytesIO):
    """What `urlopen` hands back: a body to read, its headers, and a context manager."""

    def __init__(self, body: str) -> None:
        super().__init__(body.encode("utf-8"))
        self.headers = email.message.Message()
        self.headers["Content-Type"] = "application/json"


class _Network:
    """Stands in for `urllib.request.urlopen`: a body or an exception per URL."""

    def __init__(self) -> None:
        self.answers: dict[str, Answer] = {OSV: '{"vulns": []}'}
        self.sent: list[urllib.request.Request] = []
        self.timeouts: list[float | None] = []

    def urlopen(self, request: urllib.request.Request, timeout: float | None = None) -> _Response:
        self.sent.append(request)
        self.timeouts.append(timeout)
        answer = self.answers.get(request.full_url)
        if answer is None:
            raise AssertionError(f"unexpected request to {request.full_url}")
        if isinstance(answer, BaseException):
            raise answer
        return _Response(answer)

    @property
    def urls(self) -> list[str]:
        return [request.full_url for request in self.sent]


class _FixedDate(dt.date):
    """`date.today()` pinned to TODAY, so an age in days does not depend on the calendar."""

    @classmethod
    def today(cls) -> Self:
        return cls(TODAY.year, TODAY.month, TODAY.day)


@pytest.fixture(autouse=True)
def net(monkeypatch: pytest.MonkeyPatch) -> _Network:
    """Every test runs without the network and without the developer's own index variables."""
    for key in cli.INDEX_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    network = _Network()
    monkeypatch.setattr(urllib.request, "urlopen", network.urlopen)
    return network


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://unused.invalid/", code, "status", email.message.Message(), None
    )


def _document(name: str = "demo-pkg", first: str = "2020-01-01", **info: object) -> str:
    """A PyPI JSON document with two releases; `first` is the day of the earliest upload."""
    return json.dumps(
        {
            "info": {
                "name": name,
                "version": "1.1",
                "summary": "A demo",
                "project_urls": {"Source": "https://example.org/demo"},
                **info,
            },
            "releases": {
                "1.1": [{"upload_time_iso_8601": "2026-06-29T08:00:00.000000Z"}],
                "1.0": [
                    {"upload_time_iso_8601": f"{first}T23:59:59.000000Z"},
                    {"upload_time_iso_8601": "2026-06-28T08:00:00.000000Z"},
                ],
            },
        }
    )


# Covers F-0007.R1 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_a_name_pypi_answers_404_for_does_not_exist(
    net: _Network, capsys: pytest.CaptureFixture[str]
) -> None:
    net.answers[PYPI.format("ghost-pkg")] = _http_error(404)
    assert main(["verify-dep", "ghost-pkg"]) == 1
    out = capsys.readouterr().out
    assert "ghost-pkg: does NOT exist on PyPI. Hallucinated name? (slopsquatting)" in out
    # Nothing else is asked about a name that does not exist: no OSV query.
    assert net.urls == [PYPI.format("ghost-pkg")]


# Covers F-0007.R2 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize(("days", "warned"), [(89, True), (90, False)])
def test_first_release_is_the_earliest_upload_and_under_90_days_it_warns(
    net: _Network,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    days: int,
    warned: bool,
) -> None:
    monkeypatch.setattr(cli, "dt", types.SimpleNamespace(date=_FixedDate))
    first = (TODAY - dt.timedelta(days=days)).isoformat()
    net.answers[PYPI.format("demo-pkg")] = _document(first=first)
    assert main(["verify-dep", "demo-pkg"]) == (1 if warned else 0)
    out = capsys.readouterr().out
    assert f"first release: {first} ({days} days ago)" in out
    assert ("younger than 90 days: review with care" in out) is warned


# Covers F-0007.R2 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_package_facts_are_printed_and_no_repository_or_another_canonical_name_warns(
    net: _Network, capsys: pytest.CaptureFixture[str]
) -> None:
    net.answers[PYPI.format("Demo_Pkg")] = _document()
    assert main(["verify-dep", "Demo_Pkg"]) == 0  # the same name after PEP 503 normalisation
    out = capsys.readouterr().out
    assert "demo-pkg 1.1 — A demo" in out
    assert "first release: 2020-01-01 (" in out
    assert "repository: https://example.org/demo  ·  releases: 2" in out
    assert "canonical name" not in out and "declares no repository" not in out

    net.answers[PYPI.format("demo")] = _document(project_urls=None)
    assert main(["verify-dep", "demo"]) == 1
    out = capsys.readouterr().out
    assert "the canonical name is 'demo-pkg'" in out
    assert "repository: (not declared)" in out and "declares no repository" in out


# Covers F-0007.R3 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_look_alikes_are_popular_names_within_two_edits_of_the_normalised_name() -> None:
    assert cli._lookalikes("request") == ["requests"]  # one edit
    assert cli._lookalikes("reqeusts") == ["requests"]  # a transposition is two edits
    assert cli._lookalikes("Nunpy") == ["numpy"]
    assert cli._lookalikes("reqeustss") == []  # three edits
    assert cli._lookalikes("demo-pkg") == []
    # A popular package is not its own look-alike, however it is spelled.
    assert cli._lookalikes("requests") == []
    assert cli._lookalikes("Python_Dateutil") == []


# Covers F-0007.R3 and F-0007.R6 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_a_look_alike_unknown_to_pypi_still_warns_when_an_index_is_used(
    net: _Network, capsys: pytest.CaptureFixture[str]
) -> None:
    net.answers[INDEX + "reqeusts/"] = '{"versions": ["1.0"]}'
    net.answers[PYPI.format("reqeusts")] = _http_error(404)
    assert main(["verify-dep", "--index", INDEX, "reqeusts"]) == 1
    out = capsys.readouterr().out
    assert "exists on index.example: yes" in out
    assert f"not on PyPI {cli.PYPI_LABEL}" in out
    assert f"looks like a popular package: requests (typosquatting?) {cli.PYPI_LABEL}" in out
    assert "does NOT exist" not in out
    assert net.urls == [INDEX + "reqeusts/", PYPI.format("reqeusts")]


# Covers F-0007.R4 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_vulnerabilities_known_to_osv_are_counted_as_a_warning(
    net: _Network, capsys: pytest.CaptureFixture[str]
) -> None:
    net.answers[PYPI.format("Demo_Pkg")] = _document()
    net.answers[OSV] = json.dumps({"vulns": [{"id": "GHSA-1"}, {"id": "PYSEC-2"}]})
    assert main(["verify-dep", "Demo_Pkg"]) == 1
    out = capsys.readouterr().out
    assert "2 known vulnerabilities in OSV (check the affected versions)" in out
    query = net.sent[-1]
    assert (query.get_method(), query.full_url) == ("POST", OSV)
    # OSV is asked about the canonical name PyPI returned, in the PyPI ecosystem.
    assert isinstance(query.data, bytes)
    assert json.loads(query.data) == {"package": {"name": "demo-pkg", "ecosystem": "PyPI"}}


# Covers F-0007.R4 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize(
    "answer",
    [TimeoutError("timed out"), urllib.error.URLError("unreachable"), "<html>busy</html>"],
    ids=["timeout", "connection", "not-json"],
)
def test_osv_out_of_reach_is_said_and_does_not_change_the_exit_code(
    net: _Network, capsys: pytest.CaptureFixture[str], answer: Answer
) -> None:
    net.answers[PYPI.format("demo-pkg")] = _document()
    net.answers[OSV] = answer
    assert main(["verify-dep", "demo-pkg"]) == 0
    out = capsys.readouterr().out
    assert "(OSV not reachable: vulnerability check skipped)" in out
    assert "known vulnerabilities" not in out


# Covers F-0007.R6 and F-0007.R9 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_with_an_index_the_normalised_name_goes_there_first_and_pypi_lines_are_labelled(
    net: _Network, capsys: pytest.CaptureFixture[str]
) -> None:
    net.answers[INDEX + "demo-pkg/"] = '{"versions": ["1.0", "1.1"]}'
    net.answers[PYPI.format("Demo.Pkg")] = _document()
    assert main(["verify-dep", "--index", INDEX, "Demo.Pkg"]) == 0
    assert net.urls == [INDEX + "demo-pkg/", PYPI.format("Demo.Pkg"), OSV]
    lookup = net.sent[0]
    assert (lookup.get_method(), lookup.data) == ("GET", None)
    assert lookup.header_items() == [("Accept", f"{cli.SIMPLE_JSON}, text/html;q=0.1")]
    lines = capsys.readouterr().out.splitlines()
    assert "   index: https://index.example/simple/" in lines
    assert "   exists on index.example: yes" in lines
    assert "   versions on index.example: 2 (latest 1.1)" in lines
    # What was measured on PyPI says so on every line; what the index answered does not.
    measured_on_pypi = [line for line in lines if "PyPI" in line]
    assert len(measured_on_pypi) == 3
    assert all(line.endswith(cli.PYPI_LABEL) for line in measured_on_pypi)
    assert measured_on_pypi[0].startswith("   on PyPI: demo-pkg 1.1")


# Covers F-0007.R7 and F-0007.R8 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_an_index_that_times_out_is_reported_without_its_credentials(
    net: _Network, capsys: pytest.CaptureFixture[str]
) -> None:
    index = INDEX.replace("https://", "https://alice:s3cret-t0ken@")
    net.answers[INDEX + "demo-pkg/"] = TimeoutError(f"timed out reading {index}demo-pkg/")
    net.answers[PYPI.format("demo-pkg")] = _document()
    assert main(["verify-dep", "--index", index, "demo-pkg"]) == 2
    captured = capsys.readouterr()
    # The error text named the raw URL; what is printed names the sanitised one.
    reason = f"timed out reading {INDEX}demo-pkg/"
    assert f"could not query index.example ({reason}). Verify by hand." in captured.out
    assert "alice" not in captured.out + captured.err
    assert "s3cret-t0ken" not in captured.out + captured.err
    # The credentials travel as a header of the request to the index and nowhere else.
    assert net.urls == [INDEX + "demo-pkg/", PYPI.format("demo-pkg"), OSV]
    basic = "Basic " + base64.b64encode(b"alice:s3cret-t0ken").decode()
    assert net.sent[0].get_header("Authorization") == basic
    assert all(not request.has_header("Authorization") for request in net.sent[1:])
    # The index failing does not stop the PyPI measurements for the same package.
    assert f"on PyPI: demo-pkg 1.1 — A demo {cli.PYPI_LABEL}" in captured.out


# Covers F-0007.R8 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize(
    "answer",
    [
        TimeoutError("timed out"),
        urllib.error.URLError("no route to host"),
        _http_error(500),
        "<html>not JSON</html>",
        http.client.BadStatusLine("garbage"),
    ],
    ids=["timeout", "connection", "http-500", "not-json", "bad-status-line"],
)
def test_pypi_that_cannot_be_read_is_exit_2_with_a_message_not_a_traceback(
    net: _Network, capsys: pytest.CaptureFixture[str], answer: Answer
) -> None:
    net.answers[PYPI.format("demo-pkg")] = answer
    assert main(["verify-dep", "demo-pkg"]) == 2
    out = capsys.readouterr().out
    assert "demo-pkg: could not query PyPI (" in out and "Verify by hand." in out
    assert out.endswith(f"\n{REMINDER}\n")
    assert net.urls == [PYPI.format("demo-pkg")]


# Covers F-0007.R8 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_a_pypi_document_with_missing_fields_is_shown_as_unknown(
    net: _Network, capsys: pytest.CaptureFixture[str]
) -> None:
    net.answers[PYPI.format("demo-pkg")] = "{}"
    assert main(["verify-dep", "demo-pkg"]) == 1
    out = capsys.readouterr().out
    assert "demo-pkg ? — " in out
    assert "first release: ? (? days ago)" in out
    assert "repository: (not declared)  ·  releases: 0" in out
    assert "younger than 90 days" not in out  # an unknown age is not reported as young


# Covers F-0007.R8 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize(
    "body",
    [
        "[]",
        '"a string"',
        '{"info": ["not", "an", "object"]}',
        '{"info": {"name": "demo-pkg"}, "releases": {"1.0": [{"upload_time_iso_8601": 20200101}]}}',
        '{"info": {"name": "demo-pkg"}, "releases": {"1.0": [{"upload_time_iso_8601": "soon"}]}}',
        '{"info": {"name": "demo-pkg", "project_urls": ["https://example.org"]}}',
    ],
)
def test_a_pypi_answer_of_an_unexpected_shape_is_exit_2_not_a_traceback(
    net: _Network, capsys: pytest.CaptureFixture[str], body: str
) -> None:
    """Threat model, boundary 3: the answer is untrusted data. Valid JSON of the wrong shape
    used to raise AttributeError or TypeError out of the command."""
    net.answers[PYPI.format("demo-pkg")] = body
    assert main(["verify-dep", "demo-pkg"]) == 2
    out = capsys.readouterr().out
    assert "PyPI answered in an unexpected shape. Verify by hand." in out
    assert out.rstrip().endswith(REMINDER)


# Covers F-0007.R4 and F-0007.R8 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize("body", ["[]", '{"vulns": "many"}', '{"vulns": {"id": 1}}'])
def test_an_osv_answer_of_an_unexpected_shape_counts_as_not_reachable(
    net: _Network, capsys: pytest.CaptureFixture[str], body: str
) -> None:
    net.answers[PYPI.format("demo-pkg")] = _document()
    net.answers[OSV] = body
    assert main(["verify-dep", "demo-pkg"]) == 0
    assert "OSV not reachable: vulnerability check skipped" in capsys.readouterr().out


# Covers F-0007.R7 and F-0007.R8 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize(
    "index",
    [
        "https://user:s3cret@[not-an-address/simple/",
        "https://user:s3cret@index.example:port/simple/",
    ],
)
def test_an_index_url_that_cannot_be_parsed_is_a_usage_error_without_its_credentials(
    net: _Network, capsys: pytest.CaptureFixture[str], index: str
) -> None:
    """It used to raise ValueError out of the command; a port that is not a number was dropped
    and the request went to the default port."""
    assert main(["verify-dep", "--index", index, "demo-pkg"]) == 2
    captured = capsys.readouterr()
    assert "the index URL cannot be parsed" in captured.err
    assert "s3cret" not in captured.out + captured.err
    assert net.urls == []


# Covers F-0007.R8 and F-0007.R9 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_an_index_url_that_is_not_http_is_refused_without_a_request(
    net: _Network, capsys: pytest.CaptureFixture[str]
) -> None:
    net.answers[PYPI.format("demo-pkg")] = _document()
    assert main(["verify-dep", "--index", "file:///etc/passwd", "demo-pkg"]) == 2
    out = capsys.readouterr().out
    assert "the index URL must be http(s)" in out and "Verify by hand." in out
    assert net.urls == [PYPI.format("demo-pkg"), OSV]


# Covers F-0007.R8 and F-0007.R9 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_only_the_package_name_travels_to_pypi_and_osv_over_https(net: _Network) -> None:
    net.answers[PYPI.format("demo-pkg")] = _document()
    assert main(["verify-dep", "demo-pkg"]) == 0
    lookup, query = net.sent
    assert (lookup.get_method(), lookup.full_url) == ("GET", "https://pypi.org/pypi/demo-pkg/json")
    assert lookup.data is None
    assert (query.get_method(), query.full_url) == ("POST", "https://api.osv.dev/v1/query")
    assert query.data == b'{"package": {"name": "demo-pkg", "ecosystem": "PyPI"}}'
    # No header carries anything of the project or the machine, and no request waits forever.
    assert [r.header_items() for r in net.sent] == [[("Content-type", "application/json")]] * 2
    assert net.timeouts == [15, 15]


# Covers F-0007.R9 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_a_name_is_quoted_into_one_path_segment_and_plain_http_is_refused(net: _Network) -> None:
    quoted = "https://pypi.org/pypi/..%2F..%2Fsimple%3Fq%3D1/json"
    net.answers[quoted] = _http_error(404)
    assert main(["verify-dep", "../../simple?q=1"]) == 1
    assert net.urls == [quoted]
    with pytest.raises(ValueError, match="only https"):
        cli._http_json("http://pypi.org/pypi/demo-pkg/json")
    assert net.urls == [quoted]  # refused before any request


# Covers F-0007.R9 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_urlopen_is_called_only_by_the_two_verify_dep_lookups() -> None:
    def mentions(tree: ast.AST) -> int:
        return sum(
            (isinstance(node, ast.Attribute) and node.attr == "urlopen")
            or (isinstance(node, ast.Name) and node.id == "urlopen")
            for node in ast.walk(tree)
        )

    package = Path(cli.__file__).parent
    found: dict[str, int] = {}
    for file in sorted(package.rglob("*.py")):
        if "templates" in file.relative_to(package).parts:
            continue  # code of the generated projects, not of the CLI
        tree = ast.parse(file.read_text(encoding="utf-8"))
        if file.name != "cli.py":
            assert mentions(tree) == 0, file
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and mentions(node):
                found[node.name] = mentions(node)
        assert sum(found.values()) == mentions(tree)  # none at module level
    assert found == {"_http_json": 1, "index_versions": 1}


# Covers F-0007.R10 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_exit_code_is_the_worst_verdict_and_the_reminder_closes_every_run(
    net: _Network, capsys: pytest.CaptureFixture[str]
) -> None:
    net.answers[PYPI.format("demo-pkg")] = _document()
    net.answers[PYPI.format("ghost-pkg")] = _http_error(404)
    net.answers[PYPI.format("down-pkg")] = urllib.error.URLError("unreachable")
    runs = (
        (["demo-pkg"], 0),
        (["demo-pkg", "ghost-pkg"], 1),
        (["down-pkg", "ghost-pkg", "demo-pkg"], 2),
    )
    for packages, code in runs:
        assert main(["verify-dep", *packages]) == code
        out = capsys.readouterr().out
        # A package that fails does not stop the ones after it, and the reminder is the last line.
        assert all(f"{name}" in out for name in packages)
        assert out.endswith(f"\n{REMINDER}\n")
