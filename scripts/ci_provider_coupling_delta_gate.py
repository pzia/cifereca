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

from scripts.ci_provider_core_coupling_inventory import DEFAULT_PROVIDERS, inspect_source

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SOURCE = "src/cifereca/"


def signal_counts(source: str, providers: tuple[str, ...] = DEFAULT_PROVIDERS) -> Counter[tuple[str, str]]:
    """Provider-specific import/comparison multiplicity, ignoring line moves."""
    result = inspect_source(source, providers)
    counts: Counter[tuple[str, str]] = Counter()
    for kind, rows in result.items():
        for row in rows:
            for provider in row["providers"]:
                counts[(kind, provider)] += 1
    return counts


def added_signals(
    old_source: str, new_source: str,
    providers: tuple[str, ...] = DEFAULT_PROVIDERS,
) -> dict[tuple[str, str], int]:
    old, new = signal_counts(old_source, providers), signal_counts(new_source, providers)
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


def _manifest_providers(root: Path, revision: str) -> set[str]:
    """Read provider IDs and import roots from committed manifests, never plugins.

    The two revisions are unioned so a manifest rename/removal cannot hide
    an added Core dependency in the same PR. Invalid manifests fail closed.
    """
    paths = _git(root, "ls-tree", "-r", "--name-only", revision, "--", "src/plugins/")
    names: set[str] = set()
    for path in paths.decode("utf-8").splitlines():
        if not path.endswith("/cifereca-plugin.json"):
            continue
        raw = json.loads(_git(root, "show", revision + ":" + path))
        if not isinstance(raw, dict) or not isinstance(raw.get("plugins"), list):
            raise ValueError("invalid_plugin_manifest")
        package = raw.get("package")
        if not isinstance(package, dict) or not isinstance(package.get("id"), str):
            raise ValueError("invalid_plugin_manifest")
        for descriptor in raw["plugins"]:
            if not isinstance(descriptor, dict):
                raise ValueError("invalid_plugin_manifest")
            if not str(descriptor.get("type", "")).startswith("provider."):
                continue
            identifier = descriptor.get("id")
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError("invalid_plugin_manifest")
            names.add(identifier.casefold())
            # Include the provider's opaque suffix (e.g. library.example)
            # and exact Python import root declared by its entrypoint.
            if identifier.startswith("provider.") and len(identifier) > 9:
                names.add(identifier[9:].casefold())
            entrypoint = descriptor.get("entrypoint")
            if isinstance(entrypoint, str) and ":" in entrypoint:
                module = entrypoint.split(":", 1)[0]
                if module and all(part.isidentifier() for part in module.split(".")):
                    names.add(module.casefold().split(".")[0])
        # Package-level imports are also implementation dependencies.
        package_id = package["id"].casefold()
        names.add(package_id)
        if ".provider." in package_id:
            names.add(package_id.split(".provider.", 1)[1])
        names.add(path.split("/")[2].casefold())
    return names


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
    providers = tuple(sorted(set(DEFAULT_PROVIDERS) | _manifest_providers(root, base) | _manifest_providers(root, head)))
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
                old_bytes.decode("utf-8"), new_bytes.decode("utf-8"), providers,
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
        "discovered_provider_ids": len(providers),
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
