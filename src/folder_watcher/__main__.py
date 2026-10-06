"""Command line: python -m folder_watcher <command> (SPEC.md section 12)."""

from __future__ import annotations

import argparse
import sys
from dataclasses import fields, is_dataclass

from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config


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
    print("config set is not built yet (Milestone 5).", file=sys.stderr)
    return 2


def cmd_llm_server(args: argparse.Namespace) -> int:
    from .launch_llm import main as launch

    return launch()


def not_yet(milestone: str):
    def run(args: argparse.Namespace) -> int:
        print(f"'{args.command}' is not built yet ({milestone}).", file=sys.stderr)
        return 2
    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="folder_watcher")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("watch", help="run the watcher").set_defaults(func=not_yet("Milestone 5"))
    sub.add_parser("llm-server", help="start llama-server from config").set_defaults(func=cmd_llm_server)
    sub.add_parser("check", help="preflight checks").set_defaults(func=not_yet("Milestone 6"))

    p = sub.add_parser("config", help="read or change a config value")
    p.add_argument("action", choices=["get", "set"])
    p.add_argument("key", help="dotted key, e.g. watch.enabled")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
