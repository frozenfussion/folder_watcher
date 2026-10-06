"""Load and validate config/config.toml (SPEC.md section 6)."""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - only on Python 3.10
    import tomli as tomllib

log = logging.getLogger(__name__)

# src/folder_watcher/config.py -> project root is three levels up.
# The package is installed in editable mode, so this points at the checkout.
# FOLDER_WATCHER_ROOT overrides it (useful for tests).
PROJECT_ROOT = Path(os.environ.get("FOLDER_WATCHER_ROOT", Path(__file__).resolve().parents[2]))
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.toml"

ENGLISH_ACTIONS = ("skip", "copy")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


class ConfigError(ValueError):
    """The config file is missing, unparsable, or has an invalid value."""


@dataclass(frozen=True)
class WatchConfig:
    enabled: bool
    source_dir: Path
    destination_dir: Path
    extensions: tuple[str, ...]
    ignore_hidden: bool
    stable_seconds: float
    stable_timeout_seconds: float


@dataclass(frozen=True)
class AgentConfig:
    english_action: str
    output_suffix: str
    max_steps: int
    job_timeout_seconds: float
    chunk_max_chars: int


@dataclass(frozen=True)
class SamplingProfile:
    temperature: float
    top_p: float
    top_k: int
    min_p: float
    presence_penalty: float


@dataclass(frozen=True)
class LLMConfig:
    host: str
    port: int
    model_path: Path
    alias: str
    ctx_size: int
    gpu_layers: str  # "auto", "all", or a non-negative integer as text
    parallel: int
    extra_args: tuple[str, ...]
    sampling_agent: SamplingProfile
    sampling_translate: SamplingProfile

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    trace_agent: bool


@dataclass(frozen=True)
class Config:
    watch: WatchConfig
    agent: AgentConfig
    llm: LLMConfig
    logging: LoggingConfig
    path: Path = field(compare=False)


# --- small typed accessors: they turn a bad value into a readable error ---

def _section(data: dict, name: str) -> dict:
    value = data.get(name)
    if not isinstance(value, dict):
        raise ConfigError(f"missing section [{name}]")
    return value


def _get(sec: dict, where: str, key: str, kind: type | tuple[type, ...]) -> Any:
    if key not in sec:
        raise ConfigError(f"missing key {where}.{key}")
    value = sec[key]
    # bool is a subclass of int in Python; do not accept true/false as a number.
    if isinstance(value, bool) and kind is not bool:
        raise ConfigError(f"{where}.{key} must be {_kind_name(kind)}, got {value!r}")
    if not isinstance(value, kind):
        raise ConfigError(f"{where}.{key} must be {_kind_name(kind)}, got {value!r}")
    return value


def _kind_name(kind: type | tuple[type, ...]) -> str:
    kinds = kind if isinstance(kind, tuple) else (kind,)
    return " or ".join(k.__name__ for k in kinds)


def _positive(value: float, where: str) -> float:
    if value <= 0:
        raise ConfigError(f"{where} must be greater than 0, got {value!r}")
    return value


def _resolve(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


# --- section parsers ---

def _parse_watch(sec: dict) -> WatchConfig:
    w = "watch"
    exts = _get(sec, w, "extensions", list)
    clean: list[str] = []
    for ext in exts:
        if not isinstance(ext, str) or not ext.startswith(".") or len(ext) < 2:
            raise ConfigError(f'watch.extensions entries must look like ".txt", got {ext!r}')
        clean.append(ext.lower())
    return WatchConfig(
        enabled=_get(sec, w, "enabled", bool),
        source_dir=_resolve(_get(sec, w, "source_dir", str)),
        destination_dir=_resolve(_get(sec, w, "destination_dir", str)),
        extensions=tuple(clean),
        ignore_hidden=_get(sec, w, "ignore_hidden", bool),
        stable_seconds=_positive(float(_get(sec, w, "stable_seconds", (int, float))), "watch.stable_seconds"),
        stable_timeout_seconds=_positive(
            float(_get(sec, w, "stable_timeout_seconds", (int, float))), "watch.stable_timeout_seconds"
        ),
    )


def _parse_agent(sec: dict) -> AgentConfig:
    a = "agent"
    action = _get(sec, a, "english_action", str)
    if action not in ENGLISH_ACTIONS:
        raise ConfigError(f"agent.english_action must be one of {ENGLISH_ACTIONS}, got {action!r}")
    return AgentConfig(
        english_action=action,
        output_suffix=_get(sec, a, "output_suffix", str),
        max_steps=int(_positive(_get(sec, a, "max_steps", int), "agent.max_steps")),
        job_timeout_seconds=_positive(float(_get(sec, a, "job_timeout_seconds", (int, float))), "agent.job_timeout_seconds"),
        chunk_max_chars=int(_positive(_get(sec, a, "chunk_max_chars", int), "agent.chunk_max_chars")),
    )


def _parse_sampling(sec: dict, where: str) -> SamplingProfile:
    num = (int, float)
    return SamplingProfile(
        temperature=float(_get(sec, where, "temperature", num)),
        top_p=float(_get(sec, where, "top_p", num)),
        top_k=_get(sec, where, "top_k", int),
        min_p=float(_get(sec, where, "min_p", num)),
        presence_penalty=float(_get(sec, where, "presence_penalty", num)),
    )


def _parse_gpu_layers(value: Any) -> str:
    # Accept 33 or "33" as well as "auto"/"all"; keep it as text for the command line.
    if isinstance(value, bool):
        raise ConfigError(f"llm.gpu_layers must be \"auto\", \"all\" or a number, got {value!r}")
    text = str(value).strip().lower()
    if text in ("auto", "all") or text.isdigit():
        return text
    raise ConfigError(f"llm.gpu_layers must be \"auto\", \"all\" or a number, got {value!r}")


def _parse_llm(sec: dict) -> LLMConfig:
    l = "llm"
    host = _get(sec, l, "host", str)
    # Hard rule: the model server is never exposed to the network.
    if host not in LOOPBACK_HOSTS:
        raise ConfigError(f"llm.host must be a localhost address {LOOPBACK_HOSTS}, got {host!r}")
    port = _get(sec, l, "port", int)
    if not 1 <= port <= 65535:
        raise ConfigError(f"llm.port must be 1-65535, got {port}")
    extra = _get(sec, l, "extra_args", list)
    if not all(isinstance(x, str) for x in extra):
        raise ConfigError("llm.extra_args must be a list of strings")
    sampling = sec.get("sampling", {})
    return LLMConfig(
        host=host,
        port=port,
        model_path=_resolve(_get(sec, l, "model_path", str)),
        alias=_get(sec, l, "alias", str),
        ctx_size=int(_positive(_get(sec, l, "ctx_size", int), "llm.ctx_size")),
        gpu_layers=_parse_gpu_layers(sec.get("gpu_layers", "auto")),
        parallel=int(_positive(_get(sec, l, "parallel", int), "llm.parallel")),
        extra_args=tuple(extra),
        sampling_agent=_parse_sampling(_section(sampling, "agent"), "llm.sampling.agent"),
        sampling_translate=_parse_sampling(_section(sampling, "translate"), "llm.sampling.translate"),
    )


def _parse_logging(sec: dict) -> LoggingConfig:
    level = _get(sec, "logging", "level", str).upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ConfigError(f"logging.level must be DEBUG, INFO, WARNING, ERROR or CRITICAL, got {level!r}")
    return LoggingConfig(level=level, trace_agent=_get(sec, "logging", "trace_agent", bool))


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    """Read and validate the config file. Raises ConfigError with a readable message."""
    path = Path(path)
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"cannot parse {path}: {e}") from None
    return Config(
        watch=_parse_watch(_section(data, "watch")),
        agent=_parse_agent(_section(data, "agent")),
        llm=_parse_llm(_section(data, "llm")),
        logging=_parse_logging(_section(data, "logging")),
        path=path,
    )


class LiveConfig:
    """Config that reloads when the file changes and keeps the last good version on error.

    The watcher calls `get()` on every file event (SPEC.md section 6.1). A broken edit
    is logged and ignored, so a typo in config.toml never takes the service down.
    """

    def __init__(self, path: Path | str = DEFAULT_CONFIG_PATH) -> None:
        self.path = Path(path)
        self._config = load_config(self.path)  # the first load must succeed
        self._seen = self._read()

    def _read(self) -> bytes | None:
        # Compare contents, not mtime/size: `config set` keeps the length the same, and
        # two quick edits can share a timestamp. The file is tiny, so this is cheap.
        try:
            return self.path.read_bytes()
        except OSError:
            return None

    def changed(self) -> bool:
        return self._read() != self._seen

    def get(self) -> Config:
        """Return the current config, reloading first if the file changed."""
        current = self._read()
        if current != self._seen:
            self._seen = current
            try:
                new = load_config(self.path)
            except ConfigError as e:
                log.error("config reload failed, keeping last good config: %s", e)
            else:
                if new.watch.enabled != self._config.watch.enabled:
                    log.info("watching %s", "enabled" if new.watch.enabled else "disabled")
                self._config = new
        return self._config


# --- `config set`: change one value in place, keeping the file's comments and layout ---

def _value_end(line: str, start: int) -> int:
    """Index just past a TOML value that starts at `start` (stops before a # comment)."""
    quote, depth, i = None, 0, start
    while i < len(line):
        c = line[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
        elif c == "#":
            break
        i += 1
    if quote or depth > 0:
        raise ConfigError("this value spans several lines; edit config/config.toml by hand instead")
    return len(line[:i].rstrip())


def _toml_literal(raw: str) -> str:
    """Turn command-line text into a TOML value: numbers, true/false and lists as written,
    anything else as a quoted string."""
    if raw.strip().lower() in ("true", "false"):
        return raw.strip().lower()
    try:
        tomllib.loads(f"v = {raw}")
        return raw.strip()
    except tomllib.TOMLDecodeError:
        return '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_value(path: Path, dotted: str, raw: str) -> str:
    """Set e.g. watch.enabled to false. Validates the whole file before replacing it.
    Returns the new TOML value text. Raises ConfigError if the key or value is wrong."""
    section, _, key = dotted.rpartition(".")
    if not section:
        raise ConfigError(f"give a full key such as watch.enabled, not {dotted!r}")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    current, index = None, None
    for i, line in enumerate(lines):
        header = re.match(r"\s*\[([^\[\]]+)\]\s*(#.*)?$", line)
        if header:
            current = header.group(1).strip()
        elif current == section and re.match(rf"\s*{re.escape(key)}\s*=", line):
            index = i
            break
    if index is None:
        raise ConfigError(f"unknown config key {dotted!r}")
    line = lines[index]
    after_eq = line.index("=") + 1
    start = after_eq + len(line[after_eq:]) - len(line[after_eq:].lstrip(" \t"))
    end = _value_end(line, start)
    new = _toml_literal(raw)
    rest = line[end:]
    # Keep a trailing comment in the same column when the new value is shorter.
    gap = len(rest) - len(rest.lstrip(" \t"))
    if rest.strip().startswith("#"):
        rest = " " * max(1, gap + (end - start) - len(new)) + rest.lstrip(" \t")
    lines[index] = line[:start] + new + rest
    text = "".join(lines)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        load_config(tmp)  # the whole file must still be valid, or nothing changes
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return new
