#!/usr/bin/env bash
# Follow the logs of both services (Ctrl+C to stop following).
#   scripts/logs.sh           readable: every watcher line, only the important model-server lines
#   scripts/logs.sh all       everything, including the model server's trace output
#   scripts/logs.sh watcher   only the watcher;   scripts/logs.sh llm   only the model server
set -euo pipefail

mode="${1:-readable}"
# How far back to start. The readable view hides most model-server lines, so it looks further back.
LINES_BACK="${LINES_BACK:-$([[ "$mode" == readable ]] && echo 200 || echo 40)}"
J=(journalctl --user -f -n "$LINES_BACK" -o short --no-hostname)
case "$mode" in
    readable)
        # Model-server lines are kept only if they matter: offload, load, listening, warnings, errors.
        "${J[@]}" -u folder-watcher-llm -u folder-watcher | awk '
            # From the model server, show only the lines that matter; it prints a lot more.
            /folder-watcher-llm\[[0-9]+\]:/ &&
                !/offloaded|model buffer|model loaded|listening on|exec:|waiting for the model|healthy| W | E |error|fail/ { next }
            { print; fflush() }'

        ;;
    all)     exec "${J[@]}" -u folder-watcher-llm -u folder-watcher ;;
    watcher) exec "${J[@]}" -u folder-watcher ;;
    llm)     exec "${J[@]}" -u folder-watcher-llm ;;
    *) echo "usage: $0 [readable|all|watcher|llm]" >&2; exit 1 ;;
esac
