"""One log format for every command; everything goes to stdout (journald captures it later)."""

from __future__ import annotations

import logging
import sys


def setup_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stdout, force=True)
    # The HTTP libraries log every request at INFO; that drowns the agent trace.
    for noisy in ("httpx", "httpx2", "httpcore", "openai", "watchdog"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
