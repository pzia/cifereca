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
