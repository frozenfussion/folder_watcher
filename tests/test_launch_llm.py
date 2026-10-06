"""The llama-server command built from config (SPEC.md section 9). No model needed."""

import shutil
from pathlib import Path

from folder_watcher.config import DEFAULT_CONFIG_PATH, load_config
from folder_watcher.launch_llm import build_argv


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_default_command():
    argv = build_argv(load_config(), server=Path("/x/llama-server"), backend="cuda")
    assert argv[0] == "/x/llama-server"
    assert flag(argv, "--host") == "127.0.0.1"
    assert flag(argv, "--port") == "8080"
    assert flag(argv, "-c") == "8192"
    assert flag(argv, "-ngl") == "auto"
    assert flag(argv, "--reasoning") == "off"
    assert "--jinja" in argv and "--offline" in argv


def test_cpu_build_leaves_out_gpu_layers():
    argv = build_argv(load_config(), server=Path("/x/llama-server"), backend="cpu")
    assert "-ngl" not in argv


def test_extra_args_come_last(tmp_path):
    cfg_file = tmp_path / "config.toml"
    shutil.copy(DEFAULT_CONFIG_PATH, cfg_file)
    cfg_file.write_text(cfg_file.read_text().replace("extra_args = []", 'extra_args = ["--threads", "4"]'))
    argv = build_argv(load_config(cfg_file), server=Path("/x/llama-server"), backend="cuda")
    assert argv[-2:] == ["--threads", "4"]
