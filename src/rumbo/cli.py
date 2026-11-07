"""RUMBO command-line interface.

rumbo init                      mount the harness (Part A) in a project
rumbo update                    refresh the files the framework owns
rumbo new adr|spec|postmortem   create a document from its template
rumbo decision "title"          log a human decision in docs/decisions.md
rumbo gate G1..G5               automatic checks + human checklist of a gate
rumbo doctor                    verify the harness is complete and current
rumbo check [STEP ...]          run the verification steps of [tool.rumbo.checks]
rumbo check-tests               anti-cheat sensor (tests not weakened)
rumbo verify-dep NAME           verify a package on PyPI before adding it
rumbo tour [BASE]               guided, pedagogical review of a git range
rumbo trace                     requirement → test traceability table
rumbo eval add SUITE ...        turn a failure into an eval case
rumbo metrics                   process metrics from git, decisions, specs and evals
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import http.client
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from rumbo import __version__
from rumbo.gates import GATES, evaluate, report
from rumbo.metrics import collect
from rumbo.metrics import render as render_metrics
from rumbo.project import RIGOR_LEVELS, Project, slug
from rumbo.scaffold import (
    OTHER_PROVIDER,
    PROVIDERS,
    Config,
    install,
    plan_files,
    scaffold,
    to_package,
    update,
    update_diff,
    valid_name,
    valid_package,
    valid_sdk_module,
    valid_sdk_package,
)
from rumbo.sensors import check, check_tests_not_weakened
from rumbo.tour import load_commits, render_markdown, resolve_base
from rumbo.trace import render as render_trace
from rumbo.trace import trace, uncovered

PROVIDER_CHOICES = [*PROVIDERS, OTHER_PROVIDER]
USAGE_ERROR = 2


def _err(message: str) -> int:
    print(f"Error: {message}", file=sys.stderr)
    return USAGE_ERROR


# ------------------------------------------------------------------------- init
def _ask(
    prompt: str, default: str = "", valid: Any = None, choices: list[str] | None = None
) -> str:
    """Asks until the answer is valid. ``valid`` is a predicate; ``choices`` a closed list."""
    suffix = f" [{default}]" if default else ""
    hint = f" ({'/'.join(choices)})" if choices else ""
    while True:
        answer = input(f"{prompt}{hint}{suffix}: ").strip() or default
        if choices and answer not in choices:
            print(f"  please answer one of: {', '.join(choices)}")
            continue
        if valid and not valid(answer):
            print("  invalid value, try again")
            continue
        return answer


def _yes(answer: str) -> bool:
    return answer.lower() in ("y", "yes", "s", "si", "sí")


def _config_from_args(args: argparse.Namespace) -> Config:
    interactive = sys.stdin.isatty() and not args.yes
    if not interactive and not args.yes:
        raise ValueError("stdin is not a terminal: pass --yes with explicit flags (see --help)")

    name, package, llm = args.name, args.package, args.llm
    provider, rigor, case = args.provider, args.rigor, args.case
    sdk_package, sdk_module, sdk_env_key = args.sdk_package, args.sdk_module, args.sdk_env_key

    if interactive:
        print("A0 · RUMBO initial questions (Enter accepts the value in brackets)\n")
        name = name or _ask("1. Project name", Path(args.dir).resolve().name, valid_name)
        package = package or _ask("   Python package (snake_case)", to_package(name), valid_package)
        if llm is None:
            llm = _yes(_ask("2. Is it an LLM product?", "no", choices=["yes", "no"]))
        if llm and not provider:
            provider = _ask("   Model provider", "anthropic", choices=PROVIDER_CHOICES)
        if llm and provider == OTHER_PROVIDER:
            sdk_package = sdk_package or _ask("   SDK package on PyPI", valid=valid_sdk_package)
            sdk_module = sdk_module or _ask("   SDK importable module", valid=valid_sdk_module)
            sdk_env_key = sdk_env_key or _ask("   Env var holding the API key", valid=bool)
        if not rigor:
            rigor = _ask("3. Rigor level", "mvp", choices=list(RIGOR_LEVELS))
        if case is None:
            case = _ask("4. Is there a concrete use case? (describe it or leave empty)", "")
    else:
        if llm is None:
            raise ValueError("with --yes you must pass --llm or --no-llm (A0 question 2)")
        if not rigor:
            raise ValueError("with --yes you must pass --rigor (A0 question 3)")
        name = name or Path(args.dir).resolve().name
        package = package or to_package(name)
    return Config(
        name=name,
        package=package,
        llm=bool(llm),
        provider=provider or "",
        rigor=rigor,
        case=case or "",
        sdk_package=sdk_package or "",
        sdk_module=sdk_module or "",
        sdk_env_key=sdk_env_key or "",
    )


def cmd_init(args: argparse.Namespace) -> int:
    try:
        cfg = _config_from_args(args)
    except ValueError as exc:
        return _err(str(exc))
    dest = Path(args.dir).resolve()
    print(f"\nMounting the RUMBO harness in {dest}")
    print(
        f"  package={cfg.package} llm={cfg.llm} provider={cfg.provider or '-'} rigor={cfg.rigor}\n"
    )
    if args.force:
        print(
            "--force: existing files are overwritten (a .bak copy is kept); "
            "pyproject.toml and .gitignore are merged, never replaced.\n"
        )
    interactive = sys.stdin.isatty() and not args.yes
    existing = [p.name for p in dest.iterdir() if p.name != ".git"] if dest.is_dir() else []
    if interactive and existing and not args.force:
        shown = ", ".join(sorted(existing)[:6]) + (", ..." if len(existing) > 6 else "")
        answer = _ask(
            f"This directory already has {len(existing)} entries ({shown}). Existing files are "
            "kept untouched (pyproject.toml and .gitignore are merged). Continue?",
            "yes",
            choices=["yes", "no"],
        )
        if answer != "yes":
            print("Nothing written.")
            return USAGE_ERROR
    rep = scaffold(cfg, dest, force=args.force)
    if not args.no_install:
        install(cfg, dest, rep)
    print("\n" + rep.summary())
    if args.no_install:
        print(
            "\nNext steps (--no-install):\n"
            "  1. uv add --dev ruff pyright pytest pytest-cov hypothesis import-linter "
            "pre-commit pip-audit bandit mutmut"
            + (
                "\n     uv add pydantic fastapi httpx uvicorn"
                + (f" {cfg.sdk_package}" if cfg.sdk_package else "")
                if cfg.llm
                else ""
            )
            + "\n  2. uv run pre-commit autoupdate && uv run pre-commit install\n"
            "  3. uv run python scripts/rumbo_check.py    # must be green\n"
        )
    else:
        print(
            "\nNext steps (A8):\n"
            "  1. uv run python scripts/rumbo_check.py    # must be green\n"
            "  2. git add -A && git commit -m 'chore: initial RUMBO harness'\n"
            '  3. Open Claude Code and say: "Read AGENTS.md and RUMBO.md; let\'s start phase R".'
        )
    print("  See what was created with: git status")
    return 1 if any(rc != 0 for _, rc in rep.commands) else 0


# ----------------------------------------------------------------------- update
def cmd_update(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    try:
        project = Project.load(root)
    except (FileNotFoundError, ValueError) as exc:
        return _err(str(exc))
    if args.diff:
        print(update_diff(project, root))
        return 0
    rep = update(project, root)
    print(rep.summary())
    print(f"\nHarness version is now {__version__}. Review the diff with: git diff")
    return 0


# -------------------------------------------------------------------------- new
NEW_KINDS = {
    "adr": ("docs/adr", "docs/adr/0000-template.md", "ADR"),
    "spec": ("specs", "specs/0000-template.md", "F"),
    "postmortem": ("docs/postmortems", "docs/postmortems/0000-template.md", ""),
}


def _next_number(folder: Path) -> int:
    numbers = [int(m.group(1)) for p in folder.glob("*.md") if (m := re.match(r"^(\d+)-", p.name))]
    return max(numbers, default=0) + 1


def cmd_new(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    folder_rel, template_rel, prefix = NEW_KINDS[args.kind]
    template = root / template_rel
    if not template.exists():
        return _err(f"missing template {template_rel}; did you run 'rumbo init'?")
    if not args.title.strip():
        return _err("the title cannot be empty")
    text = template.read_text(encoding="utf-8")
    today = dt.date.today().isoformat()
    folder = root / folder_rel
    title = args.title.strip()
    if args.kind == "postmortem":
        target = folder / f"{today}-{slug(title)}.md"
        heading = f"# Postmortem: {title} ({today})"
    else:
        n = _next_number(folder)
        target = folder / f"{n:04d}-{slug(title)}.md"
        heading = f"# {prefix}-{n:04d}: {title}"
        text = text.replace("Date: YYYY-MM-DD", f"Date: {today}")
    text = re.sub(r"^# .*$", lambda _m: heading, text, count=1, flags=re.M)
    if target.exists():
        return _err(f"{target.relative_to(root)} already exists")
    twins = [p.name for p in folder.glob(f"*-{slug(title)}.md")]
    target.write_text(text, encoding="utf-8")
    print(f"Created {target.relative_to(root).as_posix()}")
    if twins:
        print(f"WARNING: a document with the same title already exists: {', '.join(twins)}")
    return 0


# --------------------------------------------------------------------- decision
def cmd_decision(args: argparse.Namespace) -> int:
    path = Path(args.dir).resolve() / "docs" / "decisions.md"
    if not path.exists():
        return _err("missing docs/decisions.md; did you run 'rumbo init'?")
    today = dt.date.today().isoformat()
    entry = f"\n## {today} — {args.title}\n- Decision: {args.title}\n"
    if args.why:
        entry += f"- Why: {args.why}\n"
    entry += f"- Decided by: {args.who}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)
    print(f"Logged in docs/decisions.md ({today}).")
    return 0


# ------------------------------------------------------------------------- gate
def cmd_gate(args: argparse.Namespace) -> int:
    try:
        project = Project.load(Path(args.dir).resolve())
    except (FileNotFoundError, ValueError) as exc:
        return _err(str(exc))
    gate, results = evaluate(args.code, project, run=not args.no_run)
    text, ok = report(gate, results, project)
    print(text)
    return 0 if ok else 1


# ----------------------------------------------------------------------- doctor
def _declared_human(root: Path) -> bool:
    """`git config rumbo.human` is true in this clone (set by `rumbo init`, ADR-0008)."""
    try:
        proc = subprocess.run(
            ["git", "config", "--get", "rumbo.human"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def cmd_doctor(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    try:
        project = Project.load(root)
        cfg = Config.from_project(project)
    except (FileNotFoundError, ValueError) as exc:
        return _err(str(exc))
    problems: list[str] = []
    expected = list(plan_files(cfg))
    missing = [rel for rel in expected if not (root / rel).exists()]
    problems += [f"missing {rel}" for rel in missing]
    agents = project.read("AGENTS.md") or ""
    n_lines = len(agents.splitlines())
    if n_lines > 100:
        problems.append(f"AGENTS.md has {n_lines} lines (>100): it is a map, not a manual")
    if not (root / "uv.lock").exists():
        problems.append(
            "missing uv.lock (CI runs 'uv sync --locked'): run the 'uv add' lines rumbo init "
            "printed (or 'rumbo init --force' without --no-install), then 'uv lock'"
        )
    warnings: list[str] = []
    codeowners = project.read(".github/CODEOWNERS") or ""
    if "@your-handle" in codeowners:
        warnings.append(".github/CODEOWNERS still has the @your-handle placeholder")
    security = project.read("SECURITY.md") or ""
    if "<security contact" in security:
        warnings.append("SECURITY.md still has the <security contact> placeholder")
    if (root / ".git").exists() and not _declared_human(root):
        warnings.append(
            "rumbo.human is not set in this clone: the git hooks treat your commits as an "
            "agent's (ADR-0008); run 'git config rumbo.human true'"
        )
    for warning in warnings:
        print(f"⚠️  {warning}")
    if project.harness_version and project.harness_version != __version__:
        print(
            f"⚠️  harness version {project.harness_version} differs from the CLI ({__version__}): "
            "run 'rumbo update'"
        )
    for problem in problems:
        print(f"❌ {problem}")
    if not problems:
        print(
            f"✅ Harness complete (package={project.package}, llm={project.llm}, "
            f"provider={project.provider or '-'}, rigor={project.rigor})."
        )
        return 0
    return 1


# ------------------------------------------------------------------- verify-dep
POPULAR_PACKAGES = (
    "requests",
    "numpy",
    "pandas",
    "scipy",
    "matplotlib",
    "django",
    "flask",
    "fastapi",
    "pydantic",
    "httpx",
    "aiohttp",
    "sqlalchemy",
    "pytest",
    "boto3",
    "botocore",
    "urllib3",
    "certifi",
    "setuptools",
    "pip",
    "wheel",
    "six",
    "python-dateutil",
    "pyyaml",
    "click",
    "rich",
    "typer",
    "jinja2",
    "cryptography",
    "pillow",
    "scikit-learn",
    "torch",
    "tensorflow",
    "transformers",
    "openai",
    "anthropic",
    "langchain",
    "langgraph",
    "tqdm",
    "attrs",
    "packaging",
    "idna",
    "charset-normalizer",
    "colorama",
    "psycopg2",
    "redis",
    "celery",
    "uvicorn",
    "starlette",
    "httpcore",
    "anyio",
    "pyarrow",
    "polars",
    "duckdb",
    "beautifulsoup4",
    "lxml",
    "markupsafe",
)


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _http_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
    data = json.dumps(payload).encode() if payload is not None else None
    if not url.startswith("https://"):
        raise ValueError("only https URLs are allowed")
    request = urllib.request.Request(  # noqa: S310 - https only, checked above
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:  # noqa: S310 - https only
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def pypi_info(name: str) -> dict[str, Any] | None:
    return _http_json(f"https://pypi.org/pypi/{urllib.parse.quote(name, safe='')}/json")


def osv_vulnerabilities(name: str) -> int | None:
    try:
        data = _http_json(
            "https://api.osv.dev/v1/query", {"package": {"name": name, "ecosystem": "PyPI"}}
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    return len(data.get("vulns", [])) if data else 0


NETWORK_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    OSError,
    ValueError,
    json.JSONDecodeError,
    http.client.HTTPException,
)


def cmd_check(args: argparse.Namespace) -> int:
    argv = [*args.steps, "--dir", args.dir]
    if args.brief:
        argv.append("--brief")
    if args.keep_going:
        argv.append("--keep-going")
    if args.base:
        argv.extend(["--base", args.base])
    return check.main(argv)


def cmd_verify_dep(args: argparse.Namespace) -> int:
    code = 0
    for name in args.packages:
        try:
            data = pypi_info(name)
        except NETWORK_ERRORS as exc:
            print(f"❓ {name}: could not query PyPI ({exc}). Verify by hand.")
            code = max(code, 2)
            continue
        if data is None:
            print(f"❌ {name}: does NOT exist on PyPI. Hallucinated name? (slopsquatting)")
            code = 1
            continue
        info: dict[str, Any] = data.get("info") or {}
        releases: dict[str, list[dict[str, Any]]] = data.get("releases") or {}
        canonical = str(info.get("name") or name)
        dates = [
            f["upload_time_iso_8601"][:10]
            for files in releases.values()
            for f in files
            if isinstance(f, dict) and f.get("upload_time_iso_8601")
        ]
        first = min(dates) if dates else None
        age = (dt.date.today() - dt.date.fromisoformat(first)).days if first else None
        urls = info.get("project_urls") or {}
        repo = next(
            (u for k, u in urls.items() if re.search(r"source|repo|code|github|home", k, re.I)),
            info.get("home_page") or "",
        )
        print(f"📦 {canonical} {info.get('version', '?')} — {info.get('summary') or ''}")
        print(f"   first release: {first or '?'} ({age if age is not None else '?'} days ago)")
        print(f"   repository: {repo or '(not declared)'}  ·  releases: {len(releases)}")
        warnings: list[str] = []
        if _normalize(canonical) != _normalize(name):
            warnings.append(f"the canonical name is {canonical!r}")
        if age is not None and age < 90:
            warnings.append("younger than 90 days: review with care")
        if not repo:
            warnings.append("declares no repository")
        lookalikes = [
            p
            for p in POPULAR_PACKAGES
            if p != _normalize(canonical) and _levenshtein(_normalize(canonical), p) <= 2
        ]
        if lookalikes:
            warnings.append(
                f"looks like a popular package: {', '.join(lookalikes)} (typosquatting?)"
            )
        vulns = osv_vulnerabilities(canonical)
        if vulns:
            warnings.append(f"{vulns} known vulnerabilities in OSV (check the affected versions)")
        elif vulns is None:
            print("   (OSV not reachable: vulnerability check skipped)")
        for w in warnings:
            print(f"   ⚠️  {w}")
        if warnings:
            code = max(code, 1)
    print("\nReminder: the human approves every new dependency; justify it in the PR.")
    return code


# ------------------------------------------------------------------------- tour
def cmd_tour(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    base = resolve_base(args.base, root)
    if base is None:
        return _err(
            f"base {args.base!r} is not a known ref and none of the defaults (origin/main, main, "
            "master...) exists. Pass one: rumbo tour <branch-or-commit>"
        )
    if base != args.base:
        print(f"(base {args.base!r} not found; using {base!r})", file=sys.stderr)
    try:
        commits = load_commits(base, args.head, root)
    except RuntimeError as exc:
        return _err(str(exc))
    if not commits:
        return _err(f"no commits in {base}..{args.head}")
    text = render_markdown(commits, base, args.head, since=args.since)
    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = root / out
        if args.out.endswith(("/", "\\")) or out.is_dir():
            # A directory: name the file like the Makefile's `review` target did.
            out = out / f"{dt.date.today().isoformat()}-{commits[-1].short}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Wrote {out}")
    else:
        print(text)
    return 0


# ------------------------------------------------------------------------ trace
def cmd_trace(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    reqs = trace(root)
    print(render_trace(reqs))
    return 1 if args.strict and (not reqs or uncovered(reqs)) else 0


# ------------------------------------------------------------------------- eval
def known_graders(root: Path) -> set[str] | None:
    """Grader names in the GRADERS dict of evals/graders/__init__.py; None if unparseable."""
    text = (root / "evals" / "graders" / "__init__.py").read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "GRADERS" for t in targets):
            continue
        if isinstance(node.value, ast.Dict):
            return {
                k.value
                for k in node.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    return None


def _cell(text: str | None) -> str:
    """One markdown table cell: no newlines, pipes escaped."""
    return (text or "").replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


def cmd_eval_add(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    datasets = root / "evals" / "datasets"
    if not (root / "evals" / "graders" / "__init__.py").exists():
        return _err("no evals/ in this project (LLM products only); did you run 'rumbo init'?")
    if not re.fullmatch(r"[a-z0-9_-]+", args.suite):
        return _err("suite must be a plain name (letters, digits, - or _)")
    if args.suite == "all":
        return _err("'all' is reserved: run_evals.py --suite all means every dataset")
    try:
        case_input = json.loads(args.input)
        expected = json.loads(args.expected)
    except json.JSONDecodeError as exc:
        return _err(f"--input and --expected must be JSON objects: {exc}")
    if not isinstance(case_input, dict) or not isinstance(expected, dict):
        return _err("--input and --expected must be JSON objects")
    graders = known_graders(root)
    if graders is None:
        print("WARNING: could not read the GRADERS dict; grader name not validated")
    elif args.grader not in graders:
        return _err(f"unknown grader {args.grader!r}; registered: {', '.join(sorted(graders))}")
    path = datasets / f"{args.suite}.jsonl"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    ids = set()
    for line in existing:
        try:
            ids.add(json.loads(line).get("id"))
        except (json.JSONDecodeError, AttributeError):
            continue
    case_id = args.id
    if not case_id:
        n = len(existing) + 1
        while f"{args.suite}-{n:03d}" in ids:
            n += 1
        case_id = f"{args.suite}-{n:03d}"
    if case_id in ids:
        return _err(f"case id {case_id!r} already exists in {path.relative_to(root)}")
    case: dict[str, Any] = {
        "id": case_id,
        "input": case_input,
        "expected": expected,
        "grader": args.grader,
        "tags": list(dict.fromkeys(args.tag or [])),
    }
    if args.source:
        case["source"] = args.source
    datasets.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(case, ensure_ascii=False) + "\n")
    log = root / "evals" / "error_analysis" / "log.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    if not log.exists():
        log.write_text(
            "# Error analysis log\n\nOne line per production failure turned into an eval case "
            "(`rumbo eval add`). Review it in the weekly trace ritual.\n\n"
            "| Date | Case | Suite | Source | Note |\n|---|---|---|---|---|\n",
            encoding="utf-8",
        )
    with log.open("a", encoding="utf-8") as f:
        f.write(
            f"| {dt.date.today().isoformat()} | {_cell(case_id)} | {args.suite} | "
            f"{_cell(args.source)} | {_cell(args.note)} |\n"
        )
    print(f"Added {case_id} to {path.relative_to(root).as_posix()} + evals/error_analysis/log.md")
    print("Offline with a recorded fixture: make evals  ·  against the model: make evals-live")
    return 0


# ---------------------------------------------------------------------- metrics
def cmd_metrics(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    if not (root / "pyproject.toml").exists():
        return _err(f"no pyproject.toml in {root}; did you run 'rumbo init'?")
    try:
        metrics = collect(root, days=args.days)
    except (OSError, ValueError) as exc:
        return _err(f"could not read the project: {exc}")
    if args.json:
        print(json.dumps(metrics.as_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        print(render_metrics(metrics))
    return 0


# -------------------------------------------------------------------- argparser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rumbo",
        description="RUMBO: Recognize → Unify → Model → Build with ANCLA → Operate.",
        epilog="Docs and examples: https://github.com/padillacm/RUMBO",
    )
    parser.add_argument("--version", action="version", version=f"rumbo {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add_dir(p: argparse.ArgumentParser) -> None:
        p.add_argument("--dir", default=".", help="project directory (default: current)")

    p = sub.add_parser(
        "init",
        help="mount the RUMBO harness (Part A)",
        description="Mounts the harness: AGENTS.md, Makefile, CI, docs templates, tests, evals… "
        "Interactive unless --yes is given.",
    )
    add_dir(p)
    p.add_argument("--name", help="project name (plain text, no quotes)")
    p.add_argument("--package", "--pkg", dest="package", help="Python package in snake_case")
    llm = p.add_mutually_exclusive_group()
    llm.add_argument("--llm", dest="llm", action="store_true", default=None, help="LLM product")
    llm.add_argument("--no-llm", dest="llm", action="store_false", help="no LLM in the product")
    p.add_argument("--provider", choices=PROVIDER_CHOICES, help="model provider (with --llm)")
    p.add_argument("--sdk-package", help="PyPI package of the SDK (provider 'other')")
    p.add_argument("--sdk-module", help="importable module of the SDK (provider 'other')")
    p.add_argument("--sdk-env-key", help="env var holding the API key (provider 'other')")
    p.add_argument("--rigor", choices=RIGOR_LEVELS, help="rigor level (see RUMBO.md Part E)")
    p.add_argument("--case", help="concrete use case (free text)")
    p.add_argument("--yes", "-y", action="store_true", help="do not ask; use flags only")
    p.add_argument("--no-install", action="store_true", help="write files only; skip uv/pre-commit")
    p.add_argument(
        "--force", action="store_true", help="overwrite existing files (keeps .bak copies)"
    )
    p.set_defaults(func=cmd_init)

    p = sub.add_parser(
        "update",
        help="refresh the files the framework owns",
        description="Replaces RUMBO.md, .claude/commands, scripts/ and the 0000 templates with "
        "the current versions. Never touches documents you edited; lists other files that differ.",
    )
    add_dir(p)
    p.add_argument(
        "--diff", action="store_true", help="show what would change (nothing is written)"
    )
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("new", help="create an ADR, spec or postmortem from its template")
    p.add_argument("kind", choices=sorted(NEW_KINDS), help="document kind")
    p.add_argument("title", help="document title")
    add_dir(p)
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("decision", help="log a human decision in docs/decisions.md")
    p.add_argument("title", help="what was decided")
    p.add_argument("--why", default="", help="the reason")
    p.add_argument("--who", default="human", help="who decided (default: human)")
    add_dir(p)
    p.set_defaults(func=cmd_decision)

    p = sub.add_parser(
        "gate",
        help="evaluate a gate (G1..G5)",
        description="Runs the automatic checks of a gate for the project's rigor and prints the "
        "human checklist. Exit code 0 means the automatic checks passed; the gate is only "
        "signed by the human.",
    )
    p.add_argument("code", type=str.upper, choices=sorted(GATES), help="G1, G2, G3, G4 or G5")
    add_dir(p)
    p.add_argument(
        "--no-run",
        action="store_true",
        help="G4: skip the sensor and 'rumbo check' (reported as unmeasured)",
    )
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("doctor", help="verify the harness is complete and current")
    add_dir(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser(
        "check",
        help="run the verification steps of [tool.rumbo.checks] (no shell, ADR-0006)",
        description="Runs each step of [tool.rumbo.checks] in table order without a shell. "
        "Exit 0 green, 1 a step failed, 3 the environment is broken.",
    )
    p.add_argument("steps", nargs="*", metavar="STEP", help="steps to run (default: all)")
    p.add_argument("--brief", action="store_true", help="log to .rumbo-check.log, print a verdict")
    p.add_argument("--keep-going", action="store_true", help="do not stop at the first failure")
    p.add_argument("--base", help="value of {base} (default: origin/main if it exists, else main)")
    p.add_argument("--dir", default=".", help="project root (default: current directory)")
    p.set_defaults(func=cmd_check)

    sub.add_parser(
        "check-tests",
        help="anti-cheat sensor: tests not weakened (see --help of the sensor)",
        add_help=False,
    )

    p = sub.add_parser("verify-dep", help="verify packages on PyPI before adding them")
    p.add_argument("packages", nargs="+", help="package names")
    p.set_defaults(func=cmd_verify_dep)

    p = sub.add_parser(
        "tour",
        help="guided, pedagogical review of a git range",
        description="Builds a per-commit review guide from the diff: risk hotspots, reading "
        "order, verification commands and explanation-test questions.",
    )
    p.add_argument(
        "base",
        nargs="?",
        default="origin/main",
        help="base ref (default: origin/main, falling back to origin/master, main, master)",
    )
    p.add_argument("--head", default="HEAD", help="head ref (default: HEAD)")
    p.add_argument("--since", help="previous head of the same series, for git range-diff")
    p.add_argument(
        "--out",
        help="write the guide to this file, or into this directory as <date>-<sha>.md",
    )
    add_dir(p)
    p.set_defaults(func=cmd_tour)

    p = sub.add_parser(
        "trace",
        help="requirement → test traceability table",
        description="Lists every requirement of specs/ (F-NNNN.Rk) and the tests or eval cases "
        "that mention it. Gate G4 recommends full coverage and requires it in production.",
    )
    add_dir(p)
    p.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when a requirement has no test, or when specs/ has no requirements",
    )
    p.set_defaults(func=cmd_trace)

    p = sub.add_parser("eval", help="eval datasets: 'eval add' turns a failure into a case")
    eval_sub = p.add_subparsers(dest="eval_command", required=True, metavar="ACTION")
    pa = eval_sub.add_parser(
        "add",
        help="append a case to evals/datasets/SUITE.jsonl and log it in error_analysis/",
        description="Turns a production failure or a trace into a regression case. The case "
        "runs offline in `make evals` once a fixture answers it, and live in `make evals-live`.",
    )
    pa.add_argument("suite", help="dataset name (evals/datasets/<suite>.jsonl)")
    pa.add_argument("--input", required=True, help='JSON object, e.g. \'{"question": "..."}\'')
    pa.add_argument("--expected", required=True, help="JSON object the grader compares against")
    pa.add_argument("--grader", required=True, help="grader name registered in evals/graders")
    pa.add_argument("--id", help="case id (default: <suite>-NNN)")
    pa.add_argument("--tag", action="append", help="tag (repeatable), e.g. regression")
    pa.add_argument("--source", help="where it came from: trace id, issue, user report")
    pa.add_argument("--note", help="one line for the error-analysis log")
    add_dir(pa)
    pa.set_defaults(func=cmd_eval_add)

    p = sub.add_parser(
        "metrics",
        help="process metrics: specs, sessions per spec, gates, deploys, commits, evals",
        description="Reads git, docs/decisions.md, specs/, docs/postmortems/ and evals/ offline. "
        "Decision titles it understands: 'F-000N closed' (--why 'k sessions'), 'Gk signed', "
        "'deploy ...', 'incident ...'.",
    )
    add_dir(p)
    p.add_argument("--days", type=int, default=30, help="window for commits and deploys")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_metrics)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "check-tests":
        return check_tests_not_weakened.main(argv[1:])
    parser = build_parser()
    if not argv:
        parser.print_help()
        print("\nNew project? Start with: rumbo init    Existing one? Check it with: rumbo doctor")
        return USAGE_ERROR
    args = parser.parse_args(argv)
    if args.command == "init" and args.package and not valid_package(args.package):
        return _err(f"invalid package name {args.package!r}; use snake_case")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
