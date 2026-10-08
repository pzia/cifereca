from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"


def _method(name: str):
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)


def _standalone(name: str):
    method = _method(name)
    ns = {}
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(MAIN), "exec"),
        ns,
    )
    return ns[name]


def test_search_count_handles_zero_one_and_many_in_known_scope():
    update = _standalone("_update_search_count")
    labels = []
    window = NS(
        _search_mode="local",
        search_page=NS(count_label=NS(set_label=labels.append)),
    )

    update(window, 0)
    update(window, 1)
    update(window, 4)

    assert labels == [
        "0 résultats · Connu",
        "1 résultat · Connu",
        "4 résultats · Connu",
    ]


def test_search_count_switches_to_extended_scope():
    update = _standalone("_update_search_count")
    labels = []
    window = NS(
        _search_mode="global",
        search_page=NS(count_label=NS(set_label=labels.append)),
    )
    update(window, 7)
    assert labels == ["7 résultats · Étendu"]


def test_local_search_does_not_repeat_stable_count_in_toast():
    source = ast.unparse(_method("_populate_local_search"))
    assert "self._render_search_results()" in source
    assert "résultat(s) dans le connu" not in source
    assert "self.toast" not in source


def test_remote_search_only_toasts_partial_warning():
    source = ast.unparse(_method("_populate_search"))
    assert "Résultats partiels" in source
    assert "résultat(s) affiché(s)" not in source
    assert "else:\n        self.toast" not in source


def test_render_updates_business_count_after_filters():
    source = ast.unparse(_method("_render_search_results"))
    assert source.count("self._update_search_count(shown)") == 2
