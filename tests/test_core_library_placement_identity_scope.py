from __future__ import annotations

import hashlib
from pathlib import Path

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(tmp_path: Path) -> LibraryService:
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "library"))
    config.set("identities", [
        {"id": "alice", "label": "Alice"},
        {"id": "bob", "label": "Bob"},
    ])
    return LibraryService(config, Storage(tmp_path / "library.sqlite3"), Diagnostics())


def _placement(service, tmp_path, *, owner, filename, artifact_id, item_id):
    path = tmp_path / owner / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"same epub contents")
    pid = service.storage.placement_put({
        "artifact_id": artifact_id,
        "path": str(path),
        "identity_id": None if owner == "legacy" else owner,
        "provider_id": "local",
        "source_item_id": item_id,
        "state": "present",
        "metadata": {"placement_kind": "library", "storage_target_id": owner + "-target"},
    })
    if owner != "legacy":
        service.storage.catalog_entry_put({
            "artifact_id": artifact_id,
            "placement_id": pid,
            "title": "Le livre commun",
            "author": "Autrice",
            "identity_id": owner,
            "provider_id": "local",
            "item_id": item_id,
            "local_state": "local",
            "media_format": "epub",
            "metadata": {"path": str(path)},
        })
    return pid


def test_shared_artifact_exposes_only_placements_for_requested_identity(tmp_path):
    service = _service(tmp_path)
    try:
        content = b"same epub contents"
        sha = hashlib.sha256(content).hexdigest()
        artifact_id = f"sha256:{sha}"
        service.storage.artifact_put({
            "artifact_id": artifact_id, "sha256": sha,
            "size": len(content), "media_format": "epub",
            "mime_type": "application/epub+zip", "metadata": {},
        })
        a1 = _placement(service, tmp_path, owner="alice", filename="A.epub", artifact_id=artifact_id, item_id="a-1")
        a2 = _placement(service, tmp_path, owner="alice", filename="A-copy.epub", artifact_id=artifact_id, item_id="a-2")
        b1 = _placement(service, tmp_path, owner="bob", filename="B-private.epub", artifact_id=artifact_id, item_id="b-1")
        legacy = _placement(service, tmp_path, owner="legacy", filename="pre-identities.epub", artifact_id=artifact_id, item_id="legacy")

        alice_rows = service.library_entries(identity_id="alice", scan=False)
        bob_rows = service.library_entries(identity_id="bob", scan=False)
        assert len(alice_rows) == len(bob_rows) == 1
        alice, bob = alice_rows[0], bob_rows[0]

        assert alice["key"] == bob["key"]  # shared artifact identity is unchanged
        assert alice["artifact_id"] == bob["artifact_id"] == artifact_id
        assert alice["placement_count"] == 3
        assert {row["placement_id"] for row in alice["placements"]} == {a1, a2, legacy}
        assert alice["source_count"] == 2
        assert alice["identity_ids"] == ["alice"]
        assert {row["filename"] for row in alice["placements"]} == {
            "A.epub", "A-copy.epub", "pre-identities.epub",
        }
        assert {row["storage_target_id"] for row in alice["placements"]} == {
            "alice-target", "legacy-target",
        }
        assert b1 not in {row["placement_id"] for row in alice["placements"]}

        assert bob["placement_count"] == 1
        assert [row["placement_id"] for row in bob["placements"]] == [b1]
        assert [row["filename"] for row in bob["placements"]] == ["B-private.epub"]
        assert bob["identity_ids"] == ["bob"]
        assert bob["source_count"] == 1
        assert bob["local_path"].endswith("B-private.epub")
        assert all(row["identity_id"] != "alice" for row in bob["placements"])
        assert all("A-copy.epub" != row["filename"] for row in bob["placements"])
    finally:
        service.close()


def test_legacy_unscoped_placements_are_never_exposed_to_secondary_identity(tmp_path):
    service = _service(tmp_path)
    try:
        sha = hashlib.sha256(b"same epub contents").hexdigest()
        artifact_id = f"sha256:{sha}"
        service.storage.artifact_put({
            "artifact_id": artifact_id, "sha256": sha, "size": 18,
            "media_format": "epub", "metadata": {},
        })
        legacy = _placement(service, tmp_path, owner="legacy", filename="old.epub",
                            artifact_id=artifact_id, item_id="old")
        bob = _placement(service, tmp_path, owner="bob", filename="bob.epub",
                         artifact_id=artifact_id, item_id="bob")
        rows = service.library_entries(identity_id="bob", scan=False)
        assert len(rows) == 1
        assert [row["placement_id"] for row in rows[0]["placements"]] == [bob]
        assert legacy not in {row["placement_id"] for row in rows[0]["placements"]}
    finally:
        service.close()
