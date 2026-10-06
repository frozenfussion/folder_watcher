"""The job queue, plus one pipeline test: watcher -> queue -> agent loop, with a fake model."""

from __future__ import annotations

import threading
import time
from dataclasses import replace

from conftest import FakeLLM, call
from test_watcher_new_only import Harness, setup, wait_for  # noqa: F401  (setup is a fixture)

from folder_watcher.agent import run_job
from folder_watcher.llm_client import ModelUnavailable
from folder_watcher.queue import JobQueue
from folder_watcher.tools import Registry

FAST = (0.05, 0.1)  # backoff for tests


def test_fifo_one_at_a_time():
    events, running = [], []

    def handler(item, cancel):
        running.append(item)
        assert len(running) == 1  # never two jobs at once
        events.append(item)
        time.sleep(0.05)
        running.remove(item)

    q = JobQueue(handler, lambda: True, FAST)
    q.start()
    for i in range(5):
        q.put(i)
    assert wait_for(lambda: len(events) == 5)
    assert events == [0, 1, 2, 3, 4]
    q.stop(1)


def test_a_failing_job_does_not_stop_the_next():
    done = []

    def handler(item, cancel):
        if item == "bad":
            raise RuntimeError("boom")
        done.append(item)

    q = JobQueue(handler, lambda: True, FAST)
    q.start()
    for item in ("a", "bad", "b"):
        q.put(item)
    assert wait_for(lambda: done == ["a", "b"])
    q.stop(1)


def test_job_stays_queued_while_model_is_down():
    up = threading.Event()
    done, attempts = [], []

    def handler(item, cancel):
        attempts.append(item)
        if not up.is_set():
            raise ModelUnavailable("down")
        done.append(item)

    q = JobQueue(handler, up.is_set, FAST)
    q.start()
    q.put("first")
    q.put("second")
    time.sleep(0.5)
    # Tried once; then it waits for /health instead of hammering the server. "second" waits behind it.
    assert done == [] and attempts == ["first"]
    up.set()
    assert wait_for(lambda: done == ["first", "second"])
    q.stop(1)


def test_stop_cancels_the_current_job():
    started = threading.Event()

    def handler(item, cancel):
        started.set()
        cancel.wait(5)  # a job that only ends when told to

    q = JobQueue(handler, lambda: True, FAST)
    q.start()
    q.put("long")
    assert started.wait(2)
    t = time.monotonic()
    assert q.stop(2) is True
    assert time.monotonic() - t < 1


def test_stop_while_waiting_for_the_model():
    def handler(item, cancel):
        raise ModelUnavailable("down")

    q = JobQueue(handler, lambda: False, (10,))
    q.start()
    q.put("x")
    time.sleep(0.2)
    assert q.stop(2) is True  # the 10 s backoff wait is interrupted


class FlakyLLM(FakeLLM):
    """A fake model server that can be switched off and on."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.up = threading.Event()

    def chat(self, *args, **kwargs):
        if not self.up.is_set():
            raise ModelUnavailable("connection refused")
        return super().chat(*args, **kwargs)


def test_pipeline_survives_a_model_outage(setup):
    """A file dropped while the model is down is translated once it is back."""
    tmp, src, cfg_file = setup
    with Harness(tmp, cfg_file) as h:
        cfg = h.watcher.config()
        path = src / "note.txt"
        llm = FlakyLLM([call("read_file", path=str(path)), call("translate_text"),
                        call("write_translation", translation_id="t1")])
        results = []
        q = JobQueue(lambda job, cancel: results.append(run_job(job.path, cfg, Registry.discover(), llm,
                                                                  cancel=cancel)),
                     llm.up.is_set, FAST)
        h.watcher.submit = q.put
        q.start()
        path.write_text("Bonjour le monde")
        time.sleep(1.0)
        assert results == []                      # queued, waiting for the model
        llm.up.set()
        assert wait_for(lambda: len(results) == 1)
        assert results[0].status == "done"
        assert (cfg.watch.destination_dir / "note.en.txt").read_text() == "Hello the world"
        q.stop(1)


def test_shutdown_abandons_between_steps(setup):
    tmp, src, cfg_file = setup
    with Harness(tmp, cfg_file) as h:
        cfg = replace(h.watcher.config())
        path = src / "a.txt"
        path.write_text("Bonjour")
        cancel = threading.Event()

        class StopsService(FakeLLM):
            def chat(self, *a, **k):
                cancel.set()  # the service is stopped while the model is thinking
                return super().chat(*a, **k)

        llm = StopsService([call("read_file", path=str(path)), call("translate_text")])
        result = run_job(path, cfg, Registry.discover(), llm, cancel=cancel)
        assert result.status == "abandoned"
        assert list(cfg.watch.destination_dir.iterdir()) == []
