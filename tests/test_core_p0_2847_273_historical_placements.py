"""P0 #2847: 273 synthetic historical Placement cases, one-at-a-time repairs.

A regression fixture at the same scale as the reported library, NOT a
field-recipe or an assertion that the actual user's 273 books are resolved.
Exercises real SQLite, real EPUB SHA and two independently scoped identities.
"""
from __future__ import annotations

import hashlib

from cifereca.core import canonical_recovery
from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from cifereca.storage import Storage
from tests.test_core_p0_2847_inspection_binding import Service
from tests.test_library_reconciliation import epub


def test_273_missing_placements_two_explicit_repairs_leave_271_untouched(
    tmp_path, monkeypatch,
):
    root = tmp_path / "library"
    alice = root / "alice"
    bob = root / "bob"
    alice.mkdir(parents=True)
    bob.mkdir(parents=True)
    found = alice / "manually-moved.epub"
    foreign = bob / "identical-bytes.epub"
    epub(found, title="Synthetic local copy")
    foreign.write_bytes(found.read_bytes())
    contents = found.read_bytes()
    digest = hashlib.sha256(contents).hexdigest()
    assert hashlib.sha256(foreign.read_bytes()).hexdigest() == digest

    storage = Storage(tmp_path / "db" / "library.sqlite3")
    storage.identity_put({
        "identity_id": "alice", "directory_name": "alice", "label": "Alice",
    })
    storage.identity_put({
        "identity_id": "bob", "directory_name": "bob", "label": "Bob",
    })
    artifact_id = "sha256:" + digest
    storage.artifact_put({
        "artifact_id": artifact_id, "sha256": digest,
        "size": len(contents), "media_format": "epub",
    })
    placements = {}
    for index in range(273):
        path = alice / f"historical-missing-{index:03d}.epub"
        pid = storage.placement_put({
            "path": str(path), "identity_id": "alice",
            "artifact_id": artifact_id, "provider_id": "kobo",
            "source_item_id": f"independent-edition-{index:03d}",
            "state": "missing", "metadata": {},
        })
        placements[index] = (pid, path)
    bob_pid = storage.placement_put({
        "path": str(foreign), "identity_id": "bob",
        "artifact_id": artifact_id, "provider_id": "local",
        "source_item_id": "foreign-independent", "state": "present",
        "metadata": {},
    })
    initial = {p["placement_id"]: p for p in storage.placements()}
    assert len(initial) == 274
    assert sum(
        p["state"] == "missing" and p["identity_id"] == "alice"
        for p in initial.values()
    ) == 273
    assert not any(path.exists() for _, path in placements.values())

    monkeypatch.setattr(
        canonical_recovery, "acquisition_lock_file",
        lambda: tmp_path / "synthetic-acquisition.lock",
    )
    service = Service(storage, root)

    def inspection():
        audit = audit_library(storage.path, {"alice": (alice,)})
        assert audit.redacted()["status"] == "complete"
        result = LocalInspection(
            audit, lambda: True, "alice", roots=(alice,), service=service,
        )
        assert result._manual_audit_complete
        return result

    def key_for(session, placement_id):
        return next(
            key for key, pid in session._missing_case_placements.items()
            if pid == placement_id
        )

    try:
        first = inspection()
        assert len(first._missing_case_placements) == 273
        file_key, = tuple(first._manual_file_observations)
        moved_id, original_path = placements[0]
        selected = key_for(first, moved_id)
        # An observed identical SHA from Bob is never offered by Alice's
        # inspection, even though bytes are identical across two profiles.
        assert all(
            observation.identity_id == "alice"
            for observation in first._manual_file_observations.values()
        )
        proposed = first.preview_missing_relocation(selected, file_key)
        assert proposed["status"] == "review_required"
        assert "plan_token" not in proposed
        assert first.confirm_missing_repair(
            selected, file_key=file_key, user_confirmed=False,
        )["status"] == "blocked"
        assert storage.placement_get(moved_id)["state"] == "missing"

        # Invalidated plan must be explicitly re-prepared. Only this one
        # Placement is relinked; the other 272 historical paths stay absent.
        assert first.preview_missing_relocation(
            selected, file_key,
        )["status"] == "review_required"
        assert first.confirm_missing_repair(
            selected, file_key=file_key, user_confirmed=True,
        )["status"] == "relocated"
        assert not first.valid()
        assert storage.placement_get(moved_id)["path"] == str(found)
        assert storage.placement_get(moved_id)["state"] == "present"
        assert not original_path.exists()

        second = inspection()
        retire_id, retire_path = placements[1]
        retire_key = key_for(second, retire_id)
        assert len(second._missing_case_placements) == 272
        assert second.preview_missing_retirement(retire_key)["status"] == "ready"
        second.cancel_missing_repair()
        assert second.confirm_missing_repair(
            retire_key, user_confirmed=True,
        )["status"] == "blocked"
        assert storage.placement_get(retire_id)["state"] == "missing"
        assert second.preview_missing_retirement(retire_key)["status"] == "ready"
        assert second.confirm_missing_repair(
            retire_key, user_confirmed=True,
        )["status"] == "retired"
        assert not second.valid()
        assert storage.placement_get(retire_id) is None
        assert not retire_path.exists()

        after = {p["placement_id"]: p for p in storage.placements()}
        assert len(after) == 273
        for index in range(2, 273):
            pid, path = placements[index]
            assert after[pid] == initial[pid]
            assert not path.exists()
        assert after[bob_pid] == initial[bob_pid]
        assert after[moved_id]["path"] == str(found)
        assert after[moved_id]["state"] == "present"
        assert found.read_bytes() == foreign.read_bytes() == contents

        third = inspection()
        assert len(third._missing_case_placements) == 271
        assert key_for(third, placements[2][0]).startswith("path:")
        assert moved_id not in third._missing_case_placements.values()
        assert retire_id not in third._missing_case_placements.values()
        assert third.preview_missing_retirement("path:forged")["status"] == "blocked"
        third.close()
    finally:
        storage.close()
