"""Exact relink input contract; real public C/OriginLink fixtures on main."""
import pytest

from cifereca.core.notice_c_relink_inputs import (
    capture_public_relink_inputs_txn, check_public_relink_inputs_txn,
)
from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, persist_consolidated_cover_source,
    read_consolidated_notice_cover,
)
from cifereca.core.notice_c_origin_link_producer import reconcile_origin_linked_public_c
from cifereca.core.notice_cover_repository import read_notice_cover, evict_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_6366_origin_attested_public_c import (
    _origins, _source_rows, _mirror, new_source, register, publish, target,
    witnessed, public_row,
)
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


def group(db, tmp_path):
    _origins(db, tmp_path)
    a, b = _source_rows()
    assert _mirror(db, a) == 1 and _mirror(db, b) == 1
    ref = b._core_notice_canonical_cover_document_ref
    assert len(read_consolidated_notice_cover(db, ref).sources) == 2
    return ref, tuple(read_notice_cover(db, row._core_notice_cover_document_ref)
                      for row in (a, b))


def add_third(db, tmp_path):
    key, snapshot = new_source(
        "A", provider="seller-c", credential="card-c",
        edition="edition-c", right="right-c",
    )
    register(db, "A", "seller-c", "card-c")
    publish(db, tmp_path / "EBooks", target(tmp_path, "A"), key, snapshot)
    row = witnessed(public_row(
        source_provider="seller-c", record_id="edition-c",
        title="L'ouvrage commun", thumbnail_url="https://covers.example.org/third.jpg",
    ))
    return persist(db, row, at=100)


def capture(db, ref, sources):
    with db._lock, db._connect() as sql:
        sql.execute("BEGIN")
        return capture_public_relink_inputs_txn(sql, ref, sources)


def check(db, ref, sources, vector):
    with db._lock, db._connect() as sql:
        sql.execute("BEGIN IMMEDIATE")
        return check_public_relink_inputs_txn(
            sql, ref, sources, expected_digest=vector.input_digest,
        )


def test_extension_vector_includes_new_member_and_preserves_existing_document(tmp_path):
    path = tmp_path / "relink.sqlite"
    db = Storage(path)
    try:
        ref, pair = group(db, tmp_path)
        old = read_consolidated_notice_cover(db, ref)
        third = add_third(db, tmp_path)
        persist_exact_public_search_cover(db, third)
        sources = (*pair, third)
        vector = capture(db, ref, sources)
        assert len(vector.source_refs) == 3
        assert capture(db, ref, tuple(reversed(sources))) == vector
        assert vector != capture(db, ref, pair)
        # Unrelated C does not take part in the exact input vector.
        unrelated = persist(db, verified_public(record_id="other"), at=100)
        persist_exact_public_search_cover(db, unrelated)
        assert check(db, ref, sources, vector) == vector
        assert read_consolidated_notice_cover(db, ref) == old
    finally:
        db.close()

    db = Storage(path)
    try:
        assert check(db, ref, sources, vector) == vector
        linked = reconcile_origin_linked_public_c(db, third)
        assert linked is not None and linked.document == ref
        assert len(linked.sources) == 3 and linked.epoch > old.epoch
        with pytest.raises(ValueError, match="inputs_changed"):
            check(db, ref, sources, vector)
        with pytest.raises(ValueError, match="membership_changed"):
            capture(db, ref, pair)
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_overlap_and_source_change_invalidate_vector(tmp_path):
    db = Storage(tmp_path / "changed.sqlite")
    try:
        ref, pair = group(db, tmp_path)
        third = add_third(db, tmp_path)
        sources = (*pair, third)
        vector = capture(db, ref, sources)
        # Another overlapping C claim changes the vector even though no
        # source row and no retained anchor member has changed.
        foreign = persist_consolidated_cover_source(
            db, canonical_edition_id="edition:foreign-relink",
            source=third, relation_proof_id="fixture:foreign",
        )
        assert foreign.document != ref
        assert capture(db, ref, sources).overlap_count == vector.overlap_count + 1
        with pytest.raises(ValueError, match="inputs_changed"):
            check(db, ref, sources, vector)
        vector2 = capture(db, ref, sources)
        assert evict_notice_cover(db, third.document, expected_epoch=third.epoch)
        with pytest.raises(ValueError, match="source_changed"):
            check(db, ref, sources, vector2)
        assert len(read_consolidated_notice_cover(db, ref).sources) == 2
    finally:
        db.close()


def test_digest_equality_never_replaces_origin_authority(tmp_path):
    db = Storage(tmp_path / "revoked.sqlite")
    try:
        ref, pair = group(db, tmp_path)
        third = add_third(db, tmp_path)
        sources = (*pair, third)
        vector = capture(db, ref, sources)
        with db._lock, db._connect() as sql:
            sql.execute(
                "DELETE FROM origin_links WHERE source_reference_id IN ("
                "SELECT source_reference_id FROM source_references "
                "WHERE provider_id='seller-c')"
            )
        # The public vector is intentionally not a private graph snapshot.
        assert check(db, ref, sources, vector) == vector
        assert reconcile_origin_linked_public_c(db, third) is None
        assert len(read_consolidated_notice_cover(db, ref).sources) == 2
        with db._lock, db._connect() as sql:
            with pytest.raises(ValueError, match="transaction_required"):
                capture_public_relink_inputs_txn(sql, ref, sources)
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
