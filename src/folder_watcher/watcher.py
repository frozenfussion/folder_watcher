"""Watch source/ for new files and feed them to the agent (SPEC.md sections 6.1, 7.1-7.3, 7.9).

Only files that *arrive* after the watcher starts are processed: a file is created in
source/ or moved into it. Files already there at start, and later edits to any file,
are not arrivals.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import PROJECT_ROOT, Config, ConfigError, LiveConfig
from .guards import is_hidden_or_temp
from .logging_setup import setup_logging
from .state import Ledger, file_key

log = logging.getLogger("folder_watcher.watcher")

STARTUP_WAIT_SECONDS = 300   # how long to wait for the model server at startup
SHUTDOWN_GRACE_SECONDS = 30  # how long the current job gets to reach a safe stopping point


@dataclass
class Job:
    path: Path
    key: str

    def __str__(self) -> str:
        return self.path.name


def sd_notify(message: str) -> None:
    """Tell systemd about our state (Type=notify). Does nothing outside systemd."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):  # abstract socket namespace
        address = "\0" + address[1:]
    import socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.connect(address)
        sock.sendall(message.encode())


def wait_until_stable(path: Path, stable_seconds: float, timeout: float, stop: threading.Event,
                      poll: float = 0.25) -> str:
    """Wait until size and mtime stop changing for stable_seconds.
    Returns "stable", "vanished", "timeout" or "stopped"."""
    deadline = time.monotonic() + timeout
    last, since = None, time.monotonic()
    while True:
        try:
            st = path.stat()
        except FileNotFoundError:
            return "vanished"
        now = time.monotonic()
        if (st.st_size, st.st_mtime_ns) != last:
            last, since = (st.st_size, st.st_mtime_ns), now
        elif now - since >= stable_seconds:
            return "stable"
        if now > deadline:
            return "timeout"
        if stop.wait(poll):
            return "stopped"


class _Events(FileSystemEventHandler):
    def __init__(self, watcher: "Watcher") -> None:
        self.watcher = watcher

    def on_created(self, event) -> None:
        if not event.is_directory:
            self.watcher.arrived(Path(os.fsdecode(event.src_path)), "created")

    def on_moved(self, event) -> None:
        # A rename inside source/ or a move from elsewhere: the destination is the arrival.
        if not event.is_directory:
            self.watcher.arrived(Path(os.fsdecode(event.dest_path)), "moved in")

    # on_modified is deliberately not handled: an edit is not a new arrival.


class Watcher:
    def __init__(self, live: LiveConfig, start_cfg: Config, registry, ledger: Ledger,
                 submit: Callable[[Job], None], poll: float = 0.25) -> None:
        self.live = live
        self.start_cfg = start_cfg     # [llm] and the folders are fixed at start
        self.registry = registry
        self.ledger = ledger
        self.submit = submit
        self.poll = poll
        self.source = start_cfg.watch.source_dir.resolve()
        self._stop = threading.Event()
        self._settling: set[Path] = set()
        self._lock = threading.Lock()
        self._warned_restart = False
        self._last_exts = start_cfg.watch.extensions
        self._observer = Observer()

    # --- live config ---

    def config(self) -> Config:
        """[watch] and [agent] from the file as it is now; [llm] and the folders as at start."""
        new = self.live.get()
        if new.watch.extensions != self._last_exts:
            log.info("watched extensions changed: %s -> %s", ", ".join(self._last_exts), ", ".join(new.watch.extensions))
            self._last_exts = new.watch.extensions
            for ext in self.registry.unsupported(new.watch.extensions):
                log.error("watch.extensions lists %s but no reader tool supports it; those files are refused", ext)
        restart_needed = (new.llm != self.start_cfg.llm or new.logging != self.start_cfg.logging
                          or new.watch.source_dir != self.start_cfg.watch.source_dir
                          or new.watch.destination_dir != self.start_cfg.watch.destination_dir)
        if restart_needed and not self._warned_restart:
            log.warning("config changes to [llm], [logging] or the folders apply only after a restart")
        self._warned_restart = restart_needed
        watch = replace(new.watch, source_dir=self.start_cfg.watch.source_dir,
                        destination_dir=self.start_cfg.watch.destination_dir)
        return replace(self.start_cfg, watch=watch, agent=new.agent)

    # --- lifecycle ---

    def start(self) -> int:
        """Start watching. Returns how many files were already in source/ (all ignored)."""
        existing = sum(1 for p in self.source.iterdir() if p.is_file() and not p.name.startswith("."))
        self._observer.schedule(_Events(self), str(self.source), recursive=False)
        self._observer.start()
        return existing

    def stop(self) -> None:
        self._stop.set()
        self._observer.stop()
        self._observer.join(5)

    # --- arrivals ---

    def arrived(self, path: Path, how: str) -> None:
        """Called for every created / moved-in event (from the observer thread)."""
        if path.parent.resolve() != self.source:
            return  # e.g. a file moved *out* of source/
        cfg = self.config()
        name = path.name
        if cfg.watch.ignore_hidden and is_hidden_or_temp(path):
            log.debug("ignored %s: hidden or temporary file", name)
            return
        if not cfg.watch.enabled:
            log.info("ignored %s: watching is disabled (it will not be processed later)", name)
            return
        ext = path.suffix.lower()
        if ext not in cfg.watch.extensions:
            log.info("ignored %s: %s is not in watch.extensions (%s)", name, ext or "no extension",
                     ", ".join(cfg.watch.extensions))
            return
        if not self.registry.for_extension(ext):
            log.error("ignored %s: no reader tool for %s", name, ext)
            return
        with self._lock:
            if path in self._settling:
                return  # a second event for a file we are already watching settle
            self._settling.add(path)
        log.info("new file %s (%s); waiting until it stops changing", name, how)
        threading.Thread(target=self._settle, args=(path,), name=f"settle-{name}", daemon=True).start()

    def _settle(self, path: Path) -> None:
        name = path.name
        try:
            cfg = self.config()
            result = wait_until_stable(path, cfg.watch.stable_seconds, cfg.watch.stable_timeout_seconds,
                                       self._stop, self.poll)
            if result == "vanished":
                log.info("%s disappeared before it was finished; ignored", name)
                return
            if result == "timeout":
                log.warning("%s was still changing after %.0fs; ignored", name, cfg.watch.stable_timeout_seconds)
                return
            if result == "stopped":
                return
            if not self.config().watch.enabled:
                log.info("ignored %s: watching was disabled while it was being copied", name)
                return
            key = file_key(path)
            if key is None:
                return
            try:
                with path.open("rb") as f:
                    empty = f.read(1) == b""
            except OSError as e:
                log.warning("cannot read %s: %s; ignored", name, e)
                return
            if empty:
                if self.ledger.claim(key, name):
                    self.ledger.record(key, name, "skipped-empty")
                    log.info("skipped %s: the file is empty", name)
                return
            if not self.ledger.claim(key, name):
                log.debug("%s was already taken (duplicate event)", name)
                return
            log.info("queued %s", name)
            self.submit(Job(path, key))
        finally:
            with self._lock:
                self._settling.discard(path)


def run_watch(config_path: Path) -> int:
    """`python -m folder_watcher watch`: run until Ctrl+C or SIGTERM."""
    from .agent import run_job
    from .llm_client import LLMClient
    from .queue import JobQueue
    from .tools import Registry

    try:
        live = LiveConfig(config_path)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1
    cfg = live.get()
    setup_logging(cfg.logging.level)
    registry = Registry.discover()
    for ext in registry.unsupported(cfg.watch.extensions):
        log.error("watch.extensions lists %s but no reader tool supports it; those files are refused", ext)
    if not cfg.watch.source_dir.is_dir():
        log.error("source folder %s does not exist", cfg.watch.source_dir)
        return 1
    cfg.watch.destination_dir.mkdir(parents=True, exist_ok=True)

    stop = threading.Event()
    reason = {"signal": ""}

    def on_signal(signum, _frame):
        if stop.is_set():
            log.warning("second %s: exiting immediately", signal.Signals(signum).name)
            os._exit(130)
        reason["signal"] = signal.Signals(signum).name
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    llm = LLMClient(cfg.llm, timeout=cfg.agent.job_timeout_seconds)
    log.info("waiting for the model server at %s (up to %ds) ...", cfg.llm.base_url, STARTUP_WAIT_SECONDS)
    started = time.monotonic()
    while not llm.healthy():
        waited = time.monotonic() - started
        if waited > STARTUP_WAIT_SECONDS:
            log.error("the model server did not answer within %ds. Start it with: "
                      ".venv/bin/python -m folder_watcher llm-server", STARTUP_WAIT_SECONDS)
            return 1
        if stop.wait(2):
            log.info("stopped while waiting for the model server")
            return 0
        if int(waited) % 30 < 2 and waited > 2:
            log.info("still waiting for the model server (%ds)", int(waited))
    log.info("model server is ready")

    ledger = Ledger(PROJECT_ROOT / "state" / "ledger.jsonl")
    watcher: Watcher | None = None

    def process(job: Job, cancel: threading.Event) -> None:
        if not job.path.exists():
            log.info("%s was removed before it could be processed", job)
            ledger.record(job.key, job.path.name, "removed")
            return
        result = run_job(job.path, watcher.config(), registry, llm, cancel=cancel)  # may raise ModelUnavailable
        ledger.record(job.key, job.path.name, result.status)

    jobs = JobQueue(process, llm.healthy)
    watcher = Watcher(live, cfg, registry, ledger, jobs.put)
    jobs.start()
    existing = watcher.start()
    state = "enabled" if cfg.watch.enabled else "DISABLED (set watch.enabled = true to start)"
    log.info("watching %s for new %s files; watching is %s. %d file(s) already there are ignored. "
             "Stop with Ctrl+C.", cfg.watch.source_dir, ", ".join(cfg.watch.extensions), state, existing)
    # Under systemd (Type=notify) this is what makes `systemctl start` return: only now is it
    # safe to drop files, because files present before this point are treated as existing.
    sd_notify(f"READY=1\nSTATUS=watching {cfg.watch.source_dir}")

    while not stop.wait(1.0):
        watcher.config()  # notice a config edit right away, not only on the next file

    # --- defined shutdown: stop watching, drop jobs not started, abandon the current one ---
    log.info("stopping (%s): no new files will be taken", reason["signal"])
    sd_notify("STOPPING=1")
    watcher.stop()
    for job in jobs.pending():
        ledger.record(job.key, job.path.name, "not-started")
        log.info("not started: %s (copy it into source/ again to process it)", job)
    current = jobs.current
    if current is not None:
        log.info("asking the current job (%s) to stop at its next safe point; nothing is written for it", current)
    if not jobs.stop(SHUTDOWN_GRACE_SECONDS):
        log.error("the current job (%s) did not stop within %ds; exiting anyway (nothing was written for it)",
                  current, SHUTDOWN_GRACE_SECONDS)
    if current is not None and ledger.status(current.key) == "queued":
        # It never finished (abandoned, or still waiting for the model server).
        ledger.record(current.key, current.path.name, "abandoned")
        log.warning("not processed: %s (copy it into source/ again to process it)", current)
    log.info("watcher stopped")
    return 0
