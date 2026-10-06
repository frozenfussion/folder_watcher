"""The watcher, with real filesystem events on temp folders and no model (SPEC.md section 14)."""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from folder_watcher.config import DEFAULT_CONFIG_PATH, LiveConfig, set_value
from folder_watcher.state import Ledger
from folder_watcher.tools import Registry
from folder_watcher.watcher import Watcher


def wait_for(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def setup(tmp_path):
    """A config file pointing at temp folders, with a short stability window."""
    src, dst = tmp_path / "source", tmp_path / "destination"
    src.mkdir()
    dst.mkdir()
    cfg_file = tmp_path / "config.toml"
    text = DEFAULT_CONFIG_PATH.read_text()
    text = text.replace('source_dir = "source"', f'source_dir = "{src}"')
    text = text.replace('destination_dir = "destination"', f'destination_dir = "{dst}"')
    text = text.replace("stable_seconds = 2.0", "stable_seconds = 0.3")
    text = text.replace("stable_timeout_seconds = 60.0", "stable_timeout_seconds = 5.0")
    cfg_file.write_text(text)
    return tmp_path, src, cfg_file


class Harness:
    def __init__(self, tmp_path, cfg_file):
        self.live = LiveConfig(cfg_file)
        self.jobs = []
        self.watcher = Watcher(self.live, self.live.get(), Registry.discover(),
                               Ledger(tmp_path / "state" / "ledger.jsonl"), self.jobs.append, poll=0.05)

    def names(self):
        return [j.path.name for j in self.jobs]

    def __enter__(self):
        self.watcher.start()
        return self

    def __exit__(self, *exc):
        self.watcher.stop()


def test_existing_files_are_ignored(setup):
    tmp, src, cfg = setup
    (src / "old.txt").write_text("Bonjour")
    with Harness(tmp, cfg) as h:
        with (src / "old.txt").open("a") as f:  # a modified event is not an arrival
            f.write(" encore")
        time.sleep(1.0)
        assert h.jobs == []


def test_new_file_is_picked_up(setup):
    tmp, src, cfg = setup
    with Harness(tmp, cfg) as h:
        (src / "new.txt").write_text("Bonjour")
        assert wait_for(lambda: h.names() == ["new.txt"])


def test_file_moved_in_is_picked_up(setup):
    tmp, src, cfg = setup
    outside = tmp / "outside.md"
    outside.write_text("# Bonjour")
    with Harness(tmp, cfg) as h:
        os.rename(outside, src / "moved.md")
        assert wait_for(lambda: h.names() == ["moved.md"])


def test_download_renamed_into_place(setup):
    tmp, src, cfg = setup
    with Harness(tmp, cfg) as h:
        (src / "report.txt.part").write_text("Bonjour")  # temp name: ignored
        os.rename(src / "report.txt.part", src / "report.txt")
        assert wait_for(lambda: h.names() == ["report.txt"])
        time.sleep(0.5)
        assert h.names() == ["report.txt"]


def test_growing_file_waits_until_it_settles(setup):
    tmp, src, cfg = setup
    path = src / "slow.txt"
    with Harness(tmp, cfg) as h:
        def slow_copy():
            with path.open("w") as f:
                for _ in range(12):  # about 1.2 s of writing
                    f.write("Bonjour le monde.\n")
                    f.flush()
                    time.sleep(0.1)

        writer = threading.Thread(target=slow_copy)
        writer.start()
        time.sleep(0.9)
        assert h.jobs == []           # still growing: not queued
        writer.join()
        assert wait_for(lambda: h.names() == ["slow.txt"])
        assert path.stat().st_size == 12 * len("Bonjour le monde.\n")


def test_file_that_never_settles_is_given_up(setup, caplog):
    tmp, src, cfg = setup
    set_value(cfg, "watch.stable_timeout_seconds", "0.8")
    path = src / "endless.txt"
    stop = threading.Event()
    with Harness(tmp, cfg) as h:
        def keep_writing():
            with path.open("w") as f:
                while not stop.is_set():
                    f.write("x")
                    f.flush()
                    time.sleep(0.05)

        writer = threading.Thread(target=keep_writing)
        writer.start()
        assert wait_for(lambda: "still changing" in caplog.text)
        stop.set()
        writer.join()
        assert h.jobs == []


@pytest.mark.parametrize("name", [".hidden.txt", "notes.txt~", "notes.swp", "notes.tmp", "notes.part",
                                  "notes.crdownload", "scan.pdf", "image.png", "README"])
def test_ignored_files(setup, caplog, name):
    tmp, src, cfg = setup
    with Harness(tmp, cfg) as h:
        (src / name).write_text("data")
        time.sleep(0.8)
        assert h.jobs == []
    assert "ERROR" not in caplog.text


def test_pdf_gets_a_clear_log_line(setup, caplog):
    tmp, src, cfg = setup
    caplog.set_level("INFO")
    with Harness(tmp, cfg):
        (src / "scan.pdf").write_bytes(b"%PDF-1.7")
        assert wait_for(lambda: "ignored scan.pdf: .pdf is not in watch.extensions" in caplog.text)


def test_empty_file_is_skipped(setup, caplog):
    tmp, src, cfg = setup
    caplog.set_level("INFO")
    with Harness(tmp, cfg) as h:
        (src / "empty.txt").write_text("")
        assert wait_for(lambda: "skipped empty.txt: the file is empty" in caplog.text)
        assert h.jobs == []


def test_duplicate_events_process_a_file_once(setup):
    tmp, src, cfg = setup
    with Harness(tmp, cfg) as h:
        path = src / "dup.txt"
        path.write_text("Bonjour")
        for _ in range(3):  # extra events for the same file, during and after settling
            h.watcher.arrived(path, "created")
        assert wait_for(lambda: len(h.jobs) == 1)
        h.watcher.arrived(path, "created")
        time.sleep(0.8)
        assert h.names() == ["dup.txt"]


def test_copying_the_same_file_again_is_a_new_arrival(setup):
    tmp, src, cfg = setup
    sample = tmp / "sample.txt"
    sample.write_text("Bonjour")
    with Harness(tmp, cfg) as h:
        shutil.copy(sample, src / "again.txt")
        assert wait_for(lambda: len(h.jobs) == 1)
        time.sleep(0.05)
        (src / "again.txt").unlink()
        shutil.copy(sample, src / "again.txt")  # new mtime: a new arrival
        assert wait_for(lambda: len(h.jobs) == 2)


def test_live_toggle(setup, caplog):
    tmp, src, cfg = setup
    caplog.set_level("INFO")
    with Harness(tmp, cfg) as h:
        set_value(cfg, "watch.enabled", "false")
        h.watcher.config()
        assert "watching disabled" in caplog.text
        (src / "while_off.txt").write_text("Bonjour")
        assert wait_for(lambda: "ignored while_off.txt: watching is disabled" in caplog.text)
        set_value(cfg, "watch.enabled", "true")
        h.watcher.config()
        assert "watching enabled" in caplog.text
        with (src / "while_off.txt").open("a") as f:  # touching it later does not revive it
            f.write("!")
        (src / "after_on.txt").write_text("Bonjour")
        assert wait_for(lambda: h.names() == ["after_on.txt"])
        time.sleep(0.5)
        assert h.names() == ["after_on.txt"]


def test_toggle_is_logged_once_without_any_file(setup, caplog):
    tmp, src, cfg = setup
    caplog.set_level("INFO")
    with Harness(tmp, cfg) as h:
        set_value(cfg, "watch.enabled", "false")
        for _ in range(3):
            h.watcher.config()  # what the main loop does every second
        assert caplog.text.count("watching disabled") == 1


def test_malformed_config_keeps_last_good(setup, caplog):
    tmp, src, cfg = setup
    with Harness(tmp, cfg) as h:
        cfg.write_text(cfg.read_text().replace("enabled = true", "enabled = = broken"))
        h.watcher.config()
        assert "keeping last good config" in caplog.text
        (src / "still_works.txt").write_text("Bonjour")
        assert wait_for(lambda: h.names() == ["still_works.txt"])


def test_extension_list_is_live(setup):
    tmp, src, cfg = setup
    with Harness(tmp, cfg) as h:
        set_value(cfg, "watch.extensions", '[".md"]')
        (src / "now_ignored.txt").write_text("Bonjour")
        (src / "kept.md").write_text("Bonjour")
        assert wait_for(lambda: h.names() == ["kept.md"])
        time.sleep(0.5)
        assert h.names() == ["kept.md"]


def test_llm_section_is_not_live(setup, caplog):
    tmp, src, cfg = setup
    with Harness(tmp, cfg) as h:
        before = h.watcher.config().llm
        set_value(cfg, "llm.ctx_size", "4096")
        assert h.watcher.config().llm == before
        assert "apply only after a restart" in caplog.text
