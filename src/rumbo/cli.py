"""RUMBO command-line interface.

rumbo init                      mount the harness (Part A) in a project
rumbo update                    refresh the files the framework owns
rumbo promote mvp|production    raise the rigor: new checks, files and dev tools (ADR-0011)
rumbo new adr|spec|postmortem   create a document from its template
rumbo decision "title"          log a human decision in docs/decisions.md
rumbo gate G1..G5               automatic checks + human checklist of a gate
rumbo doctor                    verify the harness is complete and current
rumbo check [STEP ...]          run the verification steps of [tool.rumbo.checks]
rumbo check-tests               anti-cheat sensor (tests not weakened)
rumbo verify-dep NAME           verify a package on PyPI (or your index) before adding it
rumbo tour [BASE]               guided, pedagogical review of a git range
rumbo trace                     requirement → test traceability table
rumbo eval add SUITE ...        turn a failure into an eval case
rumbo metrics                   process metrics from git, decisions, specs and evals
"""

from __future__ import annotations

import argparse
import ast
import base64
import contextlib
import datetime as dt
import http.client
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from rumbo import __version__
from rumbo.gates import GATES, evaluate, report
from rumbo.metrics import collect
from rumbo.metrics import render as render_metrics
from rumbo.project import (
    DEFAULT_PYTHON,
    DEFAULT_SRC,
    DEFAULT_TYPE_CHECKER,
    KINDS,
    LAYOUTS,
    RIGOR_LEVELS,
    SUPPORTED_PYTHONS,
    TYPE_CHECKERS,
    Project,
    rigor_rank,
    slug,
)
from rumbo.scaffold import (
    HUMAN_COMMAND,
    OTHER_PROVIDER,
    PROVIDERS,
    Config,
    Detected,
    add_dev_dependencies,
    detect_project,
    detect_src,
    dev_dependencies,
    install,
    plan_files,
    runtime_dependencies,
    scaffold,
    set_rigor,
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


def _detect(args: argparse.Namespace) -> Detected | None:
    """ADR-0013: with --adopt, what the existing project looks like (the flags still win)."""
    if not getattr(args, "adopt", False):
        return None
    root = Path(args.dir).resolve()
    if not root.is_dir():
        raise ValueError(f"--adopt needs an existing project directory; {root} does not exist")
    try:
        detected = detect_project(root, args.package)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"could not parse pyproject.toml: {exc}") from exc
    print(f"Detected in {root} (--adopt): {detected.summary()}")
    return detected


def _config_from_args(args: argparse.Namespace) -> Config:
    interactive = sys.stdin.isatty() and not args.yes
    if not interactive and not args.yes:
        raise ValueError("stdin is not a terminal: pass --yes with explicit flags (see --help)")

    name, package, llm = args.name, args.package, args.llm
    provider, rigor, case = args.provider, args.rigor, args.case
    sdk_package, sdk_module, sdk_env_key = args.sdk_package, args.sdk_module, args.sdk_env_key
    kind, layout = args.kind, args.layout or ""
    layers = tuple(x.strip() for x in (args.layers or "").split(",") if x.strip())
    detected = _detect(args)
    default_name = detected.name if detected else Path(args.dir).resolve().name
    default_kind = detected.kind if detected else "service"

    if interactive:
        print("A0 · RUMBO initial questions (Enter accepts the value in brackets)\n")
        name = name or _ask("1. Project name", default_name, valid_name)
        default_package = (detected.package if detected else None) or to_package(name)
        package = package or _ask("   Python package (snake_case)", default_package, valid_package)
        # ADR-0010: the kind is the question; the LLM capability is its sub-question.
        kind = kind or _ask("2. What are you building?", default_kind, choices=list(KINDS))
        if llm is None:
            llm = _yes(_ask("   Does it call an LLM?", "no", choices=["yes", "no"]))
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
        name = name or default_name
        package = package or (detected.package if detected else None) or to_package(name)
    kind = kind or default_kind  # ADR-0010 default; --layout and --layers are never asked
    if detected and not layout and not layers:
        layout = detected.layout
    if llm and layout and layout != "hexagonal":
        print(f"note: an LLM product is hexagonal (ADR-0010); --layout {layout} is ignored")
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
        kind=kind,
        layout=layout,
        layers=layers,
        adopted=detected is not None,
        tests_dir=detected.tests_dir if detected else "tests",
        # ADR-0020: where the package that was finally chosen lives (never asked, no flag).
        src=detect_src(Path(args.dir).resolve(), package) if detected else DEFAULT_SRC,
        python=args.python or DEFAULT_PYTHON,
        # ADR-0015: the type checker is a flag, never an A0 question.
        types=getattr(args, "types", None) or DEFAULT_TYPE_CHECKER,
    )


def _init_summary(cfg: Config) -> str:
    """The settings `rumbo init` mounts the harness with, each one once, on one line."""
    line = (
        f"package={cfg.package} kind={cfg.kind} layout={cfg.layout} llm={cfg.llm} "
        f"provider={cfg.provider or '-'} rigor={cfg.rigor} python={cfg.python} types={cfg.types}"
    )
    if cfg.adopted:
        line += f" adopted=true tests={cfg.tests_dir}"
    if cfg.src != DEFAULT_SRC:
        line += f" src={cfg.src}"  # ADR-0020: only when the package is not under src/
    return line


def _next_steps(steps: list[str]) -> str:
    """The steps numbered from 1; the continuation lines of a step are indented under it."""
    return "\n".join(
        f"  {n}. " + step.replace("\n", "\n     ") for n, step in enumerate(steps, start=1)
    )


def cmd_init(args: argparse.Namespace) -> int:
    try:
        cfg = _config_from_args(args)
    except ValueError as exc:
        return _err(str(exc))
    dest = Path(args.dir).resolve()
    print(f"\nMounting the RUMBO harness in {dest}")
    print(f"  {_init_summary(cfg)}\n")
    if args.force:
        print(
            "--force: existing files are overwritten (a .bak copy is kept); "
            "pyproject.toml and .gitignore are merged, never replaced.\n"
        )
    interactive = sys.stdin.isatty() and not args.yes
    existing = [p.name for p in dest.iterdir() if p.name != ".git"] if dest.is_dir() else []
    if interactive and existing and not args.force and not cfg.adopted:
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
    # ADR-0021: only the human declares the human. `install` did it for a human at a terminal;
    # in every other case the command is the human's first step in this clone.
    declare = (
        [f"{' '.join(HUMAN_COMMAND)}    # you, the human, once per clone (never the agent)"]
        if (dest / ".git").exists() and not _declared_human(dest)
        else []
    )
    if args.no_install:
        runtime = runtime_dependencies(cfg)
        steps = [
            f"uv add --dev {' '.join(dev_dependencies(cfg))}"
            + (f"\nuv add {' '.join(runtime)}" if runtime else ""),
            "uv run pre-commit autoupdate && uv run pre-commit install",
            *declare,
            "uv run python scripts/rumbo_check.py    # must be green",
        ]
        print(f"\nNext steps (--no-install):\n{_next_steps(steps)}\n")
    else:
        steps = [
            *declare,
            "uv run python scripts/rumbo_check.py    # must be green",
            "git add -A && git commit -m 'chore: initial RUMBO harness'",
            'Open Claude Code and say: "Read AGENTS.md and RUMBO.md; let\'s start phase R".',
        ]
        print(f"\nNext steps (A8):\n{_next_steps(steps)}")
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


# ---------------------------------------------------------------------- promote
def cmd_promote(args: argparse.Namespace) -> int:
    """Raises the rigor (ADR-0011): writes it, runs `update`, adds the level's dev tools."""
    root = Path(args.dir).resolve()
    try:
        project = Project.load(root)
    except (FileNotFoundError, ValueError) as exc:
        return _err(str(exc))
    target: str = args.rigor
    if rigor_rank(target) <= rigor_rank(project.rigor):
        # Not a usage error (exit 2): the command is fine, the project is not below `target`.
        if target == project.rigor:
            message = f"the project is already at rigor {target!r}; nothing to promote"
        else:
            message = (
                f"cannot lower the rigor from {project.rigor!r} to {target!r}: `rumbo promote` "
                "only raises it; lowering is a hand edit of [tool.rumbo].rigor (ADR-0011)"
            )
        print(f"Error: {message}", file=sys.stderr)
        return 1
    pyproject = root / "pyproject.toml"
    try:
        text = set_rigor(pyproject.read_text(encoding="utf-8"), target)
    except ValueError as exc:
        return _err(str(exc))
    with pyproject.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"Promoting {project.name or project.package} from {project.rigor} to {target}")
    rep = update(Project.load(root), root)
    if not args.no_install:
        for cmd in list(rep.suggested):
            if (
                cmd.startswith("uv add --dev ")
                and add_dev_dependencies(cmd.split()[3:], root, rep) == 0
            ):
                rep.suggested.remove(cmd)
    print("\n" + rep.summary())
    print(
        f"\nRigor is now {target} (harness {__version__}). Review the diff with: git diff\n"
        f"The new level asks more of the gates: see what it requires with: rumbo gate G4"
    )
    return 1 if any(rc != 0 for _, rc in rep.commands) else 0


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
    """`git config rumbo.human` is true in this clone (ADR-0008).

    The human declares it; `rumbo init` does it only for a human at a terminal (ADR-0021).
    """
    try:
        proc = subprocess.run(
            ["git", "config", "--get", "rumbo.human"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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
    expected = list(plan_files(cfg, root))  # ADR-0013: an adopted project has no skeleton
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
            f"✅ Harness complete (package={project.package}, kind={project.kind}, "
            f"layout={project.layout}, llm={project.llm}, provider={project.provider or '-'}, "
            f"rigor={project.rigor})."
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
    """PEP 503 normalisation: lowercase, runs of `-`, `_` and `.` collapsed to one dash."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _http_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
    data = json.dumps(payload).encode() if payload is not None else None
    if not url.startswith("https://"):
        raise ValueError("only https URLs are allowed")
    request = urllib.request.Request(  # noqa: S310 - https only, checked above
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:  # noqa: S310 # nosec B310
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
    if data is None:
        return 0
    vulns = data.get("vulns", []) if isinstance(data, dict) else None
    return len(vulns) if isinstance(vulns, list) else None  # an unexpected shape: unknown


# ADR-0014: a private index (Artifactory, CodeArtifact, GitLab...) answers existence and versions;
# age, repository, typosquatting and OSV are still measured against PyPI and labelled as such.
INDEX_ENV_VARS = ("UV_INDEX_URL", "UV_DEFAULT_INDEX", "PIP_INDEX_URL")
SIMPLE_JSON = "application/vnd.pypi.simple.v1+json"
PYPI_LABEL = "(measured against PyPI, not against your index)"
_RELEASE_RE = re.compile(r"(\d+(?:\.\d+)*)(.*)")
_SDIST_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".zip")


def resolve_index(flag: str | None, env: Mapping[str, str]) -> str | None:
    """The private index to verify against: `--index`, else the first set `INDEX_ENV_VARS` entry.

    None when nothing is set or the index is pypi.org itself: today's PyPI-only behaviour.
    """
    url = flag or next((env[key] for key in INDEX_ENV_VARS if env.get(key)), None)
    if not url:
        return None
    try:
        parts = urllib.parse.urlsplit(url)
        _ = parts.port  # a port that is not a number raises here, not later in a request
    except ValueError:
        # Never echo the value: it may carry credentials.
        raise ValueError("the index URL cannot be parsed (check the host and the port)") from None
    host = (parts.hostname or "").lower()
    return None if host in ("pypi.org", "www.pypi.org") else url


def sanitize_index(url: str) -> str:
    """`scheme://host[:port]/path` without user info: the only form of an index URL ever printed."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    try:
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        port = ""
    return f"{parts.scheme}://{host}{port}{parts.path}"


def _redact(text: str, index: str) -> str:
    """An error message with every raw form of the index URL replaced by the sanitised one."""
    parts = urllib.parse.urlsplit(index)
    clean = sanitize_index(index)
    text = text.replace(index, clean)
    if parts.netloc:
        text = text.replace(parts.netloc, urllib.parse.urlsplit(clean).netloc)
    if parts.password:
        text = text.replace(parts.password, "***")
    return text


class _Anchors(HTMLParser):
    """Collects the text of every `<a>` of a PEP 503 project page: the file names."""

    def __init__(self) -> None:
        super().__init__()
        self.names: list[str] = []
        self._text: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._text = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._text is not None:
            self.names.append(self._text.strip())
            self._text = None

    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text += data


def _version_from_filename(filename: str) -> str | None:
    """The version in a wheel (`name-VERSION-py3-none-any.whl`) or sdist (`name-VERSION.tar.gz`)."""
    if filename.endswith(".whl"):
        parts = filename[:-4].split("-")
        return parts[1] if len(parts) >= 5 else None
    for suffix in _SDIST_SUFFIXES:
        if filename.endswith(suffix):
            stem = filename[: -len(suffix)]
            return stem.rsplit("-", 1)[1] if "-" in stem else None
    return None


def _versions_from_files(filenames: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for filename in filenames:
        version = _version_from_filename(filename)
        if version:
            seen.setdefault(version, None)
    return list(seen)


def _version_key(version: str) -> tuple[tuple[int, ...], bool, str]:
    """Best-effort ordering: release numbers as ints, finals after pre-releases, then text."""
    match = _RELEASE_RE.match(version)
    if not match:
        return ((), False, version)
    numbers, rest = match.groups()
    return (tuple(int(x) for x in numbers.split(".")), rest == "", rest)


def latest_version(versions: list[str]) -> str | None:
    return max(versions, key=_version_key) if versions else None


def parse_simple(body: str, content_type: str) -> list[str]:
    """Versions in a simple-API project page: PEP 691 JSON, else the PEP 503 HTML anchors."""
    data: Any = None
    if content_type != "text/html":
        with contextlib.suppress(ValueError):
            data = json.loads(body)
    if isinstance(data, dict):
        versions = data.get("versions")
        if isinstance(versions, list) and versions:
            return [str(v) for v in versions]
        files = data.get("files") or []
        return _versions_from_files(
            str(f.get("filename", "")) for f in files if isinstance(f, dict)
        )
    parser = _Anchors()
    parser.feed(body)
    return _versions_from_files(parser.names)


def index_versions(index: str, name: str) -> list[str] | None:
    """The versions of `name` on a simple-API index; None when the index answers 404.

    Credentials in the URL travel as a Basic Authorization header, never in the request line,
    and never reach a message: every error is raised with the URL already sanitised.
    """
    parts = urllib.parse.urlsplit(index)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"the index URL must be http(s), got {sanitize_index(index)!r}")
    url = sanitize_index(index).rstrip("/") + "/" + _normalize(name) + "/"
    headers = {"Accept": f"{SIMPLE_JSON}, text/html;q=0.1"}
    if parts.username is not None:
        user = urllib.parse.unquote(parts.username)
        password = urllib.parse.unquote(parts.password or "")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 - http(s), checked above
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:  # noqa: S310 # nosec B310
            content_type = str(resp.headers.get_content_type())
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return parse_simple(body, content_type)


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
    if args.full:
        argv.append("--full")
    return check.main(argv)


def _lookalikes(name: str) -> list[str]:
    normalized = _normalize(name)
    return [p for p in POPULAR_PACKAGES if p != normalized and _levenshtein(normalized, p) <= 2]


def _verify_on_index(name: str, index: str) -> int:
    """Existence and versions on the project's own index (ADR-0014); prints only sanitised URLs."""
    shown = sanitize_index(index)
    host = urllib.parse.urlsplit(shown).netloc
    print(f"📦 {name}")
    print(f"   index: {shown}")
    try:
        versions = index_versions(index, name)
    except NETWORK_ERRORS as exc:
        print(f"   ❓ could not query {host} ({_redact(str(exc), index)}). Verify by hand.")
        return 2
    if versions is None:
        print(f"   ❌ exists on {host}: no. Hallucinated name? (slopsquatting)")
        return 1
    latest = latest_version(versions)
    print(f"   exists on {host}: yes")
    print(f"   versions on {host}: {len(versions)}" + (f" (latest {latest})" if latest else ""))
    return 0


def _verify_on_pypi(name: str, on_index: bool) -> int:
    """Age, repository, typosquatting and OSV, measured on PyPI; labelled when an index is used."""
    label = f" {PYPI_LABEL}" if on_index else ""
    indent = "   " if on_index else ""
    try:
        data = pypi_info(name)
    except NETWORK_ERRORS as exc:
        print(f"{indent}❓ {name}: could not query PyPI ({exc}). Verify by hand.")
        return 2
    warnings: list[str] = []
    if data is None:
        if not on_index:
            print(f"❌ {name}: does NOT exist on PyPI. Hallucinated name? (slopsquatting)")
            return 1
        # The index is what the project installs from: absence on PyPI is a fact, not a failure.
        print(f"   not on PyPI{label}")
        if lookalikes := _lookalikes(name):
            warnings.append(
                f"looks like a popular package: {', '.join(lookalikes)} (typosquatting?)"
            )
        for w in warnings:
            print(f"   ⚠️  {w}{label}")
        return 1 if warnings else 0
    try:
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
        headline = f"{canonical} {info.get('version', '?')} — {info.get('summary') or ''}"
    except (AttributeError, TypeError, KeyError, ValueError):
        # ADR-0014 / threat model, boundary 3: the answer is untrusted data.
        print(f"{indent}❓ {name}: PyPI answered in an unexpected shape. Verify by hand.")
        return 2
    print(f"   on PyPI: {headline}{label}" if on_index else f"📦 {headline}")
    print(f"   first release: {first or '?'} ({age if age is not None else '?'} days ago){label}")
    print(f"   repository: {repo or '(not declared)'}  ·  releases: {len(releases)}{label}")
    if _normalize(canonical) != _normalize(name):
        warnings.append(f"the canonical name is {canonical!r}")
    if age is not None and age < 90:
        warnings.append("younger than 90 days: review with care")
    if not repo:
        warnings.append("declares no repository")
    if lookalikes := _lookalikes(canonical):
        warnings.append(f"looks like a popular package: {', '.join(lookalikes)} (typosquatting?)")
    vulns = osv_vulnerabilities(canonical)
    if vulns:
        warnings.append(f"{vulns} known vulnerabilities in OSV (check the affected versions)")
    elif vulns is None:
        print(f"   (OSV not reachable: vulnerability check skipped){label}")
    for w in warnings:
        print(f"   ⚠️  {w}{label}")
    return 1 if warnings else 0


def cmd_verify_dep(args: argparse.Namespace) -> int:
    try:
        index = resolve_index(args.index, os.environ)
    except ValueError as exc:
        return _err(str(exc))
    code = 0
    for name in args.packages:
        if index is not None:
            code = max(code, _verify_on_index(name, index))
        code = max(code, _verify_on_pypi(name, on_index=index is not None))
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
        return _err(
            "no evals/ in this project (LLM products and data pipelines only); "
            "did you run 'rumbo init'?"
        )
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
    p.add_argument(
        "--kind", choices=KINDS, help="what you are building (A0 question 2; default: service)"
    )
    p.add_argument(
        "--layout",
        choices=LAYOUTS,
        help="package layout (default by kind: service hexagonal, the others flat; --llm forces "
        "hexagonal)",
    )
    p.add_argument(
        "--layers", metavar="A,B,C", help="layers of a layered layout, outermost first (>= 2)"
    )
    llm = p.add_mutually_exclusive_group()
    llm.add_argument("--llm", dest="llm", action="store_true", default=None, help="LLM product")
    llm.add_argument("--no-llm", dest="llm", action="store_false", help="no LLM in the product")
    p.add_argument("--provider", choices=PROVIDER_CHOICES, help="model provider (with --llm)")
    p.add_argument("--sdk-package", help="PyPI package of the SDK (provider 'other')")
    p.add_argument("--sdk-module", help="importable module of the SDK (provider 'other')")
    p.add_argument("--sdk-env-key", help="env var holding the API key (provider 'other')")
    p.add_argument("--rigor", choices=RIGOR_LEVELS, help="rigor level (see RUMBO.md Part E)")
    p.add_argument(
        "--types",
        choices=TYPE_CHECKERS,
        help=f"type checker (ADR-0015; default: {DEFAULT_TYPE_CHECKER})",
    )
    p.add_argument("--case", help="concrete use case (free text)")
    p.add_argument(
        "--python",
        metavar="X.Y",
        help="minimum Python the project promises (ADR-0014; default {DEFAULT_PYTHON}; one of "
        f"{', '.join(SUPPORTED_PYTHONS)})",
    )
    p.add_argument("--yes", "-y", action="store_true", help="do not ask; use flags only")
    p.add_argument(
        "--adopt",
        action="store_true",
        help="existing project (ADR-0013): detect package, kind, layout and tests directory, "
        "write the harness without the src/ and tests skeleton, check only changed files",
    )
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

    p = sub.add_parser(
        "promote",
        help="raise the rigor level (prototype → mvp → production)",
        description="Writes the new rigor in [tool.rumbo], runs `rumbo update` (appends the "
        "level's checks, renders its files) and `uv add --dev` for the tools it needs. Nothing is "
        "removed; lowering the rigor is a hand edit (ADR-0011).",
    )
    p.add_argument("rigor", choices=RIGOR_LEVELS, help="the new, higher rigor level")
    add_dir(p)
    p.add_argument(
        "--no-install", action="store_true", help="write files only; print the 'uv add' line"
    )
    p.set_defaults(func=cmd_promote)

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
    p.add_argument(
        "--full", action="store_true", help="replace {changed} with . (whole tree, ADR-0013)"
    )
    p.add_argument("--dir", default=".", help="project root (default: current directory)")
    p.set_defaults(func=cmd_check)

    sub.add_parser(
        "check-tests",
        help="anti-cheat sensor: tests not weakened (see --help of the sensor)",
        add_help=False,
    )

    p = sub.add_parser(
        "verify-dep",
        help="verify packages on PyPI, or on your index, before adding them",
        description="Existence and versions are checked on the index the project installs "
        "from (--index, else UV_INDEX_URL, UV_DEFAULT_INDEX or PIP_INDEX_URL; PyPI when none "
        "is set); age, repository, typosquatting and OSV are always measured against PyPI "
        "(ADR-0014). Credentials in the URL are never printed.",
    )
    p.add_argument("packages", nargs="+", help="package names")
    p.add_argument(
        "--index",
        metavar="URL",
        help="simple-API index to verify existence and versions against (default: the first "
        "set of $UV_INDEX_URL, $UV_DEFAULT_INDEX, $PIP_INDEX_URL; PyPI when none is set)",
    )
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


def _utf8_console() -> None:
    """Emit UTF-8 whatever the console's code page: the reports use ✅/❌/⚠️ marks.

    Python < 3.15 on Windows writes through cp1252 by default and raises on the first mark.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):  # a closed or exotic stream stays
                reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
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
