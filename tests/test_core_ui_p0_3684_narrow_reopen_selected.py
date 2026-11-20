"""Selection and keyboard focus for the shared narrow master/detail page."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/master_detail.py"


class Row:
    pass


def page_method(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    owner = next(n for n in tree.body
                 if isinstance(n, ast.ClassDef) and n.name == "MasterDetailPage")
    method = next(n for n in owner.body
                  if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {"BookListItem": Row, "Gtk": NS(DirectionType=NS(TAB_FORWARD="tab"))}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns[name]


def activate_method():
    return page_method("_activate_row")


def page_with_rows(*, narrow=True, selected=0, reject=False):
    rows = [Row(), Row()]
    events = []
    selection = NS(item=rows[selected] if selected is not None else None)

    def select(index):
        events.append(("select", index))
        if not reject:
            selection.item = rows[index]
            if narrow:
                page._narrow_detail_visible = True

    def reopen():
        events.append(("reopen",))
        page._narrow_detail_visible = True

    selection.get_selected_item = lambda: selection.item
    selection.set_selected = select
    page = NS(
        store=NS(get_n_items=lambda: len(rows), get_item=rows.__getitem__),
        selection=selection, _narrow_master_detail=narrow,
        _narrow_detail_visible=False, _show_narrow_detail=reopen,
        detail=NS(child_focus=lambda direction: events.append(("focus", direction))),
    )
    return page, rows, events


def test_reopens_retained_row_without_selecting_or_rerendering():
    page, rows, events = page_with_rows()
    activate_method()(page, None, 0)
    assert page._narrow_detail_visible
    assert page.selection.get_selected_item() is rows[0]
    assert events == [("reopen",)]
    activate_method()(page, None, 0)
    assert events == [("reopen",)]


def test_new_row_uses_normal_selection_once():
    page, rows, events = page_with_rows()
    activate_method()(page, None, 1)
    assert page.selection.get_selected_item() is rows[1]
    assert page._narrow_detail_visible
    assert events == [("select", 1)]


def test_wide_activation_still_focuses_current_detail():
    page, _, events = page_with_rows(narrow=False)
    activate_method()(page, None, 0)
    assert events == [("focus", "tab")]


@pytest.mark.parametrize("position", [-1, 2, 0xFFFFFFFF])
def test_invalid_position_is_inert(position):
    page, _, events = page_with_rows()
    activate_method()(page, None, position)
    assert events == []
    assert not page._narrow_detail_visible


def test_rejected_selection_does_not_open_previous_row():
    page, rows, events = page_with_rows(reject=True)
    activate_method()(page, None, 1)
    assert page.selection.get_selected_item() is rows[0]
    assert events == [("select", 1)]
    assert not page._narrow_detail_visible


@pytest.mark.parametrize("detail_accepts_focus", [True, False])
def test_opening_narrow_detail_leaves_keyboard_focus_on_a_visible_target(detail_accepts_focus):
    events = []

    def focus_detail(direction):
        events.append(("focus-detail", direction))
        return detail_accepts_focus

    page = NS(
        _narrow_master_detail=True,
        _narrow_detail_visible=False,
        _set_narrow_detail_context=lambda active: events.append(("context", active)),
        detail_back=NS(
            set_visible=lambda visible: events.append(("show-back", visible)),
            grab_focus=lambda: events.append(("focus-back",)),
        ),
        paned=NS(set_position=lambda position: events.append(("position", position))),
        detail=NS(child_focus=focus_detail),
    )

    page_method("_show_narrow_detail")(page)

    assert page._narrow_detail_visible
    expected = [("context", True), ("show-back", True), ("position", 0), ("focus-detail", "tab")]
    if not detail_accepts_focus:
        expected.append(("focus-back",))
    assert events == expected
