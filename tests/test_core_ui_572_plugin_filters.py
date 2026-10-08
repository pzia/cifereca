from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.plugin_filters import (
    filter_package_plugins,
    package_matches_query,
    status_matches_mode,
)


PAGE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/plugin_manager_page.py"


def _status(
    plugin_id: str,
    *,
    name: str,
    description: str = "",
    enabled: bool = True,
    state: str = "ready",
    package_id: str = "org.cifereca.provider.kobo",
):
    descriptor = NS(
        id=plugin_id,
        display_name=name,
        description=description,
        package_id=package_id,
        type="provider.library",
    )
    return NS(descriptor=descriptor, enabled=enabled, state=state)


def _method(name: str) -> str:
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PluginManagerPage")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_search_matches_package_plugin_name_id_and_description_case_insensitively():
    package = {"package_id": "org.cifereca.provider.kobo", "name": "Kobo", "publisher": "CIFERECA"}
    statuses = (
        _status("kobo.catalog", name="Catalogue Kobo", description="Recherche distante"),
        _status("kobo.library", name="Bibliothèque Kobo", description="Livres possédés"),
    )
    assert len(filter_package_plugins(package, statuses, query="KOBO")) == 2
    assert [s.descriptor.id for s in filter_package_plugins(package, statuses, query="possédés")] == ["kobo.library"]
    assert [s.descriptor.id for s in filter_package_plugins(package, statuses, query="kobo.catalog")] == ["kobo.catalog"]


def test_state_filters_are_view_only_and_attention_includes_degraded():
    ready = _status("ready", name="Ready", enabled=True, state="ready")
    disabled = _status("disabled", name="Disabled", enabled=False, state="disabled")
    blocked = _status("blocked", name="Blocked", enabled=True, state="blocked")
    degraded = _status("degraded", name="Degraded", enabled=True, state="degraded")
    statuses = (ready, disabled, blocked, degraded)
    assert [s.descriptor.id for s in filter_package_plugins({}, statuses, mode="active")] == ["ready", "blocked", "degraded"]
    assert [s.descriptor.id for s in filter_package_plugins({}, statuses, mode="disabled")] == ["disabled"]
    assert [s.descriptor.id for s in filter_package_plugins({}, statuses, mode="attention")] == ["blocked", "degraded"]
    assert status_matches_mode(blocked, "attention")


def test_missing_package_search_uses_package_context():
    package = {"package_id": "org.cifereca.provider.paris", "name": "Bibliothèques de Paris"}
    assert package_matches_query(package, "paris")
    assert package_matches_query(package, "org.cifereca.provider.paris")
    assert not package_matches_query(package, "kobo")


def test_filter_callbacks_only_rerender_inventory_and_never_toggle_plugins():
    text_changed = _method("_plugin_filter_text_changed")
    mode_changed = _method("_plugin_filter_mode_changed")
    refilter = _method("_refilter_plugins")
    combined = "\n".join((text_changed, mode_changed, refilter))
    assert "self._refilter_plugins()" in combined
    assert "self._render_inventory(" in combined
    assert "set_enabled" not in combined
    assert "_toggle(" not in combined


def test_refresh_exposes_search_and_four_view_filters():
    source = _method("refresh")
    assert "Gtk.SearchEntry()" in source
    assert "Rechercher un plugin ou un package…" in source
    assert "('Tous', 'Actifs', 'Désactivés', 'À examiner')" in source
    assert "self._plugin_inventory" in source


def test_empty_filtered_view_is_contextualized():
    source = _method("_render_inventory")
    assert "Aucun plugin ne correspond à la recherche ou au filtre sélectionné." in source
    assert "filter_package_plugins(" in source
