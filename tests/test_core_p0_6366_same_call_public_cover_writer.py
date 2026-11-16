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
        assert read_notice_cover(db, doc).attribute.candidates[0].url == URL
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
