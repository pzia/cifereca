"""P1 #3104: Search/detail image parity without a selection-driven holdings loop.

Execute the production presentation publisher headlessly and require the
real GTK cell's observable-cover protocol by AST contract. The provider
image is catalogue evidence, never a right or a title-based join.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.models import SearchResult, ItemDetail

ROOT = Path(__file__).resolve().parents[1]
DETAILS = ROOT / "src/cifereca/gui/search_details.py"
RESULTS = ROOT / "src/cifereca/gui/search_results.py"
CELLS = ROOT / "src/cifereca/gui/book_widgets.py"

COVER = "https://covers.feedbooks.net/item/synthetic-late.jpg?size=medium&t=1762000002"


class Row:
    def __init__(self, key="provider:display-1"):
        self.key = key
        self.thumbnail_url = None
        self.changes = []

    def set_thumbnail_url(self, url):
        self.thumbnail_url = url
        self.changes.append(url)


def publisher():
    node = next(
        n for n in ast.parse(DETAILS.read_text(encoding="utf-8")).body
        if isinstance(n, ast.FunctionDef) and n.name == "_publish_search_detail_cover"
    )
    namespace = {
        "__name__": "cifereca.gui.search_details",
        "__package__": "cifereca.gui",
        "SearchResult": SearchResult,
        "ItemDetail": ItemDetail,
        "BookListItem": Row,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 str(DETAILS), "exec"), namespace)
    return namespace["_publish_search_detail_cover"]


def item(*, provider="bnf-pret", credential="card-a", record="edition-a"):
    return SearchResult(
        id="display-1", record_id=record, docbase="BNF_PRET",
        medium="digital", title="Titre identique", author=None, type=None,
        format="EPUB", publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="public",
        source_provider=provider, source_credential=credential,
    )


def detail(source, *, url=COVER, provider=None, credential=None, record=None):
    return ItemDetail(
        record_id=record if record is not None else source.record_id,
        docbase=source.docbase, title=source.title, author=None, type=None,
        year=None, thumbnail_url=url, availability_status="unknown",
        availability_label="", holdings=[],
        source_provider=provider if provider is not None else source.source_provider,
        source_credential=credential if credential is not None else source.source_credential,
    )


def host(source):
    row = Row()
    window = NS(
        _search_mode="global",
        _search_generation=3,
        _identity_context_generation=9,
        service=NS(_account_context_epoch=7, _source_context_epoch=4),
        search_objects={row.key: source},
        search_page=NS(selection=NS(get_selected_item=lambda: row)),
    )
    return window, row


def test_current_exact_detail_paints_existing_row_without_reselection_or_business_mutation():
    source = item()
    window, row = host(source)
    publisher()(window, source, detail(source))
    assert row.changes == [COVER]
    assert source.thumbnail_url is None
    assert window.search_objects[row.key] is source
    record = window._search_detail_cover_by_key[row.key]
    assert record == (source, COVER, "digital", 9, 3, 7, 4)
    assert window.search_page.selection.get_selected_item() is row


def test_live_row_override_expires_when_medium_changes_on_the_same_object(monkeypatch):
    from tests.test_search_results_view import Row as ViewRow, _host

    class CaptureRow(ViewRow):
        def __init__(self, key, title, author, meta, source, thumbnail, **options):
            super().__init__(key, title, author, meta, source, thumbnail, **options)
            self.thumbnail_url = thumbnail

    source = item()
    publisher_host, publisher_row = host(source)
    publisher_row.key = f"{source.source_provider}:{source.id}"
    publisher_host.search_objects = {publisher_row.key: source}
    publisher()(publisher_host, source, detail(source))
    view = _host()
    view._remote_search_results = [source]
    view._search_detail_cover_by_key = publisher_host._search_detail_cover_by_key
    view._identity_context_generation, view._search_generation = 9, 3
    view.service._account_context_epoch, view.service._source_context_epoch = 7, 4
    monkeypatch.setitem(view._render_search_results.__func__.__globals__,
                        "BookListItem", CaptureRow)

    view._populate_search([source], announce=False)
    assert view.search_page.show_rows.call_args.args[0][0].thumbnail_url == COVER

    source.medium = "physical"
    view._populate_search([source], announce=False)
    assert view.search_page.show_rows.call_args.args[0][0].thumbnail_url is None


@pytest.mark.parametrize("change", [
    {"provider": "other-provider"},
    {"credential": "card-b"},
    {"record": "other-edition"},
    {"url": "https://cdn.example/cover.jpg?token=private"},
    {"url": "https://127.0.0.1/cover.jpg"},
    {"url": "file:///home/alice/secret.png"},
    {"url": None},
])
def test_foreign_or_nonpublic_detail_cannot_publish_a_row_image(change):
    source = item()
    window, row = host(source)
    publisher()(window, source, detail(source, **change))
    assert not row.changes
    assert not hasattr(window, "_search_detail_cover_by_key")


def test_same_title_or_display_id_does_not_override_another_exact_row():
    source = item()
    other = item(credential="card-b")
    window, row = host(other)
    publisher()(window, source, detail(source))
    assert row.changes == []
    assert not hasattr(window, "_search_detail_cover_by_key")


def test_local_mode_or_unselected_row_blocks_detail_cover_publishing():
    source = item()
    window, row = host(source)
    window._search_mode = "local"
    publisher()(window, source, detail(source))
    assert row.changes == []
    window._search_mode = "global"
    window.search_page.selection.get_selected_item = lambda: None
    publisher()(window, source, detail(source))
    assert row.changes == []


def test_in_place_repaint_is_presentation_only_and_rerender_is_versioned():
    widgets = ast.parse(CELLS.read_text(encoding="utf-8"))
    model = next(n for n in widgets.body if isinstance(n, ast.ClassDef)
                 and n.name == "BookListItem")
    cell = next(n for n in widgets.body if isinstance(n, ast.ClassDef)
                and n.name == "BookListCell")
    setter = next(n for n in model.body if isinstance(n, ast.FunctionDef)
                  and n.name == "set_thumbnail_url")
    binder = next(n for n in cell.body if isinstance(n, ast.FunctionDef)
                  and n.name == "bind")
    unbind = next(n for n in cell.body if isinstance(n, ast.FunctionDef)
                  and n.name == "unbind")
    callback = next(n for n in cell.body if isinstance(n, ast.FunctionDef)
                    and n.name == "_thumbnail_changed")
    text = ast.unparse(setter)
    assert "self.emit('thumbnail-changed')" in text
    assert "self._public_row_content" in text
    assert "self._thumbnail_handler_id = item.connect(" in ast.unparse(binder)
    assert "self._unbind_thumbnail_signal()" in ast.unparse(unbind)
    assert "self.cover.bind_url(" in ast.unparse(callback)
    search = RESULTS.read_text(encoding="utf-8")
    assert "row._public_row_content = (" in search
    assert "retained[0] is item" in search
    assert "getattr(self, '_identity_context_generation', None)" in ast.unparse(
        ast.parse(search)
    )
    assert "self._render_search_results()" not in ast.unparse(
        next(n for n in ast.parse(DETAILS.read_text(encoding="utf-8")).body
             if isinstance(n, ast.FunctionDef) and n.name == "_publish_search_detail_cover")
    )


def test_search_detail_port_exposes_only_scoped_cover_capabilities():
    """Regression: MainWindow invokes publisher through SearchDetailPort.

    Missing _search_mode silently disabled the publisher via getattr default;
    missing _search_detail_cover_by_key prevented saving the scoped result.
    """
    from cifereca.gui.search_host_port import (
        SEARCH_DETAIL_CAPABILITIES, SearchDetailPort,
    )

    assert {"_search_mode", "_search_detail_cover_by_key"} <= SEARCH_DETAIL_CAPABILITIES
    source = item()
    window, row = host(source)
    window._search_detail_cover_by_key = {}
    port = SearchDetailPort(window)
    publisher()(port, source, detail(source))
    assert row.changes == [COVER]
    assert window._search_detail_cover_by_key[row.key][0] is source
    port._search_detail_cover_by_key = {}
    assert window._search_detail_cover_by_key == {}
    with pytest.raises(AttributeError):
        port._provider_credentials = "forbidden"
