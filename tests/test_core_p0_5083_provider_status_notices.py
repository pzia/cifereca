"""#5083: vendor-neutral, read-only, identity-fenced search status UI."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.provider_status_notices import search_status_notice


ROOT = Path(__file__).resolve().parents[1] / "src"


def _window(*, provider="fabricated-source", identity="A", registered=True,
            available=False, enabled=True, accounts=None, messages=()):
    rows = list(accounts if accounts is not None else [{
        "provider": provider, "identity_id": identity, "enabled": enabled,
        "credentials": {"password": "PRIVATE_TOKEN_FOR_TEST"},
    }])
    calls = []
    descriptor = NS(
        id=provider, search_status_notice_schema="cifereca.provider-search-ui/1",
        search_status_notices=tuple(messages),
    )
    plugin = NS(descriptor=descriptor)
    inventory = [NS(
        provider_id=provider, identity_scope="authenticated",
        catalog_search_supported=True, catalog_search_available=available,
    )]
    service = NS(
        current_identity=NS(identity_id=identity),
        searchable_providers=lambda: (_ for _ in ()).throw(AssertionError("search GET")),
        catalogue_source_capabilities=lambda *, identity_id: (
            calls.append(("snapshot", identity_id)) or list(inventory)),
        config=NS(provider_accounts=lambda provider_id=None: (
            calls.append(("configured", provider_id)) or list(rows))),
        registry=NS(maybe_get=lambda key: (
            calls.append(("registry", key)) or (plugin if registered else None))),
        _provider_enabled_for_identity=lambda provider_id, identity_id: enabled,
    )
    return NS(service=service), calls, inventory, rows, plugin


def test_plugin_contribution_is_selected_without_issuing_network_or_auth():
    window, calls, _inventory, _accounts, _plugin = _window(messages=[
        ("account_disconnected", "Message du fournisseur (hors ligne)."),
    ])
    assert search_status_notice(window) == "Message du fournisseur (hors ligne)."
    assert ("snapshot", "A") in calls
    assert not any(value[0] == "search" for value in calls)


def test_live_source_does_not_show_disconnected_account_or_call_get():
    window, _calls, _inventory, _accounts, _plugin = _window(available=True)
    assert search_status_notice(window) == ""


@pytest.mark.parametrize("enabled,registered,expected", [
    (True, False, "plugin absent"),
    (False, True, "plugin désactivé"),
    (True, True, "catalogue non disponible"),
])
def test_missing_or_disabled_provider_keeps_read_only_explanation(
    enabled, registered, expected,
):
    window, _calls, _inventory, rows, _plugin = _window(
        registered=registered, enabled=enabled,
    )
    window.service.catalogue_source_capabilities = lambda *, identity_id: []
    assert expected in search_status_notice(window)
    assert rows[0]["credentials"]["password"] not in search_status_notice(window)


def test_profile_switch_cannot_reuse_a_foreign_account_notice():
    window, _calls, _inventory, rows, _plugin = _window()
    assert "compte configuré" in search_status_notice(window)
    window.service.current_identity = NS(identity_id="B")
    assert "compte configuré" not in search_status_notice(window)
    assert "aucun compte rattaché" in search_status_notice(window)
    assert rows[0]["credentials"]["password"] not in search_status_notice(window)


def test_identity_rotation_during_snapshot_revokes_notice_entirely():
    window, _calls, inventory, _accounts, _plugin = _window()
    def rotate(*, identity_id):
        window.service.current_identity = NS(identity_id="B")
        return inventory
    window.service.catalogue_source_capabilities = rotate
    assert search_status_notice(window) == ""


@pytest.mark.parametrize("hostile", [
    "Bearer PRIVATE_TOKEN",
    "token=PRIVATE_TOKEN",
    "http://private.invalid",
    "message\\nPRIVATE_TOKEN",
    "X" * 241,
    "\\u202eSpoof",
])
def test_plugin_text_is_bounded_and_cannot_publish_hostile_payload(hostile):
    window, _calls, _inventory, _rows, _plugin = _window(messages=[
        ("account_disconnected", hostile),
    ])
    value = search_status_notice(window)
    assert "Source : compte configuré" in value
    assert "PRIVATE_TOKEN" not in value
    assert "private.invalid" not in value
    assert "Spoof" not in value


def test_public_catalogue_and_unscoped_unknown_provider_do_not_grant_account():
    window, _calls, _inventory, _accounts, _plugin = _window()
    window.service.catalogue_source_capabilities = lambda *, identity_id: [
        NS(provider_id="fabricated-source", identity_scope="public",
           catalog_search_supported=True, catalog_search_available=False),
    ]
    window.service.config.provider_accounts = lambda provider_id=None: []
    assert search_status_notice(window) == ""


def test_core_gui_is_vendor_neutral_and_paris_owns_versioned_texts():
    gui = (ROOT / "cifereca/gui/query_widget_factory_controller.py").read_text()
    projector = (ROOT / "cifereca/gui/provider_status_notices.py").read_text()
    assert "_paris_search_notice" not in gui
    for text in (gui, projector):
        assert "'paris'" not in text
        assert '"paris"' not in text
        assert "NUMILOG_LIBRARY" not in text
    plugin_text = (ROOT / "plugins/provider-paris/cifereca_paris.py").read_text()
    tree = ast.parse(plugin_text)
    declarations = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "ProviderDescriptor"
    ]
    assert declarations
    paris = next(node for node in declarations
                 if any(key.arg == "id" and isinstance(key.value, ast.Constant)
                        and key.value.value == "paris" for key in node.keywords))
    fields = {key.arg: key.value for key in paris.keywords}
    assert ast.literal_eval(fields["search_status_notice_schema"]) == (
        "cifereca.provider-search-ui/1"
    )
    messages = dict(ast.literal_eval(fields["search_status_notices"]))
    assert set(messages) == {
        "unknown", "plugin_missing", "plugin_disabled", "catalog_unavailable",
        "account_disconnected", "account_disabled", "account_unscoped",
        "account_missing",
    }
