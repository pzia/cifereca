"""#1120: automatic-loan orchestration logs only closed facts/pseudonyms."""
from pathlib import Path
from types import SimpleNamespace as NS
import logging

import pytest
from cifereca.core.loan_safe_trace import trace_integer, loan_trace_ref
from cifereca.identities.resolver import AdeptProfileResolutionError
from cifereca.plugin_api import UserFacingError
from tests.test_adept_artifact_orchestration import _service, _loan, PDF

PRIVATE = 'PRIVATE_LOAN_PROFILE_PATH'


@pytest.mark.parametrize(('value', 'expected'), [(None,None),(True,None),(False,None),
    ('PRIVATE',None),([],None),({},None),(-256,None),(-255,-255),(0,0),(255,255),(256,None)])
def test_trace_integer_never_formats_arbitrary_values(value, expected):
    assert trace_integer(value, minimum=-255, maximum=255) == expected


@pytest.mark.parametrize('scenario', ['provider_failure', 'profile_missing', 'unready',
                                      'borrower_changed', 'adept_failure', 'success'])
def test_real_orchestration_keeps_outcomes_files_and_requests_but_not_private_trace_args(
    tmp_path, monkeypatch, caplog, scenario,
):
    caplog.set_level(logging.INFO)
    service = _service(tmp_path)
    loan = _loan(loan_id=PRIVATE+'-loan', record_id=PRIVATE+'-edition', title=PRIVATE+'-title')
    service._persist_active_loans('default', [loan])
    source = tmp_path / (PRIVATE+'.acsm')
    source.write_text('<fulfillmentToken><format>application/pdf</format></fulfillmentToken>')
    requests = []
    def acquire(received, **kw):
        assert received is loan and kw == {}
        if scenario == 'provider_failure':
            raise RuntimeError(PRIVATE+' provider failure')
        return NS(kind='acsm', path=source, acsm_path=source)
    monkeypatch.setattr(service, 'acquire_digital_loan', acquire)
    def profile(owner):
        assert owner == 'default'
        if scenario == 'profile_missing':
            raise AdeptProfileResolutionError(PRIVATE+' profile error', code=PRIVATE+'-profile-code')
        return PRIVATE+'-profile', PRIVATE+'-profile-source'
    monkeypatch.setattr(service, 'resolve_adept_profile_for_identity', profile)
    monkeypatch.setattr(service, 'kobo_profiles', lambda: [{
        'profile_id':'profile-hb', 'name':PRIVATE+'-profile', 'fetch_capable':True,
        'provisioning':{'acsm_fetch_ready':scenario != 'unready'}}])
    if scenario == 'borrower_changed':
        checks = []
        def borrower(*a, **kw):
            checks.append(True)
            return ('default', None) if len(checks) == 1 else (None, PRIVATE+'-borrower-code')
        monkeypatch.setattr(service, '_paris_adept_identity_for_loan', borrower)
    def execute(request):
        requests.append(request)
        assert request['metadata']['loan_id'] == loan.id
        if scenario == 'adept_failure':
            raise UserFacingError(PRIVATE+' runtime failure', code=PRIVATE+'-runtime-code')
        path = Path(request['destination']); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(PDF)
        return {'ok': True, 'profile': PRIVATE+'-profile', 'loan_id': PRIVATE+'-adept-loan',
                'artifact':{'path':str(path),'format':'pdf','drm':'none'}}
    monkeypatch.setattr(service, 'execute_acquisition_request', execute)
    monkeypatch.setattr(service, 'runtime_diagnostic', lambda *_: {
        'returncode':PRIVATE, 'duration_ms':True, 'stdout_mode':PRIVATE, 'message':PRIVATE})
    try:
        result = service._acquire_active_loan_automatically(loan)
        expected = ('retryable_error' if scenario == 'provider_failure' else
                    'complete' if scenario == 'success' else 'attention_required')
        assert result['status'] == expected and result['loan_id'] == loan.id, result
        if scenario == 'profile_missing':
            assert result['code'] == 'adept_profile_required'
            assert result['error'] == 'Profil ADEPT indisponible'
            assert PRIVATE not in result['code'] + result['error']
        if scenario == 'borrower_changed': assert result['code'] == PRIVATE+'-borrower-code'
        if scenario == 'adept_failure': assert result['code'] == PRIVATE+'-runtime-code'
        if scenario == 'unready': assert result['code'] == 'adept_profile_acsm_not_ready'
        if scenario == 'success':
            assert Path(result['path']).read_bytes() == PDF and not source.exists()
        else:
            assert source.exists()
        assert len(requests) == (1 if scenario in {'success','adept_failure'} else 0)
        records = [r for r in caplog.records
                   if r.funcName == '_acquire_active_loan_automatically_guarded'
                   or (r.funcName == 'fulfill' and Path(r.pathname).name == 'loan_adept_fulfillment.py')]
        assert records
        for record in records:
            assert PRIVATE not in record.getMessage() + repr(record.args)
            assert 'ref=' in record.getMessage()
            assert '/home/' not in record.getMessage() and 'http' not in record.getMessage()
        expected_ref = loan_trace_ref(loan, 'default')
        assert all('loan_ref='+expected_ref in r.getMessage() for r in records[1:])
        if scenario == 'success':
            assert any('returncode=None duration_ms=None' in r.getMessage() for r in records)
    finally:
        service.close()
