"""#3416: failed per-identity source enumeration stays visible and non-actionable.

This exercises the production GTK controller in existing headless doubles.
No provider network, account changes, file writes or consent updates.
"""
from __future__ import annotations

import pytest

from tests.test_core_ui_1347_sync_action_scope import _fixture


def test_broker_source_enumeration_failure_is_actionable_not_empty():
    window, broker, calls, _build, _page = _fixture()
    def failed(_identity):
        calls.append(("sources-error", _identity))
        raise RuntimeError("https://private.invalid/?token=PRIVATE_3416")
    broker.sources = failed
    window._refresh_sync_page()
    assert window._sync_all_button.sensitive is False
    assert window._sync_summary.label == (
        "État des sources indisponible · lecture impossible"
    )
    assert window._sync_attention_row.visible is True
    assert window._sync_attention_icon.icon_name == "dialog-error-symbolic"
    assert "Impossible de lire les sources" in window._sync_attention_text.label
    assert window._sync_sources_box.children[0].label == (
        "Sources indisponibles pour cette identité."
    )
    assert "PRIVATE_3416" not in repr((
        window._sync_attention_text.label,
        window._sync_summary.label,
        window._sync_sources_box.children,
    ))
    window._sync_all_button.click()
    assert window.runner.tasks == []
    assert ("sources-error", "alice") in calls


@pytest.mark.parametrize("invalid", [
    None, False, "alice", {"source_id": "private"}, (entry for entry in [1]),
    list(range(1025)),
])
def test_malformed_broker_source_result_never_looks_like_no_sources(invalid):
    window, broker, calls, _build, _page = _fixture()
    broker.sources = lambda _identity: invalid
    window._refresh_sync_page()
    assert window._sync_summary.label == (
        "État des sources indisponible · lecture impossible"
    )
    assert window._sync_attention_row.visible is True
    assert window._sync_attention_icon.icon_name == "dialog-error-symbolic"
    assert window._sync_all_button.sensitive is False
    assert window.runner.tasks == []


def test_stale_identity_response_does_not_raise_new_account_attention():
    window, broker, calls, _build, _page = _fixture()
    def switch_identity(_identity):
        window.service.current_identity.identity_id = "bob"
        raise RuntimeError("PRIVATE_ACCOUNT")
    broker.sources = switch_identity
    window._refresh_sync_page()
    assert window._sync_attention_row.visible is False
    assert window._sync_all_button.sensitive is False
    assert window.runner.tasks == []


def test_empty_but_valid_broker_snapshot_retains_original_empty_state():
    window, broker, calls, _build, _page = _fixture()
    broker.sources = lambda _identity: ()
    window._refresh_sync_page()
    assert window._sync_all_button.sensitive is False
    assert window._sync_attention_row.visible is False
    assert window._sync_sources_box.children[0].label == (
        "Aucune source synchronisable pour cette identité"
    )
