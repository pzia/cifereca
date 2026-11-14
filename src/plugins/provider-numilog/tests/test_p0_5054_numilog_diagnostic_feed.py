"""Provider Numilog diagnostics integration: bounded public events, no private payloads."""
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


def test_real_delivery_producer_to_canonical_journal_keeps_cause_correlation_and_bounds(
    tmp_path, monkeypatch,
):
    import logging

    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1])
    )
    from cifereca_numilog_delivery_trace import DeliveryTrace
    from cifereca.logging_utils import CanonicalEventFormatter

    path = tmp_path / 'cifereca.log'
    monkeypatch.setattr('cifereca.paths.log_file', lambda: path)
    diag = Diagnostics(level='detailed')
    logger = logging.getLogger('cifereca')
    previous = logger.level
    handler = logging.FileHandler(path, encoding='utf-8')
    handler.setFormatter(CanonicalEventFormatter(
        '%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with DeliveryTrace(diag, source='owned_card') as trace:
            trace.event(
                'delivery_http', 'redirect', status=302,
                destination='https://www.numilog.com/readBook.ashx?auth=PRIVATE',
            )
            trace.event(
                'html_selection', 'html_delivery_link_missing',
                mime='text/html; PRIVATE', html_kind='intermediate',
                candidates=0, hops=2,
            )
            trace.fail('html_delivery_link_missing')
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        handler.close()

    class ForbiddenMemory:
        def snapshot(self):
            raise AssertionError('physical broker must never access memory')

    broker = DiagnosticsBrokerImpl(SimpleNamespace(diagnostics=ForbiddenMemory()))
    events = broker.events(source='core_events')['events']
    assert len(events) == 4
    assert {event['source'] for event in events} == {'journal'}
    assert {event['module'] for event in events} == {'NUMILOG'}
    assert len({event['detail']['action'] for event in events}) == 1
    terminal = next(event for event in events if event['detail']['phase'] == 'completed')
    assert terminal['severity'] == 'error'
    assert terminal['detail']['code'] == 'html_delivery_link_missing'
    assert terminal['detail']['hops'] == 2
    assert 0 <= terminal['duration_ms'] <= 600000
    assert terminal['operation'] == terminal['message'] == 'acquisition_delivery'
    assert 'PRIVATE' not in json.dumps(events)  # The producer emits typed classes.
    assert len({event['event_id'] for event in events}) == 4
    assert path.read_text(encoding='utf-8').count('DIAG_JSON ') == 4


@pytest.mark.parametrize('code', [
    'http_forbidden', 'http_rate_limited', 'session_expired_confirmed',
    'login_form_detected', 'html_delivery_ambiguous',
    'adept_attention_required', 'acsm_complete', 'epub_complete',
])
def test_delivery_codes_are_produced_by_plugin_not_core(tmp_path, monkeypatch, code):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from cifereca_numilog_delivery_trace import DeliveryTrace

    diagnostics = Diagnostics(level='detailed')
    with DeliveryTrace(diagnostics, source='owned_card') as trace:
        trace.event('delivery_http', code, status=403)
        trace.fail(code)

    records = diagnostics.snapshot()
    assert [entry['detail']['code'] for entry in records] == [
        'started', code, code,
    ]
    assert records[-1]['status'] == 'error'
    assert all(entry['detail']['source'] == 'owned_card' for entry in records)
    # A plugin-supplied diagnostic record does not attest a Core acquisition.
    legacy = feed(tmp_path, [row({'action': '012345abcdef',
                                  'phase': 'completed', 'code': code})])
    assert 'detail' not in legacy['events'][0]
    assert 'provider_id' not in legacy['events'][0]
    assert legacy['events'][0]['message'] == '[opération masquée]'


def test_provider_rejects_untrusted_http_values_and_never_emits_raw_url(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from cifereca_numilog_delivery_trace import DeliveryTrace
    diagnostics = Diagnostics(level='detailed')
    secret = 'https://private.invalid/?token=PRIVATE'
    with DeliveryTrace(diagnostics, source='owned_card') as trace:
        trace.event('html_selection', 'redirect', status=True,
                    mime='text/html;PRIVATE', html_kind=secret,
                    hops=13, candidates=-1, destination=secret)
        trace.fail('http_forbidden')
    event = diagnostics.snapshot()[1]['detail']
    assert 'http_status' not in event
    assert 'hops' not in event
    assert 'candidates' not in event
    assert event['mime'] == 'html'
    assert event['html_kind'] == 'unknown'
    assert event['origin_kind'] == 'other'
    assert event['path_kind'] == 'other'
    assert 'PRIVATE' not in json.dumps(diagnostics.snapshot())


def test_plugin_confirmation_shape_is_validated_at_source(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from cifereca_numilog_delivery_trace import DeliveryTrace
    diagnostics = Diagnostics(level='detailed')
    url = ('https://www.numilog.com/Pages/Commande/'
           'ConfirmationPaiement.aspx?ref=PRIVATE&disp=simple')
    with DeliveryTrace(diagnostics, source='readbook') as trace:
        trace.event('delivery_http', 'redirect', status=302,
                    destination=url, redirect_target=url,
                    cookie_header_present=True)
    event = diagnostics.snapshot()[1]['detail']
    assert event['path_kind'] == 'confirmation_page'
    assert event['confirmation_ref_present'] is True
    assert event['confirmation_disp_simple'] is True
    assert event['redirect_target_kind'] == 'confirmation_page'
    assert event['redirect_confirmation_ref_present'] is True
    assert event['redirect_confirmation_disp_simple'] is True
    assert event['cookie_header_present'] is True
    assert 'PRIVATE' not in json.dumps(diagnostics.snapshot())


@pytest.mark.parametrize('operation', [
    'session_state', 'acquisition_delivery', 'startup_materialization',
])
def test_retired_numilog_synthetic_core_feed_does_not_infer_provider(
    tmp_path, operation,
):
    payload = feed(tmp_path, [row({
        'action': '012345abcdef', 'code': 'epub_complete',
        'url': 'https://private.invalid/?token=PRIVATE',
        'account_id': 'PRIVATE',
    }, operation=operation)])
    event, = payload['events']
    assert 'detail' not in event
    assert 'provider_id' not in event
    assert 'PRIVATE' not in json.dumps(payload)


def test_untrusted_objects_are_not_stringified_or_coerced(tmp_path):
    class Private:
        def __str__(self):
            raise AssertionError('must not stringify private objects')
        def __bool__(self):
            raise AssertionError('must not coerce private objects')
    events = feed(tmp_path, [
        row(dict(action=Private(), phase=[], code={},
                 http_status=Private())),
        row(dict(reason=Private(), connected=Private()), 'session_state'),
    ])['events']
    assert len(events) == 2
    assert all('detail' not in event for event in events)
    assert all(event['message'] == '[opération masquée]' for event in events)


def test_unverified_numilog_session_log_is_not_a_core_provider_fact(tmp_path):
    log = tmp_path / 'cifereca.log'
    log.write_text(
        '2026-10-16 08:00:00,000 WARNING cifereca_numilog '
        '[MainThread] TRACE numilog session connected=False '
        'reason=delivery_login_form_detected\n',
        encoding='utf-8',
    )
    events = query_diagnostic_events(
        Diagnostics(enabled=False), source='app_log', log_path=log,
    )['events']
    # The retired supplier-specific logger remains in the raw physical
    # journal but is no longer projected into the Core public event feed.
    assert events == []
