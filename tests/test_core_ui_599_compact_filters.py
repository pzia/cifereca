from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUERY_CONTROLS = ROOT / "src/cifereca/gui/query_controls.py"


def _method(class_name: str, method_name: str) -> str:
    tree = ast.parse(QUERY_CONTROLS.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def test_filter_bar_uses_compact_stable_names_in_expected_order():
    source = _method("QueryControls", "__init__")
    source_pos = source.index("MultiSelect('Source'")
    format_pos = source.index("MultiSelect('Format'")
    language_pos = source.index("MultiSelect('Langue'")
    field_pos = source.index("Gtk.DropDown.new_from_strings(['Champ'")
    type_pos = source.index("Gtk.DropDown.new_from_strings(['Type'")
    assert source_pos < format_pos < language_pos < field_pos < type_pos


def test_default_filter_labels_do_not_repeat_tous():
    source = _method("QueryControls", "__init__")
    assert "Forme · Sources" not in source
    assert "Forme · Formats" not in source
    assert "Fond / forme · Tous" not in source
    assert "Langues : Tous" not in source
    assert "'Champ', 'Champ · Titre'" in source
    assert "'Type', 'Type · Physique', 'Type · Numérique'" in source
    assert "Type · Support indéterminé" in source


def test_multiselect_keeps_active_values_but_hides_neutral_tous():
    init = _method("MultiSelect", "__init__")
    changed = _method("MultiSelect", "_changed")
    assert "self.set_label(label)" in init
    assert "summary = 'Tous'" in changed
    assert "summary = titles[0]" in changed
    assert "len(titles) == 2" in changed
    assert "summary = f'{titles[0]} +{len(titles) - 1}'" in changed
    assert "self.caption if not titles" in changed
    assert "f'{self.caption} : {summary}'" in changed


def test_filter_tooltips_explain_semantics_without_lengthening_bar():
    source = _method("QueryControls", "__init__")
    assert "Limiter la vue à une ou plusieurs sources" in source
    assert "Limiter la vue à un ou plusieurs formats" in source
    assert "Limiter la vue à une ou plusieurs langues" in source
    assert "Choisir le champ interrogé" in source
    assert "Limiter aux documents physiques" in source
