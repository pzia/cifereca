"""P0 #4858: W2 rechecks typed Core ownership at its final 0.7 writer.

Synthetic SQLite only; no Kobo network, download, DRM or real account.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca.core.kobo_backup import (
    _KoboLegacySessionChanged,
    _guarded_kobo_legacy_put,
)
from cifereca.storage import Storage


def _typed_source(storage, *, credential="vault", edition="edition"):
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,"
            "account_id,branch_id,edition_id,right_id,reference_kind,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "source:canonical", "bob", "kobo", credential,
                "account-b", "owned", edition, "right-b", "purchase", 1, 1,
            ),
        )
        db.commit()


@pytest.mark.parametrize("changed", ("source", "scope", "stable"))
def test_last_w2_writer_never_updates_a_typed_source_checkpoint(tmp_path, changed):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        legacy = {
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "alice", "account_id": "account-a",
            "item_id": "edition", "status": "pending",
            "title": "Old compatibility checkpoint",
        }
        storage.backup_put(legacy)
        before = storage.backup_get("kobo", "vault", "edition")
        calls = []
        def source_current():
            calls.append(1)
            return changed != "scope"
        if changed == "source":
            _typed_source(storage)
        with pytest.raises(_KoboLegacySessionChanged) if changed != "stable" else _no_exception():
            _guarded_kobo_legacy_put(
                SimpleNamespace(storage=storage),
                {**legacy, "status": "acquiring", "title": "New legacy claim"},
                source_current,
            )
        after = storage.backup_get("kobo", "vault", "edition")
        if changed == "stable":
            assert after["status"] == "acquiring"
            assert len(calls) == 2
        else:
            assert after == before
            assert len(calls) >= 1
    finally:
        storage.close()


from contextlib import nullcontext as _no_exception


def test_w2_last_writer_cannot_override_another_borrowers_typed_source(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        _typed_source(storage)
        before = storage.backup_list("kobo")
        with pytest.raises(_KoboLegacySessionChanged, match="kobo_canonical_source_delivery_active"):
            _guarded_kobo_legacy_put(
                SimpleNamespace(storage=storage),
                {
                    "provider_id": "kobo", "credential_id": "vault",
                    "identity_id": "alice", "account_id": "account-a",
                    "item_id": "edition", "status": "complete",
                    "sha256": "a" * 64, "size": 42,
                    "local_path": str(tmp_path / "not-proof.epub"),
                },
                lambda: True,
            )
        assert storage.backup_list("kobo") == before
        assert storage.artifacts() == []
        assert storage.placements() == []
        assert storage.acquisitions(provider_id="kobo") == []
    finally:
        storage.close()
