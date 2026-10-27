"""#3196: a canonical acquisition UPSERT cannot reassign source or borrower.

SQLite-only, no provider, filesystem, ADEPT, login or downloadable content.
Old historical NULL identity may be bound once but an account ID may not be
inferred or replaced by the generic acquisition writer.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _record(**changes):
    return {
        **{
            "acquisition_id": "acq-same-id",
            "entitlement_id": "right-a",
            "identity_id": "reader-a",
            "provider_id": "kobo",
            "credential_id": "card-a",
            "account_id": "member-a",
            "item_id": "edition-1",
            "title": "Acquired title",
            "status": "complete",
            "artifact_id": "sha256:" + "a" * 64,
            "metadata": {"checkpoint": "validated"},
            "attempt_count": 2,
        },
        **changes,
    }


@pytest.mark.parametrize("change", [
    {"identity_id": "reader-b"},
    {"provider_id": "numilog"},
    {"credential_id": "card-b"},
    {"account_id": "member-b"},
    {"account_id": ""},
    {"account_id": None},
    {"item_id": "edition-2"},
    {"entitlement_id": "right-b"},
])
def test_canonical_acquisition_rejects_owner_collision_atomically(
    tmp_path, change,
):
    storage = Storage(tmp_path / "canonical.sqlite")
    try:
        first = _record()
        storage.acquisition_put(first)
        before = storage.acquisition_get(first["acquisition_id"])
        malicious = _record(
            **change,
            title="Attempted overwrite",
            status="retryable_error",
            artifact_id="sha256:" + "b" * 64,
            metadata={"checkpoint": "foreign"},
            attempt_count=200,
        )
        with pytest.raises(ValueError, match="^acquisition_source_scope_conflict$"):
            storage.acquisition_put(malicious)
        assert storage.acquisition_get(first["acquisition_id"]) == before
        assert len(storage.acquisitions()) == 1
    finally:
        storage.close()


def test_exact_owner_retry_still_updates_canonical_status_and_metadata(tmp_path):
    storage = Storage(tmp_path / "canonical.sqlite")
    try:
        storage.acquisition_put(_record(status="acquiring"))
        storage.acquisition_put(_record(
            status="complete", title="Updated same edition",
            metadata={"history": "same scope"},
        ))
        after = storage.acquisition_get("acq-same-id")
        assert after["status"] == "complete"
        assert after["title"] == "Updated same edition"
        assert after["metadata"] == {"history": "same scope"}
        assert after["account_id"] == "member-a"
        assert after["attempt_count"] == 2
    finally:
        storage.close()


def test_legacy_null_identity_can_be_bound_once_but_not_reassigned(tmp_path):
    storage = Storage(tmp_path / "canonical.sqlite")
    try:
        storage.acquisition_put(_record(identity_id=None, status="pending"))
        storage.acquisition_put(_record(identity_id="reader-a"))
        assert storage.acquisition_get("acq-same-id")["identity_id"] == "reader-a"
        before = storage.acquisition_get("acq-same-id")
        with pytest.raises(ValueError, match="acquisition_source_scope_conflict"):
            storage.acquisition_put(_record(identity_id="reader-b"))
        assert storage.acquisition_get("acq-same-id") == before
        # A stale legacy observation without an identity does not erase it.
        storage.acquisition_put(_record(identity_id=None))
        assert storage.acquisition_get("acq-same-id")["identity_id"] == "reader-a"
    finally:
        storage.close()


def test_legacy_null_account_requires_explicit_migration_not_upsert(tmp_path):
    storage = Storage(tmp_path / "canonical.sqlite")
    try:
        storage.acquisition_put(_record(account_id=None))
        before = storage.acquisition_get("acq-same-id")
        for account in ("", "member-a", "member-b"):
            with pytest.raises(ValueError, match="acquisition_source_scope_conflict"):
                storage.acquisition_put(_record(account_id=account))
            assert storage.acquisition_get("acq-same-id") == before
    finally:
        storage.close()


def test_explicit_insert_only_discovery_does_not_rewrite_foreign_receipt(tmp_path):
    storage = Storage(tmp_path / "canonical.sqlite")
    try:
        storage.acquisition_put(_record())
        before = storage.acquisition_get("acq-same-id")
        assert storage.acquisition_put(
            _record(identity_id="reader-b", status="pending"), insert_only=True,
        ) == "acq-same-id"
        assert storage.acquisition_get("acq-same-id") == before
    finally:
        storage.close()


def test_generated_legacy_acquisition_id_collision_is_not_a_cross_account_upsert(
    tmp_path,
):
    storage = Storage(tmp_path / "canonical.sqlite")
    try:
        first = _record(acquisition_id=None, entitlement_id=None)
        receipt_id = storage.acquisition_put(first)
        before = storage.acquisition_get(receipt_id)
        with pytest.raises(ValueError, match="acquisition_source_scope_conflict"):
            storage.acquisition_put({
                **first, "account_id": "member-b",
                "status": "retryable_error",
                "title": "Foreign borrower's edition",
            })
        assert storage.acquisition_get(receipt_id) == before
        assert len(storage.acquisitions()) == 1
    finally:
        storage.close()
