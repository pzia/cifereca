"""Public provider stage keeps exact payloads/caching without leaking provenance."""
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import logging
import re

import pytest

from cifereca.core import CiferecaCore
from cifereca.models import Account, Loan
from cifereca.core.loan_safe_trace import loan_trace_ref, trace_duration_ms
from cifereca.providers import ProviderConfigurationError

PRIVATE = 'PRIVATE_COORDINATE'
XML = '<fulfillmentToken xmlns="http://ns.adobe.com/adept"><resourceItemInfo><resource>urn:synthetic</resource></resourceItemInfo></fulfillmentToken>'


def setup(tmp_path, monkeypatch, filename):
    core = CiferecaCore.open(config_path=tmp_path/'config', database_path=tmp_path/'db')
    service = core.library
    core.config.set('library.directory',str(tmp_path/PRIVATE/'library'))
    service.storage.provider_connection_put(dict(identity_id='default',provider_id='paris',credential_id=PRIVATE))
    account = Account(display_name=PRIVATE,unique_id=PRIVATE,provider_id='paris',credential_id=PRIVATE,identity_id='default')
    service.accounts = [account]
    loan = Loan(id=PRIVATE,provider='PNB',medium='digital',record_id=PRIVATE+'edition',title=PRIVATE,
        document_type='Roman',thumbnail_url='https://private/?auth='+PRIVATE,source_provider='paris',
        source_credential=PRIVATE,source_account_id=PRIVATE,ebook_url='https://private/?token='+PRIVATE,
        due_at='2099-01-01T00:00:00Z')
    calls=[]
    def download(received, selected, target):
        assert received is loan and selected is account
        path=target/filename
        path.write_text(XML if filename.casefold().endswith('.acsm') else 'synthetic direct file')
        calls.append((received,selected,target,path))
        return path,loan.ebook_url
    session=NS(provider_id=PRIVATE, download_digital=Mock(side_effect=download))
    monkeypatch.setattr(service,'_session_for_source',lambda *_:session)
    monkeypatch.setattr(service.acquisition,'run_post_download',Mock())
    monkeypatch.setattr(service.acquisition,'mime_type_for',lambda *_:'application/test')
    return core,service,loan,session,calls


def assert_safe(service, loan, caplog):
    relevant=[r for r in caplog.records if 'TRACE download' in r.getMessage()]
    assert relevant
    assert PRIVATE not in '\n'.join(r.getMessage()+repr(r.args) for r in relevant)
    assert any('request_ref='+loan_trace_ref(loan,None) in r.getMessage() for r in relevant)
    ref=loan_trace_ref(loan,'default')
    assert any('loan_ref='+ref in r.getMessage() for r in relevant)
    events=[e for e in service.diagnostics.snapshot() if e['category']=='PNB']
    assert all(e['detail']['loan_ref']==ref for e in events)
    assert PRIVATE not in repr(events)
    assert all('file' not in e['detail'] and 'provider' not in e['detail'] for e in events)
    return relevant,events


@pytest.mark.parametrize('filename', [PRIVATE+'.ACSM',PRIVATE+'.pdf',PRIVATE+'.epub',PRIVATE+'.secret'])
def test_acquisition_keeps_private_payloads_and_paths_outside_diagnostics(tmp_path, monkeypatch, caplog, filename):
    core,service,loan,session,calls=setup(tmp_path,monkeypatch,filename)
    try:
        with caplog.at_level(logging.INFO): result=service.acquire_digital_loan(loan)
        assert result.loan_id==loan.id and result.account_id==PRIVATE and result.credential_id==PRIVATE
        assert result.url==loan.ebook_url and result.path.name==filename and result.path.is_file()
        assert result.metadata['cover_url']==loan.thumbnail_url
        service.acquisition.run_post_download.assert_called_once()
        assert session.download_digital.call_count==1
        logs,events=assert_safe(service,loan,caplog)
        last=next(r.getMessage() for r in logs if 'provider-download end' in r.getMessage())
        assert f'suffix={Path(filename).suffix.casefold() if not filename.endswith("secret") else "unknown"}' in last
        assert re.search(r'duration_ms=\d+',last)
        assert events[-1]['detail']['bytes']==result.path.stat().st_size
        if filename.endswith('ACSM'):
            assert result.path==calls[0][3] and 'acquisition-work' in str(result.path)
        else:
            assert result.path!=calls[0][3] and not calls[0][3].exists()
    finally: core.close()


def test_valid_acsm_cache_is_reused_once_but_force_refresh_still_downloads(tmp_path,monkeypatch,caplog):
    core,service,loan,session,calls=setup(tmp_path,monkeypatch,PRIVATE+'.acsm')
    try:
        with caplog.at_level(logging.INFO):
            a=service.acquire_digital_loan(loan)
            b=service.acquire_digital_loan(loan)
            assert a.path==b.path and session.download_digital.call_count==1
            assert service.acquisition.run_post_download.call_count==1
            service.acquire_digital_loan(loan,force_refresh=True)
        assert session.download_digital.call_count==2 and service.acquisition.run_post_download.call_count==2
        logs,events=assert_safe(service,loan,caplog)
        assert len([e for e in events if e['operation']=='reuse artifact'])==1
    finally: core.close()


@pytest.mark.parametrize('stage',['download','hook'])
def test_failures_are_propagated_without_caching_or_publishing(tmp_path,monkeypatch,caplog,stage):
    core,service,loan,session,calls=setup(tmp_path,monkeypatch,PRIVATE+'.acsm')
    error=ProviderConfigurationError(PRIVATE)
    try:
        if stage=='download':session.download_digital.side_effect=error
        else:service.acquisition.run_post_download.side_effect=error
        with caplog.at_level(logging.INFO),pytest.raises(ProviderConfigurationError) as caught:
            service.acquire_digital_loan(loan)
        assert caught.value is error
        logs,events=assert_safe(service,loan,caplog)
        assert events==[]
        if stage=='download':assert not calls
        else:
            from cifereca import acsm_cache
            assert not (calls[0][2]/acsm_cache.MANIFEST).exists()
    finally:core.close()


@pytest.mark.parametrize('value,expected',[(0,0),(1.25,1250),(86400,86400000),(-1,None),
    (86400.1,None),(float('nan'),None),(float('inf'),None),(True,None),('1',None),({},None)])
def test_trace_duration_is_finite_bounded_and_never_coerced(value,expected):
    assert trace_duration_ms(value)==expected
