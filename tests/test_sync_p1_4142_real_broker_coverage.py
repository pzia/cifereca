"""P1 #4142: real Core SyncBroker has three phase states, not two.

This is a *presentation* fix only. It must not assert exhaustive remote
inventory, modify a Loan/consent or run a provider request.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SyncBroker
from cifereca.gui.sync_view import (
    sync_source_attention, sync_source_informational_partial,
    sync_source_presentation,
)
from tests.test_core_ui_1347_sync_action_scope import _fixture
from tests.test_sync_p1_3416_informational_partial import source


def real_broker_phase_states():
    # Directly invoke the actual Core broker projection: no service, storage
    # or login is needed for a source without the Kobo-local phase capability.
    phases = SyncBroker._phase_states(
        NS(), NS(provider_id="bnf-pret"),
        {"status": "partial", "last_attempt": "2026-11-02T05:29:06+00:00"},
        ("entitlements", "loan_auto_materialization"),
    )
    assert [(p.phase, p.status) for p in phases] == [
        ("discover_rights", "partial"),
        ("materialize", "not_supported"),
        ("reconcile", "not_supported"),
    ]
    return phases


@pytest.mark.parametrize("provider", ["bnf-pret", "provider.future", "paris"])
def test_real_three_phase_broker_inventory_partial_is_information_not_failure(provider):
    phases = real_broker_phase_states()
    row = source(provider=provider, phase_states=phases)
    assert sync_source_informational_partial(row)
    assert sync_source_attention(row) == (
        "info", "Notices et droits : couverture partielle (information)"
    )
    shown = sync_source_presentation(row)
    assert shown.sync_label.startswith("Couverture partielle (information)")
    assert "Couverture distante des prêts/notices non attestée" in shown.summary
    assert "aucun échec de téléchargement local déclaré" in shown.summary
    assert "Synchronisation complète" not in shown.summary


def test_three_phase_state_produces_information_banner_on_real_headless_gtk_controller():
    # A fictional provider avoids executing any provider-specific UI hook.
    source_row = source(provider="provider.future", phase_states=real_broker_phase_states())
    window, broker, calls, _build, _page = _fixture(sources=[source_row])
    row = window._sync_sources_box.children[0].children[0]
    assert row.prefixes[0].icon_name == "dialog-information-symbolic"
    assert window._sync_attention_icon.icon_name == "dialog-information-symbolic"
    assert window._sync_attention_text.label == (
        "Information : 1 source(s) à couverture partielle ; aucun échec local déclaré."
    )
    assert "Couverture distante des prêts/notices non attestée" in row.subtitle
    assert window.runner.tasks == []
    assert all(call[0] == "sources" for call in calls)
    assert source_row.status == "partial"


@pytest.mark.parametrize("reconcile", [
    "failed", "blocked", "partial", "interrupted", "unknown", "running",
    "success", "attention_required", None, 1,
])
def test_unproved_reconciliation_does_not_silence_warning(reconcile):
    phases = real_broker_phase_states()
    changed = (*phases[:2], NS(phase="reconcile", status=reconcile))
    row = source(phase_states=changed)
    assert not sync_source_informational_partial(row)
    assert sync_source_attention(row)[0] in {"warning", "error"}


@pytest.mark.parametrize("local", [
    "failed", "partial", "blocked", "interrupted", "unknown", "running",
])
def test_real_three_phase_failure_is_not_informational(local):
    first, _local, final = real_broker_phase_states()
    row = source(phase_states=(first, NS(phase="materialize", status=local), final))
    assert not sync_source_informational_partial(row)
    assert sync_source_attention(row)[0] in {"warning", "error"}


def test_duplicate_unknown_and_malformed_phases_fail_closed():
    first, local, final = real_broker_phase_states()
    cases = [
        (first, local, final, final),
        (first, local, NS(phase="future-phase", status="success")),
        (first, local, NS(phase="reconcile", status="not_supported"),
         NS(phase="future", status="success")),
        (first, NS(phase="materialize", status="not_supported"),
         NS(phase="discover_rights", status="partial")),
    ]
    for phases in cases:
        row = source(phase_states=phases)
        assert not sync_source_informational_partial(row)


def test_no_provider_claim_is_promoted_to_complete_without_remote_coverage():
    row = source(provider="bnf-pret", phase_states=real_broker_phase_states(),
                 last_success="2026-11-01T20:10:00+00:00")
    shown = sync_source_presentation(row)
    assert "Couverture partielle (information)" in shown.summary
    assert "Dernier succès confirmé" in shown.summary
    assert row.status == "partial"
    assert "Synchronisation : Dernier succès" not in shown.summary
