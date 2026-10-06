"""translate_text: translate the whole job text, chunk by chunk, with code and URLs protected."""

from __future__ import annotations

import time

from ..chunking import chunk, join
from ..prompts import TRANSLATOR_SYSTEM
from ..protect import Protected, ProtectError, check_chunk, protect
from .base import JobContext, JobFailed, Tool, ToolError

ATTEMPTS_PER_CHUNK = 2  # the first try plus one retry


def _needs_model(text: str, protected: Protected) -> bool:
    """A chunk that is only tokens and whitespace has nothing to translate."""
    return bool(protected.pattern.sub("", text).strip())


def _translate_chunk(core: str, n: int, total: int, protected: Protected, hint: str, ctx: JobContext) -> str:
    example = next(iter(protected.originals), "⟦U1⟧")
    system = TRANSLATOR_SYSTEM.format(example=example)
    if hint:  # in the system prompt, never in the text, so it cannot end up in the output
        system += f"\nThe source language is probably {hint}.\n"
    user = core
    max_tokens = ctx.config.llm.ctx_size // 2
    for attempt in range(1, ATTEMPTS_PER_CHUNK + 1):
        reply = ctx.llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                             ctx.config.llm.sampling_translate, max_tokens=max_tokens)
        out = reply.content.strip()
        problems = check_chunk(protected, core, out)
        if reply.finish_reason == "length":
            problems.append("the translation was cut off (too long)")
        if "<think>" in out or not out:
            problems.append("empty output or thinking text")
        if not problems:
            return out
        ctx.log.warning("chunk %d/%d attempt %d rejected: %s", n, total, attempt, "; ".join(problems))
    raise JobFailed(f"chunk {n}/{total} was still damaged after a retry ({'; '.join(problems)}). "
                    "Nothing was written.")


def translate_text(args: dict, ctx: JobContext) -> dict:
    if ctx.text is None:
        raise ToolError("call read_file first")
    try:
        protected = protect(ctx.text)
    except ProtectError as e:
        raise JobFailed(str(e)) from None
    pieces = chunk(protected.text, ctx.config.agent.chunk_max_chars)
    hint = str(args.get("source_language") or "").strip()
    out = []
    for n, piece in enumerate(pieces, 1):
        if time.monotonic() > ctx.deadline:
            raise JobFailed("job time limit reached during translation")
        # Translate only the content; keep the exact surrounding whitespace so chunks rejoin cleanly.
        core = piece.strip()
        lead = piece[: len(piece) - len(piece.lstrip())]
        trail = piece[len(piece.rstrip()):]
        if not _needs_model(core, protected):
            out.append(piece)
            continue
        started = time.monotonic()
        out.append(lead + _translate_chunk(core, n, len(pieces), protected, hint, ctx) + trail)
        ctx.log.info("chunk %d/%d translated in %.1fs", n, len(pieces), time.monotonic() - started)
    result = protected.restore(join(out))
    missing = [t for t, original in protected.originals.items() if original not in result]
    if missing or protected.tokens_in(result):
        raise JobFailed(f"protected text was not restored exactly ({missing}). Nothing was written.")
    tid = f"t{len(ctx.translations) + 1}"
    ctx.translations[tid] = result
    return {"translation_id": tid, "chunks": len(pieces), "protected_items": len(protected.originals)}


TOOLS = [
    Tool(
        name="translate_text",
        description="Translate the whole file into English. Use only when most of the document is NOT English. "
                    "Returns a translation_id to pass to write_translation.",
        parameters={"type": "object",
                    "properties": {"source_language": {"type": "string",
                                                       "description": "Your best guess of the document's language"}}},
        handler=translate_text,
    ),
]
