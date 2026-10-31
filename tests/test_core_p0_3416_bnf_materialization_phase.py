"""P0 #3416: BnF file automation is a separate capability, never Kobo status.

All observations are offline with the actual Core broker and BnF HTTPX fixture.
No GET of signed loan actions, no ADEPT, no automatic consent or write.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.gui.sync_view import sync_source_phase_summary
from cifereca.plugin_api import SyncPhaseState
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core


def _phase_text(source):
    return sync_source_phase_summary(source)


def test_bnf_real_session_advertises_distinct_capability_not_kobo_materialization(
    connected_core,
):
    service, session, account, _loan, _state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    source, = broker.sources(account.identity_id)
    assert source.provider_id == "bnf-pret"
    assert "loan_auto_materialization" in source.sync_capabilities
    assert "local_materialization" not in source.sync_capabilities
    phase = next(x for x in source.phase_states if x.phase == "materialize")
    assert phase.status == "not_supported"  # Kobo generic action still unavailable
    rendered = _phase_text(source)
    assert "Téléchargement local : Téléchargement distinct de cette synchronisation" in rendered
    assert "Téléchargement local : Réussie" not in rendered
    assert "Téléchargement local : Non prise en charge" not in rendered
    assert broker.bnf_auto_download_enabled(source.source_id) is False
    assert downloaded == []
    assert service.storage.acquisitions() == []
    assert service.storage.placements() == []


def test_bnf_without_actual_reader_has_no_false_capability(
    connected_core, monkeypatch,
):
    service, session, account, _loan, _state, downloaded = connected_core
    monkeypatch.setattr(session, "download_digital", None)
    broker = SynchronizationBrokerImpl(service)
    source, = broker.sources(account.identity_id)
    assert "loan_auto_materialization" not in source.sync_capabilities
    assert "Téléchargement local : Non prise en charge" in _phase_text(source)
    assert downloaded == []


def test_generic_view_only_uses_declared_capability_and_does_not_overwrite_real_failure():
    def source(phases, capabilities=()):
        return NS(provider_id="provider.custom", phase_states=phases,
                  sync_capabilities=capabilities)
    marked = ("loan_auto_materialization",)
    unavailable = source((SyncPhaseState("materialize", "not_supported"),))
    assert "Non prise en charge" in _phase_text(unavailable)
    separate = source((SyncPhaseState("materialize", "not_supported"),), marked)
    assert "Téléchargement distinct" in _phase_text(separate)
    for real in ("failed", "partial", "attention_required", "success"):
        observed = source((SyncPhaseState("materialize", real),), marked)
        displayed = _phase_text(observed)
        assert "Téléchargement distinct" not in displayed
        assert "Non prise en charge" not in displayed
    # A made-up capability or hostile unbounded collection changes nothing.
    garbage = source((SyncPhaseState("materialize", "not_supported"),),
                     tuple("loan_auto_materialization" for _ in range(33)))
    assert "Non prise en charge" in _phase_text(garbage)
