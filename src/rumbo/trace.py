"""Requirement → test traceability.

A spec `specs/NNNN-title.md` is `F-NNNN`; each line `- Rk (...)` under "Requirements" is a
requirement with the full id `F-NNNN.Rk`. A test (or an eval case) covers a requirement by
mentioning that full id anywhere in its text: a docstring, a comment, a case id. `rumbo trace`
builds the table; gate G4 reads it (recommendation below production, required in production).
The tests are read from the project's own tests directory, `[tool.rumbo].tests` (ADR-0020).

A requirement the tool cannot read is never dropped: a bullet under "Requirements" that starts
with an id and is not `- Rk (pattern): text`, and a spec that is not UTF-8, are kept as
`malformed` entries; `rumbo trace` lists them, and `--strict` and G4 count them as missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rumbo.project import NOT_UTF8, NotUTF8, numbered_documents, prose, read_utf8, tests_dir

# `- R1 (event): text` or `- R1: text`; nested items such as `R1.1` are not requirements. The
# parenthetical may contain colons and one level of parentheses; the bullet may be `*` or `+`
# and the id may be bold (`- **R3** (state): …`).
_BULLET = r"^\s*(?:-\s*|[*+]\s+)(?:\*\*|__)?"
_BOLD = r"(?:\*\*|__)?"
REQ_LINE = re.compile(
    _BULLET
    + r"(R\d+)(?![\w.])"
    + _BOLD
    + r"\s*(?:\((?:[^()]|\([^()]*\))*\))?\s*"
    + _BOLD
    + r"\s*:\s*"
    + _BOLD
    + r"\s*(.*)$"
)
# A bullet that starts with an id: a requirement, or a line to report when REQ_LINE rejects it.
REQ_START = re.compile(_BULLET + r"(R\d+)(?!\w|\.\d)")
HEADING = re.compile(r"^##\s+(.*)$")
# One pattern for every mention: `F-0001.R10` is not a mention of `F-0001.R1`.
MENTION = re.compile(r"(?<!\w)F-\d{4}\.R\d+(?!\w)")
EVAL_GLOB = "evals/datasets/*.jsonl"
MAX_PLACES_PER_FILE = 3
NO_REQUIREMENTS = (
    "No requirements found: specs/ has no `- Rk (...): ...` lines under '## Requirements'."
)


@dataclass
class Requirement:
    spec_id: str
    rid: str
    text: str
    covered_by: list[str] = field(default_factory=list)
    # A line that could not be parsed (`text` is the line) or a spec that could not be read
    # (`rid` is "?"); `where` is its place, `specs/NNNN-x.md:line`.
    malformed: bool = False
    where: str = ""

    @property
    def full_id(self) -> str:
        return f"{self.spec_id}.{self.rid}"


def spec_id(path: Path) -> str:
    match = re.match(r"^(\d{4})-", path.name)
    return f"F-{match.group(1)}" if match else path.stem


def requirements(spec: Path, name: str = "") -> list[Requirement]:
    """The requirements of a spec, in order, with the lines that look like one and are not.

    `name` is how the spec is called in the places (`where`); its file name when not given.
    """
    sid = spec_id(spec)
    name = name or spec.name
    try:
        text = read_utf8(spec, name)
    except NotUTF8:
        return [Requirement(sid, "?", NOT_UTF8, malformed=True, where=name)]
    found: list[Requirement] = []
    inside = False
    for number, line in enumerate(prose(text).split("\n"), 1):
        heading = HEADING.match(line)
        if heading:
            if inside:
                break  # the first "Requirements" section only, up to the next `## ` heading
            inside = heading.group(1).lower().startswith("requirements")
            continue
        if not inside:
            continue
        match = REQ_LINE.match(line)
        where = f"{name}:{number}"
        if match:
            found.append(Requirement(sid, match.group(1), match.group(2).strip(), where=where))
        elif start := REQ_START.match(line):
            found.append(Requirement(sid, start.group(1), line.strip(), [], True, where))
    return found


def trace(root: Path, tests: str | None = None) -> list[Requirement]:
    """Every requirement with the places that mention it; `tests` defaults to the project's."""
    reqs = [
        r
        for spec in numbered_documents(root / "specs")
        for r in requirements(spec, spec.relative_to(root).as_posix())
    ]
    by_id: dict[str, list[Requirement]] = {}
    for req in reqs:
        if not req.malformed:
            by_id.setdefault(req.full_id, []).append(req)
    if not by_id:
        return reqs
    folder = root / (tests or tests_dir(root))  # may be absolute or outside the project
    sources = sorted(folder.rglob("*.py")) if folder.is_dir() else []
    for file in (*sources, *sorted(root.glob(EVAL_GLOB))):
        rel = file.relative_to(root).as_posix() if file.is_relative_to(root) else file.as_posix()
        places: dict[str, list[int]] = {}
        text = file.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if "F-" not in line:
                continue
            for full_id in dict.fromkeys(MENTION.findall(line)):  # a line counts once
                if full_id in by_id:
                    places.setdefault(full_id, []).append(number)
        for full_id, numbers in places.items():
            for req in by_id[full_id]:
                req.covered_by += [f"{rel}:{n}" for n in numbers[:MAX_PLACES_PER_FILE]]
    return reqs


def uncovered(reqs: list[Requirement]) -> list[Requirement]:
    """What stands without a test: a requirement nothing mentions, a line nobody could parse."""
    return [r for r in reqs if r.malformed or not r.covered_by]


def unparsed(reqs: list[Requirement]) -> list[Requirement]:
    return [r for r in reqs if r.malformed]


def render(reqs: list[Requirement]) -> str:
    parsed = [r for r in reqs if not r.malformed]
    lines = [NO_REQUIREMENTS]
    if parsed:
        lines = ["| Requirement | Text | Covered by |", "|---|---|---|"]
        seen: set[str] = set()
        for r in parsed:
            where = ", ".join(r.covered_by) if r.covered_by else "**missing**"
            text = (r.text if len(r.text) <= 70 else r.text[:67] + "...").replace("|", "\\|")
            rid = r.full_id + (" (duplicate id!)" if r.full_id in seen else "")
            seen.add(r.full_id)
            lines.append(f"| {rid} | {text} | {where} |")
        missing = [r for r in parsed if not r.covered_by]
        lines.append("")
        lines.append(
            f"{len(parsed) - len(missing)}/{len(parsed)} requirements referenced by a test or "
            f"eval case (mention the id, e.g. `{parsed[0].full_id}`, in the test's docstring)."
        )
    bad = unparsed(reqs)
    if bad:
        lines.append("")
        lines.append(
            f"{len(bad)} line(s) under 'Requirements' could not be parsed as "
            "`- Rk (pattern): text`; `--strict` and gate G4 count them as missing:"
        )
        lines += [f"- {r.where}: {r.text}" for r in bad]
    return "\n".join(lines)
