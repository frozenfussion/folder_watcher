#!/usr/bin/env bash
# Start the model server and the watcher (by hand; they never start by themselves).
# Returns only when the watcher is READY: model loaded and source/ being watched.
# Files dropped before that point would count as "already there" and be ignored.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
UNITS=(folder-watcher-llm.service folder-watcher.service)

for u in "${UNITS[@]}"; do
    [[ -f "$UNIT_DIR/$u" ]] || { echo "ERROR: $u is not installed. Run: $ROOT/scripts/install_services.sh" >&2; exit 1; }
done
# Capture first: with pipefail, "systemctl ... | grep -q" would report systemctl's failure.
if [[ "$(systemctl --user is-system-running 2>&1 || true)" == *"Failed to connect to bus"* ]]; then
    echo "ERROR: systemctl --user cannot reach your user session." >&2
    echo "       Run:  export XDG_RUNTIME_DIR=/run/user/\$(id -u)   and try again." >&2
    exit 1
fi
if systemctl --user is-active --quiet folder-watcher.service; then
    echo "==> Already running. Drop files into $ROOT/source/ (status: $ROOT/scripts/status.sh)"
    exit 0
fi

since="$(date '+%Y-%m-%d %H:%M:%S')"
t0=$(date +%s.%N)
echo "==> Starting the model server, then the watcher. Loading the model can take a minute ..."
# Blocks until both units are up: the model unit waits for /health, the watcher reports
# READY to systemd only once it is watching. systemd's own timeouts limit the wait.
if ! systemctl --user start folder-watcher.service; then
    echo "ERROR: the services did not start. The log lines from this attempt:" >&2
    journalctl --user -u folder-watcher-llm -u folder-watcher --since "$since" --no-pager -o short --no-hostname \
        | grep -vE 'folder-watcher-llm\[[0-9]+\]: [0-9.]+ I (slot|srv  log_server|load_tensors: layer)' | tail -n 40 >&2
    echo "More detail:  $ROOT/scripts/logs.sh all      Preflight:  $ROOT/.venv/bin/python -m folder_watcher check" >&2
    # Leave nothing half-started: with Restart=always systemd would keep retrying in the background.
    systemctl --user stop folder-watcher.service folder-watcher-llm.service 2>/dev/null || true
    systemctl --user reset-failed folder-watcher.service folder-watcher-llm.service 2>/dev/null || true
    echo "Both services were stopped again. Fix the problem above, then run start.sh again." >&2
    exit 1
fi
for u in "${UNITS[@]}"; do
    systemctl --user is-active --quiet "$u" || { echo "ERROR: $u is not active after start" >&2; exit 1; }
done
secs=$(printf '%.1f' "$(echo "$(date +%s.%N) - $t0" | bc)")
echo "==> READY after ${secs}s: you can drop files into $ROOT/source/ now."
echo "    Follow the agent:  $ROOT/scripts/logs.sh      Stop:  $ROOT/scripts/stop.sh"
