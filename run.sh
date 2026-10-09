#!/usr/bin/env bash
# Databender launcher: creates a local virtualenv and installs dependencies on first run.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  command -v python3 >/dev/null || { echo "Python 3.10+ is required." >&2; exit 1; }
  echo "First run: setting up .venv (one-time)..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi
exec .venv/bin/python -m databender "$@"
