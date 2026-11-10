"""P0 #3196: historical W2 recovery has no active authority.

The 0.7 receipts remain inspectable as archives; only canonical A→B may
recover a completed provider delivery. Retired W2 cannot scan, update,
reclassify or GET even with a formerly eligible live session.
"""
from __future__ import annotations

from copy import copy

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_reconcile_prune import _service


def _interrupted(credential, item, *, identity="default", account="account"):
    return {
        "provider_id": "kobo", "credential_id": credential,
        "identity_id": identity, "account_id": account,
        "item_id": item, "revision_id": "legacy-revision",
        "title": "Synthetic legacy item", "format": "EPUB",
        "status": "acquiring", "local_path": None,
        "attempt_count": 1, "job_id": "legacy-attempt",
    }


@pytest.mark.parametrize("source_state", [
    "live", "disconnected", "disabled", "duplicated_session",
    "replaced_session", "rebound_account", "foreign_identity",
    "offline_credential",
])
def test_retired_w2_never_reclassifies_archived_recovery_receipts(
    tmp_path, monkeypatch, source_state,
):
    """A formerly viable source still cannot revive the legacy writer."""
    service = _service(tmp_path, manifest=[])
    try:
        record = _interrupted(
            "vault-old" if source_state == "offline_credential" else "vault-1",
            "book-1",
            identity="foreign-profile" if source_state == "foreign_identity"
            else "default",
            account="former-borrower" if source_state == "rebound_account"
            else "account",
        )
        service.storage.backup_put(record)
        before = service.storage.backup_get(
            "kobo", record["credential_id"], "book-1",
        )
        session = next(iter(service._sessions.values()))
        if source_state == "disconnected":
            session.connected = False
        elif source_state == "disabled":
            service.storage.provider_connection_put({
                "identity_id": "default", "provider_id": "kobo",
                "credential_id": "vault-1", "enabled": False,
            })
        elif source_state == "duplicated_session":
            service._sessions["extra-session"] = session
        elif source_state == "replaced_session":
            key = next(iter(service._sessions))
            service._sessions[key] = copy(session)

        def forbidden(*_args, **_kwargs):
            pytest.fail("retired W2 must never consult manifest or mutate B")

        monkeypatch.setattr(session, "backup_manifest", forbidden)
        monkeypatch.setattr(service, "_execute_kobo_acquisition", forbidden)
        monkeypatch.setattr(service.storage, "backup_put", forbidden)
        for _ in range(2):
            with pytest.raises(
                ProviderConfigurationError,
                match="kobo_legacy_w2_retired_use_canonical_provider",
            ):
                service.sync_kobo_backup()
            assert service.storage.backup_get(
                "kobo", record["credential_id"], "book-1",
            ) == before
        assert service.storage.placements() == []
    finally:
        service.close()


@pytest.mark.parametrize("mutation", [
    "disconnect", "disable", "replace", "typed", "epoch",
])
def test_legacy_recovery_has_no_candidate_scan_or_source_rebind_window(
    tmp_path, monkeypatch, mutation,
):
    """Once W2 is retired, no check-then-act TOCTOU window remains."""
    service = _service(tmp_path, manifest=[])
    try:
        service.storage.backup_put(_interrupted("vault-1", "book-1"))
        before = service.storage.backup_get("kobo", "vault-1", "book-1")
        session = next(iter(service._sessions.values()))
        if mutation == "disconnect":
            session.connected = False
        elif mutation == "disable":
            service.storage.provider_connection_put({
                "identity_id": "default", "provider_id": "kobo",
                "credential_id": "vault-1", "enabled": False,
            })
        elif mutation == "replace":
            key = next(iter(service._sessions))
            service._sessions[key] = copy(session)
        elif mutation == "epoch":
            service._source_context_epoch = (
                getattr(service, "_source_context_epoch", 0) + 1
            )
        else:
            with service.storage._lock, service.storage._connect() as db:
                db.execute(
                    "INSERT INTO source_references("
                    "source_reference_id,identity_id,provider_id,"
                    "credential_id,account_id,branch_id,edition_id,"
                    "right_id,reference_kind,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ("source:typed", "other", "kobo", "vault-1",
                     "borrower-b", "owned", "book-1",
                     "right-b", "purchase", 1, 1),
                )
        assert not hasattr(service, "_kobo_record_candidate_paths")
        with pytest.raises(
            ProviderConfigurationError,
            match="kobo_legacy_w2_retired_use_canonical_provider",
        ):
            service.sync_kobo_backup()
        assert service.storage.backup_get("kobo", "vault-1", "book-1") == before
    finally:
        service.close()
