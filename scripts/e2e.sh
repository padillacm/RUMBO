#!/usr/bin/env bash
# End-to-end check: mounts a project with `rumbo init` and requires `scripts/rumbo_check.py`
# (ADR-0006) to be green, with `make check` kept as its alias.
# Usage: scripts/e2e.sh "<rumbo init flags>"   (default: LLM + mvp)
set -euo pipefail
FLAGS=${1:-"--llm --provider anthropic --rigor mvp"}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DIR=$(mktemp -d)
trap 'rm -rf "$DIR"' EXIT
cd "$DIR"
git init -q -b main
git config user.email "e2e@rumbo.local"
git config user.name "rumbo e2e"
git config commit.gpgsign false
# shellcheck disable=SC2086
uv run --project "$ROOT" rumbo init --yes --name "Demo E2E" --package demo_e2e $FLAGS
uv run python scripts/rumbo_check.py
make check                                        # the Makefile alias still works where make exists
uv run --project "$ROOT" rumbo check --dir .      # the CLI runs the same module
uv run --project "$ROOT" rumbo doctor
# ADR-0010: a fresh library or cli project passes G5 (its ops items are all generated); a service
# or data project cannot yet: its runbook and SLOs still carry the template mark from mvp on.
if echo "$FLAGS" | grep -Eq -- "--kind (library|cli)"; then uv run --project "$ROOT" rumbo gate G5 --no-run; fi
git add -A
git commit -qm "chore: initial RUMBO harness"
uv run python scripts/check_tests_not_weakened.py --base HEAD
# an unknown step is a broken environment (exit 3), never exit 2 (block in Claude Code hooks)
rc=0; uv run python scripts/rumbo_check.py nosuchstep || rc=$?; test "$rc" -eq 3
# the guard hook blocks edits to protected files (exit 2) and lets ordinary ones through
rc=0; echo '{"tool_name":"Edit","tool_input":{"file_path":"Makefile","old_string":"a","new_string":"b"}}' | uv run python scripts/rumbo_guard.py pre-tool || rc=$?; test "$rc" -eq 2
rc=0; echo '{"tool_name":"Edit","tool_input":{"file_path":"scripts/rumbo_check.py","old_string":"a","new_string":"b"}}' | uv run python scripts/rumbo_guard.py pre-tool || rc=$?; test "$rc" -eq 2
echo "x: ; echo" >> Makefile; rc=0; echo '{"tool_name":"Bash","tool_input":{"command":"true"}}' | uv run python scripts/rumbo_guard.py post-tool || rc=$?; test "$rc" -eq 2; git checkout -- Makefile
echo '{"tool_name":"Bash","tool_input":{"command":"uv run python scripts/rumbo_check.py --brief"}}' | uv run python scripts/rumbo_guard.py pre-tool
echo '{}' | RUMBO_GUARD_SKIP_CHECK=1 uv run python scripts/rumbo_guard.py stop
# ADR-0008: the same guard runs as git hooks (installed by `rumbo init` through pre-commit), so any
# agent is refused at commit time; the human (rumbo init set rumbo.human in this clone) passes.
test "$(git config --get rumbo.human)" = "true"
echo "x: ; echo" >> Makefile; git add Makefile
rc=0; CLAUDECODE=1 git commit -qm "chore: touch the Makefile" || rc=$?; test "$rc" -ne 0
git restore --staged Makefile; git checkout -- Makefile
echo "x: ; echo" >> Makefile; git add Makefile
env -u CLAUDECODE -u GEMINI_CLI -u CURSOR_AGENT -u CODEX_SANDBOX_NETWORK_DISABLED git commit -qm "chore: touch the Makefile"
git revert -q --no-edit HEAD
rc=0; CLAUDECODE=1 git commit -q --allow-empty -m "x [tests-change-approved]" || rc=$?; test "$rc" -ne 0
# the harness must stay offline: no model credential in the environment
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u GEMINI_API_KEY -u MISTRAL_API_KEY uv run python scripts/rumbo_check.py --brief
uv run --project "$ROOT" rumbo gate G4 --no-run >/dev/null
uv run --project "$ROOT" rumbo update
# ADR-0011: a prototype grows into an mvp with one command (new steps appended, their tools added
# with `uv add --dev`); the runner must stay green with the whole mvp table.
if [ "$FLAGS" = "--no-llm --rigor prototype" ]; then
  uv run --project "$ROOT" rumbo promote mvp
  test "$(grep -c '^audit = ' pyproject.toml)" -eq 1 && test -f .github/workflows/nightly.yml
  uv run python scripts/rumbo_check.py
  rc=0; uv run --project "$ROOT" rumbo promote mvp || rc=$?; test "$rc" -eq 1   # equal rigor: refused
fi
if echo "$FLAGS" | grep -q production; then make sbom && test -s sbom.cdx.json; fi
echo "E2E OK ($FLAGS)"
