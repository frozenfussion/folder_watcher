"""Config loading, validation and live reload (SPEC.md sections 6 and 14)."""

import os
import shutil
from pathlib import Path

import pytest

from folder_watcher.config import DEFAULT_CONFIG_PATH, PROJECT_ROOT, ConfigError, LiveConfig, load_config


@pytest.fixture
def cfg_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    shutil.copy(DEFAULT_CONFIG_PATH, path)
    return path


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))


def test_shipped_config_loads():
    cfg = load_config()
    assert cfg.watch.enabled is True
    assert cfg.watch.extensions == (".txt", ".md")
    assert cfg.watch.source_dir == PROJECT_ROOT / "source"
    assert cfg.llm.host == "127.0.0.1"
    assert cfg.llm.gpu_layers == "auto"
    assert cfg.llm.sampling_translate.temperature == 0.3


def test_host_must_be_localhost(cfg_file):
    edit(cfg_file, 'host = "127.0.0.1"', 'host = "0.0.0.0"')
    with pytest.raises(ConfigError, match="localhost"):
        load_config(cfg_file)


@pytest.mark.parametrize("value", ['"all"', '"0"', "33"])
def test_gpu_layers_accepts(cfg_file, value):
    edit(cfg_file, 'gpu_layers = "auto"', f"gpu_layers = {value}")
    assert load_config(cfg_file).llm.gpu_layers == value.strip('"')


def test_bad_english_action(cfg_file):
    edit(cfg_file, 'english_action = "skip"', 'english_action = "delete"')
    with pytest.raises(ConfigError, match="english_action"):
        load_config(cfg_file)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_live_reload_picks_up_enabled(cfg_file):
    live = LiveConfig(cfg_file)
    assert live.get().watch.enabled is True
    edit(cfg_file, "enabled = true", "enabled = false")
    assert live.get().watch.enabled is False


def test_malformed_file_keeps_last_good(cfg_file):
    live = LiveConfig(cfg_file)
    edit(cfg_file, "enabled = true", "enabled = = oops")
    assert live.get().watch.enabled is True  # still the old, good value
    edit(cfg_file, "enabled = = oops", "enabled = false")
    assert live.get().watch.enabled is False  # recovers once fixed
