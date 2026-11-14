#!/usr/bin/env python3
"""Bounded, offline mutation campaign for the Core public HTTPX peer boundary.

#5376: prove that real behavioral transport tests *kill* two fail-open
mutations. No production file is modified; mutations load in child pytest
processes from a temporary plugin. This is a targeted security probe, not
a global mutation score or a live-network test.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/cifereca/public_httpx_transport.py"

@dataclass(frozen=True)
class Mutation:
    name: str
    anchor: str
    replacement: str
    test_node: str


MUTATIONS = (
    Mutation(
        "reject_nonpublic_dns",
        "if not permitted:",
        "if False:",
        "tests/test_public_httpx_transport.py::test_dns_private_targets_fail_before_a_socket_is_created",
    ),
    Mutation(
        "validate_connected_peer",
        "if not peer_ip.is_global or peer_ip != expected_ip:",
        "if False:",
        "tests/test_public_httpx_transport.py::test_dns_rebinding_cannot_change_the_actual_peer",
    ),
)

_PLUGIN = r'''
"""A one-shot pytest plugin for a *subprocess*, never persisted to the repo."""
import inspect
import cifereca.public_httpx_transport as transport

def pytest_configure(config):
    from os import environ
    source = inspect.getsource(transport._public_socket)
    anchor, replacement = environ["CIFERECA_MUTATION_ANCHOR"], environ["CIFERECA_MUTATION_REPLACEMENT"]
    if source.count(anchor) != 1:
        raise RuntimeError("mutation_anchor_mismatch")
    namespace = dict(transport.__dict__)
    exec(compile(source.replace(anchor, replacement), "<mutation-probe>", "exec"), namespace)
    transport._public_socket = namespace["_public_socket"]
'''


def _run_pytest(root: Path, node: str, *, module_dir: Path | None,
                mutation: Mutation | None, timeout: int) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    parts = [str(root / "src")]
    if module_dir is not None:
        parts.insert(0, str(module_dir))
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    args = [sys.executable, "-m", "pytest", "-q", "--tb=short"]
    if mutation is not None:
        env["CIFERECA_MUTATION_ANCHOR"] = mutation.anchor
        env["CIFERECA_MUTATION_REPLACEMENT"] = mutation.replacement
        args.extend(("-p", "cifereca_mutation_plugin"))
    args.append(node)
    return subprocess.run(
        args, cwd=root, env=env, text=True, capture_output=True,
        timeout=timeout, check=False,
    )


def campaign(root: Path = ROOT, *, timeout: int = 40) -> dict:
    source = (root / TARGET.relative_to(ROOT)).read_text(encoding="utf-8")
    report = {
        "schema": "cifereca.security-mutation-probe/1",
        "subject": "src/cifereca/public_httpx_transport.py::_public_socket",
        "offline": True,
        "probes": [],
    }
    with tempfile.TemporaryDirectory(prefix="cifereca-mutation-") as directory:
        plugin_dir = Path(directory)
        (plugin_dir / "cifereca_mutation_plugin.py").write_text(_PLUGIN, encoding="utf-8")
        for item in MUTATIONS:
            if source.count(item.anchor) != 1:
                raise ValueError("missing_or_ambiguous_mutation_anchor:" + item.name)
            baseline = _run_pytest(root, item.test_node, module_dir=None,
                                   mutation=None, timeout=timeout)
            # No misleading "killed" verdict when the original test is red.
            if baseline.returncode != 0:
                raise RuntimeError(
                    f"baseline_failed:{item.name}:exit_{baseline.returncode}:\n"
                    + (baseline.stdout + baseline.stderr)[-1200:]
                )
            mutant = _run_pytest(root, item.test_node, module_dir=plugin_dir,
                                 mutation=item, timeout=timeout)
            # Exit 1 is a pytest assertion failure. Exit 2+ (collection /
            # internal error) must NOT count as a killed security mutation.
            killed = mutant.returncode == 1 and "FAILED " in mutant.stdout
            report["probes"].append({
                "mutation": item.name,
                "test_node": item.test_node,
                "baseline_exit": baseline.returncode,
                "mutant_exit": mutant.returncode,
                "killed": bool(killed),
            })
    report["passed"] = bool(report["probes"]) and all(
        item["killed"] for item in report["probes"]
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=40)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 120:
        parser.error("timeout must be between 1 and 120 seconds")
    try:
        result = campaign(timeout=args.timeout)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"security_mutation_probe_failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
