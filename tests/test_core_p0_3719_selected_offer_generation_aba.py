"""#3719: selected-offer private reads honor the source policy generation.

SQLite v14 and a synthetic session. No provider network or delivery GET.
"""
from __future__ import annotations

from dataclasses import replace
import json
import secrets

from cifereca.core.canonical_selected_offers import qualify_selected_offers
from cifereca.core.source_sync_consent import policy_key
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_canonical_sync_stage import rig


def _policy(storage, scope, enabled):
    revision = secrets.token_hex(16)
    payload = json.dumps({
        "schema": "cifereca.source-sync-consent/1",
        "scope": [scope.identity_id, scope.provider_id,
                  scope.credential_id, scope.account_id],
        "enabled": enabled, "revision": revision,
    })
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO metadata(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (policy_key(scope), payload),
        )
    return revision


def _run(service, root, snapshot, revision, monkeypatch):
    lock_path = root.parent / "state" / "acquisition.lock"
    monkeypatch.setattr(
        "cifereca.core.canonical_selected_offers.acquisition_lock_file",
        lambda: lock_path,
    )
    with InterProcessLock(lock_path) as lock:
        return qualify_selected_offers(
            service, service.account, service.session, snapshot,
            admitted_epoch=service._source_context_epoch,
            shared_lock=lock, library_root=root, persisted_consent=None,
            expected_source_policy_revision=revision,
            # Simulate a previously captured or boolean-only consent reader:
            # the Core must independently check the persisted generation.
            consent_still_current=lambda _source: True,
        )


def _two_resources(service):
    initial = service.snapshot.rights[0]
    sibling = replace(initial, edition_id="another-edition",
                      right_id="another-right")
    return replace(service.snapshot, rights=(initial, sibling))


def _selected(snapshot, resource_id):
    row = next(right for right in snapshot.rights
               if right.right_id == resource_id)
    return replace(
        snapshot,
        rights=(replace(row, delivery="available",
                        delivery_offer_id="selected-" + resource_id),),
        status="partial", coverage="partial",
    )


def test_matching_generation_selects_two_independent_offers(rig, monkeypatch):
    service, storage, root = rig
    snapshot = _two_resources(service)
    revision = _policy(storage, snapshot.scope, True)
    reads = []

    def select(_account, **kwargs):
        reads.append(kwargs["right_id"])
        return _selected(snapshot, kwargs["right_id"])

    service.session.rights_selected_delivery_offer_snapshot = select
    result = _run(service, root, snapshot, revision, monkeypatch)
    assert reads == [r.right_id for r in snapshot.rights]
    assert [r.delivery for r in result.rights] == ["available", "available"]
    assert storage.placements() == []


def test_aba_during_first_selected_offer_blocks_it_and_remaining_private_reads(
    rig, monkeypatch,
):
    service, storage, root = rig
    snapshot = _two_resources(service)
    admitted_revision = _policy(storage, snapshot.scope, True)
    reads = []

    def select(_account, **kwargs):
        reads.append(kwargs["right_id"])
        _policy(storage, snapshot.scope, False)
        _policy(storage, snapshot.scope, True)
        return _selected(snapshot, kwargs["right_id"])

    service.session.rights_selected_delivery_offer_snapshot = select
    result = _run(service, root, snapshot, admitted_revision, monkeypatch)
    assert reads == [snapshot.rights[0].right_id]
    assert result == snapshot
    assert storage.placements() == []


def test_revoked_and_reenabled_before_preflight_never_reads_provider(
    rig, monkeypatch,
):
    service, storage, root = rig
    snapshot = _two_resources(service)
    admitted_revision = _policy(storage, snapshot.scope, True)
    _policy(storage, snapshot.scope, False)
    _policy(storage, snapshot.scope, True)
    service.session.rights_selected_delivery_offer_snapshot = (
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale generation must not read provider")
        )
    )
    assert _run(service, root, snapshot, admitted_revision, monkeypatch) == snapshot
    assert storage.placements() == []
