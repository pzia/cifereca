from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from cifereca.gui.sync_view import sync_identity_action_label, sync_source_presentation


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src" / "cifereca" / "gui" / "sync_controller.py"


def _method(name: str) -> str:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    method = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def _source(**values):
    defaults = dict(
        label="Paris · Camille",
        provider_id="paris",
        enabled=True,
        connected=True,
        sync_capable=True,
        status="idle",
        last_attempt="",
        last_success="",
        error="",
    )
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_activation_connection_and_sync_capability_are_independent_axes():
    presentation = sync_source_presentation(_source(enabled=True, connected=False))
    assert presentation.activation_label == "Activée"
    assert presentation.connection_label == "Déconnectée"
    assert "Activation : Activée" in presentation.summary
    assert "Connexion : Déconnectée" in presentation.summary
    assert presentation.action_enabled is False
    assert presentation.disabled_reason == "Reconnectez ce compte avant de synchroniser."


def test_connected_but_not_synchronizable_explains_disabled_action():
    presentation = sync_source_presentation(_source(sync_capable=False))
    assert presentation.connection_label == "Connectée"
    assert presentation.sync_label == "Non prise en charge"
    assert presentation.action_enabled is False
    assert "ne fournit pas de synchronisation" in presentation.disabled_reason


def test_last_success_and_named_action_are_user_facing():
    presentation = sync_source_presentation(_source(last_success="2026-10-08T09:12:00+00:00"))
    assert presentation.sync_label == "Dernier succès 08/10/2026 09:12"
    assert presentation.action_label == "Synchroniser Paris · Camille"
    assert presentation.action_enabled is True


def test_global_action_is_contextualized_by_identity():
    assert sync_identity_action_label(SimpleNamespace(label="Camille")) == "Synchroniser Camille"
    assert sync_identity_action_label(None) == "Synchroniser les sources"


def test_sync_page_uses_named_actions_and_disabled_reasons():
    source = _method("_refresh_sync_page")
    assert "sync_identity_action_label(identity)" in source
    assert "sync_source_presentation(source)" in source
    assert "presentation.summary" in source
    assert "presentation.action_label" in source
    assert "presentation.disabled_reason" in source
    assert "all_now.set_sensitive(actionable)" in source


def test_kobo_sync_action_describes_online_notices_not_downloads():
    source = _source(provider_id="kobo", label="Compte Kobo lié")
    presentation = sync_source_presentation(source)
    assert presentation.action_enabled is True
    assert presentation.action_label == "Synchroniser Compte Kobo lié"
    assert "ne télécharge aucun ebook" in presentation.summary
    assert "Connexion : Connectée" in presentation.summary

    offline = sync_source_presentation(_source(
        provider_id="kobo", connected=False,
    ))
    assert offline.action_label == "Synchroniser Paris · Camille"
    assert offline.action_enabled is False
    assert "Reconnectez ce compte" in offline.disabled_reason


def test_non_kobo_sync_actions_stay_unchanged():
    paris = sync_source_presentation(_source(provider_id="paris"))
    assert paris.action_label == "Synchroniser Paris · Camille"
    assert "ne télécharge aucun ebook" not in paris.summary
