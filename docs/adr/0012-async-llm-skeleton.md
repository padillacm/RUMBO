# ADR-0012: The LLM skeleton is async-first, with the request id propagated by a context variable
Status: accepted    Date: 2026-11-07

## Context
The generated LLM skeleton is synchronous: `Completion.__call__` and `answer` are plain `def`,
the Claude Code adapter blocks on `subprocess.run`, `Meter` says it is not thread-safe, every
`span` invents its own `request.id` so two model calls inside one request do not share an
identifier, and `evals/run_evals.py` runs the cases one after another (100 cases with `--k 3`
are 300 serial calls). FastAPI, `httpx`, SQLAlchemy 2 and every current model SDK
(`AsyncAnthropic`, `AsyncOpenAI`) are async-native. The skeleton is three small files and an
adapter stub that the team replaces in its first ANCLA task: converting it now is cheap,
converting it under real code later is not.

## Drivers (non-functional requirements)
No new dependency in the generated project (no pytest-asyncio: tests use `asyncio.run`);
the `data` kind's synchronous `pipeline.run` keeps working with the same eval runner; the
offline guard and the budget semantics are unchanged; a request's spans share one id without
threading it through every signature; concurrency in the evals is bounded and the report keeps
the case order.

## Options considered
1. Stay synchronous; add a context variable for the id and threads in the eval runner.
2. Async-first: `async def` port, entry point and adapters; the eval runner awaits coroutine
   entries and runs synchronous ones in a thread; a context variable carries the request id.
3. Both signatures side by side (`Completion` and `AsyncCompletion`, `answer` and
   `answer_async`): twice the skeleton to keep in sync.

## Decision and rationale
Option 2. The contract:

**Port and entry point.** `app/ports.py`: `class Completion(Protocol): async def __call__(self,
prompt: str, *, max_tokens: int = 1024) -> str`. `live()` and `require_live()` are unchanged.
`app/entrypoint.py`: `async def answer(request: dict[str, Any]) -> dict[str, Any]` and an async
`_answer`; same behaviour (ping → pong, offline abstention, budget abstention).

**Adapters.** `adapters/llm.py`: `default_completion()` returns an async `complete`.
`adapters/claude_code.py`: `async def complete(...)` built on `asyncio.create_subprocess_exec`
and `asyncio.wait_for(timeout_s)`; `parse_result` unchanged; the offline guard first, as today.

**Request id.** `app/telemetry.py` holds `REQUEST_ID: ContextVar[str | None]` and
`current_request_id()`. `span(...)` stays a synchronous `@contextmanager` (usable inside async
code): an explicit `request_id` argument sets the variable for the span's duration (token reset
on exit); otherwise the span uses the current value; if there is none it generates one and sets
it, so the outermost span of a request establishes the id and nested or concurrent spans
inherit it (`asyncio` copies the context into every task).

**Budget.** `Meter` keeps its explicit signature: a budget must be visible where it is spent.
Its docstring states what is now true: safe under `asyncio.gather` within one request (one
thread, no `await` inside `charge`), not shared across threads.

**HTTP.** `api/http.py` keeps the health endpoint, declared `async def`, and a comment showing
how a route awaits `answer`.

**Eval runner.** `evals/run_evals.py` gains `--concurrency N` (default 1). An `async` driver
runs the cases under `asyncio.Semaphore(N)`; a coroutine entry (`inspect.iscoroutinefunction`)
is awaited, a synchronous one runs in `asyncio.to_thread`; grading is synchronous; the rows are
assembled in case order and the report records `concurrency`. With `N = 1` the output is
identical to today's.

**Generated tests.** `test_entrypoint.py`, `test_claude_code_adapter.py`, `test_telemetry.py`
and `test_budget.py` drive the coroutines with `asyncio.run`; `test_telemetry.py` proves that
nested spans share the id and that two concurrent tasks get their own.

## Consequences (+/−)
+ The skeleton matches the SDKs and frameworks the team will plug in; no later migration.
+ One request, one id, in every span, with no signature change.
+ Large live suites finish in a fraction of the time with `--concurrency`.
− Teams unfamiliar with `asyncio` meet it on day one; the templates are short and commented.
− `asyncio.run` in tests instead of a plugin; a project may add pytest-asyncio later.
− The budget's time limit is still enforced between calls; a concurrent `gather` must pass
  `meter.remaining_seconds` to each call, as the docstring says.

## Fitness function
`tests/test_async_templates.py`: the rendered LLM templates define `async def answer`, an async
`Completion`, `ContextVar` and `current_request_id` in telemetry, `create_subprocess_exec` in
the Claude Code adapter, `--concurrency` and `iscoroutinefunction` in the runner; the rendered
`run_evals.py` still accepts a synchronous entry. The e2e script runs the smoke suite of an LLM
project with `--concurrency 2` and requires the same pass rate. The generated tests run in
every LLM e2e flag set.

## Reversibility: medium
Three templates and their tests go back to `def`; projects already generated keep whichever
version they were born with.
