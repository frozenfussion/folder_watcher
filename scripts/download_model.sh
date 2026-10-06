#!/usr/bin/env bash
# Download the GGUF model into models/ (SPEC.md section 3). No sudo needed.
#
#   scripts/download_model.sh                         Qwen3.5-9B Q4_K_M (the default)
#   scripts/download_model.sh unsloth/Qwen3.5-4B-GGUF Qwen3.5-4B-Q4_K_M.gguf   smaller model
#
# Only the named file is downloaded: never the mmproj (vision) files.
# Size and SHA-256 are checked against what Hugging Face reports. Safe to run again:
# a complete, verified file is not downloaded twice, and an interrupted download resumes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
REPO="${1:-unsloth/Qwen3.5-9B-GGUF}"
FILE="${2:-Qwen3.5-9B-Q4_K_M.gguf}"

case "$FILE" in
    mmproj-*) echo "ERROR: mmproj files are vision projectors; this project is text only." >&2; exit 1 ;;
    *.gguf) ;;
    *) echo "ERROR: expected a .gguf file name, got $FILE" >&2; exit 1 ;;
esac

[[ -x "$PY" ]] || { echo "ERROR: .venv missing. Run: $ROOT/scripts/setup_python.sh" >&2; exit 1; }

# The download tool lives in .venv only (the [download] extra in pyproject.toml).
if ! "$PY" -c 'import huggingface_hub' 2>/dev/null; then
    echo "==> Installing huggingface_hub into .venv"
    "$PY" -m pip install --quiet -e "$ROOT[download]"
fi

mkdir -p "$ROOT/models"
echo "==> $REPO / $FILE -> models/"

"$PY" - "$REPO" "$FILE" "$ROOT/models" <<'EOF'
import hashlib, sys, time
from pathlib import Path
from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.errors import GatedRepoError

repo, name, models = sys.argv[1], sys.argv[2], Path(sys.argv[3])
target = models / name

try:
    info = HfApi().model_info(repo, files_metadata=True)
except GatedRepoError:
    sys.exit(f"ERROR: {repo} is gated. Run `.venv/bin/hf auth login` yourself, then retry. "
             "Never paste a token into chat or a file in the repo.")
meta = next((s for s in info.siblings if s.rfilename == name), None)
if meta is None:
    sys.exit(f"ERROR: {name} not found in {repo}")
size, sha = meta.size, meta.lfs.sha256 if meta.lfs else None
print(f"    expected size {size:,} bytes, sha256 {sha}")

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(16 << 20):
            h.update(chunk)
    return h.hexdigest()

def verified() -> bool:
    if not target.exists() or target.stat().st_size != size:
        return False
    print("    checking sha256 (takes a few seconds)...")
    return sha is None or sha256(target) == sha

if verified():
    print(f"==> Already present and verified: {target}")
    sys.exit(0)

for attempt in range(1, 4):
    try:
        hf_hub_download(repo, name, local_dir=models)  # resumes partial downloads
        break
    except Exception as e:  # network errors: retry, then report
        print(f"    attempt {attempt} failed: {e}", file=sys.stderr)
        if attempt == 3:
            sys.exit("ERROR: download failed 3 times; see the errors above.")
        time.sleep(10 * attempt)

if not verified():
    sys.exit(f"ERROR: {target} does not match the expected size/sha256. Delete it and retry.")
print(f"==> Downloaded and verified: {target} ({target.stat().st_size:,} bytes)")
EOF
