"""#6168: exact Core v2 known-catalogue Search row gets its own holdings cover.

The real local Search publisher and GTK-free row renderer are executed via
the project's AST harness. No provider I/O, title/ISBN join or rights mutation.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from cifereca.core.known_catalog_provenance import known_catalog_search_result
from cifereca.gui.search_cover_bridge import SearchCoverBridge
from tests.test_core_p1_3104_search_detail_cover_live_row import (
    COVER, detail, host, publisher,
)
from tests.test_search_results_view import _host as search_host, Row

CONTEXT = (9, 3, 7, 4)


def known(*, identity="alice", credential="card-a", record="edition-a",
          docbase="BNF_PRET", collection="library", source="known_catalog",
          key="known:bnf-1", scope_version=2, thumbnail=None):
    return {
        "key": key,
        "source": source,
        "identity_id": identity,
        "provider_id": "bnf-pret",
        "item_id": record,
        "title": "Livre avec deux représentations",
        "author": "Autrice",
        "media_format": "EPUB",
        "thumbnail_url": thumbnail,
        "metadata": {
            "catalogue_scope_version": scope_version,
            "source_credential": credential,
            "docbase": docbase,
            "source_collection": collection,
            "medium": "digital",
        },
    }


def actual():
    row = known()
    notice = known_catalog_search_result(row, identity_id="alice")
    assert notice is not None
    window, gtk_row = host(notice)
    gtk_row.key = row["key"]
    window._search_mode = "local"
    window._search_local_objects = {gtk_row.key: row}
    window.service.current_identity = NS(identity_id="alice")
    window._selected_search_id = gtk_row.key
    return window, gtk_row, notice, row


def test_known_v2_explicit_holdings_paints_local_row_and_core_decision():
    window, gtk_row, notice, row = actual()
    assert row["thumbnail_url"] is None
    publisher()(window, notice, detail(notice))
    assert gtk_row.changes == [COVER]
    assert window._search_cover_bridge.selected_url(
        notice, identity_id="alice", context=CONTEXT,
    ) == COVER
    assert row["thumbnail_url"] is None  # no mutation of business state
    assert notice.thumbnail_url is None


@pytest.mark.parametrize("change", [
    {"identity_id": "bob"},
    {"item_id": "another-edition"},
    {"provider_id": "another-provider"},
    {"source": "unqualified"},
    {"key": "different-key"},
])
def test_foreign_known_row_cannot_acquire_detail_cover(change):
    window, gtk_row, notice, row = actual()
    altered = dict(row, **change)
    window._search_local_objects = {gtk_row.key: altered}
    publisher()(window, notice, detail(notice))
    assert gtk_row.changes == []
    assert not hasattr(window, "_search_cover_bridge")


@pytest.mark.parametrize("change", [
    {"source_credential": "card-b"},
    {"docbase": "OTHER"},
    {"medium": "physical"},
    {"source_collection": "other-library"},
    {"catalogue_scope_version": None},
    {"search_provenance": "known_catalog_stale"},
])
def test_unqualified_or_other_account_known_metadata_remains_read_only(change):
    window, gtk_row, notice, row = actual()
    altered = deepcopy(row)
    altered["metadata"].update(change)
    window._search_local_objects = {gtk_row.key: altered}
    publisher()(window, notice, detail(notice))
    assert gtk_row.changes == []


def test_local_mode_is_not_global_shortcut_for_any_search_result():
    window, gtk_row, notice, row = actual()
    window._search_local_objects = {}
    window.search_objects = {gtk_row.key: notice}
    publisher()(window, notice, detail(notice))
    assert gtk_row.changes == []
    assert not hasattr(window, "_search_cover_bridge")


@pytest.mark.parametrize("context_index", [0, 1, 2, 3])
def test_known_source_cover_rejects_changed_identity_search_or_core_epoch(
    context_index,
):
    window, gtk_row, notice, row = actual()
    publisher()(window, notice, detail(notice))
    revised = list(CONTEXT)
    revised[context_index] += 1
    bridge = window._search_cover_bridge
    assert bridge.selected_url(
        notice, identity_id="alice", context=tuple(revised),
    ) is None


def test_actual_local_search_renderer_uses_exact_cached_image_after_row_rebuild():
    window, gtk_row, notice, library = actual()
    publisher()(window, notice, detail(notice))
    host_window = search_host()
    host_window._search_mode = "local"
    host_window._identity_context_generation = CONTEXT[0]
    host_window._search_generation = CONTEXT[1]
    host_window.service._account_context_epoch = CONTEXT[2]
    host_window.service._source_context_epoch = CONTEXT[3]
    host_window._search_local_objects = {library["key"]: deepcopy(library)}
    host_window._search_cover_bridge = window._search_cover_bridge

    class CaptureRow(Row):
        def __init__(self, key, title, author, meta, source, thumbnail, **options):
            super().__init__(key, title, author, meta, source, thumbnail, **options)
            self.thumbnail_url = thumbnail

    host_window._render_search_results.__func__.__globals__["BookListItem"] = CaptureRow
    host_window._render_search_results()
    rows = host_window.search_page.show_rows.call_args.args[0]
    assert len(rows) == 1
    assert rows[0].thumbnail_url == COVER
    assert host_window.service.local_placements_for_sources.call_count == 0

    # Wrong borrower's return to a visually identical list cannot inherit it.
    host_window.service.current_identity = NS(identity_id="bob")
    host_window._render_search_results()
    rows = host_window.search_page.show_rows.call_args.args[0]
    assert rows == [] or all(r.thumbnail_url is None for r in rows)


def test_known_legacy_or_stale_row_never_borrows_exact_image_in_local_list():
    window, gtk_row, notice, library = actual()
    publisher()(window, notice, detail(notice))
    host_window = search_host()
    host_window._search_mode = "local"
    host_window._identity_context_generation = CONTEXT[0]
    host_window._search_generation = CONTEXT[1]
    host_window.service._account_context_epoch = CONTEXT[2]
    host_window.service._source_context_epoch = CONTEXT[3]
    host_window._search_cover_bridge = window._search_cover_bridge

    class CaptureRow(Row):
        def __init__(self, key, title, author, meta, source, thumbnail, **options):
            super().__init__(key, title, author, meta, source, thumbnail, **options)
            self.thumbnail_url = thumbnail

    host_window._render_search_results.__func__.__globals__["BookListItem"] = CaptureRow
    for changed in (
        {"catalogue_scope_version": None},
        {"source_credential": "card-b"},
        {"search_provenance": "known_catalog_stale"},
    ):
        bad = deepcopy(library)
        bad["metadata"].update(changed)
        host_window._search_local_objects = {bad["key"]: bad}
        host_window._render_search_results()
        rows = host_window.search_page.show_rows.call_args.args[0]
        assert len(rows) == 1 and rows[0].thumbnail_url is None


def test_selected_private_holdings_url_does_not_repaint_known_list():
    window, gtk_row, notice, row = actual()
    publisher()(window, notice, detail(
        notice, url="https://public.example.org/cover.jpg?token=PRIVATE",
    ))
    assert gtk_row.changes == []
    assert not hasattr(window, "_search_cover_bridge")
