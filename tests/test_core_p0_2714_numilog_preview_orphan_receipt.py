"""P0 #2714: Numilog read-only preview matches the writer's orphan-receipt scope.

A placement has no supplier account key; identical editions with orphaned
receipts cannot be treated as uniquely attested. All evidence is synthetic.
"""
from __future__ import annotations

import json

import pytest

from tests.test_core_p0_2714_locked_numilog_preview import (
    evidence, preview, durable_files,
)


def insert_orphan(evidence, *, owner="alice", edition="edition"):
    storage, _root, observed = evidence
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO acquisition_ledger "
            "(acquisition_id,entitlement_id,identity_id,provider_id,"
            "credential_id,account_id,item_id,status,artifact_id,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("PRIVATE-orphan-receipt", "PRIVATE-no-longer-existing-right", owner,
             "numilog", "PRIVATE-second-credential", "PRIVATE-second-account",
             edition, "complete", "sha256:" + observed.sha256, 1, 1),
        )


@pytest.mark.parametrize("owner", ["alice", None, ""])
def test_orphan_same_edition_receipt_blocks_preview_without_writing(evidence, tmp_path, owner):
    storage, root, observation = evidence
    insert_orphan(evidence, owner=owner)
    # The fixture INSERT is deliberate; freeze durable bytes AFTER it.
    before = durable_files(tmp_path)
    result = preview(evidence)
    assert result.status == "blocked"
    assert result.reason == "acquisition_ambiguous_or_missing"
    assert result.read_only and not result.attachment_authorized
    assert durable_files(tmp_path) == before
    assert len(list(storage._connect().execute("SELECT * FROM placements"))) == 0
    assert (root / "test.epub").exists()
    assert "PRIVATE" not in json.dumps(vars(result))
    assert observation.sha256 not in json.dumps(vars(result))


@pytest.mark.parametrize("owner,edition", [
    ("bob", "edition"),
    ("alice", "other-edition"),
])
def test_unrelated_receipt_does_not_block_selected_numilog_purchase(
    evidence, owner, edition,
):
    insert_orphan(evidence, owner=owner, edition=edition)
    result = preview(evidence)
    assert result.status == "reviewable"
    assert result.reason == "fresh_proofs_require_confirmation"
    assert result.read_only
    assert not result.attachment_authorized
