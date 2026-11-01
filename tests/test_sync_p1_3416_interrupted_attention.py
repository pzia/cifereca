"""P1 #3416: visible, provider-neutral warning when the last Sync is not confirmed.

Only CoreUI projections/GTK doubles are touched; never retry or grant consent.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_view import sync_source_attention, sync_source_presentation
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


@pytest.mark.parametrize(("status", "expected"), [
    ("interrupted", "Synchronisation : interrompue"),
    ("unknown", "Synchronisation : état non confirmé"),
    ("future_state", "Synchronisation : état non confirmé"),
    (None, "Synchronisation : état non confirmé"),
    (123, "Synchronisation : état non confirmé"),
    ("x" * 300, "Synchronisation : état non confirmé"),
])
@pytest.mark.parametrize("provider", ["fictional.future", "paris", "kobo"])
def test_interrupted_or_untrusted_current_state_is_visible_without_provider_dispatch(
    status, expected, provider,
):
    src = _source(provider_id=provider, status=status, error="",
                  last_success="2026-10-31T08:00:00")
    assert sync_source_attention(src) == ("warning", expected)
    shown = sync_source_presentation(src)
    assert shown.sync_label in {"Interrompue", "État non confirmé"}
    assert "Synchronisation : " + shown.sync_label in shown.summary
    assert "Dernier succès " not in shown.sync_label
    assert "Dernier succès confirmé" in shown.summary
    assert shown.action_enabled is True


@pytest.mark.parametrize("status", ["idle", "success", "ok", "running", "cancelled"])
def test_normal_or_explicitly_cancelled_source_is_not_an_actionable_warning(status):
    src = _source(provider_id="future", status=status)
    assert sync_source_attention(src) is None


def test_unrecognized_state_warning_never_overrides_a_real_failure():
    src = _source(
        provider_id="fictional.future", status="unknown",
        error="https://private.invalid?token=DO_NOT_PRINT",
        phase_states=(NS(phase="placement", status="failed"),),
    )
    assert sync_source_attention(src) == (
        "error", "Placement en bibliothèque : échec — action nécessaire",
    )


def test_non_synchronizable_source_does_not_raise_false_warning():
    assert sync_source_attention(_source(
        provider_id="public.catalogue", sync_capable=False, status="unknown",
    )) is None


def test_gtk_summary_exposes_interruptions_without_network_or_downloads():
    sources = [
        _source(provider_id="future", source_id="aborted", status="interrupted",
                last_success="2026-10-31T08:00:00"),
        _source(provider_id="future", source_id="healthy", status="success"),
    ]
    window, _broker, calls, _build, _page = _fixture(sources=sources)
    group = window._sync_sources_box.children[0]
    row = group.children[0]
    assert row.subtitle.startswith("Synchronisation : interrompue")
    assert row.prefixes[0].icon_name == "dialog-warning-symbolic"
    assert "avertissement" in window._sync_attention_text.label
    assert window._sync_attention_row.visible is True
    assert not window.runner.tasks
    assert all(call[0] == "sources" for call in calls)


@pytest.mark.parametrize(("status", "expected_label", "expected_alert"), [
    ("ok", "Dernier succès 31/10/2026 08:00", None),
    ("skipped", "Non exécutée", ("warning", "Synchronisation : non exécutée")),
])
def test_real_broker_success_and_revoked_skip_are_not_misclassified(
    status, expected_label, expected_alert,
):
    # SourceSyncBroker writes status=ok on committed success and status=skipped
    # when source admission has been revoked before the provider inventory.
    src = _source(
        provider_id="fictional.future", status=status, error="",
        last_attempt="2026-11-01T09:00:00",
        last_success="2026-10-31T08:00:00",
    )
    assert sync_source_attention(src) == expected_alert
    shown = sync_source_presentation(src)
    assert shown.sync_label == expected_label
    if status == "skipped":
        assert "Dernier succès confirmé : 31/10/2026 08:00" in shown.summary
    else:
        assert "État non confirmé" not in shown.summary
        assert "Dernier succès confirmé :" not in shown.summary
