"""#3171: opening Sync shares the canonical A→B observation phase.

Real LibraryService + SQLite v14, synthetic sessions; no supplier network or
ADEPT. The opening path must not start a second local reconciliation loop.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account
from tests.test_sync_state_contract import _service
from tests.test_core_p0_3171_atomic_publication_v14 import (
    new_source, register, target, publish,
)


def rig(tmp_path, monkeypatch, *, typed=True):
    service = _service(tmp_path)
    assert service.storage.schema_version() == 14
    if service.storage.identity_get("default") is None:
        service.storage.identity_put({
            "identity_id": "default", "directory_name": "hugues",
            "label": "Hugues",
        })
    account = Account(
        identity_id="default", provider_id="seller-a",
        credential_id="card-a", unique_id="buyer-a",
    )
    service.accounts = [account]
    register(service.storage, "default", "seller-a", "card-a", account="buyer-a")
    # Keep the real LibraryService profile directory from its configured
    # identity, rather than the generic test helper's default label.
    service.storage.identity_put({
        "identity_id": "default", "directory_name": "hugues",
        "label": "Hugues",
    })
    source, snap = new_source(
        "default", provider="seller-a", credential="card-a", account="buyer-a",
    )
    reads = []
    session = NS(
        provider_id="seller-a", credential_id="card-a",
        connected=True, _closed=False,
        capabilities=NS(loans=False, personal_library=False, backup=False),
    )
    if typed:
        def rights_read(_account):
            reads.append(1)
            return snap
        session.rights_observation_snapshot = rights_read
    service._sessions[service._session_key("seller-a", "card-a")] = session
    monkeypatch.setattr(service, "_loans_for_account", lambda a, force=False: [])
    monkeypatch.setattr(
        service, "_acquire_active_loan_automatically",
        lambda _: pytest.fail("opening rights observation cannot download"),
    )
    return service, account, source, snap, session, reads


def refs(store):
    with store._lock, store._connect() as db:
        return db.execute(
            "SELECT identity_id,provider_id,credential_id,account_id,branch_id,"
            "edition_id,right_id FROM source_references"
        ).fetchall(), db.execute(
            "SELECT count(*) FROM origin_links"
        ).fetchone()[0]


def test_opening_sync_reads_optional_A_once_and_stores_positive_only(tmp_path, monkeypatch):
    service, account, source, snapshot, session, reads = rig(tmp_path, monkeypatch)
    try:
        first = service.opening_sync()
        own = next(x for x in first["identities"] if x["identity_id"] == "default")
        assert own["canonical_rights"]["sources"] == 1
        assert own["canonical_rights"]["observed"] == 1
        assert own["canonical_rights"]["present_attested"] == 0
        assert reads == [1]
        assert refs(service.storage) == ([
            ("default", "seller-a", "card-a", "buyer-a",
             "purchases", "edition", "purchase"),
        ], 0)
        assert service.storage.placements() == []
        assert service.storage.acquisitions() == []

        second = service.opening_sync()
        own = next(x for x in second["identities"] if x["identity_id"] == "default")
        assert own["canonical_rights"]["source_references_changed"] == 0
        assert reads == [1, 1]
        assert len(refs(service.storage)[0]) == 1
    finally:
        service.close()


def test_opening_sync_rehashes_attested_B_in_same_producer_path(tmp_path, monkeypatch):
    service, account, source, snapshot, session, reads = rig(tmp_path, monkeypatch)
    try:
        path = service.library_dir() / "hugues" / "book.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.7\nStandalone synthetic canonical book\n")
        import hashlib
        from cifereca.core.canonical_publication import publish_completed_acquisition
        publish_completed_acquisition(
            service.storage, library_root=service.library_dir(),
            path=path, source=source, snapshot=snapshot,
            final_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            delivery_receipt_ref="synthetic-completed-receipt",
        )
        result = service.opening_sync()
        own = next(x for x in result["identities"] if x["identity_id"] == "default")
        assert own["canonical_rights"]["present_attested"] == 1
        assert reads == [1]
        assert len(refs(service.storage)[0]) == 1
        assert refs(service.storage)[1] == 1
    finally:
        service.close()


def test_opening_legacy_provider_without_optional_reader_is_unchanged(tmp_path, monkeypatch):
    service, account, source, snapshot, session, reads = rig(
        tmp_path, monkeypatch, typed=False,
    )
    try:
        result = service.opening_sync()
        own = next(x for x in result["identities"] if x["identity_id"] == "default")
        assert "canonical_rights" not in own
        assert reads == []
        assert refs(service.storage) == ([], 0)
    finally:
        service.close()


def test_opening_failed_typed_read_cannot_revoke_previous_A(tmp_path, monkeypatch):
    service, account, source, snapshot, session, reads = rig(tmp_path, monkeypatch)
    try:
        service.opening_sync()
        before = refs(service.storage)
        failed = replace(snapshot, rights=(), status="failed", coverage="unknown")
        session.rights_observation_snapshot = lambda _: failed
        outcome = service.opening_sync()
        own = next(x for x in outcome["identities"] if x["identity_id"] == "default")
        assert own["canonical_rights"]["observed"] == 0
        assert own["status"] in ("partial", "failed")
        assert refs(service.storage) == before
        assert service.storage.acquisitions() == []
    finally:
        service.close()
