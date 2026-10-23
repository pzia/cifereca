"""P0 #2797: one Paris login can have primary and paired borrowers.

A statement is exact-account and explicitly confirmed; it is never a
provider-attested purchase, an active loan proof, or a placement operation.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from cifereca.core import library_declared_links as engine
from cifereca.core.library_inventory import scan_identity_roots
from cifereca.models import Account
from tests.test_library_declared_links import rig, records, rows
from tests.test_library_reconciliation import epub


@pytest.fixture
def paris_borrowers(rig, tmp_path):
    service, book, root, right, _ = rig
    # Exactly the provider-paris shape: primary has an empty upstream UID,
    # whereas its stored Loan/entitlement account coordinate is "primary".
    primary = Account(
        display_name="PRIVATE primary", unique_id="", is_primary=True,
        provider_id="paris", credential_id="PRIVATE-credential",
        identity_id="default",
    )
    paired = Account(
        display_name="PRIVATE paired", unique_id="paired-uid",
        is_primary=False, provider_id="paris",
        credential_id=primary.credential_id, identity_id="default",
    )
    service.accounts = [primary, paired]
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "UPDATE entitlements SET account_id='primary' "
            "WHERE entitlement_id='PRIVATE-right'"
        )
    # The same book edition may be borrowed by both members. Neither
    # borrower nor their locally selected file becomes the other's right.
    service.storage.entitlement_put({
        **right, "entitlement_id": "PRIVATE-paired-right",
        "account_id": "paired-uid",
    })
    other_book = root / "PRIVATE-paired-book.epub"
    epub(other_book, title="PRIVATE paired loan")
    observations = {
        row.path: row
        for row in scan_identity_roots({"default": (root,)}).files
    }
    def opts(account_id, path):
        return {
            "observation": observations[str(path)],
            "identity_id": "default", "provider_id": "paris",
            "credential_id": primary.credential_id,
            "account_id": account_id, "item_id": "PRIVATE-item",
        }
    return service, book, other_book, primary, paired, opts


@pytest.mark.parametrize("rig", ["paris"], indirect=True)
def test_paris_primary_and_paired_borrowers_can_declare_distinct_local_files(
    paris_borrowers,
):
    service, book, other_book, _, _, opts = paris_borrowers
    original_rights = rows(service, "entitlements")
    original_ledger = rows(service, "acquisition_ledger")
    primary = opts("primary", book)
    paired = opts("paired-uid", other_book)
    assert records(service) == []
    p1 = engine.declare_local_link(service, **primary)
    p2 = engine.declare_local_link(service, **paired)
    assert p1.status == p2.status == "preview"
    assert records(service) == []
    a = engine.declare_local_link(service, **primary, confirmation=p1.plan_token)
    assert a.status == "declared" and a.changed == 1
    # The other declaration was previewed before SQLite changed: stale token
    # must be refused, but its exact account remains independently eligible.
    with pytest.raises(engine.DeclarationRejected, match="preview_changed"):
        engine.declare_local_link(service, **paired, confirmation=p2.plan_token)
    p2 = engine.declare_local_link(service, **paired)
    b = engine.declare_local_link(service, **paired, confirmation=p2.plan_token)
    assert b.status == "declared" and b.changed == 1
    all_records = records(service)
    assert len(all_records) == 2
    assert {r["scope"]["account_id"] for r in all_records} == {
        "primary", "paired-uid",
    }
    assert all(r["origin"] == "user_declaration" for r in all_records)
    assert engine.inspect_declared_link(
        service, relation_id=a.relation_id, **primary
    ).status == "declared_current"
    assert engine.inspect_declared_link(
        service, relation_id=b.relation_id, **paired
    ).status == "declared_current"
    # The persisted history must use the same account identity rules as the
    # writer, not wrongly brand both paired borrowers as disconnected.
    history = engine.list_declared_links(service, identity_id="default")
    assert history.total == 2
    assert {entry.account_id for entry in history.rows} == {
        "primary", "paired-uid",
    }
    assert all(entry.review == "recorded_unverified_file" for entry in history.rows)
    assert rows(service, "entitlements") == original_rights
    assert rows(service, "acquisition_ledger") == original_ledger
    assert not rows(service, "placements")
    public = json.dumps({
        "status": a.status, "changed": a.changed,
        "trust": a.trust, "file_mutations": a.file_mutations,
    })
    assert "PRIVATE" not in public


@pytest.mark.parametrize("rig", ["paris"], indirect=True)
def test_paris_primary_alias_revoked_on_account_change(paris_borrowers):
    service, book, _, primary, _, opts = paris_borrowers
    plan = engine.declare_local_link(service, **opts("primary", book))
    primary.is_primary = False
    with pytest.raises(engine.DeclarationRejected, match="account_scope_changed"):
        engine.declare_local_link(
            service, **opts("primary", book), confirmation=plan.plan_token,
        )
    assert records(service) == []
    assert not rows(service, "placements")


@pytest.mark.parametrize("rig", ["paris"], indirect=True)
def test_paris_duplicate_borrower_identifier_is_ambiguous(paris_borrowers):
    service, book, _, primary, _, opts = paris_borrowers
    service.accounts.append(Account(
        display_name="PRIVATE duplicate primary", unique_id="primary",
        is_primary=False, provider_id="paris",
        credential_id=primary.credential_id, identity_id="default",
    ))
    with pytest.raises(engine.DeclarationRejected, match="account_scope_changed"):
        engine.declare_local_link(service, **opts("primary", book))
    assert records(service) == []


@pytest.mark.parametrize("rig", ["paris"], indirect=True)
def test_paris_declared_file_cannot_be_reused_for_second_borrower(paris_borrowers):
    service, book, _, _, _, opts = paris_borrowers
    primary = opts("primary", book)
    preview = engine.declare_local_link(service, **primary)
    engine.declare_local_link(service, **primary, confirmation=preview.plan_token)
    with pytest.raises(engine.DeclarationRejected, match="declaration_conflict"):
        engine.declare_local_link(service, **opts("paired-uid", book))
    assert len(records(service)) == 1
    assert not rows(service, "placements")
