# ADR-0028: `verify-dep` treats every answer as untrusted and sends credentials only to the index it was given
Status: accepted    Date: 2026-11-16

Completes ADR-0014 (`verify-dep` honours a private index). ADR-0014 stands.

## Context
`rumbo verify-dep` makes up to three requests per package: the project's index, PyPI and OSV.
ADR-0014 decided what is asked of each and that the credentials of an index URL are never
printed. It left the transport to `urllib.request.urlopen` and trusted the shape of a 200
answer. The second audit before v0.4.1 (`docs/audits/2026-11-16-pre-0.4.1.md`, section 5 and
row D7) reproduced what that leaves open:

1. **A 200 answer confirms a hallucinated name (V1).** An index behind SSO answers 200 with its
   login page for every path; so does a gateway with `{"error": ...}`. The page was parsed as
   "a project with zero versions": `exists: yes`, verdict 0. It is the one case the command
   exists to catch.
2. **The credentials follow a redirect (V2).** `urllib` copies every header of a request to
   wherever a 30x points: another host, or the same host over plain `http`.
3. **The loopback exception leaves the machine (V3).** With `HTTP_PROXY` set, the request to an
   index on `127.0.0.1`, `Authorization` included, was sent to the proxy.
4. **The argument goes into the URL of an authenticated request as typed (V4).**
   `../../admin/users?x=1#` and `requests[socks]>=2` were not names, and were sent anyway.
5. **The 15 s timeout bounds one socket read, and nothing bounds the body (V5).** A server
   that sends a few bytes every two seconds holds the command for as long as it likes.
6. **Three kinds of answer end in a traceback (V7):** a charset Python does not know, JSON
   nested too deep, a protocol error (`http.client.HTTPException` is not an `OSError`). An
   index URL that cannot be parsed ended the run before PyPI was asked and without the
   reminder, against F-0007.R8 and R10.
7. **What is printed is what the network sent (V8).** "latest" ignored PEP 440 (`1.0` over
   `1.0.post1`, `rc9` over `rc10`, no epoch) and yanked files; a package summary could carry
   escape sequences that erase the warnings above it and write a line that looks like ours.
8. **No test went through the real `urllib` path (D7):** `urlopen` was replaced in every test
   of redirects, proxies and timeouts, which is where findings 2, 3 and 5 live.

## Drivers (non-functional requirements)
Standard library only (ADR-0001). A credential reaches the host it was written for and no
other, whatever that host answers. A source that cannot be read is verdict 2, never a
traceback and never a pass (F-0007.R8). The command ends in bounded time. Nothing is guessed:
what cannot be told apart is reported as not confirmed. A company proxy keeps working.

## Options considered
1. **Keep `urlopen` and patch each finding where it shows.** Rejected: redirects and proxies
   are decided by the global opener, which `urlopen` builds once per process from the
   environment; a fix around it is a fix that the next handler undoes.
2. **Leave `urllib` for `http.client`.** It gives the socket, and with it a simple deadline,
   but redirects, proxies, `CONNECT` tunnels and their credentials would be ours to rewrite.
   Rejected: more code on the trust boundary than the findings justify.
3. **One private opener, and every answer read as untrusted data (chosen).**

For the deadline, inside option 3: (a) only read the body in pieces against a clock: it does
not cover what comes before the body, where a server can drip the status line just as well;
(b) wrap the socket inside `http.client`: it depends on how `urllib` hands sockets over, which
is not a public contract; (c) (a) plus a daemon thread that abandons the request. Chosen: (c).

## Decision and rationale
**One opener.** Every request of the CLI goes through `_fetch` → `_open` → `_opener`, the only
place an opener is built. It has handlers for `http` and `https` and nothing else (no `file`,
`ftp` or `data`), and no state shared between requests. `urlopen` is no longer called.

**Redirects.** Indexes redirect by design: GitLab's package registry forwards every package it
does not hold to `pypi.org`, and pypiserver does the same. So a redirect is followed, and what
changes is what travels with it. The `Authorization` header is added with
`add_unredirected_header`, so that `urllib` never copies it; the redirect rule puts it back
only when the new URL has the same scheme, host and port as the one that was redirected.
Anywhere else the request goes on without it, and once dropped it stays dropped, also when a
later hop returns to the index. When the page that is read was served by another host, the
output says so: `exists on <host>: yes (answered by <final host> after a redirect; the
credentials were not sent there)`. Three redirects are refused, credentials or not, with a
fixed message (the target is untrusted text and is not printed), verdict 2: `https` to plain
`http`, which keeps F-0007.R9 true after a redirect; a scheme that is not `http(s)`; and a
target whose authority is not plainly `host[:port]` (user info, a backslash, a percent
escape: where two readers of one URL stop agreeing on the host). A first version of this
rule refused every cross-origin redirect of an authenticated request; the second audit showed
it made the command useless on a GitLab index.

**Proxies.** A loopback index (`localhost`, `127.0.0.0/8`, `::1`) is reached with
`ProxyHandler({})`: no proxy of the environment, so the exception of F-0007.R7 means what it
says. Everything else keeps the proxies of the environment; credentials for a remote index
only travel over `https` (F-0007.R7), that is, inside the tunnel the proxy cannot read. The
credentials of the proxy itself (`Proxy-Authorization`, which `urllib` writes as an ordinary
header) are removed from a redirected request; `ProxyHandler` writes them again when that
request really goes through the proxy, so a target that `NO_PROXY` reaches directly never
receives them.

**Deadline and size.** One request, redirects included, has `HTTP_DEADLINE` = 60 s in total
and its answer `MAX_BODY` = 32 MB; each socket operation keeps the 15 s of F-0007.R8. The body
is read with `read1` in 64 KiB pieces, checking the clock and the size after each; a body
shorter than its `Content-Length` is an `IncompleteRead`. The request runs in a daemon thread
that is abandoned at the deadline: it is the only way, without replacing `urllib`, to bound
name resolution, the connection, TLS and the headers. The main thread waits for it in slices
of 0.2 s: one long `join` is not interrupted by Ctrl+C on Windows, and the command would
then answer the keyboard only when the deadline passed. Both numbers are constants chosen
without a measurement (the one request to `pypi.org` that would have measured the largest
JSON documents was not possible from the session that wrote this): 32 MB is several times
what the largest PyPI projects are known to answer, and 60 s is four times the per-operation
timeout. Both are to be revisited with numbers.

**A page or not a page.** `simple_page` returns a page or `None`. A PEP 691 answer is a JSON
object with a `files` list or a `versions` list; a PEP 503 answer has at least one anchor that
stands for a distribution file with a PEP 440 version. The file an anchor stands for is its
text (PEP 503). A plain web server that lists a directory cuts a long text and ends it in
`..>` (nginx's autoindex, Apache's FancyIndexing); the name is then the last path segment of
the href, percent-decoded, without query or fragment. The href is used only in that case,
when the text is the beginning of that name: read for every anchor, it would turn a login
page with a link such as `<a href="/app-1.2.zip">Download the app</a>` into a package page,
which is the finding this ADR starts from. A 200 answer that is neither is `the index
answered, but not with a package page`, verdict 2. A page with no version at all is `exists
on <host>: yes, no files`, verdict 1. A PEP 503 page of a project with no file left cannot be
told from a login page and is therefore verdict 2: it asks for a look, which is the right
outcome for both.

**Arguments.** An argument is a PEP 508 name, alone or followed by extras, a version specifier
and a marker; the bare name is verified and a note says so. Anything else (a URL, a path, a
VCS reference, a direct reference `name @ url`) is a usage error for that argument, exit 2,
before any request, and the remaining arguments are verified. The argument is not echoed: a
URL can carry a token. The name is percent-encoded into one path segment of every URL.

**Errors.** `LookupError`, `http.client.HTTPException` and JSON nested too deep are "could
not query", verdict 2, for the index and PyPI; for OSV they are "not reachable", as F-0007.R4
already says of every other OSV failure. A charset that Python does not know is read as
UTF-8. An index URL that cannot be parsed is reported as an index that could not be queried
(on stdout, verdict 2); PyPI and OSV are still measured and the reminder closes the run.

**The index value.** It comes from `--index` or the environment and is not trusted to be a
URL. A value whose scheme is not `http(s)`, or that has no host, is refused in fixed text,
`the index URL must be http(s): it has to start with http:// or https:// (value not shown:
it may hold credentials)`, and no part of it is echoed: with the scheme forgotten,
`alice:PASSWORD@host/simple/` reads as scheme `alice` and path `PASSWORD@host/simple/`, and
the "sanitised" form printed the password. It is handled like a value that cannot be parsed:
verdict 2 for the index, PyPI and OSV still measured.

**What is printed.** Versions are ordered by a key written from PEP 440 (epoch, release, dev,
pre-release with numeric counter, post-release, local); a string that is not a version orders
below every one that is. A release whose every file is yanked (PEP 691 `yanked`, PEP 503
`data-yanked`) is not "latest" unless all are, and then the line says so; a latest that is a
pre-release or a development release is labelled `a pre-release`, because it is the highest
version and not the one an installer picks by default. Every string that came from the
network (summary, version, URLs, canonical name, the host that answered after a redirect,
the reason of an error) goes through `_show`: control characters (C0, C1, bidi overrides,
lone surrogates) become a space and the text is cut at 200 characters.

**A limit that stays.** `sanitize_index` removes the user-info part of an index URL. A token
written as a path segment (`https://host/TOKEN/simple/`) cannot be told from a repository
name, so it is printed with the path. The command does not guess; the help of `--index` says
where credentials go. One exception, which is not a guess about tokens: what stands before an
`@` inside the path is printed as `***`, in the index line and in error messages, because a
password whose `/` was not percent-encoded ends there (`https://user:pass/word@host/` has the
host `user` and the path `/word@host/`). The request still goes where the value says.

## Consequences (+/−)
+ A login page, an error document or a truncated answer no longer confirms a name.
+ The credentials of an index reach that index and nothing else: not a redirect target, not a
  proxy when the index is local.
+ The command ends: at most 60 s per request, three requests per package.
+ The redirect, proxy, deadline and size rules are tested against real local servers.
+ An index that forwards to another host (GitLab, pypiserver) is followed, without the
  credentials, and the output names the host that answered.
− A page read after a redirect is the page of another host: the note says so, the verdict does
  not change. A redirect to a private host that wanted the same credentials ends in its 401.
− A loopback index that redirects to a remote host is followed without the proxies of the
  environment too: the choice is made once per request, from the index.
− A link text cut by a web server with another mark than `..>` is not read from the href.
− A feed whose path holds an `@` (`.../feed@Release/...`) is printed with `***` before it.
− A PEP 503 page with no file is "not confirmed" instead of "exists".
− A request abandoned at the deadline leaves a daemon thread and its socket behind until the
  server stops sending or the process exits; the command is short-lived.
− Two numbers (60 s, 32 MB) were chosen without measuring; the PEP 440 key is ours to keep.
− A token in the path of an index URL is still printed.
− The time to parse an answer is not under the deadline: `html.parser` on 32 MB is slow.

## Fitness function
`tests/test_verify_dep.py`. Through the real `urllib`, against servers on 127.0.0.1:
`::test_a_redirect_to_another_port_never_receives_the_credentials`,
`::test_a_redirect_inside_the_index_keeps_the_credentials_and_without_them_any_is_followed`,
`::test_a_loopback_index_is_never_reached_through_the_proxy_of_the_environment`,
`::test_an_index_elsewhere_is_still_reached_through_the_proxy_of_the_environment`,
`::test_a_server_that_never_finishes_is_cut_by_the_deadline`,
`::test_a_body_larger_than_the_cap_is_refused_and_one_cut_short_is_not_a_page`,
`::test_a_redirect_away_from_the_index_is_refused_when_credentials_are_set` (its name is older
than the rule: it asserts that the target receives the request without `Authorization`),
`::test_the_credentials_of_a_proxy_do_not_follow_a_redirect_past_the_proxy`,
`::test_ctrl_c_is_not_held_until_the_deadline`.
On the rules themselves:
`::test_a_200_answer_that_is_not_a_package_page_does_not_confirm_the_name`,
`::test_a_package_page_without_files_exists_and_is_flagged_for_review`,
`::test_a_redirect_away_from_the_index_drops_the_credentials_for_good`,
`::test_the_listing_of_a_plain_web_server_is_a_package_page`,
`::test_the_file_name_comes_from_the_href_only_when_the_text_is_that_name_cut_short`,
`::test_no_shape_of_an_index_value_prints_its_password`,
`::test_an_argument_that_is_not_a_package_name_is_refused_and_the_others_go_on`,
`::test_latest_follows_the_order_of_pep_440`,
`::test_text_from_the_network_is_printed_without_control_characters_and_bounded`, and
`::test_urlopen_is_called_only_by_the_two_verify_dep_lookups`, which fails when any code of
the CLI calls `urlopen` or builds an opener outside `_opener`.

## Reversibility: high
The opener, the page test and the printing function are private to the `verify-dep` block of
`src/rumbo/cli.py`; the command line and the exit codes are unchanged.
