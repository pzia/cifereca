"""The legacy origin group candidate is not authority after graph revocation."""
import pytest

from cifereca.core import notice_c_cover_index as index
from cifereca.core import notice_c_origin_link_producer as origin
from cifereca.storage import Storage
from tests.test_core_p0_6366_origin_attested_public_c import (
    _origins, _source_rows, witnessed, public_row, A_URL,
)
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist


def _pair(db):
    sources = tuple(persist(db, row, at=100) for row in _source_rows())
    for source in sources:
        index.persist_exact_public_search_cover(db, source)
    return sources


@pytest.mark.parametrize("existing", [False, True])
def test_revoke_origin_after_legacy_discovery_before_writer(tmp_path, monkeypatch, existing):
    db = Storage(tmp_path / "race.sqlite")
    try:
        _origins(db, tmp_path)
        a, b = _pair(db)
        old = origin.reconcile_origin_linked_public_c(db, a) if existing else None
        assert (old is not None) == existing
        if existing:
            a = persist(db, witnessed(public_row(
                source_provider="seller-a", record_id="edition-a",
                title="L'ouvrage commun", thumbnail_url=A_URL,
                description="Résumé corrigé",
            )), at=101)
            index.persist_exact_public_search_cover(db, a)
        original = origin.persist_origin_linked_public_cover_group
        candidates = []

        def revoke_then_write(storage, *, sources):
            candidates.append(sources)
            with db._lock, db._connect() as sql:
                sql.execute(
                    "DELETE FROM origin_links WHERE source_reference_id IN ("
                    "SELECT source_reference_id FROM source_references WHERE provider_id='seller-b')"
                )
            return original(storage, sources=sources)

        monkeypatch.setattr(origin, "persist_origin_linked_public_cover_group", revoke_then_write)
        failures = []
        assert origin.reconcile_origin_linked_public_batch(db, [a], failed_sources=failures) == {}
        assert len(candidates) == 1 and set(s.document.source_key for s in candidates[0]) == {
            a.document.source_key, b.document.source_key,
        }
        assert failures == [a]
        if old is not None:
            assert index.read_consolidated_notice_cover(db, old.document) == old
        with db._lock, db._connect() as sql:
            count = sql.execute("SELECT count(*) FROM metadata WHERE key>=? AND key<?",
                                (index._PREFIX, index._PREFIX + "\uffff")).fetchone()[0]
        assert count == (3 if existing else 2)
        for source in (a, b):
            doc = index.canonical_cover_document_ref(source.observation.key.edition_id)
            own = index.read_consolidated_notice_cover(db, doc)
            assert len(own.sources) == 1 and own.sources[0].source_epoch == source.epoch
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_direct_origin_writer_cannot_publish_without_actual_graph(tmp_path):
    db = Storage(tmp_path / "no-proof.sqlite")
    try:
        sources = _pair(db)
        with pytest.raises(ValueError, match="notice_origin_relation_changed"):
            index.persist_origin_linked_public_cover_group(db, sources=sources)
        assert origin.reconcile_origin_linked_public_c(db, sources[0]) is None
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
