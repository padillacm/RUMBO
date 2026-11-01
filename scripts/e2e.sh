#!/usr/bin/env bash
# Prueba de extremo a extremo: monta un proyecto con `rumbo init` y exige `make check` en verde.
# Uso: scripts/e2e.sh "<flags de rumbo init>"   (por defecto: LLM + mvp)
set -euo pipefail
FLAGS=${1:-"--llm --proveedor anthropic --rigor mvp"}
RAIZ=$(cd "$(dirname "$0")/.." && pwd)
DIR=$(mktemp -d)
trap 'rm -rf "$DIR"' EXIT
cd "$DIR"
git init -q -b main
git config user.email "e2e@rumbo.local"
git config user.name "rumbo e2e"
# shellcheck disable=SC2086
uv run --project "$RAIZ" rumbo init --yes --nombre "Demo E2E" --pkg demo_e2e $FLAGS
make check
uv run --project "$RAIZ" rumbo doctor
git add -A
git commit -qm "chore: arnés RUMBO inicial"
uv run python scripts/check_tests_not_weakened.py --base HEAD
echo "E2E OK ($FLAGS)"
