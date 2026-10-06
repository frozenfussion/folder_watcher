"""The agent loop (SPEC.md section 7.4): the model chooses tools, the code enforces the rules."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import PROJECT_ROOT, Config
from .guards import DestinationGuard, GuardError, resolve_job_file
from .prompts import AGENT_SYSTEM, AGENT_TASK
from .tools import Registry
from .tools.base import JobContext, JobFailed, ToolError

log = logging.getLogger("folder_watcher.agent")

# Document text must not reach the logs above DEBUG (on-prem documents can be sensitive).
SENSITIVE_KEYS = {"text_preview"}


class JobLog(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"[job {self.extra['job']}] {msg}", kwargs


@dataclass
class JobResult:
    status: str               # "done", "skipped" or "failed"
    reason: str
    seconds: float
    steps: int
    output: Path | None = None


def _for_log(result: dict, debug: bool) -> str:
    shown = {k: (v if debug or k not in SENSITIVE_KEYS else f"<{len(str(v))} chars, shown at DEBUG>")
             for k, v in result.items()}
    text = json.dumps(shown, ensure_ascii=False)
    return text if len(text) <= 300 else text[:300] + "..."


def _run_tool(registry: Registry, name: str, raw_args: str, ctx: JobContext) -> tuple[dict, bool]:
    """Run one tool call. Returns (result for the model, finished?). Mistakes become error results."""
    tool = registry.get(name)
    if tool is None:
        return {"error": f"unknown tool {name!r}; available: {registry.names()}"}, False
    try:
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
    except ValueError as e:
        return {"error": f"arguments are not valid JSON: {e}"}, False
    try:
        return tool.handler(args, ctx), tool.terminal
    except ToolError as e:
        return {"error": str(e)}, False
    except JobFailed:
        raise
    except Exception as e:  # a bug in a tool must not crash the job loop
        ctx.log.exception("tool %s crashed", name)
        return {"error": f"internal error in {name}: {e}"}, False


def run_job(path: Path, cfg: Config, registry: Registry, llm, max_steps: int | None = None) -> JobResult:
    job = JobLog(log, {"job": uuid.uuid4().hex[:4]})
    started = time.monotonic()
    trace = cfg.logging.trace_agent
    debug = log.isEnabledFor(logging.DEBUG)
    max_steps = max_steps or cfg.agent.max_steps

    def finish(status: str, reason: str, steps: int, output: Path | None = None) -> JobResult:
        secs = time.monotonic() - started
        if status == "failed":
            job.error("FAILED after %.1fs: %s", secs, reason)
        elif status == "skipped":
            job.info("SKIPPED in %.1fs: %s", secs, reason)
        else:
            job.info("DONE in %.1fs: %s", secs, reason)
        return JobResult(status, reason, secs, steps, output)

    # --- Checks that need no model ---
    try:
        source = resolve_job_file(cfg.watch.source_dir, path)
    except GuardError as e:
        return finish("failed", str(e), 0)
    shown_path = source.relative_to(PROJECT_ROOT) if source.is_relative_to(PROJECT_ROOT) else source
    job.info("new file %s", shown_path)
    if not source.is_file():
        return finish("failed", "not a regular file", 0)
    if not registry.for_extension(source.suffix):
        return finish("failed", f"no reader tool for {source.suffix or 'files without an extension'}", 0)
    if source.stat().st_size == 0:
        return finish("skipped", "empty file (no model call)", 0)

    ctx = JobContext(job_id=job.extra["job"], source_path=source, config=cfg, llm=llm,
                     destination=DestinationGuard(cfg.watch.destination_dir), log=job,
                     deadline=started + cfg.agent.job_timeout_seconds)
    tools = registry.schemas_for_job(source)
    messages = [{"role": "system", "content": AGENT_SYSTEM},
                {"role": "user", "content": AGENT_TASK.format(path=shown_path)}]

    # --- The loop: the model picks a tool, the code runs it, the result goes back ---
    try:
        for step in range(1, max_steps + 1):
            if time.monotonic() > ctx.deadline:
                return finish("failed", f"time limit of {cfg.agent.job_timeout_seconds:.0f}s reached", step - 1)
            reply = llm.chat(messages, cfg.llm.sampling_agent, tools=tools)
            if not reply.tool_calls:
                # Safety net, visible on purpose: the model stopped without a terminal tool.
                job.info("step %d model says: %s", step, reply.content.strip()[:300] or "(nothing)")
                return finish("failed", "the model stopped without calling skip_file or write_translation", step)
            messages.append({"role": "assistant", "content": reply.content,
                             "tool_calls": [{"id": c.id, "type": "function",
                                             "function": {"name": c.name, "arguments": c.arguments}}
                                            for c in reply.tool_calls]})
            for call in reply.tool_calls:
                if trace:
                    job.info("step %d -> tool %s %s", step, call.name, call.arguments)
                result, finished = _run_tool(registry, call.name, call.arguments, ctx)
                if trace or "error" in result:
                    job.info("step %d <- %s", step, _for_log(result, debug))
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(result, ensure_ascii=False)})
                if finished:
                    if ctx.outcome == "skipped":
                        return finish("skipped", ctx.reason, step, ctx.output_path)
                    return finish("done", f"wrote {result.get('path')}", step, ctx.output_path)
        return finish("failed", f"step limit of {max_steps} reached without skip_file or write_translation",
                      max_steps)
    except JobFailed as e:
        return finish("failed", str(e), step)
    except Exception as e:  # e.g. the model server went away; the caller decides about retrying
        job.exception("unexpected error")
        return finish("failed", f"{type(e).__name__}: {e}", step)
