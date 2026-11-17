"""#6366: in-place Recommendations exact Search detail cannot resurrect C at N3.

The worker's public_c_document was valid at dispatch. A later plugin
selection, source generation or list redraw revokes its *display* lease.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
from cifereca.models import SearchResult, ItemDetail
from cifereca.plugin_api.models import UiBookListEntry
from cifereca.gui.plugin_public_c_guard import (
    pin_plugin_public_c, staged_plugin_exact_public_c,
)


def _setup():
    doc = canonical_cover_document_ref("edition:exact-recommendation")
    source = UiBookListEntry(
        key="recommendation-1", title="Le livre exact",
        cover_document_ref=doc, thumbnail_url="https://covers.invalid/a.jpg",
    )
    owner = NS(
        current_identity=NS(identity_id="alice"),
        _account_context_epoch=4, _source_context_epoch=8,
    )
    window = NS(
        service=owner, _closed=False,
        _search_purchase_evidence=None,
        _add_local_book_actions=Mock(),
    )
    selected = NS(key=source.key, _cover_document_ref=doc,
                  _cover_plugin_c_source=source)
    panel = NS(
        _next_local_public_c_check=None,
        show_public_c_search_document=Mock(),
        show_item_detail=Mock(), show_search=Mock(),
    )
    view = NS(
        _window=window, _rows={source.key: source},
        _public_c_page_generation=7,
        _page=NS(selection=NS(get_selected_item=lambda: selected), detail=panel),
        _append_recommendation_context=lambda *_args, **_kwargs: None,
    )
    receipt = pin_plugin_public_c(view, source, key=source.key)
    assert receipt is not None
    selected._cover_plugin_c_receipt = receipt
    return view, panel, source, selected, doc


def _method(name):
    path = Path(__file__).resolve().parents[1] / (
        "src/cifereca/gui/public_book_view.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "PublicBookMasterDetailView")
    fn = next(node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")],
            level=0,
        ), fn], type_ignores=[],
    ))
    ns = {
        "__name__": "cifereca.gui.public_book_view",
        "__package__": "cifereca.gui",
    }
    exec(compile(module, str(path), "exec"), ns)
    return ns[name]


def _search_item():
    return SearchResult(
        id="search-1", record_id="book-1", docbase="DILICOM",
        medium="digital", title="Le livre exact", author="Autrice",
        type="book", format="epub", publisher=None, published_at=None,
        description=None, thumbnail_url="https://covers.invalid/private.jpg",
        friendly_url=None, scenario="search",
    )


def _detail():
    return ItemDetail(
        record_id="book-1", docbase="DILICOM", title="Le livre exact",
        author="Autrice", type="book", year=None,
        thumbnail_url="https://covers.invalid/detail.jpg",
        availability_status="unknown", availability_label="Unknown",
        holdings=[],
    )


@pytest.mark.parametrize("method", [
    "show_existing_search_detail", "show_existing_search_unavailable",
])
def test_exact_search_detail_uses_same_plugin_c_n3_lease(method):
    view, panel, source, selected, doc = _setup()
    search = _search_item()
    details = _detail()
    fn = _method(method)
    if method.endswith("unavailable"):
        fn(view, search, public_c_document=doc)
    else:
        fn(view, search, details, public_c_document=doc)
    panel.show_public_c_search_document.assert_called_once()
    assert panel._next_local_public_c_check is None
    # Capture the delayed N3 callback while the render stage is active.
    checks = []
    def capture(*args, **kwargs):
        checks.append(panel._next_local_public_c_check)
    panel.show_public_c_search_document.side_effect = capture
    panel.show_public_c_search_document.reset_mock()
    if method.endswith("unavailable"):
        fn(view, search, public_c_document=doc)
    else:
        fn(view, search, details, public_c_document=doc)
    assert len(checks) == 1 and callable(checks[0])
    assert checks[0]()
    view._public_c_page_generation += 1
    assert not checks[0]()
    assert panel._next_local_public_c_check is None


@pytest.mark.parametrize("method", [
    "show_existing_search_detail", "show_existing_search_unavailable",
])
@pytest.mark.parametrize("revoke", ["selection", "account", "source", "row"])
def test_revoked_exact_search_detail_has_no_c_or_supplier_image(method, revoke):
    view, panel, source, selected, doc = _setup()
    if revoke == "selection":
        view._page.selection.get_selected_item = lambda: NS(key=source.key)
    elif revoke == "account":
        view._window.service._account_context_epoch += 1
    elif revoke == "source":
        view._window.service._source_context_epoch += 1
    else:
        view._rows[source.key] = UiBookListEntry(
            key=source.key, title=source.title, cover_document_ref=doc,
        )
    fn = _method(method)
    search = _search_item()
    if method.endswith("unavailable"):
        fn(view, search, public_c_document=doc)
        panel.show_search.assert_called_once()
        rendered = panel.show_search.call_args.args[0]
    else:
        fn(view, search, _detail(), public_c_document=doc)
        panel.show_item_detail.assert_called_once()
        rendered = panel.show_item_detail.call_args.args[0]
        assert panel.show_item_detail.call_args.args[1].thumbnail_url is None
    assert rendered.thumbnail_url is None
    assert rendered is not search
    panel.show_public_c_search_document.assert_not_called()
    assert panel._next_local_public_c_check is None


def test_canonical_exact_scope_refuses_other_recommendation_even_same_title():
    view, panel, source, selected, doc = _setup()
    admitted, stage = staged_plugin_exact_public_c(view, panel, doc)
    assert admitted
    with stage:
        check = panel._next_local_public_c_check
        assert callable(check) and check()
        view._page.selection.get_selected_item = lambda: NS(key=source.key)
        assert not check()
    assert panel._next_local_public_c_check is None
