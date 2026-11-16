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
        ui._populate_local_search(rows)
        list_rows = ui.search_page.show_rows.call_args.args[0]
        assert len(list_rows) == 1
        list_row = list_rows[0]
        assert list_row._cover_document_ref == hit.document
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
            service=NS(current_identity=NS(identity_id="alice")),
            search_page=NS(detail=panel),
            _book_description=no_actions, _known_search_result=no_actions,
            _add_local_book_actions=no_actions,
            _add_plugin_book_actions=no_actions,
            _request_cached_book_details=no_actions,
            run_search=no_actions,
        )
        _detail_selected()(detail, NS(key=item["key"]))
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
        _detail_selected()(detail, NS(key=item["key"]))
        panel.show_presentation.assert_not_called()
        panel.empty.assert_called_once()
    finally:
        db.close()
