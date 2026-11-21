# F-0006: Guided review of a change (`rumbo tour`)
Story: As the human who reviews a branch written by a coding agent I want a guide of that
branch, one commit at a time, with the risky files first, the tests before the code, the
commands that verify the change and questions I have to answer, so that I approve only what I
can explain and do not read the diff alphabetically in one sitting.

Scope: `src/rumbo/tour.py` and the `tour` subcommand of the CLI (`cmd_tour` in
`src/rumbo/cli.py`). The command templates `/rumbo-tour`, `/rumbo-ancla` and `/rumbo-fix` and the
`review` target of the generated Makefile are consumers: they run the command and are not
specified here. Written retroactively: it describes the code as it is on 2026-11-21.
Decisions: ADR-0001 (standard library only), ADR-0006 (the runner the guide points to). No ADR
of its own; the design rationale is `docs/method.md`, section 3.

## Requirements (EARS)
<!-- Ubiquitous: The system shall… · Event: When…, the system shall… · State: While…, the system shall…
     Unwanted: If…, then the system shall… · Optional: Where…, the system shall… -->
- R1 (event): When `rumbo tour [BASE] [--head H]` runs in a git repository (a RUMBO project is not needed), the system shall build a guide of the commits of `B..H`, oldest first, print it on standard output and exit 0, where H defaults to `HEAD` and B is BASE (default `origin/main`) when git resolves it to a commit and otherwise the first of `origin/main`, `origin/master`, `origin/HEAD`, `main`, `master` that git resolves, in which case one line on standard error names the base that was used; the hash, subject, body and author of each commit shall be read as fields ended by NUL, so that no character of a message hides a commit or shifts a field.
- R2 (unwanted): If no base resolves (which includes a directory that is not a git repository), the range has no commits, or git rejects the range (for example an unknown `--head`), then the system shall print a message that starts with `Error:` on standard error, write no guide and exit 2.
- R3 (ubiquitous): The guide shall open with a header that gives the range, the number of commits, the changed lines with lockfiles excluded, the number of sittings of at most 300 lines (rounded up, at least one) and, where `--since <ref>` is given, the command `git range-diff <base> <ref> <head>` with each of the three refs quoted for the shell; it shall then give one numbered section per commit with, in this order, the subject, a line with the first ten characters of the hash, the author, the kind, the changed lines and the number of files, the first eight lines of the body as a quote when there is a body, the risk hotspots, the reading order, the verification commands and the explanation test; and it shall close with a "Before you approve" checklist of five items.
- R4 (ubiquitous): The system shall give each commit one kind: the one of the Conventional Commits type of its subject when the type is known (`feat`, `fix`, `perf`: behavioral; `refactor`, `style`: structural; `test`: tests; `docs`: docs; `chore`, `ci`, `build`: mechanical), and otherwise the one of its files (all of them tests: tests; all of them documents: docs; anything else, or no file: behavioral).
- R5 (ubiquitous): The risk hotspots of a commit shall list, one line per rule with at most four paths, the changed files whose path matches a rule (CI workflows, the pre-commit configuration, dependency manifests, infrastructure, secrets, authentication and security code, the anti-cheat sensor, the agent guides; the patterns are under "Data contracts"), the test files the commit deletes, and the test files whose diff removes a line that starts with `assert` or adds `pytest.mark.skip`, `skipif`, `xfail` or `pytest.skip(`, where the diff of a merge commit is the one against its first parent, a moved file is a deleted file and an added one, and the asserts removed with a deleted test file belong to that file; when nothing matches, the list shall say that no rule matched.
- R6 (ubiquitous): The reading order of a commit shall list its files without lockfiles, sorted by three keys in turn: tests before other files, files whose path contains a word of four or more characters of the subject before the rest, and more changed lines before fewer; every entry shall give the path, whether it is a test, a document or code, whether it is new or deleted, and the lines added and removed; a commit of more than 300 changed lines shall carry an "Over budget" note after the list.
- R7 (ubiquitous): The verification commands of a commit shall be `git show --stat <hash>`; `uv run pytest -q` on at most three Python test files that the commit changes and does not delete; `uv run python scripts/rumbo_check.py` when the kind is behavioral, structural or mechanical; and, when the kind is behavioral, a `git revert --no-commit` of the commit (with `-m 1` for a merge commit, the parent its diff is taken against) followed by the runner's `test` step and `git revert --abort`; every path and the hash shall be quoted for the shell as one word. No command shall use `make`.
- R8 (ubiquitous): The explanation test of a commit shall be three questions, each followed by a hint folded inside `<details>`: which test proves the subject (hint: the test files the commit touches, or that there are none); what observable behavior changes if the change to the first code file of the reading order (its first file when the commit has no code) is reverted (hint: the first line of the body, or that the body is empty), replaced for a structural commit by which lines change behavior; and which alternative was not chosen and where the reason is written.
- R9 (optional): Where `--out <path>` is given, the system shall write the guide as UTF-8 with LF line endings to that file instead of standard output, resolve a relative path against `--dir`, create the missing parent directories, replace an existing file and print `Wrote <file>`; where the path ends with a slash or a backslash or is an existing directory, the file shall be `<YYYY-MM-DD>-<hash>.md` inside it, with today's local date and the first ten characters of the hash of the last commit of the range.
- R10 (ubiquitous): The system shall build the guide without a model call and without a network call: its only subprocesses are `git rev-parse`, `git log` and `git show`, which do not change the repository, and `tour.py` imports the standard library and `rumbo.project` only; every git call whose output it parses shall fix its own format (paths not quoted by `core.quotepath`, no colour, no signature, messages in UTF-8, no external diff, no textconv, no rename detection, fields separated by NUL, the prefixes `a/` and `b/`), so that the reviewer's git configuration does not change the guide.

## Acceptance criteria
<!-- pytest tests / Hypothesis properties that verify them (path::test). A test covers a requirement
     by mentioning its full id, e.g. `F-000N.R1`, in its docstring; `rumbo trace` builds the table. -->
In `tests/test_tour.py` and `tests/test_cli.py`; each test carries its requirement id in a
comment. `uv run rumbo trace` prints which tests cover each requirement.

No end-to-end step: `scripts/e2e.sh` does not run `rumbo tour`.

## Data contracts and ownership
<!-- What two sessions (or two agents) must agree on BEFORE writing code. Omitting explicit data
     structures is what breaks parallel work (docs/method.md, section 3.1). -->
- Types / schemas: `FileChange(path: str, added: int, removed: int, status: str = "M", tests_dir: str = "tests")`, where `path` is relative to the top of the repository, `status` is the first letter of `git show --name-status` (`A`, `M` or `D`: renames are not followed), a binary file counts 0 lines and `tests_dir` is the project's tests directory as git names it (the prefix of `--dir` inside the repository plus `[tool.rumbo].tests`; `tests` when there is no project state); `Commit(sha, subject, body, author, files: list[FileChange], weakening_hunks: list[str])` with `short` (ten characters), `lines` (lockfiles excluded), `kind`, `risks() -> list[str]` and `reading_order() -> list[FileChange]`. A file is a test when its path starts with `<tests_dir>/` or `tests/`, contains `/tests/`, or its name starts with `test_` or ends with `_test.py`; a document when it ends in `.md`, `.rst` or `.txt` or starts with `docs/`; a lockfile when its name is `uv.lock`, `poetry.lock`, `package-lock.json`, `pnpm-lock.yaml` or `yarn.lock`.
- Risk rules (`RISK_RULES`, matched on the path): `.github/` at the root; `.pre-commit-config.yaml`; `pyproject.toml`, `requirements*.txt` or `package.json` at the root; `infra/`, or a root name that starts with `Dockerfile`, `docker-compose`, `fly.toml` or `render.yaml`; a file named exactly `.env` at the root, or any path that contains `secret` or `credential`; any path that contains `auth`, `security`, `permission`, `token` or `crypto`; any path that contains `check_tests_not_weakened`; `AGENTS.md`, `CLAUDE.md` or `.claude/` at the root.
- Git calls (constants `GIT`, `LOG`, `SHOW`, `DIFF`, `PATCH`): every parsed call starts with `git -c core.quotepath=off -c color.ui=never`; the log adds `-c i18n.logOutputEncoding=UTF-8`, `--reverse`, `--no-show-signature` and the format `%H%x00%s%x00%b%x00%an%x00`; every `git show` adds `--format= --no-show-signature --diff-merges=first-parent --no-color --no-ext-diff --no-textconv --no-renames`, then `--name-status -z`, `--numstat -z`, or for the patch `--src-prefix=a/ --dst-prefix=b/ --unified=0`.
- Signatures: `resolve_base(base: str | None, root: Path) -> str | None`; `load_commits(base: str, head: str, root: Path) -> list[Commit]`, which raises `RuntimeError` with git's message when a git command fails; `render_markdown(commits: list[Commit], base: str, head: str, since: str | None = None) -> str`; the CLI `rumbo tour [BASE] [--head H] [--since REF] [--out PATH] [--dir DIR]` through `cmd_tour(args) -> int`.
- Shared invariants: exit 0 means a guide was produced, exit 2 means none was; standard output carries either the guide or the line `Wrote <file>`, never both; the base substitution notice goes to standard error; the budget is 300 changed lines (`BUDGET_LINES`); the guide is Markdown and is written as UTF-8 with LF. Git is called once per base candidate tried (`rev-parse`, six at most), once for the log, once for `rev-parse --show-prefix`, and per commit twice for `show`, plus a third time (the patch) when the commit touches a test file.
- Files owned by this spec (one writer): `src/rumbo/tour.py`, `cmd_tour` and the `tour` parser in `src/rumbo/cli.py`, `tests/test_tour.py`, and `test_tour` and `test_tour_falls_back_to_a_known_base` in `tests/test_cli.py`.
- Interfaces touched that other specs depend on: the path `scripts/rumbo_check.py` and its `test` step, which the guide prints (F-0002); the name `docs/reviews/<date>-<hash>.md`, which the command templates, the generated Makefile and the generated `docs/reviews/README.md` rely on (F-0004); `tests_dir` and `DEFAULT_TESTS` of `src/rumbo/project.py` (F-0005) and `write_text` of the scaffold (F-0004), which this command uses.

## Evals (LLM products)
<!-- case IDs, threshold -->
Not applicable: the command calls no model. The questions it prints are fixed templates filled
with the subject, a path and the first line of the body.

## NFR
0 network calls · 0 model calls (R10) · 0 runtime dependencies (ADR-0001) · cost 0 USD.
No latency target: none is measured today.

## Out of scope
Not done by this command:
- Writing questions from the diff: `/rumbo-tour` asks the agent to replace the three template
  questions; `/rumbo-review` is the second review. Neither is specified here.
- Running the verification commands: the guide prints them. It does not check that `uv` or
  `scripts/rumbo_check.py` exist in the repository under review.
- Recording the approval: the checklist ends by pointing to `rumbo decision`.

Known limits of the code as it is (observed while writing this spec, none of them tested):
- **The guide quotes commit subjects, bodies, author names and file names verbatim in its
  Markdown; only the paths and refs inside a command are quoted (R3, R7).** A hostile commit
  message can carry Markdown, HTML or text that addresses an agent that reads the guide, and
  `/rumbo-tour` makes an agent read it. There is no mitigation in the code; it is an accepted
  risk (`docs/security/threat-model.md`, "Accepted risks").
- Without the `git` executable the command does not exit 2: `FileNotFoundError` escapes as a
  traceback. R2 covers a directory that is not a repository, not a machine without git.
- `--diff-merges=first-parent` needs git 2.31 or newer (by git's release notes; nothing here
  checks the version): an older git rejects every `git show` and the command exits 2 (R2).
- The kind trusts the subject (R4): a commit titled `docs:` that changes code is labelled docs
  and gets no runner command (R7).
- A merge commit is a section of its own. When the merged commits are in the range too, their
  lines are counted twice in the header.
- For a project in a sub-directory of the repository, the paths of the guide and of its
  `pytest` command are relative to the top of the repository, and so are the risk rules.
- The risk rules look at paths only. `.env.local`, a `.env` in a subdirectory, the Makefile,
  the verification runner and the guard match no rule; `author.py` matches the `auth` rule.
- `--since` is copied into the `git range-diff` command without checking that the ref exists.
- A mistyped BASE does not stop the command when a default base exists: it falls back (R1) and
  only the line on standard error says so.

## Questions open for the human who signs
- R4: is the kind meant to follow the commit prefix? `tour.py` says the review is built "from
  the diff, never from what the commit message claims", and `docs/method.md` (section 3)
  "Describe from the diff, never from the message".
- R2: should a missing `git` executable exit 2 with a message instead of a traceback?
- R1: should a BASE that was given explicitly and does not exist be an error, not a fallback?
