"""Corroborate a verified EPUB claim against exact retained public C."""
from types import SimpleNamespace

import pytest

from cifereca.core import search_orchestration
from cifereca.core.local_origin_suggestion_preview import suggest_local_placement_documents
from cifereca.core.notice_cover_repository import evict_notice_cover, read_notice_cover
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from tests.test_core_p0_4030_epub_evidence import META, _local_epub
from tests.test_core_p0_3171_atomic_publication_v14 import rows, store
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row


URL = "https://books.example.org/title/opf-edition"


def publish(store, url, *, record, now=101):
    raw = public_row(
        id=record, record_id=record, source_provider="public-books",
        title="Roman commun", author="Autrice", friendly_url=url,
        metadata={"public_url_evidence": "public-books.public_search_product_card"},
    )
    projected, = search_orchestration._public_rows_from_response(
        ProviderSearchResponse([raw], public_notices=(PublicSearchNotice(raw),)),
        SimpleNamespace(credential_id="__public__", provider_id="public-books",
                        credentials={}, _accounts=[]),
    )
    search_orchestration._ingest_public_search_c(
        SimpleNamespace(storage=store), [projected], identity_id="A",
        require_public_cover_witness=True, now=now,
    )
    return projected._core_notice_cover_document_ref


@pytest.mark.parametrize("case", ["live", "evicted", "duplicate", "paginated", "corrected"])
def test_known_c_corroborates_only_a_unique_current_source(store, tmp_path, case):
    metadata = META + f'<dc:source xmlns:dc="http://purl.org/dc/elements/1.1/">{URL}</dc:source>'
    root, file, placement = _local_epub(store, tmp_path, metadata)
    source = publish(store, URL, record="exact")
    if case == "evicted":
        current = read_notice_cover(store, source)
        assert evict_notice_cover(store, source, expected_epoch=current.epoch)
    elif case == "duplicate":
        publish(store, URL, record="other")
    elif case == "corrected":
        publish(store, URL + "-correction", record="exact", now=102)

    before, data = rows(store), file.read_bytes()
    connection = store._connect()
    changes = connection.total_changes
    report = suggest_local_placement_documents(
        store, library_root=root, identity_id="A", placement_id=placement,
        explicitly_requested=True, page_size=1 if case == "paginated" else 20,
    )
    page = report["suggestions"]
    qualified = tuple(row.hit.record_id for row in page.suggestions
                      if row.comparison == "opf_source_url_concordant")
    assert qualified == (("exact",) if case in ("live", "evicted") else ())
    if case == "paginated":
        assert page.has_more and report["continuation"] is not None
    assert not report["provider_access_performed"] and not report["association_authorized"]
    assert URL not in repr(report)
    assert rows(store) == before and file.read_bytes() == data
    assert connection.total_changes == changes


@pytest.mark.parametrize("limit", ["unindexed", "budget"])
def test_incomplete_known_c_scan_cannot_promote_optional_opf_claim(
    store, tmp_path, monkeypatch, limit,
):
    from dataclasses import replace
    from cifereca.core import local_origin_documentary_suggestions as documentary

    metadata = META + f'<dc:source xmlns:dc="http://purl.org/dc/elements/1.1/">{URL}</dc:source>'
    root, file, placement = _local_epub(store, tmp_path, metadata)
    publish(store, URL, record="exact")
    original = documentary.search_public_notice_c

    def limited(*args, **kwargs):
        value = original(*args, **kwargs)
        return replace(value, unindexed_sources=1) if limit == "unindexed" else replace(
            value, status="budget_exceeded", has_more=True)

    monkeypatch.setattr(documentary, "search_public_notice_c", limited)
    before, data = rows(store), file.read_bytes()
    report = suggest_local_placement_documents(
        store, library_root=root, identity_id="A", placement_id=placement,
        explicitly_requested=True,
    )
    match, = report["suggestions"].suggestions
    assert match.comparison == "title_author_concordant"
    assert not report["association_authorized"] and not report["provider_access_performed"]
    assert URL not in repr(report)
    assert rows(store) == before and file.read_bytes() == data
