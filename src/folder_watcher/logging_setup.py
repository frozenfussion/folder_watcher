"""One log format for every command; everything goes to stdout (journald captures it later)."""

from __future__ import annotations

import logging
import os
import sys


def setup_logging(level: str) -> None:
    # Under systemd the journal adds its own timestamp; don't print the time twice.
    fmt = "%(levelname)-7s %(message)s" if os.environ.get("INVOCATION_ID") else "%(asctime)s %(levelname)-7s %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S", stream=sys.stdout, force=True)
    # The HTTP libraries log every request at INFO; that drowns the agent trace.
    for noisy in ("httpx", "httpx2", "httpcore", "openai", "watchdog"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
