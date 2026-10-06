"""Build the llama-server command line from config, then exec it (SPEC.md section 9).

`exec` replaces this Python process with llama-server, so systemd supervises the
server directly and config.toml stays the one source of truth for model settings.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from .config import PROJECT_ROOT, Config, ConfigError, load_config

SERVER_BIN = PROJECT_ROOT / "vendor" / "llama.cpp" / "build" / "bin" / "llama-server"
BACKEND_FILE = PROJECT_ROOT / "vendor" / "BACKEND"


def read_backend() -> str:
    """'cuda' or 'cpu', as recorded by scripts/build_llama.sh ('unknown' if not built)."""
    try:
        return BACKEND_FILE.read_text().strip() or "unknown"
    except OSError:
        return "unknown"


def build_argv(cfg: Config, server: Path = SERVER_BIN, backend: str | None = None) -> list[str]:
    """The full llama-server command. Flag names checked against `llama-server --help`."""
    llm = cfg.llm
    backend = backend or read_backend()
    argv = [
        str(server),
        "-m", str(llm.model_path),
        "--host", llm.host,
        "--port", str(llm.port),
        "-c", str(llm.ctx_size),
        "--alias", llm.alias,
        "-np", str(llm.parallel),
        "--jinja",  # tool calling needs the model's own chat template (default on; explicit on purpose)
        # Qwen3.5 thinks by default. This build deprecates passing enable_thinking through
        # --chat-template-kwargs and tells you to use --reasoning off instead (tested: no <think> text).
        "--reasoning", "off",
        "--offline",                 # never touch the network at runtime (on-prem rule)
        "--no-ui",                   # API only; the browser UI is not part of this project
        "--cors-origins", "localhost",  # WSL forwards localhost to Windows: don't let any web page call us
        "--log-colors", "off",       # plain text in journald
    ]
    # On a CPU build there is nothing to offload to, so leave the flag out.
    if backend != "cpu":
        argv += ["-ngl", llm.gpu_layers]
    argv += list(llm.extra_args)
    return argv


def main() -> int:
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1
    if not SERVER_BIN.exists():
        print(f"llama-server not found at {SERVER_BIN}. Run scripts/build_llama.sh", file=sys.stderr)
        return 1
    if not cfg.llm.model_path.exists():
        print(f"model not found at {cfg.llm.model_path}. Run scripts/download_model.sh", file=sys.stderr)
        return 1
    argv = build_argv(cfg)
    print(f"backend={read_backend()} exec: {shlex.join(argv)}", flush=True)
    os.execv(argv[0], argv)  # does not return
    return 0  # pragma: no cover
