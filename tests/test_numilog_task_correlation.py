"""Real worker/collector/feed correlation, isolated across concurrent tasks."""
import json
import logging
import re
import threading
from types import SimpleNamespace

import pytest

from cifereca.diagnostics import Diagnostics
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.task_queue import PriorityExecutor
from cifereca.task_runtime import TaskBrokerImpl


def emit(diag, action='abcdef012345'):
    diag.add('NUMILOG', 'acquisition_delivery', 'error', detail={
        'action': action, 'phase': 'completed', 'code': 'html_delivery_link_missing',
        'url': 'https://private/?auth=PRIVATE',
    })


def test_failed_real_broker_task_links_provider_event_to_public_worker_outcome(tmp_path):
    diag = Diagnostics()
    path = tmp_path/'cifereca.log'
    handler = logging.FileHandler(path)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s'))
    logger = logging.getLogger('cifereca')
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    broker = TaskBrokerImpl(SimpleNamespace(), max_workers=1)
    try:
        def operation(_control):
            emit(diag)
            raise RuntimeError('PRIVATE')
        handle = broker.for_plugin('PRIVATE').submit(operation, label='PRIVATE', kind='PRIVATE')
        with pytest.raises(RuntimeError):
            handle.result(timeout=2)
        handler.flush()
        payload = query_diagnostic_events(diag, log_path=path)
        delivery = next(e for e in payload['events'] if e['module']=='NUMILOG')
        worker = next(e for e in payload['events'] if 'task correlation' in e['message'])
        ref = delivery['detail']['task_ref']
        assert re.fullmatch('[0-9a-f]{12}', ref)
        assert worker['task_ref'] == ref
        assert f'task_ref={ref} status=failed' in worker['message']
        assert f'task_ref={ref}' in delivery['message']
        assert f'task_ref={ref}' in diag.text('test')
        assert 'PRIVATE' not in json.dumps(payload)
        assert "PRIVATE" in path.read_text()  # raw journal is intentionally unmasked
    finally:
        broker.close()
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()


def test_concurrent_and_reused_workers_never_share_a_task_reference(caplog):
    diag = Diagnostics()
    barrier = threading.Barrier(2)
    pool = PriorityExecutor(max_workers=2)
    def operation(action):
        emit(diag, action)
        barrier.wait(timeout=2)
        emit(diag, action)
    try:
        with caplog.at_level(logging.INFO, logger='cifereca'):
            a = pool.submit(0, lambda: operation('aaaaaaaaaaaa'))
            b = pool.submit(0, lambda: operation('bbbbbbbbbbbb'))
            a.result(timeout=3); b.result(timeout=3)
            pool.submit(0, lambda: emit(diag, 'cccccccccccc')).result(timeout=2)
        rows = diag.snapshot()
        refs = {action:{r['task_ref'] for r in rows if r['detail']['action']==action}
                for action in ('aaaaaaaaaaaa','bbbbbbbbbbbb','cccccccccccc')}
        assert all(len(value)==1 for value in refs.values())
        assert len(set.union(*refs.values())) == 3
        assert caplog.text.count('TRACE task correlation') == 3
    finally:
        pool.shutdown(wait=True)


def test_scope_is_reset_before_future_callbacks_and_nonworker_events():
    diag = Diagnostics()
    pool = PriorityExecutor(max_workers=1)
    ready = threading.Event()
    def operation():
        emit(diag)
        assert ready.wait(timeout=2)
    try:
        future = pool.submit(0, operation)
        done = threading.Event()
        def callback(_future):
            emit(diag, 'dddddddddddd')
            done.set()
        future.add_done_callback(callback)
        ready.set()
        future.result(timeout=2)
        assert done.wait(timeout=2)
        emit(diag, 'eeeeeeeeeeee')
        rows = diag.snapshot()
        assert rows[0].get('task_ref')
        assert 'task_ref' not in rows[1] and 'task_ref' not in rows[2]
    finally:
        pool.shutdown(wait=True)


def test_disabled_filtered_and_unrelated_events_do_not_allocate_or_log(caplog, monkeypatch):
    def forbidden(*_a, **_kw):
        raise AssertionError('unneeded random reference allocation')
    monkeypatch.setattr('cifereca.diagnostic_task_context.secrets.token_hex', forbidden)
    pool = PriorityExecutor(max_workers=1)
    def operation():
        emit(Diagnostics(enabled=False))
        Diagnostics(level='normal').add('NUMILOG','session_state',level='detailed')
        Diagnostics().add('CORE','check')
    try:
        with caplog.at_level(logging.INFO, logger='cifereca'):
            pool.submit(0, operation).result(timeout=2)
        assert 'task correlation' not in caplog.text
    finally:
        pool.shutdown(wait=True)


def test_bad_optional_log_handler_does_not_change_failure_or_poison_next_task():
    diag = Diagnostics()
    pool = PriorityExecutor(max_workers=1)
    logger = logging.getLogger('cifereca')
    class Broken(logging.Handler):
        def emit(self, record):
            raise RuntimeError('PRIVATE handler')
    handler = Broken()
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        assert pool.submit(0, lambda: emit(diag)).result(timeout=2) is None
        assert pool.submit(0, lambda: 42).result(timeout=2) == 42
    finally:
        pool.shutdown(wait=True)
        logger.removeHandler(handler)
        logger.setLevel(old_level)


@pytest.mark.parametrize('reference', ['PRIVATE','abcdef012345?auth=PRIVATE', 'abcdef01234\n', True, {}, None])
def test_public_feed_rejects_untrusted_reference_values(tmp_path, reference):
    row = dict(timestamp='2026-10-16T10:00:00+00:00',category='NUMILOG',
        operation='acquisition_delivery',status='error',task_ref=reference,
        detail={'phase':'completed','code':'html_delivery_link_missing'})
    payload = query_diagnostic_events(SimpleNamespace(snapshot=lambda:[row]),
                                      source='core_events',log_path=tmp_path/'missing')
    assert 'task_ref' not in payload['events'][0]['detail']
    assert 'PRIVATE' not in json.dumps(payload)


def test_log_scope_trace_requires_exact_core_logger_and_complete_format(tmp_path):
    path = tmp_path/'cifereca.log'
    lines = [
        'INFO cifereca TRACE task correlation task_ref=abcdef012345 status=success',
        'INFO cifereca TRACE task correlation task_ref=abcdef012345 status=success auth=PRIVATE',
        'INFO cifereca TRACE task correlation task_ref=PRIVATE status=failed',
        'INFO cifereca_numilog TRACE task correlation task_ref=abcdef012345 status=failed',
    ]
    path.write_text('\n'.join('2026-10-16 10:00:00,001 '+line for line in lines)+'\n')
    payload = query_diagnostic_events(Diagnostics(),source='app_log',log_path=path)
    assert sum('task_ref' in e for e in payload['events']) == 1
    assert 'PRIVATE' not in json.dumps(payload)


def test_reference_generation_failure_is_optional_and_does_not_break_work(monkeypatch):
    monkeypatch.setattr('cifereca.diagnostic_task_context.secrets.token_hex',
                        lambda *_: (_ for _ in ()).throw(OSError('PRIVATE')))
    diag = Diagnostics()
    pool = PriorityExecutor(max_workers=1)
    try:
        assert pool.submit(0, lambda: emit(diag)).result(timeout=2) is None
        assert 'task_ref' not in diag.snapshot()[0]
    finally:
        pool.shutdown(wait=True)


def test_cooperative_cancellation_has_its_own_terminal_outcome(caplog):
    from cifereca.plugin_api import Cancelled
    diag = Diagnostics()
    pool = PriorityExecutor(max_workers=1)
    def cancelled():
        emit(diag)
        raise Cancelled('PRIVATE')
    try:
        with caplog.at_level(logging.INFO, logger='cifereca'):
            with pytest.raises(Cancelled):
                pool.submit(0, cancelled).result(timeout=2)
        assert 'status=cancelled' in caplog.text
        assert "PRIVATE" in caplog.text  # diagnostic JSON is retained verbatim
    finally:
        pool.shutdown(wait=True)


def test_headless_taskrunner_uses_same_real_worker_scope(caplog):
    # Existing headless adapter compiles the production TaskRunner class;
    # replace only its test pool with the real shared executor. No GTK claim.
    from tests.test_core_p0_981_redacted_task_traces import _runner
    runner, displayed, flush, _logger = _runner()
    runner.pool = PriorityExecutor(max_workers=1)
    diag = Diagnostics()
    errors = []
    def operation():
        emit(diag)
        raise RuntimeError('PRIVATE')
    try:
        with caplog.at_level(logging.INFO, logger='cifereca'):
            runner.submit('PRIVATE', operation, err=errors.append, kind='obtain')
            # Sentinel behind the worker also observes completion callbacks.
            runner.pool.submit(100, lambda: None).result(timeout=2)
            flush()
        ref = diag.snapshot()[0]['task_ref']
        assert f'task_ref={ref} status=failed' in caplog.text
        assert errors and isinstance(errors[0], RuntimeError)
        assert any(row[0]=='finished' and row[1][3] is False for row in displayed)
        assert "PRIVATE" in caplog.text  # diagnostic JSON is retained verbatim
    finally:
        runner.close()
