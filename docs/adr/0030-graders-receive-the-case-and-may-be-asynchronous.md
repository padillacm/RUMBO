# ADR-0030: Graders receive the case and may be asynchronous
Status: proposed    Date: 2026-11-16

Completes ADR-0012 (the LLM skeleton is async-first). ADR-0012 stands: it made the port, the
entry point and the adapters asynchronous and left grading synchronous on purpose, because the
only graders then were deterministic. This ADR is about the one grader that calls a model.

## Context
The generated eval runner calls every grader as `grader(output, expected)`, synchronously. The
audit of 2026-11-16 (finding P9) found that the LLM judge of `evals/graders/judge.py` cannot be
used as it is written:

1. **It is synchronous and the adapters are not.** `Judge.complete` is `Callable[[str], str]`,
   while every adapter of the `Completion` port is a coroutine function. A judge wired to an
   adapter receives a coroutine object, calls `.strip()` on it and fails; wrapping the adapter
   in `asyncio.run` fails too, because the runner is already inside an event loop.
2. **It reads the question from the output.** `output.get("question", "")`: the question the
   judge shows the model is whatever the system under test wrote in its answer, or nothing. A
   system that echoes a different question is graded against the wrong one, and the case's
   real `input` never reaches the grader.

## Drivers (non-functional requirements)
The four deterministic graders and any two-argument grader a project already wrote keep working
without a change. No new dependency. Type annotations exact enough for pyright and mypy in the
generated project. Grading a model's answer must not block the event loop that `--concurrency`
relies on. The check stays offline: the judge's unit tests use a fake.

## Options considered
1. **Change the grader signature to `(output, expected, case_input)` for everyone.** Rejected:
   every existing grader and every dataset-specific grader of a project breaks.
2. **Keep graders synchronous and run them in a worker thread** (`asyncio.to_thread`), the judge
   calling its adapter with its own event loop. Rejected: one loop per judged case, the request
   id and the budget of ADR-0012 do not cross it, and the input problem is still there.
3. **The runner adapts to the grader (chosen).** It reads the signature once per call: a
   grader that declares a third positional parameter receives the case `input`; a grader that
   returns an awaitable is awaited.

For the judge's model call, two shapes were considered: accept both a synchronous and an
asynchronous `complete`, or only the asynchronous one. **Only asynchronous.** One code path and
one exact type (`Callable[[str], Awaitable[str]]`), which is the shape of the `Completion` port,
so an adapter plugs in as it is. A synchronous `complete` would block the loop for the length
of a model call and silently undo `--concurrency`; a project with a synchronous SDK wraps it
(`asyncio.to_thread`), which is one line and visible. `evals/graders/` is a harness file the
human adapts: `rumbo update` never rewrites it, so no existing project is broken by the choice.

## Decision and rationale
In `evals/graders/__init__.py`:

- `Grader = Callable[..., Verdict | Awaitable[Verdict]]`.
- `takes_input(grader) -> bool`: true when `inspect.signature` shows three or more positional
  parameters, or `*args`; false when the signature cannot be read.
- `async def grade(grader, output, expected, case_input=None) -> Verdict`: calls the grader
  with two or three arguments accordingly and awaits the result when it is not a `Verdict`.

In `evals/run_evals.py`, `run_case` grades with
`await grade(GRADERS[case["grader"]], output, case["expected"], case["input"])`. A grader that
raises is still a failed case.

In `evals/graders/judge.py`: `Judge.__call__` is `async` and takes `case_input`; the question is
`case_input["question"]`, the whole input when it has no such field, and the output's own
`question` only in a two-argument call. `complete` is `Callable[[str], Awaitable[str]]`.
`calibrate` is a coroutine (`asyncio.run(calibrate(judge, rows))`), judges the rows one at a
time and passes each row's `input`; the labeled rows are
`{"id", "input", "output", "human", "expected"?}` and `evals/calibration/example.jsonl` follows
that shape.

## Consequences (+/−)
+ A calibrated judge can be registered in `GRADERS` and run by the runner, wired to the same
  adapter the product uses.
+ The judge grades against the question that was asked.
+ Existing graders and datasets are untouched; a grader that needs the input only has to
  declare it.
− A grader is called by convention on its signature: a two-argument grader that grows an
  unrelated third positional parameter will receive the input there.
− Projects generated before this ADR keep the old judge until the human copies the new files
  (`rumbo update` lists `evals/graders/` and `evals/run_evals.py` as differing).
− Calibration rows written with the question inside `output` still work in a two-argument
  call, but through `calibrate` they need an `input`.

## Fitness function
`tests/test_templates.py::test_runner_passes_the_case_input_and_awaits_asynchronous_graders`
(the rendered runner, executed, with a two-argument, a three-argument and a coroutine grader),
`::test_grade_calls_each_grader_the_way_its_signature_asks` and
`::test_judge_is_asynchronous_and_reads_the_question_from_the_case_input`. In every generated
project: `tests/unit/test_graders.py` and `tests/unit/test_judge.py`.

## Reversibility: high
Three functions in harness files; the previous behaviour is the two-argument branch of `grade`.
