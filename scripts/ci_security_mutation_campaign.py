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
import re
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
    Mutation(
        "verify_published_candidate_sha",
        "if checked.digest() != digest.digest():",
        "if False:",
        "tests/test_core_p0_3867_attested_https_delivery.py::test_same_inode_overwrite_before_link_never_publishes_stale_digest",
        target_path="src/cifereca/core/attested_https_delivery.py",
        target_module="cifereca.core.attested_https_delivery",
        target_function="download_attested_https",
    ),
)

# Owner's bounded closure matrix: exactly one witness per invariant.
# Preserve the original public-transport campaign independently.
CLOSURE_MUTATIONS = (
    Mutation(
        "exact_inventory_credential",
        'and getattr_static(session, "credential_id", None) == scope.credential_id',
        "and True",
        "tests/test_core_p1_3119_loan_inventory_contract.py::test_wrong_session_credential_never_starts_loan_read",
        target_path="src/cifereca/plugin_api/loan_inventory.py",
        target_module="cifereca.plugin_api.loan_inventory",
        target_function="read_loan_inventory",
    ),
    Mutation(
        MUTATIONS[2].name, MUTATIONS[2].anchor, MUTATIONS[2].replacement,
        MUTATIONS[2].test_node, target_path=MUTATIONS[2].target_path,
        target_module=MUTATIONS[2].target_module, target_function=MUTATIONS[2].target_function,
    ),
    Mutation(
        "one_shot_confirmed_stage",
        "if self._used:",
        "if False:",
        "tests/test_core_p0_2055_bnf_explicit_stage.py::test_confirmed_headless_loan_stage_survives_navigation_once",
        target_path="src/cifereca/core/loan_action_scope.py",
        target_module="cifereca.core.loan_action_scope",
        target_function="_ConfirmedStageAttempt.claim",
    ),
    MUTATIONS[3],
    Mutation(
        "retired_legacy_writer_fence",
        ").fetchone() is not None:\n                return\n            # Without a typed source",
        ").fetchone() is not None:\n                pass\n            # Without a typed source",
        "tests/test_core_p0_5975_w1_all_provider_typed_source_cutover.py::test_two_storage_instances_observe_committed_typed_source",
        target_path="src/cifereca/storage.py",
        target_module="cifereca.storage",
        target_function="Storage.backup_put",
    ),
    Mutation(
        "replay_deleted_transitive_dependency",
        'if any(\n        edge["consumer"] in reachable and edge["target_module"] in modules',
        'if False and any(\n        edge["consumer"] in reachable and edge["target_module"] in modules',
        "tests/test_ci_test_core_consumer_graph.py::test_deleted_transitive_root_fixture_still_requires_plugin_replay",
        target_path="scripts/ci_test_core_consumer_graph.py",
        target_module="scripts.ci_test_core_consumer_graph",
        target_function="plugin_replay_required",
    ),
)

_PLUGIN = r'''
"""A one-shot pytest plugin for a *subprocess*, never persisted to the repo."""
import importlib
import inspect
import textwrap
from os import environ

def pytest_configure(config):
    module = importlib.import_module(environ["CIFERECA_MUTATION_MODULE"])
    parts = environ["CIFERECA_MUTATION_FUNCTION"].split(".")
    owner = module
    for part in parts[:-1]:
        owner = getattr(owner, part)
    function = parts[-1]
    source = inspect.getsource(getattr(owner, function))
    anchor, replacement = environ["CIFERECA_MUTATION_ANCHOR"], environ["CIFERECA_MUTATION_REPLACEMENT"]
    if source.count(anchor) != 1:
        raise RuntimeError("mutation_anchor_mismatch")
    namespace = dict(module.__dict__)
    exec(compile(textwrap.dedent(source.replace(anchor, replacement)), "<mutation-probe>", "exec"), namespace)
    setattr(owner, function, namespace[function])
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


def campaign(root: Path = ROOT, *, timeout: int = 40,
             mutations: tuple[Mutation, ...] = MUTATIONS) -> dict:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True,
            capture_output=True, check=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        sha = None
    report = {
        "source_sha": sha,
        "schema": "cifereca.security-mutation-probe/1",
        "subject": ("Owner closure matrix: six invariants" if mutations is CLOSURE_MUTATIONS
                    else "Core public peers, consent and candidate integrity"),
        "offline": True,
        "probes": [],
    }
    with tempfile.TemporaryDirectory(prefix="cifereca-mutation-") as directory:
        plugin_dir = Path(directory)
        (plugin_dir / "cifereca_mutation_plugin.py").write_text(_PLUGIN, encoding="utf-8")
        for item in mutations:
            source = (root / item.target_path).read_text(encoding="utf-8")
            if source.count(item.anchor) != 1:
                raise ValueError("missing_or_ambiguous_mutation_anchor:" + item.name)
            for node in (item.test_node, *item.additional_test_nodes):
                baseline = _run_pytest(root, node, module_dir=None,
                                       mutation=None, timeout=timeout)
                # No misleading "killed" verdict when the baseline is red.
                if baseline.returncode != 0 or not re.search(r"\b[1-9]\d* passed\b", baseline.stdout):
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
    report["distinct_mutation_sites"] = len(mutations)
    report["witnesses"] = len(report["probes"])
    report["passed"] = bool(report["probes"]) and all(
        item["killed"] for item in report["probes"]
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=40)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--closure", action="store_true", help="Six owner closure invariants")
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 120:
        parser.error("timeout must be between 1 and 120 seconds")
    try:
        result = campaign(timeout=args.timeout, mutations=CLOSURE_MUTATIONS if args.closure else MUTATIONS)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"security_mutation_probe_failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
