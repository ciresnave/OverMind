# SPDX-License-Identifier: MIT OR Apache-2.0
"""The static layer of the chokepoint: parse a plug-in module with `ast` and flag
the DIRECT routes to I/O (spec §3, tests item 1).

Flags:
  * an import of subprocess, socket, urllib (any submodule), http.client, ctypes,
    multiprocessing or asyncio.subprocess, in any import form (`import x`,
    `from x import y`, `from http import client`, `from asyncio import subprocess`);
  * os.system, os.popen, os.spawn*, os.exec* (and os.posix_spawn*, os.fork*, the
    same family), by attribute, by `from os import ...`, or by star import;
  * an import of another plug-in's tool module (`other_plugin_modules`);
  * an import of a harness internal: a plug-in imports `harness.plugin_api` only
    (spec §1), never `chokepoint`, `primitives`, `pipeline` or `session*`.

⚠️ What it does NOT catch, by design, and the dynamic layer's job (chokepoint.py):
`import asyncio` + `asyncio.create_subprocess_exec`, `os.posix_spawn` through an
alias this scan does not track, a third-party HTTP library, `open(..., "w")`,
`pathlib` writes, `__import__("subprocess")`, I/O done by a helper module outside
the plug-in package. A scan that passes a module says "no direct route", never
"no I/O".
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

BANNED_MODULES = ("subprocess", "socket", "urllib", "http.client", "ctypes", "multiprocessing",
                  "asyncio.subprocess")
_OS_EXACT = frozenset({"system", "popen", "posix_spawn", "posix_spawnp", "fork", "forkpty"})
_OS_PREFIXES = ("spawn", "exec")
_HARNESS_PUBLIC = frozenset({"overmind.harness.plugin_api"})


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    kind: str
    detail: str


def _banned_module(name: str) -> str | None:
    for banned in BANNED_MODULES:
        if name == banned or name.startswith(banned + "."):
            return banned
    return None


def _banned_os(name: str) -> bool:
    return name in _OS_EXACT or name.startswith(_OS_PREFIXES)


def _resolve(module: str | None, level: int, package: str) -> str:
    """The dotted name a (possibly relative) `from` import refers to."""
    if level == 0:
        return module or ""
    base = package.split(".") if package else []
    base = base[:len(base) - (level - 1)] if level > 1 else base
    return ".".join(base + ([module] if module else []))


def scan_source(source: str, filename: str = "<plug-in>", *, package: str = "",
                other_plugin_modules: Iterable[str] = ()) -> list[Finding]:
    """`package` is the dotted package the module lives in (to resolve relative
    imports); `other_plugin_modules` are the dotted names of OTHER plug-ins' modules."""
    others = frozenset(other_plugin_modules)
    findings: list[Finding] = []
    os_names: set[str] = set()

    def add(node: ast.AST, kind: str, detail: str) -> None:
        findings.append(Finding(filename, getattr(node, "lineno", 0), kind, detail))

    def check_module(node: ast.AST, dotted: str) -> None:
        banned = _banned_module(dotted)
        if banned:
            add(node, "banned-import", f"{dotted} ({banned} is I/O)")
        if dotted in others or any(dotted.startswith(o + ".") for o in others):
            add(node, "other-plugin-import", dotted)
        public = any(dotted == p or dotted.startswith(p + ".") for p in _HARNESS_PUBLIC)
        if dotted.startswith("overmind.harness.") and not public:
            add(node, "harness-internal-import", dotted)

    tree = ast.parse(source, filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                check_module(node, alias.name)
                if alias.name == "os":
                    os_names.add(alias.asname or "os")
        elif isinstance(node, ast.ImportFrom):
            dotted = _resolve(node.module, node.level, package)
            check_module(node, dotted)
            for alias in node.names:
                # `from http import client`, `from asyncio import subprocess`, `from pkg import tool_module`
                if alias.name != "*":
                    check_module(node, f"{dotted}.{alias.name}" if dotted else alias.name)
                if dotted == "os":
                    if alias.name == "*":
                        add(node, "banned-os", "from os import * (cannot tell what it brings in)")
                    elif _banned_os(alias.name):
                        add(node, "banned-os", f"os.{alias.name}")

    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in os_names and _banned_os(node.attr)):
            add(node, "banned-os", f"os.{node.attr}")
    # a single statement can raise one finding per route; report each once, in order
    unique = {(f.line, f.kind, f.detail): f for f in findings}
    return sorted(unique.values(), key=lambda f: (f.line, f.kind, f.detail))


def scan_file(path: str | Path, *, package: str = "",
              other_plugin_modules: Iterable[str] = ()) -> list[Finding]:
    p = Path(path)
    return scan_source(p.read_text(encoding="utf-8"), str(p), package=package,
                       other_plugin_modules=other_plugin_modules)
