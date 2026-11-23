"""#2385: all six public C view modes preserve known-fact boundaries."""
from dataclasses import replace

import pytest

from cifereca.core.notice_c_catalogue_views import project_public_catalogue_view
from cifereca.core.notice_c_public_search import PublicNoticeCHit, PublicNoticeCPage
from cifereca.core.notice_cover_projection import notice_cover_document_ref
from tests.test_core_p1_4552_public_age_descriptive_facts import observation


def _page():
    document = notice_cover_document_ref(observation().key)
    hit = PublicNoticeCHit(
        document=document, provider_id="provider.example", record_id="record:1",
        docbase="docbase:1", medium="digital", source_revision="rev:1",
        title="Book", author="Author", publisher=None, media_format=None,
        description="Description", observed_at=10, last_seen_at=20,
        epoch=1, cover_state="pending", stale=True, edition_id="edition:1",
    )
    second = replace(hit, provider_id="provider.other", record_id="record:2")
    return PublicNoticeCPage(
        hits=(hit,), matching_sources=(hit, second), known_matches=2,
        scanned_sources=2, unindexed_sources=0, status="partial",
        has_more=True, known_documents=1,
    )


@pytest.mark.parametrize("mode", (
    "list", "grid", "compact", "detail", "sources", "timeline",
))
def test_six_views_preserve_partial_and_provenance(mode):
    view = project_public_catalogue_view(_page(), mode=mode)
    assert view.status == "partial" and view.has_more
    assert view.known_matches == 2 and view.known_documents == 1
    assert len(view.rows) == (2 if mode == "sources" else 1)
    assert view.rows[0].source_count == 2
    assert view.rows[0].stale
    assert view.rows[0].description == ("Description" if mode == "detail" else None)


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        project_public_catalogue_view(_page(), mode="borrow")
