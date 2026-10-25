"""P1 #1867 — store/catalogue display cannot change source routing or permissions.

No GTK, provider connection, network, acquisition, rights or config mutation.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui.store_usage_labels import (
    store_search_source_label, store_plugin_usage_hint,
)
from cifereca.gui.query_widget_factory_controller import (
    _provider_choices, build_query_controls, refresh_query_providers,
)


def _window(connected, *, historical=()):
    events = []
    def searchable():
        events.append("searchable")
        return list(connected)
    def entries():
        events.append("historical")
        return [{"provider_id": name} for name in historical]
    service = NS(
        searchable_providers=searchable,
        catalog_entries=entries,
        provider_display_name=lambda provider: {
            "kobo": "Kobo",
            "numilog": "Numilog",
            "numilog-catalog": "Numilog — Catalogue public",
        }.get(provider, "other"),
        provider_search_capabilities=lambda: {},
    )
    return NS(
        service=service, plugin_manager=None, _closed=False,
        diagnostics=NS(add=Mock()), search_controls=None, library_controls=None,
    ), events


def test_fixed_source_vocabulary_never_changes_unknown_plugin_labels():
    assert store_search_source_label("kobo", "fallback") == "Kobo — Catalogue et compte"
    assert store_search_source_label("numilog", "fallback") == "Numilog — Mes achats (compte)"
    assert store_search_source_label("numilog-catalog", "fallback") == "Numilog — Catalogue public"
    assert store_search_source_label("paris", "Bibliothèques de Paris") == "Bibliothèques de Paris"
    assert store_search_source_label("completely-unknown", "Nom inconnu") == "Nom inconnu"


def test_kobo_filter_exposes_one_route_not_a_second_catalogue_provider():
    window, events = _window([("kobo", "Kobo")])
    choices = _provider_choices(window)
    assert dict(choices) == {
        "kobo": "Kobo — Catalogue et compte",
        "local": "Local",
    }
    assert "kobo-catalog" not in dict(choices)
    assert events == ["searchable"]


def test_numilog_public_is_absent_until_it_is_a_real_active_source():
    connected = [("numilog", "Numilog")]
    window, events = _window(connected)
    assert list(dict(_provider_choices(window))) == ["local", "numilog"]
    connected.append(("numilog-catalog", "Numilog — Catalogue public"))
    values = dict(_provider_choices(window))
    assert set(values) == {"local", "numilog", "numilog-catalog"}
    assert values["numilog"] != values["numilog-catalog"]
    assert events == ["searchable", "searchable"]  # no inventory or login calls


def test_library_historical_provider_labels_do_not_enable_online_search():
    window, events = _window(
        [("kobo", "Kobo")], historical=("numilog", "numilog-catalog"),
    )
    assert "numilog" not in dict(_provider_choices(window, library=False))
    library = dict(_provider_choices(window, library=True))
    assert library["numilog"] == "Numilog — Mes achats (compte)"
    assert library["numilog-catalog"] == "Numilog — Catalogue public"
    assert "numilog-catalog" not in dict(_provider_choices(window, library=False))
    assert events == ["searchable", "searchable", "historical", "searchable"]


def test_real_factory_hands_stable_id_label_pairs_to_plugin_without_remapping():
    connected = [
        ("kobo", "Kobo"),
        ("numilog", "Numilog"),
        ("numilog-catalog", "Numilog — Catalogue public"),
        ("paris", "Bibliothèques de Paris"),
    ]
    window, events = _window(connected)
    calls = []
    widget = NS(set_provider_search_capabilities=Mock())
    window.plugin_manager = NS(widget_catalog=lambda: NS(
        build_query_controls=lambda values, changed, activate, *, library:
            (calls.append((values, library)) or widget)
    ))
    output = build_query_controls(
        window, Mock(), Mock(), QueryControls=Mock(side_effect=AssertionError("fallback")),
    )
    assert output is widget
    choices, library = calls[0]
    assert library is False
    assert set(dict(choices)) == {"kobo", "numilog", "numilog-catalog", "paris", "local"}
    assert len(choices) == len(set(provider_id for provider_id, _ in choices))
    assert all(credential not in repr(choices) for credential in ("token=", "account-id", "password"))
    assert widget.set_provider_search_capabilities.call_count == 1
    assert events == ["searchable"]


def test_refresh_between_profiles_never_keeps_disabled_numilog_public_selection():
    connected = [("numilog", "Numilog"), ("numilog-catalog", "Numilog")]
    window, events = _window(connected)
    window.search_controls = NS(set_provider_choices=Mock(), set_provider_search_capabilities=Mock())
    window.library_controls = NS(set_provider_choices=Mock())
    refresh_query_providers(window)
    choices = dict(window.search_controls.set_provider_choices.call_args.args[0])
    assert "numilog-catalog" in choices

    # Source is disabled for the next profile by Core discovery. The
    # presentation layer must not create it as a synthetic alternative.
    connected[:] = [("numilog", "Numilog")]
    refresh_query_providers(window)
    choices = dict(window.search_controls.set_provider_choices.call_args.args[0])
    assert "numilog-catalog" not in choices
    assert choices["numilog"] == "Numilog — Mes achats (compte)"
    assert window.search_controls.set_provider_choices.call_count == 2
    assert window.search_controls.set_provider_search_capabilities.call_count == 2
    assert events.count("historical") == 2


def test_plugin_manager_wording_preserves_optional_public_identity_switch():
    kobo = store_plugin_usage_hint("provider.kobo")
    assert "aucun second compte" in kobo
    assert "Mes achats" in kobo
    assert "aucun droit d’achat" in store_plugin_usage_hint("provider.kobo.catalog")

    private = store_plugin_usage_hint("provider.numilog")
    public = store_plugin_usage_hint("provider.numilog.catalog")
    assert "Mes achats" in private and "source optionnelle distincte" in private
    assert "sans compte" in public and "sans téléchargement" in public
    assert "indépendante" in public
    assert store_plugin_usage_hint("ui.page.recommendations") == ""
    # Usage is an explanation, never a permission object or a source.
    for value in (kobo, private, public):
        assert "https://" not in value
        assert "credential_id" not in value


def test_plugin_manager_uses_the_vocabulary_in_read_only_rendering():
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] /
           "src/cifereca/gui/plugin_manager_page.py").read_text()
    tree = ast.parse(src)
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                and n.name == "PluginManagerPage")
    render_source = ast.unparse(node)
    assert "store_plugin_usage_hint(d.id)" in render_source
    assert "if d.id == 'provider.numilog.catalog'" in render_source
    assert "activation.set_active(self.manager.enabled_for_identity" in render_source
    assert "self._identity_toggle(pid, iid, button.get_active())" in render_source
