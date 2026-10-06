"""The tool interface (SPEC.md section 8.1)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import Config
from ..guards import DestinationGuard


class ToolError(Exception):
    """The call was wrong or not allowed. The message goes back to the model so it can react."""


class JobFailed(Exception):
    """Something the model cannot fix (e.g. a damaged translation). Ends the job at once."""


class JobAbandoned(Exception):
    """The service is shutting down; the job stops at the next safe point and writes nothing."""


@dataclass
class JobContext:
    """Everything a tool may use for one job. The model never sees this object."""
    job_id: str
    source_path: Path                 # the one file this job may read (resolved, inside source/)
    config: Config                    # config snapshot taken when the job started
    llm: Any                          # LLMClient, or a fake in tests
    destination: DestinationGuard     # the only way to write
    log: logging.LoggerAdapter
    deadline: float                   # time.monotonic() value when the job must stop
    text: str | None = None           # full text, filled in by the reader tool
    translations: dict[str, str] = field(default_factory=dict)
    outcome: str | None = None        # "skipped" or "written", set by terminal tools
    reason: str = ""                  # skip reason
    output_path: Path | None = None
    cancel: Any = None                # threading.Event set at shutdown, or None

    def check_cancel(self) -> None:
        """Called between model calls: the safe points where a job may be abandoned."""
        if self.cancel is not None and self.cancel.is_set():
            raise JobAbandoned("service is stopping")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str                  # shown to the model
    parameters: dict                  # JSON schema
    handler: Callable[[dict, JobContext], dict]
    extensions: tuple[str, ...] = ()  # file types this tool serves; empty = general tool
    terminal: bool = False            # True for skip_file / write_translation

    def schema(self) -> dict:
        """OpenAI function-calling format."""
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}
