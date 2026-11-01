#!/usr/bin/env bash
# End-to-end check: mounts a project with `rumbo init` and requires `make check` to be green.
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
make check
uv run --project "$ROOT" rumbo doctor
git add -A
git commit -qm "chore: initial RUMBO harness"
uv run python scripts/check_tests_not_weakened.py --base HEAD
# the harness must stay offline: no model credential in the environment
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u GEMINI_API_KEY -u MISTRAL_API_KEY make check >/dev/null
uv run --project "$ROOT" rumbo gate G4 --no-run >/dev/null
uv run --project "$ROOT" rumbo update
if echo "$FLAGS" | grep -q production; then make sbom && test -s sbom.cdx.json; fi
echo "E2E OK ($FLAGS)"
