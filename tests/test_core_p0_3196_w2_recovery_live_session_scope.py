"""P0 #3196 W2: offline/rebound 0.7 receipts cannot drive recovery writes.

All observations are synthetic and local; no provider GET, ADEPT or downloads.
"""
from __future__ import annotations

import pytest

from tests.test_kobo_backup_reconcile_prune import _service


def _interrupted(credential: str, item: str, *, identity="default", account="account"):
    return {
        "provider_id": "kobo", "credential_id": credential,
        "identity_id": identity, "account_id": account,
        "item_id": item, "revision_id": "legacy-revision",
        "title": "Synthetic legacy item", "format": "EPUB",
        "status": "acquiring", "local_path": None,
        "attempt_count": 1, "job_id": "legacy-attempt",
    }


def test_unscoped_recovery_never_writes_disconnected_or_rebound_receipts(tmp_path, monkeypatch):
    service = _service(tmp_path, manifest=[])
    try:
        original = {
            "active": _interrupted("vault-1", "active"),
            "offline": _interrupted("vault-old", "offline"),
            "rebound": _interrupted("vault-1", "rebound", account="former-account"),
            "foreign": _interrupted("vault-1", "foreign", identity="former-profile"),
        }
        for record in original.values():
            service.storage.backup_put(record)
        before = {
            name: service.storage.backup_get("kobo", record["credential_id"], record["item_id"])
            for name, record in original.items()
        }
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_args, **_kwargs: pytest.fail("legacy recovery must not GET"),
        )

        summary = service.sync_kobo_backup()
        assert summary["recovered_interrupted"] == 1
        assert summary["reconcile_pending"] == 0
        active = service.storage.backup_get("kobo", "vault-1", "active")
        assert active["status"] == "pending_acquisition"
        assert active["attention_code"] == "interrupted_acquisition_review"
        for name in ("offline", "rebound", "foreign"):
            receipt = before[name]
            record = original[name]
            assert service.storage.backup_get(
                "kobo", record["credential_id"], record["item_id"]
            ) == receipt

        again = service.sync_kobo_backup()
        assert again["recovered_interrupted"] == 0
        assert again["downloaded"] == 0
    finally:
        service.close()


def test_recovery_requires_one_live_session_per_credential(tmp_path):
    service = _service(tmp_path, manifest=[])
    try:
        record = _interrupted("vault-1", "ambiguous")
        service.storage.backup_put(record)
        before = service.storage.backup_get("kobo", "vault-1", "ambiguous")
        # A duplicated registered object must not count as a distinct
        # provider account or authorize a global 0.7 recovery.
        key = "synthetic-extra-session"
        session = next(iter(service._sessions.values()))
        service._sessions[key] = session
        summary = service.sync_kobo_backup()
        assert summary["recovered_interrupted"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "ambiguous") == before
    finally:
        service.close()



def _recover_only(service):
    from cifereca.core.kobo_backup import _recover_local_remnants

    summary = {
        "recovered_interrupted": 0, "recovered_unparsed": 0,
        "reconcile_pending": 0, "reconcile_conflicts": 0,
    }
    _recover_local_remnants(service, summary, {}, lambda *_args, **_kw: None)
    return summary


def test_recovery_refuses_disabled_bound_connection(tmp_path):
    service = _service(tmp_path, manifest=[])
    try:
        record = _interrupted("vault-1", "disabled")
        service.storage.backup_put(record)
        before = service.storage.backup_get("kobo", "vault-1", "disabled")
        service.storage.provider_connection_put({
            "identity_id": "default", "provider_id": "kobo",
            "credential_id": "vault-1", "label": "Disabled", "enabled": False,
        })
        summary = _recover_only(service)
        assert summary["recovered_interrupted"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "disabled") == before
    finally:
        service.close()


def test_legacy_default_alias_preserves_recovery_for_verified_named_owner(tmp_path):
    service = _service(tmp_path, manifest=[])
    try:
        # The provider's Account model can report "default" even when Core's
        # verified SQLite credential owner is another named identity.
        service.storage.provider_connection_put({
            "identity_id": "named", "provider_id": "kobo",
            "credential_id": "vault-1", "label": "Named", "enabled": True,
        })
        record = _interrupted("vault-1", "named-owned", identity="named")
        service.storage.backup_put(record)
        summary = _recover_only(service)
        assert summary["recovered_interrupted"] == 1
        receipt = service.storage.backup_get("kobo", "vault-1", "named-owned")
        assert receipt["status"] == "pending_acquisition"
        assert receipt["attention_code"] == "interrupted_acquisition_review"
    finally:
        service.close()


def test_recovery_disconnected_current_session_cannot_change_its_checkpoint(tmp_path):
    service = _service(tmp_path, manifest=[])
    try:
        record = _interrupted("vault-1", "disconnected")
        service.storage.backup_put(record)
        before = service.storage.backup_get("kobo", "vault-1", "disconnected")
        session = next(iter(service._sessions.values()))
        session.connected = False
        summary = _recover_only(service)
        assert summary["recovered_interrupted"] == 0
        assert summary["reconcile_pending"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "disconnected") == before
    finally:
        service.close()


@pytest.mark.parametrize("switch", ("disconnect", "disable", "epoch", "typed", "session_replaced"))
def test_recovery_refuses_source_changed_during_candidate_scan(
    tmp_path, monkeypatch, switch,
):
    """No stale W2 row can advance after the source check but before SQL."""
    from cifereca.core.kobo_backup import _KoboLegacySessionChanged

    service = _service(tmp_path, manifest=[])
    try:
        record = _interrupted("vault-1", "interrupted")
        service.storage.backup_put(record)
        before = service.storage.backup_get("kobo", "vault-1", "interrupted")
        session = next(iter(service._sessions.values()))
        original_scan = service._kobo_record_candidate_paths
        mutation_done = False

        def scan_then_change(*args, **kwargs):
            nonlocal mutation_done
            if not mutation_done:
                mutation_done = True
                if switch == "disconnect":
                    session.connected = False
                elif switch == "disable":
                    service.storage.provider_connection_put({
                        "identity_id": "default", "provider_id": "kobo",
                        "credential_id": "vault-1", "enabled": False,
                    })
                elif switch == "epoch":
                    service._source_context_epoch = (
                        getattr(service, "_source_context_epoch", 0) + 1
                    )
                elif switch == "session_replaced":
                    from copy import copy
                    # Same declared account/credential but another session
                    # object. Epoch-less facades must still fail closed.
                    key = next(iter(service._sessions))
                    service._sessions[key] = copy(session)
                else:
                    # Another borrower/Core pipeline publishes an exact
                    # typed edition while the legacy candidate is scanned.
                    with service.storage._lock, service.storage._connect() as db:
                        db.execute(
                            "INSERT INTO source_references("
                            "source_reference_id,identity_id,provider_id,"
                            "credential_id,account_id,branch_id,edition_id,"
                            "right_id,reference_kind,created_at,updated_at)"
                            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                "source:concurrent", "other", "kobo",
                                "vault-1", "borrower-b", "owned",
                                "interrupted", "right-b", "purchase", 1, 1,
                            ),
                        )
                        db.commit()
            return original_scan(*args, **kwargs)

        monkeypatch.setattr(
            service, "_kobo_record_candidate_paths", scan_then_change,
        )
        with pytest.raises(
            _KoboLegacySessionChanged,
            match=(
                "kobo_canonical_source_delivery_active"
                if switch == "typed" else "kobo_legacy_session_changed"
            ),
        ):
            _recover_only(service)

        assert mutation_done
        assert service.storage.backup_get(
            "kobo", "vault-1", "interrupted",
        ) == before
    finally:
        service.close()
