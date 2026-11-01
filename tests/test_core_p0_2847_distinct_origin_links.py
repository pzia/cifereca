"""P0 #2847: one OriginLink spanning N placements is still one origin.

Synthetic SQLite v14. Never moves bytes or changes provenance while auditing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.core.acquisition_audit import inspect_exact_source, safe_acquisition_audit
from tests.test_core_p0_3171_atomic_publication_v14 import store
from tests.test_core_p0_2847_audit_multiple_placements import pair


def _read(pair, *, verify=True):
    storage, root, _first, _second, account = pair
    return inspect_exact_source(
        storage, account, library_root=root, verify_files=verify,
        pseudonym_key=b"synthetic-audit-secret-0123456",
    )


@pytest.mark.parametrize("missing", (None, "first", "second"))
def test_one_source_origin_counts_once_with_two_placements(pair, missing):
    storage, _root, first, second, _account = pair
    if missing == "first":
        first.unlink()
    elif missing == "second":
        second.unlink()
    before = storage.placements()
    result = _read(pair)
    row, = result["rights"]
    with storage._lock, storage._connect() as db:
        actual, = db.execute(
            "SELECT COUNT(DISTINCT origin_link_id) FROM origin_links "
            "WHERE source_reference_id=?",
            (db.execute("SELECT source_reference_id FROM source_references")
             .fetchone()[0],),
        ).fetchone()
    assert actual == 1
    assert row["origin_link_count"] == actual
    assert row["placement_count"] == 2
    assert row["classification"] == "ambiguous_match"
    assert row["file_verification"] == (
        "multiple_placements_all_verified" if missing is None
        else "multiple_placements_mixed"
    )
    safe = safe_acquisition_audit(result)
    assert safe["rights"][0]["origin_link_count"] == 1
    assert safe["rights"][0]["placement_count"] == 2
    assert storage.placements() == before


def test_two_independent_origins_do_not_cross_multiply_two_placements(pair):
    storage, _root, _first, _second, _account = pair
    with storage._lock, storage._connect() as db:
        link = db.execute(
            "SELECT identity_id,artifact_id,source_reference_id,created_at,updated_at "
            "FROM origin_links WHERE origin_kind='provider'"
        ).fetchone()
        assert link is not None
        db.execute(
            "INSERT INTO origin_links("
            "origin_link_id,origin_kind,identity_id,artifact_id,"
            "source_reference_id,evidence_ref,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            ("synthetic-second-adept-link", "adept", *link[:3],
             "synthetic-adept-receipt", *link[3:]),
        )
    result = _read(pair)
    row, = result["rights"]
    assert row["origin_link_count"] == 2  # NOT four join rows
    assert row["placement_count"] == 2
    assert row["classification"] == "ambiguous_match"
    assert row["verified_placement_count"] == 2
    assert safe_acquisition_audit(result)["rights"][0]["origin_link_count"] == 2


def test_metadata_only_still_distinguishes_one_origin_and_two_paths(pair):
    result = _read(pair, verify=False)
    row, = result["rights"]
    assert row["origin_link_count"] == 1
    assert row["placement_count"] == 2
    assert row["file_verification"] == "not_requested"
    assert "verified_placement_count" not in row
