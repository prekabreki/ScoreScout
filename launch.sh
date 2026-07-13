#!/usr/bin/env bash
# Cross-platform launcher twin of launch.bat — starts the ScoreScout web UI.
set -euo pipefail
cd "$(dirname "$0")"

# Pick an interpreter: an explicit $PYTHON wins, then the project venv
# (where the dependencies get installed), then a system python.
if [ -n "${PYTHON:-}" ]; then
    :
elif [ -x .venv/bin/python ]; then
    PYTHON=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON=python
else
    echo "Error: no Python interpreter found. Install Python 3.10+ or set \$PYTHON." >&2
    exit 1
fi

# Fail early with an actionable message if dependencies aren't installed,
# instead of dying on an ImportError deep inside app.py.
if ! "$PYTHON" -c "import importlib.util as u, sys; sys.exit(0 if u.find_spec('flask') and u.find_spec('music21') else 1)" 2>/dev/null; then
    echo "Error: dependencies are missing for '$PYTHON'." >&2
    echo "Install them with:" >&2
    echo "    $PYTHON -m pip install -r requirements.txt" >&2
    echo "or set up a virtualenv first:" >&2
    echo "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

exec "$PYTHON" app.py
