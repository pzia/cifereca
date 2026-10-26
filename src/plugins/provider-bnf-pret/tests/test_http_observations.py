"""Actual HTTPX reads and canonical journal, with synthetic responses only."""
import json
import logging

import httpx
import pytest

from bnf_token_fixture import connected, TOKEN
from cifereca.plugin_api import ProviderError
from cifereca_bnf_pret_http import ACTIVITY, ReaderHTTPClient, ReaderHTTPError


def observations(caplog):
    return [json.loads(record.getMessage().removeprefix('DIAG_JSON '))
            for record in caplog.records if record.getMessage().startswith('DIAG_JSON ')
            and 'logical_endpoint' in record.getMessage()]


@pytest.mark.parametrize('endpoint', ['activity', 'loan_token'])
@pytest.mark.parametrize('status', [400, 401, 403, 404, 405, 429, 500, 502])
def test_failed_http_keeps_exact_status_and_endpoint_without_retry(endpoint, status, caplog):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={'content-type': 'text/html; charset=UTF-8'},
                              content=b'synthetic response body')
    client = ReaderHTTPClient(transport=httpx.MockTransport(handler))
    with client.http, caplog.at_level(logging.INFO, logger='cifereca'):
        with pytest.raises(ReaderHTTPError):
            client.activity() if endpoint == 'activity' else client.loan_token('synthetic-loan')
    rows = observations(caplog)
    assert len(calls) == 1
    assert [row['detail']['event'] for row in rows] == ['begin', 'response', 'terminal']
    assert len({row['detail']['request_ref'] for row in rows}) == 1
    assert 'http_status' not in rows[0]['detail']
    assert rows[-1]['detail']['http_status'] == status
    assert rows[-1]['detail']['content_type'] == 'text/html; charset=UTF-8'
    assert rows[-1]['detail']['logical_endpoint'] == endpoint
    assert rows[-1]['status'] == 'failed'
    assert rows[-1]['duration_ms'] >= 0
    assert rows[-1]['detail']['phase'] == ('activity_recheck' if endpoint == 'activity' else 'loan_token_get')


@pytest.mark.parametrize('activity_status', [200, 404])
def test_real_provider_get_order_and_canonical_projection(tmp_path, caplog, activity_status):
    from cifereca.core.diagnostic_journal import query_journal
    session, account, loan, _, _, downloads = connected(status=405)
    original = session.client.http._transport.handler
    def handler(request):
        if str(request.url) == ACTIVITY and activity_status != 200:
            return httpx.Response(activity_status, text='synthetic unavailable')
        return original(request)
    session.client.http._transport.handler = handler
    target = tmp_path / 'journal.log'
    file_handler = logging.FileHandler(target)
    file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s'))
    logger = logging.getLogger('cifereca')
    logger.addHandler(file_handler)
    try:
        with caplog.at_level(logging.INFO, logger='cifereca'), pytest.raises(ProviderError):
            session.download_digital(loan, account, tmp_path / 'stage', explicit_user_action=True)
        rows = observations(caplog)
        terminals = [r for r in rows if r['detail']['event'] == 'terminal']
        assert [r['detail']['logical_endpoint'] for r in terminals] == (
            ['activity', 'loan_token'] if activity_status == 200 else ['activity'])
        assert len(downloads) == (1 if activity_status == 200 else 0)
        assert not list(tmp_path.rglob('*.acsm'))
        file_handler.flush()
        # The actual consumer must retain the typed status, phase and provider.
        result = query_journal(log_path=target, provider_id='bnf-pret')
        events = result['events']
        failed = [e for e in events if e['status'] == 'failed']
        assert failed[-1]['detail']['http_status'] == (405 if activity_status == 200 else 404)
        assert failed[-1]['provider_id'] == 'bnf-pret'
    finally:
        logger.removeHandler(file_handler)
        file_handler.close()
        session.close()


@pytest.mark.parametrize('after_headers', [False, True])
def test_transport_failure_does_not_invent_response_or_success(after_headers, caplog):
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield b'<partial'
            raise httpx.ReadTimeout('synthetic timeout')
    def handler(request):
        if after_headers:
            return httpx.Response(200, stream=Broken())
        raise httpx.ConnectTimeout('synthetic timeout')
    client = ReaderHTTPClient(transport=httpx.MockTransport(handler))
    try:
        with caplog.at_level(logging.INFO, logger='cifereca'), pytest.raises(ReaderHTTPError, match='transport_unavailable'):
            client.loan_token('synthetic-loan')
        row = observations(caplog)[-1]
        assert row['status'] == 'failed'
        assert row['detail']['code'] == 'transport_unavailable'
        assert ('http_status' in row['detail']) is after_headers
    finally:
        client.close()


@pytest.mark.parametrize('status,content,headers,code', [
    (302, b'', {'location': ACTIVITY}, 'download_redirect_refused'),
    (200, b'<html>login</html>', {'content-type': 'text/html'}, 'invalid_loan_token'),
    (200, b'', {}, 'invalid_loan_token'),
    (200, TOKEN, {}, 'http_read_completed'),
])
def test_terminal_read_result_is_separate_from_fulfillment(status, content, headers, code, caplog):
    client = ReaderHTTPClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(status, content=content, headers=headers)))
    try:
        with caplog.at_level(logging.INFO, logger='cifereca'):
            if code == 'http_read_completed':
                assert client.loan_token('synthetic-loan') == TOKEN
            else:
                with pytest.raises(ReaderHTTPError, match=code):
                    client.loan_token('synthetic-loan')
        terminal = observations(caplog)[-1]
        assert terminal['detail']['code'] == code
        assert terminal['detail']['http_status'] == status
    finally:
        client.close()


def test_failing_optional_sink_cannot_change_http_result():
    class BrokenDiagnostics:
        def add(self, *args, **kwargs):
            raise RuntimeError('diagnostics unavailable')
    client = ReaderHTTPClient(diagnostics=BrokenDiagnostics(), transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=TOKEN)))
    try:
        assert client.loan_token('synthetic-loan') == TOKEN
    finally:
        client.close()


def test_supplied_diagnostics_uses_one_canonical_occurrence(caplog):
    from cifereca.diagnostics import Diagnostics
    diagnostics = Diagnostics()
    client = ReaderHTTPClient(diagnostics=diagnostics, transport=httpx.MockTransport(
        lambda r: httpx.Response(404, headers={'content-type': 'application/xml'})))
    try:
        with caplog.at_level(logging.INFO, logger='cifereca'), pytest.raises(ReaderHTTPError):
            client.loan_token('synthetic-loan')
        assert len(observations(caplog)) == 3
        assert observations(caplog)[-1]['detail']['http_status'] == 404
    finally:
        client.close()


def test_authentication_has_no_new_http_observations(caplog):
    from test_http_provider import HTTPProviderTests
    session, _, _ = HTTPProviderTests().make()
    try:
        with caplog.at_level(logging.INFO, logger='cifereca'):
            session.connect()
        assert observations(caplog) == []
    finally:
        session.close()
