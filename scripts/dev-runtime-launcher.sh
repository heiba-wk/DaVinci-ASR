#!/bin/bash
set -euo pipefail

DEV_ROOT="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$DEV_ROOT/.." && pwd)"
PRIVATE_PYTHON="$DEV_ROOT/venv/bin/python"

if [[ ! -x "$PRIVATE_PYTHON" ]]; then
    echo "DaVinci ASR development Runtime is incomplete." >&2
    exit 1
fi

unset PYTHONHOME
unset PYTHONPATH
exec "$PRIVATE_PYTHON" "$PROJECT_ROOT/runtime/main.py" "$@"
