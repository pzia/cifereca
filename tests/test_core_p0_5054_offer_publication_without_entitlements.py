"""P0 #5054: a completed selected offer can publish B without pseudo-rights.

Only trusted Core code may pass an explicit once-confirmed click. This test
exercises the real SQLite publisher, verified final bytes, and its independent
exact-source completed-delivery journal. No supplier I/O or fake rights.
"""
from dataclasses import replace
import json

import pytest

from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.canonical_recovery import _SCHEMA, _STAGED, _scope, _stage_id
from tests.test_core_p0_3171_atomic_publication_v14 import (
    SHA, new_source, register, rows, target,
)


@pytest.fixture
def prepared(tmp_path):
    from cifereca.storage import Storage

    storage = Storage(tmp_path / "state.sqlite")
    key, snapshot = new_source()
    offer = "offer:current"
    snapshot = replace(snapshot, rights=(
        replace(snapshot.rights[0],
                delivery="available", delivery_offer_id=offer),
    ))
    register(storage, "A", "seller", "card-A")
    root = tmp_path / "EBooks"
    path = target(tmp_path, "A")
    receipt = "synthetic-completed-delivery"

    def stage(*, source=key, staged_offer=offer, staged_path=path,
              digest=SHA, proof=receipt, state=_STAGED):
        payload = json.dumps({
            "schema": _SCHEMA, "source": _scope(source),
            "offer_id": staged_offer, "path": str(staged_path),
            "sha256": digest, "receipt": proof, "adept_receipt": "",
        })
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,identity_id,provider_id,credential_id,"
                "account_id,item_id,status,metadata_json,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (_stage_id(source, staged_offer), source.scope.identity_id,
                 source.scope.provider_id, source.scope.credential_id,
                 source.scope.account_id, source.edition_id,
                 state, payload, 1, 1),
            )

    def publish(**kw):
        return publish_completed_acquisition(
            storage, library_root=root, path=path,
            source=key, snapshot=snapshot, final_sha256=SHA,
            delivery_receipt_ref=receipt,
            explicitly_confirmed_once=True, explicit_offer_id=offer,
            explicit_staged_path=path, transaction_guard=lambda _db: True,
            **kw,
        )
    try:
        yield storage, key, snapshot, offer, root, path, receipt, stage, publish
    finally:
        storage.close()


def test_one_current_staged_offer_publishes_origin_without_entitlement(prepared):
    store, _key, _snap, _offer, _root, path, _receipt, stage, publish = prepared
    assert store.entitlements() == []
    stage()
    result = publish()
    assert result.changed
    assert result.artifact_id == "sha256:" + SHA
    assert rows(store) == (1, 1, 1, 1)
    assert store.entitlements() == []
    assert store.placement_for_path(path)["artifact_id"] == result.artifact_id


@pytest.mark.parametrize("failure", [
    "missing_stage", "wrong_sha", "foreign_receipt", "wrong_path",
    "complete_replay", "untrusted_metadata",
])
def test_ambiguous_or_replayed_delivery_does_not_create_b(prepared, failure):
    store, key, snap, offer, root, path, receipt, stage, publish = prepared
    if failure != "missing_stage":
        stage(
            digest="f" * 64 if failure == "wrong_sha" else SHA,
            proof="foreign-receipt" if failure == "foreign_receipt" else receipt,
            staged_path=root / "A" / "foreign.epub" if failure == "wrong_path" else path,
            state="complete" if failure == "complete_replay" else _STAGED,
        )
    if failure == "untrusted_metadata":
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET metadata_json='{}'"
            )
    with pytest.raises(ValueError, match="canonical_explicit_offer_stage_unproven"):
        publish()
    assert rows(store) == (0, 0, 0, 0)
    assert store.entitlements() == []


def test_cannot_use_offer_without_transaction_guard_or_explicit_click(prepared):
    store, key, snap, offer, root, path, receipt, stage, _publish = prepared
    stage()
    for options in (
        {"explicitly_confirmed_once": False,
         "explicit_offer_id": offer, "explicit_staged_path": path},
        {"explicitly_confirmed_once": True,
         "explicit_offer_id": offer, "explicit_staged_path": path},
        {"explicitly_confirmed_once": True,
         "explicit_offer_id": offer, "explicit_staged_path": path,
         "explicit_entitlement_id": "legacy:owned",
         "transaction_guard": lambda _db: True},
    ):
        with pytest.raises(ValueError, match="canonical_explicit_transaction_guard_required"):
            publish_completed_acquisition(
                store, library_root=root, path=path,
                source=key, snapshot=snap, final_sha256=SHA,
                delivery_receipt_ref=receipt, **options,
            )
    assert rows(store) == (0, 0, 0, 0)


def test_missing_current_delivery_offer_rejected_even_with_exact_stage(prepared):
    store, key, snapshot, offer, root, path, receipt, stage, _publish = prepared
    stage()
    refused = replace(snapshot, rights=(
        replace(snapshot.rights[0], delivery="unknown", delivery_offer_id=""),
    ))
    with pytest.raises(ValueError, match="canonical_explicit_selected_offer_unattested"):
        publish_completed_acquisition(
            store, library_root=root, path=path, source=key,
            snapshot=refused, final_sha256=SHA,
            delivery_receipt_ref=receipt, explicitly_confirmed_once=True,
            explicit_offer_id=offer, explicit_staged_path=path,
            transaction_guard=lambda _db: True,
        )
    assert rows(store) == (0, 0, 0, 0)
