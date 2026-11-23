"""#2385: source-mode rows preserve exact source identity, not just provider names."""
from cifereca.core.notice_c_catalogue_views import project_public_catalogue_view
from tests.test_core_p1_2385_catalogue_views import _page


def test_public_catalogue_exact_source_coordinates():
    page = _page()
    for mode in ("list", "grid", "compact", "detail", "sources", "timeline"):
        view = project_public_catalogue_view(page, mode=mode)
        expected = page.matching_sources if mode == "sources" else page.hits
        assert len(view.rows) == len(expected)
        for row, hit in zip(view.rows, expected):
            assert (row.record_id, row.docbase, row.medium, row.source_revision) == (
                hit.record_id, hit.docbase, hit.medium, hit.source_revision
            )
    sources = project_public_catalogue_view(page, mode="sources")
    assert {row.record_id for row in sources.rows} == {"record:1", "record:2"}
    assert sources.known_documents == 1
