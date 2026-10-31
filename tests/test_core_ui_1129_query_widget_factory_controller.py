"""P0 #1129: provider-aware query widget factory without a GTK import."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.query_widget_factory_controller import (
    build_query_controls, refresh_query_providers, refresh_library_origin_choices,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "query_widget_factory_controller.py"


def _setup(*, catalog=None, manager=True, extra_catalog=None):
    events = []
    caps = object()
    service = NS(
        searchable_providers=lambda: (
            events.append(("searchable",)) or
            [("remote-z", "Zulu"), ("remote-a", "Alpha"), ("local", "Legacy Local")]
        ),
        catalog_entries=lambda: (
            events.append(("catalog",)) or
            list(extra_catalog if extra_catalog is not None else [
                {"provider_id": "remote-a"},
                {"provider_id": "remote-b"},
                {"provider_id": None},
                {"provider_id": "remote-b"},
            ])
        ),
        provider_display_name=lambda provider: (
            events.append(("label", provider)) or f"Display {provider}"
        ),
        provider_search_capabilities=lambda: (
            events.append(("capabilities",)) or caps
        ),
    )
    plugin_manager = NS(widget_catalog=lambda: (
        events.append(("widget-catalog",)) or catalog
    )) if manager else None
    window = NS(
        service=service,
        plugin_manager=plugin_manager,
        diagnostics=NS(add=lambda *args, **kwargs:
                       events.append(("diagnostic", args, kwargs))),
    )
    factory_calls = []

    class Fallback:
        def __init__(self, providers, changed, activate, *, library=False):
            factory_calls.append((providers, changed, activate, library))
            self.providers = providers
            self.changed = changed
            self.activate = activate
            self.library = library
            self.capability_updates = []

        def set_provider_search_capabilities(self, values):
            self.capability_updates.append(values)
            events.append(("set-capabilities", values))

    changed = Mock()
    activate = Mock()
    def create(library=False, fallback=Fallback):
        return build_query_controls(
            window, changed, activate, library=library, QueryControls=fallback,
        )
    return window, events, factory_calls, changed, activate, caps, create


EXPECTED_PROVIDERS = [
    ("remote-a", "Alpha"),
    ("local", "Local"),
    ("remote-z", "Zulu"),
]
EXPECTED_LIBRARY_PROVIDERS = [
    ("remote-a", "Alpha"),
    ("remote-b", "Display remote-b"),
    ("local", "Local"),
    ("remote-z", "Zulu"),
]


def test_controller_imports_without_gtk_and_retains_public_mainwindow_contract():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(tree)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in item or "main_window" in item for item in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    owner = next(n for n in main.body
                 if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    method = next(n for n in owner.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_query_controls")
    assert [arg.arg for arg in method.args.args] == ["self", "changed", "activate"]
    assert [arg.arg for arg in method.args.kwonlyargs] == ["library"]
    assert "query_widget_factory_controller.build_query_controls" in ast.unparse(method)
    assert "QueryControls=QueryControls" in ast.unparse(method)


def test_without_manager_uses_fallback_and_current_provider_order():
    window, events, calls, changed, activate, caps, create = _setup(manager=False)
    widget = create()
    assert widget.providers == EXPECTED_PROVIDERS
    assert widget.changed is changed
    assert widget.activate is activate
    assert widget.library is False
    assert calls == [(EXPECTED_PROVIDERS, changed, activate, False)]
    assert widget.capability_updates == [caps]
    assert ("widget-catalog",) not in events


def test_local_provider_label_is_authoritative_even_if_remote_lists_local():
    window, events, calls, changed, activate, caps, create = _setup(manager=False)
    widget = create()
    assert widget.providers[1] == ("local", "Local")
    assert widget.providers.count(("local", "Local")) == 1
    assert all(p != "remote-b" for p, _ in widget.providers)
    assert any(p == "remote-b" for p, _ in create(library=True).providers)


def test_catalog_entry_without_provider_id_maps_to_local_without_duplicate():
    window, events, calls, changed, activate, caps, create = _setup(manager=False)
    widget = create(library=True)
    assert sum(p == "local" for p, _ in widget.providers) == 1
    assert ("label", "local") in events


def test_catalog_provider_order_stably_sorted_by_display_name():
    window, events, calls, changed, activate, caps, create = _setup(
        manager=False, extra_catalog=[
            {"provider_id": "x"}, {"provider_id": "b"}, {"provider_id": "x"},
        ],
    )
    widget = create(library=True)
    assert widget.providers == [
        ("remote-a", "Alpha"), ("b", "Display b"), ("x", "Display x"),
        ("local", "Local"), ("remote-z", "Zulu"),
    ]


def test_public_plugin_factory_receives_complete_provider_list_and_callbacks():
    received = []
    plugin_widget = NS(set_provider_search_capabilities=Mock())
    catalog = NS(build_query_controls=lambda *args, **kwargs: (
        received.append((args, kwargs)) or plugin_widget
    ))
    window, events, calls, changed, activate, caps, create = _setup(catalog=catalog)
    result = create()
    assert result is plugin_widget
    assert calls == []
    assert received == [((EXPECTED_PROVIDERS, changed, activate), {"library": False})]
    plugin_widget.set_provider_search_capabilities.assert_called_once_with(caps)


def test_library_plugin_widget_skips_provider_search_capabilities():
    plugin_widget = NS(set_provider_search_capabilities=Mock())
    catalog = NS(build_query_controls=lambda *args, **kwargs: plugin_widget)
    window, events, calls, changed, activate, caps, create = _setup(catalog=catalog)
    result = create(library=True)
    assert result is plugin_widget
    assert calls == []
    plugin_widget.set_provider_search_capabilities.assert_not_called()
    assert ("capabilities",) not in events


def test_library_fallback_keeps_library_scope_and_skips_capabilities():
    window, events, calls, changed, activate, caps, create = _setup(manager=False)
    widget = create(library=True)
    assert widget.library is True
    assert widget.capability_updates == []
    assert calls == [(EXPECTED_LIBRARY_PROVIDERS, changed, activate, True)]
    assert ("capabilities",) not in events


def test_missing_widget_catalog_uses_fallback_not_none():
    window, events, calls, changed, activate, caps, create = _setup(catalog=None)
    widget = create()
    assert widget.providers == EXPECTED_PROVIDERS
    assert calls
    assert events.count(("widget-catalog",)) == 1


def test_non_callable_plugin_factory_uses_standard_controls():
    window, events, calls, changed, activate, caps, create = _setup(
        catalog=NS(build_query_controls="not callable"),
    )
    widget = create()
    assert widget.providers == EXPECTED_PROVIDERS
    assert len(calls) == 1


def test_plugin_factory_returning_none_falls_back_without_diagnostics():
    catalog = NS(build_query_controls=lambda *a, **kw: None)
    window, events, calls, changed, activate, caps, create = _setup(catalog=catalog)
    widget = create()
    assert widget.providers == EXPECTED_PROVIDERS
    assert len(calls) == 1
    assert not any(e[0] == "diagnostic" for e in events)


def test_plugin_build_error_is_logged_and_fallback_used():
    def broken_factory(*_args, **_kwargs):
        raise RuntimeError("factory unavailable")
    catalog = NS(build_query_controls=broken_factory)
    window, events, calls, changed, activate, caps, create = _setup(catalog=catalog)
    widget = create()
    assert widget.providers == EXPECTED_PROVIDERS
    assert len(calls) == 1
    assert ("diagnostic", ("PLUGIN", "query widget", "error"),
            {"detail": {"code": "query_widget_factory_failed"}}) in events


def test_no_capability_setter_is_supported_for_legacy_plugin_widgets():
    widget = NS()
    catalog = NS(build_query_controls=lambda *a, **kw: widget)
    window, events, calls, changed, activate, caps, create = _setup(catalog=catalog)
    assert create() is widget
    assert calls == []
    assert ("capabilities",) not in events


def test_non_callable_capability_setter_is_ignored():
    widget = NS(set_provider_search_capabilities="not callable")
    catalog = NS(build_query_controls=lambda *a, **kw: widget)
    window, events, calls, changed, activate, caps, create = _setup(catalog=catalog)
    assert create() is widget
    assert ("capabilities",) not in events


def test_plugin_factory_failure_does_not_replace_original_callbacks():
    received = []
    def broken_factory(providers, changed, activate, *, library):
        received.append((changed, activate, library))
        raise ValueError("widget failure")
    catalog = NS(build_query_controls=broken_factory)
    window, events, calls, changed, activate, caps, create = _setup(catalog=catalog)
    widget = create()
    assert received == [(changed, activate, False)]
    assert widget.changed is changed
    assert widget.activate is activate
    assert widget.library is False


def test_provider_discovery_failure_does_not_create_partial_widget():
    window, events, calls, changed, activate, caps, create = _setup(manager=False)
    window.service.searchable_providers = Mock(side_effect=ValueError("bad registry"))
    with pytest.raises(ValueError, match="bad registry"):
        create()
    assert calls == []


def test_library_never_touches_plugin_search_capability_registry():
    widget = NS(set_provider_search_capabilities=Mock())
    catalog = NS(build_query_controls=lambda *args, **kw: widget)
    window, events, calls, changed, activate, caps, create = _setup(catalog=catalog)
    window.service.provider_search_capabilities = Mock(
        side_effect=AssertionError("library widget must not read search hints")
    )
    create(library=True)
    window.service.provider_search_capabilities.assert_not_called()


def test_search_excludes_historical_baen_and_library_keeps_it():
    window, _events, _calls, _changed, _activate, _caps, create = _setup(
        manager=False,
        extra_catalog=[{"provider_id": "baen"}, {"provider_id": "remote-a"}],
    )
    window.service.provider_display_name = lambda provider: {"baen": "Baen"}.get(provider, provider)
    assert "baen" not in dict(create().providers)
    assert dict(create(library=True).providers)["baen"] == "Baen"


def test_refresh_after_connection_publishes_new_ebooksgratuits_without_rebuilding():
    window, events, calls, _changed, _activate, _caps, create = _setup()
    active = [("remote-a", "Alpha")]
    window.service.searchable_providers = lambda: list(active)
    controls = NS(
        set_provider_choices=Mock(), set_provider_search_capabilities=Mock(),
    )
    historical = NS(set_provider_choices=Mock())
    window.search_controls = controls
    window.library_controls = historical
    active.append(("ebooksgratuits", "Ebooks libres et gratuits"))
    refresh_query_providers(window)
    selection = dict(controls.set_provider_choices.call_args.args[0])
    assert "ebooksgratuits" in selection
    assert selection["ebooksgratuits"] == "Ebooks libres et gratuits"
    assert "remote-b" not in selection  # historical-only catalogue provider
    assert "remote-b" in dict(historical.set_provider_choices.call_args.args[0])
    controls.set_provider_search_capabilities.assert_called_once()
    assert calls == []  # no page rebuild


def test_refresh_does_nothing_after_close():
    window, _events, _calls, _changed, _activate, _caps, _create = _setup()
    window._closed = True
    window.search_controls = NS(set_provider_choices=Mock())
    refresh_query_providers(window)
    window.search_controls.set_provider_choices.assert_not_called()


def test_unavailable_paris_hint_is_scoped_and_never_selectable():
    """A configured Paris outage stays visible without advertising online search."""
    window, _events, _calls, _changed, _activate, _caps, _create = _setup()
    window.service.current_identity = NS(identity_id="alice")
    window.service.searchable_providers = lambda: []
    window.service.catalogue_source_capabilities = lambda identity_id=None: [
        NS(provider_id="paris", identity_scope="authenticated",
           catalog_search_supported=True, catalog_search_available=False),
    ]
    window.service.accounts_for_identity = lambda identity_id: []  # login failed
    window.service.config = NS(provider_accounts=lambda provider: [
        {"provider": "paris", "identity_id": "alice", "enabled": True,
         "credentials": {"password": "MUST_NOT_LEAK"}}
    ])
    controls = NS(
        set_provider_choices=Mock(),
        set_provider_search_capabilities=Mock(),
        set_provider_status_notice=Mock(),
    )
    window.search_controls = controls
    refresh_query_providers(window)
    assert "paris" not in dict(controls.set_provider_choices.call_args.args[0])
    assert "compte configuré mais source non connectée" in (
        controls.set_provider_status_notice.call_args.args[0]
    )
    assert "MUST_NOT_LEAK" not in controls.set_provider_status_notice.call_args.args[0]

    # Switching profile must not retain evidence of another profile account.
    window.service.current_identity = NS(identity_id="bob")
    refresh_query_providers(window)
    assert "aucun compte rattaché à ce profil" in (
        controls.set_provider_status_notice.call_args.args[0]
    )


def test_connected_or_disabled_paris_has_no_disconnected_notice():
    window, _events, _calls, _changed, _activate, _caps, _create = _setup()
    window.service.current_identity = NS(identity_id="alice")
    controls = NS(
        set_provider_choices=Mock(), set_provider_search_capabilities=Mock(),
        set_provider_status_notice=Mock(),
    )
    window.search_controls = controls
    row = NS(
        provider_id="paris", identity_scope="authenticated",
        catalog_search_supported=True, catalog_search_available=True,
    )
    window.service.catalogue_source_capabilities = lambda identity_id=None: [row]
    refresh_query_providers(window)
    controls.set_provider_status_notice.assert_called_with("")
    row.catalog_search_available = False
    window.service.catalogue_source_capabilities = lambda identity_id=None: []
    refresh_query_providers(window)
    controls.set_provider_status_notice.assert_called_with("")


def test_paris_disabled_and_legacy_unscoped_account_do_not_look_connected():
    window, _events, _calls, _changed, _activate, _caps, _create = _setup()
    window.service.current_identity = NS(identity_id="alice")
    window.service.catalogue_source_capabilities = lambda identity_id=None: [
        NS(provider_id="paris", identity_scope="authenticated",
           catalog_search_supported=True, catalog_search_available=False),
    ]
    controls = NS(
        set_provider_choices=Mock(), set_provider_search_capabilities=Mock(),
        set_provider_status_notice=Mock(),
    )
    window.search_controls = controls
    window.service.config = NS(provider_accounts=lambda provider: [
        {"provider": "paris", "identity_id": "alice", "enabled": False},
    ])
    refresh_query_providers(window)
    assert "compte désactivé" in controls.set_provider_status_notice.call_args.args[0]
    window.service.config = NS(provider_accounts=lambda provider: [
        {"provider": "paris", "enabled": True},
    ])
    refresh_query_providers(window)
    assert "profil du compte à vérifier" in controls.set_provider_status_notice.call_args.args[0]


@pytest.mark.parametrize(("present", "enabled", "expected"), [
    (False, True, "plugin absent"),
    (True, False, "plugin désactivé"),
    (True, True, "catalogue non disponible"),
])
def test_search_paris_plugin_absent_or_disabled_is_scoped(
    present, enabled, expected,
):
    window, events, _calls, _changed, _activate, _caps, _create = _setup()
    controls = NS(
        set_provider_choices=Mock(), set_provider_search_capabilities=Mock(),
        set_provider_status_notice=Mock(),
    )
    window.search_controls = controls
    window.service.current_identity = NS(identity_id="alice")
    window.service.catalogue_source_capabilities = lambda identity_id=None: []
    window.service.config = NS(provider_accounts=lambda provider: [
        {"provider": "paris", "identity_id": "alice", "enabled": True,
         "credentials": {"password": "PRIVATE_NOT_FOR_DIAGNOSTICS"}},
    ])
    window.service.registry = NS(maybe_get=lambda provider: object() if present else None)
    window.service._provider_enabled_for_identity = lambda provider, identity: enabled
    refresh_query_providers(window)
    text = controls.set_provider_status_notice.call_args.args[0]
    assert expected in text
    assert "PRIVATE_NOT_FOR_DIAGNOSTICS" not in text
    window.service.current_identity = NS(identity_id="bob")
    refresh_query_providers(window)
    controls.set_provider_status_notice.assert_called_with("")


def test_library_selector_includes_scoped_local_and_numilog_origins():
    window, events, _calls, _changed, _activate, _caps, create = _setup(
        manager=False, extra_catalog=[{"provider_id": "local"}],
    )
    window.service.current_identity = NS(identity_id="alice")
    window.library_controls = NS(set_provider_choices=Mock())
    # The Core has already consolidated this single book and both origins.
    snapshot = [{
        "key": "book-a", "identity_id": "alice", "provider_id": "local",
        "providers": ["local", "numilog"],
    }]
    refresh_library_origin_choices(window, snapshot, identity_id="alice")
    options = dict(window.library_controls.set_provider_choices.call_args.args[0])
    assert options["numilog"] == "Numilog — Mes achats (compte)"
    assert options["local"] == "Local"
    assert len(options) == len(set(options))
    # An offline Library filter must not authorize or announce an online
    # Numilog source that searchable_providers has not declared.
    assert "numilog" not in dict(create(library=False).providers)
    assert not any(e[0] == "diagnostic" for e in events)


def test_library_selector_rejects_foreign_or_malformed_origin_evidence():
    window, _events, _calls, _changed, _activate, _caps, _create = _setup(
        extra_catalog=[{"provider_id": "local"}], manager=False,
    )
    window.service.current_identity = NS(identity_id="alice")
    window.library_controls = NS(set_provider_choices=Mock())
    snapshot = [
        {"identity_id": "alice", "provider_id": "local",
         "providers": ["local", "numilog"]},
        {"identity_id": "bob", "provider_id": "foreign",
         "providers": ["private-provider"]},
        {"identity_id": "alice", "providers": "password=PRIVATE_SOURCE"},
        {"identity_id": "alice", "providers": [None, 41, "account@secret",
                                               "a\\nb", ""]},
        {"identity_id": "alice", "providers": ["unsafe"] * 33},
    ]
    refresh_library_origin_choices(window, snapshot, identity_id="alice")
    selections = dict(window.library_controls.set_provider_choices.call_args.args[0])
    assert "numilog" in selections
    for forbidden in ("private-provider", "account@secret", "password=PRIVATE_SOURCE",
                      "unsafe", "a\\nb"):
        assert forbidden not in repr(selections)
    assert window._library_origin_provider_ids == ("local", "numilog")


def test_library_snapshot_choices_are_revoked_on_profile_switch():
    window, _events, _calls, _changed, _activate, _caps, _create = _setup(
        extra_catalog=[{"provider_id": "local"}], manager=False,
    )
    window.service.current_identity = NS(identity_id="alice")
    window.library_controls = NS(set_provider_choices=Mock())
    refresh_library_origin_choices(window, [
        {"identity_id": "alice", "providers": ["local", "numilog"]},
    ], identity_id="alice")
    assert "numilog" in dict(window.library_controls.set_provider_choices.call_args.args[0])
    window.service.current_identity = NS(identity_id="bob")
    window.search_controls = NS(
        set_provider_choices=Mock(), set_provider_search_capabilities=Mock(),
    )
    refresh_query_providers(window)
    assert "numilog" not in dict(window.library_controls.set_provider_choices.call_args.args[0])
    # Delayed callback from the previous identity cannot repopulate choices.
    last = window.library_controls.set_provider_choices.call_count
    refresh_library_origin_choices(window, [
        {"identity_id": "alice", "providers": ["numilog"]},
    ], identity_id="alice")
    assert window.library_controls.set_provider_choices.call_count == last
    refresh_library_origin_choices(window, [
        {"identity_id": "bob", "providers": ["local", "bnf-pret"]},
    ], identity_id="bob")
    assert "bnf-pret" in dict(window.library_controls.set_provider_choices.call_args.args[0])
    assert "numilog" not in dict(window.library_controls.set_provider_choices.call_args.args[0])


def test_library_origin_choices_hook_is_after_successful_identity_fence():
    owner = ROOT / "library_refresh.py"
    text = ast.unparse(ast.parse(owner.read_text(encoding="utf-8")))
    assert "not self._identity_request_current(identity_id, generation)" in text
    assert "refresh_library_origin_choices(self, entries, identity_id=identity_id)" in text
    assert text.index("self._show_library_entries(entries)") < text.index(
        "refresh_library_origin_choices(self, entries, identity_id=identity_id)"
    )
