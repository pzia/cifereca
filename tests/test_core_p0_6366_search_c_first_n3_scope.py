"""P0 #6366: real C-first Search source cannot leak pixels via late N3.

Use persisted Core C observations and the actual GTK-free Search list and
detail controller functions, with AST-extracted cell methods. No live network,
no GTK display, no entitlement or acquisition mutation.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.gui.detail_model import book_detail_presentation
from cifereca.gui.search_known_public_c_guard import (
    PinnedKnownPublicC, pin_known_public_c,
    attach_filtered_known_public_c,
    known_public_c_current, known_public_c_cell_current,
)
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import (
    persist, verified_public,
)
from tests.test_search_results_view import _host


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


def _seed(tmp_path):
    db = Storage(tmp_path / "known-public-c-n3.sqlite")
    source = verified_public(
        title="Une forêt connue", author="Autrice",
    )
    observed = persist(db, source)
    page = search_public_notice_c(db, "forêt")
    known = merge_public_notice_c_known_rows(
        [], page, identity_id="alice",
    )
    assert len(known) == 1
    item = known[0]
    ui = _host()
    ui._identity_context_generation = 2
    ui._search_generation = 3
    ui.service._account_context_epoch = 5
    ui.service._source_context_epoch = 7
    ui._populate_local_search(known)
    row = ui.search_page.show_rows.call_args.args[0][0]
    assert row._cover_document_ref == observed.document
    assert type(row._cover_known_public_c_receipt) is PinnedKnownPublicC
    assert row._cover_known_public_c_source is item
    assert row._cover_known_public_c_receipt.epoch == (
        item["metadata"]["notice_c_epoch"]
    )
    return db, ui, row, item


def _cell_method(name):
    path = ROOT / "book_widgets.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(x for x in tree.body
               if isinstance(x, ast.ClassDef) and x.name == "BookListCell")
    fn = next(x for x in cls.body
              if isinstance(x, ast.FunctionDef) and x.name == name)
    module = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(
            module="__future__",
            names=[ast.alias(name="annotations")], level=0,
        ), fn], type_ignores=[],
    ))
    ns = {
        "__name__": "cifereca.gui.book_widgets",
        "__package__": "cifereca.gui",
    }
    exec(compile(module, str(path), "exec"), ns)
    return ns[name]


def _search_selected():
    path = ROOT / "search_details.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(x for x in tree.body
              if isinstance(x, ast.FunctionDef) and x.name == "_search_selected")
    module = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(
            module="__future__",
            names=[ast.alias(name="annotations")], level=0,
        ), fn], type_ignores=[],
    ))
    ns = {"book_detail_presentation": book_detail_presentation}
    exec(compile(module, str(path), "exec"), ns)
    return ns["_search_selected"]


def test_real_c_first_list_pin_rejects_source_epoch_query_and_aba(tmp_path):
    db, host, row, item = _seed(tmp_path)
    try:
        receipt = row._cover_known_public_c_receipt
        assert known_public_c_current(host, receipt)
        assert known_public_c_cell_current(host, row, receipt)
        assert pin_known_public_c(host, row, item) == receipt

        host.service.current_identity.identity_id = "bob"
        assert not known_public_c_cell_current(host, row, receipt)
        host.service.current_identity.identity_id = "alice"
        assert known_public_c_cell_current(host, row, receipt)

        for name in (
            "_identity_context_generation", "_search_generation",
        ):
            setattr(host, name, getattr(host, name) + 1)
            assert not known_public_c_current(host, receipt)
            setattr(host, name, getattr(host, name) - 1)
            assert known_public_c_current(host, receipt)

        for name in ("_account_context_epoch", "_source_context_epoch"):
            setattr(host.service, name, getattr(host.service, name) + 1)
            assert not known_public_c_current(host, receipt)
            setattr(host.service, name, getattr(host.service, name) - 1)
            assert known_public_c_current(host, receipt)

        host._search_mode = "global"
        assert not known_public_c_current(host, receipt)
        host._search_mode = "local"
        assert known_public_c_current(host, receipt)

        # Core has now published an updated candidate under the SAME C doc.
        item["metadata"]["notice_c_epoch"] += 1
        assert not known_public_c_current(host, receipt)
        item["metadata"]["notice_c_epoch"] -= 1
        assert known_public_c_current(host, receipt)

        # Same key/title/document/epoch but a different source dictionary.
        old = item
        host._search_local_objects[row.key] = {
            **old, "metadata": dict(old["metadata"]),
        }
        assert not known_public_c_current(host, receipt)
        host._search_local_objects[row.key] = old
        assert known_public_c_current(host, receipt)
        row._cover_known_public_c_source = dict(old)
        assert not known_public_c_cell_current(host, row, receipt)
        row._cover_known_public_c_source = old

        previous_service = host.service
        host.service = NS(**vars(previous_service))
        assert not known_public_c_current(host, receipt)
        host.service = previous_service
        assert known_public_c_cell_current(host, row, receipt)
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_real_c_first_gtk_list_check_does_not_paint_recycled_cell(tmp_path):
    db, host, row, item = _seed(tmp_path)
    try:
        sink = []
        cell = NS(
            window=host, _bind_epoch=7, _bound_key=row.key,
            _thumbnail_item=row,
        )
        guard = _cell_method("_search_cover_guard")(cell, row)
        check = guard["is_current"]
        assert check()
        assert _cell_method("_admit_core_local_cover")(
            cell, row, object(), row._cover_document_ref,
        )
        cell._bind_epoch += 1
        assert not check()
        cell._bind_epoch -= 1
        assert check()
        cell._thumbnail_item = NS(key=row.key)
        assert not check()
        cell._thumbnail_item = row
        assert check()
        host._search_local_objects.pop(row.key)
        assert not check()
        assert not _cell_method("_admit_core_local_cover")(
            cell, row, object(), row._cover_document_ref,
        )
        # A rejected source cannot silently request its legacy URL.
        assert not hasattr(row, "thumbnail_url") or not row.thumbnail_url
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


@pytest.mark.parametrize("mode", ["local", "global"])
def test_current_c_first_source_is_pinned_in_both_search_modes(tmp_path, mode):
    db, host, row, item = _seed(tmp_path)
    try:
        host._search_mode = mode
        host._render_search_results()
        actual = host.search_page.show_rows.call_args.args[0][0]
        assert actual._cover_known_public_c_source is item
        receipt = actual._cover_known_public_c_receipt
        assert known_public_c_cell_current(host, actual, receipt)
        assert receipt.context[0] == mode
        assert receipt.document == row._cover_document_ref
        assert receipt.epoch == row._cover_public_c_epoch
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_search_c_first_detail_stages_pinned_c_and_revokes_at_n3(tmp_path):
    db, host, row, item = _seed(tmp_path)
    try:
        checks = []
        presentations = []
        panel = NS(empty=Mock(), _next_local_public_c_check=None)
        def show(presentation, **kwargs):
            checks.append(panel._next_local_public_c_check)
            presentations.append(presentation)
        panel.show_presentation = show
        controller = NS(
            _closed=False, service=host.service,
            _search_mode="local", _identity_context_generation=2,
            _search_generation=3, _search_detail_request_generation=0,
            _cancel_cached_book_details=lambda: None,
            _search_local_objects=host._search_local_objects,
            _selected_search_id=None,
            search_page=NS(
                detail=panel,
                selection=NS(get_selected_item=lambda: row),
            ),
        )
        _search_selected()(controller, row)
        panel.empty.assert_not_called()
        assert len(checks) == 1 and callable(checks[0])
        assert checks[0]() is True
        assert panel._next_local_public_c_check is None
        assert presentations[0].cover_document_ref == row._cover_document_ref
        assert presentations[0].thumbnail_url is None

        controller._search_generation += 1
        assert checks[0]() is False
        controller._search_generation -= 1
        controller.search_page.selection.get_selected_item = lambda: NS(key=row.key)
        assert checks[0]() is False
        controller.search_page.selection.get_selected_item = lambda: row
        assert checks[0]() is True

        # Same key, foreign original row: cannot open a new detail/actions.
        forged = NS(key=row.key, _cover_document_ref=row._cover_document_ref,
                    _cover_known_public_c_source=dict(item))
        panel.empty.reset_mock()
        _search_selected()(controller, forged)
        panel.empty.assert_called_once()
        assert len(presentations) == 1
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_search_c_first_detail_guard_survives_render_exception(tmp_path):
    db, host, row, item = _seed(tmp_path)
    try:
        panel = NS(empty=Mock(), _next_local_public_c_check=None,
                   show_presentation=Mock(side_effect=RuntimeError("interrupted")))
        controller = NS(
            _closed=False, service=host.service, _search_mode="local",
            _identity_context_generation=2, _search_generation=3,
            _search_detail_request_generation=0,
            _cancel_cached_book_details=lambda: None,
            _search_local_objects=host._search_local_objects,
            _selected_search_id=None, search_page=NS(
                detail=panel, selection=NS(get_selected_item=lambda: row),
            ),
        )
        with pytest.raises(RuntimeError, match="interrupted"):
            _search_selected()(controller, row)
        assert panel._next_local_public_c_check is None
        assert db.entitlements() == []
    finally:
        db.close()


def test_real_widget_source_routes_known_c_to_guard_at_bind_and_signal():
    tree = ast.parse((ROOT / "book_widgets.py").read_text(encoding="utf-8"))
    widget = next(x for x in tree.body if isinstance(x, ast.ClassDef)
                  and x.name == "BookListCell")
    for method in ("bind", "_thumbnail_changed"):
        fn = next(x for x in widget.body if isinstance(x, ast.FunctionDef)
                  and x.name == method)
        code = ast.unparse(fn)
        assert "_cover_known_public_c_source" in code
        assert "self._search_cover_guard(item)" in code
    assert "known_public_c_cell_current" in ast.unparse(next(
        x for x in widget.body if isinstance(x, ast.FunctionDef)
        and x.name == "_search_cover_guard"
    ))
    # Search detail uses the already-integrated opt-in N3 guard on the
    # shared declared document gateway, not another loader.
    detail_source = (ROOT / "detail_panel.py").read_text()
    assert "is_current=getattr(" in detail_source
    assert '"_next_local_public_c_check", None' in detail_source


@pytest.mark.parametrize("alter", [
    "epoch", "document", "owner", "source",
])
def test_filtered_c_copy_cannot_mint_new_core_witness_from_equal_title(
    tmp_path, alter,
):
    db, host, row, original = _seed(tmp_path)
    try:
        projected = {
            **original, "metadata": dict(original["metadata"]),
        }
        if alter == "epoch":
            projected["metadata"]["notice_c_epoch"] += 1
        elif alter == "document":
            from cifereca.core.notice_c_cover_index import (
                canonical_cover_document_ref,
            )
            projected["_cover_document_ref"] = canonical_cover_document_ref(
                "edition:wrong-book",
            )
        elif alter == "owner":
            projected["identity_id"] = "another-profile"
        else:
            projected["source"] = "another-source"
        # A filtered copy may contain the same headline and a nominal
        # DocumentRef; only the ORIGINAL Core dictionary is a witness.
        visible = NS(
            key=row.key,
            _cover_document_ref=projected["_cover_document_ref"],
            _cover_public_c_epoch=projected["metadata"]["notice_c_epoch"],
        )
        attach_filtered_known_public_c(host, visible, projected)
        assert not hasattr(visible, "_cover_known_public_c_receipt")
        assert not known_public_c_cell_current(
            host, visible,
            getattr(visible, "_cover_known_public_c_receipt", None),
        )
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
