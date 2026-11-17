"""Storage's archival acquisition-history invariants survive retirement of 0.8.

An observation is no longer a public writer of pseudo-rights or artifacts.
Existing persisted ledger rows still retain their original retry/history data.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _row():
    return {
        "acquisition_id": "archived-acquisition",
        "provider_id": "fixture-provider", "credential_id": "card-1",
        "account_id": "reader-1", "identity_id": "alice",
        "item_id": "edition-1", "title": "Original title",
        "status": "discovered", "metadata": {"candidate_label": "original"},
    }


@pytest.mark.parametrize("state", [
    "pending", "acquiring", "complete", "retryable_error", "attention_required",
])
def test_insert_only_observation_preserves_archived_ledger_state_and_history(tmp_path, state):
    storage = Storage(tmp_path / "db.sqlite3")
    try:
        row = _row()
        acquisition_id = storage.acquisition_put(row, insert_only=True)
        storage.acquisition_update_state(
            acquisition_id, state, candidate_id="original",
            increment_attempt=state != "pending",
            artifact_id="sha256:fixture" if state == "complete" else None,
            error_code="runtime_refused" if state.endswith("error") else None,
            attention_code="runtime_refused" if state == "attention_required" else None,
        )
        before = storage.acquisition_get(acquisition_id)
        assert before is not None
        repeated_id = storage.acquisition_put({
            **row, "title": "New title", "metadata": {"candidate_label": "new"},
        }, insert_only=True)
        assert repeated_id == acquisition_id
        assert storage.acquisition_get(acquisition_id) == before
        assert len(storage.acquisitions(provider_id="fixture-provider")) == 1
        assert storage.entitlements() == []
    finally:
        storage.close()


def test_storage_upsert_remains_explicitly_mutable(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    try:
        row = {"acquisition_id": "acq-1", "provider_id": "test",
               "credential_id": "credential", "item_id": "book-1",
               "status": "retryable_error", "metadata": {"history": "kept"}}
        storage.acquisition_put(row)
        storage.acquisition_put({**row, "status": "attention_required",
                                 "metadata": {"history": "changed"}})
        result = storage.acquisition_get("acq-1")
        assert result is not None
        assert result["status"] == "attention_required"
        assert result["metadata"]["history"] == "changed"
    finally:
        storage.close()


def test_existing_ledger_can_recover_and_retry_without_a_new_right(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    try:
        row = _row()
        acquisition_id = storage.acquisition_put(row)
        storage.acquisition_update_state(
            acquisition_id, "acquiring", increment_attempt=True,
            candidate_id="first",
        )
        assert storage.acquisition_recover_interrupted() == 1
        assert storage.acquisition_put(row, insert_only=True) == acquisition_id
        assert storage.acquisition_get(acquisition_id)["status"] == "retryable_error"
        storage.acquisition_update_state(
            acquisition_id, "acquiring", candidate_id="second",
            increment_attempt=True,
        )
        current = storage.acquisition_get(acquisition_id)
        assert current["status"] == "acquiring"
        assert current["candidate_id"] == "second"
        assert current["attempt_count"] == 2
        assert storage.entitlements() == []
    finally:
        storage.close()


def test_old_offer_planner_and_right_writer_are_not_shipped():
    from cifereca import acquisition_engine
    from cifereca.plugin_api import AcquisitionOffer
    # The public plugin offer contract is independent of the retired
    # historical Core model that carried an Entitlement.
    for symbol in ("AcquisitionCandidate", "AcquisitionOffer", "AcquisitionPlan",
                   "AcquisitionPolicy", "AcquisitionPlanner", "AcquisitionLedger",
                   "AcquisitionExecutor"):
        assert not hasattr(acquisition_engine, symbol), symbol
    assert AcquisitionOffer.__module__ != acquisition_engine.__name__
