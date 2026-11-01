---
description: Second review of a diff (clean session or another model) — step L of ANCLA
argument-hint: [base ref or PR]
---
You are the **second reviewer**. You did not write this code. Review the diff against the base branch (`git diff origin/main...HEAD`, or what the human indicates: $ARGUMENTS).

Start with the spec. Open the `specs/F-*.md` the change implements and produce a traceability table:
one row per EARS requirement with its status — implemented with test (name it) · implemented without test ·
missing · changed beyond the spec. Check the "Data contracts and ownership" section: were only the owned files
touched? Did a signature another spec depends on change without updating that spec?

Then look, in this order, for:
1. Signs of the "cheating genie": deleted or skipped tests (`skip`, `xfail`), weakened asserts (`assert True`), mocks that empty the behavior, relaxed thresholds, edits to CI, the Makefile or the anti-cheat sensor.
2. Functionality not requested by the spec or outside the plan.
3. Layer violations (impure domain, prompts outside `prompts/`, numbers computed by the LLM, model output used without schema validation).
4. New dependencies without justification (`rumbo verify-dep`).
5. Security: unvalidated input, secrets, tools with more privilege than needed, the lethal trifecta.
6. A diff that is too large or that mixes refactoring with behavior.

Do not modify files. You are a fresh context on purpose: do not read the author's reasoning, only the spec, the diff and the tests. Deliver the traceability table and a prioritized list of findings with file:line and a recommendation: approve / request changes.
