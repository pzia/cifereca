"""P0 #2714 / #2797: exact Numilog borrower under shared credentials.

All acquisitions/files are synthetic. An unrelated sibling account must not
invalidate the selected account, while duplicate/foreign ownership of that
*same account coordinate* always fails closed. The Numilog receipt/edition,
SHA/CRC, lock and confirmation/undo contracts remain unchanged.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.library_inventory import scan_identity_roots
from cifereca.core.numilog_manual_link_transaction import (
    ManualAttachmentRejected, reconcile_numilog_manual, undo_numilog_manual,
)
from cifereca.models import Account
from tests.test_core_p0_2714_numilog_manual_transaction import rig, _rows


def _sibling(account, *, identity="default", uid="second-account"):
    return replace(
        account, unique_id=uid, identity_id=identity,
        display_name="PRIVATE other borrower",
    )


def test_distinct_account_on_same_credential_does_not_block_exact_numilog_attachment(rig):
    service, book, options = rig
    original = book.read_bytes()
    service.accounts.append(_sibling(service.accounts[0]))
    before = {
        table: _rows(service, table) for table in (
            "entitlements", "acquisition_ledger", "artifacts",
            "placements", "catalog_entries",
        )
    }
    preview = reconcile_numilog_manual(service, **options)
    assert preview["status"] == "preview"
    assert _rows(service, "placements") == before["placements"]
    applied = reconcile_numilog_manual(
        service, **options, confirmation=preview["plan_token"],
    )
    assert applied["status"] == "applied"
    assert applied["file_mutations"] == applied["rights_mutations"] == 0
    assert len(_rows(service, "placements")) == 1
    assert book.read_bytes() == original
    undo_preview = undo_numilog_manual(
        service, **options, operation_id=applied["operation_id"],
    )
    assert undo_preview["status"] == "undo_preview"
    undone = undo_numilog_manual(
        service, **options, operation_id=applied["operation_id"],
        confirmation=undo_preview["plan_token"],
    )
    assert undone["status"] == "undone"
    assert _rows(service, "placements") == before["placements"]
    for table in ("entitlements", "acquisition_ledger", "artifacts", "catalog_entries"):
        assert _rows(service, table) == before[table]
    assert book.read_bytes() == original


def test_two_borrowers_with_distinct_editions_can_confirm_independently(rig):
    service, book, first = rig
    account = service.accounts[0]
    other = _sibling(account)
    service.accounts.append(other)
    alternate = book.with_name("PRIVATE_other_edition.epub")
    alternate.write_bytes(book.read_bytes())
    observation = next(
        row for row in scan_identity_roots(
            {"default": (book.parent,)},
        ).files if row.path == str(alternate)
    )
    service.storage.entitlement_put({
        "entitlement_id": "PRIVATE-right-second",
        "provider_id": "numilog", "credential_id": account.credential_id,
        "account_id": other.unique_id, "identity_id": "default",
        "item_id": "PRIVATE-edition-second",
        "entitlement_type": "owned", "state": "active",
        "metadata": {"source": "numilog.authenticated_library"},
    })
    service.storage.acquisition_put({
        "acquisition_id": "PRIVATE-receipt-second",
        "entitlement_id": "PRIVATE-right-second",
        "provider_id": "numilog", "credential_id": account.credential_id,
        "account_id": other.unique_id, "identity_id": "default",
        "item_id": "PRIVATE-edition-second", "status": "complete",
        "artifact_id": "sha256:" + observation.sha256,
    })
    second = {
        **first, "observation": observation,
        "account_id": other.unique_id, "item_id": "PRIVATE-edition-second",
    }
    for options in (first, second):
        preview = reconcile_numilog_manual(service, **options)
        assert preview["status"] == "preview"
        applied = reconcile_numilog_manual(
            service, **options, confirmation=preview["plan_token"],
        )
        assert applied["status"] == "applied"
        assert applied["changed"] == 1
    assert len(_rows(service, "placements")) == 2
    assert {row["account_id"] for row in service.storage.entitlements(provider_id="numilog")} == {
        first["account_id"], other.unique_id,
    }
    assert book.read_bytes() == alternate.read_bytes()


@pytest.mark.parametrize("hostile", ["same_id", "same_id_foreign_identity", "renamed_during_preview"])
def test_duplicate_or_rebound_selected_borrower_cannot_confirm(rig, hostile):
    service, _book, options = rig
    original = service.accounts[0]
    preview = reconcile_numilog_manual(service, **options)
    assert preview["status"] == "preview"
    if hostile == "same_id":
        service.accounts.append(_sibling(original, uid=original.unique_id))
    elif hostile == "same_id_foreign_identity":
        service.accounts.append(_sibling(original, identity="alex", uid=original.unique_id))
    else:
        original.unique_id = "changed-borrower"
    with pytest.raises(ManualAttachmentRejected, match="^account_scope_changed$"):
        reconcile_numilog_manual(
            service, **options, confirmation=preview["plan_token"],
        )
    assert not _rows(service, "placements")


def test_foreign_borrower_must_not_borrow_other_accounts_receipt(rig):
    service, _book, options = rig
    sibling = _sibling(service.accounts[0])
    service.accounts.append(sibling)
    # An account coordinate without an acquisition for its own edition has
    # no right to inherit an otherwise matching original receipt.
    with pytest.raises(ManualAttachmentRejected, match="^owned_right_not_attested$"):
        reconcile_numilog_manual(
            service, **{**options, "account_id": sibling.unique_id},
        )
    assert not _rows(service, "placements")
