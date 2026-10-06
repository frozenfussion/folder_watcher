#!/usr/bin/env bash
# What is running, which backend, model health, GPU memory, and the live config.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
SMI="$(command -v nvidia-smi || echo /usr/lib/wsl/lib/nvidia-smi)"

echo "== Services (never start by themselves; 'static' means they cannot be enabled)"
# Capture first: with pipefail, "systemctl ... | grep -q" would report systemctl's failure.
if [[ "$(systemctl --user is-system-running 2>&1 || true)" == *"Failed to connect to bus"* ]]; then
    echo "   systemctl --user cannot reach your session. Run:  export XDG_RUNTIME_DIR=/run/user/\$(id -u)"
else
    for u in folder-watcher-llm.service folder-watcher.service; do
        state="$(systemctl --user is-active "$u" 2>/dev/null)"; enabled="$(systemctl --user is-enabled "$u" 2>/dev/null)"
        since="$(systemctl --user show -p ActiveEnterTimestamp --value "$u" 2>/dev/null)"
        printf '   %-28s %-9s (%s)%s\n' "$u" "${state:-unknown}" "${enabled:-not installed}" \
            "$([[ "$state" == active && -n "$since" ]] && echo " since $since")"
    done
fi
echo "== llama.cpp backend: $(cat "$ROOT/vendor/BACKEND" 2>/dev/null || echo 'not built')"
port="$("$PY" -m folder_watcher config get llm.port 2>/dev/null || echo 8080)"
health="$(curl -s -m 3 "http://127.0.0.1:$port/health" 2>/dev/null)"
echo "== Model server (port $port): ${health:-not answering}"
if [[ -x "$SMI" ]]; then
    echo "== GPU memory in use: $("$SMI" --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null | sed 's/, / of /')"
else
    echo "== GPU: none found (CPU mode)"
fi
echo "== Live config: watch.enabled = $("$PY" -m folder_watcher config get watch.enabled 2>/dev/null)," \
     "extensions = $("$PY" -m folder_watcher config get watch.extensions 2>/dev/null)"
echo "== Preflight"
"$PY" -m folder_watcher check | tail -n +2
