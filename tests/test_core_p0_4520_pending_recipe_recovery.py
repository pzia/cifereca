"""#4520: actual durable broker interruption -> restart -> explicit recovery.

All data, including the transformer and operator confirmation, is synthetic.
No production preference is changed.
"""
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.artifact_runtime import ArtifactExecutionBrokerImpl
from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_publication as publication
from cifereca.core import derived_variant_recovery_operator as operator
from cifereca.core import derived_variant_recovery_selection as selection
from cifereca.plugin_api import Conflict
from cifereca.storage import Storage
from tests.test_core_p0_4520_durable_broker import _broker, _operation
from tests.test_core_p0_4520_derived_journal import _fixture
from tests.test_core_p0_4520_recovery_worker_lock import _paths


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX durable publication/recovery")


def test_restart_retains_interrupted_recipe_until_review_then_cache_hit(tmp_path, monkeypatch):
    service, broker, request = _broker(tmp_path, monkeypatch)
    owner = NS(identity_id="id1", directory_name="library")
    service.current_identity = owner
    service.identity_by_id = lambda identity: owner if identity == "id1" else None
    service.config.identities = lambda: [{"id": "id1", "directory_name": "library"}]
    service._source_context_epoch = service._account_context_epoch = 1
    db = service.storage
    transformer = service.plugin_manager.transformer
    root = tmp_path / "library"
    original = root / "Book.epub"
    final = root / "Optimized.epub"

    def fail(*args, **kwargs):
        assert kwargs["journal_reservation"] is not None
        raise publication.DerivedPublicationConflict("synthetic_interruption")

    try:
        with monkeypatch.context() as guard:
            guard.setattr(publication, "publish_derived_variant", fail)
            with pytest.raises(Conflict, match="Publication canonique"):
                _operation(broker, request)
        stage, = (root / ".cifereca-staging/variants").iterdir()
        assert stage.samefile(final) and original.read_bytes() == b"book-data"
        before = journal.summarize_derived_journal(db)
        assert before["pending_count"] == 1 and before["committed_count"] == 0
        assert len(db.placements()) == 1 and not db.artifact_variants()
        assert transformer.plan_calls == transformer.transform_calls == 1

        database = db.path
        db.close()
        db = service.storage = Storage(database)
        broker = ArtifactExecutionBrokerImpl(service)
        assert broker._durable_variant_publication is False
        broker._durable_variant_publication = True  # isolated recipe only
        lock = tmp_path / "acquisition.lock"
        lock.chmod(0o600)
        _paths(monkeypatch, lock)

        # A retry is not consent to re-run the transformation or relink.
        with pytest.raises(Conflict) as conflict:
            _operation(broker, request)
        assert conflict.value.code == "derived_journal_slot_occupied"
        assert transformer.plan_calls == transformer.transform_calls == 1
        assert tuple(stage.parent.iterdir()) == (stage,)
        assert journal.summarize_derived_journal(db) == before

        page = selection.recovery_choices(service, identity_id="id1", explicitly_requested=True)
        assert page is not None
        choice, = page.choices
        review = selection.review_recovery_choice(service, page, choice, explicitly_requested=True)
        assert review is not None
        assert selection.preview_confirmed_recovery_choice(
            service, page, review, confirmed=True,
        ) == "verified_no_write"
        assert journal.summarize_derived_journal(db) == before
        assert operator.run_confirmed_linked_recovery(service, review.review)["status"] == "review_required"
        assert operator.run_confirmed_linked_recovery(
            service, review.review, confirmed=True,
        )["status"] == "committed"
        assert operator.run_confirmed_linked_recovery(
            service, review.review, confirmed=True,
        )["status"] == "review_required"

        after = journal.summarize_derived_journal(db)
        assert after["pending_count"] == 0 and after["committed_count"] == 1
        assert after["reserved_output_bytes"] == before["reserved_output_bytes"]
        result = _operation(broker, request)
        assert transformer.plan_calls == transformer.transform_calls == 1
        assert result.artifact_id != request.source.artifact_id
        targets = [p for p in db.placements() if p["artifact_id"] == result.artifact_id]
        assert len(targets) == 1 and targets[0]["path"] == str(final)
        assert targets[0]["identity_id"] == "id1"
        with db._lock, db._connect() as sql:
            assert sql.execute(
                "SELECT origin_kind,source_reference_id,parent_artifact_id "
                "FROM origin_links WHERE artifact_id=?", (result.artifact_id,),
            ).fetchall() == [("derived", None, request.source.artifact_id)]
        assert stage.samefile(final) and original.read_bytes() == b"book-data"
        assert journal.summarize_derived_journal(db) == after
    finally:
        db.close()


def test_preflight_only_blocks_exact_recipe_and_preserves_corrupt_evidence(tmp_path):
    service, intent = _fixture(tmp_path)
    db = service.storage
    options = dict(identity_id=intent.identity_id, placement_id=intent.parent_placement_id,
                   transformer_id=intent.transformer_id, profile_id=intent.profile_id,
                   options_fingerprint=intent.options_fingerprint)
    try:
        journal.require_no_pending_derived_recipe(db, **options)
        key = journal.reserve_derived_intent(db, intent)
        raw = db.metadata_get(key)
        with pytest.raises(journal.DerivedJournalConflict, match="slot_occupied"):
            journal.require_no_pending_derived_recipe(db, **options)
        for delta in ({"identity_id": "other"}, {"placement_id": "other"},
                      {"profile_id": "other"}, {"options_fingerprint": "other"},
                      {"transformer_id": "other"}):
            journal.require_no_pending_derived_recipe(db, **{**options, **delta})
        assert db.metadata_get(key) == raw
        db.metadata_set(key, "not a valid receipt")
        with pytest.raises(journal.DerivedJournalConflict, match="slot_occupied"):
            journal.require_no_pending_derived_recipe(db, **options)
        assert db.metadata_get(key) == "not a valid receipt"
    finally:
        db.close()
