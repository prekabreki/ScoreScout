#!/usr/bin/env bash
# Cross-platform launcher twin of launch.bat — starts the Flask web UI.
set -euo pipefail
cd "$(dirname "$0")"
exec "${PYTHON:-python3}" app.py
