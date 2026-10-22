"""#2796: repeatability of offline evidence vs changed proofs or file cohorts.

Synthetic keys only. Never emit source identifiers, hashes or file paths in
public audit summaries. These tests do not explain the user's live 29-link
difference without comparing actual local evidence on the same machine.
"""
from __future__ import annotations

from copy import deepcopy
import json

from cifereca.core.library_inventory import FileObservation, Inventory
from cifereca.core.library_reconciliation import EvidenceSnapshot, reconcile


def _fixture(count=600):
    inventory = Inventory()
    tables = {name: [] for name in (
        "artifacts", "entitlements", "acquisition_ledger", "placements",
    )}
    for index in range(count):
        digest = f"{index + 1:064x}"
        path = f"/PRIVATE_LIBRARY/synthetic-{index:04d}.epub"
        artifact_id = "sha256:" + digest
        item_id = f"edition-{index:04d}"
        right = {
            "entitlement_id": f"entitlement-{index:04d}",
            "identity_id": "alice",
            "provider_id": "numilog",
            "credential_id": "credential",
            "account_id": "account",
            "item_id": item_id,
            "state": "active",
            "entitlement_type": "owned",
            "attested_source": "numilog.authenticated_library",
        }
        inventory.files.append(FileObservation(
            "alice", path, 123, "epub", digest, "ok",
            structure_validation="epub_structure_crc_ok",
        ))
        tables["artifacts"].append({
            "artifact_id": artifact_id, "sha256": digest,
            "size": 123, "media_format": "epub",
        })
        tables["entitlements"].append(right)
        tables["acquisition_ledger"].append({
            **right, "acquisition_id": f"acquisition-{index:04d}",
            "artifact_id": artifact_id, "status": "complete",
        })
        tables["placements"].append({
            "placement_id": f"placement-{index:04d}",
            "identity_id": "alice",
            "provider_id": "numilog",
            "source_item_id": item_id,
            "artifact_id": artifact_id,
            "path": path,
            "state": "present",
        })
    return inventory, tables


def _run(inventory, tables):
    return reconcile(inventory, EvidenceSnapshot(tables), seconds=30)


def _counts(report):
    return report.redacted()["counts"]


def test_same_six_hundred_files_and_snapshot_remain_identical_across_runs():
    inventory, tables = _fixture()
    original = deepcopy(tables)
    first = _run(inventory, tables)
    second = _run(inventory, tables)
    assert first.reasons == second.reasons == set()
    assert first.files == second.files
    assert first.edges == second.edges
    assert first.unverified_rights == second.unverified_rights == []
    assert _counts(first) == _counts(second)
    assert _counts(first)["confirmed_link"] == 600
    assert tables == original  # evidence is never rewritten or normalized
    public = json.dumps(first.redacted())
    assert "PRIVATE_LIBRARY" not in public
    assert inventory.files[0].sha256 not in public
    assert "entitlement-0000" not in public


def test_twenty_nine_missing_path_proofs_change_exactly_twenty_nine_links():
    inventory, tables = _fixture()
    baseline = _run(inventory, tables)
    altered = deepcopy(tables)
    for placement in altered["placements"][:29]:
        placement["path"] += ".historical"
    after = _run(inventory, altered)
    assert _counts(baseline)["confirmed_link"] == 600
    assert _counts(after)["confirmed_link"] == 571
    assert _counts(after)["local_file_without_attested_right"] == 29
    assert len(after.unverified_rights) == 29
    assert "comparison_budget" not in after.reasons
    assert _run(inventory, altered).redacted() == after.redacted()
    assert tables["placements"][0]["path"] == inventory.files[0].path


def test_same_file_count_with_changed_cohort_can_mimic_missing_path_proof_counts():
    inventory, tables = _fixture()
    replaced = deepcopy(inventory)
    for index in range(29):
        observation = replaced.files[index]
        replaced.files[index] = FileObservation(
            observation.identity_id, observation.path + ".replacement",
            observation.size, observation.media_format, observation.sha256,
            observation.validation, observation.metadata,
            observation.fingerprint, observation.structure_validation,
        )
    changed_cohort = _run(replaced, tables)
    missing_paths = deepcopy(tables)
    for row in missing_paths["placements"][:29]:
        row["path"] += ".historical"
    changed_snapshot = _run(inventory, missing_paths)
    assert len(replaced.files) == len(inventory.files) == 600
    assert _counts(changed_cohort) == _counts(changed_snapshot)
    assert len(changed_cohort.unverified_rights) == 29
    # These two distinct causes are observationally indistinguishable from
    # *aggregate* counts: only a scoped private cohort/snapshot comparison
    # can explain a production drift, not a guessed timeout or missing book.
