"""`python -m folder_watcher check`: preflight checks with a plain-language fix for each failure."""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

from .config import PROJECT_ROOT, Config, ConfigError, load_config

UNITS = ("folder-watcher-llm.service", "folder-watcher.service")
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"


class Report:
    def __init__(self) -> None:
        self.failures = 0

    def ok(self, what: str) -> None:
        print(f"  OK    {what}")

    def info(self, what: str) -> None:
        print(f"  INFO  {what}")

    def fail(self, what: str, fix: str) -> None:
        self.failures += 1
        print(f"  FAIL  {what}")
        for i, line in enumerate(fix.splitlines()):
            print(f"        fix: {line}" if i == 0 else f"             {line}")


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=10)


def check_systemd(r: Report) -> bool:
    """True if `systemctl --user` works."""
    try:
        pid1 = Path("/proc/1/comm").read_text().strip()
    except OSError:
        pid1 = "unknown"
    if pid1 != "systemd":
        r.fail(f"systemd is not running (PID 1 is {pid1})",
               "add these two lines to /etc/wsl.conf (needs sudo):\n  [boot]\n  systemd=true\n"
               "then in Windows PowerShell run:  wsl.exe --shutdown   and reopen Ubuntu")
        return False
    if shutil.which("systemctl") is None:
        r.fail("systemctl not found", "install systemd (sudo apt install systemd)")
        return False
    p = _systemctl("is-system-running")
    if "Failed to connect to bus" in (p.stderr + p.stdout):
        r.fail(f"systemctl --user cannot reach your user session ({p.stderr.strip()})",
               "run:  export XDG_RUNTIME_DIR=/run/user/$(id -u)\n"
               "and add that line to ~/.bashrc. If /run/user/$(id -u) does not exist, close and\n"
               "reopen the Ubuntu terminal so a login session is created.")
        return False
    r.ok(f"systemd user session is running ({p.stdout.strip() or 'unknown state'})")
    return True


def check_units(r: Report) -> dict[str, str]:
    """Unit files installed, never enabled. Returns {unit: active state}."""
    states = {}
    missing = [u for u in UNITS if not (UNIT_DIR / u).exists()]
    if missing:
        r.fail(f"service files not installed: {', '.join(missing)}", "run:  scripts/install_services.sh")
        return {u: "not installed" for u in UNITS}
    for unit in UNITS:
        enabled = _systemctl("is-enabled", unit).stdout.strip()
        has_install = re.search(r"^\[Install\]", (UNIT_DIR / unit).read_text(), re.M)  # a real section, not a comment
        if enabled == "enabled" or has_install:
            r.fail(f"{unit} is set to start automatically ({enabled})",
                   f"run:  systemctl --user disable {unit}   then:  scripts/install_services.sh")
        states[unit] = _systemctl("is-active", unit).stdout.strip() or "unknown"
    r.ok("services installed, not set to start automatically: " +
         ", ".join(f"{u.removesuffix('.service')} {s}" for u, s in states.items()))
    return states


def check_gpu(r: Report) -> None:
    backend_file = PROJECT_ROOT / "vendor" / "BACKEND"
    backend = backend_file.read_text().strip() if backend_file.exists() else "not built"
    smi = shutil.which("nvidia-smi") or ("/usr/lib/wsl/lib/nvidia-smi" if os.path.exists("/usr/lib/wsl/lib/nvidia-smi") else None)
    gpu = None
    if smi:
        p = subprocess.run([smi, "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=10)
        gpu = p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None
    if gpu and backend == "cpu":
        r.info(f"GPU found ({gpu}) but llama.cpp was built for CPU; run scripts/build_llama.sh for GPU speed")
    elif gpu:
        r.ok(f"GPU: {gpu}; llama.cpp backend: {backend}")
    else:
        r.info(f"no NVIDIA GPU found; llama.cpp backend: {backend} (CPU works, only slower)")


def check_port(r: Report, cfg: Config, llm_active: bool) -> None:
    from .llm_client import LLMClient

    if llm_active:
        healthy = LLMClient(cfg.llm).healthy()
        if healthy:
            r.ok(f"model server is healthy at {cfg.llm.base_url}")
        else:
            r.fail(f"model server service is running but {cfg.llm.base_url}/health does not answer 'ok'",
                   "it may still be loading; wait and retry. Otherwise see:  scripts/logs.sh llm")
        return
    # With WSL mirrored networking a free port does not refuse the connection, it just never
    # answers; so "no answer within 1 s" also means free.
    with socket.socket() as s:
        s.settimeout(1.0)
        try:
            busy = s.connect_ex((cfg.llm.host, cfg.llm.port)) == 0
        except OSError:
            busy = False
    if busy:
        r.fail(f"port {cfg.llm.port} is already in use by another program",
               f"stop that program, or pick a free port:  .venv/bin/python -m folder_watcher config set llm.port 8081")
    else:
        r.info(f"model server is not running (start everything with: scripts/start.sh)")


def run_checks(config_path: Path) -> int:
    r = Report()
    print("Folder Watcher check")
    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        r.fail(f"config: {e}", f"correct {config_path}, or restore it with:  git checkout {config_path}")
        print(f"\n{r.failures} problem(s) found.")
        return 1
    r.ok(f"config valid ({config_path})")
    try:
        from .tools import Registry
        bad = Registry.discover().unsupported(cfg.watch.extensions)
        if bad:
            r.fail(f"no reader tool for {', '.join(bad)} in watch.extensions",
                   'remove them, e.g.:  .venv/bin/python -m folder_watcher config set watch.extensions \'[".txt", ".md"]\'')
        else:
            r.ok(f"every watched extension has a reader: {', '.join(cfg.watch.extensions)}")
        for name, folder in (("source", cfg.watch.source_dir), ("destination", cfg.watch.destination_dir)):
            if not folder.is_dir():
                r.fail(f"{name} folder missing: {folder}", f"run:  mkdir -p {folder}")
            elif not os.access(folder, os.W_OK | os.R_OK):
                r.fail(f"{name} folder not readable/writable: {folder}", f"run:  chmod u+rwx {folder}")
            else:
                r.ok(f"{name} folder: {folder}")
        model = cfg.llm.model_path
        if model.is_file():
            r.ok(f"model file: {model.name} ({model.stat().st_size / 1e9:.1f} GB)")
        else:
            r.fail(f"model file missing: {model}", "run:  scripts/download_model.sh")
        server = PROJECT_ROOT / "vendor" / "llama.cpp" / "build" / "bin" / "llama-server"
        if server.is_file() and os.access(server, os.X_OK):
            r.ok("llama-server is built")
        else:
            r.fail(f"llama-server not found at {server}", "run:  scripts/build_llama.sh")
        check_gpu(r)
        states = check_units(r) if check_systemd(r) else {}
        check_port(r, cfg, states.get("folder-watcher-llm.service") == "active")
        watch = states.get("folder-watcher.service")
        if watch is not None:
            r.info(f"watcher service: {watch}; watch.enabled = {str(cfg.watch.enabled).lower()}")
    except Exception as e:  # a check must report, never crash
        r.fail(f"unexpected error while checking: {type(e).__name__}: {e}", "please report this output")
    print(f"\n{'All checks passed.' if r.failures == 0 else f'{r.failures} problem(s) found.'}")
    return 0 if r.failures == 0 else 1
