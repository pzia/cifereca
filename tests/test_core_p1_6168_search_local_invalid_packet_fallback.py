"""#6168: Search-local Core packet cannot downgrade to legacy cover URL."""
from __future__ import annotations

from tests.test_core_p1_6168_search_local_epub_documentref import search_book
from tests.test_search_results_view import _host


class VisibleRow:
    def __init__(self, key, title, author, meta, source, thumbnail):
        self.key = key
        self.title = title
        self.subtitle = author
        self.meta = meta
        self.thumbnail_url = thumbnail


def render_source(row, *, current="alice"):
    host = _host()
    host.service.current_identity.identity_id = current
    host._render_search_results.__func__.__globals__["BookListItem"] = VisibleRow
    host._search_local_objects = {row["key"]: row}
    assert host._render_search_results() == 1
    displayed, = host.search_page.show_rows.call_args.args[0]
    return displayed


def test_search_valid_packet_uses_n2_and_not_unattested_url(search_book):
    item, proof = search_book
    actual = render_source(item)
    assert actual._cover_document_ref == proof.scope
    assert actual.thumbnail_url is None


def test_search_invalid_or_foreign_packet_never_falls_back(search_book):
    item, _ = search_book
    for alteration in (
        {"identity_id": "bob"},
        {"placement_id": "a-different-placement"},
        {"artifact_id": "sha256:" + "f" * 64},
        {"key": "wrong-gtk-key"},
        {"_exact_local_cover_evidence": object()},
    ):
        row = {**item, **alteration}
        actual = render_source(row)
        assert not hasattr(actual, "_cover_document_ref")
        assert actual.thumbnail_url is None
    foreign = render_source(item, current="bob")
    assert not hasattr(foreign, "_cover_document_ref")
    assert foreign.thumbnail_url is None


def test_search_no_packet_keeps_historical_legacy_url(search_book):
    item, _ = search_book
    row = {k: v for k, v in item.items()
           if k != "_exact_local_cover_evidence"}
    actual = render_source(row)
    assert not hasattr(actual, "_cover_document_ref")
    assert actual.thumbnail_url == item["thumbnail_url"]
