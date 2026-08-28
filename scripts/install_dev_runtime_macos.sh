#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEV_ROOT="$PROJECT_ROOT/.dev-runtime"
VENV="$DEV_ROOT/venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
APP_ROOT="$HOME/Library/Application Support/HEIBA/DaVinciASR"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "This development installer currently supports macOS Apple Silicon only." >&2
    exit 1
fi

"$PYTHON_BIN" -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version'

mkdir -p "$DEV_ROOT" "$APP_ROOT/logs"
if [[ ! -x "$VENV/bin/python" ]]; then
    "$PYTHON_BIN" -m venv --copies "$VENV"
fi

"$VENV/bin/python" -m pip install --disable-pip-version-check --upgrade pip
"$VENV/bin/python" -m pip install --disable-pip-version-check -r "$PROJECT_ROOT/requirements/direct.txt"

cp "$SCRIPT_DIR/dev-runtime-launcher.sh" "$DEV_ROOT/DaVinci ASR"
chmod 755 "$DEV_ROOT/DaVinci ASR"

"$DEV_ROOT/DaVinci ASR" --data-root "$APP_ROOT" doctor --cpu

echo "Development Runtime installed: $DEV_ROOT"
echo "Open the DaVinci ASR Lua window to start its private Runtime on demand."
