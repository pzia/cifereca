"""P0 #2714: orphan acquisitions must not transfer an edition to another buyer.

A placement stores provider/identity/edition, not account or credential.
An old receipt without its original entitlement is still competing ownership
evidence. All cases use local synthetic SQLite and an inert EPUB.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.numilog_manual_link_transaction import (
    ManualAttachmentRejected, reconcile_numilog_manual, undo_numilog_manual,
)
from tests.test_core_p0_2714_numilog_manual_transaction import (
    rig, _rows, _stage,
)


def _orphan_receipt(service, params, *, identity="default", edition="private-item"):
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "INSERT INTO acquisition_ledger "
            "(acquisition_id,entitlement_id,identity_id,provider_id,credential_id,"
            "account_id,item_id,status,artifact_id,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("PRIVATE-orphan-receipt", "PRIVATE-removed-entitlement", identity,
             "numilog", "PRIVATE-second-login", "PRIVATE-second-buyer", edition,
             "complete", "sha256:" + params["observation"].sha256, 1, 1),
        )


@pytest.mark.parametrize("owner", ["default", None, ""])
def test_orphan_receipt_same_edition_blocks_attested_preview_without_mutation(rig, owner):
    service, book, options = rig
    before_bytes = book.read_bytes()
    _orphan_receipt(service, options, identity=owner)
    baseline = {name: _rows(service, name) for name in (
        "placements", "artifacts", "entitlements", "acquisition_ledger",
        "catalog_entries",
    )}
    with pytest.raises(
        ManualAttachmentRejected, match="^acquisition_ambiguous_or_missing$",
    ) as exc:
        reconcile_numilog_manual(service, **options)
    assert "PRIVATE" not in str(exc.value)
    assert book.read_bytes() == before_bytes
    assert {name: _rows(service, name) for name in baseline} == baseline


def test_orphan_receipt_appearing_after_user_preview_revokes_confirmation(rig):
    service, _book, options = rig
    preview = _stage(rig)
    _orphan_receipt(service, options)
    with pytest.raises(
        ManualAttachmentRejected, match="^acquisition_ambiguous_or_missing$",
    ):
        reconcile_numilog_manual(
            service, **options, confirmation=preview["plan_token"],
        )
    assert _rows(service, "placements") == []


def test_orphan_receipt_after_link_prevents_reusing_or_undoing_ambiguous_placement(rig):
    service, book, options = rig
    preview = _stage(rig)
    done = reconcile_numilog_manual(
        service, **options, confirmation=preview["plan_token"],
    )
    assert done["status"] == "applied"
    baseline = _rows(service, "placements")
    original = book.read_bytes()
    _orphan_receipt(service, options)
    with pytest.raises(
        ManualAttachmentRejected, match="^acquisition_ambiguous_or_missing$",
    ):
        reconcile_numilog_manual(service, **options)
    with pytest.raises(
        ManualAttachmentRejected, match="^acquisition_ambiguous_or_missing$",
    ):
        undo_numilog_manual(
            service, **options, operation_id=done["operation_id"],
        )
    assert _rows(service, "placements") == baseline
    assert book.read_bytes() == original


@pytest.mark.parametrize("owner,edition", [
    ("alex", "private-item"),
    ("default", "other-edition"),
])
def test_receipt_for_other_identity_or_edition_does_not_poison_selected_purchase(
    rig, owner, edition,
):
    service, _book, options = rig
    _orphan_receipt(service, options, identity=owner, edition=edition)
    preview = reconcile_numilog_manual(service, **options)
    assert preview["status"] == "preview"
    assert preview["changed"] == 0
    assert preview["plan_token"]
    assert "PRIVATE" not in json.dumps(preview)
    assert _rows(service, "placements") == []
