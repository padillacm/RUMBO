"""Gates G1–G5: automatic checks plus the human checklist, scaled by rigor.

The automatic checks are cheap sensors over the artifacts; they never replace the human
signature. A gate passes only when every automatic check is green **and** the human ticks
the manual items. ``ok=None`` marks an informational result (could not be measured, or a
recommendation for the current rigor): it is shown but does not fail the gate.
"""

from __future__ import annotations

import functools
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from rumbo.metrics import dataset_cases
from rumbo.project import (
    NOT_UTF8,
    SUPPORTED_PYTHONS,
    NotUTF8,
    Project,
    is_template,
    normalise_tests_dir,
    numbered_documents,
    prose,
    section,
)
from rumbo.trace import trace, uncovered

MAX_DIFF_LINES = 300
FIX_MAX_DIFF_LINES = 150  # ADR-0009: the fix lane allows half the normal diff
# ADR-0009: a PR is in the fix lane when every commit since the merge base has one of these
# types and none carries the breaking marker `!` before the colon.
FIX_TYPES = re.compile(r"^(fix|test|refactor|chore|docs|ci|perf|style|build)(\([^)]*\))?:")
# The candidates of the runner and of the anti-cheat sensor (their DEFAULT_BASES), in order.
BASES = ("origin/main", "origin/master", "origin/HEAD", "main", "master")
CHECK_ENV = 3  # scripts/rumbo_check.py: the verification environment is broken (ADR-0006)
TAIL_LINES = 20  # of the output of the runner and of the sensor, in the detail of a G4 check
TAIL_STDOUT = 15  # of them, the end of standard output: where the verdict is
# ADR-0018: a gate cannot be green on a verification that did not happen.
ENV_REPAIR = "this is the environment, not the code: the human repairs it and runs the gate again"
# ADR-0010: what each kind has in operation. `_g5` and the G5 checklist read the same tuples,
# so the human is never asked to sign a document the automatic checks do not expect.
RELEASED_KINDS = ("library", "cli")  # published, not operated
RUNBOOK_KINDS = ("service", "data")
SLO_KINDS = ("service",)
# A PRD table row whose first cell is a feature id: `| F-0001 | ... |`.
MIN_EVAL_CASES = {"prototype": 20, "mvp": 50, "production": 100}
EARS = re.compile(r"\bshall\b|\bdeber[áa]n?\b", re.I)
# The placeholder of the requirement lines of the spec template (`When …, the system shall …`).
# Three dots, `<200 ms` or `list[...]` are what a real requirement may say.
ELLIPSIS = "…"
CODE_SPAN = re.compile(r"`[^`\n]*`")
MERMAID = re.compile(r"^[ \t]*```mermaid[^\n]*\n(.*?)^[ \t]*```", re.M | re.S)
LOCKFILES = ("uv.lock", "poetry.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock")
# The templates the documents of a project were generated from (`.tmpl` when rendered).
TEMPLATES = Path(__file__).resolve().parent / "templates"
SPEC_TEMPLATE = "specs/0000-template.md"
THREAT_MODEL = "docs/security/threat-model.md"
C4 = "docs/architecture/c4.md"
WORKFLOWS = ".github/workflows"
RUNNER = "scripts/rumbo_check.py"
_TAG = re.compile(r"\{\{[#^/]?\w+\}\}")
# ADR-0032: the triggers of a workflow that verifies a change (not only a schedule).
_CHANGE_TRIGGER = re.compile(r"\b(?:push|pull_request|pull_request_target|merge_group)\b")
_ON_KEY = re.compile(r"""^(['"]?)on\1[ \t]*:(.*)$""")


@dataclass
class Result:
    description: str
    ok: bool | None
    detail: str = ""


Check = Callable[[Project, bool], list[Result]]


@dataclass(frozen=True)
class Item:
    text: str
    min_rigor: str = "prototype"
    llm_only: bool = False
    max_rigor: str = "production"
    # ADR-0010: the same conditions as the automatic checks, so both ask about the same things.
    arch_only: bool = False  # only when the layout has layers (`Project.arch`, as in `_g3`)
    kinds: tuple[str, ...] = ()  # only for these kinds (as in `_g5`); empty means every kind


@dataclass(frozen=True)
class Gate:
    code: str
    phase: str
    manual: tuple[Item, ...]
    checks: tuple[Check, ...]

    def checklist(self, project: Project) -> list[str]:
        return [
            item.text
            for item in self.manual
            if project.at_least(item.min_rigor)
            and not (project.at_least(item.max_rigor) and item.max_rigor != "production")
            and (project.llm or not item.llm_only)
            and (project.arch or not item.arch_only)
            and (not item.kinds or project.kind in item.kinds)
        ]


def _read(p: Project, rel: str) -> tuple[str | None, str]:
    """(text, problem) of a file of the project: the text is None when the file is missing or
    is not UTF-8, and in the second case `problem` names it. A gate never raises on a file."""
    try:
        return p.read(rel), ""
    except NotUTF8 as exc:
        return None, str(exc)


def _doc(p: Project, rel: str, label: str, required: bool = True) -> Result:
    text, problem = _read(p, rel)
    if problem:
        return Result(f"{label} complete", False if required else None, problem)
    if text is None:
        return Result(f"{label} exists", False if required else None, f"missing {rel}")
    detail = ""
    if not text.strip():
        detail = f"{rel} is empty"
    elif is_template(text):
        detail = f"{rel} still has the template mark on its first line"
    complete = not detail
    return Result(f"{label} complete", complete if required else (complete or None), detail)


# ------------------------------------------------- what the human wrote, what the template had
def _squash(line: str) -> str:
    """A line as a formatter may have rewritten it: no whitespace, runs of dashes folded."""
    return re.sub(r"-+", "-", re.sub(r"\s+", "", line))


@functools.cache
def _packaged(rel: str) -> str:
    """The template `rel` was generated from, as shipped with this CLI; "" when there is none."""
    for name in (rel, rel + ".tmpl"):
        try:
            return (TEMPLATES / name).read_text(encoding="utf-8")
        except OSError:
            continue
    return ""


def _own_lines(body: str, *templates: str) -> list[str]:
    """The non-empty lines of `body` that are not lines of any of the templates.

    The comparison is by line, squashed (`_squash`), and a `{{variable}}` of a template matches
    anything: the document need not be rendered again, and a project that was renamed since it
    was generated still compares equal. A document that only lost its mark has no line of its own.
    """
    literal: set[str] = set()
    patterns: list[re.Pattern[str]] = []
    for template in templates:
        for line in template.splitlines():
            parts = _TAG.split(_squash(line))
            if len(parts) == 1:
                literal.add(parts[0])
            elif any(parts):  # a section tag alone on its line matches nothing
                patterns.append(re.compile(".*".join(re.escape(part) for part in parts)))
    own: list[str] = []
    for line in body.splitlines():
        squashed = _squash(line)
        if not squashed or squashed in literal:
            continue
        if not any(pattern.fullmatch(squashed) for pattern in patterns):
            own.append(line)
    return own


# ------------------------------------------------------------------ G1 Recognize
def _g1(p: Project, _run: bool) -> list[Result]:
    results = [_doc(p, "docs/product/brief.md", "Brief")]
    src = p.root / p.code_root  # ADR-0020: `src`, or the package itself when it is at the root
    offenders: list[str] = []
    if src.is_dir():
        pattern = re.compile(r"^\s*(from|import)\s+spikes\b", re.M)
        offenders = [
            f.relative_to(p.root).as_posix()
            for f in src.rglob("*.py")
            if pattern.search(f.read_text(encoding="utf-8", errors="replace"))
        ]
    results.append(
        Result(
            f"nothing in spikes/ is imported from {p.code_root}/",
            not offenders,
            ", ".join(offenders),
        )
    )
    return results


# ---------------------------------------------------------------------- G2 Unify
def _spec_templates(p: Project) -> tuple[str, ...]:
    """The spec template of this CLI and the project's own copy of it, which a team may edit."""
    own, _ = _read(p, SPEC_TEMPLATE)
    return (_packaged(SPEC_TEMPLATE), own or "")


def _written(text: str, title: str, templates: tuple[str, ...]) -> list[str]:
    """Lines of a section that are the human's: outside code fences, not lines of a template."""
    return _own_lines(section(prose(text), title), *templates)


def _has_real_ears(text: str, templates: tuple[str, ...] = ()) -> bool:
    for line in _written(text, "Requirements", templates or (_packaged(SPEC_TEMPLATE),)):
        said = CODE_SPAN.sub("", line)  # `error: <path> not found` is what the system prints
        if EARS.search(said) and ELLIPSIS not in said:
            return True
    return False


def _has_real_contracts(text: str, templates: tuple[str, ...] = ()) -> bool:
    lines = _written(text, "Data contracts and ownership", templates or (_packaged(SPEC_TEMPLATE),))
    return any(line.strip().startswith("-") for line in lines)


def _datasets(p: Project) -> tuple[int, str]:
    """(real eval cases, a note naming the datasets that are not UTF-8)."""
    count, unreadable = dataset_cases(p.root)
    return count, "".join(f"; evals/datasets/{name}: {NOT_UTF8}" for name in unreadable)


def _g2(p: Project, _run: bool) -> list[Result]:
    results = [_doc(p, "docs/product/prd.md", "PRD", required=p.at_least("mvp"))]
    specs = numbered_documents(p.root / "specs")
    results.append(Result("at least one spec in specs/", bool(specs), "use 'rumbo new spec'"))
    templates = _spec_templates(p)
    for spec in specs:
        rel = spec.relative_to(p.root).as_posix()
        text, problem = _read(p, rel)
        if text is None:
            results.append(Result(f"{rel}: complete", False, problem or f"missing {rel}"))
            continue
        results.append(
            Result(f"{rel}: complete", not is_template(text), "still has the template mark")
        )
        if p.at_least("mvp"):
            results.append(
                Result(
                    f"{rel}: EARS requirements",
                    _has_real_ears(text, templates),
                    "no requirement line with 'shall' beyond the template placeholders",
                )
            )
        results.append(
            Result(
                f"{rel}: acceptance criteria",
                bool(section(text, "Acceptance criteria")),
                "section is empty",
            )
        )
        contracts = _has_real_contracts(text, templates)
        results.append(
            Result(
                f"{rel}: data contracts and file ownership",
                contracts if p.at_least("production") else (contracts or None),
                "fill 'Data contracts and ownership' (types, signatures, owned files) before "
                "splitting the work across sessions",
            )
        )
    if p.evals:  # ADR-0010: LLM products and data pipelines both have datasets
        minimum = MIN_EVAL_CASES[p.rigor]
        n, unreadable = _datasets(p)
        results.append(
            Result(
                f"≥{minimum} real cases in evals/datasets/ (rigor {p.rigor})",
                n >= minimum,
                f"found {n} (a case is an object with an 'input'; cases tagged 'example' do "
                f"not count){unreadable}",
            )
        )
        results.append(_doc(p, "docs/evals/plan.md", "Eval plan", required=p.at_least("mvp")))
    return results


# ---------------------------------------------------------------------- G3 Model
def _contracts(p: Project) -> int:
    text, _ = _read(p, "pyproject.toml")
    try:
        data = tomllib.loads(text or "")
    except tomllib.TOMLDecodeError:
        return 0
    contracts = data.get("tool", {}).get("importlinter", {}).get("contracts", [])
    return len(contracts) if isinstance(contracts, list) else 0


@dataclass
class Workflows:
    """Every `*.yml` and `*.yaml` under `.github/workflows/` (ADR-0032): an adopted project
    keeps the names its workflows had, so no check asks for `ci.yml`."""

    texts: dict[str, str] = field(default_factory=dict)  # relative path -> text
    unreadable: list[str] = field(default_factory=list)  # "<path>: not UTF-8 (...)"

    @classmethod
    def of(cls, p: Project) -> Workflows:
        found = cls()
        folder = p.root / WORKFLOWS
        files = sorted(folder.iterdir()) if folder.is_dir() else []
        for file in files:
            if file.suffix not in (".yml", ".yaml") or not file.is_file():
                continue
            rel = f"{WORKFLOWS}/{file.name}"
            text, problem = _read(p, rel)
            if problem:
                found.unreadable.append(problem)
            else:
                found.texts[rel] = text or ""
        return found

    def first(self, wanted: Callable[[str], bool]) -> str:
        """The first workflow whose text satisfies `wanted`; "" when none does."""
        return next((rel for rel, text in self.texts.items() if wanted(text)), "")

    def missing(self, what: str) -> str:
        """The detail of a check no workflow satisfied; it names the ones that were not read."""
        return "; ".join([f"no workflow under {WORKFLOWS}/ {what}", *self.unreadable])


def _verifies_changes(workflow: str) -> bool:
    """True when the workflow is triggered by a push or a pull request, not only a schedule."""
    lines = workflow.splitlines()
    for i, line in enumerate(lines):
        key = _ON_KEY.match(line)
        if not key:
            continue
        block = [key.group(2)]
        for row in lines[i + 1 :]:
            if row.strip() and not row[0].isspace() and not row.startswith("#"):
                break  # the next top-level key
            block.append(row.split("#", 1)[0])
        return bool(_CHANGE_TRIGGER.search("\n".join(block)))
    return False


def _runs_the_runner(workflow: str) -> bool:
    return RUNNER in workflow and _verifies_changes(workflow)


def _assessed(document: str | None, title: str, problem: str) -> tuple[bool, str]:
    """(a section of the threat model has a line of the human's, the detail when it has not).

    Section content counts only once the human removed the template mark, and only when it is
    not the template's own: a table whose rows are still empty assesses nothing.
    """
    if problem:
        return False, problem
    filled = not is_template(document) and bool(
        _own_lines(section(document or "", title), _packaged(THREAT_MODEL))
    )
    return filled, f"section '{title}' is empty or still equals the template"


def _g3(p: Project, _run: bool) -> list[Result]:
    adrs = numbered_documents(p.root / "docs" / "adr")
    results = [Result("at least one ADR", bool(adrs), "use 'rumbo new adr'")]
    workflow_adr = False
    for adr in adrs:
        rel = adr.relative_to(p.root).as_posix()
        text, problem = _read(p, rel)
        if text is None:
            results.append(Result(f"{rel}: complete", False, problem or f"missing {rel}"))
            continue
        results.append(
            Result(f"{rel}: complete", not is_template(text), "still has the template mark")
        )
        has_fitness = bool(section(text, "Fitness function"))
        results.append(
            Result(
                f"{rel}: fitness function",
                has_fitness if p.at_least("production") else (has_fitness or None),
                "section is empty (required in production; recommended before)",
            )
        )
        # First heading; an empty or heading-less file is simply not the workflow ADR.
        title = next((ln for ln in text.splitlines() if ln.lstrip().startswith("#")), "")
        if re.search(r"workflow", title, re.I) and re.search(r"agent", title, re.I):
            workflow_adr = True
    if p.llm:
        results.append(
            Result(
                "ADR 'workflow vs. agent'",
                workflow_adr if p.at_least("mvp") else (workflow_adr or None),
                "no ADR whose title mentions both 'workflow' and 'agent'",
            )
        )
    tm_required = p.at_least("mvp")
    results.append(_doc(p, THREAT_MODEL, "Threat model", required=tm_required))
    tm, problem = _read(p, THREAT_MODEL)
    trifecta, detail = _assessed(tm, "Lethal trifecta", problem)
    results.append(
        Result(
            "threat model: lethal trifecta assessed",
            trifecta if tm_required else (trifecta or None),
            detail,
        )
    )
    if p.llm:  # the OWASP LLM and Agentic lists are about a model: nothing to fill without one
        owasp, detail = _assessed(tm, "OWASP", problem)
        results.append(
            Result(
                "threat model: OWASP LLM/Agentic checklist filled",
                owasp if tm_required else (owasp or None),
                detail,
            )
        )
    results.append(_doc(p, C4, "C4 levels 1–2", required=tm_required))
    c4, problem = _read(p, C4)
    diagrams = MERMAID.findall(c4 or "")
    drawn = (
        not is_template(c4)
        and len(diagrams) >= 2
        and bool(_own_lines("\n".join(diagrams), _packaged(C4)))
    )
    results.append(
        Result(
            "C4 has Mermaid diagrams",
            drawn if tm_required else (drawn or None),
            problem
            or "two diagrams, with at least one line that is not the template's, in a document "
            "whose template mark was removed",
        )
    )
    if p.arch:
        results.append(
            Result(
                "import-linter contracts",
                _contracts(p) >= 2,
                "need the layers and purity contracts",
            )
        )
    else:
        # ADR-0010: a flat package has no layers to contract; the spikes rule still applies.
        # ADR-0020: in the project's own tests directory, not a hard-coded `tests/`.
        arch_test = f"{p.tests_dir}/architecture/test_architecture.py"
        results.append(
            Result(
                "architecture test present",
                (p.root / arch_test).is_file(),
                f"missing {arch_test}",
            )
        )
    workflows = Workflows.of(p)
    ci = workflows.first(_runs_the_runner)
    results.append(
        Result(
            "CI runs rumbo check",
            bool(ci),
            f"in {ci}"
            if ci
            else workflows.missing(
                f"that runs on push or pull_request has a step that runs {RUNNER} (ADR-0006)"
            ),
        )
    )
    return results


# ------------------------------------------------------------- G4 Build (per PR)
def _run_cmd(cmd: list[str], root: Path) -> tuple[int | None, str]:
    """(exit code, tail of the output); the code is None when the program is not installed.

    The verdict of the runner and of the sensor is the end of their standard output. What the
    tools wrote to standard error goes before it and gets the smaller share of the tail, so
    that warnings never push the finding out of the report.
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return None, f"'{cmd[0]}' is not installed; could not run {' '.join(cmd)}"
    out = (proc.stdout or "").strip().splitlines()[-TAIL_STDOUT:]
    err = (proc.stderr or "").strip().splitlines()
    return proc.returncode, "\n".join(err[-(TAIL_LINES - len(out)) :] + out)


def _check_command(root: Path) -> list[str]:
    """`scripts/rumbo_check.py` (ADR-0006); `make check` for a project not yet updated."""
    if (root / "scripts" / "rumbo_check.py").is_file():
        return [sys.executable, "scripts/rumbo_check.py"]
    return ["make", "check"]


def _git(args: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _find_base(root: Path) -> tuple[str | None, str, bool]:
    """(merge-base sha, base name, HEAD is that base); (None, reason, False) without a base."""
    head = _git(["rev-parse", "HEAD"], root)
    if head.returncode != 0:
        return None, "not a git repository or no commits yet", False
    for base in BASES:
        mb = _git(["merge-base", "HEAD", base], root)
        if mb.returncode == 0:
            sha = mb.stdout.strip()
            return sha, base, sha == head.stdout.strip()
    return None, f"no base branch found ({', '.join(BASES)})", False


def merge_base(root: Path) -> tuple[str | None, str]:
    """(merge-base sha, base name) of the branch work; (None, reason) when there is none."""
    return _branch_base(*_find_base(root))


def _branch_base(sha: str | None, info: str, at_base: bool) -> tuple[str | None, str]:
    if at_base:
        # HEAD is the base (or behind it): there is no branch work to measure, and
        # counting only the uncommitted changes would pass a huge commit on main.
        return None, f"HEAD is already on '{info}': commit on a branch to measure the diff"
    return sha, info


def _untracked(root: Path) -> list[tuple[int, int, str]]:
    """(lines, 0, path) per new text file git does not know yet and does not ignore: it is
    part of the change the moment it is added, so it is part of the size now."""
    listed = _git(["ls-files", "--others", "--exclude-standard", "-z"], root)
    rows: list[tuple[int, int, str]] = []
    for path in filter(None, listed.stdout.split("\0")) if listed.returncode == 0 else []:
        if Path(path).name in LOCKFILES:
            continue
        try:
            with (root / path).open("rb") as file:
                chunk = file.read(8000)
                if b"\0" in chunk:
                    continue  # binary, by the rule git uses: `--numstat` counts no line of it
                lines, last = 0, b""
                while chunk:
                    lines, last = lines + chunk.count(b"\n"), chunk[-1:]
                    chunk = file.read(1 << 20)
        except OSError:
            continue
        rows.append((lines + (last not in (b"", b"\n")), 0, path))
    return rows


def _tracked(root: Path, base: str) -> list[tuple[int, int, str]]:
    """(added, removed, path) per text file git knows that changed since `base`, lockfiles
    excluded.

    The paths are relative to `root` and only what is under it counts (`--relative`): the
    project may be one directory of a repository.
    """
    excludes = [f":(exclude,glob)**/{lock}" for lock in LOCKFILES]  # at any depth
    options = ["--numstat", "-z", "-M", "--relative", "--no-ext-diff"]
    diff = _git(["diff", *options, base, "--", ".", *excludes], root)
    rows: list[tuple[int, int, str]] = []
    fields = iter(diff.stdout.split("\0"))
    for entry in fields:
        parts = entry.split("\t")
        if len(parts) != 3:
            continue
        # A rename is `added removed <empty>` followed by the old and the new path.
        path = parts[2] or (next(fields, ""), next(fields, ""))[1]
        if parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), path))
    return rows


def _numstat(root: Path, base: str) -> list[tuple[int, int, str]]:
    """What changed since `base`: the files git knows, then the ones not yet added to it."""
    return _tracked(root, base) + _untracked(root)


def _size(rows: list[tuple[int, int, str]]) -> int:
    return sum(added + removed for added, removed, _ in rows)


def diff_size(root: Path) -> tuple[int | None, str]:
    """Lines added+removed since the merge base, excluding lockfiles. (None, reason) if unknown."""
    base, info = merge_base(root)
    if base is None:
        return None, info
    return _size(_numstat(root, base)), info


def lane(root: Path, base: str) -> str:
    """`fix` when every commit in base..HEAD has a non-feat type and no `!` (ADR-0009).

    Merge commits are not commits of the change: `git merge main` keeps a fix a fix.
    """
    log = _git(["log", "--no-merges", "--format=%s", f"{base}..HEAD"], root)
    subjects = [s for s in log.stdout.splitlines() if s.strip()]
    if log.returncode != 0 or not subjects:
        return "normal"
    return "fix" if all(FIX_TYPES.match(s) for s in subjects) else "normal"


def _tests_dir(p: Project) -> str:
    """The project's tests directory, `[tool.rumbo].tests`, as a relative POSIX path."""
    return normalise_tests_dir(p.tests_dir)


def _dependency_names(pyproject: str) -> set[str] | None:
    """`[project].dependencies` as normalised names (PEP 503); None when unreadable."""
    try:
        data = tomllib.loads(pyproject)
    except tomllib.TOMLDecodeError:
        return None
    deps = data.get("project", {}).get("dependencies", [])
    if not isinstance(deps, list):
        return None
    names: set[str] = set()
    for dep in deps:
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", str(dep))
        if match:
            names.add(re.sub(r"[-_.]+", "-", match.group(1)).lower())
    return names


def _fix_lane(p: Project, base: str, info: str, rows: list[tuple[int, int, str]]) -> list[Result]:
    """The three extra G4 rules of the fix lane (ADR-0009)."""
    tests_dir = _tests_dir(p)
    tests_touched = [path for added, _, path in rows if added and path.startswith(f"{tests_dir}/")]
    results = [
        Result(
            "fix lane: regression test added",
            bool(tests_touched),
            f"no file under {tests_dir}/ gained lines since {info}",
        )
    ]
    size = _size(rows)
    results.append(
        Result(
            f"fix lane: diff ≤ {FIX_MAX_DIFF_LINES} lines",
            size <= FIX_MAX_DIFF_LINES,
            f"{size} lines vs {info} (lockfiles excluded); a bigger change is a feature",
        )
    )
    # `./`: the pyproject.toml of this project, which may not be the one at the repository root.
    shown = _git(["show", f"{base}:./pyproject.toml"], p.root)
    before = _dependency_names(shown.stdout) if shown.returncode == 0 else None
    after = _dependency_names(_read(p, "pyproject.toml")[0] or "")
    if before is None or after is None:
        results.append(
            Result(
                "fix lane: no new runtime dependency",
                None,
                "could not read [project].dependencies at the merge base and in the worktree",
            )
        )
    else:
        new = sorted(after - before)
        results.append(
            Result(
                "fix lane: no new runtime dependency",
                not new,
                f"added since {info}: {', '.join(new)}",
            )
        )
    return results


def _not_measured(why: str, output: str) -> str:
    """Detail of a G4 check whose verification did not happen (ADR-0018)."""
    return f"environment broken ({why}); {ENV_REPAIR}\n{output}"


def _coverage(p: Project) -> list[Result]:
    """Every requirement has a test; a line that could not be read as one counts as missing."""
    reqs = trace(p.root, p.tests_dir)
    if not reqs:
        return []
    missing = uncovered(reqs)
    untested = [r.full_id for r in missing if not r.malformed]
    unparsed = [r.where for r in missing if r.malformed]
    parts: list[str] = []
    if untested:
        parts.append(f"{len(untested)} without a test: {', '.join(untested[:8])}")
    if unparsed:
        parts.append(f"{len(unparsed)} line(s) could not be parsed: {', '.join(unparsed[:8])}")
    return [
        Result(
            "every spec requirement is referenced by a test or eval case",
            (not missing) if p.at_least("production") else ((not missing) or None),
            "; ".join(parts) + " (see 'rumbo trace')",
        )
    ]


def _g4(p: Project, run: bool) -> list[Result]:
    results: list[Result] = []
    sha, name, at_base = _find_base(p.root)
    base, info = _branch_base(sha, name, at_base)
    current = lane(p.root, base) if base else "normal"
    # `report` moves this result into the heading.
    results.append(Result(f"lane: {current}", True, info))
    if base is None:
        results.append(Result("diff size measured", None, f"could not measure: {info}"))
    else:
        fresh = _untracked(p.root)
        rows = _tracked(p.root, base) + fresh
        size, new = _size(rows), _size(fresh)
        results.append(
            Result(
                f"small diff (≤{MAX_DIFF_LINES} lines, lockfiles excluded)",
                size <= MAX_DIFF_LINES,
                f"{size} lines vs {info}"
                + (f", {new} of them in files not yet added to git" if new else ""),
            )
        )
        if current == "fix":
            results += _fix_lane(p, base, info, rows)
    results += _coverage(p)
    if not run:
        results.append(Result("sensor and rumbo check", None, "skipped (--no-run)"))
        return results
    sensor = p.root / "scripts" / "check_tests_not_weakened.py"
    # The sensor is given the base this gate measured, HEAD included: under `--strict` it
    # refuses a base it found by default when that base is HEAD, which is every run on `main`.
    named = ["--base", sha] if sha else []
    # ADR-0018: the run was requested, so a check that could not be measured fails the gate.
    # The failure is the environment's: the detail hands the repair to the human, not the agent.
    code, out = _run_cmd([sys.executable, str(sensor), "--strict", *named], p.root)
    if code is None:
        results.append(Result("no test weakened", False, _not_measured("could not run", out)))
    else:
        results.append(Result("no test weakened", code == 0, out))
    code, out = _run_cmd(_check_command(p.root), p.root)
    if code is None:
        results.append(Result("rumbo check green", False, _not_measured("could not run", out)))
    elif code == CHECK_ENV:
        results.append(Result("rumbo check green", False, _not_measured("exit 3", out)))
    else:
        results.append(Result("rumbo check green", code == 0, "" if code == 0 else out))
    if p.at_least("production"):
        nightly = (p.root / ".github" / "workflows" / "nightly.yml").exists()
        results.append(Result("nightly workflow present (mutation, evals k=3)", nightly))
    return results


# -------------------------------------------------------------------- G5 Operate
UNRELEASED = re.compile(r"^##\s+\[?Unreleased\]?", re.M | re.I)
PYPI_SECRET = re.compile(r"secrets\.PYPI|PYPI_API_TOKEN|PYPI_TOKEN")


def _dependency_updates(p: Project, ops: bool) -> Result:
    dependabot = (p.root / ".github" / "dependabot.yml").exists() or (
        p.root / "renovate.json"
    ).exists()
    return Result(
        "automatic dependency updates",
        dependabot if ops else (dependabot or None),
        "no .github/dependabot.yml or renovate.json (not generated for prototype)",
    )


def _python_matrix(ci: str) -> int:
    """Entries of the `python` list under the first `matrix:` key of a workflow; 0 without one."""
    lines = ci.splitlines()
    for i, line in enumerate(lines):
        if line.strip() != "matrix:":
            continue
        indent = len(line) - len(line.lstrip())
        for j in range(i + 1, len(lines)):
            row = lines[j]
            if row.strip() and len(row) - len(row.lstrip()) <= indent:
                break  # end of the matrix block
            key = re.match(r"\s*python(?:-version)?:\s*(.*)$", row)
            if not key:
                continue
            value = key.group(1).strip()
            if value.startswith("["):
                return len([x for x in value.strip("[]").split(",") if x.strip()])
            depth = len(row) - len(row.lstrip())
            count = 0
            for item in lines[j + 1 :]:
                if not item.strip():
                    continue
                if len(item) - len(item.lstrip()) <= depth or not item.lstrip().startswith("-"):
                    break
                count += 1
            return count
    return 0


def _python_versions(p: Project, workflows: Workflows, ops: bool) -> Result:
    """A package runs on the Pythons it promises (ADR-0010): two at least, in any workflow.

    ADR-0032: when its minimum is the newest supported Python there is one version to run on,
    and every workflow job runs on one.
    """
    counts = {rel: _python_matrix(text) for rel, text in workflows.texts.items()}
    best = max(counts, key=lambda rel: counts[rel], default="")
    versions = counts.get(best, 0)
    if p.python == SUPPORTED_PYTHONS[-1]:
        found = bool(workflows.texts)
        return Result(
            f"CI runs on Python {p.python}, the newest supported (one version is enough)",
            found if ops else (found or None),
            workflows.missing("runs on any Python"),
        )
    return Result(
        "CI runs on at least two Python versions",
        (versions >= 2) if ops else ((versions >= 2) or None),
        f"found a matrix with {versions} 'python' entries in {best or WORKFLOWS + '/'}",
    )


def _g5_release(p: Project, ops: bool, workflows: Workflows) -> list[Result]:
    """Library and cli (ADR-0010): a changelog, Trusted Publishing and a Python matrix."""
    changelog, problem = _read(p, "CHANGELOG.md")
    unreleased = changelog is not None and bool(UNRELEASED.search(changelog))
    results = [
        Result(
            "CHANGELOG.md with an 'Unreleased' section",
            unreleased if ops else (unreleased or None),
            problem
            or ("missing CHANGELOG.md" if changelog is None else "no '## [Unreleased]' heading"),
        )
    ]
    release, problem = _read(p, ".github/workflows/release.yml")
    trusted = release is not None and "id-token: write" in release
    no_secret = release is not None and not PYPI_SECRET.search(release)
    results.append(
        Result(
            "release workflow publishes with Trusted Publishing (no PyPI token secret)",
            (trusted and no_secret) if ops else ((trusted and no_secret) or None),
            problem
            or (
                "missing .github/workflows/release.yml"
                if release is None
                else "needs 'id-token: write' and no PYPI secret (register the Trusted Publisher)"
            ),
        )
    )
    results.append(_python_versions(p, workflows, ops))
    return results


def _g5(p: Project, _run: bool) -> list[Result]:
    ops = p.at_least("mvp")
    workflows = Workflows.of(p)
    results: list[Result]
    if p.kind in RELEASED_KINDS:
        results = _g5_release(p, ops, workflows)
    else:
        results = []
        if p.kind in RUNBOOK_KINDS:
            results.append(_doc(p, "docs/ops/runbook.md", "Runbook", required=ops))
        if p.kind in SLO_KINDS:
            results.append(_doc(p, "docs/ops/slos.md", "SLOs", required=ops))
        dockerfile = (p.root / "infra" / "Dockerfile").exists()
        # Recommended, never required, for a pipeline: it may run where no image is built.
        results.append(
            Result("Dockerfile in infra/", (dockerfile or None) if p.kind == "data" else dockerfile)
        )
    results.append(_dependency_updates(p, ops))
    if p.kind == "data":
        n, unreadable = _datasets(p)
        results.append(
            Result(
                "datasets present in evals/datasets/",
                (n > 0) if ops else ((n > 0) or None),
                "no case beyond the examples (a case is an object with an 'input'; cases tagged "
                f"'example' do not count){unreadable}",
            )
        )
    if p.llm:
        app = p.root / p.package_path / "app"
        instrumented = (app / "budget.py").exists() and (app / "telemetry.py").exists()
        entry = _read(p, f"{p.package_path}/app/entrypoint.py")[0] or ""
        wired = "Meter(" in entry and "span(" in entry
        results.append(
            Result(
                "per-request budget and GenAI telemetry wired into the entry point",
                (instrumented and wired) if ops else ((instrumented and wired) or None),
                "app/budget.py + app/telemetry.py must exist and entrypoint.answer must open a "
                "span and a Meter (see /rumbo-o)",
            )
        )
    if p.at_least("production"):
        sbom = workflows.first(lambda text: "sbom" in text.lower())
        results.append(
            Result(
                "SBOM generated in CI",
                bool(sbom),
                f"in {sbom}" if sbom else workflows.missing("has an SBOM step"),
            )
        )
    return results


GATES: dict[str, Gate] = {
    "G1": Gate(
        "G1",
        "R — Recognize",
        (
            Item("Brief written first by the human"),
            Item("Problem validated with ≥5 users or real data"),
            Item("Success metric and abandonment criterion written down"),
            Item("Prototypes only in spikes/"),
            Item(
                "LLM products: feasibility test with 20–30 real questions run by hand",
                llm_only=True,
            ),
        ),
        (_g1,),
    ),
    "G2": Gate(
        "G2",
        "U — Unify",
        (
            Item("PRD with explicit non-goals", "mvp"),
            Item(
                "Specs with EARS patterns and executable acceptance criteria, signed by the human",
                "mvp",
            ),
            Item("Prototype: a criteria list per feature is enough", max_rigor="mvp"),
            Item(
                "Non-functional requirements with numbers (p95, cost per request, availability)",
                "mvp",
            ),
            Item("Every requirement has a planned test or eval"),
            Item("Eval plan and dataset of real questions", "mvp", llm_only=True),
            Item("Requirement → test traceability for every feature", "production"),
        ),
        (_g2,),
    ),
    "G3": Gate(
        "G3",
        "M — Model",
        (
            Item("ADR 'workflow vs. agent' decided by the human", "mvp", llm_only=True),
            Item("Import-linter contracts enforced in CI", arch_only=True),
            Item("C4 levels 1–2", "mvp"),
            Item(
                "Threat model with lethal trifecta and OWASP LLM/Agentic reviewed by the human",
                "mvp",
            ),
            Item("Tools typed and least-privilege", llm_only=True),
            Item("Every significant ADR has a fitness function", "production"),
        ),
        (_g3,),
    ),
    "G4": Gate(
        "G4",
        "B — Build with ANCLA (per PR)",
        (
            Item("Failing test written first and reviewed by the human"),
            Item("Plan approved before coding"),
            Item("CI green (with smoke evals when there is an LLM)"),
            Item("No test deleted or weakened"),
            Item("Small diff; structural and behavioral commits separated"),
            Item("New dependencies verified (rumbo verify-dep) and approved"),
            Item("Second review by a clean session or another model", "mvp"),
            Item(
                "Explanation test: the human explains the change in 3 sentences, "
                "without reading the summary",
                "mvp",
            ),
            Item("Two reviewers on critical areas; mental models documented", "production"),
        ),
        (_g4,),
    ),
    "G5": Gate(
        "G5",
        "O — Operate, Observe, Optimize",
        (
            Item("Prototype: runs locally, logs are enough", max_rigor="mvp"),
            Item("Rollback rehearsed", "mvp"),
            Item("SLOs and alerts active", "mvp", kinds=SLO_KINDS),
            Item("GenAI traces with cost", "mvp", llm_only=True),
            Item("Cost limits configured", "mvp", llm_only=True),
            Item("Runbook with one entry per failure mode", "mvp", kinds=RUNBOOK_KINDS),
            Item("Weekly trace-review ritual scheduled", "mvp", llm_only=True),
            Item("Automatic dependency updates", "mvp"),
            Item(
                "Canary or feature flags per cohort; SLOs with error budget",
                "production",
                kinds=SLO_KINDS,
            ),
        ),
        (_g5,),
    ),
}


def evaluate(code: str, project: Project, run: bool = True) -> tuple[Gate, list[Result]]:
    gate = GATES[code.upper()]
    results: list[Result] = []
    for check in gate.checks:
        results.extend(check(project, run))
    return gate, results


def report(gate: Gate, results: list[Result], project: Project) -> tuple[str, bool]:
    failed = [r for r in results if r.ok is False]
    unmeasured = [r for r in results if r.ok is None]
    head = "🛑" if failed else "✅"
    heading = f"{head} Gate {gate.code} · {gate.phase} · rigor {project.rigor}"
    lane_result = next((r for r in results if r.description.startswith("lane: ")), None)
    if lane_result is not None:
        heading += f" · {lane_result.description}"
    lines = [heading, "", "Automatic checks (sensors):"]
    for r in results:
        if r is lane_result:
            continue
        mark = "✅" if r.ok else ("⚠️ " if r.ok is None else "❌")
        lines.append(f"  {mark} {r.description}")
        if r.detail and not r.ok:
            lines += [f"       {d}" for d in r.detail.splitlines()]
    lines += ["", "Human checklist (signed by the human, never by the agent):"]
    lines += [f"  [ ] {item}" for item in gate.checklist(project)]
    lines.append("")
    if failed:
        lines.append(f"Automatic checks: {len(failed)} FAILED. The gate cannot pass yet.")
    elif unmeasured:
        lines.append(
            f"Automatic checks: green, but {len(unmeasured)} could not be measured or are "
            "recommendations. GATE NOT SIGNED yet: the human ticks the checklist above."
        )
    else:
        lines.append(
            "Automatic checks: green. GATE NOT SIGNED yet: the human ticks the checklist above."
        )
    lines.append(f'Record the signature with: rumbo decision "{gate.code} signed" --why "..."')
    return "\n".join(lines), not failed
