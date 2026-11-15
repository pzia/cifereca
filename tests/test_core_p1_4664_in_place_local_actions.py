"""#4664: exact Recommendations detail receives Search's local actions via Core.

Runs the real GTK adapter method in isolation, without GTK/network. The
source item is a Core SearchResult already admitted by the exact-detail port.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.query_model import result_row
from cifereca.plugin_api import DetailField, DetailSection
from tests.test_core_p1_4664_recommendation_exact_detail import _detail, _source


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/public_book_view.py"


def _adapter():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "PublicBookMasterDetailView")
    module = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__",
                             names=[ast.alias(name="annotations")], level=0), cls],
        type_ignores=[],
    ))
    namespace = {"__name__": "cifereca.gui.public_book_view",
                 "__package__": "cifereca.gui"}
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return object.__new__(namespace["PublicBookMasterDetailView"])


def test_exact_in_place_uses_same_core_local_action_controller_as_search():
    view = _adapter()
    events = []
    panel = NS(
        show_item_detail=lambda item, detail: events.append(("render", item, detail)),
        _group=lambda title, rows, **kwargs: events.append(("context", title, rows)),
    )
    view._page = NS(detail=panel)
    view._window = NS(
        _add_local_book_actions=lambda target, row:
            events.append(("core_actions", target, row)),
    )
    item, detail = _source(), _detail()
    context = (DetailSection(
        "recommendation", "Recommandation",
        (DetailField("affinity", "Affinité", "90 %"),), 10,
    ),)

    view.show_existing_search_detail(item, detail, context_sections=context)

    assert [event[0] for event in events] == [
        "render", "core_actions", "context",
    ]
    assert events[0][1:] == (item, detail)
    assert events[1][1] is panel
    # Exactly the projection passed by _apply_search_detail on Search:
    # never the recommendation's grouped/approximate notice or score.
    assert events[1][2] == result_row(item)
    assert events[1][2]["source_credential"] == "card-A"
    assert events[1][2]["record_id"] == "record-1"
    assert events[2][1:] == ("Recommandation", [("Affinité", "90 %")])


def test_exact_action_projection_preserves_account_source_and_edition():
    view = _adapter()
    rows = []
    view._page = NS(detail=NS(
        show_item_detail=lambda *_: None,
        _group=lambda *_args, **_kwargs: None,
    ))
    view._window = NS(
        _add_local_book_actions=lambda _panel, row: rows.append(row),
    )
    for account, edition in (("card-A", "edition-A"), ("card-B", "edition-B")):
        item = replace(
            _source(), source_credential=account, record_id=edition,
        )
        view.show_existing_search_detail(item, _detail())
    assert len(rows) == 2
    assert [(row["source_credential"], row["record_id"]) for row in rows] == [
        ("card-A", "edition-A"), ("card-B", "edition-B"),
    ]
    assert all(row["state"] == "remote_catalog" for row in rows)
    # Showing the detail neither obtains files nor manufactures ownership.
    assert all(row["state"] != "remote_owned" for row in rows)


def test_recommendation_exact_detail_updates_the_loaded_search_row_without_reselection(monkeypatch):
    import sys
    from cifereca.gui.search_document_cover_bridge import exact_remote_search_document
    from tests.test_core_p1_3104_search_detail_cover_live_row import COVER, Row, detail, publisher
    from tests.test_core_p1_6168_search_remote_document_gateway import host

    window, source, key = host()
    visible_key = f"{source.source_provider}:{source.id}"
    window.search_objects = {visible_key: window.search_objects[key]}
    class VisibleRow(Row):
        def set_thumbnail_url(self, url):
            if url == self.thumbnail_url:
                return False
            super().set_thumbnail_url(url)
            return True

    row = VisibleRow(visible_key)

    def reject_selection():
        raise AssertionError("opening a recommendation must preserve Search selection")

    window.search_page = NS(
        store=NS(get_n_items=lambda: 1, get_item=lambda index: row),
        selection=NS(get_selected_item=reject_selection),
    )
    monkeypatch.setitem(
        sys.modules, "cifereca.gui.search_details",
        NS(_publish_search_detail_cover=publisher()),
    )
    initial = exact_remote_search_document(window, source, visible_key)
    calls = []
    view = _adapter()
    view._window = window
    window._add_local_book_actions = lambda *_: None
    view._page = NS(detail=NS(
        show_search_document=lambda item, **kwargs: calls.append((item, kwargs)),
    ))
    incoming = detail(source)
    view.show_existing_search_detail(source, incoming)
    proof = calls[0][1]["cover_evidence"]
    assert calls[0][0] is source and calls[0][1]["detail"] is incoming
    assert proof.document == initial.document
    assert row._cover_document_ref == proof.document and row.changes == [COVER]
    assert proof.candidates[0].tier == "origin" and proof.candidates[0].url == COVER
    assert exact_remote_search_document(window, source, visible_key) == proof

    calls.clear()
    view.show_existing_search_unavailable(source)
    assert calls == [(source, {"cover_evidence": proof})]


def test_recommendation_exact_cover_refuses_an_object_from_an_old_search():
    from cifereca.gui.recommendation_exact_cover import loaded_recommendation_cover
    from tests.test_core_p1_6168_search_remote_document_gateway import host

    window, source, key = host()
    visible_key = f"{source.source_provider}:{source.id}"
    window.search_objects = {visible_key: replace(window.search_objects[key])}
    assert loaded_recommendation_cover(window, source) is None
