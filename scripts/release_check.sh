#!/usr/bin/env bash
# Factory release gate: static checks, all tests (which include the golden-spec replay against
# the committed baseline), and a final explicit comparison. Any failure blocks the release.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest -q
uv run python -m factory.cli evals run --model replay --backend local --out evals/runs/release-check.json
uv run python -m factory.cli evals compare evals/baseline.scripted.json evals/runs/release-check.json
echo "RELEASE CHECK PASSED"
