"""P0 #2714: a Numilog placement has no borrower/account column.

A different credential of the SAME Cifereca identity may own the exact same
upstream edition. The selected account's otherwise valid purchase receipt
cannot make a provider/identity/item placement uniquely attributable.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.numilog_manual_link_transaction import (
    ManualAttachmentRejected, reconcile_numilog_manual, undo_numilog_manual,
)
from tests.test_core_p0_2714_numilog_manual_transaction import rig, _rows, _stage


def _second_right(service, *, credential="PRIVATE-other-credential",
                  account="PRIVATE-other-borrower", identity="default",
                  item="private-item", state="active"):
    service.storage.entitlement_put({
        "entitlement_id": "PRIVATE-other-right",
        "identity_id": identity,
        "provider_id": "numilog",
        "credential_id": credential,
        "account_id": account,
        "item_id": item,
        "entitlement_type": "owned",
        "state": state,
        "metadata": {
            "source": "numilog.authenticated_library",
            "title": "PRIVATE same edition",
        },
    })


@pytest.mark.parametrize("other_state", ["active", "expired", "realized"])
def test_two_credential_rights_same_local_identity_cannot_attest_placement(
    rig, other_state,
):
    service, book, options = rig
    before_file = book.read_bytes()
    _second_right(service, state=other_state)
    before = {name: _rows(service, name) for name in (
        "placements", "acquisition_ledger", "artifacts", "catalog_entries",
    )}
    with pytest.raises(
        ManualAttachmentRejected, match="^placement_account_ambiguous$",
    ) as error:
        reconcile_numilog_manual(service, **options)
    assert "PRIVATE" not in str(error.value)
    assert {name: _rows(service, name) for name in before} == before
    assert book.read_bytes() == before_file


def test_second_credential_appearing_after_preview_invalidates_confirmation(rig):
    service, book, options = rig
    preview = _stage(rig)
    before = _rows(service, "placements")
    _second_right(service)
    with pytest.raises(
        ManualAttachmentRejected, match="^placement_account_ambiguous$",
    ):
        reconcile_numilog_manual(
            service, **options, confirmation=preview["plan_token"],
        )
    assert _rows(service, "placements") == before
    assert "PRIVATE" not in json.dumps(preview)


def test_second_credential_appearing_after_link_prevents_inherited_attestation_or_undo(
    rig,
):
    service, book, options = rig
    preview = _stage(rig)
    result = reconcile_numilog_manual(
        service, **options, confirmation=preview["plan_token"],
    )
    assert result["status"] == "applied"
    before = _rows(service, "placements")
    _second_right(service)
    # The placement cannot encode which of the two borrower accounts owns
    # it, including on an otherwise legitimate read-only 'already_linked'.
    with pytest.raises(
        ManualAttachmentRejected, match="^placement_account_ambiguous$",
    ):
        reconcile_numilog_manual(service, **options)
    with pytest.raises(
        ManualAttachmentRejected, match="^placement_account_ambiguous$",
    ):
        undo_numilog_manual(
            service, **options, operation_id=result["operation_id"],
        )
    assert _rows(service, "placements") == before
    assert book.exists()


@pytest.mark.parametrize("other_identity,other_item", [
    ("alex", "private-item"),
    ("default", "a-different-edition"),
])
def test_other_local_identity_or_other_edition_does_not_poison_selected_proof(
    rig, other_identity, other_item,
):
    service, book, options = rig
    _second_right(service, identity=other_identity, item=other_item)
    stage = reconcile_numilog_manual(service, **options)
    assert stage["status"] == "preview"
    assert _rows(service, "placements") == []
    applied = reconcile_numilog_manual(
        service, **options, confirmation=stage["plan_token"],
    )
    assert applied["status"] == "applied"
    assert len(_rows(service, "placements")) == 1
    assert service.storage.placement_for_path(book)["provider_id"] == "numilog"


def test_same_item_other_credential_does_not_create_fake_receipt_or_catalogue(rig):
    service, _book, options = rig
    _second_right(service)
    rights_before = _rows(service, "entitlements")
    with pytest.raises(ManualAttachmentRejected):
        reconcile_numilog_manual(service, **options)
    assert _rows(service, "entitlements") == rights_before
    assert len(_rows(service, "acquisition_ledger")) == 1
    assert _rows(service, "placements") == []
    assert _rows(service, "catalog_entries") == []


def test_shared_inspection_preflight_explains_cross_credential_ambiguity(rig):
    from pathlib import Path
    from cifereca.core.library_audit import audit_library
    from cifereca.core.library_local_inspection import LocalInspection

    service, book, options = rig
    _second_right(service)
    audit = audit_library(
        Path(service.storage.path), {"default": (book.parent,)},
    )
    inspection = LocalInspection(
        audit, lambda: True, "default", roots=(book.parent,), service=service,
    )
    try:
        right = next(
            key for key, scope in inspection._manual_right_scopes.items()
            if scope[2] == options["credential_id"]
        )
        file = inspection.unconfirmed_files(
            query="PRIVATE_edition", limit=5,
        ).cases[0].key
        review = inspection.preflight_manual_link(right, file)
        assert review.status == "blocked"
        assert "right_scope_ambiguous" in review.codes
        assert review.read_only is True
        assert review.attachment_authorized is False
        assert _rows(service, "placements") == []
        # Neither credential nor entitlement ID enters closed public codes.
        assert "PRIVATE" not in json.dumps(review.codes)
    finally:
        inspection.close()
