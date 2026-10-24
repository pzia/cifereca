"""Numilog P0 #1905 — no-network, no-acquisition per-source backup preview."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from cifereca.acquisition_engine import Entitlement, SourceIdentity
from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.models import Account
from tests.test_core_p0_1862_numilog_store_mirror import _connect
from tests.test_sync_state_contract import _service


def _item(credential: str, lid: str, variant: str = "a" * 16) -> str:
    scope = hashlib.sha256(credential.encode()).hexdigest()[:20]
    return f"numilog:{scope}:{lid}:{variant}"


def _owned(service, account, lid="123", *, variant="a" * 16,
           trusted=True, downloadable=None):
    item = _item(account.credential_id, lid, variant)
    source = SourceIdentity(
        "numilog", account.credential_id, account.unique_id,
        account.identity_id,
    )
    entitlement_id = Entitlement(source=source, item_id=item).entitlement_id
    metadata = {
        "source": "numilog.authenticated_library" if trusted
        else "numilog.public_catalog",
    }
    if downloadable is not None:
        metadata["downloadable"] = downloadable
    service.storage.entitlement_put({
        "entitlement_id": entitlement_id,
        "provider_id": "numilog", "identity_id": account.identity_id,
        "credential_id": account.credential_id, "account_id": account.unique_id,
        "item_id": item, "entitlement_type": "owned", "state": "active",
        "metadata": metadata,
    })
    return item, entitlement_id


def _broker(service, account):
    broker = SynchronizationBrokerImpl(service)
    return broker, broker._source_id(account)


def _preview(broker, sid, verify=False):
    return broker.preview_local_materialization(
        sid, verify_local_files=verify,
    )


def _ledger(service, account, item, entitlement_id, *, data=b"not-a-real-epub",
            wrong_scope=False, symlink=False):
    artifact_id = "sha256:" + hashlib.sha256(data).hexdigest()
    service.storage.artifact_put({
        "artifact_id": artifact_id,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data), "media_format": "epub", "kind": "file",
    })
    location = (
        service.config.get("library.directory") + "/"
        + ("alex" if wrong_scope else "hugues")
        + "/numilog/test.epub"
    )
    path = Path(location)
    path.parent.mkdir(parents=True, exist_ok=True)
    if symlink:
        external = path.parent.parent.parent.parent / "SECRET.epub"
        external.write_bytes(data)
        path.symlink_to(external)
    else:
        path.write_bytes(data)
    service.storage.placement_put({
        "artifact_id": artifact_id, "path": str(path), "state": "present",
        "identity_id": account.identity_id, "provider_id": "numilog",
        "source_item_id": item,
    })
    service.storage.acquisition_put({
        "acquisition_id": "fictional-acquisition-" + item[-16:],
        "entitlement_id": entitlement_id,
        "provider_id": "numilog", "credential_id": account.credential_id,
        "identity_id": account.identity_id, "account_id": account.unique_id,
        "item_id": item, "artifact_id": artifact_id, "status": "complete",
        "title": "Fictional",
    })
    return path


def test_empty_numilog_preview_is_offline_and_never_authorizes_backup(tmp_path):
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=1)
        session.library = lambda: pytest.fail("preview must be offline")
        session.download_direct = lambda *_a: pytest.fail("download forbidden")
        broker, sid = _broker(service, account)
        assert broker.local_materialization_schedule(sid) == "not_supported"
        first = _preview(broker, sid)
        second = _preview(broker, sid, verify=True)
        for result in (first, second):
            assert result.ok and result.changed == 0
            assert result.facts["status"] == "preview_only"
            assert result.facts["offline"] is True
            assert result.facts["read_only"] is True
            assert result.facts["manifest_refreshed"] is False
            assert result.facts["requires_manifest_validation"] is True
            assert result.facts["schedule_available"] is False
            assert result.facts["confirmation_available"] is False
            assert result.facts["acquisition_authorized"] is False
            assert result.facts["known_item_count"] == 0
            assert all(value == 0 for value in result.facts["counts"].values())
        assert calls == []
    finally:
        service.close()


def test_scoped_identity_and_variant_counts_without_network_or_persistence(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        a, a_session, a_calls = _connect(
            service, identity="default", credential="vault-a", number=0,
        )
        b, b_session, b_calls = _connect(
            service, identity="alex", credential="vault-b", number=0,
        )
        _owned(service, a, "123", variant="a" * 16)
        _owned(service, a, "123", variant="b" * 16)
        _owned(service, b, "123", variant="a" * 16)
        # A legacy/public entitlement is NEVER proof of a private purchase.
        _owned(service, a, "456", trusted=False)
        a_session.library = b_session.library = lambda: pytest.fail("remote")
        a_session.download_direct = b_session.download_direct = (
            lambda *_args: pytest.fail("no downloads")
        )
        service.current_identity = service.identity_by_id("alex")
        broker, asid = _broker(service, a)
        bsid = broker._source_id(b)
        monkeypatch.setattr(service.storage, "metadata_set",
                            lambda *_args: pytest.fail("preview writes preferences"))
        first = _preview(broker, asid)
        second = _preview(broker, bsid)
        assert first.ok and second.ok
        assert first.facts["counts"] == {
            "owned_purchases": 2, "already_local_linked": 0,
            "already_local_verified": 0, "needs_file_verification": 0,
            "potentially_missing": 2, "candidate_unverified": 2,
            "needs_reconciliation": 0, "attention_required": 0,
            "not_downloadable": 0, "ignored_unattested_rights": 1,
        }
        assert second.facts["counts"]["owned_purchases"] == 1
        assert second.facts["counts"]["potentially_missing"] == 1
        assert second.facts["counts"]["ignored_unattested_rights"] == 0
        assert a_calls == b_calls == []
        assert all(value is False for value in [
            first.facts["acquisition_authorized"],
            second.facts["acquisition_authorized"],
        ])
        for secret in ("vault-a", "vault-b", "SECRET", "123"):
            assert secret not in json.dumps(first.facts)
    finally:
        service.close()


def test_successful_ledger_is_linked_but_not_verified_until_sha_is_read(
    tmp_path,
):
    service = _service(tmp_path)
    try:
        account, session, _calls = _connect(service, number=0)
        item, eid = _owned(service, account)
        path = _ledger(service, account, item, eid)
        session.library = lambda: pytest.fail("offline preview only")
        broker, sid = _broker(service, account)

        unverified = _preview(broker, sid)
        counts = unverified.facts["counts"]
        assert counts["owned_purchases"] == 1
        assert counts["already_local_linked"] == 1
        assert counts["already_local_verified"] == 0
        assert counts["needs_file_verification"] == 1
        assert counts["potentially_missing"] == 0

        verified = _preview(broker, sid, verify=True)
        counts = verified.facts["counts"]
        assert verified.ok and verified.facts["files_verified"] is True
        assert counts["already_local_linked"] == 1
        assert counts["already_local_verified"] == 1
        assert counts["needs_file_verification"] == 0
        assert counts["needs_reconciliation"] == 0

        path.write_bytes(b"tampered after acquisition")
        damaged = _preview(broker, sid, verify=True)
        counts = damaged.facts["counts"]
        assert counts["already_local_verified"] == 0
        assert counts["already_local_linked"] == 0
        assert counts["needs_reconciliation"] == 1
        assert counts["potentially_missing"] == 0
        assert path.read_bytes() == b"tampered after acquisition"
    finally:
        service.close()


@pytest.mark.parametrize("unsafe", ["symlink", "outside_identity", "missing"])
def test_unsafe_local_evidence_never_becomes_verified_or_download_candidate(
    tmp_path, unsafe,
):
    service = _service(tmp_path)
    try:
        account, session, _calls = _connect(service, number=0)
        item, eid = _owned(service, account)
        path = _ledger(
            service, account, item, eid,
            wrong_scope=unsafe == "outside_identity",
            symlink=unsafe == "symlink",
        )
        if unsafe == "missing":
            path.unlink()
        session.library = lambda: pytest.fail("no network")
        broker, sid = _broker(service, account)
        result = _preview(broker, sid, verify=True)
        assert result.ok
        assert result.facts["counts"]["needs_reconciliation"] == 1
        assert result.facts["counts"]["already_local_verified"] == 0
        assert result.facts["counts"]["potentially_missing"] == 0
        assert str(path) not in json.dumps(result.facts)
        assert "SECRET.epub" not in json.dumps(result.facts)
    finally:
        service.close()


def test_failed_or_ambiguous_ledger_is_never_replayed_implicitly(tmp_path):
    service = _service(tmp_path)
    try:
        account, session, _calls = _connect(service, number=0)
        first, eid = _owned(service, account, "100", downloadable=False)
        pending, eid2 = _owned(service, account, "200")
        service.storage.acquisition_put({
            "acquisition_id": "fictional-failed",
            "entitlement_id": eid2, "provider_id": "numilog",
            "identity_id": account.identity_id,
            "credential_id": account.credential_id,
            "account_id": account.unique_id,
            "item_id": pending, "status": "attention_required",
        })
        session.library = lambda: pytest.fail("no network")
        broker, sid = _broker(service, account)
        result = _preview(broker, sid)
        counts = result.facts["counts"]
        assert counts["owned_purchases"] == 2
        assert counts["not_downloadable"] == 1
        assert counts["attention_required"] == 1
        assert counts["potentially_missing"] == 0
        assert counts["candidate_unverified"] == 0
        assert counts["needs_reconciliation"] == 0
    finally:
        service.close()


@pytest.mark.parametrize("state", [
    "disabled", "rebound", "orphaned", "ambiguous", "unknown_identity",
])
def test_invalid_numilog_binding_fails_closed_and_is_offline(tmp_path, state):
    service = _service(tmp_path)
    try:
        account, session, _calls = _connect(service, number=0)
        _owned(service, account, "123")
        session.library = lambda: pytest.fail("provider must never be called")
        if state == "disabled":
            service.storage.metadata_set(
                "sync.source.enabled." + SynchronizationBrokerImpl._source_id(account), "off",
            )
        elif state == "rebound":
            service.storage.provider_connection_put({
                "provider_id": "numilog", "credential_id": account.credential_id,
                "identity_id": "alex", "enabled": True,
            })
        elif state == "orphaned":
            service.accounts.clear()
        elif state == "ambiguous":
            service.accounts.append(replace(account, unique_id="second"))
        elif state == "unknown_identity":
            service.accounts[0] = replace(account, identity_id="not-configured")
        broker, sid = _broker(service, account)
        result = _preview(broker, sid)
        assert not result.ok
        assert result.facts["acquisition_authorized"] is False
        assert result.facts["status"] in {
            "source_disabled", "invalid_source", "unknown_source",
        }
        assert "counts" not in result.facts
        assert "numilog-private" not in repr(result)
    finally:
        service.close()


def test_invalid_verify_flag_and_cross_provider_are_never_permissive(tmp_path):
    service = _service(tmp_path)
    try:
        account, _session, _calls = _connect(service, number=0)
        broker, sid = _broker(service, account)
        assert _preview(broker, sid).ok
        invalid = broker.preview_local_materialization(
            sid, verify_local_files=1,
        )
        assert not invalid.ok
        assert invalid.facts["status"] == "invalid_options"
        assert invalid.facts["acquisition_authorized"] is False
        assert broker.local_materialization_schedule(sid) == "not_supported"
        # No preview token can be interpreted as a signed download capability.
        assert "confirmation_token" not in _preview(broker, sid).facts
    finally:
        service.close()


def test_directory_collision_fails_closed_before_reading_bytes(tmp_path):
    service = _service(tmp_path)
    try:
        account, session, _calls = _connect(service, number=0)
        item, eid = _owned(service, account)
        _ledger(service, account, item, eid)
        service.identities[1] = replace(
            service.identities[1], directory_name="hugues",
        )
        session.library = lambda: pytest.fail("no network")
        broker, sid = _broker(service, account)
        guarded = _preview(broker, sid, verify=True)
        assert not guarded.ok
        assert guarded.facts["status"] == "invalid_directory"
        assert guarded.facts["acquisition_authorized"] is False
    finally:
        service.close()


def test_foreign_placement_never_counts_as_linked_without_sha_opt_in(tmp_path):
    service = _service(tmp_path)
    try:
        account, _session, _calls = _connect(service, number=0)
        item, eid = _owned(service, account)
        path = _ledger(service, account, item, eid, wrong_scope=True)
        broker, sid = _broker(service, account)
        result = _preview(broker, sid, verify=False)
        assert result.ok
        assert result.facts["counts"]["already_local_linked"] == 0
        assert result.facts["counts"]["needs_reconciliation"] == 1
        assert result.facts["counts"]["potentially_missing"] == 0
        assert str(path) not in json.dumps(result.facts)
    finally:
        service.close()


def test_duplicate_completed_acquisitions_require_reconciliation_not_download(tmp_path):
    service = _service(tmp_path)
    try:
        account, _session, _calls = _connect(service, number=0)
        item, eid = _owned(service, account)
        _ledger(service, account, item, eid)
        service.storage.acquisition_put({
            "acquisition_id": "another-fictional-acquisition",
            "entitlement_id": eid,
            "provider_id": "numilog", "identity_id": account.identity_id,
            "credential_id": account.credential_id, "account_id": account.unique_id,
            "item_id": item, "status": "complete",
        })
        broker, sid = _broker(service, account)
        result = _preview(broker, sid, verify=True)
        assert result.ok
        counts = result.facts["counts"]
        assert counts["already_local_linked"] == 0
        assert counts["already_local_verified"] == 0
        assert counts["needs_reconciliation"] == 1
        assert counts["potentially_missing"] == 0
    finally:
        service.close()


@pytest.mark.parametrize("fake_right", ["expired", "loan"])
def test_unattested_loan_or_expired_entitlement_is_not_a_purchase(
    tmp_path, fake_right,
):
    service = _service(tmp_path)
    try:
        account, _session, _calls = _connect(service, number=0)
        item, eid = _owned(service, account)
        # The read-only preview must recognize legacy inconsistent rows.
        # A normal Core writer must no longer be able to forge an owned
        # entitlement with a loan_id; simulate that old SQLite state directly.
        if fake_right == "loan":
            with service.storage._connect() as db:
                db.execute(
                    "UPDATE entitlements SET loan_id=? WHERE entitlement_id=?",
                    ("borrower-loan", eid),
                )
        else:
            service.storage.entitlement_put({
                "entitlement_id": eid, "provider_id": "numilog",
                "credential_id": account.credential_id,
                "account_id": account.unique_id,
                "identity_id": account.identity_id,
                "item_id": item, "entitlement_type": "owned", "state": "active",
                "expires_at": "2099-01-01T00:00:00Z",
                "metadata": {"source": "numilog.authenticated_library"},
            })
        broker, sid = _broker(service, account)
        result = _preview(broker, sid)
        assert result.ok
        assert result.facts["known_item_count"] == 0
        assert result.facts["counts"]["ignored_unattested_rights"] == 1
        assert result.facts["counts"]["potentially_missing"] == 0
    finally:
        service.close()


def test_preview_never_creates_configured_library_or_identity_directories(tmp_path):
    service = _service(tmp_path)
    try:
        account, _session, _calls = _connect(service, number=0)
        absent = tmp_path / "not-created-by-preview"
        service.config.set("library.directory", str(absent))
        assert not absent.exists()
        broker, sid = _broker(service, account)
        assert _preview(broker, sid, verify=False).ok
        assert _preview(broker, sid, verify=True).ok
        assert not absent.exists()
    finally:
        service.close()


def test_artifact_identifier_must_match_verified_digest(tmp_path):
    """A corrupted stored hash must not become a proven local copy."""
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=0)
        item, entitlement_id = _owned(service, account)
        _ledger(service, account, item, entitlement_id)
        # Storage enforces UNIQUE(sha256): deliberately corrupt the one
        # existing artifact row instead of inserting an impossible duplicate.
        # The ledger and placement still point at that artifact identifier.
        with service.storage._lock, service.storage._connect() as db:
            row = db.execute("SELECT artifact_id, sha256 FROM artifacts").fetchone()
            assert row is not None
            artifact_id, digest = row
            bogus_sha = ("f" if digest[0] != "f" else "e") + digest[1:]
            db.execute(
                "UPDATE artifacts SET sha256=? WHERE artifact_id=?",
                (bogus_sha, artifact_id),
            )
        session.library = lambda: pytest.fail("preview must stay offline")
        broker, sid = _broker(service, account)
        result = _preview(broker, sid, verify=True)
        assert result.ok
        assert result.facts["counts"]["already_local_verified"] == 0
        assert result.facts["counts"]["needs_reconciliation"] == 1
        assert result.facts["acquisition_authorized"] is False
        assert calls == []
    finally:
        service.close()

@pytest.mark.parametrize("media_format,kind,expected_verified", [
    ("acsm", "file", False),
    ("html", "file", False),
    ("unknown", "file", False),
    ("epub", "bundle", False),
    ("epub", "file", True),
    ("pdf", "file", True),
    ("EPUB", "file", True),
])
def test_non_book_artifacts_are_not_proven_numilog_backup(
    tmp_path, media_format, kind, expected_verified,
):
    """A signed ACSM or a bundle with a good hash is not a local ebook."""
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=0)
        item, entitlement_id = _owned(service, account)
        path = _ledger(service, account, item, entitlement_id)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        service.storage.artifact_put({
            "artifact_id": "sha256:" + digest,
            "sha256": digest, "size": path.stat().st_size,
            "media_format": media_format, "kind": kind,
        })
        session.library = lambda: pytest.fail("offline preview must not fetch")
        session.download_direct = lambda *_a: pytest.fail("preview must not acquire")
        broker, sid = _broker(service, account)
        result = _preview(broker, sid, verify=True)
        assert result.ok and result.changed == 0
        counts = result.facts["counts"]
        assert counts["owned_purchases"] == 1
        assert counts["already_local_verified"] == int(expected_verified)
        assert counts["needs_reconciliation"] == int(not expected_verified)
        assert counts["potentially_missing"] == 0
        assert result.facts["acquisition_authorized"] is False
        assert calls == []
    finally:
        service.close()
