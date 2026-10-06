"""`check`: a failing check exits non-zero and prints its whole fix, never a stack trace."""

from folder_watcher.checks import Report, run_checks
from folder_watcher.config import DEFAULT_CONFIG_PATH


def test_missing_model_fails_with_a_fix(tmp_path, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text(DEFAULT_CONFIG_PATH.read_text().replace(
        'model_path = "models/Qwen3.5-9B-Q4_K_M.gguf"', f'model_path = "{tmp_path}/missing.gguf"'))
    assert run_checks(cfg) == 1
    out = capsys.readouterr().out
    assert "FAIL  model file missing" in out and "fix: run:  scripts/download_model.sh" in out
    assert "Traceback" not in out


def test_broken_config_is_reported_not_raised(tmp_path, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[watch\nenabled = = true")
    assert run_checks(cfg) == 1
    assert "FAIL  config: cannot parse" in capsys.readouterr().out


def test_multi_line_fix_is_printed_in_full(capsys):
    r = Report()
    r.fail("something", "first line\nsecond line")
    out = capsys.readouterr().out
    assert "fix: first line" in out and "second line" in out and r.failures == 1
