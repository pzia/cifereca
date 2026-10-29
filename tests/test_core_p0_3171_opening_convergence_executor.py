"""#3171: opening Sync executes SAME canonical A→B path as explicit Sync.

Real LibraryService, SQLite v14, file bytes and shared InterProcessLock.
Only synthetic authenticated sessions, no provider HTTP or live ADEPT.
The 29 October journal is the prioritization corpus, not a license to
guess rights or retry a one-use ACSM.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.convergence_execution import CompletedDelivery
from cifereca.core.source_acquisition_consent import set_source_acquisition_consent
from tests.test_core_p0_3171_opening_canonical_rights import rig
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA


def _lock_paths(monkeypatch, tmp_path):
    path = tmp_path / "private" / "core-acquisition.lock"
    for module in (
        "cifereca.services",
        "cifereca.core.sync_coordination",
        "cifereca.core.canonical_selected_offers",
        "cifereca.core.convergence_execution",
        "cifereca.core.canonical_recovery",
        "cifereca.core.canonical_selected_delivery",
    ):
        monkeypatch.setattr(module + ".acquisition_lock_file", lambda: path)
    return path


def _selected_offer(service, *, callback=None):
    observations = []
    def selected(account, **kwargs):
        observations.append(kwargs)
        if callback is not None:
            callback()
        return replace(
            service.snapshot,
            rights=(replace(
                service.snapshot.rights[0],
                delivery="available", delivery_offer_id="synthetic-selected-offer",
            ),),
        )
    return selected, observations


def _grant(service, account, source):
    persist_attested_source_references(service.storage, service.snapshot)
    set_source_acquisition_consent(service, account, source, allowed=True)


def test_opening_consent_get_and_second_opening_are_idempotent(tmp_path, monkeypatch):
    service, account, source, snapshot, session, reads = rig(tmp_path, monkeypatch)
    _lock_paths(monkeypatch, tmp_path)
    try:
        _grant(service, account, source)
        selected, preflights = _selected_offer(service)
        session.rights_selected_delivery_offer_snapshot = selected
        gets = []
        file = service.library_dir() / "hugues" / "canonically-delivered.pdf"

        def delivered(key, offer):
            gets.append((key, offer.delivery_offer_id))
            assert key == source and file.is_relative_to(service.library_dir())
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(BYTES)
            return CompletedDelivery(
                key, offer.delivery_offer_id, file, SHA,
                "independent-synthetic-completed-delivery",
            )

        service._canonical_right_delivery = delivered
        first = service.opening_sync()
        own = next(row for row in first["identities"] if row["identity_id"] == "default")
        assert own["canonical_execution"]["downloaded"] == 1
        assert own["canonical_execution"]["failed"] == 0
        assert own["new_acquisitions"] == 1
        assert len(preflights) == len(gets) == len(reads) == 1
        assert preflights[0] == {
            "edition_id": source.edition_id, "right_id": source.right_id,
            "kind": source.kind, "branch": source.branch,
            "explicit_user_action": True,
        }
        assert file.read_bytes() == BYTES
        assert len(service.storage.placements()) == 1
        assert service.storage.placements()[0]["identity_id"] == "default"
        with service.storage._lock, service.storage._connect() as db:
            assert db.execute("SELECT count(*) FROM origin_links").fetchone()[0] == 1

        second = service.opening_sync()
        own = next(row for row in second["identities"] if row["identity_id"] == "default")
        assert own["canonical_execution"]["present_attested"] == 1
        assert own["canonical_execution"]["downloaded"] == 0
        assert own["new_acquisitions"] == 0
        assert len(preflights) == len(gets) == 1
        assert len(reads) == 2
        assert len(service.storage.placements()) == 1
    finally:
        service.close()


def test_opening_legacy_preference_without_exact_consent_never_gets(tmp_path, monkeypatch):
    service, account, source, snapshot, session, reads = rig(tmp_path, monkeypatch)
    _lock_paths(monkeypatch, tmp_path)
    try:
        service.storage.metadata_set("kobo_backup.enabled", "1")
        service.storage.metadata_set("sync.source.materialization.on_start.any", "1")
        session.rights_selected_delivery_offer_snapshot = lambda *_a, **_kw: pytest.fail(
            "historical preference cannot authorize an offer GET"
        )
        service._canonical_right_delivery = lambda *_a, **_kw: pytest.fail(
            "no persisted SourceKey consent: no provider delivery"
        )
        result = service.opening_sync()
        own = next(row for row in result["identities"] if row["identity_id"] == "default")
        assert own["canonical_execution"]["downloaded"] == 0
        assert own["canonical_execution"]["pending"] == 1
        assert service.storage.placements() == []
        assert service.storage.acquisitions() == []
        assert len(reads) == 1
    finally:
        service.close()


def test_opening_consent_revoked_during_offer_read_never_downloads(tmp_path, monkeypatch):
    service, account, source, snapshot, session, reads = rig(tmp_path, monkeypatch)
    _lock_paths(monkeypatch, tmp_path)
    try:
        _grant(service, account, source)
        def revoke():
            set_source_acquisition_consent(service, account, source, allowed=False)
        session.rights_selected_delivery_offer_snapshot, offers = _selected_offer(
            service, callback=revoke,
        )
        service._canonical_right_delivery = lambda *_a, **_kw: pytest.fail(
            "revoked consent cannot issue a provider GET"
        )
        result = service.opening_sync()
        own = next(row for row in result["identities"] if row["identity_id"] == "default")
        assert own["canonical_execution"]["downloaded"] == 0
        assert own["canonical_execution"]["pending"] >= 1
        assert len(offers) == 1
        assert service.storage.placements() == []
        with service.storage._lock, service.storage._connect() as db:
            assert db.execute("SELECT count(*) FROM source_references").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM origin_links").fetchone()[0] == 0
    finally:
        service.close()


def test_opening_standalone_locked_method_does_not_invent_lease(tmp_path, monkeypatch):
    service, account, source, snapshot, session, reads = rig(tmp_path, monkeypatch)
    _lock_paths(monkeypatch, tmp_path)
    try:
        _grant(service, account, source)
        session.rights_selected_delivery_offer_snapshot = lambda *_a, **_kw: pytest.fail(
            "without the outer lease there is no selected offer"
        )
        service._canonical_right_delivery = lambda *_a, **_kw: pytest.fail(
            "standalone locked implementation is not an acquisition authorization"
        )
        result = service._opening_sync_locked()
        own = next(row for row in result["identities"] if row["identity_id"] == "default")
        assert "canonical_execution" not in own
        assert service.storage.placements() == []
    finally:
        service.close()
