"""The agent loop, with a fake LLM: decisions come from the (fake) model, rules from the code."""

import json
import re
import time
from dataclasses import replace

from conftest import FakeLLM, call

from folder_watcher.agent import run_job
from folder_watcher.config import load_config
from folder_watcher.llm_client import Reply, ToolCall, request_kwargs

FRENCH = """# Bonjour

Bonjour le monde. Voici `make test` et https://exemple.fr/page.

```bash
# Bonjour, ne pas traduire
echo Bonjour
```

Merci.
"""


def make(cfg, name, text):
    path = cfg.watch.source_dir / name
    path.write_text(text)
    return path


def outputs(cfg):
    return sorted(p.name for p in cfg.watch.destination_dir.iterdir())


def tool_results(llm):
    """The tool results the model saw on its last turn."""
    return [json.loads(m["content"]) for m in llm.agent_calls[-1]["messages"] if m["role"] == "tool"]


def test_skip_english(cfg, registry):
    path = make(cfg, "note.txt", "Hello team.")
    llm = FakeLLM([call("read_file", path=str(path)), call("skip_file", reason="already English")])
    result = run_job(path, cfg, registry, llm)
    assert (result.status, result.reason, result.steps) == ("skipped", "already English", 2)
    assert outputs(cfg) == []
    assert llm.translate_calls == []


def test_skip_with_copy_action(cfg, registry):
    cfg = replace(cfg, agent=replace(cfg.agent, english_action="copy"))
    path = make(cfg, "note.txt", "Hello team.")
    llm = FakeLLM([call("read_file", path=str(path)), call("skip_file", reason="English")])
    assert run_job(path, cfg, registry, llm).status == "skipped"
    assert (cfg.watch.destination_dir / "note.txt").read_text() == "Hello team."


def test_translate_protects_code_and_urls(cfg, registry):
    path = make(cfg, "doc.md", FRENCH)
    llm = FakeLLM([call("read_file", path=str(path)), call("translate_text", source_language="French"),
                   call("write_translation", translation_id="t1")])
    result = run_job(path, cfg, registry, llm)
    assert result.status == "done"
    out = (cfg.watch.destination_dir / "doc.en.md").read_text()
    assert out.startswith("# Hello\n\nHello the world.")
    assert "```bash\n# Bonjour, ne pas traduire\necho Bonjour\n```" in out  # code untouched
    assert "`make test`" in out and "https://exemple.fr/page." in out
    assert out.endswith("Thank you.\n")
    sent = "".join(llm.translate_calls)
    assert "ne pas traduire" not in sent and "exemple.fr" not in sent and "make test" not in sent


def test_damaged_chunk_is_retried_once(cfg, registry):
    path = make(cfg, "doc.md", FRENCH)
    attempts = []

    def flaky(text):
        attempts.append(text)
        out = FakeLLM.translate(text)
        return re.sub(r"⟦B\d+⟧", "", out) if len(attempts) == 1 else out  # drop the code block once

    llm = FakeLLM([call("read_file", path=str(path)), call("translate_text"),
                   call("write_translation", translation_id="t1")], translator=flaky)
    assert run_job(path, cfg, registry, llm).status == "done"
    assert len(attempts) == 2


def test_damaged_twice_fails_and_writes_nothing(cfg, registry):
    path = make(cfg, "doc.md", FRENCH)
    llm = FakeLLM([call("read_file", path=str(path)), call("translate_text"),
                   call("write_translation", translation_id="t1")],
                  translator=lambda t: re.sub(r"⟦U\d+⟧", "https://changed.example", FakeLLM.translate(t)))
    result = run_job(path, cfg, registry, llm)
    assert result.status == "failed"
    assert "damaged after a retry" in result.reason and "Nothing was written" in result.reason
    assert outputs(cfg) == []


def test_empty_file_needs_no_model(cfg, registry):
    path = make(cfg, "empty.txt", "")
    llm = FakeLLM([])
    result = run_job(path, cfg, registry, llm)
    assert (result.status, result.reason) == ("skipped", "empty file (no model call)")
    assert llm.agent_calls == []


def test_unsupported_extension(cfg, registry):
    path = make(cfg, "a.pdf", "%PDF")
    result = run_job(path, cfg, registry, FakeLLM([]))
    assert result.status == "failed" and ".pdf" in result.reason


def test_path_escape_is_refused_before_the_model(cfg, registry, tmp_path):
    (tmp_path / "secret.txt").write_text("secret")
    llm = FakeLLM([])
    result = run_job(cfg.watch.source_dir / ".." / "secret.txt", cfg, registry, llm)
    assert result.status == "failed" and "not inside the source folder" in result.reason
    assert llm.agent_calls == []


def test_model_cannot_read_another_file(cfg, registry, tmp_path):
    path = make(cfg, "a.txt", "Bonjour")
    (tmp_path / "secret.txt").write_text("secret")
    llm = FakeLLM([call("read_file", path=str(cfg.watch.source_dir / ".." / "secret.txt"))])
    run_job(path, cfg, registry, llm)
    assert "read refused" in tool_results(llm)[0]["error"]


def test_tool_errors_go_back_to_the_model(cfg, registry, tmp_path):
    path = make(cfg, "a.txt", "Bonjour")
    llm = FakeLLM([
        call("translate_text"),                                          # before read_file
        call("no_such_tool"),
        Reply("", [ToolCall("id-bad", "read_file", "{not json")], "tool_calls"),
        call("read_file", path=str(path)),
        call("translate_text"),
        call("write_translation", translation_id="t1", filename="../../evil.en.txt"),
        call("write_translation", translation_id="t1"),
    ])
    cfg = replace(cfg, agent=replace(cfg.agent, max_steps=10))
    result = run_job(path, cfg, registry, llm)
    errors = [r["error"] for r in tool_results(llm) if "error" in r]
    assert "call read_file first" in errors[0]
    assert "unknown tool" in errors[1]
    assert "not valid JSON" in errors[2]
    assert "invalid file name" in errors[3]
    assert result.status == "done" and outputs(cfg) == ["a.en.txt"]
    assert not (tmp_path / "evil.en.txt").exists() and not (tmp_path.parent / "evil.en.txt").exists()


def test_step_limit(cfg, registry):
    path = make(cfg, "a.txt", "Bonjour")
    llm = FakeLLM([call("read_file", path=str(path))] * 20)
    result = run_job(path, cfg, registry, llm, max_steps=3)
    assert result.status == "failed" and "step limit of 3" in result.reason
    assert len(llm.agent_calls) == 3


def test_model_stops_without_terminal_tool(cfg, registry):
    path = make(cfg, "a.txt", "Bonjour")
    llm = FakeLLM([call("read_file", path=str(path)), Reply("This looks French.")])
    result = run_job(path, cfg, registry, llm)
    assert result.status == "failed" and "without calling skip_file or write_translation" in result.reason


def test_time_limit(cfg, registry):
    cfg = replace(cfg, agent=replace(cfg.agent, job_timeout_seconds=0.05))
    path = make(cfg, "a.txt", "Bonjour")

    class SlowLLM(FakeLLM):
        def chat(self, *args, **kwargs):
            time.sleep(0.03)
            return super().chat(*args, **kwargs)

    llm = SlowLLM([call("read_file", path=str(path))] * 5)
    result = run_job(path, cfg, registry, llm)
    assert result.status == "failed" and "time limit" in result.reason
    assert len(llm.agent_calls) == 2  # stopped at the first step boundary after the deadline


def test_existing_output_is_never_overwritten(cfg, registry):
    (cfg.watch.destination_dir / "a.en.txt").write_text("old")
    path = make(cfg, "a.txt", "Bonjour")
    llm = FakeLLM([call("read_file", path=str(path)), call("translate_text"),
                   call("write_translation", translation_id="t1")])
    assert run_job(path, cfg, registry, llm).status == "done"
    assert (cfg.watch.destination_dir / "a.en.txt").read_text() == "old"
    assert (cfg.watch.destination_dir / "a.en-1.txt").read_text() == "Hello"


def test_document_text_not_logged_at_info(cfg, registry, caplog):
    path = make(cfg, "a.txt", "Bonjour CONFIDENTIEL")
    llm = FakeLLM([call("read_file", path=str(path)), call("skip_file", reason="test")])
    with caplog.at_level("INFO"):
        run_job(path, cfg, registry, llm)
    assert "CONFIDENTIEL" not in caplog.text
    assert "shown at DEBUG" in caplog.text


def test_requests_never_turn_thinking_on():
    cfg = load_config()
    for profile in (cfg.llm.sampling_agent, cfg.llm.sampling_translate):
        kwargs = request_kwargs("m", [], profile, tools=[{}], max_tokens=10)
        text = json.dumps(kwargs)
        assert "chat_template_kwargs" not in text and "enable_thinking" not in text and "reasoning" not in text
        assert kwargs["temperature"] == profile.temperature
        assert kwargs["extra_body"] == {"top_k": profile.top_k, "min_p": profile.min_p}


def test_placeholder_rules_only_when_needed(cfg, registry):
    """Showing an example token to the model when there are none made it invent one."""
    systems = []

    class Recorder(FakeLLM):
        def chat(self, messages, profile, tools=None, max_tokens=None):
            if not tools:
                systems.append(messages[0]["content"])
            return super().chat(messages, profile, tools, max_tokens)

    for name, text in (("plain.txt", "Bonjour le monde."), ("code.md", "Voici `make`.")):
        path = make(cfg, name, text)
        llm = Recorder([call("read_file", path=str(path)), call("translate_text"),
                        call("write_translation", translation_id="t1")])
        assert run_job(path, cfg, registry, llm).status == "done"
    assert "⟦" not in systems[0] and "placeholder" not in systems[0]
    assert "⟦C1⟧" in systems[1]
