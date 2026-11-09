"""Fitness function of ADR-0012: the generated LLM skeleton is async-first.

The rendered templates are checked as text (the contract's names) and by running them: the
telemetry module proves the three request-id behaviours, the eval runner takes a synchronous
and a coroutine entry under `--concurrency 2` and prints the same report as a serial run.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from rumbo.scaffold import Config, plan_files

ANTHROPIC = Config(name="x", package="x", llm=True, provider="anthropic", rigor="mvp")
CLAUDE_CODE = Config(name="y", package="y", llm=True, provider="claude-code", rigor="mvp")
DATA = Config(name="z", package="z", llm=False, kind="data", rigor="mvp")


def _text(plan: dict[str, str | Path], path: str) -> str:
    item = plan[path]
    assert isinstance(item, str), path
    return item


@pytest.mark.parametrize("cfg", [ANTHROPIC, CLAUDE_CODE], ids=["anthropic", "claude-code"])
def test_llm_skeleton_is_async(cfg: Config) -> None:
    plan = plan_files(cfg)
    pkg = f"src/{cfg.package}"
    assert "async def __call__(self, prompt: str" in _text(plan, f"{pkg}/app/ports.py")
    entrypoint = _text(plan, f"{pkg}/app/entrypoint.py")
    assert "async def answer(request" in entrypoint and "return await _answer(" in entrypoint
    telemetry = _text(plan, f"{pkg}/app/telemetry.py")
    assert "REQUEST_ID: ContextVar[str | None]" in telemetry
    assert "def current_request_id() -> str | None" in telemetry
    assert "@contextmanager\ndef span(" in telemetry  # synchronous: wraps sync and async code
    assert "asyncio.gather" in _text(plan, f"{pkg}/app/budget.py")
    assert "async def health()" in _text(plan, f"{pkg}/api/http.py")
    assert "return await answer(request)" in _text(plan, f"{pkg}/api/http.py")
    runner = _text(plan, "evals/run_evals.py")
    assert '"--concurrency"' in runner and "inspect.iscoroutinefunction(entry)" in runner
    assert "asyncio.Semaphore(concurrency)" in runner and "asyncio.to_thread(entry" in runner
    assert f"{cfg.package}.app.entrypoint:answer" in runner
    for test in ("test_entrypoint", "test_telemetry", "test_budget"):
        assert "asyncio.run(" in _text(plan, f"tests/unit/{test}.py"), test


def test_claude_code_adapter_is_an_asyncio_subprocess() -> None:
    plan = plan_files(CLAUDE_CODE)
    adapter = _text(plan, "src/y/adapters/claude_code.py")
    assert "async def complete(" in adapter
    assert "await asyncio.create_subprocess_exec(" in adapter
    assert "asyncio.wait_for(proc.communicate(), timeout_s)" in adapter
    assert "import subprocess" not in adapter  # no blocking call left
    assert adapter.index("require_live(") < adapter.index("shutil.which(")  # offline guard first
    assert "def parse_result(stdout: str, *, max_chars: int) -> str:" in adapter
    assert "async def complete(prompt: str, *, max_tokens: int = 1024) -> str:" in _text(
        plan_files(ANTHROPIC), "src/x/adapters/llm.py"
    )
    tests = _text(plan, "tests/unit/test_claude_code_adapter.py")
    assert "create_subprocess_exec" in tests and "asyncio.run(complete(" in tests


def test_data_project_runner_keeps_a_synchronous_entry() -> None:
    plan = plan_files(DATA)
    runner = _text(plan, "evals/run_evals.py")
    assert "z.pipeline:run" in runner and "asyncio.to_thread(entry" in runner
    assert "def run(request: dict[str, Any]) -> dict[str, Any]:" in _text(plan, "src/z/pipeline.py")
    assert "src/z/app/telemetry.py" not in plan
    http = _text(plan_files(Config(name="s", package="s", llm=False)), "src/s/api/http.py")
    assert "async def health()" in http and "answer" not in http


# --- the rendered telemetry module, executed -----------------------------------------------


@pytest.fixture
def telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    package = "adr12_pkg"
    plan = plan_files(Config(name="t", package=package, llm=True, provider="anthropic"))
    app = tmp_path / package / "app"
    app.mkdir(parents=True)
    (tmp_path / package / "__init__.py").write_text("", encoding="utf-8")
    (app / "__init__.py").write_text("", encoding="utf-8")
    for name in ("budget", "telemetry"):
        (app / f"{name}.py").write_text(_text(plan, f"src/{package}/app/{name}.py"), "utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield importlib.import_module(f"{package}.app.telemetry")
    for name in [m for m in sys.modules if m.split(".")[0] == package]:
        del sys.modules[name]


def test_nested_spans_share_the_outermost_request_id(telemetry: ModuleType) -> None:
    assert telemetry.current_request_id() is None
    with telemetry.span("answer") as outer, telemetry.span("model_call") as inner:
        assert inner.attributes["request.id"] == outer.attributes["request.id"]
        assert telemetry.current_request_id() == outer.attributes["request.id"]
        assert len(outer.attributes["request.id"]) == 32
    assert telemetry.current_request_id() is None


def test_concurrent_tasks_get_their_own_request_id(telemetry: ModuleType) -> None:
    async def one_request() -> str:
        with telemetry.span("answer"):
            await asyncio.sleep(0)
            inner = telemetry.current_request_id()
            assert isinstance(inner, str)
            return inner

    async def two_requests() -> tuple[str, str]:
        return await asyncio.gather(one_request(), one_request())

    first, second = asyncio.run(two_requests())
    assert first != second


def test_an_explicit_request_id_wins_and_is_restored(telemetry: ModuleType) -> None:
    with telemetry.span("answer") as outer:
        with telemetry.span("replay", request_id="given") as inner:
            assert inner.attributes["request.id"] == "given"
            assert telemetry.current_request_id() == "given"
        assert telemetry.current_request_id() == outer.attributes["request.id"]
    assert telemetry.current_request_id() is None


# --- the rendered eval runner, executed ----------------------------------------------------

ENTRIES = '''
"""Two entry points for the smoke dataset: a plain function and a coroutine function."""

import asyncio
from typing import Any


def sync_entry(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("question") == "ping":
        return {"answer": "pong"}
    return {"abstain": True, "reason": "no data"}


async def async_entry(request: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(0.01)
    return sync_entry(request)
'''


def _materialize_evals(cfg: Config, root: Path) -> Path:
    for path, item in plan_files(cfg).items():
        if not path.startswith("evals/"):
            continue
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(item, str):
            target.write_text(item, encoding="utf-8")
        else:
            shutil.copyfile(item, target)
    (root / "evals" / "entries.py").write_text(ENTRIES, encoding="utf-8")
    return root / "evals" / "run_evals.py"


def _run(runner: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(runner), "--suite", "smoke", *args],
        capture_output=True,
        text=True,
        cwd=runner.parent.parent,
        timeout=120,
    )


@pytest.mark.parametrize("entry", ["sync_entry", "async_entry"])
@pytest.mark.parametrize("cfg", [ANTHROPIC, DATA], ids=["llm", "data"])
def test_rendered_runner_runs_sync_and_async_entries_concurrently(
    tmp_path: Path, cfg: Config, entry: str
) -> None:
    runner = _materialize_evals(cfg, tmp_path)
    concurrent = _run(runner, "--entry", f"entries:{entry}", "--concurrency", "2")
    assert concurrent.returncode == 0, concurrent.stdout + concurrent.stderr
    lines = concurrent.stdout.splitlines()
    assert lines[:2] == ["PASS example-001 (1/1) ", "PASS example-002 (1/1) "]  # case order kept
    assert lines[-1] == "cases=2 k=1  pass@1=1.000  pass^1=1.000"
    reports = sorted((tmp_path / "evals" / "reports").glob("smoke-*.json"))
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert report["concurrency"] == 2
    assert [case["id"] for case in report["cases"]] == ["example-001", "example-002"]

    serial = _run(runner, "--entry", f"entries:{entry}", "--k", "2")
    assert serial.returncode == 0, serial.stdout + serial.stderr
    assert serial.stdout.splitlines()[-1] == "cases=2 k=2  pass@1=1.000  pass^2=1.000"
    assert concurrent.stdout == _run(runner, "--entry", f"entries:{entry}").stdout  # N=1 == N=2


def test_rendered_runner_counts_a_failing_entry_and_rejects_zero_concurrency(
    tmp_path: Path,
) -> None:
    runner = _materialize_evals(DATA, tmp_path)
    (tmp_path / "evals" / "entries.py").write_text(
        ENTRIES + "\n\nasync def boom(request):\n    raise RuntimeError('down')\n", "utf-8"
    )
    failing = _run(runner, "--entry", "entries:boom", "--concurrency", "3")
    assert failing.returncode == 1
    assert "FAIL example-001 (0/1) exception: RuntimeError('down')" in failing.stdout
    assert "pass@1=0.000" in failing.stdout
    assert _run(runner, "--concurrency", "0").returncode == 2  # argparse usage error


# --- the eval fingerprint (ADR-0016), executed -----------------------------------------------

EMPTY = hashlib.sha256(b"").hexdigest()


def _project(tmp_path: Path, cfg: Config = ANTHROPIC) -> tuple[Path, Path, Path]:
    runner = _materialize_evals(cfg, tmp_path)
    prompts = tmp_path / "src" / cfg.package / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    prompt = prompts / "system.md"
    prompt.write_bytes(b"You answer briefly.\nBe precise.\n")
    return runner, prompt, tmp_path / "evals" / "graders" / "judge.py"


def _last_report(root: Path) -> dict[str, Any]:
    reports = sorted((root / "evals" / "reports").glob("smoke-*.json"))
    return json.loads(reports[-1].read_text(encoding="utf-8"))


def _warnings(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.startswith("WARNING")]


def test_report_carries_the_fingerprint_and_it_follows_prompts_and_judge(tmp_path: Path) -> None:
    runner, prompt, judge = _project(tmp_path)
    assert _run(runner, "--entry", "entries:sync_entry").returncode == 0
    first = _last_report(tmp_path)["fingerprint"]
    assert set(first) == {"prompts", "judge", "entry"}
    assert (
        first["prompts"]
        == hashlib.sha256(  # sorted relative path and content, NUL-framed
            b"system.md\0You answer briefly.\nBe precise.\n\0"
        ).hexdigest()
    )
    assert first["judge"] == hashlib.sha256(judge.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    assert first["entry"] == hashlib.sha256(b"entries:sync_entry").hexdigest()

    prompt.write_text("You answer at length.\n", encoding="utf-8")
    judge.write_text(judge.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    assert _run(runner, "--entry", "entries:async_entry").returncode == 0
    second = _last_report(tmp_path)["fingerprint"]
    assert all(second[part] != first[part] for part in ("prompts", "judge", "entry"))


def test_live_run_records_evals_fingerprint_json(tmp_path: Path) -> None:
    runner, _, _ = _project(tmp_path)
    record = tmp_path / "evals" / "fingerprint.json"
    assert not record.exists()
    assert _run(runner, "--entry", "entries:sync_entry").returncode == 0
    assert not record.exists()  # an offline run never records
    live = _run(runner, "--entry", "entries:sync_entry", "--live")
    assert live.returncode == 0, live.stdout + live.stderr
    text = record.read_text(encoding="utf-8")
    assert text.endswith("}\n") and text.startswith("{\n  ")  # pretty JSON, trailing newline
    data = json.loads(text)
    assert list(data) == ["prompts", "judge", "entry", "recorded", "suite"]
    assert {k: data[k] for k in ("prompts", "judge", "entry")} == _last_report(tmp_path)[
        "fingerprint"
    ]
    assert data["recorded"] == date.today().isoformat() and data["suite"] == "smoke"


def test_offline_run_warns_once_per_changed_part_and_keeps_the_exit_code(tmp_path: Path) -> None:
    runner, prompt, judge = _project(tmp_path)
    args = ("--entry", "entries:sync_entry")
    assert _run(runner, *args, "--live").returncode == 0
    recorded = json.loads((tmp_path / "evals" / "fingerprint.json").read_text("utf-8"))["recorded"]

    clean = _run(runner, *args)
    assert clean.returncode == 0 and _warnings(clean) == []

    prompt.write_text("You answer at length.\n", encoding="utf-8")
    drifted = _run(runner, *args)
    assert drifted.returncode == 0
    assert _warnings(drifted) == [
        f"WARNING: prompts changed since the last live run ({recorded}): "
        "the recorded fixtures may be stale; re-record with --live"
    ]
    lines = drifted.stdout.splitlines()  # after the summary line, nothing else follows
    assert lines[-2].startswith("cases=") and lines[-1] == _warnings(drifted)[0]

    judge.write_text(judge.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    both = _run(runner, *args)
    assert both.returncode == 0
    assert _warnings(both) == [
        _warnings(drifted)[0],
        f"WARNING: the judge changed since the last live run ({recorded}): "
        "re-calibrate before trusting its verdicts (see evals/calibration/)",
    ]

    prompt.write_text("You answer briefly.\nBe precise.\n", encoding="utf-8")
    only_judge = _run(runner, *args)
    assert len(_warnings(only_judge)) == 1 and "the judge changed" in _warnings(only_judge)[0]

    failing = _run(runner, "--entry", "entries:sync_entry", "--min-pass", "1.5")
    assert failing.returncode == 1 and len(_warnings(failing)) == 1  # exit code is the gate's


def test_offline_run_without_a_record_prints_no_warning(tmp_path: Path) -> None:
    runner, prompt, _ = _project(tmp_path)
    prompt.write_text("anything\n", encoding="utf-8")
    result = _run(runner, "--entry", "entries:sync_entry")
    assert result.returncode == 0 and "WARNING" not in result.stdout + result.stderr


def test_data_project_runner_fingerprints_with_empty_inputs(tmp_path: Path) -> None:
    runner = _materialize_evals(DATA, tmp_path)
    assert not (tmp_path / "src").exists()
    assert not (tmp_path / "evals" / "graders" / "judge.py").exists()
    assert _run(runner, "--entry", "entries:sync_entry", "--live").returncode == 0
    recorded = json.loads((tmp_path / "evals" / "fingerprint.json").read_text("utf-8"))
    assert recorded["prompts"] == EMPTY and recorded["judge"] == EMPTY
    assert recorded["entry"] == hashlib.sha256(b"entries:sync_entry").hexdigest()
    fingerprint = _last_report(tmp_path)["fingerprint"]
    assert fingerprint["prompts"] == EMPTY and fingerprint["judge"] == EMPTY
    assert _warnings(_run(runner, "--entry", "entries:sync_entry")) == []


def test_fingerprint_ignores_line_endings(tmp_path: Path) -> None:
    runner, prompt, judge = _project(tmp_path)
    assert _run(runner, "--entry", "entries:sync_entry").returncode == 0
    lf = _last_report(tmp_path)["fingerprint"]
    prompt.write_bytes(prompt.read_bytes().replace(b"\n", b"\r\n"))
    judge.write_bytes(judge.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert _run(runner, "--entry", "entries:sync_entry").returncode == 0
    assert _last_report(tmp_path)["fingerprint"] == lf
