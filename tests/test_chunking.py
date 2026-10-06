"""Chunking: round-trip, no split inside code fences, headings stay attached."""

import random
from pathlib import Path

import pytest

from folder_watcher.chunking import chunk, join

SAMPLES = Path(__file__).parent / "samples"

DOC = """# Titre

Premier paragraphe. Deuxième phrase ici.

## Section

- un
- deux

```python
def f():

    return 1   # blank line above is inside the fence
```

Dernier paragraphe.
"""


@pytest.mark.parametrize("size", [10, 40, 80, 200, 5000])
def test_round_trip(size):
    assert join(chunk(DOC, size)) == DOC


def test_round_trip_samples():
    for path in SAMPLES.iterdir():
        text = path.read_text()
        for size in (50, 300, 3000):
            assert join(chunk(text, size)) == text


def test_round_trip_random_text():
    rng = random.Random(42)
    parts = ["word ", "Sentence. ", "\n", "\n\n", "# H\n", "```\ncode\n\n```\n", "  ", "- item\n"]
    for _ in range(200):
        text = "".join(rng.choice(parts) for _ in range(rng.randint(0, 60)))
        assert join(chunk(text, rng.randint(5, 80))) == text


def test_never_splits_inside_a_code_fence():
    for size in (10, 20, 40):
        for piece in chunk(DOC, size):
            assert piece.count("```") % 2 == 0, piece


def test_heading_stays_with_next_paragraph():
    pieces = chunk(DOC, 40)
    assert any(p.startswith("# Titre\n\nPremier paragraphe") for p in pieces)
    assert not any(p.strip() in ("# Titre", "## Section") for p in pieces)


def test_small_text_is_one_chunk():
    assert chunk("Bonjour.\n", 3000) == ["Bonjour.\n"]


def test_long_paragraph_splits_on_sentences():
    text = "Une phrase assez longue. " * 20
    pieces = chunk(text, 100)
    assert len(pieces) > 1
    assert all(len(p) <= 100 for p in pieces)
    assert join(pieces) == text


def test_empty_text():
    assert chunk("", 100) == []
