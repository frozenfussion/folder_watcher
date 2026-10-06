"""Tool registry (SPEC.md section 8.2).

Any module in this package that defines `TOOLS: list[Tool]` is picked up
automatically. Adding a file type means adding one reader module; nothing else changes.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from .base import Tool


class Registry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"two tools are named {tool.name!r}")
            self._tools[tool.name] = tool

    @classmethod
    def discover(cls) -> "Registry":
        tools: list[Tool] = []
        for module in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
            mod = importlib.import_module(f"{__name__}.{module.name}")
            tools.extend(getattr(mod, "TOOLS", []))
        return cls(tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def for_extension(self, ext: str) -> list[Tool]:
        """Reader tool(s) that serve this file type."""
        return [t for t in self._tools.values() if ext.lower() in t.extensions]

    def supported_extensions(self) -> set[str]:
        return {ext for t in self._tools.values() for ext in t.extensions}

    def unsupported(self, extensions: tuple[str, ...]) -> list[str]:
        """Configured extensions that have no reader tool (checked at startup)."""
        return [e for e in extensions if e not in self.supported_extensions()]

    def tools_for_job(self, path: Path) -> list[Tool]:
        """The matching reader plus all general tools. A .txt job never sees a PDF reader."""
        return self.for_extension(path.suffix) + [t for t in self._tools.values() if not t.extensions]

    def schemas_for_job(self, path: Path) -> list[dict]:
        return [t.schema() for t in self.tools_for_job(path)]
