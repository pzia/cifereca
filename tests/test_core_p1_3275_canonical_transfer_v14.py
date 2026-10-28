"""#3275/#3171: A→B→C one-time physical transfers preserve 8 independent origins.

A/B purchases and ADEPT receipts are synthetic; three real PDF files and
SQLite schema v14 validate two transfers, SHA dedupe, rollback and provenance.
No provider network, DRM, or automated rights inheritance.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from cifereca.core.canonical_transfer import publish_completed_local_transfer
from cifereca.locking import InterProcessLock
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, publish, register, target,
)


@pytest.fixture
def case(tmp_path, monkeypatch):
    db = Storage(tmp_path / "store.sqlite")
    monkeypatch.setattr(
        "cifereca.core.canonical_transfer.acquisition_lock_file",
        lambda: tmp_path / "shared-acquisition.lock",
    )
    register(db, "A", "seller-a", "card-a", account="buyer-a")
    register(db, "B", "seller-b", "card-b", account="buyer-b")
    db.identity_put({
        "identity_id": "C", "directory_name": "C", "label": "Recipient C",
    })
    root = tmp_path / "EBooks"
    a = target(tmp_path, "A")
    source_a, snapshot_a = new_source(
        "A", provider="seller-a", credential="card-a", account="buyer-a",
    )
    first = publish(
        db, root, a, source_a, snapshot_a, adept_receipt_ref="adept-A",
    )
    assert len(first.origin_link_ids) == 2
    try:
        yield db, root, a
    finally:
        db.close()


def rows(db):
    with db._lock, db._connect() as connection:
        return {
            name: connection.execute(
                "SELECT count(*) FROM " + name
            ).fetchone()[0]
            for name in ("artifacts", "placements", "source_references",
                         "origin_links", "entitlements")
        }


def transfer(db, root, a, b, src, dst, *, transfer_id, authorized=True):
    with InterProcessLock(root.parent / "shared-acquisition.lock") as lease:
        return publish_completed_local_transfer(
            db, library_root=root, from_identity_id=src, to_identity_id=dst,
            from_path=a, to_path=b, transfer_id=transfer_id,
            transfer_receipt_ref="completed-copy-" + transfer_id,
            explicitly_authorized=authorized, shared_lock=lease,
        )


def test_A_to_B_purchase_B_to_C_creates_1_artifact_3_placements_8_origins(case):
    db, root, a = case
    b = target(root.parent, "B")
    c = target(root.parent, "C")
    ab = transfer(db, root, a, b, "A", "B", transfer_id="A-to-B")
    assert ab.changed and rows(db) == {
        "artifacts": 1, "placements": 2, "source_references": 2,
        "origin_links": 4, "entitlements": 0,
    }
    assert db.placement_for_path(b)["identity_id"] == "B"

    own_b, remote_b = new_source(
        "B", provider="seller-b", credential="card-b", account="buyer-b",
    )
    purchase_b = publish(
        db, root, b, own_b, remote_b, adept_receipt_ref="adept-B",
    )
    assert purchase_b.artifact_id == ab.artifact_id
    assert purchase_b.placement_id == ab.placement_id
    assert rows(db)["origin_links"] == 6

    bc = transfer(db, root, b, c, "B", "C", transfer_id="B-to-C")
    assert bc.changed
    assert rows(db) == {
        "artifacts": 1, "placements": 3, "source_references": 4,
        "origin_links": 8, "entitlements": 0,
    }
    assert db.placement_for_path(c)["identity_id"] == "C"
    assert db.placement_for_path(a)["artifact_id"] == ab.artifact_id
    assert db.placement_for_path(b)["artifact_id"] == ab.artifact_id
    assert db.placement_for_path(c)["artifact_id"] == ab.artifact_id

    with db._lock, db._connect() as connection:
        origins = connection.execute(
            "SELECT origin_kind,identity_id,from_identity_id "
            "FROM origin_links ORDER BY origin_kind,identity_id"
        ).fetchall()
        states = connection.execute(
            "SELECT identity_id,reference_kind,synchronizable "
            "FROM source_references ORDER BY identity_id,reference_kind"
        ).fetchall()
    assert sorted(x[0] for x in origins) == sorted(
        ["provider", "adept", "transfer", "cifereca",
         "provider", "adept", "transfer", "cifereca"]
    )
    assert ("transfer", "B", "A") in origins
    assert ("transfer", "C", "B") in origins
    assert ("cifereca", "B", None) in origins
    assert ("cifereca", "C", None) in origins
    assert ("C", "internal", 0) in states
    assert all(not (identity == "C" and kind == "purchase")
               for identity, kind, _ in states)

    before = rows(db)
    assert transfer(db, root, a, b, "A", "B", transfer_id="A-to-B").changed is False
    assert transfer(db, root, b, c, "B", "C", transfer_id="B-to-C").changed is False
    assert rows(db) == before

    b.unlink()
    c.unlink()
    with db._lock, db._connect() as connection:
        connection.execute(
            "DELETE FROM placements WHERE identity_id IN ('B','C')"
        )
    assert rows(db) == {
        "artifacts": 1, "placements": 1, "source_references": 4,
        "origin_links": 8, "entitlements": 0,
    }
    assert a.read_bytes() == BYTES


def test_transfer_not_authorized_never_writes_any_origin(case):
    db, root, a = case
    b = target(root.parent, "B")
    before = rows(db)
    with pytest.raises(ValueError, match="canonical_transfer_authorization_required"):
        transfer(db, root, a, b, "A", "B", transfer_id="A-to-B", authorized=False)
    assert rows(db) == before


def test_same_hardlink_is_not_independent_physical_copy(case):
    db, root, a = case
    b = root / "B" / "book.pdf"
    b.parent.mkdir(parents=True)
    b.hardlink_to(a)
    before = rows(db)
    with pytest.raises(ValueError, match="canonical_transfer_independent_copy_required"):
        transfer(db, root, a, b, "A", "B", transfer_id="hardlink")
    assert rows(db) == before
    assert b.read_bytes() == BYTES


@pytest.mark.parametrize("attack", ["bad_bytes", "recipient_outside_profile",
                                    "wrong_source_profile", "foreign_placement"])
def test_invalid_copy_or_profile_never_mints_transfer_right(case, attack, tmp_path):
    db, root, a = case
    b = target(root.parent, "B")
    if attack == "bad_bytes":
        b.write_bytes(b"%PDF-1.7\nDifferent PDF bytes\n")
    elif attack == "recipient_outside_profile":
        b = tmp_path / "outside.pdf"
        b.write_bytes(BYTES)
    elif attack == "wrong_source_profile":
        a = b
    elif attack == "foreign_placement":
        db.placement_put({"path": str(b), "identity_id": "C",
                          "artifact_id": "sha256:" + SHA,
                          "state": "present"})
    prior = rows(db)
    with pytest.raises(ValueError):
        transfer(db, root, a, b, "A", "B", transfer_id="adversarial")
    assert rows(db) == prior
    assert db.entitlements() == []


def test_second_origin_insert_failure_rolls_back_recipient_copy_and_source(case):
    db, root, a = case
    b = target(root.parent, "B")
    before = rows(db)
    with db._lock, db._connect() as conn:
        conn.execute(
            "CREATE TRIGGER synthetic_transfer_failure "
            "BEFORE INSERT ON origin_links WHEN NEW.origin_kind='cifereca' "
            "BEGIN SELECT RAISE(ABORT,'synthetic_transfer_crash'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic_transfer_crash"):
        transfer(db, root, a, b, "A", "B", transfer_id="rollback")
    assert rows(db) == before
    assert b.read_bytes() == BYTES
    with db._lock, db._connect() as conn:
        conn.execute("DROP TRIGGER synthetic_transfer_failure")
    assert transfer(db, root, a, b, "A", "B", transfer_id="rollback").changed
    assert rows(db)["origin_links"] == before["origin_links"] + 2


def test_reappearing_valid_independent_copy_repairs_invalid_placement_only(case):
    db, root, a = case
    b = target(root.parent, "B")
    first = transfer(db, root, a, b, "A", "B", transfer_id="same-copy")
    with db._lock, db._connect() as conn:
        conn.execute(
            "UPDATE placements SET state='invalid' WHERE placement_id=?",
            (first.placement_id,),
        )
    replay = transfer(db, root, a, b, "A", "B", transfer_id="same-copy")
    assert replay.changed
    assert replay.placement_id == first.placement_id
    assert db.placement_for_path(b)["state"] == "present"
    assert rows(db)["origin_links"] == 4


def test_two_donors_reusing_same_transfer_id_keep_distinct_contextual_origins(case):
    db, root, a = case
    b = target(root.parent, "B")
    c = target(root.parent, "C")
    db.placement_put({
        "path": str(c), "identity_id": "C",
        "artifact_id": "sha256:" + SHA, "state": "present",
    })
    first = transfer(db, root, a, b, "A", "B", transfer_id="shared-id")
    second = transfer(db, root, c, b, "C", "B", transfer_id="shared-id")
    assert first.internal_reference_id != second.internal_reference_id
    assert first.transfer_origin_id != second.transfer_origin_id
    assert first.cifereca_origin_id != second.cifereca_origin_id
    assert rows(db) == {
        "artifacts": 1, "placements": 3,
        "source_references": 3, "origin_links": 6,
        "entitlements": 0,
    }
    with db._lock, db._connect() as connection:
        transfers = connection.execute(
            "SELECT from_identity_id FROM origin_links "
            "WHERE origin_kind='transfer' AND identity_id='B' "
            "ORDER BY from_identity_id"
        ).fetchall()
    assert transfers == [("A",), ("C",)]
    assert transfer(
        db, root, a, b, "A", "B", transfer_id="shared-id",
    ).changed is False


def test_explicit_transfer_without_held_global_lock_never_writes(case):
    db, root, a = case
    b = target(root.parent, "B")
    unopened = InterProcessLock(root.parent / "shared-acquisition.lock")
    before = rows(db)
    with pytest.raises(ValueError, match="canonical_transfer_authorization_required"):
        publish_completed_local_transfer(
            db, library_root=root,
            from_identity_id="A", to_identity_id="B",
            from_path=a, to_path=b, transfer_id="unlocked",
            transfer_receipt_ref="signed-synthetic-copy",
            explicitly_authorized=True, shared_lock=unopened,
        )
    assert rows(db) == before


def test_other_lock_does_not_authorize_canonical_transfer(case):
    db, root, a = case
    b = target(root.parent, "B")
    before = rows(db)
    with InterProcessLock(root.parent / "unrelated.lock") as unrelated:
        with pytest.raises(ValueError, match="canonical_transfer_authorization_required"):
            publish_completed_local_transfer(
                db, library_root=root, from_identity_id="A",
                to_identity_id="B", from_path=a, to_path=b,
                transfer_id="wrong-lock", transfer_receipt_ref="copy-completed",
                explicitly_authorized=True, shared_lock=unrelated,
            )
    assert rows(db) == before
