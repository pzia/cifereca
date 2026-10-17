"""Public Diagnostics boundary: useful Numilog causes without private payloads."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.brokers import DiagnosticsBrokerImpl
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics


def feed(tmp_path, rows):
    return query_diagnostic_events(SimpleNamespace(snapshot=lambda: rows),
                                   source='core_events', log_path=tmp_path/'absent')


def row(detail, operation='acquisition_delivery', **kw):
    return dict(timestamp='2026-10-16T08:00:00+00:00', category='NUMILOG',
                operation=operation, status='error', detail=detail, **kw)


def test_real_delivery_producer_to_public_broker_keeps_cause_correlation_and_bounds(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'src/plugins/provider-numilog'))
    from cifereca_numilog_delivery_trace import DeliveryTrace
    diag = Diagnostics(level='detailed')
    service = SimpleNamespace(diagnostics=diag)
    monkeypatch.setattr('cifereca.paths.log_file', lambda: tmp_path/'absent')
    # Real producer and collector, no HTTP/provider/acquisition call.
    with DeliveryTrace(diag, source='owned_card') as trace:
        trace.event('delivery_http', 'redirect', status=302,
                    destination='https://www.numilog.com/readBook.ashx?auth=PRIVATE')
        trace.event('html_selection', 'html_delivery_link_missing',
                    mime='text/html; PRIVATE', html_kind='intermediate', candidates=0, hops=2)
        trace.fail('html_delivery_link_missing')
    events = DiagnosticsBrokerImpl(service).events(source='core_events')['events']
    assert len(events) == 4
    assert {e['module'] for e in events} == {'NUMILOG'}
    assert len({e['detail']['action'] for e in events}) == 1
    terminal = next(e for e in events if e['detail']['phase'] == 'completed')
    assert terminal['severity'] == 'error'
    assert terminal['detail']['code'] == 'html_delivery_link_missing'
    assert terminal['detail']['hops'] == 2
    assert 0 <= terminal['detail']['duration_ms'] <= 600000
    assert 'phase=completed' in terminal['message']
    assert 'code=html_delivery_link_missing' in terminal['message']
    assert 'action=' in terminal['message']
    assert 'PRIVATE' not in json.dumps(events)
    assert len({e['event_id'] for e in events}) == 4


@pytest.mark.parametrize('code', ['http_forbidden', 'http_rate_limited',
    'session_expired_confirmed', 'login_form_detected', 'html_delivery_ambiguous', 'adept_attention_required',
    'acsm_complete', 'epub_complete'])
def test_public_outcomes_do_not_collapse_to_generic_acquisition(tmp_path, code):
    event = feed(tmp_path, [row({'action':'012345abcdef', 'phase':'completed', 'code':code})])['events'][0]
    assert f'code={code}' in event['message']
    assert event['detail']['code'] == code


@pytest.mark.parametrize('key,value', [
    ('action','012345abcdef?auth=PRIVATE'), ('action','012345abcde\n'),
    ('source','https://private/?auth=PRIVATE'), ('phase','PRIVATE'), ('code','PRIVATE'),
    ('origin_kind','private.example'), ('path_kind','/home/PRIVATE'),
    ('mime','text/html;PRIVATE'), ('html_kind','<PRIVATE>'),
    ('http_status', True), ('http_status', 600), ('http_status', '403'),
    ('candidates', -1), ('candidates', 10001), ('hops', 13), ('hops', False),
    ('duration_ms', 600001), ('duration_ms', 1.2),
    ('reason','PRIVATE'), ('connected', 'True'),
    ('connected_initial', 'True'), ('connected_initial', 1),
    ('connected_initial', {}),
])
def test_unknown_values_never_cross_public_boundary(tmp_path, key, value):
    operation = 'session_state' if key in {'reason','connected'} else 'acquisition_delivery'
    detail = {key:value, 'url':'https://private/?auth=PRIVATE', 'title':'PRIVATE',
              'cookie':'PRIVATE', 'orderid':'PRIVATE', 'html':'<PRIVATE>'}
    event = feed(tmp_path, [row(detail, operation, duration_ms=value if key=='duration_ms' else None)])['events'][0]
    assert key not in event.get('detail', {})
    assert 'PRIVATE' not in json.dumps(event)


def test_untrusted_objects_are_not_stringified_or_coerced(tmp_path):
    class Private:
        def __str__(self): raise AssertionError('must not stringify private objects')
        def __bool__(self): raise AssertionError('must not coerce private objects')
    events = feed(tmp_path, [row(dict(action=Private(), phase=[], code={}, http_status=Private())),
        row(dict(reason=Private(), connected=Private()), 'session_state'),
        row(dict(action='012345abcdef', code='http_forbidden'), 'private search account')])['events']
    assert all('detail' not in event for event in events)
    assert any(event['message'] == '[opération masquée]' for event in events)


def test_session_projection_retains_observation_without_inventing_failure_status(tmp_path):
    event = feed(tmp_path, [dict(row({'reason':'library_redirect', 'connected':False},
                                    'session_state'), status='ok')])['events'][0]
    assert event['detail'] == {'reason':'library_redirect', 'connected':False}
    assert event['status'] == 'ok'  # producer observation, no auth-cause inference
    assert 'reason=library_redirect connected=False' in event['message']


def test_exact_session_log_survives_disabled_collector_without_free_text(tmp_path):
    log = tmp_path/'cifereca.log'
    bodies = [
        ('cifereca_numilog', 'TRACE numilog session connected=True reason=connect_succeeded'),
        ('cifereca_numilog', 'TRACE numilog session connected=False reason=library_redirect'),
        ('cifereca_numilog', 'TRACE numilog session connected=False reason=PRIVATE'),
        ('cifereca_numilog', 'TRACE numilog session connected=False reason=library_redirect auth=PRIVATE'),
        ('cifereca_numilog', 'PRIVATE'),
        ('cifereca_numilog.PRIVATE', 'TRACE numilog session connected=False reason=library_redirect'),
        ('external', 'TRACE numilog session connected=False reason=library_redirect'),
    ]
    log.write_text('\n'.join(f'2026-10-16 08:00:00,{i:03} WARNING {logger} [MainThread] {body}'
                             for i,(logger,body) in enumerate(bodies))+'\n')
    result = query_diagnostic_events(Diagnostics(enabled=False), log_path=log)
    assert result['status'] == 'partial'
    assert result['total'] == 5
    assert {e['module'] for e in result['events']} == {'cifereca_numilog'}
    messages = [e['message'] for e in result['events']]
    assert bodies[0][1] in messages and bodies[1][1] in messages
    assert sum(m.startswith('[contenu libre masqué') for m in messages) == 3
    assert 'PRIVATE' not in json.dumps(result)


def test_all_bounded_delivery_fields_fit_existing_message_export(tmp_path):
    detail = dict(action='012345abcdef', source='owned_card', phase='artifact_validation',
        code='session_expired_confirmed', mime='acsm_xml', html_kind='intermediate',
        origin_kind='acsm_fulfillment', path_kind='personal_library',
        http_status=599, candidates=10000, hops=12, connected_initial=True)
    event = feed(tmp_path, [row(detail, duration_ms=600000)])['events'][0]
    assert event['detail'] == dict(detail, duration_ms=600000)
    assert len(event['message']) <= 500
    for key,value in event['detail'].items():
        assert f'{key}={value}' in event['message']
    # Module filtering must find the newly recognized source.
    result = query_diagnostic_events(SimpleNamespace(snapshot=lambda:[row(detail)]),
        log_path=tmp_path/'absent', source='core_events', module='NUMILOG')
    assert result['total'] == 1


@pytest.mark.parametrize('connected', [True, False])
def test_login_form_evidence_preserves_initial_state_without_expiry_inference(tmp_path, connected):
    events = feed(tmp_path, [
        row(dict(action='012345abcdef', phase='start', code='started', connected_initial=connected)),
        row(dict(action='012345abcdef', phase='html_selection', code='login_form_detected',
                 connected_initial=connected, html_kind='login', hops=2)),
        row(dict(reason='delivery_login_form_detected', connected=False), 'session_state'),
        row(dict(action='012345abcdef', phase='completed', code='login_form_detected',
                 connected_initial=connected, hops=2), duration_ms=792),
    ])['events']
    delivery = [e for e in events if 'action' in e.get('detail', {})]
    assert len(delivery) == 3
    assert all(e['detail']['connected_initial'] is connected for e in delivery)
    assert all(f'connected_initial={connected}' in e['message'] for e in delivery)
    assert len({e['detail']['action'] for e in delivery}) == 1
    terminal = next(e for e in delivery if e['detail']['phase'] == 'completed')
    assert terminal['detail']['code'] == 'login_form_detected'
    assert terminal['detail']['hops'] == 2 and terminal['detail']['duration_ms'] == 792
    suspension = next(e for e in events if 'reason' in e.get('detail', {}))
    assert suspension['detail'] == dict(reason='delivery_login_form_detected', connected=False)
    assert 'session_expired_confirmed' not in json.dumps(events)


def test_delivery_suspension_fixed_log_survives_without_collector(tmp_path):
    path = tmp_path/'application.log'
    path.write_text('2026-10-17 19:21:03,410 WARNING cifereca_numilog [MainThread] '
                    'TRACE numilog session connected=False reason=delivery_login_form_detected\n')
    events = query_diagnostic_events(SimpleNamespace(snapshot=lambda: []),
                                     source='app_log', log_path=path)['events']
    assert len(events) == 1
    assert 'reason=delivery_login_form_detected' in events[0]['message']
