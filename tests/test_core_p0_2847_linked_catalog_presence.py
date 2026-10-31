"""#2847: a catalogue's local flag cannot outlive its exact linked Placement.

Real SQLite and filesystem, no providers, sync, network or repair mutations.
"""
from __future__ import annotations

import hashlib

import pytest

from tests.test_kobo_backup_guard import _service, _write_epub


def _catalogued(service, *, physical: bool, state: str, owner: str = "alice",
                artifact_mismatch: bool = False):
    path = service.library_dir() / "alice" / "local" / "linked.epub"
    if physical:
        _write_epub(path)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    actual_sha = hashlib.sha256(path.read_bytes()).hexdigest() if physical else "a" * 64
    aid = "sha256:" + actual_sha
    pid = service.storage.placement_put({
        "path": str(path), "identity_id": owner, "state": state,
        "artifact_id": aid, "provider_id": "local",
        "source_item_id": "catalog-edition",
    })
    service.storage.catalog_entry_put({
        "catalog_entry_id": "catalog:local:linked",
        "placement_id": pid,
        "artifact_id": ("sha256:" + "f" * 64) if artifact_mismatch else aid,
        "identity_id": "alice",
        "provider_id": "local", "item_id": "catalog-edition",
        "title": "Synthetic linked",
        "local_state": "local",
        "media_format": "epub",
        "metadata": {"path": str(path), "source": "canonical-indexer"},
    })
    return pid, path


@pytest.mark.parametrize("state,physical,owner,mismatch", [
    ("missing", False, "alice", False),
    ("unverified", True, "alice", False),
    ("present", False, "alice", False),
    ("present", True, "bob", False),
    ("present", True, "alice", True),
])
def test_linked_nonlegacy_local_requires_exact_present_physical_placement(
    tmp_path, state, physical, owner, mismatch,
):
    service = _service(tmp_path)
    try:
        _, path = _catalogued(
            service, physical=physical, state=state,
            owner=owner, artifact_mismatch=mismatch,
        )
        stored = service.storage.catalog_entry_get("catalog:local:linked")
        assert stored["local_state"] == "local"
        assert "imported_from" not in stored["metadata"]
        rows = service.library_entries(identity_id="alice", scan=False)
        assert not any(row.get("item_id") == "catalog-edition" and row.get("local")
                       for row in rows)
        # Projection never rewrites the stale SQLite facts or the disk.
        assert service.storage.catalog_entry_get("catalog:local:linked") == stored
        assert path.exists() is physical
    finally:
        service.close()


def test_exact_live_link_survives_and_disappears_after_file_vanishes(tmp_path):
    service = _service(tmp_path)
    try:
        pid, path = _catalogued(service, physical=True, state="present")
        rows = service.library_entries(identity_id="alice", scan=False)
        assert any(row.get("local") and row.get("item_id") == "catalog-edition"
                   and row.get("placement_id") == pid for row in rows)
        path.unlink()
        rows = service.library_entries(identity_id="alice", scan=False)
        assert not any(row.get("local") and row.get("item_id") == "catalog-edition"
                       for row in rows)
        assert service.storage.placement_get(pid)["state"] == "present"
    finally:
        service.close()


def test_unbound_nonlegacy_local_declarations_retain_compatibility(tmp_path):
    service = _service(tmp_path)
    try:
        service.storage.catalog_entry_put({
            "catalog_entry_id": "catalog:manual",
            "identity_id": "alice",
            "provider_id": "local", "item_id": "manual-edition",
            "title": "Manual declaration",
            "local_state": "local",
            "metadata": {"path": str(tmp_path / "manual-unscanned.epub")},
        })
        rows = service.library_entries(identity_id="alice", scan=False)
        assert any(row.get("local") and row.get("item_id") == "manual-edition"
                   for row in rows)
    finally:
        service.close()
