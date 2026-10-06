#!/usr/bin/env bash
# Install the NVIDIA CUDA toolkit (nvcc) inside WSL2 Ubuntu, so llama.cpp can be built for the GPU.
#
# Run once, as root:   sudo bash scripts/system/02_cuda_toolkit_wsl.sh
# Safe to run twice: every step checks first and skips what is already done.
# Override the package:  sudo CUDA_TOOLKIT_PKG=cuda-toolkit-13-0 bash scripts/system/02_cuda_toolkit_wsl.sh
#
# Rules from NVIDIA's "CUDA on WSL" user guide (docs.nvidia.com/cuda/wsl-user-guide):
#   * NEVER install an NVIDIA Linux driver inside WSL2. The Windows driver is already exposed
#     as /usr/lib/wsl/lib/libcuda.so, and a Linux driver package can shadow it.
#   * Use the WSL-Ubuntu repository, and install only a cuda-toolkit-X-Y package.
#     Never "cuda", "cuda-X-Y" or "cuda-drivers": those pull in the Linux driver.
#
# Repository and file names below were read from
#   https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/
# on 2026-10-06 (cuda-keyring_1.1-1_all.deb, cuda-toolkit-13-0 .. 13-3 present).
# The steps follow NVIDIA's "deb (network)" install for WSL-Ubuntu: keyring, apt update,
# install cuda-toolkit-X-Y. (The Downloads page itself is JavaScript-rendered and could not
# be read as text, so the commands were matched against the repo index and the WSL guide.)
set -euo pipefail

REPO="https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64"
KEYRING_DEB="cuda-keyring_1.1-1_all.deb"
WSL_SMI="/usr/lib/wsl/lib/nvidia-smi"

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo:  sudo bash $0" >&2
    exit 1
fi

# --- 0. Sanity checks: this must be WSL2 with the Windows NVIDIA driver visible ---
if [[ ! -e /usr/lib/wsl/lib/libcuda.so.1 || ! -x "$WSL_SMI" ]]; then
    echo "ERROR: /usr/lib/wsl/lib/libcuda.so.1 or nvidia-smi not found." >&2
    echo "       This script is only for WSL2 with an NVIDIA GPU and the Windows NVIDIA driver installed." >&2
    exit 1
fi

DRIVER_CUDA="$("$WSL_SMI" | grep -oE 'CUDA Version: *[0-9]+\.[0-9]+' | grep -oE '[0-9]+\.[0-9]+' || true)"
if [[ -z "$DRIVER_CUDA" ]]; then
    echo "ERROR: could not read 'CUDA Version' from nvidia-smi." >&2
    exit 1
fi
echo "==> Windows driver supports CUDA up to $DRIVER_CUDA"

# --- 1. Remove Linux NVIDIA driver libraries and Ubuntu's own CUDA toolkit, if present ---
# Ubuntu's "nvidia-cuda-toolkit" depends on libnvidia-compute-*, which is a Linux driver
# library that installs a second libcuda.so in /usr/lib/x86_64-linux-gnu. That is exactly
# what NVIDIA warns against on WSL2, so it goes.
#
# Select by Debian *source* package, and remove everything in ONE transaction. Removing only
# the driver packages is not enough: the leftover CUDA 12.0 runtime libs (libcudart12, ...)
# still need a libcuda provider, and apt silently installs another driver variant
# (e.g. libnvidia-compute-580-server) to satisfy them. This happened on the reference machine.
#   nvidia-cuda-toolkit          Ubuntu's CUDA 12.0 toolkit and all its libraries
#   cub, libthrust, libcudacxx   header packages it pulled in
#   nvidia-graphics-drivers-*    Linux driver userspace (libnvidia-compute-*, firmware, ...)
# NVIDIA's own WSL packages (cuda-*, libcublas, nsight-*) have other source names, so they stay.
REMOVE=()
while read -r src pkg status; do
    [[ "$status" == "installed" ]] || continue
    case "$src" in
        nvidia-cuda-toolkit|cub|libthrust|libcudacxx|nvidia-graphics-drivers-*) REMOVE+=("$pkg") ;;
    esac
done < <(dpkg-query -W -f='${source:Package} ${Package} ${db:Status-Status}\n')

if (( ${#REMOVE[@]} )); then
    echo "==> Removing ${#REMOVE[@]} Linux driver / Ubuntu CUDA packages:"
    printf '      %s\n' "${REMOVE[@]}"
    # Dry run first: if apt wants to INSTALL anything to make this removal work, it is
    # pulling a driver back in through the back door. Stop instead.
    if apt-get -s purge "${REMOVE[@]}" | grep -q '^Inst '; then
        echo "ERROR: removing these would make apt install other packages:" >&2
        apt-get -s purge "${REMOVE[@]}" | grep '^Inst ' >&2
        exit 1
    fi
    DEBIAN_FRONTEND=noninteractive apt-get purge -y "${REMOVE[@]}"
    ldconfig
else
    echo "==> No Linux NVIDIA driver or Ubuntu CUDA packages installed (good)"
fi

# --- 2. Remove NVIDIA's old, revoked repository key (step from the WSL guide; harmless if absent) ---
if command -v apt-key >/dev/null 2>&1; then
    apt-key del 7fa2af80 >/dev/null 2>&1 || true
fi

# --- 3. Add NVIDIA's WSL-Ubuntu repository via the cuda-keyring package ---
if dpkg-query -W -f='${Version}' cuda-keyring 2>/dev/null | grep -q '^1\.1-1$'; then
    echo "==> cuda-keyring 1.1-1 already installed"
else
    echo "==> Installing $KEYRING_DEB from NVIDIA's WSL-Ubuntu repository"
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    curl -fsSL -o "$TMP/$KEYRING_DEB" "$REPO/$KEYRING_DEB"
    dpkg -i "$TMP/$KEYRING_DEB"
fi

echo "==> apt-get update"
apt-get update

# --- 4. Pick the newest toolkit the Windows driver supports (same major, minor <= driver) ---
pkg_available() { apt-cache show "$1" >/dev/null 2>&1; }

PKG="${CUDA_TOOLKIT_PKG:-}"
if [[ -z "$PKG" ]]; then
    MAJOR="${DRIVER_CUDA%%.*}"
    MINOR="${DRIVER_CUDA##*.}"
    for (( m = MINOR; m >= 0; m-- )); do
        if pkg_available "cuda-toolkit-$MAJOR-$m"; then PKG="cuda-toolkit-$MAJOR-$m"; break; fi
    done
fi
if [[ -z "$PKG" ]] || ! pkg_available "$PKG"; then
    echo "ERROR: no suitable cuda-toolkit package found for driver CUDA $DRIVER_CUDA." >&2
    echo "       Available: $(apt-cache pkgnames cuda-toolkit- | grep -E '^cuda-toolkit-[0-9]+-[0-9]+$' | sort -V | tr '\n' ' ')" >&2
    exit 1
fi
case "$PKG" in
    cuda-toolkit-[0-9]*-[0-9]*) ;;
    *) echo "ERROR: refusing to install '$PKG': only cuda-toolkit-X-Y is safe on WSL2." >&2; exit 1 ;;
esac

echo "==> Installing $PKG (toolkit only, no driver)"
DEBIAN_FRONTEND=noninteractive apt-get install -y "$PKG"

# --- 5. Verify ---
VER="${PKG#cuda-toolkit-}"; VER="${VER/-/.}"
NVCC="/usr/local/cuda-$VER/bin/nvcc"
echo "==> Verifying"
if [[ -x "$NVCC" ]]; then
    "$NVCC" --version | tail -2
else
    echo "WARNING: $NVCC not found. Look in /usr/local/cuda*/bin." >&2
fi
if ls /usr/lib/x86_64-linux-gnu/libcuda.so* >/dev/null 2>&1; then
    echo "ERROR: a Linux libcuda.so is still present in /usr/lib/x86_64-linux-gnu:" >&2
    ls -l /usr/lib/x86_64-linux-gnu/libcuda.so* >&2
    dpkg -S /usr/lib/x86_64-linux-gnu/libcuda.so.1 >&2 || true
    exit 1
else
    echo "   libcuda: only the WSL copy in /usr/lib/wsl/lib (good)"
fi
echo "==> Done. Tell Claude it finished: it will add /usr/local/cuda-$VER/bin to your PATH and verify."
