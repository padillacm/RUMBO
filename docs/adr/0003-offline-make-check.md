# ADR-0003: `make check` is offline; a model credential lives only in the nightly job
Status: accepted    Date: 2026-11-01

## Context
LLM products need evals, and evals need the model. Running them on every push and pull request
puts an API key in a workflow that any contributor's branch can modify: the lethal trifecta
(private data, untrusted content, exfiltration path) inside CI.

## Drivers (non-functional requirements)
No secret reachable from a pull request; `make check` reproducible and fast on a laptop
without network; the real model still measured regularly.

## Options considered
1. Live evals in CI with the key as a repository secret (common, exfiltrable from a PR).
2. Live evals only on `main` after merge (late feedback; secret still in the PR workflow file).
3. `make check` offline with recorded fixtures and an `OfflineError` guard; the full suite runs
   nightly in a protected GitHub environment that is the only holder of the credential.

## Decision and rationale
Option 3. The smoke suite exercises the code path with fixtures on every push; the nightly
`evals-live` job (environment `evals`, required reviewers) measures the model with k=3. The
`claude-code` provider (ADR-0004) removes the key from laptops altogether.

## Consequences (+/−)
+ No PR can read a model credential; `make check` needs nothing but the repository.
+ Fixtures force the team to record real answers, which doubles as a regression dataset.
− Model regressions are detected the next morning, not on the PR.
− Every adapter must honour `require_live()`.

## Fitness function
`tests/test_scaffold.py::test_ci_never_holds_the_model_credential` and the e2e script, which
runs `make check` with every provider variable unset.

## Reversibility: high
