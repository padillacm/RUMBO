"""Process metrics from the artifacts the harness already produces (ANCLA: Learn And measure).

Everything is read offline from git, docs/decisions.md, specs/, evals/ and docs/postmortems/.
Conventions the numbers depend on (all are plain `rumbo decision` titles):

    rumbo decision "F-0003 closed" --why "2 sessions"     sessions per spec
    rumbo decision "G4 signed" --why "..."                 gates signed by the human
    rumbo decision "deploy v1.4.0" --who ci                deployment frequency
    rumbo decision "incident: wrong figure" --why "..."    change failure rate proxy

DORA metrics proper need deployment data the repository does not have; the deploy/incident
decisions are the cheapest proxy for a 1–3 person team.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import statistics
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rumbo.project import NotUTF8, is_template, numbered_documents, read_utf8
from rumbo.trace import trace, uncovered

# Tolerant of hand-written entries: em dash, en dash or hyphen, extra spaces, CRLF.
DECISION = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*[—–-]+\s*(.+?)\s*$", re.M)
WHY = re.compile(r"^-\s*why:\s*(.*?)\s*$", re.M | re.I)
WHO = re.compile(r"^-\s*decided by:\s*(.*?)\s*$", re.M | re.I)
CLOSED = re.compile(r"^(F-\d{4}) closed\b", re.I)
SIGNED = re.compile(r"^(G[1-5]) signed\b", re.I)
SESSIONS = re.compile(r"(\d+)\s*sessions?", re.I)
CONVENTIONAL = re.compile(r"^(feat|fix|refactor|test|docs|chore|ci|build|perf|style)(\(|!|:)")
MAX_DAYS = 36500  # a century: beyond it the window is a typing mistake, and `date` overflows


@dataclass
class Decision:
    date: dt.date
    title: str
    why: str = ""
    who: str = ""


@dataclass
class Metrics:
    days: int
    specs_total: int = 0
    specs_complete: int = 0
    specs_closed: list[str] = field(default_factory=list)
    sessions_per_spec: list[int] = field(default_factory=list)
    gates_signed: dict[str, int] = field(default_factory=dict)
    deploys: int = 0
    incidents: int = 0
    postmortems: int = 0
    commits_by_type: dict[str, int] = field(default_factory=dict)
    commits_total: int = 0
    test_change_approvals: int = 0
    requirements_total: int = 0
    requirements_uncovered: int = 0
    eval_cases: int = 0
    latest_eval: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["median_sessions_per_spec"] = (
            statistics.median(self.sessions_per_spec) if self.sessions_per_spec else None
        )
        return data


def parse_decisions(text: str) -> list[Decision]:
    decisions: list[Decision] = []
    matches = list(DECISION.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end]
        why = WHY.search(body)
        who = WHO.search(body)
        try:
            date = dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        decisions.append(
            Decision(
                date,
                m.group(2).strip(),
                why.group(1) if why else "",
                who.group(1) if who else "",
            )
        )
    return decisions


def _text(path: Path) -> str:
    """The text of a file; "" when it is missing or not UTF-8, which counts as nothing."""
    try:
        return read_utf8(path) if path.is_file() else ""
    except (NotUTF8, OSError):
        return ""


def _complete(spec: Path) -> bool:
    """The human removed the template mark; a spec that is not UTF-8 counts as nothing."""
    try:
        return not is_template(read_utf8(spec))
    except (NotUTF8, OSError):
        return False


def _commits(root: Path, since: dt.date) -> list[str]:
    # The first instant of the day, as for the decisions: git reads a bare date as "that day at
    # the current time of day" and would leave out the morning of the first day.
    try:
        proc = subprocess.run(
            ["git", "log", f"--since={since.isoformat()}T00:00:00", "--format=%s%n%b%x00"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:  # git not installed
        return []
    if proc.returncode != 0:
        return []
    return [c.strip() for c in proc.stdout.split("\x00") if c.strip()]


def _latest_eval(root: Path) -> dict[str, Any] | None:
    folder = root / "evals" / "reports"
    reports = sorted(folder.glob("*.json")) if folder.is_dir() else []
    if not reports:
        return None
    latest = max(reports, key=lambda p: p.stat().st_mtime)
    try:
        data = json.loads(read_utf8(latest))
    except (ValueError, OSError):  # not JSON, not UTF-8 (PowerShell 5 writes UTF-16 with `>`)
        return None
    if not isinstance(data, dict):
        return None
    summary = {k: v for k, v in data.items() if k in ("suite", "k", "live") or k.startswith("pass")}
    summary["file"] = latest.name
    cases = data.get("cases")
    summary["cases"] = len(cases) if isinstance(cases, list) else 0
    return summary


def dataset_cases(root: Path) -> tuple[int, list[str]]:
    """(real cases, datasets with a line that is not UTF-8) of `evals/datasets/*.jsonl`.

    A case is a JSON object with an `input` (what `evals/run_evals.py` runs) that is not tagged
    `example`; anything else on a line is skipped and counts as nothing.
    """
    folder = root / "evals" / "datasets"
    count = 0
    unreadable: list[str] = []
    for file in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
        try:
            lines = file.read_bytes().splitlines()
        except OSError:
            continue
        for raw in lines:
            try:
                case = json.loads(raw.decode("utf-8-sig")) if raw.strip() else None
            except UnicodeDecodeError:
                if file.name not in unreadable:
                    unreadable.append(file.name)
                continue
            except ValueError:
                continue
            if not isinstance(case, dict) or "input" not in case:
                continue
            tags = case.get("tags")
            if not (isinstance(tags, list) and "example" in tags):
                count += 1
    return count, unreadable


def eval_cases(root: Path) -> int:
    """Dataset cases that are not tagged 'example'; see `dataset_cases` for what a case is."""
    return dataset_cases(root)[0]


def collect(root: Path, days: int = 30, today: dt.date | None = None) -> Metrics:
    if not 0 <= days <= MAX_DAYS:
        raise ValueError(f"--days must be between 0 and {MAX_DAYS}, got {days}")
    today = today or dt.date.today()
    since = today - dt.timedelta(days=days)
    m = Metrics(days=days)

    specs = numbered_documents(root / "specs")
    m.specs_total = len(specs)
    m.specs_complete = sum(_complete(spec) for spec in specs)

    for d in parse_decisions(_text(root / "docs" / "decisions.md")):
        if closed := CLOSED.match(d.title):
            m.specs_closed.append(closed.group(1).upper())
            if sessions := SESSIONS.search(d.why):
                m.sessions_per_spec.append(int(sessions.group(1)))
        elif signed := SIGNED.match(d.title):
            gate = signed.group(1).upper()
            m.gates_signed[gate] = m.gates_signed.get(gate, 0) + 1
        elif d.date >= since and d.title.lower().startswith("deploy"):
            m.deploys += 1
        elif d.date >= since and d.title.lower().startswith("incident"):
            m.incidents += 1

    m.postmortems = len(
        [p for p in (root / "docs" / "postmortems").glob("*.md") if not p.name.startswith("0000-")]
        if (root / "docs" / "postmortems").is_dir()
        else []
    )

    for commit in _commits(root, since):
        m.commits_total += 1
        kind = CONVENTIONAL.match(commit)
        key = kind.group(1) if kind else "other"
        m.commits_by_type[key] = m.commits_by_type.get(key, 0) + 1
        if "[tests-change-approved]" in commit:
            m.test_change_approvals += 1

    reqs = trace(root)
    m.requirements_total = len(reqs)
    m.requirements_uncovered = len(uncovered(reqs))
    m.eval_cases = eval_cases(root)
    m.latest_eval = _latest_eval(root)
    return m


def render(m: Metrics) -> str:
    closed = ", ".join(m.specs_closed) or "-"
    lines = [f"# RUMBO metrics (last {m.days} days)", "", "## Flow"]
    lines.append(
        f"- Specs: {m.specs_total} ({m.specs_complete} complete, {len(m.specs_closed)} closed: "
        f"{closed})"
    )
    if m.sessions_per_spec:
        lines.append(
            f"- Sessions per closed spec: median {statistics.median(m.sessions_per_spec):g}, "
            f"max {max(m.sessions_per_spec)} (target: 1-2; above that, split the spec)"
        )
    else:
        lines.append(
            "- Sessions per closed spec: no data "
            '(log `rumbo decision "F-000N closed" --why "1 session"`)'
        )
    gates = ", ".join(f"{g} x{n}" for g, n in sorted(m.gates_signed.items())) or "none"
    lines.append(f"- Gates signed by the human: {gates}")
    by_type = sorted(m.commits_by_type.items(), key=lambda kv: -kv[1])
    types = ", ".join(f"{k} {v}" for k, v in by_type) or "-"
    lines.append(
        f"- Commits: {m.commits_total} ({types}); test-change approvals: {m.test_change_approvals}"
    )
    lines += ["", "## Operations (DORA proxies from docs/decisions.md)"]
    ops = f"- Deploys logged: {m.deploys} · incidents: {m.incidents}"
    if m.deploys:
        ops += f" · change failure rate ≈ {m.incidents / m.deploys:.0%}"
    else:
        ops += ' (log `rumbo decision "deploy vX" --who ci` to measure frequency)'
    lines += [ops, f"- Postmortems written: {m.postmortems}", "", "## Quality"]
    if m.requirements_total:
        covered = m.requirements_total - m.requirements_uncovered
        lines.append(f"- Requirements with a test: {covered}/{m.requirements_total} (rumbo trace)")
    else:
        lines.append("- Requirements with a test: no `- Rk` lines in specs/ yet")
    if m.eval_cases or m.latest_eval:
        lines.append(f"- Real eval cases: {m.eval_cases}")
        if m.latest_eval:
            e = m.latest_eval
            passes = ", ".join(
                f"{k}={v:.3f}" if isinstance(v, int | float) else f"{k}={v}"
                for k, v in e.items()
                if k.startswith("pass")
            )
            lines.append(
                f"- Latest eval run: {e.get('file')} suite={e.get('suite')} "
                f"live={e.get('live')} {passes}"
            )
        else:
            lines.append("- Latest eval run: none in evals/reports/")
    lines += [
        "",
        "Hours worked without AI per week and the explanation-test pass rate are human-reported: "
        "add them to this review by hand.",
    ]
    return "\n".join(lines)
