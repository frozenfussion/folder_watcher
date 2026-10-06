"""Reader for plain text and Markdown files."""

from __future__ import annotations

from ..config import PROJECT_ROOT
from ..guards import GuardError, check_read
from .base import JobContext, Tool, ToolError

PREVIEW_CHARS = 1500  # enough to judge the language; the full text never enters the chat


def decode(data: bytes, ctx: JobContext) -> str:
    """UTF-8, with a logged fallback (SPEC.md 7.8)."""
    if data.startswith(b"\xef\xbb\xbf"):
        ctx.log.warning("file starts with a UTF-8 byte-order mark; removing it")
        return data.decode("utf-8-sig")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        ctx.log.warning("file is not valid UTF-8; reading it as Latin-1")
        return data.decode("latin-1")


def read_file(args: dict, ctx: JobContext) -> dict:
    try:
        path = check_read(ctx.source_path, str(args.get("path", "")), PROJECT_ROOT)
    except GuardError as e:
        raise ToolError(str(e)) from None
    ctx.text = decode(path.read_bytes(), ctx)
    return {"text_preview": ctx.text[:PREVIEW_CHARS], "total_chars": len(ctx.text), "format": path.suffix.lower()}


TOOLS = [
    Tool(
        name="read_file",
        description="Read the new file. Returns a preview of the beginning of the text, enough to tell "
                    "which language it is written in, and the total length. Call this first.",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string", "description": "Path of the new file, as given in the task"}},
                    "required": ["path"]},
        handler=read_file,
        extensions=(".txt", ".md"),
    ),
]
