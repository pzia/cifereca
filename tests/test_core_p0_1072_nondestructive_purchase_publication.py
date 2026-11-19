"""Non-destructive product publication through the existing C writer."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_purchase_publication import (
    prepare_purchase_public_observation, publish_purchase_public_txn,
)
from cifereca.core.notice_cover_repository import (
    read_notice_cover, evict_notice_cover, persist_notice_cover,
)
from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from cifereca.storage import Storage
from tests.test_core_p0_1072_durable_purchase_intent import prepare, set_policy, request
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row, URL


def observation(candidate, *, at=100, cover=None, title=None, product=None, owner="__public__",
                bibliography_metadata=None):
    row = public_row(
        source_provider="seller-a", source_credential=owner,
        title=title or candidate.title, friendly_url=product or candidate.public_locator,
        thumbnail_url=cover, description="Résumé public",
        metadata={"public_url_evidence": "seller-a.public_search_product_card",
                  **(bibliography_metadata or {})},
    )
    session = SimpleNamespace(provider_id="seller-a", credential_id=owner,
                              connected=True, credentials={}, _accounts=[])
    response = ProviderSearchResponse(
        results=[row], public_notices=(PublicSearchNotice(result=row),),
    )
    return prepare_purchase_public_observation(response, session, candidate, observed_at=at)


@pytest.mark.parametrize("mode", ["new", "existing", "evicted", "changed_policy", "rollback"])
def test_purchase_publication_preserves_exact_c_and_never_updates_existing_watermark(tmp_path, mode):
    db = Storage(tmp_path / "publication.sqlite")
    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")
        ref, = request(db, snapshot, candidate)
        spec = journal.read_job(db, ref).spec
        incoming = observation(candidate, at=101)
        document = notice_cover_document_ref(incoming.key)
        old = None
        if mode in ("existing", "evicted"):
            richer = observation(candidate, at=100, cover=URL)
            old = persist_notice_cover(
                db, richer, field_path=("search_result", "thumbnail_url"),
                cover_inventory_complete=True,
            )
            persist_exact_public_search_cover(db, old)
            if mode == "evicted":
                assert evict_notice_cover(db, document, expected_epoch=old.epoch)
        if mode == "changed_policy":
            set_policy(db, "false")

        def write():
            with db._lock, db._connect() as sql:
                sql.execute("BEGIN IMMEDIATE")
                result = publish_purchase_public_txn(
                    sql, spec, snapshot, candidate, incoming, now=102,
                )
                if mode == "rollback":
                    raise ValueError("synthetic rollback")
                return result

        if mode in ("evicted", "changed_policy", "rollback"):
            with pytest.raises(ValueError):
                write()
            assert read_notice_cover(db, document) is None
        else:
            published = write()
            assert published.created is (mode == "new")
            if old is not None:
                assert published.source == old
            else:
                assert published.source.attribute.state == "pending"
            hits = search_public_notice_c(db, "synthétique", now=102).hits
            assert hits and all(hit.last_seen_at == (100 if old else 101) for hit in hits)
            assert read_notice_cover(db, document) == published.source
        with db._lock, db._connect() as sql:
            count, = sql.execute("SELECT count(*) FROM metadata WHERE key>=? AND key<?",
                                 (journal._PREFIX, journal._END)).fetchone()
        assert count == (2 if mode == "new" else 1)
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        db.close()


@pytest.mark.parametrize("invalid", [
    {"title": "Autre titre"}, {"product": "https://catalogue.example.org/edition/456"},
    {"owner": "private"},
])
def test_requires_exact_same_call_public_title_url_and_anonymous_session(tmp_path, invalid):
    db = Storage(tmp_path / "invalid.sqlite")
    try:
        _, candidate = prepare(db)
        with pytest.raises(ValueError):
            observation(candidate, **invalid)
    finally:
        db.close()
