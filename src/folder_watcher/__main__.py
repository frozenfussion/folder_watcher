"""Command line: python -m folder_watcher <command> (SPEC.md section 12)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from dataclasses import fields, is_dataclass

from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config, set_value
from .guards import GuardError, resolve_job_file
from .logging_setup import setup_logging


def _lookup(cfg: object, dotted: str) -> object:
    # "llm.sampling.agent.top_k" -> cfg.llm.sampling_agent.top_k
    dotted = dotted.replace("sampling.", "sampling_")
    value = cfg
    for part in dotted.split("."):
        if not is_dataclass(value) or part not in {f.name for f in fields(value)}:
            raise KeyError(dotted)
        value = getattr(value, part)
    return value


def cmd_config(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1
    if args.action == "get":
        try:
            print(_lookup(cfg, args.key))
        except KeyError:
            print(f"unknown config key: {args.key}", file=sys.stderr)
            return 1
        return 0
    if args.value is None:
        print("config set needs a value, for example:  config set watch.enabled false", file=sys.stderr)
        return 1
    try:
        old = _lookup(cfg, args.key)
        set_value(Path(args.config), args.key, args.value)
        new = _lookup(load_config(args.config), args.key)
    except KeyError:
        print(f"not changed: unknown config key {args.key!r}", file=sys.stderr)
        return 1
    except ConfigError as e:
        print(f"not changed: {e}", file=sys.stderr)
        return 1
    print(f"{args.key}: {old} -> {new}")
    section = args.key.split(".")[0]
    if section in ("watch", "agent"):
        print("A running watcher picks this up within a second; no restart needed.")
    else:
        print(f"[{section}] settings are not live: restart the services for this to take effect.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run the agent on one file by hand, without the watcher."""
    from .agent import run_job
    from .llm_client import LLMClient
    from .tools import Registry

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1
    setup_logging(cfg.logging.level)
    path = Path(args.file)
    try:
        resolve_job_file(cfg.watch.source_dir, path)
    except GuardError as e:
        print(f"refused: {e}", file=sys.stderr)
        if not path.is_symlink():
            print(f"The agent only reads files in source/. Copy it there first, for example:  "
                  f"cp {args.file} source/", file=sys.stderr)
        return 1
    registry = Registry.discover()
    for ext in registry.unsupported(cfg.watch.extensions):
        logging.error("watch.extensions lists %s but no reader tool supports it", ext)
    llm = LLMClient(cfg.llm, timeout=cfg.agent.job_timeout_seconds)
    if not llm.wait_until_ready(60, on_wait=lambda: logging.info("waiting for the model server at %s ...",
                                                                  cfg.llm.base_url)):
        print(f"The model server at {cfg.llm.base_url} is not answering.\n"
              "Start it in another terminal with:  .venv/bin/python -m folder_watcher llm-server", file=sys.stderr)
        return 1
    result = run_job(path, cfg, registry, llm, max_steps=args.max_steps)
    return 1 if result.status == "failed" else 0


def cmd_watch(args: argparse.Namespace) -> int:
    from .watcher import run_watch

    return run_watch(Path(args.config))


def cmd_llm_wait(args: argparse.Namespace) -> int:
    """Wait until the model server answers /health (used by the systemd unit's ExecStartPost)."""
    from .llm_client import LLMClient

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1
    print(f"waiting for the model to load at {cfg.llm.base_url} (up to {args.timeout:.0f}s)", flush=True)
    client = LLMClient(cfg.llm)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if client.healthy():
            print("model server is healthy", flush=True)
            return 0
        if args.main_pid and not _alive(args.main_pid):
            # llama-server already exited (e.g. model file missing): fail now, not after the timeout.
            print("the model server exited while loading; see the lines above", file=sys.stderr)
            return 1
        time.sleep(1)
    print(f"model server did not become healthy within {args.timeout:.0f}s", file=sys.stderr)
    return 1


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cmd_check(args: argparse.Namespace) -> int:
    from .checks import run_checks

    return run_checks(Path(args.config))


def cmd_llm_server(args: argparse.Namespace) -> int:
    from .launch_llm import main as launch

    return launch()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="folder_watcher")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("watch", help="watch source/ and process new files (Ctrl+C to stop)").set_defaults(func=cmd_watch)
    sub.add_parser("llm-server", help="start llama-server from config").set_defaults(func=cmd_llm_server)
    sub.add_parser("check", help="preflight checks with plain-language fixes").set_defaults(func=cmd_check)
    p = sub.add_parser("llm-wait", help="wait until the model server is healthy (used by systemd)")
    p.add_argument("--timeout", type=float, default=280)
    p.add_argument("--main-pid", type=int, default=0, help="stop waiting if this process exits")
    p.set_defaults(func=cmd_llm_wait)

    p = sub.add_parser("run", help="run the agent on one file in source/ (no watcher)")
    p.add_argument("file", help="path of a file inside source/")
    p.add_argument("--max-steps", type=int, default=None, help="override agent.max_steps for this run")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("config", help="read or change a config value")
    p.add_argument("action", choices=["get", "set"])
    p.add_argument("key", help="dotted key, e.g. watch.enabled")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
