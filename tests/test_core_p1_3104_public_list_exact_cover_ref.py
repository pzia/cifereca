"""#3104/#6168: public plugin lists preserve only attested Core cover refs."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.core.book_cover_projection import CoverDocumentScope


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/public_book_view.py"


class ListItem:
    def __init__(self, key, title, subtitle, meta, badge, thumbnail_url):
        self.key = key
        self.title = title
        self.subtitle = subtitle
        self.meta = meta
        self.badge = badge
        self.thumbnail_url = thumbnail_url


class Page:
    def __init__(self, *_a):
        self.rows = []

    def show_rows(self, rows, total_count=None):
        self.rows = list(rows)


def view():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef)
               and x.name == "PublicBookMasterDetailView")
    ns = {"MasterDetailPage": Page, "BookListItem": ListItem, "Any": object}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[
            ast.alias(name="annotations")], level=0), cls,
    ], type_ignores=[])), str(SOURCE), "exec"), ns)
    return ns["PublicBookMasterDetailView"](NS(), title="Public")


def row(key, ref):
    return NS(
        key=key, title="Same title", subtitle="Same author",
        meta="", badge="", thumbnail_url="https://covers.example.org/x.jpg",
        _cover_document_ref=ref,
    )


def test_exact_core_public_ref_passes_to_shared_list_widget():
    v = view()
    ref = CoverDocumentScope(
        kind="public_catalogue", identity_id=None,
        credential_id="__public__", provider_id="bnf",
        source_key="notice-c:edition:1",
    )
    v.show_rows([row("same", ref)])
    item = v._page.rows[0]
    assert item._cover_document_ref is ref
    assert item.thumbnail_url == "https://covers.example.org/x.jpg"


def test_duplicate_titles_and_keys_do_not_conflate_distinct_editions():
    v = view()
    first = CoverDocumentScope(
        kind="public_catalogue", identity_id=None,
        credential_id="__public__", provider_id="bnf",
        source_key="notice-c:edition:1",
    )
    second = CoverDocumentScope(
        kind="public_catalogue", identity_id=None,
        credential_id="__public__", provider_id="bnf",
        source_key="notice-c:edition:2",
    )
    v.show_rows([row("duplicate", first), row("duplicate", second)])
    a, b = v._page.rows
    assert a.key != b.key
    assert a._cover_document_ref == first
    assert b._cover_document_ref == second
    assert a.thumbnail_url == b.thumbnail_url


def test_untrusted_plugin_string_or_duck_type_never_becomes_documentref():
    v = view()
    v.show_rows([
        row("url", "notice-c:edition:1"),
        row("duck", NS(kind="public_catalogue", source_key="notice-c:edition:1")),
        row("none", None),
    ])
    assert all(not hasattr(item, "_cover_document_ref") for item in v._page.rows)
    assert len(v._rows) == 3


def test_rerender_revokes_stale_ref_instead_of_reusing_old_row():
    v = view()
    ref = CoverDocumentScope(
        kind="public_catalogue", identity_id=None,
        credential_id="__public__", provider_id="bnf",
        source_key="notice-c:edition:1",
    )
    v.show_rows([row("same", ref)])
    old = v._page.rows[0]
    v.show_rows([row("same", None)])
    new = v._page.rows[0]
    assert old is not new
    assert not hasattr(new, "_cover_document_ref")
