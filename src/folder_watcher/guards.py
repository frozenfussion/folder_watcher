"""Path safety (SPEC.md section 4.2). These rules are enforced in code, never only in a prompt."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

HIDDEN_OR_TEMP_SUFFIXES = ("~", ".swp", ".tmp", ".part", ".crdownload")


class GuardError(ValueError):
    """A path or file name was rejected. The message is safe to show to the model."""


def is_inside(base: Path, path: Path) -> bool:
    """True if `path`, with symlinks and '..' resolved, is `base` or inside it."""
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def is_hidden_or_temp(path: Path) -> bool:
    name = path.name
    return name.startswith(".") or name.endswith(HIDDEN_OR_TEMP_SUFFIXES)


def resolve_job_file(source_dir: Path, path: Path | str) -> Path:
    """Check a file given to the agent lives in source/ and return its resolved path."""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    # Symlink first: a link in source/ pointing elsewhere should be reported as a link.
    if candidate.is_symlink():
        raise GuardError(f"{path} is a symlink; only regular files in the source folder are processed")
    if not is_inside(source_dir, candidate):
        raise GuardError(f"{path} is not inside the source folder {source_dir}")
    return candidate.resolve()


def check_read(job_file: Path, requested: str, project_root: Path) -> Path:
    """The model may only read the file that triggered the job."""
    candidate = Path(requested)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    if candidate.resolve() != job_file.resolve():
        raise GuardError(f"read refused: only the job's file ({job_file.name}) may be read")
    return job_file


def check_filename(name: str) -> str:
    """An output name must be a plain file name: no folders, no '..', not hidden."""
    if not name or name != Path(name).name or name in (".", "..") or "\\" in name:
        raise GuardError(f"invalid file name {name!r}: give a plain file name with no folders")
    if name.startswith("."):
        raise GuardError(f"invalid file name {name!r}: hidden files are not allowed")
    return name


class DestinationGuard:
    """The only way anything is written: inside destination/, never overwriting."""

    def __init__(self, destination_dir: Path) -> None:
        self.root = destination_dir.resolve()

    def write_new(self, name: str, data: bytes) -> Path:
        """Write `data` as `name`, or `name-1`, `name-2`, ... if taken. Returns the final path."""
        check_filename(name)
        self.root.mkdir(parents=True, exist_ok=True)
        # report.en.md -> report.en-1.md: number goes before the final extension.
        stem, dot, ext = name.rpartition(".")
        if not dot:
            stem, ext = name, ""
        # Write to a temp file first, then link it into place: a reader never sees a
        # half-written file, and os.link fails instead of overwriting an existing one.
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".partial-")
        # mkstemp makes owner-only files; give the output normal permissions (respecting umask).
        umask = os.umask(0)
        os.umask(umask)
        os.fchmod(fd, 0o666 & ~umask)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            for n in range(1000):
                # check_filename allows no folders, so candidate sits directly in root.
                # os.link never follows an existing name (even a planted symlink): it
                # raises FileExistsError and we move on to the next number.
                candidate = self.root / (name if n == 0 else f"{stem}-{n}{dot}{ext}")
                try:
                    os.link(tmp, candidate)
                    return candidate
                except FileExistsError:
                    continue
            raise GuardError(f"too many existing files named like {name}")
        finally:
            os.unlink(tmp)
