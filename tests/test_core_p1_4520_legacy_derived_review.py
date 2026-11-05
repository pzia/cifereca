"""#4520: read-only legacy Converter review never fabricates derived origin.

SQLite v14 plus real synthetic EPUB bytes. No provider, DRM, external files
or claims from a matching global Artifact SHA.
"""
from __future__ import annotations

from pathlib import Path
import shutil

from cifereca.artifact_runtime import ArtifactExecutionBrokerImpl
from cifereca.plugin_api import TransformRequest
from tests.test_core041_artifact_authority import (
    Control, FakeManager, FakeTransformer, make_service,
)


def _published(tmp_path):
    service, source, source_pid = make_service(tmp_path)
    service.plugin_manager = FakeManager(transformer=FakeTransformer())
    result = ArtifactExecutionBrokerImpl(service).execute_transform(
        TransformRequest(source, "optimize"), control=Control(),
    )
    row = service.storage.artifact_variants(
        source_artifact_id=source.artifact_id,
    )[0]
    return service, source, source_pid, result, row


def _review(service, variant_id, *, owner="id1", root=None):
    return service.storage.review_legacy_derived_variant(
        identity_id=owner, variant_id=variant_id,
        library_root=root or service.storage.path.parent,
    )


def test_normal_derived_receipt_is_attested_without_writing_anything(tmp_path):
    service, source, original_pid, result, row = _published(tmp_path)
    try:
        before = (
            service.storage.placements(),
            service.storage.artifact_variants(),
            service.storage.artifact_get(result.artifact_id),
        )
        review = _review(service, row["variant_id"], root=tmp_path)
        assert review.status == "already_attested"
        assert review.reason_code == "derived_origin_exact"
        assert review.physical_pair_verified is True
        assert review.derived_origin_attested is True
        assert review.may_migrate is False
        assert (
            service.storage.placements(),
            service.storage.artifact_variants(),
            service.storage.artifact_get(result.artifact_id),
        ) == before
        assert service.storage.placement_get(original_pid)["artifact_id"] == (
            source.artifact_id
        )
    finally:
        service.storage.close()


def test_absent_origin_yields_physically_verified_but_unattributed_legacy(tmp_path):
    service, source, _pid, result, row = _published(tmp_path)
    try:
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "DELETE FROM origin_links WHERE artifact_id=? AND "
                "origin_kind='derived'",
                (result.artifact_id,),
            )
        review = _review(service, row["variant_id"], root=tmp_path)
        assert review.status == "physical_pair_only"
        assert review.reason_code == "derived_origin_unattributed"
        assert review.physical_pair_verified is True
        assert review.derived_origin_attested is False
        assert review.may_migrate is False
        with service.storage._lock, service.storage._connect() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM origin_links WHERE artifact_id=?",
                (result.artifact_id,),
            ).fetchone()[0] == 0
    finally:
        service.storage.close()


def test_other_identity_cannot_infer_ownership_from_same_global_sha(tmp_path):
    service, source, _pid, result, row = _published(tmp_path)
    try:
        service.storage.identity_put({
            "identity_id": "id2", "label": "Another",
            "directory_name": "other",
        })
        other_original = tmp_path / "other" / "Book.epub"
        other_original.parent.mkdir()
        shutil.copyfile(tmp_path / "library" / "Book.epub", other_original)
        service.storage.placement_put({
            "artifact_id": source.artifact_id, "path": str(other_original),
            "identity_id": "id2", "provider_id": "local",
            "source_item_id": "book-1", "state": "present", "metadata": {},
        })
        # Only A has an output Placement and an OriginLink. A's derived SHA
        # cannot be presented as proof of anything in B.
        another = _review(service, row["variant_id"], owner="id2", root=tmp_path)
        assert another.status == "blocked"
        assert another.reason_code == "physical_scope_ambiguous"
        assert not another.physical_pair_verified
        assert not another.may_migrate
        assert _review(service, row["variant_id"], root=tmp_path).status == (
            "already_attested"
        )
    finally:
        service.storage.close()


def test_hash_change_and_symlink_never_supply_fake_legacy_evidence(tmp_path):
    service, source, _pid, result, row = _published(tmp_path)
    try:
        path = next(
            Path(p["path"]) for p in service.storage.placements()
            if p["artifact_id"] == result.artifact_id
        )
        path.write_bytes(b"modified")
        changed = _review(service, row["variant_id"], root=tmp_path)
        assert changed.status == "blocked"
        assert changed.reason_code == "physical_sha_unverified"
        path.unlink()
        external = tmp_path / "valuable.epub"
        external.write_bytes(b"book-data-optimized")
        path.symlink_to(external)
        linked = _review(service, row["variant_id"], root=tmp_path)
        assert linked.status == "blocked"
        assert linked.reason_code == "physical_path_unverified"
        assert external.read_bytes() == b"book-data-optimized"
    finally:
        service.storage.close()


def test_multiple_candidate_sources_do_not_establish_parent_causality(tmp_path):
    service, source, _pid, result, row = _published(tmp_path)
    try:
        another = tmp_path / "library" / "Independent.epub"
        shutil.copyfile(tmp_path / "library" / "Book.epub", another)
        service.storage.placement_put({
            "artifact_id": source.artifact_id, "path": str(another),
            "identity_id": "id1", "provider_id": "local",
            "source_item_id": "book-1", "state": "present", "metadata": {},
        })
        review = _review(service, row["variant_id"], root=tmp_path)
        assert review.status == "blocked"
        assert review.reason_code == "physical_scope_ambiguous"
        assert review.may_migrate is False
    finally:
        service.storage.close()


def test_invalid_input_is_bounded_and_cannot_read_outside_library(tmp_path):
    service, _source, _pid, _result, row = _published(tmp_path)
    try:
        variant = row["variant_id"]
        assert _review(service, variant, owner="no-such-profile",
                       root=tmp_path).status == "blocked"
        assert _review(service, variant, root=tmp_path / "missing").status == (
            "blocked"
        )
        assert _review(service, "nonexistent", root=tmp_path).reason_code == (
            "variant_missing"
        )
        assert service.storage.review_legacy_derived_variant(
            identity_id="id1", variant_id=variant,
            library_root=Path("relative"),
        ).reason_code == "invalid_request"
    finally:
        service.storage.close()
