"""P0 #5057: fail closed when a PR adds Core-owned provider coupling.

Compare committed Git objects at the PR's exact base and head. No checkout,
provider import, network call, local-library read or persistent write.
Existing debt may decrease, but must not grow per file/provider/kind.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys

from scripts.ci_provider_core_coupling_inventory import inspect_source

_SHA = re.compile(r"[0-9a-f]{40}\\Z")
_SOURCE = "src/cifereca/"


def signal_counts(source: str) -> Counter[tuple[str, str]]:
    """Provider-specific import/comparison multiplicity, ignoring line moves."""
    result = inspect_source(source)
    counts: Counter[tuple[str, str]] = Counter()
    for kind, rows in result.items():
        for row in rows:
            for provider in row["providers"]:
                counts[(kind, provider)] += 1
    return counts


def added_signals(
    old_source: str, new_source: str,
) -> dict[tuple[str, str], int]:
    old, new = signal_counts(old_source), signal_counts(new_source)
    return {key: count - old[key] for key, count in sorted(new.items())
            if count > old[key]}


def _git(root: Path, *args: str, missing_ok: bool = False) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=False,
        timeout=30,
    )
    if result.returncode:
        if missing_ok:
            return b""
        raise ValueError("git_revision_unavailable")
    return result.stdout


def gate(root: Path, base: str, head: str) -> dict[str, object]:
    """Inspect ONLY changed installed Core Python objects, never plugins."""
    if not (_SHA.fullmatch(base) and _SHA.fullmatch(head)):
        raise ValueError("invalid_sha")
    _git(root, "rev-parse", "--verify", base + "^{commit}")
    _git(root, "rev-parse", "--verify", head + "^{commit}")
    changed = _git(
        root, "diff", "--name-status", "--no-renames",
        base + "..." + head, "--", _SOURCE,
    )
    violations: dict[str, dict[str, int]] = {}
    parse_errors: list[str] = []
    scanned = 0
    for record in changed.decode("utf-8").splitlines():
        parts = record.split("\t")
        if len(parts) != 2:
            raise ValueError("invalid_git_diff")
        status, path = parts
        if not (path.startswith(_SOURCE) and path.endswith(".py")
                and ".." not in Path(path).parts):
            continue
        if status == "D":
            continue
        if status not in {"A", "M"}:
            raise ValueError("unsupported_git_change")
        scanned += 1
        # A blank or unreadable committed object is not a zero-signal source.
        new_bytes = _git(root, "show", head + ":" + path)
        old_bytes = (b"" if status == "A"
                     else _git(root, "show", base + ":" + path))
        try:
            delta = added_signals(
                old_bytes.decode("utf-8"), new_bytes.decode("utf-8"),
            )
        except (SyntaxError, UnicodeError):
            parse_errors.append(path)
            continue
        if delta:
            violations[path] = {
                kind + ":" + provider: increase
                for (kind, provider), increase in sorted(delta.items())
            }
    return {
        "schema": "core.provider.coupling-monotone/1",
        "changed_core_python_files": scanned,
        "violations": dict(sorted(violations.items())),
        "parse_errors": sorted(parse_errors),
        "pass": not violations and not parse_errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", required=True)
    args = parser.parse_args(argv)
    try:
        report = gate(args.root, args.base_ref, args.head_ref)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        print(json.dumps({"schema": "core.provider.coupling-monotone/1",
                          "pass": False, "error": "gate_unavailable"}))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
