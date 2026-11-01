"""P1 #3416: configured connection failure belongs in the Sync attention header.

A configured credential is never a fake SyncSourceRef. Count only closed,
identity-scoped notices; no provider I/O, consent or local artifact mutation.
"""
from __future__ import annotations

import pytest

from tests.test_core_ui_1347_sync_action_scope import _source
from tests.test_core_ui_p1_3105_paris_cards import _window


@pytest.mark.parametrize(("provider_status", "expected"), [
    ("ready", "connexion non établie"),
    (None, "absent"),
    ("skeleton", "non prêt"),
])
def test_only_configured_paris_connection_still_shows_attention(
    provider_status, expected,
):
    window, _broker, calls = _window(provider_status=provider_status)
    assert len(window._sync_sources_box.children) == 1
    row = window._sync_sources_box.children[0].children[0]
    assert expected in row.subtitle
    assert window._sync_attention_row.get_visible() is True
    assert window._sync_attention_icon.icon_name == "dialog-warning-symbolic"
    assert window._sync_attention_text.label == (
        "À vérifier : 0 échec(s) · 1 avertissement(s). "
        "Consultez les comptes signalés ci-dessous."
    )
    assert "PRIVATE" not in window._sync_attention_text.label
    assert not window._sync_all_button.sensitive
    assert not window.runner.tasks
    assert not any(event[0] == "user-reconnect" for event in calls)


@pytest.mark.parametrize(("enabled", "config_enabled"), [
    (False, True), (True, False),
])
def test_explicitly_disabled_source_or_plugin_does_not_raise_false_alert(
    enabled, config_enabled,
):
    window, _broker, calls = _window(
        provider_status="ready", enabled=enabled, config_enabled=config_enabled,
    )
    assert "désactivé" in window._sync_sources_box.children[0].children[0].subtitle
    assert window._sync_attention_row.get_visible() is False
    assert not window.runner.tasks
    assert not any(event[0] == "user-reconnect" for event in calls)


def test_unavailable_connection_aggregates_with_genuine_live_source_failure():
    live = _source(
        provider_id="bnf-pret", source_id="bnf", status="failed",
        error="https://private.invalid/?token=PRIVATE_SYNTHETIC",
    )
    window, _broker, calls = _window(sources=[live])
    assert window._sync_attention_row.get_visible() is True
    assert window._sync_attention_icon.icon_name == "dialog-error-symbolic"
    assert window._sync_attention_text.label == (
        "À vérifier : 1 échec(s) · 1 avertissement(s). "
        "Consultez les comptes signalés ci-dessous."
    )
    assert "PRIVATE" not in window._sync_attention_text.label
    assert "2 source(s)" not in window._sync_summary.label
    assert "1 source(s)" in window._sync_summary.label
    assert len(window._sync_sources_box.children) == 2
    assert not window.runner.tasks
    assert all(event[0] != "user-reconnect" for event in calls)
