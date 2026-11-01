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

from rumbo.project import is_template, numbered_documents
from rumbo.trace import trace, uncovered

DECISION = re.compile(r"^## (\d{4}-\d{2}-\d{2}) — (.+)$", re.M)
CLOSED = re.compile(r"^(F-\d{4}) closed\b", re.I)
SIGNED = re.compile(r"^(G[1-5]) signed\b", re.I)
SESSIONS = re.compile(r"(\d+)\s*sessions?", re.I)
CONVENTIONAL = re.compile(r"^(feat|fix|refactor|test|docs|chore|ci|build|perf|style)(\(|!|:)")


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
        why = re.search(r"^- Why: (.*)$", body, re.M)
        who = re.search(r"^- Decided by: (.*)$", body, re.M)
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


def _commits(root: Path, since: dt.date) -> list[str]:
    proc = subprocess.run(
        ["git", "log", f"--since={since.isoformat()}", "--format=%s%n%b%x00"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    return [c.strip() for c in proc.stdout.split("\x00") if c.strip()]


def _latest_eval(root: Path) -> dict[str, Any] | None:
    folder = root / "evals" / "reports"
    reports = sorted(folder.glob("*.json")) if folder.is_dir() else []
    if not reports:
        return None
    try:
        data = json.loads(reports[-1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    summary = {k: v for k, v in data.items() if k in ("suite", "k", "live") or k.startswith("pass")}
    summary["file"] = reports[-1].name
    summary["cases"] = len(data.get("cases", []))
    return summary


def _eval_cases(root: Path) -> int:
    folder = root / "evals" / "datasets"
    count = 0
    for file in folder.glob("*.jsonl") if folder.is_dir() else []:
        for line in file.read_text(encoding="utf-8").splitlines():
            try:
                case = json.loads(line) if line.strip() else None
            except json.JSONDecodeError:
                case = None
            if case and "example" not in case.get("tags", []):
                count += 1
    return count


def collect(root: Path, days: int = 30, today: dt.date | None = None) -> Metrics:
    today = today or dt.date.today()
    since = today - dt.timedelta(days=days)
    m = Metrics(days=days)

    specs = numbered_documents(root / "specs")
    m.specs_total = len(specs)
    m.specs_complete = sum(not is_template(s.read_text(encoding="utf-8")) for s in specs)

    decisions_text = (
        (root / "docs" / "decisions.md").read_text(encoding="utf-8")
        if (root / "docs" / "decisions.md").is_file()
        else ""
    )
    for d in parse_decisions(decisions_text):
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
    m.eval_cases = _eval_cases(root)
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
            passes = ", ".join(f"{k}={v:.3f}" for k, v in e.items() if k.startswith("pass"))
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
