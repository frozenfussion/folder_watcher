#!/usr/bin/env bash
# Install the two systemd *user* services (SPEC.md section 11). No sudo needed.
#
#   scripts/install_services.sh
#
# The services are installed but NEVER enabled: they have no [Install] section, so
# nothing starts at boot or login. Start them by hand with scripts/start.sh.
# Safe to run again: unchanged files are left alone.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
PY="$ROOT/.venv/bin/python"

if [[ "$(systemctl --user is-system-running 2>&1 || true)" == *"Failed to connect to bus"* ]]; then
    echo "ERROR: systemctl --user cannot reach your user session." >&2
    echo "       Run:  export XDG_RUNTIME_DIR=/run/user/\$(id -u)   and try again." >&2
    exit 1
fi
[[ -x "$PY" ]] || { echo "ERROR: $PY missing. Run: $ROOT/scripts/setup_python.sh" >&2; exit 1; }

mkdir -p "$UNIT_DIR"

write_unit() {  # write_unit <name> <content>: only touch the file if it changed
    local path="$UNIT_DIR/$1" content="$2"
    if [[ -f "$path" ]] && [[ "$(cat "$path")" == "$content" ]]; then
        echo "==> $1 unchanged"
    else
        printf '%s\n' "$content" > "$path"
        echo "==> wrote $path"
    fi
}

write_unit folder-watcher-llm.service "# Installed by $ROOT/scripts/install_services.sh. No [Install] section on purpose:
# this service never starts by itself. Start it with scripts/start.sh.
[Unit]
Description=Folder Watcher: local LLM server (llama.cpp)

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
# Trace level, so the journal records where the layers went (\"offloaded 33/33 layers to GPU\").
# It is verbose (~35 lines per request); scripts/logs.sh shows only the useful lines by default.
Environment=LLAMA_ARG_LOG_VERBOSITY=4
SyslogIdentifier=folder-watcher-llm
ExecStart=$PY -m folder_watcher llm-server
# The unit counts as started only once the model is loaded and /health answers,
# so the watcher (After=) never starts against a model that is still loading.
ExecStartPost=$PY -m folder_watcher llm-wait --timeout 280 --main-pid \$MAINPID
# always, not on-failure: llama-server exits cleanly (code 0) on SIGTERM, e.g. from
# 'systemctl --user kill', which on-failure ignores; the watcher would then wait forever.
# An explicit stop (scripts/stop.sh) is never restarted.
Restart=always
RestartSec=3
# A cold model load (5.7 GB from disk) must fit; warm loads took 3-9 s on the reference machine.
TimeoutStartSec=300"

write_unit folder-watcher.service "# Installed by $ROOT/scripts/install_services.sh. No [Install] section on purpose:
# this service never starts by itself. Start it with scripts/start.sh.
[Unit]
Description=Folder Watcher: agent that translates new documents
Requires=folder-watcher-llm.service
After=folder-watcher-llm.service

[Service]
# notify: the watcher tells systemd READY=1 only once it is watching, so
# 'systemctl --user start' returns exactly when it is safe to drop files.
Type=notify
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
SyslogIdentifier=folder-watcher
ExecStart=$PY -m folder_watcher watch
Restart=on-failure
RestartSec=3
TimeoutStartSec=120
# On stop the watcher waits up to 30 s for the current job to reach a safe point.
# 60 s is twice that, so systemd never has to kill it mid-cleanup.
TimeoutStopSec=60"

echo "==> systemctl --user daemon-reload"
systemctl --user daemon-reload

echo "==> Checking"
for unit in folder-watcher-llm.service folder-watcher.service; do
    if grep -q '^\[Install\]' "$UNIT_DIR/$unit"; then
        echo "ERROR: $unit has an [Install] section; it must not." >&2
        exit 1
    fi
    printf '   %-28s is-enabled: %s\n' "$unit" "$(systemctl --user is-enabled "$unit" 2>&1 || true)"
done
echo "==> Done. The services are installed but will not start by themselves."
echo "    Start them with:  $ROOT/scripts/start.sh"
