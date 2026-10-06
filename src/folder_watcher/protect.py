"""Keep code and URLs away from the model (SPEC.md section 7.6).

Fenced code blocks, inline code and URLs are swapped for placeholder tokens such as
⟦B1⟧ before translation and swapped back afterwards. The model never sees the
protected text, so it cannot translate a code comment or "fix" a URL. After each
chunk is translated, `check_chunk` verifies every token came back exactly once,
in order, and that code-block tokens still sit on a line of their own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Brackets that practically never occur in real documents; the first pair not
# already present in the text is used, so a token can never collide with content.
BRACKETS = (("⟦", "⟧"), ("⟪", "⟫"), ("⦃", "⦄"))

FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)[^\n]+?(?<!`)\1(?!`)")
# Markdown link target "](url" or autolink "<url>" or a bare URL.
URL = re.compile(r"https?://[^\s<>()\[\]\"']+")
URL_TRAILING_PUNCT = ".,;:!?"


class ProtectError(ValueError):
    pass


@dataclass
class Protected:
    text: str                                  # the text with tokens in place of protected parts
    originals: dict[str, str] = field(default_factory=dict)  # token -> original text
    blocks: set[str] = field(default_factory=set)            # tokens that stand for code blocks
    pattern: re.Pattern = field(default=re.compile(r"(?!)"))  # matches any of our tokens

    def tokens_in(self, text: str) -> list[str]:
        return self.pattern.findall(text)

    def restore(self, text: str) -> str:
        """Put the original bytes back. Code-block tokens replace their whole line."""
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line.strip() in self.blocks:
                lines[i] = self.originals[line.strip()]
        text = "\n".join(lines)
        return self.pattern.sub(lambda m: self.originals[m.group(0)], text)


def protect(text: str) -> Protected:
    """Swap code blocks, inline code and URLs for tokens."""
    try:
        left, right = next((l, r) for l, r in BRACKETS if l not in text and r not in text)
    except StopIteration:
        raise ProtectError("the document contains all reserved placeholder brackets") from None
    result = Protected(text="", pattern=re.compile(re.escape(left) + r"[BCU]\d+" + re.escape(right)))
    counter = 0

    def token(kind: str, original: str) -> str:
        nonlocal counter
        counter += 1
        tok = f"{left}{kind}{counter}{right}"
        result.originals[tok] = original
        return tok

    def protect_inline(segment: str) -> str:
        segment = INLINE_CODE.sub(lambda m: token("C", m.group(0)), segment)

        def url(m: re.Match) -> str:
            raw = m.group(0)
            core = raw.rstrip(URL_TRAILING_PUNCT)  # "see https://x.org." -> keep the final dot
            return token("U", core) + raw[len(core):]

        return URL.sub(url, segment)

    # Walk the lines; a fenced block (open fence to matching close fence, or to the end
    # of the document if never closed, as CommonMark does) becomes one token line.
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    plain: list[str] = []
    i = 0
    while i < len(lines):
        m = FENCE_OPEN.match(lines[i])
        if not m:
            plain.append(lines[i])
            i += 1
            continue
        out.append(protect_inline("".join(plain)))
        plain = []
        fence = m.group(1)
        close = re.compile(r"^ {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*$")
        j = i + 1
        while j < len(lines) and not close.match(lines[j].rstrip("\n")):
            j += 1
        block = "".join(lines[i:j + 1])
        newline = "\n" if block.endswith("\n") else ""
        tok = token("B", block[: len(block) - len(newline)])
        result.blocks.add(tok)
        out.append(tok + newline)
        i = j + 1
    out.append(protect_inline("".join(plain)))
    result.text = "".join(out)
    return result


def check_chunk(protected: Protected, source: str, translated: str) -> list[str]:
    """Problems with a translated chunk's tokens; an empty list means it is safe to use."""
    expected = protected.tokens_in(source)
    found = protected.tokens_in(translated)
    problems = []
    for tok in expected:
        n = found.count(tok)
        if n != 1:
            problems.append(f"{tok} appears {n} times (expected 1)")
    extra = sorted(set(found) - set(expected))
    if extra:
        problems.append(f"unexpected tokens {extra}")
    if not problems and found != expected:
        problems.append("tokens are out of order")
    lines = [line.strip() for line in translated.split("\n")]
    for tok in expected:
        if tok in protected.blocks and tok not in lines:
            problems.append(f"code block {tok} is not on a line of its own")
    return problems
