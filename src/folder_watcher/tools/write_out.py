"""write_translation: save a finished translation into destination/."""

from __future__ import annotations

from ..config import PROJECT_ROOT
from ..guards import GuardError, check_filename
from .base import JobContext, Tool, ToolError


def write_translation(args: dict, ctx: JobContext) -> dict:
    tid = str(args.get("translation_id", ""))
    if tid not in ctx.translations:
        known = ", ".join(ctx.translations) or "none yet; call translate_text first"
        raise ToolError(f"unknown translation_id {tid!r} (known: {known})")
    src = ctx.source_path
    name = args.get("filename") or f"{src.stem}{ctx.config.agent.output_suffix}{src.suffix}"
    try:
        check_filename(name)
        if not name.lower().endswith(src.suffix.lower()):
            raise GuardError(f"file name must end in {src.suffix}, like the original")
        path = ctx.destination.write_new(name, ctx.translations[tid].encode("utf-8"))
    except GuardError as e:
        raise ToolError(f"{e}. Call write_translation again without a filename to use the default name.") from None
    ctx.outcome, ctx.output_path = "written", path
    try:
        shown = str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        shown = str(path)
    return {"status": "written", "path": shown}


TOOLS = [
    Tool(
        name="write_translation",
        description="Save the translation into the destination folder. Finishes the job. "
                    "Leave out filename to use the standard name.",
        parameters={"type": "object",
                    "properties": {"translation_id": {"type": "string"},
                                   "filename": {"type": "string",
                                                "description": "Optional plain file name, no folders"}},
                    "required": ["translation_id"]},
        handler=write_translation,
        terminal=True,
    ),
]
