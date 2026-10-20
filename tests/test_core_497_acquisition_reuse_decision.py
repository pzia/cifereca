from __future__ import annotations

from pathlib import Path

from cifereca.core.acquisition_decision import decide_artifact_reuse


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / 'src/cifereca/services.py'


def test_valid_ledger_artifact_is_reused_before_catalog():
    decision = decide_artifact_reuse(
        force_refresh=False, catalog_valid=True, ledger_complete_valid=True,
        legacy_repair=False, previous_exists=True, previous_path_exists=True, previous_valid=True, previous_protected=False,
    )
    assert decision.action == 'reuse'
    assert decision.reason == 'valid_ledger_artifact'
    assert decision.source == 'ledger'


def test_valid_catalog_artifact_is_reused_when_ledger_is_not_complete():
    decision = decide_artifact_reuse(
        force_refresh=False, catalog_valid=True, ledger_complete_valid=False,
        legacy_repair=False,
    )
    assert decision.action == 'reuse'
    assert decision.reason == 'valid_catalog_artifact'
    assert decision.source == 'catalog'


def test_explicit_refresh_is_never_collapsed_into_automatic_reuse():
    decision = decide_artifact_reuse(
        force_refresh=True, catalog_valid=True, ledger_complete_valid=True,
        legacy_repair=False,
    )
    assert decision.action == 'reacquire_explicit'
    assert decision.reason == 'explicit_force_refresh'
    assert decision.source == 'user'


def test_legacy_acsm_repair_is_distinct_from_normal_acquisition():
    decision = decide_artifact_reuse(
        force_refresh=False, catalog_valid=False, ledger_complete_valid=False,
        legacy_repair=True, legacy_reason='missing_post_process_marker',
        previous_exists=True, previous_path_exists=True, previous_valid=True, previous_protected=False,
    )
    assert decision.action == 'repair_legacy'
    assert decision.reason == 'missing_post_process_marker'
    assert decision.source == 'ledger'


def test_missing_previous_artifact_has_stable_reason():
    decision = decide_artifact_reuse(
        force_refresh=False, catalog_valid=False, ledger_complete_valid=False,
        legacy_repair=False, previous_exists=True, previous_path_exists=False,
    )
    assert decision.action == 'acquire'
    assert decision.reason == 'ledger_artifact_missing'


def test_invalid_previous_artifact_has_stable_reason():
    decision = decide_artifact_reuse(
        force_refresh=False, catalog_valid=False, ledger_complete_valid=False,
        legacy_repair=False, previous_exists=True, previous_path_exists=True, previous_valid=False, previous_protected=False,
    )
    assert decision.reason == 'ledger_artifact_invalid'


def test_protected_previous_artifact_has_stable_reason():
    decision = decide_artifact_reuse(
        force_refresh=False, catalog_valid=False, ledger_complete_valid=False,
        legacy_repair=False, previous_exists=True, previous_path_exists=True, previous_valid=True, previous_protected=True,
    )
    assert decision.reason == 'ledger_artifact_protected'


def test_no_previous_artifact_requires_normal_acquisition():
    decision = decide_artifact_reuse(
        force_refresh=False, catalog_valid=False, ledger_complete_valid=False,
        legacy_repair=False, previous_exists=False, previous_path_exists=False,
    )
    assert decision.action == 'acquire'
    assert decision.reason == 'no_local_artifact'
    assert decision.source == 'none'


def test_service_logs_one_structured_decision_before_provider_call(tmp_path, monkeypatch, caplog):
    import logging
    from tests.test_core_0977_adept_retry_guard import _service, _loan
    service = _service(tmp_path)
    caplog.set_level(logging.INFO)
    calls = []
    def acquire(*args, **kwargs):
        decisions = [r for r in caplog.records if r.getMessage().startswith('TRACE download loan_auto decision ')]
        assert len(decisions) == 1
        assert 'action=acquire' in decisions[0].getMessage()
        calls.append('provider')
        raise RuntimeError('synthetic provider failure')
    monkeypatch.setattr(service, 'acquire_digital_loan', acquire)
    try:
        result = service._acquire_active_loan_automatically(_loan())
        assert result['status'] == 'retryable_error'
        assert calls == ['provider']
    finally:
        service.close()


def test_reuse_returns_before_provider_call_and_exposes_reason(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest
    from tests.test_core_0977_adept_retry_guard import _service, _loan, _seed
    service = _service(tmp_path)
    path = tmp_path / 'retained.epub'
    path.write_bytes(b'synthetic local artifact')
    _seed(service, status='complete', executor='provider', metadata={'path': str(path)})
    before = service.storage.acquisitions()
    monkeypatch.setattr('cifereca.services.inspect_artifact',
                        lambda *a: SimpleNamespace(valid=True, protected=False))
    monkeypatch.setattr(service, 'catalog_index_path', lambda *a, **kw: {'status': 'indexed'})
    def forbidden(*a, **kw): pytest.fail('reuse cannot acquire, fulfill or rewrite ledger')
    monkeypatch.setattr(service, 'acquire_digital_loan', forbidden)
    monkeypatch.setattr(service, 'execute_acquisition_request', forbidden)
    monkeypatch.setattr(service.storage, 'acquisition_put', forbidden)
    try:
        assert service._acquire_active_loan_automatically(_loan()) == {
            'status': 'unchanged', 'loan_id': 'loan-a', 'decision': 'reuse',
            'reason': 'valid_ledger_artifact', 'source': 'ledger',
        }
        assert service.storage.acquisitions() == before
        assert path.read_bytes() == b'synthetic local artifact'
    finally:
        service.close()


def test_successful_acquisition_persists_decision_metadata():
    source = SERVICES.read_text(encoding='utf-8')
    # #1120: the successful receipt is composed in a pure helper while the
    # guarded caller keeps the same storage write and decision provenance.
    receipt = (SERVICES.parent / 'core' / 'loan_completion_receipt.py').read_text(
        encoding='utf-8'
    )
    assert 'loan_completion_receipt.completed_receipt(' in source
    assert '"acquisition_decision": decision.as_dict()' in receipt
