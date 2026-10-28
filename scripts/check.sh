#!/usr/bin/env bash
#
# Runs the checks from .github/workflows/ci.yml locally, on one Python, so a PR can be checked
# before it's opened:
#   ruff lint and format check, pytest, the docs page check, and the action's render-and-validate
#   smoke test (run through the CLI, since the action itself only runs on GitHub).
#
# CI still runs on every PR, across Python 3.10 to 3.13. This doesn't replace that matrix.
#
# Uses .venv (created with uv if it's missing). Video tests are skipped when ffmpeg isn't installed,
# as they would be in pytest; install it (brew install ffmpeg) to run them too.
#
# Usage:  scripts/check.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v uv > /dev/null; then
    echo "error: needs uv; see https://docs.astral.sh/uv/" >&2
    exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
    echo "==> Creating .venv"
    uv venv --quiet
fi
uv pip install --quiet --python .venv/bin/python -e ".[dev]"

if ! command -v ffmpeg > /dev/null; then
    echo "note: ffmpeg isn't installed, so the video tests are skipped"
fi

echo "==> ruff"
.venv/bin/ruff check .
.venv/bin/ruff format --check .

echo "==> pytest"
.venv/bin/pytest -q

echo "==> Docs page is up to date"
.venv/bin/python scripts/build_docs.py --check

echo "==> Action smoke test"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
.venv/bin/app-preview graphic -t google.feature-graphic --title "Action test" -o "$work/fg.png"
.venv/bin/app-preview validate "$work/fg.png" -t google.feature-graphic

echo "All checks passed."
