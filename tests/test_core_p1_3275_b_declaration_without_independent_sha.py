"""#3275: B may explicitly declare A's received EPUB against B's own Kobo
purchase without forging a receipt or transferring A's Kobo ownership.

This exercises the existing *generic* Core declaration writer (#2797 /
#3171), not the attested-SHA Kobo reconciliation writer. All providers,
accounts, bytes and rights are synthetic; no HTTP or DRM.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.core import library_declared_links as declarations
from cifereca.core.library_inventory import scan_identity_roots
from cifereca.models import Account
from cifereca.library_files import sha256_file
from tests.test_library_reconciliation import epub
from tests.test_kobo_backup_guard import (
    KoboSession, _manifest_row, _service,
)


def test_b_own_manifest_plus_received_a_file_needs_explicit_user_declaration(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    monkeypatch.setattr(
        declarations, "acquisition_lock_file",
        lambda: tmp_path / "user-declaration.lock",
    )
    try:
        # The cheap backup fixture uses a ZIP sufficient for transfer, but
        # the *declaration writer* correctly requires a fully validated EPUB.
        # Build exact source records from a structurally valid scanned book.
        source = tmp_path / "EBooks" / "alice" / "kobo" / "Book.epub"
        source.parent.mkdir(parents=True, exist_ok=True)
        epub(source, title="Synthetic Kobo Book")
        artifact_id = "sha256:" + sha256_file(source)
        source_placement = service.storage.placement_put({
            "artifact_id": artifact_id, "path": str(source),
            "identity_id": "alice", "provider_id": "kobo",
            "source_item_id": "book-1", "state": "present", "metadata": {},
        })
        service.storage.catalog_entry_put({
            "catalog_entry_id": "alice-kobo-book-1",
            "artifact_id": artifact_id, "placement_id": source_placement,
            "title": "Synthetic Kobo Book", "author": "Synthetic Author",
            "identity_id": "alice", "provider_id": "kobo",
            "item_id": "book-1", "local_state": "local", "media_format": "epub",
            "metadata": {"path": str(source), "source_credential": "vault-1"},
        })
        source_bytes = source.read_bytes()
        received = Path(
            service.materialize_to_identity(source_placement, "bob")["path"]
        )
        assert received.read_bytes() == source_bytes
        assert received != source

        service.storage.provider_connection_put({
            "identity_id": "bob", "provider_id": "kobo",
            "credential_id": "vault-b", "label": "B independent Kobo",
        })
        a = service._sessions[service._session_key("kobo", "vault-1")]
        b = KoboSession(
            descriptor=a.descriptor,
            credential_id="vault-b", label="B independent Kobo",
            credentials={}, options={}, manifest=[_manifest_row()],
        )
        b.connect()
        b._accounts = [Account(
            display_name="B's independent Kobo", unique_id="account-b",
            provider_id="kobo", credential_id="vault-b",
            identity_id="bob",
        )]
        service._sessions[service._session_key("kobo", "vault-b")] = b
        service.accounts = b.accounts
        service.current_identity = service.identity_by_id("bob")
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_a, **_k: pytest.fail("B must never redownload A's received bytes"),
        )

        # B's positive manifest is a real B product entitlement but not an
        # attestation that A's *file hash* was ever delivered to account B.
        first = service.sync_kobo_backup_account("vault-b")
        assert first["attempted"] == first["downloaded"] == 0
        b_back = service.storage.backup_get("kobo", "vault-b", "book-1")
        assert b_back["status"] != "complete"
        b_rights = [
            row for row in service.storage.entitlements(provider_id="kobo")
            if row["identity_id"] == "bob"
            and row["credential_id"] == "vault-b"
            and row["item_id"] == "book-1"
        ]
        assert len(b_rights) == 1 and b_rights[0]["state"] == "active"
        assert b_rights[0]["entitlement_type"] == "owned"

        scanned = scan_identity_roots({
            "bob": (tmp_path / "EBooks" / "bob",),
        })
        observed = [f for f in scanned.files if f.path == str(received)]
        assert len(observed) == 1
        scope = dict(
            identity_id="bob", provider_id="kobo", credential_id="vault-b",
            account_id="account-b", item_id="book-1",
        )
        assert not declarations.has_active_declared_link(service.storage, **scope)
        preview = declarations.declare_local_link(
            service, observation=observed[0], **scope,
        )
        assert preview.status == "preview"
        assert preview.trust == "user_declaration"
        assert not declarations.has_active_declared_link(service.storage, **scope)
        assert service.storage.backup_get("kobo", "vault-b", "book-1")["status"] != "complete"

        confirmed = declarations.declare_local_link(
            service, observation=observed[0], confirmation=preview.plan_token,
            **scope,
        )
        assert confirmed.status == "declared"
        assert confirmed.trust == "user_declaration"
        assert confirmed.rights_mutations == 0
        assert confirmed.placement_mutations == 0
        assert confirmed.file_mutations == 0
        assert declarations.has_active_declared_link(service.storage, **scope)

        # A's right never appears under B and the independent B user
        # statement never manufactures a completed supplier receipt.
        original_placements = [
            p for p in service.storage.placements()
            if p["identity_id"] == "bob"
        ]
        for _ in range(2):
            refreshed = service.sync_kobo_backup_account("vault-b")
            assert refreshed["attempted"] == refreshed["downloaded"] == 0
            assert refreshed["already_local_declared"] + refreshed["reconcile_skipped"] >= 1
            back = service.storage.backup_get("kobo", "vault-b", "book-1")
            assert back is None or back["status"] != "complete"
            assert declarations.has_active_declared_link(service.storage, **scope)
            assert [
                p for p in service.storage.placements() if p["identity_id"] == "bob"
            ] == original_placements
            assert source.read_bytes() == received.read_bytes() == source_bytes
            assert len([
                row for row in service.storage.entitlements(provider_id="kobo")
                if row["identity_id"] == "bob" and row["credential_id"] == "vault-b"
                and row["item_id"] == "book-1"
            ]) == 1
            assert not [
                row for row in service.storage.acquisitions(provider_id="kobo")
                if row["credential_id"] == "vault-b" and row["status"] == "complete"
            ]
    finally:
        service.close()
