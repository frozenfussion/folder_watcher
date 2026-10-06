#!/usr/bin/env bash
# Install the system packages Folder Watcher needs to build llama.cpp and run Python.
#
# Run once, as root:   sudo bash scripts/system/01_base_packages.sh
# Safe to run twice: apt skips packages that are already installed.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo:  sudo bash $0" >&2
    exit 1
fi

PACKAGES=(
    build-essential   # gcc, g++, make: compile llama.cpp
    cmake             # llama.cpp build system
    git               # clone llama.cpp
    curl              # download the model
    pkg-config        # lets cmake find system libraries
    python3-venv      # create .venv (the generic package, works for any python3.x)
    python3-pip       # pip for the system python; we only ever use pip inside .venv
    libssl-dev        # optional for llama.cpp (HTTPS support); without it cmake prints a warning.
                      # libcurl4-openssl-dev is no longer used: llama.cpp deprecated LLAMA_CURL.
)

echo "==> apt-get update"
apt-get update

echo "==> Installing: ${PACKAGES[*]}"
# --no-install-recommends keeps the install small and avoids pulling in extras.
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${PACKAGES[@]}"

echo "==> Checking"
for tool in gcc g++ make cmake git curl pkg-config; do
    printf '   %-11s %s\n' "$tool" "$(command -v "$tool" || echo MISSING)"
done
echo "==> Done. Tell Claude it finished so it can verify."
