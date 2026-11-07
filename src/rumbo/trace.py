"""Requirement → test traceability.

A spec `specs/NNNN-title.md` is `F-NNNN`; each line `- Rk (...)` under "Requirements" is a
requirement with the full id `F-NNNN.Rk`. A test (or an eval case) covers a requirement by
mentioning that full id anywhere in its text: a docstring, a comment, a case id. `rumbo trace`
builds the table; gate G4 reads it (recommendation below production, required in production).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rumbo.project import numbered_documents, section

# `- R1 (event): text`; nested items such as `R1.1` are not requirements.
# `- R1 (event): text` or `- R1: text`; the parenthetical may itself contain colons.
REQ_LINE = re.compile(r"^\s*-\s*(R\d+)(?![\w.])\s*(?:\([^)]*\))?\s*:\s*(.*)$")
SOURCE_GLOBS = ("tests/**/*.py", "evals/datasets/*.jsonl")


@dataclass
class Requirement:
    spec_id: str
    rid: str
    text: str
    covered_by: list[str] = field(default_factory=list)

    @property
    def full_id(self) -> str:
        return f"{self.spec_id}.{self.rid}"


def spec_id(path: Path) -> str:
    match = re.match(r"^(\d{4})-", path.name)
    return f"F-{match.group(1)}" if match else path.stem


def requirements(spec: Path) -> list[Requirement]:
    text = spec.read_text(encoding="utf-8")
    body = section(text, "Requirements")
    sid = spec_id(spec)
    found: list[Requirement] = []
    for line in body.splitlines():
        match = REQ_LINE.match(line)
        if match:
            found.append(Requirement(sid, match.group(1), match.group(2).strip()))
    return found


def trace(root: Path) -> list[Requirement]:
    reqs = [r for spec in numbered_documents(root / "specs") for r in requirements(spec)]
    if not reqs:
        return []
    sources: list[tuple[str, list[str]]] = []
    for pattern in SOURCE_GLOBS:
        for file in sorted(root.glob(pattern)):
            rel = file.relative_to(root).as_posix()
            sources.append((rel, file.read_text(encoding="utf-8", errors="replace").splitlines()))
    for req in reqs:
        needle = re.compile(rf"(?<!\w){re.escape(req.full_id)}(?!\w)")
        for rel, lines in sources:
            hits = [n for n, line in enumerate(lines, 1) if needle.search(line)]
            req.covered_by += [f"{rel}:{n}" for n in hits[:3]]
    return reqs


def uncovered(reqs: list[Requirement]) -> list[Requirement]:
    return [r for r in reqs if not r.covered_by]


def render(reqs: list[Requirement]) -> str:
    if not reqs:
        return (
            "No requirements found: specs/ has no `- Rk (...): ...` lines under '## Requirements'."
        )
    lines = ["| Requirement | Text | Covered by |", "|---|---|---|"]
    seen: set[str] = set()
    for r in reqs:
        where = ", ".join(r.covered_by) if r.covered_by else "**missing**"
        text = (r.text if len(r.text) <= 70 else r.text[:67] + "...").replace("|", "\\|")
        rid = r.full_id + (" (duplicate id!)" if r.full_id in seen else "")
        seen.add(r.full_id)
        lines.append(f"| {rid} | {text} | {where} |")
    missing = uncovered(reqs)
    lines.append("")
    lines.append(
        f"{len(reqs) - len(missing)}/{len(reqs)} requirements referenced by a test or eval case "
        f"(mention the id, e.g. `{reqs[0].full_id}`, in the test's docstring)."
    )
    return "\n".join(lines)
