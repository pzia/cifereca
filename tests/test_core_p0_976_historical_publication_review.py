"""Synthetic scope and publication record tests; no live ADEPT or provider."""
from dataclasses import replace

import pytest

from cifereca.core.adept_historical_publication_review import review_legacy_adept_publication
from tests.test_core_p0_3171_atomic_publication_v14 import (
    store, register, new_source, target, publish,
)


def _setup(store, tmp_path, *, adept=True):
    source, snapshot = new_source(kind="loan", branch="loans", right="loan-1")
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    published = publish(
        store, tmp_path / "EBooks", path, source, snapshot,
        adept_receipt_ref="private-adept-receipt" if adept else "",
    )
    return source, path, published


def test_recorded_present_is_not_proof_of_bytes_or_rights(store, tmp_path):
    source, path, published = _setup(store, tmp_path)
    path.unlink()  # Deliberately leave recorded state stale.
    result = review_legacy_adept_publication(store, source=source)
    assert result == {
        "source_reference_record": "recorded",
        "canonical_origin_records": "provider_and_adept_recorded",
        "canonical_placement_state": "present_recorded",
        "physical_bytes_attested": False,
        "provider_loan_state": "not_checked",
        "activation_attested": False,
        "may_rebind_or_return": False,
        "may_reacquire": False,
    }
    assert published.artifact_id not in repr(result)
    assert "private-adept-receipt" not in repr(result)
    assert store.placement_for_path(path)["state"] == "present"
    assert not path.exists()


@pytest.mark.parametrize("change", ["branch", "right_id", "edition_id", "identity_id", "credential_id", "account_id"])
def test_nearby_reference_is_not_exact(store, tmp_path, change):
    source, _, _ = _setup(store, tmp_path)
    if change in ("identity_id", "credential_id", "account_id"):
        other = replace(source, scope=replace(source.scope, **{change: "other"}))
    else:
        other = replace(source, **{change: "other"})
    result = review_legacy_adept_publication(store, source=other)
    assert result["source_reference_record"] == "not_recorded"
    assert result["canonical_origin_records"] == "not_checked"


def test_placement_in_another_identity_is_not_local(store, tmp_path):
    source, path, _ = _setup(store, tmp_path)
    register(store, "B", "seller", "card-B", account="buyer-B")
    with store._lock, store._connect() as db:
        db.execute("UPDATE placements SET identity_id='B' WHERE path=?", (str(path),))
    result = review_legacy_adept_publication(store, source=source)
    assert result["canonical_placement_state"] == "not_recorded_in_profile"
    assert store.placement_for_path(path)["identity_id"] == "B"


def test_provider_only_and_missing_are_independent_observations(store, tmp_path):
    source, path, _ = _setup(store, tmp_path, adept=False)
    with store._lock, store._connect() as db:
        db.execute("UPDATE placements SET state='missing' WHERE path=?", (str(path),))
    result = review_legacy_adept_publication(store, source=source)
    assert result["canonical_origin_records"] == "provider_only_recorded"
    assert result["canonical_placement_state"] == "missing_recorded"
    assert not result["activation_attested"]


def test_origin_budget_refuses_partial_report(store, tmp_path, monkeypatch):
    from cifereca.core import adept_historical_publication_review as review
    source, _, _ = _setup(store, tmp_path)
    monkeypatch.setattr(review, "_MAX_ROWS", 1)
    result = review_legacy_adept_publication(store, source=source)
    assert result["canonical_origin_records"] == "budget_exceeded"
    assert result["canonical_placement_state"] == "not_checked"
