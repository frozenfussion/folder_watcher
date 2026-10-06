"""A thin client for the local OpenAI-compatible model server (llama-server).

Thinking stays off: the server runs with --reasoning off, and this client never sends
chat_template_kwargs, so no request can switch thinking back on.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from openai import OpenAI

from .config import LLMConfig, SamplingProfile


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text, exactly as the model produced it


@dataclass
class Reply:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"


def request_kwargs(model: str, messages: list[dict], profile: SamplingProfile,
                   tools: list[dict] | None = None, max_tokens: int | None = None) -> dict:
    """Everything sent to /v1/chat/completions. Pure, so tests can check it."""
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": profile.temperature,
        "top_p": profile.top_p,
        "presence_penalty": profile.presence_penalty,
        # Not part of the OpenAI API, but accepted by llama-server.
        "extra_body": {"top_k": profile.top_k, "min_p": profile.min_p},
    }
    if tools:
        kwargs["tools"] = tools
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    return kwargs


class LLMClient:
    def __init__(self, llm: LLMConfig, timeout: float = 600) -> None:
        self.llm = llm
        # The key is a dummy: the server is local and has no key set.
        self._client = OpenAI(base_url=llm.base_url + "/v1", api_key="local", timeout=timeout, max_retries=2)

    def chat(self, messages: list[dict], profile: SamplingProfile,
             tools: list[dict] | None = None, max_tokens: int | None = None) -> Reply:
        r = self._client.chat.completions.create(
            **request_kwargs(self.llm.alias, messages, profile, tools, max_tokens))
        choice = r.choices[0]
        calls = [ToolCall(tc.id, tc.function.name, tc.function.arguments or "{}")
                 for tc in (choice.message.tool_calls or [])]
        return Reply(choice.message.content or "", calls, choice.finish_reason or "")

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(self.llm.base_url + "/health", timeout=3) as r:
                return json.load(r).get("status") == "ok"
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def wait_until_ready(self, timeout: float, on_wait=None) -> bool:
        """Poll /health until the model is loaded. Calls on_wait() once if it has to wait."""
        deadline = time.monotonic() + timeout
        waited = False
        while time.monotonic() < deadline:
            if self.healthy():
                return True
            if not waited and on_wait:
                on_wait()
            waited = True
            time.sleep(1)
        return False
