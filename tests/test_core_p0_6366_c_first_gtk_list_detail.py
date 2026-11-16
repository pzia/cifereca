"""Headless real Search rendering: public C one N2 ref list/detail, no action."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui.detail_model import book_detail_presentation
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import (
    persist, verified_public,
)
from tests.test_search_results_view import _host


ROOT = Path(__file__).resolve().parents[1]
OWNER = ROOT / "src/cifereca/gui/search_details.py"


def _detail_selected():
    source = ast.parse(OWNER.read_text())
    fn = next(x for x in source.body if isinstance(x, ast.FunctionDef)
              and x.name == "_search_selected")
    module = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0,
        ), fn], type_ignores=[],
    ))
    env = {"book_detail_presentation": book_detail_presentation}
    exec(compile(module, str(OWNER), "exec"), env)
    return env[fn.name]


def test_public_c_source_binds_same_document_to_list_and_detail_without_actions(
    tmp_path,
):
    db = Storage(tmp_path / "view.sqlite")
    try:
        hit = persist(db, verified_public(
            title="La forêt connue", author="Auteur source",
        ))
        page = search_public_notice_c(db, "forêt")
        rows = merge_public_notice_c_known_rows(
            [], page, identity_id="alice",
        )
        assert len(rows) == 1
        item = rows[0]
        ui = _host()
        # MainWindow supplies these existing four Core/UI epochs. The
        # headless generic Search fixture predates the C-first N3 guard.
        ui._identity_context_generation = 2
        ui._search_generation = 3
        ui.service._account_context_epoch = 5
        ui.service._source_context_epoch = 7
        ui._populate_local_search(rows)
        list_rows = ui.search_page.show_rows.call_args.args[0]
        assert len(list_rows) == 1
        list_row = list_rows[0]
        assert list_row._cover_document_ref == hit.document
        assert list_row._cover_known_public_c_source is item
        assert list_row._cover_known_public_c_receipt is not None
        assert list_row.options == {}
        assert list_row.meta.startswith("Notice C publique")
        assert list_row._public_row_content[-2] == hit.document
        assert list_row._public_row_content[-1] is None
        assert list_row.key == item["key"]

        panel = NS(show_presentation=Mock(), empty=Mock())
        no_actions = Mock(side_effect=AssertionError("no provider actions"))
        detail = NS(
            _closed=False,
            _search_detail_request_generation=0,
            _cancel_cached_book_details=Mock(),
            _search_local_objects={item["key"]: item},
            _selected_search_id=None,
            service=ui.service,
            _search_mode="local",
            _identity_context_generation=2, _search_generation=3,
            search_page=NS(detail=panel, selection=NS(
                get_selected_item=lambda: list_row,
            )),
            _book_description=no_actions, _known_search_result=no_actions,
            _add_local_book_actions=no_actions,
            _add_plugin_book_actions=no_actions,
            _request_cached_book_details=no_actions,
            run_search=no_actions,
        )
        _detail_selected()(detail, list_row)
        panel.empty.assert_not_called()
        presentation = panel.show_presentation.call_args.args[0]
        assert presentation.cover_document_ref == list_row._cover_document_ref
        assert presentation.thumbnail_url is None
        assert presentation.title == "La forêt connue"
        assert presentation.actions == ()
        assert presentation.links == ()
        no_actions.assert_not_called()

        # A stale GTK selection from another account epoch/identity is inert:
        # neither the previous C ref nor a private action may be invoked.
        detail.service.current_identity.identity_id = "bob"
        panel.show_presentation.reset_mock()
        _detail_selected()(detail, list_row)
        panel.show_presentation.assert_not_called()
        panel.empty.assert_called_once()
    finally:
        db.close()


def test_online_extension_preserves_same_c_cover_until_exact_live_source(tmp_path):
    """Changing Search mode must not drop the C list N2 or join by URL/title."""
    db = Storage(tmp_path / "online-view.sqlite")
    try:
        record = verified_public(
            title="Un même titre", revision_id="rev-1",
        )
        persisted = persist(db, record)
        page = search_public_notice_c(db, "titre")
        known = merge_public_notice_c_known_rows(
            [], page, identity_id="alice",
        )
        ui = _host()
        ui._search_mode = "global"
        ui._search_local_objects = {known[0]["key"]: known[0]}
        ui._remote_search_results = []
        ui._render_search_results()
        retained = ui.search_page.show_rows.call_args.args[0]
        assert len(retained) == 1
        assert retained[0]._cover_document_ref == persisted.document
        assert retained[0]._public_row_content[-2] == persisted.document
        assert retained[0]._public_row_content[-3] == known[0]["metadata"]["notice_c_epoch"]
        assert retained[0]._public_row_content[-1] is None
        assert retained[0].options == {}

        # Same title and same cover are insufficient to suppress this C
        # when the provider reports another revision, even with same ID.
        from dataclasses import replace
        other = replace(record, revision_id="rev-2")
        other._core_notice_cover_document_ref = None
        ui._remote_search_results = [other]
        ui._render_search_results()
        expanded = ui.search_page.show_rows.call_args.args[0]
        assert len(expanded) == 2
        assert sum(getattr(x, "_cover_document_ref", None)
                   == persisted.document for x in expanded) == 1

        # An independently Core-stamped SAME C source may replace the known
        # row once live results are received, not an arbitrary shared URL.
        record._core_notice_cover_document_ref = persisted.document
        ui._remote_search_results = [record]
        ui._render_search_results()
        replaced = ui.search_page.show_rows.call_args.args[0]
        assert len(replaced) == 1
        assert replaced[0].key == f"{record.source_provider}:{record.id}"
    finally:
        db.close()
