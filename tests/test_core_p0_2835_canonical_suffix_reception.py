"""#2835: filename convention cannot substitute for exact provider presence.

Synthetic EPUB bytes and offers; real canonical writer, scanner and Sync,
no provider network or live credentials. Seed an already-delivered placement
to exercise historical suffixes without enabling a rename/transfer operation.
"""
from hashlib import sha256
from pathlib import Path

import pytest

from cifereca.artifact_runtime import _placement_ref
from cifereca.catalog import LocalCatalog
from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_selected_candidate_finalization import (
    rig, _isolate_delivery_lock, _prepare,
)
from tests.test_core_p0_3171_canonical_sync_stage import refs
from tests.test_p1_4496_canonical_epub_readable_names import _epub


@pytest.mark.parametrize("suffix,convention", [
    (".epub", "epub"), (".kepub.epub", "kepub"),
    (".fxl.kepub.epub", "fxl.kepub"),
])
def test_exact_provider_placement_survives_scan_and_sync_without_get(
    rig, suffix, convention,
):
    service, db, root = rig
    _, preflights = _prepare(service)
    payload = _epub(extra_metadata='<meta property="rendition:layout">pre-paginated</meta>')
    digest = sha256(payload).hexdigest()
    path = root / "A" / "Acquisitions" / ("Previously delivered" + suffix)
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    published = publish_completed_acquisition(
        db, library_root=root, path=path, source=service.source,
        snapshot=service.snapshot, final_sha256=digest,
        delivery_receipt_ref="synthetic-exact-delivery",
    )
    original_format = db.artifact_get(published.artifact_id)["media_format"]
    original_refs = refs(db)

    def unexpected_get(*args, **kwargs):
        pytest.fail("exact provider presence must not issue another GET")

    service.session.rights_selected_delivery_candidate = unexpected_get
    assert LocalCatalog(db, root).scan().cataloged == 1
    for _ in range(2):
        result = synchronize_account_source(service, service.account)
        assert result["canonical_execution"]["present_attested"] == 1
        assert result["canonical_execution"]["downloaded"] == 0
        assert len(db.placements()) == 1
        placement = db.placement_get(published.placement_id)
        assert placement["artifact_id"] == "sha256:" + digest
        assert Path(placement["path"]) == path
        assert _placement_ref(placement).facts["epub_filename_convention"] == convention
        assert db.artifact_get(published.artifact_id)["media_format"] == original_format
        assert path.read_bytes() == payload
        assert refs(db) == original_refs
    assert preflights == []


def test_unlinked_fxl_copy_does_not_veto_authorized_epub_get(rig):
    service, db, root = rig
    _, preflights = _prepare(service)
    payload = _epub()
    digest = sha256(payload).hexdigest()
    local = root / "A" / "manual" / "Unlinked.fxl.kepub.epub"
    local.parent.mkdir(parents=True)
    local.write_bytes(payload)
    assert LocalCatalog(db, root).index_path(local)["status"] == "indexed"
    assert refs(db)[1] == 0
    calls = []

    def candidate(_account, *, target_dir, **kwargs):
        calls.append(kwargs["delivery_offer_id"])
        path = Path(target_dir) / "received.epub"
        path.write_bytes(payload)
        return path, "synthetic-epub"

    service.session.rights_selected_delivery_candidate = candidate
    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 1
    assert calls == ["signed-right-offer"] and len(preflights) == 1
    assert local.read_bytes() == payload
    placements = db.placements()
    assert len(placements) == 2
    assert {p["artifact_id"] for p in placements} == {"sha256:" + digest}
    delivered, = [p for p in placements if Path(p["path"]) != local]
    assert delivered["identity_id"] == "A"
    assert Path(delivered["path"]).parent == root / "A" / "Acquisitions"
    assert Path(delivered["path"]).read_bytes() == payload
    assert refs(db)[1] == 1
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["present_attested"] == 1
    assert second["canonical_execution"]["downloaded"] == 0
    assert calls == ["signed-right-offer"] and refs(db)[1] == 1
    assert local.read_bytes() == payload
