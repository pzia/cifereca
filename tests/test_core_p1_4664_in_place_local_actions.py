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
