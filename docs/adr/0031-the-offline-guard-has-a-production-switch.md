# ADR-0031: The offline guard has a production switch
Status: accepted    Date: 2026-11-16

Completes ADR-0003 (the check is offline; a model credential lives only in the nightly job).
ADR-0003 stands: the check never reaches the network and offline remains the default.

## Context
ADR-0003 gave every generated LLM project an offline guard: `require_live()` in `app/ports.py`
raises `OfflineError` unless `RUMBO_EVALS_LIVE=1`, and every adapter calls it first. The eval
runner sets that variable with `--live`. Nothing else ever sets it.

The audit of 2026-11-16 (finding P4) followed the generated service to production: the image
built from `infra/Dockerfile` starts with the variable unset, so the deployed service never
calls the model. The skeleton entry point abstains with `offline: no fixture for this question`
on every request. The only way out a team finds is to export a variable named after the evals
in production, which no template, runbook or README mentions.

## Drivers (non-functional requirements)
The check, the unit tests and CI must stay unable to reach the model by default, with nothing
to remember (ADR-0003). The deployed product must be able to call it without a variable whose
name says "evals". Operations must be able to cut the model off without a new image. Every
place that sets or documents the switch is generated, so a new project is correct from day one.

## Options considered
1. **Invert the guard: live by default, `RUMBO_OFFLINE=1` in the check.** Rejected: the day
   someone runs pytest, a script or the runner without the variable, the model is called with
   whatever credential is in the shell. ADR-0003 is safe because forgetting is safe.
2. **Detect "production" from the environment** (a container, no TTY, a platform variable).
   Rejected: guesses, and CI is also a container without a TTY.
3. **A second, neutral variable that only the deployment sets (chosen).**

## Decision and rationale
`app/ports.py` holds `LIVE_VARIABLES = ("RUMBO_LIVE", "RUMBO_EVALS_LIVE")`; `live()` is true
when either is exactly `1`. The error of `require_live()` names both and says where each is set.

- **`RUMBO_LIVE=1`, the production switch.** `infra/Dockerfile` sets it (`ENV RUMBO_LIVE=1`) in
  the runtime stage of an LLM project (kinds `service` and `data`). A library or a cli has no
  image: the application that embeds it sets the variable where it is deployed, and the README
  says so. Overriding it with an empty value at deploy time is the kill switch: every request
  abstains, without a new image.
- **`RUMBO_EVALS_LIVE=1`** keeps its one owner, the eval runner with `--live`.
- **Offline stays enforced where it matters.** A run of `evals/run_evals.py` without `--live`
  removes both variables from its environment before calling the entry point, so the `evals`
  step of the check is offline whatever the caller's shell holds. No workflow, Makefile target
  or step of `[tool.rumbo.checks]` sets `RUMBO_LIVE`.
- **Documented in every generated place:** the README, `.env.example` (blank, "leave it blank
  on a laptop"), `AGENTS.md` (never set either variable in a test, the check or CI) and a
  runbook entry ("Every request abstains with reason `offline`").

## Consequences (+/−)
+ A deployed service calls the model with no undocumented step, and the variable's name says
  what it is.
+ Operations get a kill switch that needs no deploy of code.
+ ADR-0003 is unchanged for the check, the tests and CI.
− A developer who exports `RUMBO_LIVE=1` in a shell makes the product live there: unit tests
  that must be offline remove both variables (the generated ones do), and the runner removes
  them for the smoke evals; a hand-written script is on its own.
− Two variables to explain instead of one.
− Projects generated before this ADR keep the single variable until the human copies
  `app/ports.py` (a document of the project: `rumbo update` does not touch `src/`).

## Fitness function
`tests/test_templates.py::test_model_is_unreachable_without_a_live_variable` (the rendered
`ports.py`, executed: neither variable, each one, values other than `1`),
`::test_production_switch_is_set_where_the_product_is_deployed_and_documented` (the Dockerfile
sets it only with an LLM; no workflow, Makefile or checks table names it) and
`::test_offline_run_ignores_live_variables_of_the_environment` (the rendered runner with both
variables exported). In every generated LLM project: `tests/unit/test_entrypoint.py`.

## Reversibility: high
One tuple in `app/ports.py` and one `ENV` line; removing `RUMBO_LIVE` restores ADR-0003 as it
was.
