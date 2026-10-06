"""Split long texts into chunks small enough to translate, and join them back (SPEC.md 7.7).

Guarantee: "".join(chunk(text, n)) == text, for any text. Separators stay attached
to the piece before them, so nothing is lost or duplicated when rejoining.
"""

from __future__ import annotations

import re

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
HEADING = re.compile(r"^ {0,3}#{1,6}(\s|$)")
SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")


def _paragraphs(text: str) -> list[str]:
    """Split on blank lines (kept with the paragraph before them). Fenced blocks are never split."""
    units: list[str] = []
    current = ""
    fence = None          # the opening fence ("```" or "~~~~") while inside a code block
    after_blank = False
    for line in text.splitlines(keepends=True):
        if fence is not None:
            current += line
            if re.match(r"^ {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*$", line.rstrip("\n")):
                fence = None
            continue
        if line.strip() == "":
            current += line
            after_blank = True
            continue
        if after_blank and current.strip():
            units.append(current)
            current = ""
        after_blank = False
        m = FENCE.match(line)
        if m:
            fence = m.group(1)
        current += line
    if current:
        units.append(current)
    return units


def _attach_headings(units: list[str]) -> list[str]:
    """A heading paragraph is merged into the paragraph that follows it."""
    merged: list[str] = []
    carry = ""
    for unit in units:
        if HEADING.match(unit) and len(unit.strip().splitlines()) == 1:
            carry += unit
            continue
        merged.append(carry + unit)
        carry = ""
    if carry:
        merged.append(carry)
    return merged


def _split_sentences(line: str, max_chars: int) -> list[str]:
    pieces, start = [], 0
    for m in SENTENCE_END.finditer(line):
        pieces.append(line[start:m.end()])
        start = m.end()
    pieces.append(line[start:])
    out = []
    for p in pieces:
        while len(p) > max_chars:  # one enormous "sentence": hard cut as a last resort
            out.append(p[:max_chars])
            p = p[max_chars:]
        if p:
            out.append(p)
    return out


def _split_unit(unit: str, max_chars: int) -> list[str]:
    """Break an oversized paragraph into lines, then sentences. Code fences stay whole."""
    if len(unit) <= max_chars or any(FENCE.match(line) for line in unit.splitlines()):
        return [unit]
    pieces = []
    for line in unit.splitlines(keepends=True):
        pieces.extend([line] if len(line) <= max_chars else _split_sentences(line, max_chars))
    return pieces


def chunk(text: str, max_chars: int) -> list[str]:
    """Pack paragraphs (or smaller pieces of oversized ones) into chunks of at most max_chars."""
    pieces: list[str] = []
    for unit in _attach_headings(_paragraphs(text)):
        pieces.extend(_split_unit(unit, max_chars))
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > max_chars:
            chunks.append(current)
            current = ""
        current += piece
    if current:
        chunks.append(current)
    return chunks


def join(chunks: list[str]) -> str:
    return "".join(chunks)
