"""Code and URL protection: the model never sees them, and they come back byte-identical."""

from folder_watcher.protect import check_chunk, protect

DOC = """# Guide

Lancez `make test` puis lisez [la doc](https://exemple.fr/guide-d-installation?langue=fr).
Voir aussi https://exemple.fr/contact.

```bash
# commentaire en français
ls -la source/
```

~~~
non fermé ~~~ pas une fin
~~~
"""


def test_nothing_protected_reaches_the_model():
    p = protect(DOC)
    for secret in ("make test", "exemple.fr", "commentaire", "ls -la", "non fermé"):
        assert secret not in p.text
    assert "la doc" in p.text  # link text is still translated


def test_restore_is_exact():
    p = protect(DOC)
    assert p.restore(p.text) == DOC


def test_url_trailing_punctuation_is_not_protected():
    p = protect("Voir https://exemple.fr/contact.")
    assert p.text.endswith("⟧.")
    assert list(p.originals.values()) == ["https://exemple.fr/contact"]


def test_block_tokens_are_alone_on_their_line():
    p = protect(DOC)
    lines = p.text.split("\n")
    assert all(tok in lines for tok in p.blocks)
    assert len(p.blocks) == 2


def test_unclosed_fence_runs_to_the_end():
    text = "Texte\n\n```\ncode sans fin\n"
    p = protect(text)
    assert "code sans fin" not in p.text
    assert p.restore(p.text) == text


def test_other_brackets_when_text_contains_ours():
    text = "Le symbole ⟦ est rare. `code`"
    p = protect(text)
    assert "⟪C1⟫" in p.text
    assert p.restore(p.text) == text


def test_check_chunk_accepts_good_output():
    p = protect(DOC)
    translated = p.text.replace("Lancez", "Run").replace("puis lisez", "then read")
    assert check_chunk(p, p.text, translated) == []


def test_check_chunk_finds_damage():
    p = protect(DOC)
    tokens = p.tokens_in(p.text)
    first, block = tokens[0], next(t for t in tokens if t in p.blocks)
    assert "appears 0 times" in " ".join(check_chunk(p, p.text, p.text.replace(first, "")))
    assert "appears 2 times" in " ".join(check_chunk(p, p.text, p.text + first))
    assert "not on a line of its own" in " ".join(check_chunk(p, p.text, p.text.replace("\n\n" + block, " " + block)))
    swapped = p.text.replace(tokens[0], "@@").replace(tokens[1], tokens[0]).replace("@@", tokens[1])
    assert "out of order" in " ".join(check_chunk(p, p.text, swapped))
    assert "unexpected" in " ".join(check_chunk(p, "no tokens here", "but ⟦U9⟧ appeared"))
