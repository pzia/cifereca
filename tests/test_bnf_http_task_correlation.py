"""BnF HTTP facts share the actual Core worker, never a time-based guess."""
import json
import logging
import threading

import httpx
import pytest

from cifereca.core.diagnostic_bnf_loan_action import BnfLoanActionError
from cifereca.core.diagnostic_journal import query_journal
from cifereca.diagnostic_task_context import diagnostic_task_reference
from cifereca.diagnostics import Diagnostics
from cifereca.logging_utils import CanonicalEventFormatter
from cifereca.task_queue import PriorityExecutor
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core


def emit(diag, *, provider='bnf-pret'):
    diag.add('API', 'BnF HTTP', 'failed', detail={
        'provider_id': provider, 'logical_endpoint': 'loan_token',
        'phase': 'loan_token_get', 'http_status': 405,
        'request_ref': 'synthetic-http-request',
        'task_ref': 'plugin-supplied-value',
    })


@pytest.mark.parametrize('failure_phase,status', [
    ('activity', 404), ('loan_token', 405), ('loan_token', 502),
])
def test_real_core_http_and_terminal_share_worker_reference(
    connected_core, tmp_path, failure_phase, status,
):
    service, session, account, loan, state, downloaded = connected_core
    session.client.diagnostics = service.diagnostics
    original = session.client.http._transport.handler
    def handler(request):
        is_activity = request.url.path == '/my_profile/activity'
        if (failure_phase == 'activity' and is_activity
                or failure_phase == 'loan_token' and request.url.path.startswith('/loans/')):
            return httpx.Response(status, content=b'synthetic failure')
        return original(request)
    session.client.http._transport.handler = handler
    path = tmp_path / 'journal.log'
    logger = logging.getLogger('cifereca')
    sink = logging.FileHandler(path)
    sink.setFormatter(CanonicalEventFormatter('%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s'))
    logger.addHandler(sink)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    pool = PriorityExecutor(max_workers=1)
    try:
        future = pool.submit(0, lambda: service.stage_bnf_loan_acsm(
            loan, confirmed=True, expected_identity_id='default'))
        with pytest.raises(BnfLoanActionError):
            future.result(timeout=5)
        sink.flush()
        rows = query_journal(path, provider_id='bnf-pret', limit=100)['events']
        http = [r for r in rows if r.get('operation') == 'BnF HTTP']
        terminal = [r for r in rows if r.get('operation') == 'bnf_loan_action']
        assert http and len(terminal) == 1
        ref = terminal[0]['task_ref']
        assert all(row['task_ref'] == ref for row in http)
        failed = [row for row in http if row['status'] == 'failed']
        assert len(failed) == 1
        assert failed[0]['detail']['http_status'] == status
        assert failed[0]['detail']['logical_endpoint'] == failure_phase
        if failure_phase == 'activity':
            assert all(r['detail']['logical_endpoint'] == 'activity' for r in http)
        assert not service.storage.entitlements() and not service.storage.acquisitions()
        assert not list(tmp_path.rglob('*.acsm'))
    finally:
        pool.shutdown(wait=True)
        logger.removeHandler(sink)
        logger.setLevel(old_level)
        sink.close()


def test_http_outside_worker_does_not_invent_reference():
    diag = Diagnostics()
    emit(diag)
    assert 'task_ref' not in diag.snapshot()[0]
    assert diagnostic_task_reference() is None


def test_interleaved_workers_and_reused_thread_keep_distinct_references():
    diag = Diagnostics()
    pool = PriorityExecutor(max_workers=2)
    barrier = threading.Barrier(2)
    def operation():
        emit(diag)
        ref = diagnostic_task_reference()
        barrier.wait(timeout=3)
        emit(diag)
        return ref
    try:
        a = pool.submit(0, operation)
        b = pool.submit(0, operation)
        refs = {a.result(timeout=5), b.result(timeout=5)}
        assert len(refs) == 2
        c = pool.submit(0, lambda: emit(diag))
        c.result(timeout=5)
        rows = diag.snapshot()
        assert all(sum(r.get('task_ref') == ref for r in rows[:4]) == 2 for ref in refs)
        assert rows[-1]['task_ref'] not in refs
        assert all(r['task_ref'] != 'plugin-supplied-value' for r in rows)
        emit(diag)
        assert 'task_ref' not in diag.snapshot()[-1]
    finally:
        pool.shutdown(wait=True)


def test_unrelated_event_is_not_promoted_to_bnf_worker_observation():
    diag = Diagnostics()
    pool = PriorityExecutor(max_workers=1)
    try:
        pool.submit(0, lambda: emit(diag, provider='paris')).result(timeout=5)
        assert 'task_ref' not in diag.snapshot()[0]
    finally:
        pool.shutdown(wait=True)


def test_errors_only_retains_http_failure_with_actual_worker(caplog):
    diag = Diagnostics(level='errors')
    pool = PriorityExecutor(max_workers=1)
    try:
        with caplog.at_level(logging.INFO, logger='cifereca'):
            pool.submit(0, lambda: emit(diag)).result(timeout=5)
        row, = diag.snapshot()
        payload, = [json.loads(r.getMessage().removeprefix('DIAG_JSON '))
                    for r in caplog.records if r.getMessage().startswith('DIAG_JSON ')]
        assert payload['task_ref'] == row['task_ref']
        assert row['task_ref'] != row['detail']['task_ref']
    finally:
        pool.shutdown(wait=True)
