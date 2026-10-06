#!/usr/bin/env bash
# Stop both services, then start them again; returns only when the watcher is READY.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/scripts/stop.sh"
exec "$ROOT/scripts/start.sh"
