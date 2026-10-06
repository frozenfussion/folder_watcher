"""skip_file: the agent decided the document needs no translation."""

from __future__ import annotations

from .base import JobContext, Tool, ToolError


def skip_file(args: dict, ctx: JobContext) -> dict:
    reason = str(args.get("reason", "")).strip()
    if not reason:
        raise ToolError("give a short reason for skipping, e.g. 'already English'")
    if ctx.config.agent.english_action == "copy":
        out = ctx.destination.write_new(ctx.source_path.name, ctx.source_path.read_bytes())
        ctx.output_path = out
    ctx.outcome, ctx.reason = "skipped", reason
    return {"status": "skipped"}


TOOLS = [
    Tool(
        name="skip_file",
        description="Finish the job without translating, because the document is already in English. "
                    "Give a short reason.",
        parameters={"type": "object",
                    "properties": {"reason": {"type": "string", "description": "Why no translation is needed"}},
                    "required": ["reason"]},
        handler=skip_file,
        terminal=True,
    ),
]
