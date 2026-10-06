#!/usr/bin/env bash
# Build llama.cpp's llama-server into vendor/llama.cpp (SPEC.md section 9.1). No sudo needed.
#
#   scripts/build_llama.sh          CUDA build if an NVIDIA GPU and nvcc are found, else CPU
#   scripts/build_llama.sh --cpu    force a CPU build
#   LLAMA_REF=v0.6.0 scripts/build_llama.sh  build a specific tag/branch
#
# Default ref: the newest tag. llama.cpp tags every master build as bNNNN, so this is
# usually the tip of master (Qwen3.5 is a recent architecture and needs a recent build).
#
# Safe to run again: it fetches, checks out the ref and rebuilds incrementally.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/vendor/llama.cpp"
BUILD="$SRC/build"
REPO_URL="https://github.com/ggml-org/llama.cpp"

FORCE_CPU=0
for arg in "$@"; do
    case "$arg" in
        --cpu) FORCE_CPU=1 ;;
        *) echo "unknown option: $arg" >&2; exit 1 ;;
    esac
done

for tool in git cmake c++; do
    command -v "$tool" >/dev/null || { echo "ERROR: $tool missing. Run: sudo bash $ROOT/scripts/system/01_base_packages.sh" >&2; exit 1; }
done

# --- 1. Choose the backend ---
find_nvcc() {
    if command -v nvcc >/dev/null 2>&1; then command -v nvcc; return; fi
    [[ -x /usr/local/cuda/bin/nvcc ]] && { echo /usr/local/cuda/bin/nvcc; return; }
    # Newest versioned toolkit, e.g. /usr/local/cuda-13.1/bin/nvcc
    ls -d /usr/local/cuda-*/bin/nvcc 2>/dev/null | sort -V | tail -1
}

has_gpu() {
    local smi
    smi="$(command -v nvidia-smi || echo /usr/lib/wsl/lib/nvidia-smi)"
    [[ -x "$smi" ]] && "$smi" -L >/dev/null 2>&1
}

BACKEND=cpu
NVCC=""
if (( FORCE_CPU )); then
    echo "==> --cpu given: building for CPU"
elif has_gpu; then
    NVCC="$(find_nvcc || true)"
    if [[ -z "$NVCC" ]]; then
        # A CPU build on a GPU machine is ~10x slower; do not do it silently.
        echo "ERROR: an NVIDIA GPU is present but nvcc (the CUDA toolkit) was not found." >&2
        echo "       Install it:  sudo bash $ROOT/scripts/system/02_cuda_toolkit_wsl.sh" >&2
        echo "       Or build for CPU on purpose:  $0 --cpu" >&2
        exit 1
    fi
    BACKEND=cuda
    echo "==> NVIDIA GPU found, using nvcc: $NVCC ($("$NVCC" --version | grep -oE 'release [0-9.]+'))"
else
    echo "==> No NVIDIA GPU found: building for CPU"
fi

# --- 2. Get the source ---
mkdir -p "$ROOT/vendor"
if [[ ! -d "$SRC/.git" ]]; then
    echo "==> Cloning $REPO_URL"
    git clone --filter=blob:none "$REPO_URL" "$SRC"
fi
git -C "$SRC" fetch --tags --force --quiet origin
REF="${LLAMA_REF:-$(git -C "$SRC" describe --tags "$(git -C "$SRC" rev-list --tags --max-count=1)")}"
if [[ "$REF" == "master" ]]; then
    git -C "$SRC" checkout --quiet -B master origin/master
else
    git -C "$SRC" checkout --quiet "$REF"
fi
echo "==> llama.cpp at $REF ($(git -C "$SRC" rev-parse --short HEAD))"

# --- 3. Configure and build ---
CMAKE_ARGS=(-DCMAKE_BUILD_TYPE=Release)
if [[ "$BACKEND" == cuda ]]; then
    # "native" compiles only for the GPU in this machine, which is much faster than all
    # architectures. Absolute nvcc path, so the build works even if PATH lacks it.
    CMAKE_ARGS+=(-DGGML_CUDA=ON -DCMAKE_CUDA_COMPILER="$NVCC" -DCMAKE_CUDA_ARCHITECTURES=native)
else
    CMAKE_ARGS+=(-DGGML_CUDA=OFF)
fi

# Switching between CPU and CUDA needs a clean cache.
if [[ -f "$ROOT/vendor/BACKEND" && "$(cat "$ROOT/vendor/BACKEND")" != "$BACKEND" ]]; then
    echo "==> Backend changed, removing old build directory"
    rm -rf "$BUILD"
fi

echo "==> cmake configure (${CMAKE_ARGS[*]})"
cmake -S "$SRC" -B "$BUILD" "${CMAKE_ARGS[@]}"

echo "==> Building llama-server with $(nproc) jobs (CUDA builds take several minutes)"
cmake --build "$BUILD" --config Release --target llama-server -j "$(nproc)"

SERVER="$BUILD/bin/llama-server"
[[ -x "$SERVER" ]] || { echo "ERROR: build finished but $SERVER is missing" >&2; exit 1; }

echo "$BACKEND" > "$ROOT/vendor/BACKEND"
echo "==> Built: $SERVER"
"$SERVER" --version 2>&1 | tail -n 3 || true
echo "==> Backend: $BACKEND (saved to vendor/BACKEND)"
