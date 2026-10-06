from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from cifereca.plugin_api import capability_name_valid, check_service_contract


@dataclass(frozen=True, slots=True)
class ContractTestResult:
    ok: bool
    code: str = ""
    detail: str = ""


def check_capabilities(capabilities) -> tuple[ContractTestResult, ...]:
    out = []
    for value in capabilities or ():
        name = str(value)
        out.append(ContractTestResult(
            capability_name_valid(name),
            "" if capability_name_valid(name) else "legacy_capability_name",
            name,
        ))
    return tuple(out)


def check_service(category: str, service: object) -> ContractTestResult:
    result = check_service_contract(category, service)
    return ContractTestResult(result.ok, result.reason_code, repr(dict(result.detail or {})))


def check_public_imports(root: str | Path) -> tuple[ContractTestResult, ...]:
    root = Path(root)
    out = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            out.append(ContractTestResult(False, "python_parse_error", f"{path}: {exc}"))
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name == "cifereca" or (name.startswith("cifereca.") and not name.startswith("cifereca.plugin_api")):
                    out.append(ContractTestResult(False, "private_core_import", f"{path}:{getattr(node, 'lineno', 0)}: {name}"))
    if not out:
        return (ContractTestResult(True),)
    return tuple(out)
