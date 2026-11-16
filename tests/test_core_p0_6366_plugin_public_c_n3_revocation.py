"""P0 #6366: Recommendations C list/detail cannot paint after an owner rebind.

Exercise the real neutral PublicBookMasterDetailView and BookListCell methods
headlessly, using an actual canonical Core C DocumentRef (not a title/URL
guess). C is documentary only: no entitlement, provider I/O or cache DB.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
from cifereca.gui.plugin_public_c_guard import (
    PinnedPluginPublicC, plugin_public_c_current,
    plugin_public_c_cell_current, plugin_public_c_detail_current,
    selected_plugin_public_c,
)
from cifereca.plugin_api.models import UiBookDetailPresentation, UiBookListEntry
from tests.test_core_p1_3104_master_detail_visual_signature import (
    BookRow, page_with, production_methods,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
CANONICAL = canonical_cover_document_ref("edition:recommendation-n3")
SECOND = canonical_cover_document_ref("edition:another-public-edition")
RAW = "https://covers.example.org/known-public-same.jpg"


class Row:
    def __init__(self, key, title, subtitle, meta, badge, thumbnail_url):
        self.key = key
        self.title = title
        self.subtitle = subtitle
        self.meta = meta
        self.badge = badge
        self.thumbnail_url = thumbnail_url


class Selection:
    def __init__(self):
        self.current = None
        self.cleared = []

    def get_selected_item(self):
        return self.current

    def set_selected(self, value):
        self.cleared.append(value)
        self.current = None


class Panel:
    def __init__(self):
        self._next_local_public_c_check = None
        self.renders = []
        self.actions = []
        self.error = None

    def show_presentation(self, presentation):
        self.renders.append((
            presentation,
            self._next_local_public_c_check,
        ))
        if self.error is not None:
            raise self.error

    def add_actions(self, actions):
        self.actions.append(tuple(actions))


class Page:
    def __init__(self, *_args, **_kwargs):
        self.rows = []
        self.selection = Selection()
        self.detail = Panel()

    def show_rows(self, rows, total_count=None):
        self.rows = list(rows)


def _view():
    file = ROOT / "public_book_view.py"
    tree = ast.parse(file.read_text(encoding="utf-8"))
    node = next(x for x in tree.body
                if isinstance(x, ast.ClassDef)
                and x.name == "PublicBookMasterDetailView")
    body = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(
            module="__future__",
            names=[ast.alias(name="annotations")], level=0,
        ), node,
    ], type_ignores=[]))
    scope = {
        "MasterDetailPage": Page, "BookListItem": Row,
        "Any": object,
    }
    exec(compile(body, str(file), "exec"), scope)
    service = NS(
        current_identity=NS(identity_id="alice"),
        _account_context_epoch=7,
        _source_context_epoch=9,
    )
    window = NS(service=service, _closed=False)
    view = scope["PublicBookMasterDetailView"](
        window, title="Recommandations",
    )
    return view, window, service


def _book(*, key="recommendation:one", document=CANONICAL):
    return UiBookListEntry(
        key=key, title="Le même livre", subtitle="Autrice",
        meta="Pourquoi : Catalogue public", badge="Affinité 83/100",
        thumbnail_url=RAW, cover_document_ref=document,
    )


def _detail(document=CANONICAL):
    return UiBookDetailPresentation(
        title="Le même livre", subtitle="Autrice",
        thumbnail_url=RAW, cover_document_ref=document,
    )


def _method(name):
    file = ROOT / "book_widgets.py"
    tree = ast.parse(file.read_text(encoding="utf-8"))
    klass = next(x for x in tree.body
                 if isinstance(x, ast.ClassDef)
                 and x.name == "BookListCell")
    fn = next(x for x in klass.body
              if isinstance(x, ast.FunctionDef) and x.name == name)
    module = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(
            module="__future__",
            names=[ast.alias(name="annotations")], level=0,
        ), fn,
    ], type_ignores=[]))
    ns = {
        "__name__": "cifereca.gui.book_widgets",
        "__package__": "cifereca.gui",
    }
    exec(compile(module, str(file), "exec"), ns)
    return ns[name]


def _selected():
    view, window, service = _view()
    source = _book()
    view.show_rows((source,))
    visible = view._page.rows[0]
    view._page.selection.current = visible
    return view, window, service, source, visible


def test_genuine_public_c_plugin_row_stamped_and_revalidated_on_both_gtk_paths():
    view, window, service, source, visible = _selected()
    receipt = visible._cover_plugin_c_receipt
    assert type(receipt) is PinnedPluginPublicC
    assert receipt.source_row is source
    assert receipt.document == CANONICAL
    assert plugin_public_c_cell_current(visible, receipt)
    assert selected_plugin_public_c(view, CANONICAL) == (visible, receipt)

    # Use the ACTUAL cell methods, not just an abstract verifier.
    cell = NS(
        window=window, _bind_epoch=3,
        _bound_key=visible.key, _thumbnail_item=visible,
    )
    assert _method("_admit_core_local_cover")(
        cell, visible, object(), CANONICAL,
    )
    cellcheck = _method("_search_cover_guard")(cell, visible)["is_current"]
    assert cellcheck()
    cell._bind_epoch += 1
    assert not cellcheck()
    cell._bind_epoch -= 1
    assert cellcheck()
    cell._thumbnail_item = NS(key=visible.key)
    assert not cellcheck()
    cell._thumbnail_item = visible
    assert cellcheck()
    service._source_context_epoch += 1
    assert not cellcheck()
    assert not _method("_admit_core_local_cover")(
        cell, visible, object(), CANONICAL,
    )
    service._source_context_epoch -= 1
    assert cellcheck()
    window._closed = True
    assert not cellcheck()


@pytest.mark.parametrize("alter", [
    "identity", "account_epoch", "source_epoch", "service",
    "page", "mapping", "document", "selected",
])
def test_plugin_public_c_n3_revoked_on_every_source_ownership_transition(alter):
    view, window, service, source, visible = _selected()
    receipt = visible._cover_plugin_c_receipt
    assert plugin_public_c_current(receipt)
    assert plugin_public_c_detail_current(view, visible, receipt)
    if alter == "identity":
        service.current_identity = NS(identity_id="bob")
    elif alter == "account_epoch":
        service._account_context_epoch += 1
    elif alter == "source_epoch":
        service._source_context_epoch += 1
    elif alter == "service":
        window.service = NS(**vars(service))
    elif alter == "page":
        view._public_c_page_generation += 1
    elif alter == "mapping":
        view._rows[visible.key] = _book()
    elif alter == "document":
        view._rows[visible.key] = UiBookListEntry(
            **{**source.__dict__, "cover_document_ref": SECOND},
        ) if hasattr(source, "__dict__") else _book(document=SECOND)
    else:
        view._page.selection.current = NS(key=visible.key)
    if alter == "selected":
        assert plugin_public_c_current(receipt)
    else:
        assert not plugin_public_c_current(receipt)
    assert not plugin_public_c_detail_current(view, visible, receipt)


def test_plugin_rerender_replaces_even_equal_public_c_and_aborts_old_n3():
    view, window, service, source, visible = _selected()
    old_receipt = visible._cover_plugin_c_receipt
    equal = _book()
    assert source == equal and source is not equal
    view.show_rows((equal,))
    next_row = view._page.rows[0]
    assert next_row._cover_plugin_c_source is equal
    assert plugin_public_c_cell_current(next_row, next_row._cover_plugin_c_receipt)
    assert not plugin_public_c_current(old_receipt)
    assert old_receipt.document == next_row._cover_document_ref

    old = BookRow("recommendation:one", cover=RAW)
    newer = BookRow("recommendation:one", cover=RAW)
    old._cover_document_ref = newer._cover_document_ref = CANONICAL
    old._cover_plugin_c_receipt = old_receipt
    newer._cover_plugin_c_receipt = next_row._cover_plugin_c_receipt
    old._public_row_content = newer._public_row_content = (
        "same title", CANONICAL, RAW,
    )
    same, show = production_methods()
    assert not same(old, newer)
    page, store, selected, perf, _states, _counts = page_with(old)
    show(page, [newer])
    assert store.items[0] is newer
    assert perf[-1][0] == "list.replace"


def test_plugin_detail_uses_one_staged_n3_check_and_cleans_it():
    view, window, service, source, visible = _selected()
    panel = view._page.detail
    view.show_detail(_detail())
    assert len(panel.renders) == 1
    rendered, check = panel.renders[0]
    assert rendered.cover_document_ref == CANONICAL
    assert callable(check) and check()
    assert panel._next_local_public_c_check is None
    # Deferred N3 callback cannot reuse the old detail after selection moves.
    view._page.selection.current = NS(key=visible.key)
    assert not check()
    view._page.selection.current = visible
    assert check()
    service._account_context_epoch += 1
    assert not check()


def test_stale_plugin_detail_drops_both_c_ref_and_supplier_image_url():
    view, window, service, source, visible = _selected()
    view._page.selection.current = None
    view.show_detail(_detail())
    rendered, check = view._page.detail.renders[-1]
    assert rendered.cover_document_ref is None
    assert rendered.thumbnail_url is None
    assert check is None
    assert view._page.detail._next_local_public_c_check is None


def test_plugin_detail_c_stage_cleaned_on_renderer_exception():
    view, window, service, source, visible = _selected()
    panel = view._page.detail
    panel.error = RuntimeError("synthetic render failure")
    with pytest.raises(RuntimeError, match="synthetic"):
        view.show_detail(_detail())
    assert panel._next_local_public_c_check is None


def test_plugin_duplicate_key_c_occurrences_not_joined_by_title_url_or_ref():
    view, window, service = _view()
    a = _book(key="duplicate", document=CANONICAL)
    b = _book(key="duplicate", document=SECOND)
    view.show_rows([a, b])
    assert len(view._page.rows) == 2
    first, second = view._page.rows
    assert first.key != second.key
    assert first._cover_plugin_c_receipt.source_row is a
    assert second._cover_plugin_c_receipt.source_row is b
    assert plugin_public_c_cell_current(
        first, first._cover_plugin_c_receipt,
    )
    assert plugin_public_c_cell_current(
        second, second._cover_plugin_c_receipt,
    )
    # A cross-row token may not paint the other source's N3.
    assert not plugin_public_c_cell_current(
        second, first._cover_plugin_c_receipt,
    )


def test_noncanonical_plugin_cover_preserves_existing_neutral_abi():
    from cifereca.core.book_cover_projection import CoverDocumentScope

    view, window, service = _view()
    legacy = CoverDocumentScope(
        kind="public_catalogue", identity_id=None,
        source_key="notice-c:v1:" + "a" * 64,
        provider_id="bnf", credential_id="__public__",
    )
    view.show_rows((_book(document=legacy),))
    row = view._page.rows[0]
    assert row._cover_document_ref == legacy
    assert not hasattr(row, "_cover_plugin_c_source")
    view._page.selection.current = row
    view.show_detail(_detail(document=legacy))
    rendered, current_check = view._page.detail.renders[-1]
    assert rendered.cover_document_ref == legacy
    assert current_check is None
