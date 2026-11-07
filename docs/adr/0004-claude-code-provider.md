# ADR-0004: Provider `claude-code` calls the model through the Claude Code CLI
Status: accepted    Date: 2026-11-01

## Context
Teams that already pay for Claude Code asked to run evals without creating and guarding an API
key. The `claude -p` CLI authenticates with the developer's own login and accepts a prompt on
the command line, returning JSON.

## Drivers (non-functional requirements)
No credential on laptops; bounded calls (timeout, output size); the same `Completion` port as
the SDK providers; CI still possible.

## Options considered
1. API key only (status quo).
2. Claude Agent SDK as a dependency: full featured, but a dependency and a key.
3. Subprocess to the `claude` CLI with `--output-format json --max-turns 1`, behind the
   offline guard; CI uses `CLAUDE_CODE_OAUTH_TOKEN` in the nightly environment only.

## Decision and rationale
Option 3 (`adapters/claude_code.py`). It adds no SDK to the project and keeps the key off the
machine; the adapter is ~40 lines with its own tests.

## Consequences (+/−)
+ Zero secrets locally; the human's subscription pays for the evals.
− Depends on the CLI's output format and on `npm` in the nightly job.
− Not suitable as the production adapter of a deployed service (the CLI is a developer tool).

## Fitness function
`tests/test_scaffold.py::test_claude_code_provider_has_no_sdk_and_an_adapter`; the e2e matrix
includes `--provider claude-code`.

## Reversibility: high
