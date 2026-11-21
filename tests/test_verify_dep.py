"""`rumbo verify-dep` (F-0007, ADR-0028). No test here leaves this machine.

Most tests fake the HTTP layer: `cli._open`, the one place the CLI opens a connection, is replaced
for every test by `_Network`, which answers by URL, records what was sent and fails the test on a
request it was not told about. The tests that take the `serve` fixture go through the real
`urllib` path instead (redirects, proxies, deadline, body cap), against `http.server` instances
on 127.0.0.1, with PyPI and OSV stubbed.
"""

import _thread
import ast
import base64
import contextlib
import datetime as dt
import email.message
import http.client
import io
import json
import re
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Self

import pytest

from rumbo import cli
from rumbo.cli import main

PYPI = "https://pypi.org/pypi/{}/json"
OSV = "https://api.osv.dev/v1/query"
INDEX = "https://index.example/simple/"
REMINDER = "Reminder: the human approves every new dependency; justify it in the PR."
TODAY = dt.date(2026, 6, 30)
PROXY_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
BASIC = "Basic " + base64.b64encode(b"alice:s3cret-t0ken").decode()
NOT_A_PAGE = "the index answered, but not with a package page"


class _Response(io.BytesIO):
    """What the opener hands back: a body to read, its headers, and a context manager."""

    def __init__(self, body: str | bytes, content_type: str = "application/json") -> None:
        super().__init__(body if isinstance(body, bytes) else body.encode("utf-8"))
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type


Answer = str | BaseException | _Response


class _Network:
    """Stands in for `cli._open`: a body, a whole response or an exception per URL."""

    def __init__(self) -> None:
        self.answers: dict[str, Answer] = {OSV: '{"vulns": []}'}
        self.sent: list[urllib.request.Request] = []
        self.timeouts: list[float | None] = []
        self.direct: list[bool] = []
        self.real: Callable[..., Any] = cli._open
        # The answer for a URL no test spelled out; without it such a request fails the test.
        self.fallback: Callable[[urllib.request.Request], Answer] | None = None

    def open(
        self, request: urllib.request.Request, timeout: float | None = None, direct: bool = False
    ) -> _Response:
        self.sent.append(request)
        self.timeouts.append(timeout)
        self.direct.append(direct)
        answer = self.answers.get(request.full_url)
        if answer is None and self.fallback is not None:
            answer = self.fallback(request)
        if answer is None:
            raise AssertionError(f"unexpected request to {request.full_url}")
        if isinstance(answer, BaseException):
            raise answer
        return answer if isinstance(answer, _Response) else _Response(answer)

    @property
    def urls(self) -> list[str]:
        return [request.full_url for request in self.sent]


def _no_urlopen(*args: object, **kwargs: object) -> None:
    raise AssertionError("the CLI opens connections through cli._open only (ADR-0028)")


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
    monkeypatch.setattr(cli, "_open", network.open)
    monkeypatch.setattr(urllib.request, "urlopen", _no_urlopen)
    return network


Respond = Callable[[BaseHTTPRequestHandler, threading.Event], None]


class _Server:
    """An `http.server` on 127.0.0.1 that records (path, Authorization) of every GET."""

    def __init__(self, respond: Respond) -> None:
        self.seen: list[tuple[str, str | None]] = []
        self.raw: list[str] = []  # the request line and every header, as they arrived
        self.stop = threading.Event()
        seen, raw, stop = self.seen, self.raw, self.stop

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                seen.append((self.path, self.headers.get("Authorization")))
                raw.append(f"{self.requestline}\n{self.headers}")
                with contextlib.suppress(OSError):  # the client hung up: that is the test
                    respond(self, stop)

            def log_message(self, format: str, *args: object) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/simple/"
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,), daemon=True).start()

    def close(self) -> None:
        self.stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()


def _answers(
    body: str | bytes,
    content_type: str = cli.SIMPLE_JSON,
    status: int = 200,
    location: str | None = None,
) -> Respond:
    data = body if isinstance(body, bytes) else body.encode("utf-8")

    def respond(handler: BaseHTTPRequestHandler, stop: threading.Event) -> None:
        handler.send_response(status)
        if location is not None:
            handler.send_header("Location", location)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    return respond


@pytest.fixture
def serve(net: _Network, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Respond], _Server]]:
    """Real servers on 127.0.0.1 and the real `urllib` path to them; PyPI and OSV are stubbed.

    A request whose URL names any other host fails the test before a connection is opened
    (`.invalid` never resolves: it is only ever asked of a local proxy).
    """
    servers: list[_Server] = []

    def loopback_only(
        request: urllib.request.Request, timeout: float | None = None, direct: bool = False
    ) -> Any:
        host = urllib.parse.urlsplit(request.full_url).hostname or ""
        assert host == "127.0.0.1" or host.endswith(".invalid"), f"a test reached for {host}"
        return net.real(request, timeout, direct)

    def start(respond: Respond) -> _Server:
        servers.append(_Server(respond))
        return servers[-1]

    monkeypatch.setattr(cli, "_open", loopback_only)
    monkeypatch.setattr(cli, "pypi_info", lambda name: None)
    monkeypatch.setattr(cli, "osv_vulnerabilities", lambda name: 0)
    for key in PROXY_ENV:
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(key.lower(), raising=False)
    yield start
    for server in servers:
        server.close()


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
    [
        TimeoutError("timed out"),
        urllib.error.URLError("unreachable"),
        "<html>busy</html>",
        http.client.IncompleteRead(b"{"),
        http.client.BadStatusLine("garbage"),
        "[" * 100_000 + "]" * 100_000,
    ],
    ids=["timeout", "connection", "not-json", "incomplete-read", "bad-status-line", "deep-json"],
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
        http.client.IncompleteRead(b"{"),
        "[" * 100_000 + "]" * 100_000,
        _Response(b"\xff\xfe\xff{", "application/json"),
    ],
    ids=[
        "timeout",
        "connection",
        "http-500",
        "not-json",
        "bad-status-line",
        "incomplete-read",
        "deep-json",
        "not-text",
    ],
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


# Covers F-0007.R7 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize(
    "index",
    [
        "http://alice:s3cret-t0ken@index.example/simple/",
        "http://alice@index.example/simple/",  # a token as the user name, no password
        "http://alice:s3cret-t0ken@10.0.0.5:8080/simple/",
    ],
)
def test_index_credentials_are_never_sent_over_plain_http(
    net: _Network, capsys: pytest.CaptureFixture[str], index: str
) -> None:
    """Decided by the human (2026-11-16), not accepted as a risk: Basic is not encryption, so
    over plain http the credentials are refused before any request. PyPI is still measured."""
    net.answers[PYPI.format("demo-pkg")] = _document()
    assert main(["verify-dep", "--index", index, "demo-pkg"]) == 2
    captured = capsys.readouterr()
    assert "refusing to send the index credentials over plain http; use https" in captured.out
    assert "Verify by hand." in captured.out
    assert "alice" not in captured.out + captured.err
    assert "s3cret-t0ken" not in captured.out + captured.err
    assert net.urls == [PYPI.format("demo-pkg"), OSV], "nothing was sent to the index"
    assert all(not request.has_header("Authorization") for request in net.sent)


# Covers F-0007.R7 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize(
    "index",
    [
        "http://index.example/simple/",  # plain http without credentials: allowed
        "https://alice:s3cret-t0ken@index.example/simple/",  # credentials over https: sent
        "http://alice:s3cret-t0ken@localhost:3141/simple/",  # loopback: never leaves the machine
        "http://alice:s3cret-t0ken@127.0.0.1:3141/simple/",
        "http://alice:s3cret-t0ken@[::1]:3141/simple/",
    ],
)
def test_https_or_loopback_or_no_credentials_still_query_the_index(
    net: _Network, capsys: pytest.CaptureFixture[str], index: str
) -> None:
    shown = cli.sanitize_index(index)
    net.answers[shown + "demo-pkg/"] = json.dumps({"versions": ["1.0"]})
    net.answers[PYPI.format("demo-pkg")] = _document()
    assert main(["verify-dep", "--index", index, "demo-pkg"]) == 0
    out = capsys.readouterr().out
    assert "exists on " in out and ": yes" in out
    assert net.urls[0] == shown + "demo-pkg/"
    sent_basic = net.sent[0].has_header("Authorization")
    assert sent_basic == ("alice" in index)


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
    and the request went to the default port. Since ADR-0028 it is no longer a usage error that
    ends the run: the index is reported as not queried (verdict 2, on stdout like every other
    source), PyPI and OSV are still measured, and the reminder closes the run (R8, R10)."""
    net.answers[PYPI.format("demo-pkg")] = _document()
    assert main(["verify-dep", "--index", index, "demo-pkg"]) == 2
    captured = capsys.readouterr()
    reason = "the index URL cannot be parsed (check the host and the port)"
    assert f"could not query the index ({reason}). Verify by hand." in captured.out
    assert "s3cret" not in captured.out + captured.err
    assert net.urls == [PYPI.format("demo-pkg"), OSV], "nothing was sent to the index"
    assert f"on PyPI: demo-pkg 1.1 — A demo {cli.PYPI_LABEL}" in captured.out
    assert captured.out.endswith(f"\n{REMINDER}\n")


# Covers F-0007.R8 and F-0007.R9 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_an_index_url_that_is_not_http_is_refused_without_a_request(
    net: _Network, capsys: pytest.CaptureFixture[str]
) -> None:
    net.answers[PYPI.format("demo-pkg")] = _document()
    assert main(["verify-dep", "--index", "file:///etc/passwd", "demo-pkg"]) == 2
    out = capsys.readouterr().out
    assert "the index URL must be http(s)" in out and "Verify by hand." in out
    assert net.urls == [PYPI.format("demo-pkg"), OSV]
    assert "passwd" not in out  # second audit, A3: no part of such a value is echoed


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
    # ADR-0028: the command refuses what is not a package name before any request (it used to
    # ask PyPI and answer 1); the lookup itself still quotes whatever it is given.
    assert main(["verify-dep", "../../simple?q=1"]) == 2
    assert net.urls == []
    assert cli.pypi_info("../../simple?q=1") is None
    assert net.urls == [quoted]
    with pytest.raises(ValueError, match="only https"):
        cli._http_json("http://pypi.org/pypi/demo-pkg/json")
    assert net.urls == [quoted]  # refused before any request


# Covers F-0007.R9 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_urlopen_is_called_only_by_the_two_verify_dep_lookups() -> None:
    """Since ADR-0028 nothing calls `urlopen` (it would bring urllib's default opener: every
    redirect followed, every ambient proxy). The two lookups, `_http_json` and `index_page`, reach
    the network through one chain: `_fetch` -> `_open` -> `_opener`, the only place an opener is
    built."""
    banned = ("urlopen", "build_opener", "install_opener")
    seam = ("OpenerDirector", "_opener", "_open", "_fetch")

    def mentions(tree: ast.AST, names: tuple[str, ...]) -> list[str]:
        return [
            name
            for node in ast.walk(tree)
            for name in names
            if (isinstance(node, ast.Attribute) and node.attr == name)
            or (isinstance(node, ast.Name) and node.id == name)
        ]

    package = Path(cli.__file__).parent
    found: dict[str, list[str]] = {}
    for file in sorted(package.rglob("*.py")):
        if "templates" in file.relative_to(package).parts:
            continue  # code of the generated projects, not of the CLI
        tree = ast.parse(file.read_text(encoding="utf-8"))
        assert mentions(tree, banned) == [], file
        if file.name != "cli.py":
            assert mentions(tree, seam) == [], file
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and mentions(node, seam):
                found[node.name] = mentions(node, seam)
        # None at module level or in a class.
        assert sum(len(names) for names in found.values()) == len(mentions(tree, seam))
    assert found == {
        "_opener": ["OpenerDirector", "OpenerDirector"],  # the annotation and the call
        "_open": ["_opener"],
        "_fetch": ["_open"],
        "_http_json": ["_fetch"],
        "index_page": ["_fetch"],
    }


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


# ------------------------------------------------------------------- ADR-0028
# verify-dep treats every answer as untrusted and sends credentials only to the index it was
# given (second audit before v0.4.1, findings V1 to V5, V7, V8 and D7).

SSO_LOGIN = (
    "<!DOCTYPE html><html><head><title>Sign in</title></head><body><h1>Sign in</h1>"
    '<form action="/sso"><input name="user"></form><a href="/help">Help</a> '
    '<a href="/downloads/">Download the client</a></body></html>'
)
PAGE = json.dumps({"files": [{"filename": "demo_pkg-1.0-py3-none-any.whl"}]})
SECRET_INDEX = INDEX.replace("https://", "https://alice:s3cret-t0ken@")


def _with_credentials(url: str) -> str:
    return url.replace("http://", "http://alice:s3cret-t0ken@")


# Covers F-0007.R6 and F-0007.R8 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        ("text/html; charset=utf-8", SSO_LOGIN),
        ("application/json", '{"error": "unauthorized"}'),
        ("application/json", '["1.0"]'),
        (cli.SIMPLE_JSON, '{"files": "none", "versions": null}'),
        ("text/html", '<a href="a">README.txt</a> <a href="b">demo-pkg-latest.tar.gz</a>'),
        (cli.SIMPLE_JSON, "[" * 100_000 + "]" * 100_000),
        ("text/html", ""),
        ("text/html", '<form action="/login"><a href="/app-1.2.zip">Download the app</a></form>'),
    ],
    ids=[
        "sso-login",
        "json-error",
        "json-list",
        "json-wrong-types",
        "html-without-files",
        "deep-json",
        "empty",
        "login-page-linking-an-archive",
    ],
)
def test_a_200_answer_that_is_not_a_package_page_does_not_confirm_the_name(
    net: _Network, capsys: pytest.CaptureFixture[str], content_type: str, body: str
) -> None:
    """Audit V1: an index behind SSO answers 200 with its login page for every path. A
    hallucinated name used to be reported as `exists: yes, versions: 0`, verdict 0."""
    net.answers[INDEX + "ghost-pkg/"] = _Response(body, content_type)
    net.answers[PYPI.format("ghost-pkg")] = _http_error(404)
    assert main(["verify-dep", "--index", INDEX, "ghost-pkg"]) == 2
    out = capsys.readouterr().out
    assert f"could not query index.example ({NOT_A_PAGE}). Verify by hand." in out
    assert "exists on" not in out
    assert out.endswith(f"\n{REMINDER}\n")


# Covers F-0007.R6 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize(
    "body",
    [
        '{"files": []}',
        '{"name": "empty-pkg", "versions": [], "files": []}',
        '{"files": [{"filename": "README.txt"}, {"no": "filename"}, "text"]}',
    ],
)
def test_a_package_page_without_files_exists_and_is_flagged_for_review(
    net: _Network, capsys: pytest.CaptureFixture[str], body: str
) -> None:
    """Audit V1: a real page with nothing to install is not a clean verdict 0."""
    net.answers[INDEX + "empty-pkg/"] = _Response(body, cli.SIMPLE_JSON)
    net.answers[PYPI.format("empty-pkg")] = _http_error(404)
    assert main(["verify-dep", "--index", INDEX, "empty-pkg"]) == 1
    out = capsys.readouterr().out
    assert "exists on index.example: yes, no files" in out
    assert "versions on" not in out


# Covers F-0007.R6 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_simple_page_tells_a_package_page_from_any_other_answer() -> None:
    files = '{"files": [{"filename": "p-1.0.tar.gz"}, {"filename": "p-2.0.tar.gz", "yanked": "x"}]}'
    assert cli.simple_page(files, cli.SIMPLE_JSON) == cli.SimplePage(
        ["1.0", "2.0"], frozenset({"2.0"})
    )
    listed = cli.simple_page('{"versions": ["1.0", 2, null, "1.0"]}', cli.SIMPLE_JSON)
    assert listed == cli.SimplePage(["1.0"], frozenset())  # only strings, each once
    assert cli.simple_page('{"files": []}', cli.SIMPLE_JSON) == cli.SimplePage([], frozenset())
    html = (
        '<a href="x" data-yanked="oops">p-2.0.tar.gz</a><a href="y">p-1.0-py3-none-any.whl</a>'
        '<a href="/">up</a>'
    )
    assert cli.simple_page(html, "text/html") == cli.SimplePage(["2.0", "1.0"], frozenset({"2.0"}))
    assert cli.simple_page('{"error": "unauthorized"}', cli.SIMPLE_JSON) is None
    assert cli.simple_page(SSO_LOGIN, "text/html") is None
    assert cli.simple_page("<![if !IE]><a>p-1.0.tar.gz</a>", "text/html") is not None
    # `parse_simple` keeps its contract (F-0007, data contracts): versions, [] when there are none.
    assert cli.parse_simple(SSO_LOGIN, "text/html") == []
    assert cli.parse_simple(html, "text/html") == ["2.0", "1.0"]


def _redirected(
    url: str, to: str, authorization: str | None = BASIC
) -> urllib.request.Request | None:
    """What the redirect rule of the CLI answers to `url` being redirected to `to`."""
    request = urllib.request.Request(url)  # noqa: S310 - built, never opened
    if authorization is not None:
        request.add_unredirected_header("Authorization", authorization)
    return cli._Redirects().redirect_request(
        request, io.BytesIO(), 302, "Found", http.client.HTTPMessage(), to
    )


# Covers F-0007.R6 and F-0007.R7 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize(
    ("code", "location"),
    [
        (301, "http://127.0.0.1:{port}/simple/demo-pkg/"),
        (302, "http://127.0.0.1:{port}/simple/demo-pkg/"),
        (303, "http://127.0.0.1:{port}/simple/demo-pkg/"),
        (307, "http://127.0.0.1:{port}/simple/demo-pkg/"),
        (308, "http://127.0.0.1:{port}/simple/demo-pkg/"),
        (302, "//127.0.0.1:{port}/simple/demo-pkg/"),
    ],
    ids=["301", "302", "303", "307", "308", "scheme-relative"],
)
def test_a_redirect_away_from_the_index_is_refused_when_credentials_are_set(
    serve: Callable[[Respond], _Server],
    capsys: pytest.CaptureFixture[str],
    code: int,
    location: str,
) -> None:
    """Audit V2, then the second audit (A1). urllib copies the headers of a request to wherever
    a redirect points; the first fix refused such a redirect outright, and real indexes redirect
    by design (GitLab forwards every package it does not hold to pypi.org). The rule since: the
    redirect is followed, and the target receives the request without the credentials. The name
    of this test is older than the rule (renaming a test is the human's call)."""
    target = serve(_answers(PAGE))
    index = serve(_answers("", status=code, location=location.format(port=target.port)))
    assert main(["verify-dep", "--index", _with_credentials(index.url), "demo-pkg"]) == 0
    captured = capsys.readouterr()
    assert index.seen == [("/simple/demo-pkg/", BASIC)]
    assert target.seen == [("/simple/demo-pkg/", None)]  # the request, and no Authorization
    assert not any(secret in target.raw[0] for secret in ("s3cret-t0ken", BASIC.split()[1]))
    # The user is told whose page was read.
    assert (
        f"   exists on 127.0.0.1:{index.port}: yes (answered by 127.0.0.1:{target.port} after a "
        "redirect; the credentials were not sent there)\n"
    ) in captured.out
    assert "s3cret-t0ken" not in captured.out + captured.err


# Covers F-0007.R7 and F-0007.R9 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_a_redirect_away_from_the_index_drops_the_credentials_for_good() -> None:
    """A1, on the rule itself (https cannot be served by a local test server)."""
    away = _redirected(INDEX + "demo-pkg/", "https://pypi.org/simple/demo-pkg/")
    assert away is not None and away.full_url == "https://pypi.org/simple/demo-pkg/"
    assert not away.has_header("Authorization") and away.unredirected_hdrs == {}
    # Not even when a later hop comes back to the index.
    back = cli._Redirects().redirect_request(
        away, io.BytesIO(), 302, "Found", http.client.HTTPMessage(), INDEX + "demo-pkg/"
    )
    assert back is not None and back.full_url == INDEX + "demo-pkg/"
    assert not back.has_header("Authorization") and back.unredirected_hdrs == {}
    # Still refused, with or without credentials, and in fixed text (the target is untrusted):
    # https to plain http, another scheme, and a target that cannot be read as scheme://host.
    for to in (
        "http://index.example/simple/demo-pkg/",
        "http://evil.example/simple/demo-pkg/",
        "ftp://index.example/simple/demo-pkg/",
        "https://[not-an-address/simple/",
        "https://index.example@evil.example/simple/demo-pkg/",
        "https://index.example\\@evil.example/simple/demo-pkg/",
        "https://index.example:8443%40evil.example/simple/",
    ):
        for authorization in (BASIC, None):
            with pytest.raises(ValueError, match=r"not followed$") as refused:
                _redirected(INDEX + "demo-pkg/", to, authorization)
            assert "example" not in str(refused.value)


# Covers F-0007.R7 and F-0007.R9 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_a_redirect_is_followed_inside_the_index_or_when_there_is_nothing_to_leak() -> None:
    same = _redirected(INDEX + "Demo_Pkg/", "https://INDEX.example:443/simple/demo-pkg/")
    assert same is not None and same.full_url == "https://INDEX.example:443/simple/demo-pkg/"
    # The credentials go on, still as a header no later redirect copies.
    assert same.unredirected_hdrs == {"Authorization": BASIC}
    assert "Authorization" not in same.headers
    other = _redirected(INDEX + "demo-pkg/", "https://mirror.example/demo-pkg/", None)
    assert other is not None and other.full_url == "https://mirror.example/demo-pkg/"
    assert not other.has_header("Authorization")
    # https never becomes plain http or another scheme, credentials or not (PyPI, OSV: R9).
    for to in ("http://pypi.org/pypi/demo-pkg/json", "ftp://pypi.org/pypi/demo-pkg/json"):
        with pytest.raises(ValueError, match="not followed"):
            _redirected("https://pypi.org/pypi/demo-pkg/json", to, None)


# Covers F-0007.R7 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_credentials_are_an_unredirected_header_and_only_a_loopback_index_skips_the_proxies(
    net: _Network,
) -> None:
    local = "http://alice:s3cret-t0ken@127.0.0.1:3141/simple/"
    for index in (SECRET_INDEX, local):
        net.answers[cli.sanitize_index(index) + "demo-pkg/"] = '{"versions": ["1.0"]}'
    net.answers[PYPI.format("demo-pkg")] = _document()
    assert main(["verify-dep", "--index", SECRET_INDEX, "demo-pkg"]) == 0
    assert main(["verify-dep", "--index", local, "demo-pkg"]) == 0
    for request in (net.sent[0], net.sent[3]):
        assert request.unredirected_hdrs == {"Authorization": BASIC}
        assert "Authorization" not in request.headers
    # index, PyPI, OSV, twice: only the index on this machine is reached without a proxy.
    assert net.direct == [False, False, False, True, False, False]
    # D7: the index request carries the per-operation timeout of R8 like the other two.
    assert net.timeouts == [cli.HTTP_TIMEOUT] * 6 and cli.HTTP_TIMEOUT == 15


# Covers F-0007.R7 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_a_redirect_to_another_port_never_receives_the_credentials(
    serve: Callable[[Respond], _Server], capsys: pytest.CaptureFixture[str]
) -> None:
    """Audit V2 and D7, through the real urllib, on the two chains of the second audit (A1).
    Away and back: once the credentials are dropped they are not put back, not even for the
    index itself. Inside the index and then away: they stop where the index stops."""
    hops: dict[str, str] = {}

    def index_then_back(handler: BaseHTTPRequestHandler, stop: threading.Event) -> None:
        if handler.path == "/simple/demo-pkg/":
            _answers("", status=302, location=hops["away"])(handler, stop)
        else:
            _answers(PAGE)(handler, stop)

    index = serve(index_then_back)
    elsewhere = serve(_answers("", status=307, location=index.url + "final/"))
    hops["away"] = elsewhere.url + "x/"
    assert main(["verify-dep", "--index", _with_credentials(index.url), "demo-pkg"]) == 0
    captured = capsys.readouterr()
    assert f"versions on 127.0.0.1:{index.port}: 1 (latest 1.0)" in captured.out
    assert index.seen == [("/simple/demo-pkg/", BASIC), ("/simple/final/", None)]
    assert elsewhere.seen == [("/simple/x/", None)]
    assert "s3cret-t0ken" not in captured.out + captured.err
    assert "alice" not in captured.out + captured.err

    def inside_then_away(handler: BaseHTTPRequestHandler, stop: threading.Event) -> None:
        to = "/pkgs/demo-pkg/" if handler.path == "/simple/demo-pkg/" else hops["out"]
        _answers("", status=301, location=to)(handler, stop)

    second = serve(inside_then_away)
    target = serve(_answers(PAGE))
    hops["out"] = target.url + "demo-pkg/"
    assert main(["verify-dep", "--index", _with_credentials(second.url), "demo-pkg"]) == 0
    assert second.seen == [("/simple/demo-pkg/", BASIC), ("/pkgs/demo-pkg/", BASIC)]
    assert target.seen == [("/simple/demo-pkg/", None)]
    assert f"(answered by 127.0.0.1:{target.port} after a redirect" in capsys.readouterr().out


# Covers F-0007.R6 and F-0007.R7 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_a_redirect_inside_the_index_keeps_the_credentials_and_without_them_any_is_followed(
    serve: Callable[[Respond], _Server], capsys: pytest.CaptureFixture[str]
) -> None:
    def normalises(handler: BaseHTTPRequestHandler, stop: threading.Event) -> None:
        if handler.path == "/simple/demo-pkg/":
            _answers("", status=301, location="/simple/moved/demo-pkg/")(handler, stop)
        else:
            _answers(PAGE)(handler, stop)

    index = serve(normalises)
    assert main(["verify-dep", "--index", _with_credentials(index.url), "demo-pkg"]) == 0
    assert index.seen == [("/simple/demo-pkg/", BASIC), ("/simple/moved/demo-pkg/", BASIC)]
    assert f"versions on 127.0.0.1:{index.port}: 1 (latest 1.0)" in capsys.readouterr().out

    mirror = serve(_answers(PAGE))
    front = serve(_answers("", status=302, location=mirror.url + "demo-pkg/"))
    assert main(["verify-dep", "--index", front.url, "demo-pkg"]) == 0
    assert mirror.seen == [("/simple/demo-pkg/", None)]
    assert f"versions on 127.0.0.1:{front.port}: 1 (latest 1.0)" in capsys.readouterr().out


# Covers F-0007.R7 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_a_loopback_index_is_never_reached_through_the_proxy_of_the_environment(
    serve: Callable[[Respond], _Server], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit V3 and D7: "the credentials never leave this machine" was false with HTTP_PROXY
    set, the proxy received the request and its Authorization header."""
    proxy = serve(_answers("", status=404))
    index = serve(_answers(PAGE))
    for key in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(key, f"http://127.0.0.1:{proxy.port}")
    assert main(["verify-dep", "--index", _with_credentials(index.url), "demo-pkg"]) == 0
    assert proxy.seen == []
    assert index.seen == [("/simple/demo-pkg/", BASIC)]


# Covers F-0007.R6 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_an_index_elsewhere_is_still_reached_through_the_proxy_of_the_environment(
    serve: Callable[[Respond], _Server], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of V3: a company proxy keeps working for everything that is not loopback.
    `index.invalid` is never resolved: the request for it goes to the local proxy."""
    proxy = serve(_answers(PAGE))
    for key in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(key, f"http://127.0.0.1:{proxy.port}")
    assert main(["verify-dep", "--index", "http://index.invalid/simple/", "demo-pkg"]) == 0
    assert proxy.seen == [("http://index.invalid/simple/demo-pkg/", None)]


# Covers F-0007.R7 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_the_credentials_of_a_proxy_do_not_follow_a_redirect_past_the_proxy(
    serve: Callable[[Respond], _Server], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second audit (its `a3_misc.py`): urllib writes `Proxy-Authorization` as an ordinary
    header of the request, and copies it to a redirect target that `NO_PROXY` reaches directly."""
    target = serve(_answers(PAGE))
    proxy = serve(_answers("", status=302, location=target.url + "demo-pkg/"))
    for key in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(key, f"http://proxyuser:pr0xy-pw@127.0.0.1:{proxy.port}")
    for key in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(key, "127.0.0.1")
    assert main(["verify-dep", "--index", "http://index.invalid/simple/", "demo-pkg"]) == 0
    sent = base64.b64encode(b"proxyuser:pr0xy-pw").decode()
    assert len(proxy.raw) == 1 and sent in proxy.raw[0]  # the proxy is still given its own
    assert target.seen == [("/simple/demo-pkg/", None)]
    assert sent not in target.raw[0] and "proxy-authorization" not in target.raw[0].lower()


def _drips(handler: BaseHTTPRequestHandler, stop: threading.Event) -> None:
    """Headers at once, then a few bytes of body every 50 ms for as long as the test lives."""
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html")
    handler.end_headers()
    while not stop.wait(0.05):
        handler.wfile.write(b"<!-- padding -->\n")
        handler.wfile.flush()


def _never_answers(handler: BaseHTTPRequestHandler, stop: threading.Event) -> None:
    stop.wait(30)


# Covers F-0007.R8 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize("respond", [_drips, _never_answers], ids=["dripping-body", "no-headers"])
def test_a_server_that_never_finishes_is_cut_by_the_deadline(
    serve: Callable[[Respond], _Server],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    respond: Respond,
) -> None:
    """Audit V5 and D7: the 15 s timeout is per socket read, so a server that keeps sending a
    little held the command for as long as it liked."""
    monkeypatch.setattr(cli, "HTTP_DEADLINE", 0.5)
    index = serve(respond)
    start = time.monotonic()
    assert main(["verify-dep", "--index", index.url, "demo-pkg"]) == 2
    elapsed = time.monotonic() - start
    out = capsys.readouterr().out
    reason = "no complete answer within 0.5 s"
    assert f"could not query 127.0.0.1:{index.port} ({reason}). Verify by hand." in out
    assert elapsed < 3, "far below the 15 s of one socket read"
    assert out.endswith(f"\n{REMINDER}\n")


# Covers F-0007.R8 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_a_body_larger_than_the_cap_is_refused_and_one_cut_short_is_not_a_page(
    serve: Callable[[Respond], _Server],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Audit V5 and D7, through the real urllib."""
    files = [{"filename": f"demo_pkg-1.{n}-py3-none-any.whl"} for n in range(100)]
    big = json.dumps({"files": files})
    index = serve(_answers(big))
    host = f"127.0.0.1:{index.port}"
    monkeypatch.setattr(cli, "MAX_BODY", len(big) - 1)
    assert main(["verify-dep", "--index", index.url, "demo-pkg"]) == 2
    reason = f"answer larger than {len(big) - 1} bytes"
    assert f"could not query {host} ({reason}). Verify by hand." in capsys.readouterr().out
    monkeypatch.setattr(cli, "MAX_BODY", len(big))  # exactly at the cap: read
    assert main(["verify-dep", "--index", index.url, "demo-pkg"]) == 0
    assert f"versions on {host}: 100 (latest 1.99)" in capsys.readouterr().out

    def hangs_up(handler: BaseHTTPRequestHandler, stop: threading.Event) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html")
        handler.send_header("Content-Length", "5000")
        handler.end_headers()
        handler.wfile.write(b'<a href="x">demo_pkg-1.0.tar.gz</a>')

    short = serve(hangs_up)
    assert main(["verify-dep", "--index", short.url, "demo-pkg"]) == 2
    out = capsys.readouterr().out
    assert f"could not query 127.0.0.1:{short.port} (IncompleteRead(" in out
    assert "exists on" not in out


# Covers F-0007.R4 and F-0007.R8 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_the_cap_applies_to_pypi_and_to_osv(
    net: _Network, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    net.answers[PYPI.format("demo-pkg")] = _document()
    monkeypatch.setattr(cli, "MAX_BODY", 64)
    assert main(["verify-dep", "demo-pkg"]) == 2
    out = capsys.readouterr().out
    assert "demo-pkg: could not query PyPI (answer larger than 64 bytes). Verify by hand." in out
    monkeypatch.setattr(cli, "MAX_BODY", len(_document()))
    net.answers[OSV] = json.dumps({"vulns": [{"id": f"GHSA-{n}"} for n in range(100)]})
    assert main(["verify-dep", "demo-pkg"]) == 0
    assert "(OSV not reachable: vulnerability check skipped)" in capsys.readouterr().out


# Covers F-0007.R9 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_extras_and_a_version_specifier_are_split_off_and_the_bare_name_is_verified(
    net: _Network, capsys: pytest.CaptureFixture[str]
) -> None:
    """Audit V4: `requests[socks]>=2` went into the URL of an authenticated request as typed."""
    net.answers[INDEX + "demo-pkg/"] = '{"versions": ["1.0"]}'
    net.answers[PYPI.format("Demo_Pkg")] = _document()
    requirement = "Demo_Pkg[socks, http2] >=1.0,<2 ; python_version < '3.13'"
    assert main(["verify-dep", "--index", INDEX, requirement]) == 0
    out = capsys.readouterr().out
    assert net.urls == [INDEX + "demo-pkg/", PYPI.format("Demo_Pkg"), OSV]
    assert "note: only the name 'Demo_Pkg' is verified" in out
    assert main(["verify-dep", "--index", INDEX, "Demo_Pkg"]) == 0
    assert "note:" not in capsys.readouterr().out
    # Second audit: every character a version can hold (`_`, and anything after `===`).
    for specifier in ("===1.0-anything_here", ">=1.0_post1", "==1!2.0+local.1", " (>=1.0, <2)"):
        assert main(["verify-dep", "--index", INDEX, "Demo_Pkg" + specifier]) == 0, specifier
        assert "note: only the name 'Demo_Pkg' is verified" in capsys.readouterr().out
    assert set(net.urls) == {INDEX + "demo-pkg/", PYPI.format("Demo_Pkg"), OSV}


# Covers F-0007.R9 and F-0007.R10 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize(
    "argument",
    [
        "https://alice:s3cret-t0ken@files.example/demo_pkg-1.0-py3-none-any.whl",
        "git+https://github.com/example/demo-pkg.git@main",
        "demo-pkg @ https://alice:s3cret-t0ken@files.example/demo_pkg-1.0.tar.gz",
        "./dist/demo_pkg-1.0-py3-none-any.whl",
        "../../admin/users?x=1#",
        "C:\\wheels\\demo_pkg-1.0-py3-none-any.whl",
        "demo pkg",
        "demo-pkg\x1b[2J",
        "demo-pkg\n",
        "demo-pkg; \x1b[2J",
        "_private",
        "",
    ],
)
def test_an_argument_that_is_not_a_package_name_is_refused_and_the_others_go_on(
    net: _Network, capsys: pytest.CaptureFixture[str], argument: str
) -> None:
    """Audit V4: a URL, a path or a VCS reference is a usage error for that argument, before any
    request; it is not echoed (it can carry a token), and the names after it are verified."""
    net.answers[PYPI.format("demo-pkg")] = _document()
    assert main(["verify-dep", argument, "demo-pkg"]) == 2
    captured = capsys.readouterr()
    assert "Error: argument 1 of verify-dep is not a package name" in captured.err
    assert "s3cret-t0ken" not in captured.out + captured.err
    assert "\x1b" not in captured.out + captured.err
    assert net.urls == [PYPI.format("demo-pkg"), OSV]
    assert "demo-pkg 1.1 — A demo" in captured.out
    assert captured.out.endswith(f"\n{REMINDER}\n")


# Covers F-0007.R9 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_the_name_is_quoted_into_one_path_segment_of_the_index_url(net: _Network) -> None:
    quoted = INDEX + "-%2F-%2Fadmin%2Fusers%3Fx%3D1%23/"
    net.answers[quoted] = _http_error(404)
    assert cli.index_versions(SECRET_INDEX, "../../admin/users?x=1#") is None
    assert net.urls == [quoted]


# Covers F-0007.R6 and F-0007.R8 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_an_unknown_charset_is_read_as_utf8_instead_of_a_traceback(
    net: _Network, capsys: pytest.CaptureFixture[str]
) -> None:
    """Audit V7: `charset=no-such-codec` raised LookupError out of the command."""
    body = '<a href="x">demo_pkg-1.0.tar.gz</a> é'
    for charset in ("no-such-codec", "hex", "idna"):
        net.answers[INDEX + "demo-pkg/"] = _Response(body, f"text/html; charset={charset}")
        net.answers[PYPI.format("demo-pkg")] = _http_error(404)
        assert main(["verify-dep", "--index", INDEX, "demo-pkg"]) == 0
        assert "versions on index.example: 1 (latest 1.0)" in capsys.readouterr().out


# Covers F-0007.R8 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize(
    "answer",
    [
        http.client.IncompleteRead(b"<a"),
        http.client.BadStatusLine("garbage"),
        http.client.LineTooLong("header line"),
        _http_error(503),
    ],
    ids=["incomplete-read", "bad-status-line", "line-too-long", "http-503"],
)
def test_a_protocol_error_from_the_index_is_verdict_2_and_pypi_is_still_measured(
    net: _Network, capsys: pytest.CaptureFixture[str], answer: Answer
) -> None:
    """Audit V7: `http.client.HTTPException` is not an OSError; it must not escape."""
    net.answers[INDEX + "demo-pkg/"] = answer
    net.answers[PYPI.format("demo-pkg")] = _document()
    assert main(["verify-dep", "--index", INDEX, "demo-pkg"]) == 2
    out = capsys.readouterr().out
    assert "could not query index.example (" in out and "Verify by hand." in out
    assert f"on PyPI: demo-pkg 1.1 — A demo {cli.PYPI_LABEL}" in out
    assert out.endswith(f"\n{REMINDER}\n")


# Covers F-0007.R6 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize(
    ("versions", "latest"),
    [
        (["1.0", "1.0.post1"], "1.0.post1"),
        (["1.0", "1.0-1"], "1.0-1"),  # an implicit post-release
        (["1.0rc9", "1.0rc10"], "1.0rc10"),
        (["2.0", "1!0.1"], "1!0.1"),
        (["1.0", "1.0+local"], "1.0+local"),
        (["1.0+abc", "1.0+5"], "1.0+5"),
        (["1.0.dev1", "1.0a1"], "1.0a1"),
        (["0.9", "1.0.dev1"], "1.0.dev1"),
        (["1.0.dev5", "1.0a10", "1.0b2", "1.0rc1"], "1.0rc1"),
        (["1.0.dev1", "1.0a1", "1.0rc1", "1.0"], "1.0"),
        (["1.0.post1.dev1", "1.0.post1"], "1.0.post1"),
        (["1.0", "1.0.post1.dev1"], "1.0.post1.dev1"),
        (["1.0a1.dev1", "1.0a1"], "1.0a1"),
        (["1.0A1", "1.0.BETA.2"], "1.0.BETA.2"),
        (["1.2", "1.9", "1.10"], "1.10"),
        (["0.1", "v1"], "v1"),
        (["banana", "9" * 5000, "2024.1"], "2024.1"),
    ],
)
def test_latest_follows_the_order_of_pep_440(versions: list[str], latest: str) -> None:
    """Audit V8: `1.0` beat `1.0.post1`, `rc9` beat `rc10`, and an epoch was not read."""
    assert cli.latest_version(versions) == latest
    assert cli.latest_version(versions[::-1]) == latest


# Covers F-0007.R6 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_a_yanked_release_is_not_the_latest_unless_every_file_is_yanked(
    net: _Network, capsys: pytest.CaptureFixture[str]
) -> None:
    """Audit V8: PEP 691 `yanked` and PEP 503 `data-yanked`; a release is yanked when every one
    of its files is."""
    net.answers[PYPI.format("demo-pkg")] = _http_error(404)
    files = [
        {"filename": "demo_pkg-9.9-py3-none-any.whl", "yanked": "malware"},
        {"filename": "demo-pkg-9.9.tar.gz", "yanked": True},
        {"filename": "demo-pkg-2.0.tar.gz", "yanked": True},
        {"filename": "demo_pkg-2.0-py3-none-any.whl", "yanked": False},
        {"filename": "demo-pkg-1.0.tar.gz"},
    ]
    net.answers[INDEX + "demo-pkg/"] = _Response(json.dumps({"files": files}), cli.SIMPLE_JSON)
    assert main(["verify-dep", "--index", INDEX, "demo-pkg"]) == 0
    assert "versions on index.example: 3 (latest 2.0)" in capsys.readouterr().out

    html = '<a href="a" data-yanked="bad">demo-pkg-9.9.tar.gz</a><a href="b">demo-pkg-1.0.zip</a>'
    net.answers[INDEX + "demo-pkg/"] = _Response(html, "text/html")
    assert main(["verify-dep", "--index", INDEX, "demo-pkg"]) == 0
    assert "versions on index.example: 2 (latest 1.0)" in capsys.readouterr().out

    net.answers[INDEX + "demo-pkg/"] = _Response(
        '<a href="a" data-yanked>demo-pkg-9.9.tar.gz</a>', "text/html"
    )
    assert main(["verify-dep", "--index", INDEX, "demo-pkg"]) == 0
    out = capsys.readouterr().out
    assert "versions on index.example: 1 (latest 9.9, every file yanked)" in out


# Covers F-0007.R2 and F-0007.R6 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_text_from_the_network_is_printed_without_control_characters_and_bounded(
    net: _Network, capsys: pytest.CaptureFixture[str]
) -> None:
    """Audit V8: a summary could move the cursor up, erase the warnings and write
    `audited: safe to add`; a project URL could add a line that looks like one of ours."""
    net.answers[INDEX + "demo-pkg/"] = json.dumps({"versions": ["1.0\x1b[2J\x07"]})
    net.answers[PYPI.format("demo-pkg")] = _document(
        version="1.1\u202e\x9b0m",
        summary="A demo\x1b[1A\x1b[2K\r   \u2705 audited: safe to add" + " padding" * 2000,
        project_urls={
            "Source": "https://example.org/x\n   first release: 2010-01-01 (5000 days ago)"
        },
    )
    main(["verify-dep", "--index", INDEX, "demo-pkg"])
    out = capsys.readouterr().out
    assert not re.search(r"[\x00-\x09\x0b-\x1f\x7f-\x9f\u2028-\u202e\u2066-\u2069]", out)
    lines = out.splitlines()
    assert not any(line.lstrip().startswith("first release: 2010") for line in lines)
    assert sum("first release:" in line for line in lines) == 2  # ours, and the one neutralised
    assert max(len(line) for line in lines) < 400
    assert "versions on index.example: 1 (latest 1.0 [2J)" in out
    # The function every network string goes through.
    assert cli._show("a\x1b[1Ab\r\nc\x00\ud800d") == "a [1Ab c d"
    assert cli._show("x" * 500) == "x" * 199 + "…"
    assert cli._show({"not": "text"}) == "{'not': 'text'}"

    net.answers[PYPI.format("demo-pkg")] = _document(name="demo-pkg\x1b[2K\nevil")
    net.answers[OSV] = urllib.error.URLError("unreachable\x1b[2J")
    main(["verify-dep", "demo-pkg"])
    out = capsys.readouterr().out
    assert "\x1b" not in out and "the canonical name is 'demo-pkg [2K evil'" in out
    net.answers[PYPI.format("demo-pkg")] = urllib.error.HTTPError(
        "https://unused.invalid/", 500, "down\x1b[2J\nnow", email.message.Message(), None
    )
    assert main(["verify-dep", "demo-pkg"]) == 2
    assert "could not query PyPI (HTTP Error 500: down [2J now)" in capsys.readouterr().out


# Covers F-0007.R7 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_the_help_says_where_the_credentials_of_an_index_go(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Audit V8: `sanitize_index` cannot know that a path segment is a token, so it does not
    guess; the help says which part of the URL is treated as a secret."""
    with pytest.raises(SystemExit):
        main(["verify-dep", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "https://USER:TOKEN@HOST/simple/" in text
    assert "A token written in the path is printed with the path" in text
    # What the help promises: the user info is removed, the path is printed as it is.
    shown = cli.sanitize_index("https://alice:s3cret-t0ken@index.example/TOKEN123/simple/?t=abc")
    assert shown == "https://index.example/TOKEN123/simple/"


# ------------------------------------------------- second audit of ADR-0028 (A2, A3, A4)
# The two listings below are reconstructed from the listing format of the two servers (nginx
# cuts the text of a link at 50 characters, Apache's FancyIndexing at 23, both ending in `..>`);
# they were not captured from a running nginx or Apache.
NGINX_AUTOINDEX = """<html>
<head><title>Index of /simple/mycompany-internal-toolkit/</title></head>
<body>
<h1>Index of /simple/mycompany-internal-toolkit/</h1><hr><pre><a href="../">../</a>
<a href="mycompany_internal_toolkit-1.4.0-cp312-cp312-manylinux_2_17_x86_64.whl">mycompany_internal_toolkit-1.4.0-cp312-cp312-ma..&gt;</a> 12-Nov-2026 10:01    48211
<a href="mycompany_internal_toolkit-1.4.0-cp312-cp312-win_amd64.whl">mycompany_internal_toolkit-1.4.0-cp312-cp312-wi..&gt;</a> 12-Nov-2026 10:01    48211
</pre><hr></body>
</html>"""  # noqa: E501
APACHE_AUTOINDEX = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html>
 <head>
  <title>Index of /simple/mycompany-toolkit</title>
 </head>
 <body>
<h1>Index of /simple/mycompany-toolkit</h1>
<pre><img src="/icons/blank.gif" alt="Icon "> <a href="?C=N;O=D">Name</a>                    <a href="?C=M;O=A">Last modified</a>      <a href="?C=S;O=A">Size</a>  <a href="?C=D;O=A">Description</a><hr><img src="/icons/back.gif" alt="[PARENTDIR]"> <a href="/simple/">Parent Directory</a>                             -
<img src="/icons/unknown.gif" alt="[   ]"> <a href="mycompany_toolkit-1.4.0-py3-none-any.whl">mycompany_toolkit-1.4.0..&gt;</a> 2026-11-12 10:01   48K
<img src="/icons/compressed.gif" alt="[   ]"> <a href="mycompany_toolkit-1.4.0.tar.gz">mycompany_toolkit-1.4.0..&gt;</a> 2026-11-12 10:01   41K
<img src="/icons/compressed.gif" alt="[   ]"> <a href="mycompany_toolkit-1.3.2.tar.gz">mycompany_toolkit-1.3.2..&gt;</a> 2026-10-02 09:30   40K
<hr></pre>
</body></html>"""  # noqa: E501
# What `python -m http.server` writes (`http.server.SimpleHTTPRequestHandler.list_directory`):
# the href is percent-encoded, the text is the name.
HTTP_SERVER_LISTING = """<!DOCTYPE HTML>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Directory listing for /simple/demo-pkg/</title>
</head>
<body>
<h1>Directory listing for /simple/demo-pkg/</h1>
<hr>
<ul>
<li><a href="demo_pkg-1.0%2Blocal.1-py3-none-any.whl">demo_pkg-1.0+local.1-py3-none-any.whl</a></li>
<li><a href="demo_pkg-0.9.tar.gz">demo_pkg-0.9.tar.gz</a></li>
</ul>
<hr>
</body>
</html>"""


# Covers F-0007.R6 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize(
    ("name", "body", "versions"),
    [
        ("mycompany-internal-toolkit", NGINX_AUTOINDEX, "1 (latest 1.4.0)"),
        ("mycompany-toolkit", APACHE_AUTOINDEX, "2 (latest 1.4.0)"),
        ("demo-pkg", HTTP_SERVER_LISTING, "2 (latest 1.0+local.1)"),
    ],
    ids=["nginx-autoindex", "apache-autoindex", "python-http-server"],
)
def test_the_listing_of_a_plain_web_server_is_a_package_page(
    net: _Network, capsys: pytest.CaptureFixture[str], name: str, body: str, versions: str
) -> None:
    """Second audit, A2: nginx and Apache cut the text of a link (`mycompany_toolkit-1.4.0..>`),
    so no anchor text was a file name and a project served by a plain web server was "not a
    package page" (exit 2). The file name is in the href."""
    net.answers[INDEX + f"{name}/"] = _Response(body, "text/html")
    net.answers[PYPI.format(name)] = _http_error(404)
    assert main(["verify-dep", "--index", INDEX, name]) == 0
    out = capsys.readouterr().out
    assert "exists on index.example: yes" in out
    assert f"versions on index.example: {versions}" in out


# Covers F-0007.R6 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_the_file_name_comes_from_the_href_only_when_the_text_is_that_name_cut_short() -> None:
    """A2 without reopening V1: a link of a login page that points at an archive
    (`<a href="/app-1.2.zip">Download the app</a>`) must not make that page a package page."""
    cut = '<a href="/f/demo_pkg-1.4.0-py3-none-any.whl?get=1#sha256=abc">demo_pkg-1.4.0..&gt;</a>'
    assert cli.simple_page(cut, "text/html") == cli.SimplePage(["1.4.0"], frozenset())
    quoted = '<a href="demo_pkg-2.0%2Blocal.1.tar.gz" data-yanked="x">demo_pkg-2.0+loc..&gt;</a>'
    assert cli.simple_page(quoted, "text/html") == cli.SimplePage(
        ["2.0+local.1"], frozenset({"2.0+local.1"})
    )
    # A text that is a file name is the file name (PEP 503), whatever the href says.
    both = '<a href="https://files.example/other-9.9.tar.gz">demo_pkg-1.0.tar.gz</a>'
    assert cli.simple_page(both, "text/html") == cli.SimplePage(["1.0"], frozenset())
    for text in ("Download the app", "app", "app-1.2", "app-1.2...", "..&gt;", "x..&gt;", ""):
        assert cli.simple_page(f'<a href="/app-1.2.zip">{text}</a>', "text/html") is None, text
    for href in ("http://[bad/app-1.2.zip", "", "?C=N;O=D", "app-1.2.zip/"):
        assert cli.simple_page(f'<a href="{href}">app-1..&gt;</a>', "text/html") is None, href
    assert cli.simple_page("<a>app-1..&gt;</a>", "text/html") is None


MARKER = "M4RK3R-pw"
MUST_BE_HTTP = (
    "the index URL must be http(s): it has to start with http:// or https:// "
    "(value not shown: it may hold credentials)"
)
CANNOT_BE_PARSED = "the index URL cannot be parsed (check the host and the port)"


# Covers F-0007.R7 and F-0007.R8 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize("how", ["flag", "env"])
@pytest.mark.parametrize(
    ("value", "refusal"),
    [
        (f"alice:{MARKER}@index.example/simple/", MUST_BE_HTTP),
        (f"//alice:{MARKER}@index.example/simple/", MUST_BE_HTTP),
        (f"https:/alice:{MARKER}@index.example/simple/", MUST_BE_HTTP),
        (f"https//alice:{MARKER}@index.example/simple/", MUST_BE_HTTP),
        (f"https:alice:{MARKER}@index.example/simple/", MUST_BE_HTTP),
        (f"https:///alice:{MARKER}@index.example/simple/", MUST_BE_HTTP),
        (f"https:\\\\alice:{MARKER}@index.example\\simple\\", MUST_BE_HTTP),
        (f"https://alice:{MARKER}@/simple/", MUST_BE_HTTP),
        (f"htps://alice:{MARKER}@index.example/simple/", MUST_BE_HTTP),
        (f"ftp://alice:{MARKER}@index.example/simple/", MUST_BE_HTTP),
        (f"file:///home/alice/{MARKER}/simple/", MUST_BE_HTTP),
        (f"https://alice:{MARKER}@index.example:port/simple/", CANNOT_BE_PARSED),
        (f"https://alice:{MARKER}@[::1/simple/", CANNOT_BE_PARSED),
        (f"https://alice:{MARKER}/x@index.example/simple/", CANNOT_BE_PARSED),
        (f"https://alice:1234/{MARKER}@index.example/simple/", None),
        (f"https://alice/{MARKER}@index.example/simple/", None),
        (f"https://index.example/org/alice:{MARKER}@feed/simple/", None),
        (f"HTTPS://alice:{MARKER}@index.example/simple/?token={MARKER}#{MARKER}", None),
        (f" https://alice:{MARKER}@index.example/simple/", None),
        (f"https://{MARKER}@index.example/simple/", None),
        (f"https://alice:{MARKER}@index.example/sim ple/", None),
        (f"http://alice:{MARKER}@index.example/simple/", None),
    ],
)
def test_no_shape_of_an_index_value_prints_its_password(
    net: _Network,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str,
    refusal: str | None,
    how: str,
) -> None:
    """Second audit, A3: `alice:<pw>@host/simple/` (the scheme forgotten) was printed whole, as
    `index: alice://<pw>@host/simple/`. A value that does not start with http(s)://host is not
    echoed at all; one that does is printed without its user info, and without what stands
    before an `@` in its path (a password whose `/` was not percent-encoded ends up there).
    The worst error a request can raise is assumed: one that quotes the URL that was asked."""
    net.answers[PYPI.format("demo-pkg")] = _document()

    def quotes_the_request(request: urllib.request.Request) -> BaseException:
        text = f"URL can't contain control characters. {request.selector!r} in {request.full_url}"
        return http.client.InvalidURL(text)

    net.fallback = quotes_the_request
    if how == "env":
        monkeypatch.setenv("UV_INDEX_URL", value)
    argv = ["verify-dep", "demo-pkg"] + (["--index", value] if how == "flag" else [])
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert MARKER not in captured.out + captured.err
    assert base64.b64encode(f"alice:{MARKER}".encode()).decode() not in captured.out + captured.err
    if refusal is not None:
        assert f"could not query the index ({refusal}). Verify by hand." in captured.out
        assert "index.example" not in captured.out and "alice" not in captured.out
        assert net.urls == [PYPI.format("demo-pkg"), OSV], "nothing was sent to the index"
    assert captured.out.endswith(f"\n{REMINDER}\n")


# Covers F-0007.R8 (specs/0007-dependencies-verified-before-they-are-added.md).
def test_ctrl_c_is_not_held_until_the_deadline(
    serve: Callable[[Respond], _Server], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second audit, A4: the main thread waited for the request with one `join(deadline)`,
    which Windows does not interrupt: Ctrl+C took effect when the deadline passed (60 s)."""
    monkeypatch.setattr(cli, "HTTP_DEADLINE", 8.0)
    index = serve(_never_answers)
    interrupt = threading.Timer(0.3, _thread.interrupt_main)
    start = time.monotonic()
    interrupt.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            main(["verify-dep", "--index", index.url, "demo-pkg"])
    finally:
        interrupt.cancel()
    assert time.monotonic() - start < 3, "Ctrl+C waited for the deadline"


# Covers F-0007.R6 (specs/0007-dependencies-verified-before-they-are-added.md).
@pytest.mark.parametrize(
    ("versions", "latest"),
    [
        (["1.0", "2.0rc1"], "2.0rc1, a pre-release"),
        (["1.0", "2.0.dev0"], "2.0.dev0, a pre-release"),
        (["2.0a1"], "2.0a1, a pre-release"),
        (["2.0rc1", "2.0"], "2.0"),
        (["1.0", "1.0.post1"], "1.0.post1"),
    ],
)
def test_a_pre_release_shown_as_the_latest_is_called_one(
    net: _Network, capsys: pytest.CaptureFixture[str], versions: list[str], latest: str
) -> None:
    """Second audit, minor: `latest 2.0rc1` read as "the release you would get"."""
    net.answers[INDEX + "demo-pkg/"] = json.dumps({"versions": versions})
    net.answers[PYPI.format("demo-pkg")] = _http_error(404)
    assert main(["verify-dep", "--index", INDEX, "demo-pkg"]) == 0
    assert f"versions on index.example: {len(versions)} (latest {latest})" in (
        capsys.readouterr().out
    )
