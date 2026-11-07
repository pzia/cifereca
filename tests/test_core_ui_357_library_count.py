from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path
from types import SimpleNamespace as NS


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _method(class_name: str, method_name: str):
    tree = ast.parse(gui_source_text(MAIN))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)


def _standalone(class_name: str, method_name: str):
    method = _method(class_name, method_name)
    ns = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(MAIN), "exec"), ns)
    return ns[method_name]


def test_library_render_passes_unfiltered_total_to_master_detail():
    owner = MAIN.parent / "library_render_controller.py"
    module = ast.parse(owner.read_text(encoding="utf-8"))
    source = ast.unparse(next(node for node in module.body
                              if isinstance(node, ast.FunctionDef) and node.name == "render_library"))
    assert "window.library_page.show_rows(rows, total_count=len(entries))" in source


def test_p1_3687_zero_visible_keeps_attested_total_and_pluralization():
    """An empty filtered projection is not an empty catalogue."""
    update = _standalone("MasterDetailPage", "_update_count")
    labels = []
    page = NS(
        store=NS(get_n_items=lambda: 0),
        count_label=NS(set_label=labels.append),
        _total_count=0,
        _count_total_only=False,
        _item_count_label=lambda n: f"{n} élément" if n == 1 else f"{n} éléments",
    )
    update(page, 0)
    assert labels[-1] == ""  # Genuine empty result.
    update(page, 1)
    assert labels[-1] == "0 / 1 élément"
    update(page, 20)
    assert labels[-1] == "0 / 20 éléments"

    # The History business-total policy never exposes the number of
    # materialized rows, even before a paginated page arrives.
    page._count_total_only = True
    update(page, 20)
    assert labels[-1] == "20 éléments"
    page._count_total_only = False
    update(page, 0)
    assert labels[-1] == ""  # Identity switch / actual empty snapshot.
    assert page._total_count == 0


def test_p1_3687_filtered_count_returns_to_standard_after_reset():
    update = _standalone("MasterDetailPage", "_update_count")
    labels = []
    visible = [0]
    page = NS(
        store=NS(get_n_items=lambda: visible[0]),
        count_label=NS(set_label=labels.append),
        _total_count=0,
        _count_total_only=False,
        _item_count_label=lambda n: f"{n} élément" if n == 1 else f"{n} éléments",
    )
    update(page, 3)
    assert labels[-1] == "0 / 3 éléments"
    visible[0] = 2
    update(page, 3)
    assert labels[-1] == "2 / 3 élément(s)"
    visible[0] = 3
    update(page, 3)
    assert labels[-1] == "3 élément(s)"
    visible[0] = 0
    update(page, 0)
    assert labels[-1] == ""


def test_count_label_shows_filtered_over_total_only_when_reduced():
    update = _standalone("MasterDetailPage", "_update_count")
    labels = []
    page = NS(
        store=NS(get_n_items=lambda: 37),
        count_label=NS(set_label=labels.append),
        _total_count=0,
    )
    update(page, 1000)
    assert labels[-1] == "37 / 1000 élément(s)"

    page.store = NS(get_n_items=lambda: 1000)
    update(page, 1000)
    assert labels[-1] == "1000 élément(s)"
