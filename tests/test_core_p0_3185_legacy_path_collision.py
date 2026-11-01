"""P0 #3185: source-bound receipts cannot hide foreign references to one path.

Synthetic SQLite and PDF; this is read-only and never authorizes unlink.
"""
import json

import pytest

from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows, store
from tests.test_core_p0_3185_canonical_backup_relink import rig
from tests.test_core_p0_3185_legacy_scope_review import _mark_legacy, _review
from tests.test_core_p0_3719_prepare_duplicate import _journal


def _insert_other_ledger(storage, *, path, owner, raw=None):
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO acquisition_ledger("
            "acquisition_id,provider_id,credential_id,account_id,item_id,"
            "identity_id,status,artifact_id,metadata_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("other-path-owner", "unrelated-provider", "another-card",
             "other-buyer", "foreign-book", owner, "retryable_error",
             None, json.dumps({"legacy_path": path}) if raw is None else raw,
             1, 1),
        )


@pytest.mark.parametrize("variant", [
    "another_account", "foreign_profile", "escaped_path", "nested_path",
    "different_status", "ambiguous_json",
])
def test_any_foreign_ledger_path_reference_blocks_positive_legacy_binding(
    rig, variant,
):
    storage, _root, paths, publications, _lock = rig
    _mark_legacy(rig)
    canonical_id = _journal(rig)
    path = str(paths[1])
    raw = None
    if variant == "escaped_path":
        raw = json.dumps({"path": path}).replace("/", chr(92) + "/")
    elif variant == "nested_path":
        raw = json.dumps({"history": {"old": [path]}})
    elif variant == "different_status":
        raw = json.dumps({"path": path, "schema": "other-legacy"})
    elif variant == "ambiguous_json":
        raw = json.dumps({"path": path})[:-1] + ',"path":"not-this-path"}'
    _insert_other_ledger(
        storage, path=path,
        owner="B" if variant == "foreign_profile" else "A", raw=raw,
    )
    before = rows(storage)
    result = _review(rig)
    assert result.reason == "legacy_receipt_unverified"
    assert result.status == "blocked"
    assert result.completed_path_receipts == 0
    assert result.may_migrate_or_retire is False
    assert storage.placement_get(publications[1].placement_id)["state"] == "present"
    assert all(p.read_bytes() == BYTES for p in paths)
    assert rows(storage) == before
    with storage._lock, storage._connect() as db:
        assert db.execute(
            "SELECT status FROM acquisition_ledger WHERE acquisition_id=?",
            (canonical_id,),
        ).fetchone() == ("complete",)


def test_unrelated_ledger_path_does_not_create_false_collision(rig):
    storage, _root, paths, _pubs, _lock = rig
    _mark_legacy(rig)
    _journal(rig)
    _insert_other_ledger(storage, path=str(paths[1]) + ".other", owner="A")
    result = _review(rig)
    assert result.reason == "completed_receipt_path_review_required"
    assert result.completed_path_receipts == 1
    assert not result.may_migrate_or_retire


@pytest.mark.parametrize("mutation", ["disabled_connection", "rebound_account",
                                      "changed_reference_branch",
                                      "changed_reference_kind"])
def test_account_or_source_reference_rebound_is_never_path_evidence(
    rig, mutation,
):
    storage, _root, paths, pubs, _lock = rig
    _mark_legacy(rig)
    _journal(rig)
    with storage._lock, storage._connect() as db:
        if mutation in ("changed_reference_branch", "changed_reference_kind"):
            # The real v14 schema makes published coordinates immutable.
            # Remove the guard ONLY in this synthetic database to simulate
            # out-of-band corruption; the read-only Core must fail closed.
            db.execute("DROP TRIGGER trg_linked_source_coords_immutable")
        if mutation == "disabled_connection":
            db.execute(
                "UPDATE provider_connections SET enabled=0 "
                "WHERE provider_id='seller-b' AND credential_id='card-b'",
            )
        elif mutation == "rebound_account":
            db.execute(
                "UPDATE provider_accounts SET account_id='other-buyer' "
                "WHERE provider_id='seller-b' AND credential_id='card-b'",
            )
        elif mutation == "changed_reference_branch":
            db.execute(
                "UPDATE source_references SET branch_id='other-branch' "
                "WHERE provider_id='seller-b' AND credential_id='card-b'",
            )
        else:
            db.execute(
                "UPDATE source_references SET reference_kind='loan' "
                "WHERE provider_id='seller-b' AND credential_id='card-b'",
            )
    result = _review(rig)
    assert result.reason == "legacy_receipt_unverified"
    assert result.completed_path_receipts == 0
    assert result.status == "blocked"
    assert not result.may_migrate_or_retire
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert all(p.read_bytes() == BYTES for p in paths)


def test_global_receipt_budget_is_not_a_positive_path_attestation(
    rig, monkeypatch,
):
    from cifereca.core import canonical_legacy_scope_review as module
    storage, _root, paths, _pubs, _lock = rig
    _mark_legacy(rig)
    _journal(rig)
    monkeypatch.setattr(module, "_MAX_LEDGER_ROWS", 0)
    result = _review(rig)
    assert result.reason == "legacy_receipt_unverified"
    assert result.completed_path_receipts == 0
    assert all(p.read_bytes() == BYTES for p in paths)
