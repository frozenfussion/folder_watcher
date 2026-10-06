"""The job queue: first in, first out, one worker, one job at a time (SPEC.md 7.3, 7.9).

A failed job never stops the queue. If the model server is unreachable, the job stays
at the front of the queue and is retried with growing pauses until the server is back.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
from typing import Callable

from .llm_client import ModelUnavailable

log = logging.getLogger("folder_watcher.queue")

BACKOFF_SECONDS = (5, 10, 20, 40, 60)  # then every 60 s until the server answers


class JobQueue:
    def __init__(self, handler: Callable[[object, threading.Event], None], is_ready: Callable[[], bool],
                 backoff: tuple[float, ...] = BACKOFF_SECONDS) -> None:
        self.handler = handler        # handler(item, cancel) runs one job
        self.is_ready = is_ready      # True when the model server answers /health
        self.backoff = backoff
        self._items: queue.Queue = queue.Queue()
        self._stopping = threading.Event()
        self.cancel = threading.Event()  # tells the running job to stop at its next safe point
        self.current: object | None = None
        self._thread = threading.Thread(target=self._work, name="job-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def put(self, item: object) -> None:
        self._items.put(item)

    def pending(self) -> list[object]:
        with self._items.mutex:
            return list(self._items.queue)

    def stop(self, timeout: float) -> bool:
        """Stop taking jobs, ask the current one to stop, wait up to `timeout`. True if stopped."""
        self._stopping.set()
        self.cancel.set()
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _wait_for_model(self, item: object) -> bool:
        """Pause until the model server is back. False if the service is stopping."""
        for n in itertools.count():
            delay = self.backoff[min(n, len(self.backoff) - 1)]
            log.warning("model server unavailable; %s stays queued, retrying in %ss", item, delay)
            if self._stopping.wait(delay):
                return False
            if self.is_ready():
                log.info("model server is back; retrying %s", item)
                return True
        return False  # pragma: no cover (itertools.count never ends)

    def _work(self) -> None:
        while not self._stopping.is_set():
            try:
                item = self._items.get(timeout=0.2)
            except queue.Empty:
                continue
            self.current = item
            while not self._stopping.is_set():
                try:
                    self.handler(item, self.cancel)
                    break
                except ModelUnavailable:
                    if not self._wait_for_model(item):
                        break
                except Exception:  # one bad job must never stop the queue
                    log.exception("job for %s crashed; continuing with the next file", item)
                    break
            self.current = None
