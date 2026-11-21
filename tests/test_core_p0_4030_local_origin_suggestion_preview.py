"""#4030: no same-SHA or catalogue-only evidence grants provenance."""
from pathlib import Path
import hashlib

import pytest

from cifereca.core import local_origin_suggestion_preview as preview
from tests.test_core_p0_3171_atomic_publication_v14 import (
    store, register, new_source, target, publish, rows, BYTES, SHA,
)


def _local(store, root, owner):
    path = root / owner / "manual.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(BYTES)
    artifact = "sha256:" + SHA
    store.artifact_put({"artifact_id": artifact, "sha256": SHA,
                        "size": len(BYTES), "media_format": "pdf"})
    pid = store.placement_put({"artifact_id": artifact, "identity_id": owner,
                               "path": str(path), "state": "present"})
    return path, pid


def _review(store, root, pid, owner="A"):
    return preview.preview_local_origin_suggestion(
        store, library_root=root, identity_id=owner, placement_id=pid,
    )


def test_exact_local_candidate_does_not_inherit_other_profile_origin(store, tmp_path):
    root = tmp_path / "EBooks"
    register(store, "A", "seller", "card-A")
    register(store, "B", "other", "card-B")
    key, snap = new_source("B", provider="other", credential="card-B")
    publish(store, root, target(tmp_path, "B"), key, snap)
    path, pid = _local(store, root, "A")
    before = rows(store)
    report = _review(store, root, pid)
    assert report["status"] == "documentary_candidate"
    assert report["physical_bytes_verified"] is True
    assert not report["provider_origin_attested_by_preview"]
    assert not report["may_write_origin_or_right"]
    assert not report["may_search_private_catalogues"]
    assert report["requires_separate_search_admission"]
    assert str(path) not in repr(report) and SHA not in repr(report)
    assert rows(store) == before


def test_provider_origin_in_manual_folder_is_excluded_even_without_active_connection(store, tmp_path):
    key, snap = new_source()
    register(store, "A", "seller", "card-A")
    root = tmp_path / "EBooks"
    path = target(tmp_path, "A", "manual.pdf")
    result = publish(store, root, path, key, snap)
    with store._lock, store._connect() as db:
        db.execute("UPDATE provider_connections SET enabled=0")
    assert _review(store, root, result.placement_id)["reason"] == "historical_origin_requires_preservation"
    assert path.read_bytes() == BYTES


@pytest.mark.parametrize("change", ["foreign", "missing", "symlink", "bad_bytes", "budget"])
def test_refuses_without_exact_physical_scope(store, tmp_path, monkeypatch, change):
    register(store, "A", "seller", "card-A")
    root = tmp_path / "EBooks"
    path, pid = _local(store, root, "A")
    owner = "A"
    if change == "foreign":
        owner = "B"
    elif change == "missing":
        path.unlink()
    elif change == "symlink":
        other = path.with_name("other.pdf")
        path.rename(other)
        path.symlink_to(other)
    elif change == "bad_bytes":
        path.write_bytes(b"foreign")
    else:
        monkeypatch.setattr(preview, "_MAX_BYTES", 1)
    result = _review(store, root, pid, owner)
    assert result["status"] == "excluded"
    assert result["physical_bytes_verified"] is False
    assert not result["may_write_origin_or_right"]


def test_new_provider_origin_during_review_revokes_candidate(store, tmp_path, monkeypatch):
    key, snap = new_source()
    register(store, "A", "seller", "card-A")
    root = tmp_path / "EBooks"
    path, pid = _local(store, root, "A")
    original = preview._snapshot
    seen = False

    def changed(*args, **kwargs):
        nonlocal seen
        value = original(*args, **kwargs)
        if not seen:
            seen = True
            publish(store, root, path, key, snap)
        return value

    monkeypatch.setattr(preview, "_snapshot", changed)
    assert _review(store, root, pid)["reason"] == "evidence_changed"
    assert path.read_bytes() == BYTES
