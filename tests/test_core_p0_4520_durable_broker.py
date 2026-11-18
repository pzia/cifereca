"""#4520: exercise the private, default-off durable broker path."""
from dataclasses import replace
import os
from pathlib import Path

import pytest

from cifereca.artifact_runtime import ArtifactExecutionBrokerImpl
from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_publication as publication
from cifereca.plugin_api import Conflict, TransformRequest
from tests.test_core041_artifact_authority import (
    Control, FakeManager, FakeTransformer, make_service,
)


def _broker(tmp_path, monkeypatch):
    service, source, pid = make_service(tmp_path)
    service.plugin_manager = FakeManager(transformer=FakeTransformer())
    broker = ArtifactExecutionBrokerImpl(service)
    from cifereca import paths
    monkeypatch.setattr(paths, "acquisition_lock_file", lambda: tmp_path / "acquisition.lock")
    assert broker._durable_variant_publication is False
    broker._durable_variant_publication = True
    request = TransformRequest(
        replace(source, identity_id="id1", placement_id=pid),
        "optimize", identity_id="id1", placement_id=pid,
    )
    return service, broker, request


def _operation(broker, request):
    return broker.admit_transform(request, transformer_plugin_id="document.transformer.test")(Control())


def test_full_broker_reservation_and_canonical_commit(tmp_path, monkeypatch):
    service, broker, request = _broker(tmp_path, monkeypatch)
    db = service.storage
    original = os.link

    def observed(*args, **kwargs):
        assert journal.summarize_derived_journal(db)["pending_count"] == 1
        return original(*args, **kwargs)

    monkeypatch.setattr(os, "link", observed)
    try:
        result = _operation(broker, request)
        assert result.sha256 != request.source.sha256
        summary = journal.summarize_derived_journal(db)
        assert (summary["pending_count"], summary["committed_count"]) == (0, 1)
        final = tmp_path / "library" / "Optimized.epub"
        stage, = (final.parent / ".cifereca-staging/variants").iterdir()
        assert stage.samefile(final)
        assert _operation(broker, request).artifact_id == result.artifact_id
        assert service.plugin_manager.transformer.transform_calls == 1
        assert journal.summarize_derived_journal(db) == summary
    finally:
        db.close()


def test_sql_failure_retains_stage_and_exact_pending_receipt(tmp_path, monkeypatch):
    service, broker, request = _broker(tmp_path, monkeypatch)
    db = service.storage

    def fail(*args, **kwargs):
        assert kwargs["journal_reservation"] is not None
        raise publication.DerivedPublicationConflict("synthetic_sql_failure")

    monkeypatch.setattr(publication, "publish_derived_variant", fail)
    try:
        with pytest.raises(Conflict, match="Publication canonique"):
            _operation(broker, request)
        assert journal.summarize_derived_journal(db)["pending_count"] == 1
        final = tmp_path / "library" / "Optimized.epub"
        stage, = (final.parent / ".cifereca-staging/variants").iterdir()
        assert stage.samefile(final)
        assert len(db.placements()) == 1
        assert not db.artifact_variants()
    finally:
        db.close()


def test_late_collision_preserves_the_other_writer_and_receipt(tmp_path, monkeypatch):
    service, broker, request = _broker(tmp_path, monkeypatch)
    db = service.storage
    target = tmp_path / "library" / "Optimized.epub"
    original = os.link

    def collide(*args, **kwargs):
        target.write_bytes(b"independent")
        return original(*args, **kwargs)

    monkeypatch.setattr(os, "link", collide)
    try:
        with pytest.raises(Conflict) as exc:
            _operation(broker, request)
        assert exc.value.code == "artifact_variant_filename_conflict"
        assert target.read_bytes() == b"independent"
        stage, = (target.parent / ".cifereca-staging/variants").iterdir()
        assert stage.read_bytes() == b"book-data-optimized"
        assert journal.summarize_derived_journal(db)["pending_count"] == 1
        assert len(db.placements()) == 1
    finally:
        db.close()


def test_known_capacity_refusal_precedes_link(tmp_path, monkeypatch):
    service, broker, request = _broker(tmp_path, monkeypatch)
    db = service.storage
    monkeypatch.setattr(journal, "_MAX_RECORDS", 0)
    monkeypatch.setattr(os, "link", lambda *a, **k: pytest.fail("no link at capacity"))
    try:
        with pytest.raises(Conflict) as exc:
            _operation(broker, request)
        assert exc.value.code == "derived_journal_capacity_exceeded"
        assert not (tmp_path / "library" / "Optimized.epub").exists()
        assert journal.summarize_derived_journal(db)["count"] == 0
        assert len(db.placements()) == 1
    finally:
        db.close()


def test_durable_mode_requires_admission_before_transform(tmp_path, monkeypatch):
    service, broker, request = _broker(tmp_path, monkeypatch)
    try:
        with pytest.raises(Conflict) as exc:
            broker.execute_transform(request, control=Control())
        assert exc.value.code == "artifact_transform_scope_unverified"
        assert service.plugin_manager.transformer.plan_calls == 0
        assert service.plugin_manager.transformer.transform_calls == 0
        assert journal.summarize_derived_journal(service.storage)["count"] == 0
    finally:
        service.storage.close()
