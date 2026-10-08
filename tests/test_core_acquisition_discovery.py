from __future__ import annotations

import pytest

from cifereca.acquisition_engine import (
    AcquisitionCandidate,
    AcquisitionLedger,
    AcquisitionOffer,
    AcquisitionPlanner,
    Artifact,
    Entitlement,
    SourceIdentity,
)
from cifereca.storage import Storage


def _offer(*, label: str) -> AcquisitionOffer:
    return AcquisitionOffer(
        entitlement=Entitlement(SourceIdentity("kobo", "vault-a"), "book-1"),
        candidates=(
            AcquisitionCandidate(
                candidate_id=label,
                kind="http",
                handling="http_plain",
                media_format="epub",
                url="https://cdn.example/book.epub",
            ),
        ),
        metadata={"candidate_label": label},
    )


@pytest.mark.parametrize("state", ["pending", "acquiring", "complete", "retryable_error", "attention_required"])
def test_rediscovery_preserves_existing_ledger_state_and_history(tmp_path, state):
    storage = Storage(tmp_path / "db.sqlite3")
    ledger = AcquisitionLedger(storage)
    initial = _offer(label="original")
    acquisition_id = ledger.discover(initial, title="Original title")
    plan = AcquisitionPlanner().plan(initial)
    assert plan is not None
    ledger.set_plan(acquisition_id, plan)
    if state in {"acquiring", "complete", "retryable_error", "attention_required"}:
        ledger.begin(acquisition_id)
    if state == "complete":
        ledger.complete(
            acquisition_id,
            Artifact(
                artifact_id="sha256:test-1",
                sha256="test-1",
                size=42,
                media_format="epub",
            ),
        )
    elif state in {"retryable_error", "attention_required"}:
        ledger.fail(
            acquisition_id,
            code="runtime_refused",
            message="Previous failure",
            retryable=state == "retryable_error",
        )

    before = storage.acquisition_get(acquisition_id)
    assert before is not None
    rediscovered_id = ledger.discover(_offer(label="new"), title="New title")
    after = storage.acquisition_get(acquisition_id)

    assert rediscovered_id == acquisition_id
    assert after == before
    assert len(storage.acquisitions(provider_id="kobo")) == 1


def test_storage_upsert_remains_explicitly_mutable(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    row = {"acquisition_id": "acq-1", "provider_id": "test",
           "credential_id": "credential", "item_id": "book-1",
           "status": "retryable_error", "metadata": {"history": "kept"}}
    storage.acquisition_put(row)
    storage.acquisition_put({**row, "status": "attention_required", "metadata": {"history": "changed"}})
    result = storage.acquisition_get("acq-1")
    assert result is not None
    assert result["status"] == "attention_required"
    assert result["metadata"]["history"] == "changed"


def test_explicit_new_plan_after_rediscovery_can_start_new_attempt(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    ledger = AcquisitionLedger(storage)
    first_offer = _offer(label="first")
    acquisition_id = ledger.discover(first_offer)
    first_plan = AcquisitionPlanner().plan(first_offer)
    assert first_plan is not None
    ledger.set_plan(acquisition_id, first_plan)
    ledger.begin(acquisition_id)
    ledger.fail(acquisition_id, code="timeout", message="Retry me", retryable=True)

    new_offer = _offer(label="second")
    assert ledger.discover(new_offer) == acquisition_id
    assert storage.acquisition_get(acquisition_id)["status"] == "retryable_error"
    new_plan = AcquisitionPlanner().plan(new_offer)
    assert new_plan is not None
    ledger.set_plan(acquisition_id, new_plan)
    ledger.begin(acquisition_id)
    current = storage.acquisition_get(acquisition_id)
    assert current is not None
    assert current["status"] == "acquiring"
    assert current["candidate_id"] == "second"
    assert current["attempt_count"] == 2
