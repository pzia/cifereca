"""Structured failure events must not repeat private UI/ledger evidence."""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_failure_diagnostics import failure_fields
from cifereca.core.loan_safe_trace import loan_trace_ref
from cifereca.plugin_api import UserFacingError
from tests.test_adept_artifact_orchestration import _service, _loan

PRIVATE='PRIVATE_DIAGNOSTIC_TITLE_ACCOUNT_PATH'


@pytest.mark.parametrize('stage,code', [('adept','kobo_adept_invalid_json'),
    ('adept',PRIVATE), ('adept','kobo_adept_failed'), ('adept','kobo_adept_invalid_utf8'), ('provider','pnb_redirect_refused'), ('adept','pnb_redirect_refused')])
def test_real_service_keeps_exact_public_error_retry_ledger_process_but_expurgates_diagnostic(
    tmp_path,monkeypatch,stage,code,
):
    service=_service(tmp_path)
    loan=_loan(loan_id=PRIVATE+'loan',record_id=PRIVATE+'edition',title=PRIVATE)
    service._persist_active_loans('default',[loan])
    source=tmp_path/(PRIVATE+'.acsm');source.write_text('<fulfillmentToken><format>application/pdf</format></fulfillmentToken>')
    error=UserFacingError(PRIVATE+' raw failure /home/PRIVATE',code=code)
    raw={'returncode':2**100,'duration_ms':-1,'stdout_bytes':5,'stderr_bytes':True,
         'message':PRIVATE,'stdout_mode':PRIVATE,'profile':PRIVATE}
    def acquire(*_a,**_kw):
        if stage=='provider':raise error
        return NS(kind='acsm',path=source,acsm_path=source)
    monkeypatch.setattr(service,'acquire_digital_loan',acquire)
    monkeypatch.setattr(service,'resolve_adept_profile_for_identity',lambda _:('hb','explicit'))
    monkeypatch.setattr(service,'kobo_profiles',lambda:[{'name':'hb','profile_id':'profile-hb','fetch_capable':True}])
    monkeypatch.setattr(service,'execute_acquisition_request',lambda *_:(_ for _ in ()).throw(error))
    monkeypatch.setattr(service,'runtime_diagnostic',lambda *_:raw)
    try:
        result=service._acquire_active_loan_automatically(loan)
        assert result['loan_id']==loan.id
        assert result['error'] == (
            'Redirection PNB refusée ; consulter les traces PNB'
            if code == 'pnb_redirect_refused'
            else 'Acquisition ADEPT indisponible'
        )
        assert PRIVATE not in result['error'] + str(result['code'])
        assert result['status']==('retryable_error' if code=='kobo_adept_invalid_json' else 'attention_required')
        if stage=='adept':
            assert result['process']['returncode']==2**100 and result['process']['duration_ms']==-1
            assert result['process']['stdout_bytes']==5
        ledger=service.storage.acquisitions(provider_id=loan.source_provider)
        safe_code = code if code != PRIVATE else 'adept_error'
        assert len(ledger)==1 and ledger[0]['error_code']==safe_code
        assert ledger[0]['error_message'] == result['error']
        assert PRIVATE not in ledger[0]['error_code'] + ledger[0]['error_message']
        assert ledger[0]['metadata']['provider_loan_id']==loan.id
        assert source.is_file()
        events=[e for e in service.diagnostics.snapshot() if e['operation'] in {'acquisition failed','redirect refused'}]
        assert len(events)==(int(stage=='adept')+int(code=='pnb_redirect_refused'))
        assert PRIVATE not in repr(events) and '/home/' not in repr(events)
        for event in events:
            assert event['detail']['loan_ref']==loan_trace_ref(loan,'default')
            assert 'message' not in event['detail'] and 'provider_loan_id' not in event['detail']
            assert 'plugin_version' not in event['detail']
            if event['category']=='KOBO-ADEPT':
                assert event['detail']['code']==safe_code
                assert event['detail']['stdout_bytes']==5
                assert not set(event['detail']) & {'returncode','duration_ms','stderr_bytes','stdout_mode'}
    finally:service.close()


@pytest.mark.parametrize('key,value', [('returncode',True),('returncode',256),('returncode',-256),
    ('duration_ms',86_400_001),('duration_ms',-1),('stdout_bytes',-1),('stderr_bytes',2**63),
    ('artifact_bytes','5'),('artifact_present',1)])
def test_projection_never_coerces_or_publishes_bad_process_facts(key,value):
    raw={key:value,'path':PRIVATE,'stdout':PRIVATE,'stderr':PRIVATE,'profile':PRIVATE}
    before=dict(raw)
    detail=failure_fields(code=PRIVATE,process=raw,loan_ref=PRIVATE)
    assert detail=={'phase':'adept_fulfillment','code':'unclassified_error'}
    assert raw==before


def test_projection_has_fixed_meaning_for_all_valid_bounds_and_never_stringifies_unknowns():
    raw=dict(returncode=-255,duration_ms=86_400_000,stdout_bytes=0,stderr_bytes=2**63-1,
             artifact_bytes=10,artifact_present=False)
    result=failure_fields(code='adept_error',process=raw,loan_ref='a'*20)
    assert result==dict(raw,phase='adept_fulfillment',code='adept_error',loan_ref='a'*20)
    class Hostile:
        def __str__(self):raise AssertionError('must not stringify')
        def __eq__(self,_):raise AssertionError('must not compare untrusted types')
    assert failure_fields(code=Hostile(),process=Hostile(),loan_ref=Hostile())=={
        'phase':'adept_fulfillment','code':'unclassified_error'}
