#!/usr/bin/env bash
# Fire the UCONN CAD PACK dashboard.
#
# Inside the benchmark repo (submissions/UCONN-CAD-PACK/):
#     ./submissions/UCONN-CAD-PACK/run_dashboard.sh
# From a standalone clone of this folder, point it at the benchmark repo:
#     NEURALCAD_REPO=/path/to/IDETC26-Hackathon-Autodesk-neuralCAD-Edit ./run_dashboard.sh
#
# Either way the benchmark repo needs its setup done first: uv sync at its
# root and the dataset extracted to data/edit_192_external. Then open
# http://127.0.0.1:8050
set -e

SUB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REPO="${NEURALCAD_REPO:-}"
if [ -z "$REPO" ] && [ -f "$SUB/../../src/config/edit_192_external.json" ]; then
    REPO="$(cd "$SUB/../.." && pwd)"
fi
if [ -z "$REPO" ] || [ ! -f "$REPO/src/config/edit_192_external.json" ]; then
    echo "error: benchmark repo not found." >&2
    echo "Set NEURALCAD_REPO=/path/to/IDETC26-Hackathon-Autodesk-neuralCAD-Edit" >&2
    exit 1
fi
export NEURALCAD_REPO="$REPO"

# free the port if a previous instance is still holding it
pkill -f tools/dashboard.py 2>/dev/null || true
sleep 1

cd "$REPO"
PY="$REPO/.venv/bin/python"
if [ -x "$PY" ]; then
    PYTHONPATH="$REPO:$SUB" exec "$PY" "$SUB/tools/dashboard.py"
fi
echo "note: $PY not found, falling back to 'uv run'" >&2
PYTHONPATH="$REPO:$SUB" exec uv run python "$SUB/tools/dashboard.py"
