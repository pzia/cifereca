#!/usr/bin/env python3
"""Bounded, offline #5376 mutation witnesses for transport and admission.

All mutated functions live in child pytest processes. Production sources
are never written and no external network requests or accounts are used.
Only the specified behavioral witnesses contribute to the verdict.
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
    target_path: str = "src/cifereca/public_httpx_transport.py"
    target_module: str = "cifereca.public_httpx_transport"
    target_function: str = "_public_socket"
    additional_test_nodes: tuple[str, ...] = ()


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
    Mutation(
        "respect_revoked_delivery_admission",
        "if callback() is True:",
        "if True:",
        "tests/test_core_p0_3867_attested_https_delivery.py::test_admission_revoked_after_redirect_denies_followup_before_get",
        target_path="src/cifereca/core/attested_https_delivery.py",
        target_module="cifereca.core.attested_https_delivery",
        target_function="_admitted",
        additional_test_nodes=(
            "tests/test_core_p0_3867_attested_https_delivery.py::test_rejected_final_publish_after_stream_preserves_no_target",
        ),
    ),
)

_PLUGIN = r'''
"""A one-shot pytest plugin for a *subprocess*, never persisted to the repo."""
import importlib
import inspect
from os import environ

def pytest_configure(config):
    module = importlib.import_module(environ["CIFERECA_MUTATION_MODULE"])
    function = environ["CIFERECA_MUTATION_FUNCTION"]
    source = inspect.getsource(getattr(module, function))
    anchor, replacement = environ["CIFERECA_MUTATION_ANCHOR"], environ["CIFERECA_MUTATION_REPLACEMENT"]
    if source.count(anchor) != 1:
        raise RuntimeError("mutation_anchor_mismatch")
    namespace = dict(module.__dict__)
    exec(compile(source.replace(anchor, replacement), "<mutation-probe>", "exec"), namespace)
    setattr(module, function, namespace[function])
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
        env["CIFERECA_MUTATION_MODULE"] = mutation.target_module
        env["CIFERECA_MUTATION_FUNCTION"] = mutation.target_function
        args.extend(("-p", "cifereca_mutation_plugin"))
    args.append(node)
    return subprocess.run(
        args, cwd=root, env=env, text=True, capture_output=True,
        timeout=timeout, check=False,
    )


def campaign(root: Path = ROOT, *, timeout: int = 40) -> dict:
    report = {
        "schema": "cifereca.security-mutation-probe/1",
        "subject": "Core transport and admission controls",
        "offline": True,
        "probes": [],
    }
    with tempfile.TemporaryDirectory(prefix="cifereca-mutation-") as directory:
        plugin_dir = Path(directory)
        (plugin_dir / "cifereca_mutation_plugin.py").write_text(_PLUGIN, encoding="utf-8")
        for item in MUTATIONS:
            source = (root / item.target_path).read_text(encoding="utf-8")
            if source.count(item.anchor) != 1:
                raise ValueError("missing_or_ambiguous_mutation_anchor:" + item.name)
            for node in (item.test_node, *item.additional_test_nodes):
                baseline = _run_pytest(root, node, module_dir=None,
                                       mutation=None, timeout=timeout)
                # No misleading "killed" verdict when the baseline is red.
                if baseline.returncode != 0:
                    raise RuntimeError(
                        f"baseline_failed:{item.name}:exit_{baseline.returncode}:\n"
                        + (baseline.stdout + baseline.stderr)[-1200:]
                    )
                mutant = _run_pytest(root, node, module_dir=plugin_dir,
                                     mutation=item, timeout=timeout)
                # Exit 1 is a pytest assertion failure. Exit 2+ (collection /
                # internal error) must NOT count as a killed security mutation.
                killed = mutant.returncode == 1 and "FAILED " in mutant.stdout
                report["probes"].append({
                    "mutation": item.name,
                    "subject": item.target_path + "::" + item.target_function,
                    "test_node": node,
                    "baseline_exit": baseline.returncode,
                    "mutant_exit": mutant.returncode,
                    "killed": bool(killed),
                })
    report["distinct_mutation_sites"] = len(MUTATIONS)
    report["witnesses"] = len(report["probes"])
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
