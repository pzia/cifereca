"""Real v14 OriginLink graph, public C writer and journal; no network/executor."""
import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core import search_orchestration
from cifereca.core.notice_c_cover_index import (
    persist_consolidated_cover_source, read_consolidated_notice_cover,
)
from cifereca.core.notice_c_origin_recompute import (
    request_origin_public_recompute, prepare_origin_public_recompute,
)
from cifereca.core.notice_c_origin_link_producer import reattest_exact_origin_group_txn
from cifereca.core.notice_c_aggregate_inputs import capture_public_aggregate_inputs
from cifereca.storage import Storage
from tests.test_core_p0_4982_exact_public_recompute import finish
from tests.test_core_p0_6366_origin_attested_public_c import (
    _origins, _source_rows, _mirror, A_URL, B_URL,
    new_source, register, publish, target, witnessed, public_row,
)
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist

NEW_URL = "https://covers.example.org/origin-b-new.jpg"


def setup_group(db, tmp_path, monkeypatch):
    _origins(db, tmp_path)
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 200)
    a, b = _source_rows()
    assert _mirror(db, a) == 1 and _mirror(db, b) == 1
    group = read_consolidated_notice_cover(db, b._core_notice_canonical_cover_document_ref)
    assert len(group.sources) == 2
    return group


def refresh(db, **changes):
    return persist(db, witnessed(public_row(**{
        "source_provider": "seller-b", "record_id": "edition-b",
        "title": "L'ouvrage commun", "thumbnail_url": NEW_URL,
        **changes,
    })), at=201)


def request(db, group, generation=0):
    return request_origin_public_recompute(
        db, group.document, now=100, expected_generation=generation,
    )


def prepare(db, ref):
    return prepare_origin_public_recompute(db, journal.read_job(db, ref).spec)


def test_multi_source_recomposition_crash_restart_and_stable_ref(tmp_path, monkeypatch):
    path = tmp_path / "origin-recompute.sqlite"
    db = Storage(path)
    try:
        group = setup_group(db, tmp_path, monkeypatch)
        changed = refresh(db)
        assert read_consolidated_notice_cover(db, group.document) == group
        ref = request(db, group)
        assert request(db, group) == ref
        with pytest.raises(RuntimeError, match="crash"):
            finish(db, ref, prepare(db, ref), abort=True)
        assert read_consolidated_notice_cover(db, group.document) == group
        assert journal.read_job(db, ref).state == "pending"
    finally:
        db.close()
    db = Storage(path)
    try:
        lease = journal.claim_job(db, ref, now=130, lease_seconds=30)
        assert lease is not None and lease.fence == 2
        publication = prepare(db, ref)
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            journal._held(sql, lease, 131)
            assert publication(sql) is True
            journal._complete_job_txn(sql, lease, now=131)
        updated = read_consolidated_notice_cover(db, group.document)
        assert updated.epoch == group.epoch + 1
        assert {c.url for c in updated.candidates} == {A_URL, NEW_URL}
        assert {member.source_ref: member.relation_proof_id for member in updated.sources} == {
            member.source_ref: member.relation_proof_id for member in group.sources
        }
        assert next(m for m in updated.sources if m.provider_id == "seller-b").source_epoch == changed.epoch
        assert journal.read_job(db, ref).state == "current"
        again = request(db, updated, ref.generation)
        finish(db, again, prepare(db, again))
        assert read_consolidated_notice_cover(db, group.document) == updated
        assert db.entitlements() == [] and db.acquisitions() == []
        with db._lock, db._connect() as sql:
            raw = str(sql.execute(
                "SELECT value FROM metadata WHERE key LIKE ?",
                ("notice.c.cover.canonical.v1:%",),
            ).fetchall())
        assert "buyer-A" not in raw and "synthetic-completed-delivery" not in raw
        assert str(tmp_path) not in raw
    finally:
        db.close()


@pytest.mark.parametrize("damage", ["origin", "source_evidence", "placement"])
def test_origin_graph_revoked_after_prepare_without_public_vector_change(
    tmp_path, monkeypatch, damage,
):
    db = Storage(tmp_path / "origin-revocation.sqlite")
    try:
        group = setup_group(db, tmp_path, monkeypatch)
        refresh(db)
        ref = request(db, group)
        publication = prepare(db, ref)
        before = capture_public_aggregate_inputs(db, group.document)
        with db._lock, db._connect() as sql:
            if damage == "origin":
                sql.execute(
                    "DELETE FROM origin_links WHERE source_reference_id IN ("
                    "SELECT source_reference_id FROM source_references "
                    "WHERE provider_id='seller-b')"
                )
            elif damage == "source_evidence":
                sql.execute(
                    "UPDATE source_references SET evidence_kind='historical_receipt' "
                    "WHERE provider_id='seller-b'"
                )
            else:
                sql.execute("DELETE FROM placements")
        assert capture_public_aggregate_inputs(db, group.document) == before
        with pytest.raises(ValueError, match="relation_changed"):
            finish(db, ref, publication)
        assert read_consolidated_notice_cover(db, group.document) == group
        assert journal.read_job(db, ref).state == "pending"
        with pytest.raises(ValueError, match="relation_changed"):
            prepare(db, ref)
        with pytest.raises(ValueError, match="relation_changed"):
            request(db, group, ref.generation)
    finally:
        db.close()


@pytest.mark.parametrize("damage", ["competing_edition", "new_member"])
def test_membership_ambiguity_or_extension_requires_relink(tmp_path, monkeypatch, damage):
    db = Storage(tmp_path / "origin-relink.sqlite")
    try:
        group = setup_group(db, tmp_path, monkeypatch)
        refresh(db)
        ref = request(db, group)
        publication = prepare(db, ref)
        before = capture_public_aggregate_inputs(db, group.document)
        if damage == "competing_edition":
            refresh(db, revision_id="other-edition")
        else:
            key, snapshot = new_source(
                "A", provider="seller-c", credential="card-c",
                edition="edition-c", right="right-c",
            )
            register(db, "A", "seller-c", "card-c")
            publish(db, tmp_path / "EBooks", target(tmp_path, "A"), key, snapshot)
            persist(db, witnessed(public_row(
                source_provider="seller-c", record_id="edition-c",
                title="L'ouvrage commun", thumbnail_url=B_URL,
            )), at=201)
        # A new graph member or an alternative source edition is not
        # included in the original members' public vector.
        assert capture_public_aggregate_inputs(db, group.document) == before
        with pytest.raises(ValueError, match="relation_changed"):
            finish(db, ref, publication)
        assert read_consolidated_notice_cover(db, group.document) == group
    finally:
        db.close()


def test_reject_bibliographic_conflict_or_manually_asserted_relation(tmp_path, monkeypatch):
    db = Storage(tmp_path / "origin-conflict.sqlite")
    try:
        group = setup_group(db, tmp_path, monkeypatch)
        refresh(db, title="Un autre ouvrage")
        with pytest.raises(ValueError, match="relation_changed"):
            request(db, group)
        assert read_consolidated_notice_cover(db, group.document) == group
        inputs = capture_public_aggregate_inputs(db, group.document)
        foreign = None
        for source in inputs.sources:
            foreign = persist_consolidated_cover_source(
                db, canonical_edition_id="edition:foreign-relation", source=source,
                relation_proof_id="strong:fixture:foreign",
            )
        with pytest.raises(ValueError, match="origin_group_required"):
            request(db, foreign)
        assert read_consolidated_notice_cover(db, foreign.document) == foreign
        with db._lock, db._connect() as sql:
            with pytest.raises(ValueError, match="transaction_required"):
                reattest_exact_origin_group_txn(sql, inputs.sources)
    finally:
        db.close()
