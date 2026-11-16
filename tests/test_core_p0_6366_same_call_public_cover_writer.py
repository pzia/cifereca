"""#6366/#3724: no Notice C publication from a forged public-looking row.

A same-call PublicSearchNotice from a genuinely anonymous *producing*
session is Core's only public search cover authority. The ordinary catalogue
mirror remains descriptive/legacy; a SearchResult, URL or cache replay alone
cannot publish an attributed Notice C source in federated production.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from types import SimpleNamespace as NS

from cifereca.core import search_orchestration
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.search_orchestration import _public_rows_from_response
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from cifereca.models import SearchResult
from cifereca.storage import Storage
from tests.test_core_p0_6366_real_search_notice_c_ingestion import (
    public_row, OTHER_URL, URL, source_count,
)


def mirror(db, rows):
    return search_orchestration._mirror_remote_results(
        NS(storage=db), rows, identity_id="alice",
        require_public_cover_witness=True,
    )


def witnessed(row):
    session = NS(
        provider_id=row.source_provider,
        credential_id="__public__", credentials={}, _accounts=[],
    )
    response = ProviderSearchResponse(
        results=[row], public_notices=(PublicSearchNotice(row),),
    )
    rows = _public_rows_from_response(response, session)
    assert len(rows) == 1 and rows[0] is not row
    return rows[0]


def test_legacy_provider_public_shaped_row_cannot_write_core_notice_c(tmp_path):
    db = Storage(tmp_path / "notices.sqlite")
    try:
        raw = public_row(metadata={"catalogue_access": "shared_read_only"})
        assert mirror(db, [raw]) == 1  # Historical descriptive mirror only.
        assert source_count(db) == 0
        assert not hasattr(raw, "_core_notice_cover_document_ref")

        safe = witnessed(raw)
        assert safe.thumbnail_url == URL
        assert mirror(db, [safe]) == 1
        doc = safe._core_notice_cover_document_ref
        assert doc.identity_id is None
        persisted = read_notice_cover(db, doc)
        assert persisted.attribute.candidates[0].url == URL
        # The existing v2 bibliographic witness must survive this cover gate.
        bibliography = persisted.observation.fields_copy()["search_result"]["public_bibliography"]
        assert bibliography["title"] == safe.title
        assert source_count(db) == 1
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_witness_is_bound_to_exact_row_edition_url_not_equal_looking_copy(
    tmp_path,
):
    db = Storage(tmp_path / "notices.sqlite")
    try:
        raw = public_row(revision_id="edition-v1")
        accepted = witnessed(raw)
        # Clone and serialized/replayed rows have NO Core witness.
        variants = [
            replace(accepted),
            SearchResult(**asdict(accepted)),
            replace(accepted, revision_id="edition-v2"),
            replace(accepted, thumbnail_url=OTHER_URL),
            replace(accepted, source_collection="other_public"),
            replace(accepted, source_account_id="owned-account"),
        ]
        assert all(
            not hasattr(candidate, "_core_same_call_public_cover_witness")
            for candidate in variants
        )
        for row in variants:
            assert mirror(db, [row]) == 1
        assert source_count(db) == 0

        assert mirror(db, [accepted]) == 1
        original = accepted._core_notice_cover_document_ref
        assert read_notice_cover(db, original).epoch == 1
        # Mutate the already-witnessed live object between parse and write.
        # A stale epoch/source/image cannot reuse its former same-call proof.
        accepted.thumbnail_url = OTHER_URL
        assert mirror(db, [accepted]) == 1
        assert source_count(db) == 1
        assert read_notice_cover(db, original).epoch == 1
        assert read_notice_cover(db, original).attribute.candidates[0].url == URL
    finally:
        db.close()


def test_private_or_unsigned_session_does_not_create_public_writer_witness(
    tmp_path,
):
    db = Storage(tmp_path / "notices.sqlite")
    source = public_row()
    sessions = (
        NS(provider_id="bnf", credential_id="__public__",
           credentials={"password": "private"}, _accounts=[]),
        NS(provider_id="bnf", credential_id="__public__",
           credentials={}, _accounts=[NS(account_id="private")]),
        NS(provider_id="bnf", credential_id="private-user",
           credentials={}, _accounts=[]),
    )
    try:
        response = ProviderSearchResponse(
            results=[source], public_notices=(PublicSearchNotice(source),),
        )
        for session in sessions:
            projected = _public_rows_from_response(response, session)
            for row in projected:
                assert not hasattr(row, "_core_same_call_public_cover_witness")
                assert mirror(db, [row]) == 1
        assert source_count(db) == 0

        wrong = ProviderSearchResponse(
            results=[source], public_notices=(PublicSearchNotice(replace(source)),),
        )
        projected = _public_rows_from_response(
            wrong, NS(provider_id="bnf", credential_id="__public__",
                      credentials={}, _accounts=[]),
        )
        assert all(
            not hasattr(row, "_core_same_call_public_cover_witness")
            for row in projected
        )
        assert source_count(db) == 0
    finally:
        db.close()


def test_live_search_scope_uses_unchanged_facade_and_resets_after_callback(tmp_path):
    """The real two search paths use the legacy façade without a new kwarg."""
    import pytest

    db = Storage(tmp_path / "live-scope.sqlite")
    try:
        host = NS(storage=db)
        def mirror_facade(rows, *, identity_id=None):
            return search_orchestration._mirror_remote_results(
                host, rows, identity_id=identity_id,
            )
        host._mirror_remote_results = mirror_facade
        raw = public_row()
        assert search_orchestration._mirror_live_search_results(
            host, [raw], identity_id="alice",
        ) == 1
        assert source_count(db) == 0
        safe = witnessed(raw)
        assert search_orchestration._mirror_live_search_results(
            host, [safe], identity_id="alice",
        ) == 1
        assert source_count(db) == 1
        assert search_orchestration._CORE_LIVE_PUBLIC_C_WRITER.get() is False

        def rejected(rows, *, identity_id=None):
            raise ValueError("cancelled test callback")
        host._mirror_remote_results = rejected
        with pytest.raises(ValueError, match="cancelled test callback"):
            search_orchestration._mirror_live_search_results(
                host, [raw], identity_id="alice",
            )
        assert search_orchestration._CORE_LIVE_PUBLIC_C_WRITER.get() is False
    finally:
        db.close()


def test_same_call_live_writer_publishes_canonical_cover_for_shared_n2(tmp_path):
    from cifereca.core.notice_c_cover_index import (
        is_canonical_cover_document, read_consolidated_notice_cover,
    )
    from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot

    db = Storage(tmp_path / "canonical-live.sqlite")
    try:
        safe = witnessed(public_row())
        assert mirror(db, [safe]) == 1
        doc = safe._core_notice_c_canonical_document_ref
        assert safe._core_notice_cover_document_ref != doc
        assert is_canonical_cover_document(doc)
        c = read_consolidated_notice_cover(db, doc)
        assert c is not None and c.epoch == 1
        assert len(c.sources) == 1
        assert c.sources[0].relation_proof_id == "core:exact-source:v1"
        assert c.candidates[0].url == URL
        snapshot = cached_notice_cover_snapshot(NS(storage=db), doc)
        assert snapshot.scope == doc
        assert snapshot.attested_book_identity == doc.source_key
        assert snapshot.candidates[0].url == URL
        assert mirror(db, [safe]) == 1
        assert read_consolidated_notice_cover(db, doc).epoch == 1
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_distinct_exact_editions_never_share_canonical_cover(tmp_path):
    from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover

    db = Storage(tmp_path / "canonical-isolation.sqlite")
    try:
        first = witnessed(public_row(revision_id="edition-v1"))
        second = witnessed(public_row(revision_id="edition-v2"))
        assert mirror(db, [first]) == 1
        assert mirror(db, [second]) == 1
        a = first._core_notice_c_canonical_document_ref
        b = second._core_notice_c_canonical_document_ref
        assert a != b
        assert len(read_consolidated_notice_cover(db, a).sources) == 1
        assert len(read_consolidated_notice_cover(db, b).sources) == 1
    finally:
        db.close()
