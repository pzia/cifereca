"""#2847: bounded exact-source provenance/placement audit without N×M join.

SQLite v14 and synthetic file bytes only. No provider I/O or mutations during
inspection, and no unbounded filesystem hashes on truncated evidence.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core import acquisition_audit as audit
from cifereca.core.acquisition_audit import inspect_exact_source, safe_acquisition_audit
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA, store, target
from tests.test_core_p0_2847_audit_multiple_placements import pair


def _more_origins(db, amount):
    with db._lock, db._connect() as sql:
        owner, artifact, source, created, updated = sql.execute(
            "SELECT identity_id,artifact_id,source_reference_id,"
            "created_at,updated_at FROM origin_links LIMIT 1"
        ).fetchone()
        for index in range(amount):
            sql.execute(
                "INSERT INTO origin_links(origin_link_id,origin_kind,identity_id,"
                "artifact_id,source_reference_id,evidence_ref,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (f"distinct-proof-{index}", "adept", owner, artifact, source,
                 f"evidence-{index}", created, updated),
            )


def _more_placements(case, amount):
    storage, root, _first, _second, _account = case
    for index in range(amount):
        path = target(root.parent, "A", f"extra-copy-{index}.pdf")
        assert path.read_bytes() == BYTES
        storage.placement_put({
            "artifact_id": "sha256:" + SHA, "identity_id": "A",
            "path": str(path), "provider_id": "seller",
            "source_item_id": "edition", "state": "present", "metadata": {},
        })


def _inspect(case, *, verify=True):
    storage, root, _first, _second, account = case
    result = inspect_exact_source(
        storage, account, library_root=root, verify_files=verify,
        pseudonym_key=b"synthetic-private-audit-secret",
    )
    public = safe_acquisition_audit(result)
    row, = public["rights"]
    return result, public, row


@pytest.mark.parametrize("extra_origins,limit,expected_origins,truncated", [
    (0, 3, 1, False),
    (2, 3, 3, False),
    (3, 3, 3, True),
])
def test_distinct_origin_bound_does_not_reduce_physical_coverage(
    pair, monkeypatch, extra_origins, limit, expected_origins, truncated,
):
    storage, root, first, second, _account = pair
    _more_origins(storage, extra_origins)
    monkeypatch.setattr(audit, "_MAX_ROWS", limit)
    before = storage.placements()
    result, safe, row = _inspect(pair)
    assert row["origin_link_count"] == expected_origins
    assert row["placement_count"] == 2
    assert row["file_verification"] == "multiple_placements_all_verified"
    assert row["verified_placement_count"] == 2
    assert row["classification"] == "ambiguous_match"
    assert result["truncated"] is truncated
    assert safe["truncated"] is truncated
    assert str(first) not in json.dumps(safe)
    assert str(second) not in json.dumps(safe)
    assert storage.placements() == before


@pytest.mark.parametrize("verify", [False, True])
def test_overflow_placements_never_hash_partial_prefix_or_claim_all_verified(
    pair, monkeypatch, verify,
):
    storage, root, first, second, _account = pair
    _more_origins(storage, 2)
    _more_placements(pair, 2)  # 3 origins × 4 placements, cap = 3.
    monkeypatch.setattr(audit, "_MAX_ROWS", 3)
    if verify:
        monkeypatch.setattr(
            audit, "_file_proof",
            lambda **_kw: pytest.fail("unbounded or partial file hashing"),
        )
    before = storage.placements()
    result, safe, row = _inspect(pair, verify=verify)
    assert result["truncated"] is True
    assert safe["truncated"] is True
    assert row["origin_link_count"] == 3
    assert row["placement_count"] == 3
    assert row["classification"] == "ambiguous_match"
    if verify:
        assert row["file_verification"] == "multiple_placements_unverified"
        assert row["verified_placement_count"] == 0
        assert row["missing_placement_count"] == 0
        assert row["unverified_placement_count"] == 3
    else:
        assert row["file_verification"] == "not_requested"
        assert "verified_placement_count" not in row
    assert storage.placements() == before


def test_no_origin_never_fabricates_placement_even_for_matching_sha(
    pair, monkeypatch,
):
    storage, root, first, second, account = pair
    with storage._lock, storage._connect() as sql:
        sql.execute("DELETE FROM origin_links")
    monkeypatch.setattr(audit, "_MAX_ROWS", 3)
    result, safe, row = _inspect(pair)
    assert row["classification"] == "source_unlinked"
    assert row["origin_link_count"] == 0
    assert row["placement_count"] == 0
    assert row["file_verification"] == "not_requested"
    assert safe["truncated"] is False
    assert first.is_file() and second.is_file()
