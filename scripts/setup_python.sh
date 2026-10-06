#!/usr/bin/env bash
# Create .venv and install the project into it (editable, with dev extras).
# No sudo needed. Safe to run more than once.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"

# Prefer the newest python3.x on the system; 3.11+ has tomllib built in.
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$(command -v "$candidate")"; break; fi
done
if [[ -z "$PYTHON" ]]; then
    echo "ERROR: no python3 found. Run: sudo bash $ROOT/scripts/system/01_base_packages.sh" >&2
    exit 1
fi
echo "==> Using $PYTHON ($("$PYTHON" --version))"

if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "ERROR: Python 3.10 or newer is required." >&2
    exit 1
fi

if [[ ! -x "$VENV/bin/python" ]]; then
    echo "==> Creating virtual environment in $VENV"
    if ! "$PYTHON" -m venv "$VENV"; then
        rm -rf "$VENV"   # remove the half-made venv so the next run starts clean
        echo "ERROR: could not create the venv (python3-venv is probably missing)." >&2
        echo "       Run: sudo bash $ROOT/scripts/system/01_base_packages.sh" >&2
        exit 1
    fi
else
    echo "==> Virtual environment already exists: $VENV"
fi

echo "==> Upgrading pip"
"$VENV/bin/python" -m pip install --quiet --upgrade pip

echo "==> Installing folder-watcher (editable) with dev extras"
"$VENV/bin/python" -m pip install --quiet -e "$ROOT[dev]"

echo "==> Installed:"
"$VENV/bin/python" -m pip list --format=columns 2>/dev/null | grep -iE '^(folder-watcher|watchdog|openai|pytest|tomli) ' || true
echo "==> Done. Activate with: source $VENV/bin/activate"
