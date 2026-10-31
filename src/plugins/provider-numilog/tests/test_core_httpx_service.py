"""Numilog public and account clients use one Core service; no real HTTP."""
import json

import httpx
import pytest

import cifereca_numilog as account
import cifereca_numilog_public_client as public
from cifereca.http_trace import BROWSER_USER_AGENT, HTTPTraceCapture
import cifereca.http_trace as trace_module
from cifereca.logging_utils import diagnostics_level, set_diagnostics_level


@pytest.mark.parametrize('level', ['normal', 'trace'])
def test_public_detail_service_preserves_anonymity_and_result(monkeypatch, level):
    capture=HTTPTraceCapture()
    monkeypatch.setattr(trace_module,'HTTP_TRACE',capture)
    previous=diagnostics_level()
    url='https://www.numilog.com/1958476/jefferson-prend-des-risques.ebook'
    content=('<script type="application/ld+json">'+json.dumps({
        '@type':'Book','url':url,'name':'Fixture'})+'</script>').encode()
    original=public.HTTPX_TRANSPORT.client
    constructions,requests=[],[]

    def serve(request):
        requests.append(request)
        assert request.headers['user-agent'] == BROWSER_USER_AGENT
        assert 'cookie' not in request.headers and 'authorization' not in request.headers
        return httpx.Response(200,headers={'Content-Type':'text/html'},stream=httpx.ByteStream(content))

    def construct(**kwargs):
        constructions.append(kwargs.copy())
        return original(**kwargs,transport=httpx.MockTransport(serve))

    monkeypatch.setattr(public.HTTPX_TRANSPORT,'client',construct)
    set_diagnostics_level(level)
    try:
        assert public.fetch_public_detail(url)
        assert len(requests)==len(constructions)==1
        assert constructions[0]['producer']=='provider.numilog.public'
        assert constructions[0]['trust_env'] is False
        assert constructions[0]['follow_redirects'] is False
        entries=capture.snapshot()['exchanges']
        assert len(entries)==(1 if level=='trace' else 0)
        if entries:
            assert entries[0]['response_body']['data']==content
            assert entries[0]['response_body']['state']=='complete'
    finally:
        set_diagnostics_level(previous)


def test_account_client_follows_trace_switch_without_reconnect(monkeypatch):
    capture=HTTPTraceCapture()
    monkeypatch.setattr(trace_module,'HTTP_TRACE',capture)
    original=account.HTTPX_TRANSPORT.client
    previous=diagnostics_level()
    constructions,requests=[],[]

    def serve(request):
        requests.append(request)
        assert request.headers['user-agent'] == BROWSER_USER_AGENT
        return httpx.Response(200,stream=httpx.ByteStream(b'private synthetic bytes'))

    def construct(**kwargs):
        constructions.append(kwargs.copy())
        return original(**kwargs,transport=httpx.MockTransport(serve))

    monkeypatch.setattr(account.HTTPX_TRANSPORT,'client',construct)
    set_diagnostics_level('normal')
    session=account.NumilogSession(credential_id='synthetic',label='',credentials={},
                                   options={},config={},diagnostics=None)
    try:
        for level in ('normal','trace','normal'):
            set_diagnostics_level(level)
            assert session.client.get('https://www.numilog.com/synthetic').content==b'private synthetic bytes'
        assert len(constructions)==1 and len(requests)==3
        assert constructions[0]['producer']=='provider.numilog'
        assert constructions[0]['trust_env'] is False and constructions[0]['follow_redirects'] is False
        assert len(capture.snapshot()['exchanges'])==1
    finally:
        session.close()
        set_diagnostics_level(previous)
