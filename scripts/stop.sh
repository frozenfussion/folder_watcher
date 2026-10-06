#!/usr/bin/env bash
# Stop the watcher and the model server, confirm both stopped and the GPU memory was freed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/vendor/llama.cpp/build/bin/llama-server"
SMI="$(command -v nvidia-smi || echo /usr/lib/wsl/lib/nvidia-smi)"

vram() { [[ -x "$SMI" ]] && "$SMI" --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 || true; }

before="$(vram)"
t0=$(date +%s.%N)
echo "==> Stopping the watcher, then the model server ..."
# The watcher first: it may need up to 30 s to bring a running job to a safe stop.
systemctl --user stop folder-watcher.service folder-watcher-llm.service
secs=$(printf '%.1f' "$(echo "$(date +%s.%N) - $t0" | bc)")

ok=1
for u in folder-watcher.service folder-watcher-llm.service; do
    state="$(systemctl --user is-active "$u" 2>/dev/null || true)"
    printf '    %-28s %s\n' "$u" "$state"
    [[ "$state" == "inactive" || "$state" == "failed" ]] || ok=0
done
if pgrep -f "^$SERVER" >/dev/null; then
    echo "WARNING: a llama-server from this project is still running (pid $(pgrep -f "^$SERVER" | tr '\n' ' '))" >&2
    ok=0
fi
if [[ -n "$before" ]]; then
    for _ in $(seq 20); do  # the driver may take a moment to report the memory as free
        after="$(vram)"; (( before - after > 1000 )) && break; sleep 0.5
    done
    echo "    GPU memory in use: ${before} MiB before, ${after} MiB now (freed $((before - after)) MiB)"
fi
if (( ok )); then
    echo "==> Stopped in ${secs}s. Both services are inactive."
else
    echo "ERROR: something is still running; see $ROOT/scripts/status.sh" >&2
    exit 1
fi
