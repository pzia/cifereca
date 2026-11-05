"""P0 #2847: two placements never inherit first-path filesystem evidence.

Read-only SQLite v14 proof. No provider session, GET, file repair or removal.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cifereca.core.acquisition_audit import inspect_exact_source, safe_acquisition_audit
from cifereca.models import Account
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, publish, register, store, target,
)


@pytest.fixture
def pair(store, tmp_path):
    source, snapshot = new_source()
    register(store, "A", "seller", "card-A", account="buyer-A")
    root = tmp_path / "EBooks"
    first = target(tmp_path, "A", "first.pdf")
    publish(store, root, first, source, snapshot)
    second = target(tmp_path, "A", "second.pdf")
    store.placement_put({
        "artifact_id": "sha256:" + SHA, "identity_id": "A",
        "path": str(second), "provider_id": "seller",
        "source_item_id": "edition", "state": "present", "metadata": {},
    })
    account = Account(identity_id="A", provider_id="seller",
                      credential_id="card-A", unique_id="buyer-A")
    assert len(store.placements()) == 2
    return store, root, first, second, account


def _audit(pair, verify=True):
    db, root, *_rest, account = pair
    result = inspect_exact_source(
        db, account, library_root=root, verify_files=verify,
        pseudonym_key=b"fixture-secret-not-exported",
    )
    row, = result["rights"]
    return result, row


@pytest.mark.parametrize("scenario,expected,counts", [
    ("both", "multiple_placements_all_verified", (2, 0, 0)),
    ("first_missing", "multiple_placements_mixed", (1, 1, 0)),
    ("second_missing", "multiple_placements_mixed", (1, 1, 0)),
    ("both_missing", "multiple_placements_none_verified", (0, 2, 0)),
    ("second_corrupt", "multiple_placements_mixed", (1, 0, 1)),
])
def test_each_placement_is_checked_instead_of_only_first(
    pair, scenario, expected, counts,
):
    db, root, first, second, account = pair
    if scenario in {"first_missing", "both_missing"}:
        first.unlink()
    if scenario in {"second_missing", "both_missing"}:
        second.unlink()
    if scenario == "second_corrupt":
        second.write_bytes(b"%PDF-corrupted copy not the published SHA")
    before = db.placements()
    result, row = _audit(pair)
    assert row["classification"] == "ambiguous_match"
    assert row["placement_count"] == 2
    assert row["file_verification"] == expected
    assert (
        row["verified_placement_count"],
        row["missing_placement_count"],
        row["unverified_placement_count"],
    ) == counts
    safe = safe_acquisition_audit(result)
    public, = safe["rights"]
    assert public["file_verification"] == expected
    assert (public["verified_placement_count"],
            public["missing_placement_count"],
            public["unverified_placement_count"]) == counts
    assert public["classification"] == "ambiguous_match"
    rendered = json.dumps(safe)
    assert str(first) not in rendered and str(second) not in rendered
    assert "buyer-A" not in rendered and "card-A" not in rendered
    assert db.placements() == before


def test_read_only_metadata_audit_does_not_hash_unrequested(pair, monkeypatch):
    import cifereca.core.acquisition_audit as module
    monkeypatch.setattr(module, "_file_proof", lambda **kwargs:
                        pytest.fail("unexpected file hash"))
    result, row = _audit(pair, verify=False)
    assert row["classification"] == "ambiguous_match"
    assert row["file_verification"] == "not_requested"
    assert "verified_placement_count" not in row
    safe = safe_acquisition_audit(result)
    assert "verified_placement_count" not in safe["rights"][0]


def test_safe_projection_rejects_fake_multi_path_counts(pair):
    result, row = _audit(pair)
    row["verified_placement_count"] = 999
    row["file_verification"] = "multiple_placements_all_verified"
    safe = safe_acquisition_audit(result)
    public, = safe["rights"]
    assert safe["truncated"] is True
    assert public["classification"] == "ambiguous_match"
    assert public["file_verification"] == "multiple_placements_unverified"
    assert "verified_placement_count" not in public


def test_reappeared_file_with_sqlite_missing_state_is_not_claimed_absent(pair):
    db, root, first, second, account = pair
    first.write_bytes(BYTES)
    second.write_bytes(BYTES)
    with db._lock, db._connect() as sql:
        sql.execute("UPDATE placements SET state='missing' WHERE path=?", (str(second),))
    before = db.placements()
    result, row = _audit(pair)
    assert row["classification"] == "ambiguous_match"
    assert row["file_verification"] == "multiple_placements_mixed"
    assert (
        row["verified_placement_count"], row["missing_placement_count"],
        row["unverified_placement_count"],
    ) == (1, 0, 1)
    public, = safe_acquisition_audit(result)["rights"]
    assert public["file_verification"] == "multiple_placements_mixed"
    assert public["missing_placement_count"] == 0
    assert public["unverified_placement_count"] == 1
    assert db.placements() == before  # audit did not silently repair SQLite
    assert first.read_bytes() == second.read_bytes() == BYTES


def test_missing_sqlite_row_remains_missing_when_file_is_actually_gone(pair):
    db, root, first, second, account = pair
    second.unlink()
    with db._lock, db._connect() as sql:
        sql.execute("UPDATE placements SET state='missing' WHERE path=?", (str(second),))
    before = db.placements()
    result, row = _audit(pair)
    assert row["file_verification"] == "multiple_placements_mixed"
    assert (
        row["verified_placement_count"], row["missing_placement_count"],
        row["unverified_placement_count"],
    ) == (1, 1, 0)
    assert db.placements() == before
    assert first.read_bytes() == BYTES


def test_single_reappeared_missing_state_does_not_invent_a_present_placement(pair):
    db, root, first, second, account = pair
    with db._lock, db._connect() as sql:
        sql.execute("DELETE FROM placements WHERE path=?", (str(second),))
        sql.execute("UPDATE placements SET state='missing' WHERE path=?", (str(first),))
    before = db.placements()
    assert len(before) == 1
    result, row = _audit(pair)
    assert row["classification"] == "local_unverified"
    assert row["file_verification"] == "placement_state_unverified"
    public, = safe_acquisition_audit(result)["rights"]
    assert public["file_verification"] == "placement_state_unverified"
    assert db.placements() == before
    assert first.read_bytes() == BYTES


def test_sqlite_missing_symlink_is_never_reclassified_as_restored_book(pair):
    db, root, first, second, account = pair
    # A symlink may point to perfectly matching bytes. It is never
    # physical proof for the missing historical Placement's own inode.
    second.unlink()
    second.symlink_to(first)
    with db._lock, db._connect() as sql:
        sql.execute("UPDATE placements SET state='missing' WHERE path=?", (str(second),))
    before = db.placements()
    result, row = _audit(pair)
    assert row["classification"] == "ambiguous_match"
    assert row["file_verification"] == "multiple_placements_mixed"
    assert (
        row["verified_placement_count"], row["missing_placement_count"],
        row["unverified_placement_count"],
    ) == (1, 0, 1)
    safe, = safe_acquisition_audit(result)["rights"]
    assert safe["missing_placement_count"] == 0
    assert safe["unverified_placement_count"] == 1
    assert second.is_symlink() and first.read_bytes() == BYTES
    assert db.placements() == before
