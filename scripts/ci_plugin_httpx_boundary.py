#!/usr/bin/env python3
"""Reject autonomous HTTP stacks in production plugins (#3315).

Static import/constructor check, not a sandbox or a universal network detector.
Tests are excluded; Browser Recorder is the one documented autonomous package.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTONOMOUS_PACKAGES = {"browser-recorder": "docs/HTTP-TRACE.md"}
_STACKS = ("urllib.request", "urllib2", "requests", "aiohttp", "urllib3", "http.client")
_HTTPX_CONSTRUCTORS = {"Client", "AsyncClient", "HTTPTransport", "AsyncHTTPTransport",
                       "BaseTransport", "AsyncBaseTransport"}
_HTTPX_SHORTCUTS = {"get", "post", "put", "delete", "patch", "head", "options", "request", "stream"}
_CORE_PRIMITIVES = {"TraceHTTPClient", "TraceAsyncHTTPClient", "TraceHTTPTransport", "TraceAsyncHTTPTransport"}


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    target: str


def _stack(name):
    return any(name == prefix or name.startswith(prefix + ".") for prefix in _STACKS)


def _qualified(node, aliases):
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        prefix = _qualified(node.value, aliases)
        return prefix + "." + node.attr if prefix else ""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "getattr" and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
        prefix = _qualified(node.args[0], aliases)
        return prefix + "." + node.args[1].value if prefix else ""
    return ""


def _constructor(name):
    leaf = name.rsplit(".", 1)[-1]
    return (name.startswith("httpx.") and leaf in (_HTTPX_CONSTRUCTORS | _HTTPX_SHORTCUTS)
            or name.startswith("cifereca.") and leaf in _CORE_PRIMITIVES
            or _stack(name))


def violations_in_source(source, path):
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [Violation(path, exc.lineno or 0, "unparseable_python")]
    aliases, findings = {}, []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".")[0]] = item.name if item.asname else item.name.split(".")[0]
                if _stack(item.name):
                    findings.append(Violation(path, node.lineno, item.name))
        elif isinstance(node, ast.ImportFrom):
            for item in node.names:
                qualified = ".".join(part for part in (node.module, item.name) if part)
                aliases[item.asname or item.name] = qualified
                if _stack(qualified):
                    findings.append(Violation(path, node.lineno, qualified))
    # Follow simple imported-constructor aliases, including literal getattr.
    bindings = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)]
    for _ in range(len(bindings) + 1):
        changed = False
        for node in bindings:
            value = _qualified(node.value, aliases)
            if not _constructor(value):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and aliases.get(target.id) != value:
                    aliases[target.id] = value
                    changed = True
        if not changed:
            break
    for node in ast.walk(tree):
        targets = [node.func] if isinstance(node, ast.Call) else node.bases if isinstance(node, ast.ClassDef) else ()
        for target in targets:
            name = _qualified(target, aliases)
            if _constructor(name):
                findings.append(Violation(path, node.lineno, name))
    return sorted(set(findings), key=lambda row: (str(row.path), row.line, row.target))


def check_repository(root):
    root = Path(root)
    if not root.is_dir():
        raise ValueError("Plugin source root is unavailable")
    findings, checked, excepted = [], 0, 0
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part in {"tests", "__pycache__", ".venv", "build", "dist"} or part.startswith(".")
               for part in relative.parts):
            continue
        if relative.parts[0] in AUTONOMOUS_PACKAGES:
            excepted += 1
            continue
        checked += 1
        if path.is_symlink():
            findings.append(Violation(relative, 0, "uncheckable_symlink"))
            continue
        findings.extend(violations_in_source(path.read_text(encoding="utf-8"), relative))
    if checked + excepted == 0:
        raise ValueError("No production plugin sources found")
    return findings, checked, excepted


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "src/plugins")
    args = parser.parse_args(argv)
    try:
        findings, checked, excepted = check_repository(args.root)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    for row in findings:
        print(f"{row.path}:{row.line}: {row.target}: use the Core HTTPX_TRANSPORT service")
    if findings:
        return 1
    print(f"HTTPX boundary OK: {checked} production files; {excepted} Browser Recorder files excepted (docs/HTTP-TRACE.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
