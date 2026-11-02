"""P1 #4142: real broker's three-phase partial is ambiguous, not success.

An unproven BnF inventory may coexist with blocked downloads. The Core UI
must expose unknown coverage without suppressing a real attention warning.
All cases are offline and never make a provider request or acquisition.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.gui.sync_view import (
    sync_source_attention, sync_source_informational_partial,
    sync_source_unverified_remote_inventory, sync_source_presentation,
)
from cifereca.storage import Storage
from tests.test_core_0978_source_state import fake_broker
from tests.test_core_ui_1347_sync_action_scope import _fixture
from tests.test_sync_p1_3416_informational_partial import source


def real_broker_phase_states():
    # Exercise the actual broker's phase projection, with only local helper
    # methods stubbed. No provider I/O or extra source state is synthesized.
    broker = NS(
        _safe_phase_timestamp=SynchronizationBrokerImpl._safe_phase_timestamp,
        _source_id=lambda account: "sync:synthetic",
        _materialization_state=lambda source_id: {},
    )
    phases = SynchronizationBrokerImpl._phase_states(
        broker, NS(provider_id="bnf-pret"),
        {"status": "partial", "last_attempt": "2026-11-02T05:29:06+00:00"},
        ("entitlements", "loan_auto_materialization"),
    )
    assert [(row.phase, row.status) for row in phases] == [
        ("discover_rights", "partial"),
        ("materialize", "not_supported"),
        ("reconcile", "not_supported"),
    ]
    return phases


@pytest.mark.parametrize("provider", ["bnf-pret", "provider.future", "paris"])
def test_real_three_phase_broker_partial_explains_coverage_without_inventing_success(provider):
    row = source(provider=provider, phase_states=real_broker_phase_states())
    assert sync_source_unverified_remote_inventory(row) is True
    assert sync_source_informational_partial(row) is False
    assert sync_source_attention(row) == ("warning", "Notices et droits : résultat partiel")
    shown = sync_source_presentation(row)
    assert shown.sync_label.startswith("Partielle · couverture distante non attestée")
    assert "Inventaire distant : couverture non attestée" in shown.summary
    assert "une action requise ou un échec reste possible" in shown.summary
    assert "aucun échec de téléchargement local déclaré" not in shown.summary
    assert "Synchronisation complète" not in shown.summary


def test_actual_three_phase_broker_shows_warning_not_false_information_in_gtk():
    # Fictional provider: no BnF-specific UI switch or network dependency.
    row = source(provider="provider.future", phase_states=real_broker_phase_states())
    window, _broker, calls, _build, _page = _fixture(sources=[row])
    widget = window._sync_sources_box.children[0].children[0]
    assert widget.prefixes[0].icon_name == "dialog-warning-symbolic"
    assert window._sync_attention_icon.icon_name == "dialog-warning-symbolic"
    assert window._sync_attention_text.label == (
        "À vérifier : 0 échec(s) · 1 avertissement(s). "
        "Consultez les comptes signalés ci-dessous."
    )
    assert "Inventaire distant : couverture non attestée" in widget.subtitle
    assert window.runner.tasks == []
    assert all(call[0] == "sources" for call in calls)
    assert row.status == "partial"


def test_real_core_attention_required_cannot_be_hidden_by_missing_local_phase(tmp_path):
    storage = Storage(tmp_path / "sync.sqlite3")
    try:
        broker = fake_broker(storage, partial=True)
        source_id = broker.sources()[0].source_id
        outcome = broker.synchronize_source(source_id)
        assert outcome.ok is False
        assert outcome.facts["attention_required"] == 1
        row = broker.sources()[0]
        assert sync_source_unverified_remote_inventory(row)
        assert sync_source_informational_partial(row) is False
        assert sync_source_attention(row)[0] == "warning"
        shown = sync_source_presentation(row)
        assert "Partielle" in shown.sync_label
        assert "une action requise ou un échec reste possible" in shown.summary
        assert "aucun échec de téléchargement local déclaré" not in shown.summary
    finally:
        storage.close()


@pytest.mark.parametrize("reconcile", [
    "failed", "blocked", "partial", "interrupted", "unknown", "running",
    "success", "attention_required", None, 1,
])
def test_unknown_or_actionable_reconciliation_is_not_a_clean_three_phase_partial(reconcile):
    first, local, _reconcile = real_broker_phase_states()
    row = source(phase_states=(first, local, NS(phase="reconcile", status=reconcile)))
    assert not sync_source_unverified_remote_inventory(row)
    assert not sync_source_informational_partial(row)
    assert sync_source_attention(row)[0] in {"warning", "error"}


@pytest.mark.parametrize("local", [
    "failed", "partial", "blocked", "interrupted", "unknown", "running", "success",
])
def test_three_phase_local_state_changes_are_not_invented_as_clean_coverage(local):
    first, _local, reconcile = real_broker_phase_states()
    row = source(phase_states=(first, NS(phase="materialize", status=local), reconcile))
    assert not sync_source_unverified_remote_inventory(row)
    assert not sync_source_informational_partial(row)
    assert sync_source_attention(row)[0] in {"warning", "error"}


def test_duplicate_unknown_and_malformed_phases_fail_closed():
    first, local, reconcile = real_broker_phase_states()
    for phases in (
        (first, local, reconcile, reconcile),
        (first, local, NS(phase="future-phase", status="success")),
        (first, local, NS(phase="reconcile", status="not_supported"),
         NS(phase="future", status="success")),
        (first, NS(phase="materialize", status="not_supported"),
         NS(phase="discover_rights", status="partial")),
    ):
        row = source(phase_states=phases)
        assert not sync_source_unverified_remote_inventory(row)
        assert not sync_source_informational_partial(row)


def test_prior_success_remains_historical_not_a_new_success():
    row = source(provider="bnf-pret", phase_states=real_broker_phase_states(),
                 last_success="2026-11-01T20:10:00+00:00")
    shown = sync_source_presentation(row)
    assert "Partielle" in shown.sync_label
    assert "Dernier succès confirmé" in shown.summary
    assert row.status == "partial"
    assert "Synchronisation : Dernier succès" not in shown.summary
