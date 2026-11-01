<!-- rumbo:template — delete this line when the document is complete. Only the HUMAN removes it; `rumbo gate` checks it. -->
# F-000N: <name>
Story: As a <user> I want <capability> so that <benefit>.

## Requirements (EARS)
<!-- Ubiquitous: The system shall… · Event: When…, the system shall… · State: While…, the system shall…
     Unwanted: If…, then the system shall… · Optional: Where…, the system shall… -->
- R1 (event): When …, the system shall …
- R2 (unwanted): If …, then the system shall …

## Acceptance criteria
<!-- pytest tests / Hypothesis properties that verify them (path::test). A test covers a requirement
     by mentioning its full id, e.g. `F-000N.R1`, in its docstring; `rumbo trace` builds the table. -->

## Data contracts and ownership
<!-- What two sessions (or two agents) must agree on BEFORE writing code. Spec studies show that
     omitting explicit data structures is what breaks parallel work (88.9% vs 24.6% integration). -->
- Types / schemas: <name, fields, invariants>
- Signatures: <function(args) -> result; errors raised>
- Shared invariants: <e.g. date blocks cover the range exactly, no overlap>
- Files owned by this spec (one writer): <src/<pkg>/…, tests/…>
- Interfaces touched that other specs depend on: <none | list; notify their sessions>

## Evals (LLM products)
<!-- case IDs, threshold -->

## NFR
p95 < X s · cost < Y USD

## Out of scope
