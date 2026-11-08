"""#4761: recommendation-only parameters have one stable horizontal GTK row.

The filter row is owned by Core LocalQueryControls, not duplicated here.
"""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "recommendations.py"


def method(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    node = next(x for x in ast.walk(tree)
                if isinstance(x, ast.FunctionDef) and x.name == name)
    return ast.unparse(node)


def test_computation_parameters_occupy_one_independent_horizontal_scroll_row():
    init = method("_init_master_detail")
    assert "Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)" in init
    assert "controls_scroll.set_name('recommendations-parameters-scroll')" in init
    assert "controls_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)" in init
    assert "controls_scroll.set_propagate_natural_width(False)" in init
    assert "controls_scroll.set_propagate_natural_height(True)" in init
    assert "controls_scroll.set_child(controls)" in init
    assert "toolbox.append(controls_scroll)" in init
    assert "toolbox.append(self._query_controls.widget)" in init
    assert "Gtk.FlowBox()" not in init
    assert "controls.set_max_children_per_line" not in init


def test_all_calculation_settings_and_opt_in_llm_remain_in_the_same_row():
    init = method("_init_master_detail")
    # No independent profile selector: global Core selection is authoritative.
    assert "controls.append(self._labeled('Profil', self._identity))" not in init
    assert "self._identity.connect('notify::selected', self._identity_changed)" not in init
    assert "self._identity = Gtk.DropDown(model=self._identity_model)" in init
    assert "controls.append(self._labeled('Référence', self._reference))" in init
    assert "controls.append(self._exclude_owned)" in init
    assert "controls.append(self._labeled('Affinité ≥', self._score))" in init
    assert "controls.append(self._labeled('Diversité', self._exploration))" in init
    assert "controls.append(self._refresh_button)" in init
    assert "controls.append(self._llm_button)" in init
    # No opt-in LLM request on load; only existing explicit clicked handler.
    assert "self._llm_button.connect('clicked', lambda *_: self._explain_selected())" in init
    assert "self._query_controls is None" in init
    assert "self._append_legacy_filters(controls, Gtk)" in init


def test_legacy_core_still_appends_local_filter_widgets_without_new_search():
    legacy = method("_append_legacy_filters")
    assert "controls.append(self._labeled('Catalogue', self._provider))" in legacy
    assert "controls.append(self._labeled('Langue', self._language))" in legacy
    assert "controls.append(self._labeled('Filtrer', self._text))" in legacy
    assert "controls.insert(" not in legacy
    assert "search(" not in legacy
