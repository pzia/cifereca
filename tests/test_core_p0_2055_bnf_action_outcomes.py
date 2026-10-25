"""Actual Core/HTTPX/SQLite BnF action results, with no real account or ADEPT."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import pytest

from cifereca.core.diagnostic_bnf_loan_action import (
    BnfLoanActionError, public_fields, public_log_fields, log_message,
)
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostic_task_context import diagnostic_task_scope, diagnostic_task_reference
from cifereca.diagnostics import Diagnostics
from cifereca.plugin_api import ProviderError
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core


def events(service, tmp_path, **kwargs):
    log = tmp_path/'empty-log'
    log.write_text('')
    return query_diagnostic_events(service.diagnostics, log_path=log,
                                   domain='loan_download', provider_id='bnf-pret', **kwargs)['events']


def test_stage_terminal_is_unique_and_does_not_invent_task_reference(connected_core, tmp_path):
    service, session, account, loan, state, downloaded = connected_core
    result = service.stage_bnf_loan_acsm(loan, confirmed=True, expected_identity_id='default')
    rows = events(service,tmp_path)
    assert len(rows)==1
    detail = rows[0]['detail']
    assert detail['action']=='stage_loan_token'
    assert detail['status']=='success' and detail['reason_code']=='candidate_acsm'
    assert detail['phase']=='source_revalidation'
    assert 'task_ref' not in detail and 'task_ref' not in rows[0]
    assert 'path' not in detail
    assert Path(result['path']).is_file() and len(downloaded)==1
    assert not service.storage.entitlements() and not service.storage.acquisitions()


def test_actual_task_context_is_shared_with_terminal_without_private_identifiers(connected_core,tmp_path,caplog):
    service, session, account, loan, state, downloaded = connected_core
    caplog.set_level('INFO',logger='cifereca')
    with diagnostic_task_scope():
        ref=diagnostic_task_reference()
        service.stage_bnf_loan_acsm(loan,confirmed=True,expected_identity_id='default')
    row,=events(service,tmp_path)
    assert row['task_ref']==row['detail']['task_ref']==ref
    line=next(r.getMessage() for r in caplog.records if r.getMessage().startswith('EVENT bnf loan action '))
    assert public_log_fields(line)==row['detail']
    assert loan.id not in line and loan.source_credential not in line and loan.record_id not in line


@pytest.mark.parametrize('status,reason',[(401,'auth_required'),(403,'access_denied'),(429,'rate_limited'),(500,'http_unavailable'),(302,'download_redirect_refused')])
def test_http_refusal_preserves_only_closed_provider_reason(connected_core,tmp_path,status,reason):
    service,session,account,loan,state,downloaded=connected_core
    original=session.client.http._transport.handler
    def handler(request):
        if request.url.path.startswith('/loans/'):
            downloaded.append(request)
            return httpx.Response(status,content=b'private ACSM secret',headers={'location':'https://private.invalid/session'})
        return original(request)
    session.client.http._transport.handler=handler
    with pytest.raises(BnfLoanActionError,match='^bnf_'+reason+'$'):
        service.stage_bnf_loan_acsm(loan,confirmed=True,expected_identity_id='default')
    row,=events(service,tmp_path)
    assert row['detail']['reason_code']=='bnf_'+reason
    assert row['detail']['phase']=='provider_delivery'
    assert row['detail']['status']=='failed' and len(downloaded)==1
    assert 'private' not in json.dumps(row)
    assert not service.storage.entitlements() and not service.storage.acquisitions()


def test_missing_live_loan_is_not_mistaken_for_failed_acsm_get(connected_core,tmp_path):
    service,session,account,loan,state,downloaded=connected_core
    state['activity']='<section class="active-loans"></section>'
    with pytest.raises(BnfLoanActionError,match='bnf_loan_not_observed'):
        service.stage_bnf_loan_acsm(loan,confirmed=True,expected_identity_id='default')
    row,=events(service,tmp_path)
    assert row['detail']['phase']=='provider_delivery'
    assert row['detail']['reason_code']=='bnf_loan_not_observed'
    assert not downloaded  # phase does not assert an HTTP transfer occurred


def test_private_exception_does_not_become_diagnostic_or_public_error(connected_core,tmp_path,monkeypatch,caplog):
    service,session,account,loan,state,downloaded=connected_core
    def bad(*args,**kwargs):raise ProviderError('https://private.invalid/ACSM?token=SECRET')
    monkeypatch.setattr(session,'download_digital',bad)
    with pytest.raises(BnfLoanActionError,match='^bnf_loan_token_unavailable$'):
        service.stage_bnf_loan_acsm(loan,confirmed=True,expected_identity_id='default')
    row,=events(service,tmp_path)
    assert 'SECRET' not in json.dumps(row) and 'private.invalid' not in caplog.text


@pytest.mark.parametrize('result,status,reason',[
    ({'status':'complete'},'success','complete'),
    ({'status':'unchanged'},'success','unchanged'),
    ({'status':'attention_required','code':'adept_profile_required'},'attention_required','adept_profile_required'),
    ({'status':'attention_required','code':'adept_profile_ambiguous'},'attention_required','adept_profile_ambiguous'),
    ({'status':'failed','code':'private unknown'},'failed','bnf_import_unconfirmed'),
])
def test_import_attribution_does_not_reclassify_manual_acquisition(connected_core,tmp_path,monkeypatch,result,status,reason):
    service,session,account,loan,state,downloaded=connected_core
    calls=[]
    def import_acsm(*args,**kwargs):
        calls.append((args,kwargs));return result
    monkeypatch.setattr(service,'import_acsm',import_acsm)
    source=tmp_path/'synthetic.acsm'
    assert service.import_bnf_loan_acsm(source,expected_identity_id='default',title='Fiction') is result
    assert calls==[((source,),{'identity_id':'default','provider_id':'manual','title':'Fiction'})]
    row,=events(service,tmp_path)
    assert row['detail']['action']=='import_acsm' and row['detail']['phase']=='adept_import'
    assert row['detail']['status']==status and row['detail']['reason_code']==reason
    assert not downloaded and not service.storage.entitlements()


def test_import_error_and_scope_refusal_are_distinct_without_calling_another_profile(connected_core,tmp_path,monkeypatch):
    service,*_=connected_core
    calls=[]
    def fail(*args,**kwargs):calls.append(1);raise RuntimeError('SECRET license private path')
    monkeypatch.setattr(service,'import_acsm',fail)
    with pytest.raises(BnfLoanActionError,match='^bnf_loan_scope_unproven$'):
        service.import_bnf_loan_acsm(tmp_path/'token.acsm',expected_identity_id='alex')
    assert not calls
    with pytest.raises(BnfLoanActionError,match='^bnf_import_failed$'):
        service.import_bnf_loan_acsm(tmp_path/'token.acsm',expected_identity_id='default')
    rows=events(service,tmp_path)
    assert {r['detail']['phase'] for r in rows}=={'admission','adept_import'}
    assert 'SECRET' not in json.dumps(rows)


def test_generic_diagnostic_cannot_attest_action_or_enter_domain(tmp_path):
    diagnostic=Diagnostics()
    fields=dict(schema_version=1,provider_id='bnf-pret',action='stage_loan_token',phase='provider_delivery',status='failed',reason_code='bnf_access_denied',duration_ms=10)
    diagnostic.add('LOAN','bnf_loan_action','failed',detail=fields)
    assert not events(NS(diagnostics=diagnostic),tmp_path)
    diagnostic.add_core_bnf_loan_action(fields)
    assert len(events(NS(diagnostics=diagnostic),tmp_path))==1
    for mutation in ({'reason_code':'SECRET'},{'status':'success'},{'task_ref':'profile-name'}, {'loan_id':'private'}, {'action':'borrow'}, {'provider_id':'paris'}):
        bad=dict(fields,**mutation)
        assert not public_fields(bad) and log_message(bad) is None


def test_persisted_core_and_app_log_projection_share_only_actual_task_reference(connected_core,tmp_path,caplog):
    service,session,account,loan,state,downloaded=connected_core
    caplog.set_level('INFO',logger='cifereca')
    with diagnostic_task_scope():
        service.stage_bnf_loan_acsm(loan,confirmed=True,expected_identity_id='default')
    core_row,=events(service,tmp_path)
    line=next(r.getMessage() for r in caplog.records if r.getMessage().startswith('EVENT bnf loan action '))
    log=tmp_path/'app.log'
    log.write_text('2026-10-25 07:00:00,000 INFO cifereca '+line+'\n')
    result=query_diagnostic_events(service.diagnostics,log_path=log,source='app_log',provider_id='bnf-pret')
    row,=result['events']
    assert row['business_module']=='LOAN' and row['status']=='success'
    assert row['detail']==core_row['detail']
    assert row['task_ref']==core_row['task_ref']
    log.write_text('2026-10-25 07:00:00,000 INFO cifereca.plugin '+line+'\n')
    assert not query_diagnostic_events(service.diagnostics,log_path=log,source='app_log',provider_id='bnf-pret')['events']


def test_bad_html_and_staging_storage_failure_have_no_success_or_adept(connected_core,tmp_path,monkeypatch):
    service,session,account,loan,state,downloaded=connected_core
    original=session.client.http._transport.handler
    def html(request):
        if request.url.path.startswith('/loans/'):
            return httpx.Response(200,content=b'<html>private login</html>',headers={'content-type':'text/html'})
        return original(request)
    session.client.http._transport.handler=html
    with pytest.raises(BnfLoanActionError,match='^bnf_invalid_loan_token$'):
        service.stage_bnf_loan_acsm(loan,confirmed=True,expected_identity_id='default')
    def storage(*args):raise OSError('private directory')
    monkeypatch.setattr(service,'_acquisition_work_dir',storage)
    with pytest.raises(BnfLoanActionError,match='^bnf_local_staging_failed$'):
        service.stage_bnf_loan_acsm(loan,confirmed=True,expected_identity_id='default')
    rows=events(service,tmp_path)
    assert {(r['detail']['phase'],r['detail']['reason_code']) for r in rows}=={
        ('provider_delivery','bnf_invalid_loan_token'),('local_staging','bnf_local_staging_failed')}
    assert not service.storage.acquisitions()


from tests.test_acsm_import_preservation import importer


def test_real_import_orchestration_stays_manual_and_preserves_original(importer,tmp_path):
    service,requests=importer
    source=tmp_path/'chosen.acsm'
    source.write_bytes(b'<fulfillmentToken xmlns="http://ns.adobe.com/adept" fulfillmentType="loan"><format>application/pdf</format></fulfillmentToken>')
    before=source.read_bytes()
    identity=service.current_identity.identity_id
    result=service.import_bnf_loan_acsm(source,expected_identity_id=identity,title='Synthetic')
    assert result['status']=='complete' and Path(result['path']).is_file()
    assert source.read_bytes()==before
    assert len(requests)==1 and requests[0]['source']['provider']=='manual'
    assert all(row['provider_id']=='manual' for row in service.storage.acquisitions())
    assert not any(row['provider_id']=='bnf-pret' for row in service.storage.entitlements())
    row,=events(service,tmp_path)
    assert row['detail']['action']=='import_acsm' and row['detail']['reason_code']=='complete'
