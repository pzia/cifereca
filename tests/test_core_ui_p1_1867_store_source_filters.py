"""P0 #5054 / P1 #1867 — generic source display cannot change routing or permissions.

No GTK, provider connection, network, acquisition, rights or config mutation.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

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
            "source-a": "Source A",
            "source-b": "Source B",
            "source-b-public": "Source B — Catalogue public",
        }.get(provider, "other"),
        provider_search_capabilities=lambda: {},
    )
    return NS(
        service=service, plugin_manager=None, _closed=False,
        diagnostics=NS(add=Mock()), search_controls=None, library_controls=None,
    ), events


def test_search_labels_are_taken_from_plugin_discovery_not_brand_overrides():
    supplied = [
        ("source-a", "Source A (plugin)"),
        ("source-b", "Bibliothèque B (plugin)"),
        ("source-b-public", "Catalogue public (plugin)"),
        ("new-provider", "Nouvelle source (plugin)"),
    ]
    window, events = _window(supplied)
    labels = dict(_provider_choices(window))
    for provider_id, label in supplied:
        assert labels[provider_id] == label
    assert labels["local"] == "Local"
    assert events == ["searchable"]


def test_one_connected_source_does_not_invent_a_second_catalogue_provider():
    window, events = _window([("source-a", "Source A")])
    choices = _provider_choices(window)
    assert dict(choices) == {
        "source-a": "Source A",
        "local": "Local",
    }
    assert "source-a-public" not in dict(choices)
    assert events == ["searchable"]


def test_optional_public_source_is_absent_until_explicitly_active():
    connected = [("source-b", "Source B")]
    window, events = _window(connected)
    assert list(dict(_provider_choices(window))) == ["local", "source-b"]
    connected.append(("source-b-public", "Source B — Catalogue public"))
    values = dict(_provider_choices(window))
    assert set(values) == {"local", "source-b", "source-b-public"}
    assert values["source-b"] != values["source-b-public"]
    assert events == ["searchable", "searchable"]  # no inventory or login calls


def test_library_historical_provider_labels_do_not_enable_online_search():
    window, events = _window(
        [("source-a", "Source A")], historical=("source-b", "source-b-public"),
    )
    assert "source-b" not in dict(_provider_choices(window, library=False))
    library = dict(_provider_choices(window, library=True))
    assert library["source-b"] == "Source B"
    assert library["source-b-public"] == "Source B — Catalogue public"
    assert "source-b-public" not in dict(_provider_choices(window, library=False))
    assert events == ["searchable", "searchable", "historical", "searchable"]


def test_real_factory_hands_stable_id_label_pairs_to_plugin_without_remapping():
    connected = [
        ("source-a", "Source A"),
        ("source-b", "Source B"),
        ("source-b-public", "Source B — Catalogue public"),
        ("source-c", "Source C"),
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
    assert set(dict(choices)) == {"source-a", "source-b", "source-b-public", "source-c", "local"}
    assert len(choices) == len(set(provider_id for provider_id, _ in choices))
    assert all(credential not in repr(choices) for credential in ("token=", "account-id", "password"))
    assert widget.set_provider_search_capabilities.call_count == 1
    assert events == ["searchable"]


def test_refresh_between_profiles_drops_disabled_public_source():
    connected = [("source-b", "Source B"), ("source-b-public", "Source B")]
    window, events = _window(connected)
    window.search_controls = NS(set_provider_choices=Mock(), set_provider_search_capabilities=Mock())
    window.library_controls = NS(set_provider_choices=Mock())
    refresh_query_providers(window)
    choices = dict(window.search_controls.set_provider_choices.call_args.args[0])
    assert "source-b-public" in choices

    # Source is disabled for the next profile by Core discovery. The
    # presentation layer must not create it as a synthetic alternative.
    connected[:] = [("source-b", "Source B")]
    refresh_query_providers(window)
    choices = dict(window.search_controls.set_provider_choices.call_args.args[0])
    assert "source-b-public" not in choices
    assert choices["source-b"] == "Source B"
    assert window.search_controls.set_provider_choices.call_count == 2
    assert window.search_controls.set_provider_search_capabilities.call_count == 2
    assert events.count("historical") == 2


def test_plugin_manager_uses_the_vocabulary_in_read_only_rendering():
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] /
           "src/cifereca/gui/plugin_manager_page.py").read_text()
    tree = ast.parse(src)
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                and n.name == "PluginManagerPage")
    render_source = ast.unparse(node)
    assert "ui_usage_hint" in render_source
    assert "store_plugin_usage_hint" not in render_source
    assert "cette activation ne confère aucun droit" in render_source
    assert 'if d.instantiation == \'singleton_public\'' in render_source
    assert "activation.set_active(self.manager.enabled_for_identity" in render_source
    assert "self._identity_toggle(pid, iid, button.get_active())" in render_source
