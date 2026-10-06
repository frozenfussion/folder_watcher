"""Path safety: nothing is read outside source/ or written outside destination/."""

import os

import pytest

from folder_watcher.guards import (DestinationGuard, GuardError, check_filename, check_read, is_hidden_or_temp,
                                   is_inside, resolve_job_file)


def test_is_inside_rejects_dotdot_and_absolute(tmp_path):
    base = tmp_path / "destination"
    base.mkdir()
    assert is_inside(base, base / "a.txt")
    assert not is_inside(base, base / ".." / "a.txt")
    assert not is_inside(base, tmp_path / "elsewhere" / "a.txt")
    assert not is_inside(base, base.parent)


def test_is_inside_follows_symlinks(tmp_path):
    base = tmp_path / "destination"
    base.mkdir()
    (base / "escape").symlink_to(tmp_path)
    assert not is_inside(base, base / "escape" / "a.txt")


def test_resolve_job_file(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    (src / "a.txt").write_text("x")
    (tmp_path / "secret.txt").write_text("x")
    assert resolve_job_file(src, src / "a.txt") == (src / "a.txt").resolve()
    with pytest.raises(GuardError):
        resolve_job_file(src, src / ".." / "secret.txt")
    (src / "link.txt").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(GuardError):
        resolve_job_file(src, src / "link.txt")


def test_read_only_the_job_file(tmp_path):
    job = tmp_path / "source" / "a.txt"
    job.parent.mkdir()
    job.write_text("x")
    assert check_read(job, "source/a.txt", tmp_path) == job
    for bad in ("source/../source/b.txt", "/etc/passwd", "../a.txt", "source/b.txt"):
        with pytest.raises(GuardError):
            check_read(job, bad, tmp_path)


@pytest.mark.parametrize("name", ["", "../evil.md", "a/b.md", "/etc/passwd", ".hidden.md", "..", "a\\b.md"])
def test_bad_file_names(name):
    with pytest.raises(GuardError):
        check_filename(name)


def test_write_never_overwrites(tmp_path):
    guard = DestinationGuard(tmp_path)
    first = guard.write_new("report.en.md", b"one")
    second = guard.write_new("report.en.md", b"two")
    third = guard.write_new("report.en.md", b"three")
    assert [p.name for p in (first, second, third)] == ["report.en.md", "report.en-1.md", "report.en-2.md"]
    assert first.read_bytes() == b"one"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".partial-")]  # temp files cleaned up


def test_write_does_not_follow_a_planted_symlink(tmp_path):
    dest = tmp_path / "destination"
    dest.mkdir()
    outside = tmp_path / "outside.md"
    (dest / "report.en.md").symlink_to(outside)
    path = DestinationGuard(dest).write_new("report.en.md", b"data")
    assert path.name == "report.en-1.md"
    assert not outside.exists()


def test_write_rejects_escaping_name(tmp_path):
    with pytest.raises(GuardError):
        DestinationGuard(tmp_path / "destination").write_new("../evil.md", b"x")
    assert not (tmp_path / "evil.md").exists()


def test_output_permissions_follow_umask(tmp_path):
    old = os.umask(0o022)
    try:
        path = DestinationGuard(tmp_path).write_new("a.md", b"x")
    finally:
        os.umask(old)
    assert oct(path.stat().st_mode & 0o777) == oct(0o644)


@pytest.mark.parametrize("name,hidden", [(".x.md", True), ("a.md~", True), ("a.swp", True), ("a.part", True),
                                         ("a.crdownload", True), ("a.tmp", True), ("a.md", False)])
def test_hidden_or_temp(tmp_path, name, hidden):
    assert is_hidden_or_temp(tmp_path / name) is hidden
