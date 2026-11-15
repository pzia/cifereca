"""#4761: Ctrl+F must target the owning Recommendations local filter.

The actual GTK/XTEST dispatch is exercised in recommendations_detail_probe.
These headless contracts catch regressions even when GTK is unavailable.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_master_detail_never_steals_plugin_ctrl_f_for_global_search():
    source = _source("src/cifereca/gui/master_detail.py")
    assert "def bind_local_filter_focus(" in source
    assert "if callable(focus):" in source
    assert "return bool(focus())" in source
    assert 'if title not in ("Recherche", "Bibliothèque"):' in source
    assert source.index("if callable(focus):") < source.index(
        'if title not in ("Recherche", "Bibliothèque"):'
    )
    assert 'else "search_controls"' in source


def test_recommendations_binds_only_its_optional_public_local_filter():
    adapter = _source("src/cifereca/gui/public_book_view.py")
    controls = _source("src/cifereca/gui/query_controls.py")
    plugin = _source("src/plugins/recommendations/recommendations.py")
    assert "def bind_local_filter_focus(" in adapter
    assert "self._page.bind_local_filter_focus(callback)" in adapter
    assert "def focus_text_filter(self) -> bool:" in controls
    assert "self.entry.get_mapped() and self.entry.grab_focus()" in controls
    assert 'getattr(self._master, "bind_local_filter_focus", None)' in plugin
    assert 'getattr(self._query_controls, "focus_text_filter", None)' in plugin
    assert 'getattr(self._text, "grab_focus", None)' in plugin
    assert "bind_focus(focus_text)" in plugin
