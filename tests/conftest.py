"""Shared test helpers: a config in a temp folder and a fake LLM. No model server needed."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from folder_watcher.config import load_config
from folder_watcher.llm_client import Reply, ToolCall
from folder_watcher.tools import Registry


@pytest.fixture
def cfg(tmp_path: Path):
    base = load_config()
    (tmp_path / "source").mkdir()
    (tmp_path / "destination").mkdir()
    return replace(base, watch=replace(base.watch, source_dir=tmp_path / "source",
                                       destination_dir=tmp_path / "destination"))


@pytest.fixture
def registry():
    return Registry.discover()


def call(name: str, **args) -> Reply:
    """A model reply that calls one tool."""
    return Reply("", [ToolCall(f"id-{name}", name, json.dumps(args))], "tool_calls")


class FakeLLM:
    """Plays back scripted agent replies; 'translates' by replacing French words.

    `translator` can be swapped for a function that damages the output, to test the checks.
    """

    WORDS = {"Bonjour": "Hello", "le monde": "the world", "Merci": "Thank you", "Voici": "Here is"}

    def __init__(self, agent_replies: list[Reply], translator=None) -> None:
        self.agent_replies = list(agent_replies)
        self.translator = translator or self.translate
        self.agent_calls: list[dict] = []
        self.translate_calls: list[str] = []

    @classmethod
    def translate(cls, text: str) -> str:
        for fr, en in cls.WORDS.items():
            text = text.replace(fr, en)
        return text

    def chat(self, messages, profile, tools=None, max_tokens=None) -> Reply:
        if tools:  # an agent turn
            self.agent_calls.append({"messages": [dict(m) for m in messages], "tools": tools})
            if not self.agent_replies:
                return Reply("I am done.")
            return self.agent_replies.pop(0)
        text = messages[-1]["content"]
        self.translate_calls.append(text)
        return Reply(self.translator(text))
