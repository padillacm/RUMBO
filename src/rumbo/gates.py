"""Gates G1–G5: automatic checks plus the human checklist, scaled by rigor.

The automatic checks are cheap sensors over the artifacts; they never replace the human
signature. A gate passes only when every automatic check is green **and** the human ticks
the manual items. ``ok=None`` marks an informational result (could not be measured, or a
recommendation for the current rigor): it is shown but does not fail the gate.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rumbo.metrics import eval_cases
from rumbo.project import Project, is_template, numbered_documents, section
from rumbo.trace import spec_id, trace, uncovered

MAX_DIFF_LINES = 300
FIX_MAX_DIFF_LINES = 150  # ADR-0009: the fix lane allows half the normal diff
# ADR-0009: a PR is in the fix lane when every commit since the merge base has one of these
# types and none carries the breaking marker `!` before the colon.
FIX_TYPES = re.compile(r"^(fix|test|refactor|chore|docs|ci|perf|style|build)(\([^)]*\))?:")
BASES = ("origin/main", "origin/master", "origin/HEAD", "@{upstream}", "main", "master")
CHECK_ENV = 3  # scripts/rumbo_check.py: the verification environment is broken (ADR-0006)
# ADR-0018: a gate cannot be green on a verification that did not happen.
ENV_REPAIR = "this is the environment, not the code: the human repairs it and runs the gate again"
# ADR-0010: what each kind has in operation. `_g5` and the G5 checklist read the same tuples,
# so the human is never asked to sign a document the automatic checks do not expect.
RELEASED_KINDS = ("library", "cli")  # published, not operated
RUNBOOK_KINDS = ("service", "data")
SLO_KINDS = ("service",)
# A PRD table row whose first cell is a feature id: `| F-0001 | ... |`.
PRD_FEATURE = re.compile(r"^\s*\|\s*[*_`\[]*(F-\d{4})\b", re.M)
MIN_EVAL_CASES = {"prototype": 20, "mvp": 50, "production": 100}
EARS = re.compile(r"\bshall\b|\bdeber[áa]n?\b", re.I)
PLACEHOLDER = re.compile(r"…|\.\.\.|<[^>]+>")
LOCKFILES = ("uv.lock", "poetry.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock")


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


def _doc(p: Project, rel: str, label: str, required: bool = True) -> Result:
    text = p.read(rel)
    if text is None:
        return Result(f"{label} exists", False if required else None, f"missing {rel}")
    complete = not is_template(text)
    detail = "" if complete else f"{rel} still has the template mark on its first line"
    return Result(f"{label} complete", complete if required else (complete or None), detail)


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
def _has_real_ears(text: str) -> bool:
    body = section(text, "Requirements")
    return any(EARS.search(line) and not PLACEHOLDER.search(line) for line in body.splitlines())


def _has_real_contracts(text: str) -> bool:
    body = section(text, "Data contracts and ownership")
    return any(
        line.strip().startswith("-") and not PLACEHOLDER.search(line) for line in body.splitlines()
    )


def _prd_features(p: Project, specs: list[Path]) -> list[Result]:
    """Reported at every rigor, never required: the PRD features that have no spec yet.

    No row while the PRD is a template or lists no `F-NNNN` id in a table.
    """
    prd = p.read("docs/product/prd.md")
    if prd is None or is_template(prd):
        return []
    features = list(dict.fromkeys(PRD_FEATURE.findall(prd)))
    if not features:
        return []
    with_spec = {spec_id(spec) for spec in specs}
    missing = [feature for feature in features if feature not in with_spec]
    if not missing:
        return [Result("every PRD feature has a spec", True)]
    listed = ", ".join(missing)
    return [
        Result(
            f"PRD features without a spec: {listed} (write each when the feature is next touched)",
            None,
        )
    ]


def _g2(p: Project, _run: bool) -> list[Result]:
    results = [_doc(p, "docs/product/prd.md", "PRD", required=p.at_least("mvp"))]
    specs = numbered_documents(p.root / "specs")
    results.append(Result("at least one spec in specs/", bool(specs), "use 'rumbo new spec'"))
    results += _prd_features(p, specs)
    for spec in specs:
        text = spec.read_text(encoding="utf-8")
        rel = spec.relative_to(p.root).as_posix()
        results.append(
            Result(f"{rel}: complete", not is_template(text), "still has the template mark")
        )
        if p.at_least("mvp"):
            results.append(
                Result(
                    f"{rel}: EARS requirements",
                    _has_real_ears(text),
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
        contracts = _has_real_contracts(text)
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
        n = eval_cases(p.root)
        results.append(
            Result(
                f"≥{minimum} real cases in evals/datasets/ (rigor {p.rigor})",
                n >= minimum,
                f"found {n} (cases tagged 'example' do not count)",
            )
        )
        results.append(_doc(p, "docs/evals/plan.md", "Eval plan", required=p.at_least("mvp")))
    return results


# ---------------------------------------------------------------------- G3 Model
def _contracts(p: Project) -> int:
    text = p.read("pyproject.toml") or ""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return 0
    contracts = data.get("tool", {}).get("importlinter", {}).get("contracts", [])
    return len(contracts) if isinstance(contracts, list) else 0


def _g3(p: Project, _run: bool) -> list[Result]:
    adrs = numbered_documents(p.root / "docs" / "adr")
    results = [Result("at least one ADR", bool(adrs), "use 'rumbo new adr'")]
    workflow_adr = False
    for adr in adrs:
        text = adr.read_text(encoding="utf-8")
        rel = adr.relative_to(p.root).as_posix()
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
    results.append(_doc(p, "docs/security/threat-model.md", "Threat model", required=tm_required))
    tm = p.read("docs/security/threat-model.md") or ""
    # Section content counts only once the human removed the template mark.
    trifecta = not is_template(tm) and bool(section(tm, "Lethal trifecta"))
    owasp = not is_template(tm) and bool(section(tm, "OWASP"))
    results.append(
        Result(
            "threat model: lethal trifecta assessed",
            trifecta if tm_required else (trifecta or None),
            "section 'Lethal trifecta' is empty",
        )
    )
    results.append(
        Result(
            "threat model: OWASP LLM/Agentic checklist filled",
            owasp if (tm_required and p.llm) else (owasp or None),
            "section 'OWASP' is empty",
        )
    )
    results.append(_doc(p, "docs/architecture/c4.md", "C4 levels 1–2", required=tm_required))
    c4 = p.read("docs/architecture/c4.md") or ""
    results.append(
        Result(
            "C4 has Mermaid diagrams",
            (not is_template(c4) and c4.count("```mermaid") >= 2)
            if tm_required
            else ((not is_template(c4) and c4.count("```mermaid") >= 2) or None),
            "two diagrams, in a document whose template mark was removed",
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
    ci = p.read(".github/workflows/ci.yml") or ""
    results.append(
        Result(
            "CI runs rumbo check",
            "scripts/rumbo_check.py" in ci,
            "no workflow step runs scripts/rumbo_check.py (ADR-0006)",
        )
    )
    return results


# ------------------------------------------------------------- G4 Build (per PR)
def _run_cmd(cmd: list[str], root: Path) -> tuple[int | None, str]:
    """(exit code, last lines of output); the code is None when the program is not installed."""
    try:
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None, f"'{cmd[0]}' is not installed; could not run {' '.join(cmd)}"
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode, "\n".join(lines[-15:])


def _check_command(root: Path) -> list[str]:
    """`scripts/rumbo_check.py` (ADR-0006); `make check` for a project not yet updated."""
    if (root / "scripts" / "rumbo_check.py").is_file():
        return [sys.executable, "scripts/rumbo_check.py"]
    return ["make", "check"]


def _git(args: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def merge_base(root: Path) -> tuple[str | None, str]:
    """(merge-base sha, base name) of the branch work; (None, reason) when there is none."""
    head = _git(["rev-parse", "HEAD"], root)
    if head.returncode != 0:
        return None, "not a git repository or no commits yet"
    for base in BASES:
        mb = _git(["merge-base", "HEAD", base], root)
        if mb.returncode != 0:
            continue
        sha = mb.stdout.strip()
        if sha == head.stdout.strip():
            # HEAD is the base (or behind it): there is no branch work to measure, and
            # counting only the uncommitted changes would pass a huge commit on main.
            return None, f"HEAD is already on '{base}': commit on a branch to measure the diff"
        return sha, base
    return None, "no base branch found (origin/main, main, @{upstream}...)"


def _numstat(root: Path, base: str) -> list[tuple[int, int, str]]:
    """(added, removed, path) per text file changed since `base`, lockfiles excluded."""
    excludes = [f":(exclude){lock}" for lock in LOCKFILES]
    diff = _git(["diff", "--numstat", base, "--", ".", *excludes], root)
    rows: list[tuple[int, int, str]] = []
    for line in diff.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def diff_size(root: Path) -> tuple[int | None, str]:
    """Lines added+removed since the merge base, excluding lockfiles. (None, reason) if unknown."""
    base, info = merge_base(root)
    if base is None:
        return None, info
    return sum(added + removed for added, removed, _ in _numstat(root, base)), info


def lane(root: Path, base: str) -> str:
    """`fix` when every commit in base..HEAD has a non-feat type and no `!` (ADR-0009)."""
    log = _git(["log", "--format=%s", f"{base}..HEAD"], root)
    subjects = [s for s in log.stdout.splitlines() if s.strip()]
    if log.returncode != 0 or not subjects:
        return "normal"
    return "fix" if all(FIX_TYPES.match(s) for s in subjects) else "normal"


def _tests_dir(p: Project) -> str:
    """`[tool.rumbo].tests` in pyproject.toml, else `tests`."""
    try:
        data = tomllib.loads(p.read("pyproject.toml") or "")
    except tomllib.TOMLDecodeError:
        return "tests"
    rumbo = data.get("tool", {}).get("rumbo", {})
    value = rumbo.get("tests") if isinstance(rumbo, dict) else None
    return (str(value).replace("\\", "/").strip("/") or "tests") if value else "tests"


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


def _fix_lane(p: Project, base: str, info: str) -> list[Result]:
    """The three extra G4 rules of the fix lane (ADR-0009)."""
    rows = _numstat(p.root, base)
    tests_dir = _tests_dir(p)
    tests_touched = [path for added, _, path in rows if added and path.startswith(f"{tests_dir}/")]
    results = [
        Result(
            "fix lane: regression test added",
            bool(tests_touched),
            f"no file under {tests_dir}/ gained lines since {info}",
        )
    ]
    size = sum(added + removed for added, removed, _ in rows)
    results.append(
        Result(
            f"fix lane: diff ≤ {FIX_MAX_DIFF_LINES} lines",
            size <= FIX_MAX_DIFF_LINES,
            f"{size} lines vs {info} (lockfiles excluded); a bigger change is a feature",
        )
    )
    shown = _git(["show", f"{base}:pyproject.toml"], p.root)
    before = _dependency_names(shown.stdout) if shown.returncode == 0 else None
    after = _dependency_names(p.read("pyproject.toml") or "")
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


def _g4(p: Project, run: bool) -> list[Result]:
    results: list[Result] = []
    base, info = merge_base(p.root)
    current = lane(p.root, base) if base else "normal"
    # `report` moves this result into the heading.
    results.append(Result(f"lane: {current}", True, info))
    if base is None:
        results.append(Result("diff size measured", None, f"could not measure: {info}"))
    else:
        size = sum(added + removed for added, removed, _ in _numstat(p.root, base))
        results.append(
            Result(
                f"small diff (≤{MAX_DIFF_LINES} lines, lockfiles excluded)",
                size <= MAX_DIFF_LINES,
                f"{size} lines vs {info}",
            )
        )
        if current == "fix":
            results += _fix_lane(p, base, info)
    reqs = trace(p.root, p.tests_dir)
    if reqs:
        missing = uncovered(reqs)
        results.append(
            Result(
                "every spec requirement is referenced by a test or eval case",
                (not missing) if p.at_least("production") else ((not missing) or None),
                f"{len(missing)} without a test: {', '.join(r.full_id for r in missing[:8])}"
                " (see 'rumbo trace')",
            )
        )
    if not run:
        results.append(Result("sensor and rumbo check", None, "skipped (--no-run)"))
        return results
    sensor = p.root / "scripts" / "check_tests_not_weakened.py"
    # ADR-0018: the run was requested, so a check that could not be measured fails the gate.
    # The failure is the environment's: the detail hands the repair to the human, not the agent.
    code, out = _run_cmd([sys.executable, str(sensor), "--strict"], p.root)
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


def _g5_release(p: Project, ops: bool) -> list[Result]:
    """Library and cli (ADR-0010): a changelog, Trusted Publishing and a Python matrix."""
    changelog = p.read("CHANGELOG.md")
    unreleased = changelog is not None and bool(UNRELEASED.search(changelog))
    results = [
        Result(
            "CHANGELOG.md with an 'Unreleased' section",
            unreleased if ops else (unreleased or None),
            "missing CHANGELOG.md" if changelog is None else "no '## [Unreleased]' heading",
        )
    ]
    release = p.read(".github/workflows/release.yml")
    trusted = release is not None and "id-token: write" in release
    no_secret = release is not None and not PYPI_SECRET.search(release)
    results.append(
        Result(
            "release workflow publishes with Trusted Publishing (no PyPI token secret)",
            (trusted and no_secret) if ops else ((trusted and no_secret) or None),
            "missing .github/workflows/release.yml"
            if release is None
            else "needs 'id-token: write' and no PYPI secret (register the Trusted Publisher)",
        )
    )
    versions = _python_matrix(p.read(".github/workflows/ci.yml") or "")
    results.append(
        Result(
            "CI runs on at least two Python versions",
            (versions >= 2) if ops else ((versions >= 2) or None),
            f"found a matrix with {versions} 'python' entries in .github/workflows/ci.yml",
        )
    )
    return results


def _g5(p: Project, _run: bool) -> list[Result]:
    ops = p.at_least("mvp")
    results: list[Result]
    if p.kind in RELEASED_KINDS:
        results = _g5_release(p, ops)
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
        n = eval_cases(p.root)
        results.append(
            Result(
                "datasets present in evals/datasets/",
                (n > 0) if ops else ((n > 0) or None),
                "no case beyond the examples (cases tagged 'example' do not count)",
            )
        )
    if p.llm:
        app = p.root / p.package_path / "app"
        instrumented = (app / "budget.py").exists() and (app / "telemetry.py").exists()
        entry = p.read(f"{p.package_path}/app/entrypoint.py") or ""
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
        ci = p.read(".github/workflows/ci.yml") or ""
        results.append(Result("SBOM generated in CI", "sbom" in ci.lower()))
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
