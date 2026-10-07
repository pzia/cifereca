from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUERY_CONTROLS = ROOT / "src/cifereca/gui/query_controls.py"


def _method_source(class_name: str, method_name: str) -> str:
    tree = ast.parse(QUERY_CONTROLS.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def test_multiselect_shows_selected_values_and_full_tooltip():
    init = _method_source("MultiSelect", "__init__")
    changed = _method_source("MultiSelect", "_changed")

    assert "self.choice_labels" in init
    assert "summary = 'Tous'" in changed
    assert "summary = titles[0]" in changed
    assert "len(titles) == 2" in changed
    assert "summary = f'{titles[0]} +{len(titles) - 1}'" in changed
    assert "tooltip = ', '.join(titles)" in changed
    assert "self.set_tooltip_text(tooltip)" in changed


def test_summary_and_tooltip_follow_atomic_programmatic_changes():
    from test_query_controls_batching import MultiSelect
    calls = []
    control = MultiSelect('Formats', iter([('epub', 'EPUB'), ('pdf', 'PDF'), ('mp3', 'MP3')]), lambda: calls.append(1))
    control.set_values(('epub',))
    assert control.label == 'Formats : EPUB'
    assert control.tooltip == 'EPUB'
    control.set_values(('epub', 'pdf'))
    assert control.label == 'Formats : EPUB + PDF'
    control.set_values(('epub', 'pdf', 'mp3'))
    assert control.label == 'Formats : EPUB +2'
    assert control.tooltip == 'EPUB, PDF, MP3'
    control.set_values(())
    assert control.label == 'Formats : Tous'
    assert control.tooltip is None
    control.set_values(())
    assert len(calls) == 4
