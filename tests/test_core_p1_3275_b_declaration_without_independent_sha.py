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

        # End-to-end CoreUI bridge: B must be able to pick its OWN right and
        # the received A file as separate, exact, unconfirmed observations.
        # A successful snapshot does not license an automatic Kobo backup.
        from cifereca.core.library_audit import audit_library
        from cifereca.core.library_local_inspection import LocalInspection
        root_b = tmp_path / "EBooks" / "bob"
        audit = audit_library(service.storage.path, {"bob": (root_b,)})
        assert audit.redacted()["status"] == "complete", audit.redacted()["reasons"]
        inspection = LocalInspection(
            audit, lambda: True, "bob", roots=(root_b,), service=service,
        )
        try:
            rights = inspection.page(
                provider="kobo", state="rights_without_verified_file",
            ).cases
            files = inspection.unconfirmed_files(limit=10).cases
            assert len(rights) == len(files) == 1
            right, file = rights[0], files[0]
            context = inspection.declaration_context(right.key, file.key)
            assert context["identity"] == "bob"
            assert context["provider"] == "kobo"
            assert context["account"] == "account-b"
            assert context["edition"] == "book-1"
            assert context["path"] == str(received)
            assert inspection.preview_manual_attachment(
                right.key, file.key,
            )["status"] in {"unavailable", "blocked"}
            ui_plan = inspection.preview_declared_link(right.key, file.key)
            assert ui_plan["status"] == "preview"
            assert ui_plan["requires_explicit_confirmation"] is True
            assert ui_plan["trust"] == "user_declaration"
            assert "plan_token" not in ui_plan
            assert "relation_id" not in ui_plan
            assert not declarations.has_active_declared_link(service.storage, **scope)
            inspection.revoke_manual_attachment_preview()
            assert inspection.confirm_declared_link(
                right.key, file.key, user_confirmed=True,
            )["status"] == "unavailable"
        finally:
            inspection.close()

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

        # After explicit consent the inspected file remains a *declaration*,
        # not a verified Kobo receipt or a confirmed acquisition/placement.
        # Reopening the UI inspection must still find the exact B pair.
        post = audit_library(service.storage.path, {"bob": (root_b,)})
        assert post.redacted()["status"] == "complete", post.redacted()["reasons"]
        second_inspection = LocalInspection(
            post, lambda: True, "bob", roots=(root_b,), service=service,
        )
        try:
            declared = second_inspection.page(state="declared_link_recorded").cases
            assert any(row.key.startswith("right:") for row in declared)
            assert any(row.key.startswith("file:") for row in declared)
            assert any(
                "Association manuelle enregistrée" in row.label
                for row in declared if row.key.startswith("right:")
            )
            assert not any(
                row.key.startswith("file:")
                for row in second_inspection.page(state="confirmed_link").cases
            )
            selected_right = next(
                row for row in declared if row.key.startswith("right:")
            )
            selected_file = next(
                row for row in declared if row.key.startswith("file:")
            )
            review = second_inspection.preview_declared_link(
                selected_right.key, selected_file.key,
            )
            assert review["status"] == "already_declared"
            assert review["presence_state"] == "present_declared"
            assert review["trust"] == "user_declaration"
            assert review["revoke_available"] is True
            assert "plan_token" not in review
        finally:
            second_inspection.close()

        # A's right never appears under B and the independent B user
        # statement never manufactures a completed supplier receipt.
        original_placements = [
            p for p in service.storage.placements()
            if p["identity_id"] == "bob"
        ]
        # A repeated local scan legitimately refreshes observation clocks.
        # Idempotence here means the exact owner, path, artifact, metadata and
        # state stay unchanged, NOT that last_seen_at/updated_at freeze.
        def stable_placement(row):
            return {
                key: value for key, value in row.items()
                if key not in {"last_seen_at", "updated_at"}
            }

        expected_placements = [
            stable_placement(row) for row in original_placements
        ]
        previous_clocks = [
            (row["last_seen_at"], row["updated_at"])
            for row in original_placements
        ]
        assert all(
            type(seen) is int and type(updated) is int
            for seen, updated in previous_clocks
        )
        for _ in range(2):
            refreshed = service.sync_kobo_backup_account("vault-b")
            assert refreshed["attempted"] == refreshed["downloaded"] == 0
            assert refreshed["already_local_declared"] + refreshed["reconcile_skipped"] >= 1
            back = service.storage.backup_get("kobo", "vault-b", "book-1")
            assert back is None or back["status"] != "complete"
            assert declarations.has_active_declared_link(service.storage, **scope)
            current_placements = [
                p for p in service.storage.placements() if p["identity_id"] == "bob"
            ]
            assert [stable_placement(p) for p in current_placements] == expected_placements
            assert len(current_placements) == len(previous_clocks)
            current_clocks = [
                (row["last_seen_at"], row["updated_at"])
                for row in current_placements
            ]
            assert all(
                type(seen) is int and type(updated) is int
                and seen >= former_seen and updated >= former_updated
                for (seen, updated), (former_seen, former_updated)
                in zip(current_clocks, previous_clocks)
            )
            previous_clocks = current_clocks
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
