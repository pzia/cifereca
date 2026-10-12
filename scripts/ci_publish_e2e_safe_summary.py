"""Publish a minimal, fail-closed GTK E2E summary for GitHub Actions.

Never export scenario descriptions, error messages, screenshots, logs, traces,
report paths, HTTP URLs, or unrestricted JSON from e2e-artifacts/.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
from typing import Any

_SCHEMA = "cifereca-e2e-public/1"
_MAX_JSON_BYTES = 128 * 1024
_MAX_ROWS = 100


def scenario_names(source: str) -> frozenset[str]:
    """Only extract literal scenario IDs; never execute E2E Python modules."""
    module = ast.parse(source)
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "SCENARIOS"
                   for target in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            break
        names: set[str] = set()
        for key in node.value.keys:
            if not isinstance(key, ast.Constant) or type(key.value) is not str:
                return frozenset()
            value = key.value
            if not value.isascii() or not value or len(value) > 64 or any(
                char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in value
            ):
                return frozenset()
            names.add(value)
        return frozenset(names)
    return frozenset()


def _fixed(status: str) -> dict[str, Any]:
    return {"schema": _SCHEMA, "status": status,
            "total": 0, "passed": 0, "failed": 0, "scenarios": []}


def summarize(raw: bytes | None, allowed: frozenset[str]) -> dict[str, Any]:
    """Reject the entire input if any field needed for proof is untrusted."""
    if raw is None:
        return _fixed("not_produced")
    if not allowed or len(raw) > _MAX_JSON_BYTES:
        return _fixed("rejected")
    try:
        rows = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        return _fixed("rejected")
    if type(rows) is not list or len(rows) > _MAX_ROWS:
        return _fixed("rejected")
    safe = []
    seen: set[str] = set()
    for row in rows:
        if type(row) is not dict:
            return _fixed("rejected")
        name = row.get("scenario")
        code = row.get("returncode")
        if (type(name) is not str or name not in allowed or name in seen
                or type(code) is not int or not 0 <= code <= 255):
            return _fixed("rejected")
        seen.add(name)
        safe.append({"id": name, "outcome": "PASS" if code == 0 else "FAIL"})
    passed = sum(row["outcome"] == "PASS" for row in safe)
    return {"schema": _SCHEMA, "status": "summarized", "total": len(safe),
            "passed": passed, "failed": len(safe) - passed, "scenarios": safe}


def publish(source: Path, input_path: Path, output_path: Path) -> dict[str, Any]:
    """No untrusted exception or path is printed to the Actions log."""
    try:
        allowed = scenario_names(source.read_text(encoding="utf-8"))
        if input_path.exists():
            with input_path.open("rb") as stream:
                raw = stream.read(_MAX_JSON_BYTES + 1)
        else:
            raw = None
        public = summarize(raw, allowed)
    except (OSError, SyntaxError, UnicodeError, ValueError):
        public = _fixed("rejected")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(public, ensure_ascii=True, indent=2) + "\n",
                           encoding="utf-8")
    return public


def main() -> int:
    # Fixed repository-local paths, not values accepted from an E2E scenario.
    root = Path(__file__).resolve().parents[1]
    public = publish(root / "e2e/run.py",
                     root / "e2e-artifacts/summary.json",
                     root / "e2e-public/summary.json")
    print("E2E public summary status: " + public["status"])
    return 0 if public["status"] != "rejected" else 1


if __name__ == "__main__":
    sys.exit(main())
