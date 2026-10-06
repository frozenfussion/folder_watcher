"""Tool registry: discovery, extension lookup, per-file schema filtering, config validation."""

from pathlib import Path

import pytest

from folder_watcher.tools import Registry
from folder_watcher.tools.base import Tool


def names(schemas):
    return [s["function"]["name"] for s in schemas]


def test_discovers_the_v1_tools(registry):
    assert registry.names() == ["read_file", "skip_file", "translate_text", "write_translation"]
    assert registry.get("skip_file").terminal and registry.get("write_translation").terminal
    assert not registry.get("translate_text").terminal


def test_extension_lookup(registry):
    assert [t.name for t in registry.for_extension(".md")] == ["read_file"]
    assert [t.name for t in registry.for_extension(".TXT")] == ["read_file"]
    assert registry.for_extension(".pdf") == []
    assert registry.supported_extensions() == {".txt", ".md"}


def test_config_validation(registry):
    assert registry.unsupported((".txt", ".md")) == []
    assert registry.unsupported((".txt", ".pdf")) == [".pdf"]


def test_schemas_only_show_matching_readers(registry):
    pdf_reader = Tool("read_pdf", "Read a PDF.", {"type": "object", "properties": {}}, lambda a, c: {},
                      extensions=(".pdf",))
    reg = Registry([*(registry.get(n) for n in registry.names()), pdf_reader])
    assert "read_pdf" not in names(reg.schemas_for_job(Path("a.txt")))
    assert "read_file" not in names(reg.schemas_for_job(Path("a.pdf")))
    assert names(reg.schemas_for_job(Path("a.pdf")))[0] == "read_pdf"


def test_schema_format(registry):
    schema = registry.get("read_file").schema()
    assert schema["type"] == "function"
    assert schema["function"]["parameters"]["required"] == ["path"]


def test_duplicate_names_rejected(registry):
    with pytest.raises(ValueError):
        Registry([registry.get("skip_file"), registry.get("skip_file")])
