from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path
from types import SimpleNamespace as NS


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
INVALID = 0xFFFFFFFF


class BookListItem:
    def __init__(self, key: str):
        self.key = key


class Store:
    def __init__(self, rows):
        self.rows = list(rows)

    def get_n_items(self):
        return len(self.rows)

    def get_item(self, index):
        return self.rows[index]

    def splice(self, start, remove, rows):
        self.rows[start:start + remove] = list(rows)

    def append(self, row):
        self.rows.append(row)


class Selection:
    def __init__(self, selected):
        self.selected = selected
        self.calls = []

    def get_selected_item(self):
        return self.selected

    def set_selected(self, index):
        self.calls.append(index)
        if index == INVALID:
            self.selected = None


def _show_rows():
    tree = ast.parse(gui_source_text(MAIN))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MasterDetailPage")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "show_rows")
    module = ast.Module(body=[method], type_ignores=[])
    ns = {"BookListItem": BookListItem, "time": NS(perf_counter=lambda: 0.0)}
    exec(compile(ast.fix_missing_locations(module), str(MAIN), "exec"), ns)
    return ns["show_rows"]


def _page(selected):
    page = NS()
    page.selection = Selection(selected)
    page.store = Store([selected] if selected is not None else [])
    page._update_count = lambda *_: None
    page.list_stack = NS(set_visible_child_name=lambda *_: None)
    page.clear_rows = lambda: page.store.rows.clear()
    page.window = NS()
    page.title = "Test"
    return page


def test_filtering_out_selected_book_does_not_select_first_remaining_row():
    show_rows = _show_rows()
    page = _page(BookListItem("b"))

    show_rows(page, [BookListItem("a"), BookListItem("c")])

    assert page.selection.calls == [INVALID]


def test_existing_selected_book_is_restored_by_key():
    show_rows = _show_rows()
    page = _page(BookListItem("b"))

    show_rows(page, [BookListItem("a"), BookListItem("b"), BookListItem("c")])

    assert page.selection.calls == [INVALID, 1]


def test_initial_population_does_not_implicitly_select_first_row():
    show_rows = _show_rows()
    page = _page(None)

    show_rows(page, [BookListItem("a"), BookListItem("b")])

    assert page.selection.calls == [INVALID]
