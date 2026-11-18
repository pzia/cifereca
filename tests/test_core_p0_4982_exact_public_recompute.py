"""Real canonical SQLite writer and durable journal; no network or executor."""
from dataclasses import replace

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, persist_consolidated_cover_source,
    read_consolidated_notice_cover,
)
from cifereca.core.notice_c_exact_recompute import (
    request_exact_public_recompute, prepare_exact_public_recompute,
)
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import (
    persist, verified_public,
)


def source(db, title, at):
    return persist(db, verified_public(record_id="same-edition", title=title),
                   at=at)


def request(db, c, generation=0):
    return request_exact_public_recompute(
        db, c.document, now=100, expected_generation=generation,
    )


def finish(db, ref, publish, *, abort=False):
    lease = journal.claim_job(db, ref, now=100, lease_seconds=30)
    assert lease is not None
    with db._lock, db._connect() as sql:
        sql.execute("BEGIN IMMEDIATE")
        journal._held(sql, lease, 101)
        assert publish(sql) is True
        journal._complete_job_txn(sql, lease, now=101)
        if abort:
            raise RuntimeError("crash before commit")
    return lease


def test_exact_recompute_atomic_restart_and_idempotent_c(tmp_path):
    path = tmp_path / "exact.sqlite"
    db = Storage(path)
    try:
        old = persist_exact_public_search_cover(db, source(db, "Ancien", 10))
        changed = source(db, "Nouveau titre", 11)
        ref = request(db, old)
        assert request(db, old) == ref
        assert read_consolidated_notice_cover(db, old.document) == old
        prepared = prepare_exact_public_recompute(
            db, journal.read_job(db, ref).spec,
        )
        with pytest.raises(RuntimeError, match="crash"):
            finish(db, ref, prepared, abort=True)
        assert read_consolidated_notice_cover(db, old.document) == old
        assert journal.read_job(db, ref).state == "pending"
    finally:
        db.close()
    db = Storage(path)
    try:
        lease = journal.claim_job(db, ref, now=130, lease_seconds=30)
        assert lease is not None and lease.fence == 2
        prepared = prepare_exact_public_recompute(
            db, journal.read_job(db, ref).spec,
        )
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            journal._held(sql, lease, 131)
            assert prepared(sql) is True
            journal._complete_job_txn(sql, lease, now=131)
        new = read_consolidated_notice_cover(db, old.document)
        assert new.epoch == old.epoch + 1
        assert new.sources[0].source_epoch == changed.epoch
        assert new.sources[0].public_bibliography.title == "Nouveau titre"
        assert journal.read_job(db, ref).state == "current"
        assert search_public_notice_c(db, "nouveau", now=131).hits[0].document == old.document
        # A new request observes the updated C vector; the canonical
        # writer must not churn N2 on otherwise unchanged inputs.
        retry = request(db, new, ref.generation)
        assert retry.generation > ref.generation
        finish(db, retry, prepare_exact_public_recompute(
            db, journal.read_job(db, retry).spec,
        ))
        assert read_consolidated_notice_cover(db, old.document) == new
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_changed_vector_and_eviction_veto_real_writer(tmp_path):
    db = Storage(tmp_path / "exact.sqlite")
    try:
        initial = source(db, "Ancien", 10)
        old = persist_exact_public_search_cover(db, initial)
        ref = request(db, old)
        spec = journal.read_job(db, ref).spec
        prepared = prepare_exact_public_recompute(db, spec)
        changed = source(db, "Nouveau", 11)
        with pytest.raises(ValueError, match="inputs_changed"):
            finish(db, ref, prepared)
        assert read_consolidated_notice_cover(db, old.document) == old
        with pytest.raises(ValueError, match="inputs_changed"):
            prepare_exact_public_recompute(db, spec)
        newer = request(db, old, ref.generation)
        newer_spec = journal.read_job(db, newer).spec
        prepared = prepare_exact_public_recompute(db, newer_spec)
        assert evict_notice_cover(db, changed.document, expected_epoch=changed.epoch)
        with pytest.raises(ValueError, match="inputs_changed"):
            finish(db, newer, prepared)
        assert read_consolidated_notice_cover(db, old.document) == old
        with pytest.raises(ValueError, match="exact_source_required"):
            request(db, old, newer.generation)
    finally:
        db.close()


def test_no_relation_reconstruction_or_implementation_downgrade(tmp_path):
    db = Storage(tmp_path / "exact.sqlite")
    try:
        a = source(db, "Titre", 10)
        own = persist_exact_public_search_cover(db, a)
        ref = request(db, own)
        spec = journal.read_job(db, ref).spec
        with pytest.raises(ValueError, match="unsupported_spec"):
            prepare_exact_public_recompute(
                db, replace(spec, implementation_digest="0" * 64),
            )
        # A distinct retained group is not an authority even if its title,
        # source and cover are identical to the independent self C.
        foreign = persist_consolidated_cover_source(
            db, canonical_edition_id="edition:foreign", source=a,
            relation_proof_id="fixture:strong:foreign",
        )
        with pytest.raises(ValueError, match="exact_source_required"):
            request(db, foreign)
        b = persist(db, verified_public(
            source_provider="seller-b", record_id="different", title="Titre",
        ), at=10)
        group = persist_consolidated_cover_source(
            db, canonical_edition_id=a.observation.key.edition_id, source=b,
            relation_proof_id="fixture:strong:multi",
        )
        assert group.document == own.document and len(group.sources) == 2
        with pytest.raises(ValueError, match="exact_source_required"):
            request(db, group, ref.generation)
        assert read_consolidated_notice_cover(db, own.document) == group
    finally:
        db.close()


def test_prepared_singleton_cannot_overwrite_new_multi_source_membership(tmp_path):
    db = Storage(tmp_path / "exact.sqlite")
    try:
        a = source(db, "Titre", 10)
        own = persist_exact_public_search_cover(db, a)
        ref = request(db, own)
        prepared = prepare_exact_public_recompute(db, journal.read_job(db, ref).spec)
        b = persist(db, verified_public(
            source_provider="seller-b", record_id="different", title="Titre",
        ), at=11)
        group = persist_consolidated_cover_source(
            db, canonical_edition_id=a.observation.key.edition_id, source=b,
            relation_proof_id="fixture:strong:multi",
        )
        assert group.document == own.document and len(group.sources) == 2
        with pytest.raises(ValueError, match="inputs_changed"):
            finish(db, ref, prepared)
        assert read_consolidated_notice_cover(db, own.document) == group
        assert journal.read_job(db, ref).state == "pending"
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
