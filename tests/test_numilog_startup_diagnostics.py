"""Real Core/SQLite failure telemetry; no live account, network or GTK claim."""
import json
import logging
import re
from types import SimpleNamespace as NS

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.numilog_materialization_startup import execute
from cifereca.core.numilog_startup_diagnostics import public_fields, read, save
from cifereca.diagnostics import Diagnostics
from cifereca.gui.numilog_materialization_status import describe_run
from cifereca.plugin_api import Cancelled
from cifereca.task_queue import PriorityExecutor
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_1862_numilog_store_mirror import _row
from tests.test_core_p0_numilog_startup_account_isolation import _two_accounts, _safe_mock_downloads

PRIVATE = 'PRIVATE email@example.invalid https://private/?auth=PRIVATE /home/PRIVATE <html>PRIVATE</html>'


def explode(*_a, **_kw):
    raise RuntimeError(PRIVATE)


def terminal(service):
    return [r['detail'] for r in service.diagnostics.snapshot()
            if r['operation'] == 'startup_materialization' and r['detail']['phase'] == 'completed']


def assert_private_absent(service, result, status, tmp_path):
    payload = query_diagnostic_events(service.diagnostics, source='core_events', log_path=tmp_path/'absent')
    for data in (payload, result.facts, status, service.diagnostics.text('test')):
        text = json.dumps(data)
        assert 'PRIVATE' not in text and 'email@' not in text and '/home/' not in text
    return payload


@pytest.mark.parametrize('fault,phase,code,attempted', [
    ('manifest', 'manifest_fetch', 'manifest_failed', 0),
    ('invalid', 'manifest_fetch', 'manifest_invalid', 0),
    ('preflight', 'preflight', 'preflight_failed', 0),
    ('acquire', 'acquisition', 'acquisition_failed', 1),
    ('adept', 'adept', 'adept_failed', 1),
    ('index', 'index', 'index_failed', 1),
    ('finalize', 'receipt_finalization', 'receipt_finalize_failed', 1),
])
def test_each_actual_failure_phase_is_correlated_persisted_exported_and_passive(
    tmp_path, monkeypatch, caplog, fault, phase, code, attempted,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    pool = PriorityExecutor(max_workers=1)
    try:
        service.diagnostics = Diagnostics()
        _authorize(broker, sid)
        session.library = lambda: [_row(account.credential_id, 0)]
        acquired = _safe_mock_downloads(monkeypatch, service)
        if fault == 'manifest': session.library = explode
        elif fault == 'invalid': session.library = lambda: {'html': PRIVATE}
        elif fault == 'mirror':
            monkeypatch.setattr('cifereca.core.numilog_materialization_startup.mirror_numilog_manifest_rights', explode)
        elif fault == 'preflight': monkeypatch.setattr(type(broker), 'preflight_numilog_purchase', explode)
        elif fault == 'acquire': monkeypatch.setattr(service, 'acquire_direct_item', explode)
        elif fault == 'adept':
            monkeypatch.setattr(service, '_import_acsm_impl', explode)
            monkeypatch.setattr(service, 'acquire_direct_item', lambda row, **kw:
                service.import_acsm(tmp_path/'PRIVATE.acsm', identity_id=account.identity_id,
                                    provider_id='numilog', title=PRIVATE))
        elif fault == 'index':
            monkeypatch.setattr('cifereca.core.numilog_materialization_startup.index_delivered', explode)
        elif fault == 'finalize': monkeypatch.setattr(service.storage, 'numilog_materialization_finish_startup', explode)
        with caplog.at_level(logging.INFO, logger='cifereca'):
            result = pool.submit(0, lambda: execute(broker, account.identity_id, 'startup')).result(timeout=4)
        summary, = terminal(service)
        assert summary['code'] == code and summary['cause_phase'] == phase
        assert summary['status'] == ('partial' if fault == 'finalize' else 'failed')
        assert summary['attempted'] == attempted and summary['failed'] == 1
        assert summary['downloaded'] == int(fault == 'finalize')
        ref = summary['task_ref']
        assert re.fullmatch('[0-9a-f]{12}', ref)
        # The Core return is not an exception, so executor status is success;
        # the real GUI wraps a false result as a failed task (tested below).
        assert f'task_ref={ref} status=success' in caplog.text
        status = broker.numilog_materialization_run_status(sid)
        assert status['diagnostic'] == summary
        assert status['status'] == ('running' if fault == 'finalize' else 'failed')
        caption = describe_run('on_start', status)
        assert f'Phase : {phase}' in caption and f'cause : {code}' in caption
        assert f'{attempted} tenté(s)' in caption and '1 échec(s)' in caption
        payload = assert_private_absent(service, result, status, tmp_path)
        event = next(e for e in payload['events'] if e.get('detail', {}).get('phase') == 'completed')
        assert event['module'] == 'NUMILOG'
        assert event['detail']['task_ref'] == ref and f'code={code}' in event['message']
        # Rendering/export/status cannot fetch even when connected.
        session.library = explode
        for _ in range(3):
            assert broker.numilog_materialization_run_status(sid) == status
            assert describe_run('on_start', status) == caption
            query_diagnostic_events(service.diagnostics, source='core_events', log_path=tmp_path/'absent')
        assert calls == []
        assert 'PRIVATE' not in caplog.text
    finally:
        pool.shutdown(wait=True)
        service.close()


def test_claim_revocation_blocks_without_attempt_and_no_claim_is_not_an_error(tmp_path, monkeypatch):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        original = service.storage.numilog_materialization_claim_startup
        def claim(**kw):
            result = original(**kw)
            broker.revoke_numilog_materialization_consent(sid)
            return result
        monkeypatch.setattr(service.storage, 'numilog_materialization_claim_startup', claim)
        session.library = explode
        result = execute(broker, account.identity_id, 'startup')
        assert result.facts['status'] == 'blocked'
        diagnostic = broker.numilog_materialization_run_status(sid)['diagnostic']
        assert diagnostic['status'] == 'blocked' and diagnostic['code'] == 'consent_revoked'
        assert diagnostic['attempted'] == diagnostic['failed'] == 0
        assert calls == []
    finally: service.close()


def test_not_due_keeps_previous_receipt_and_never_retries_a_signed_download(tmp_path, monkeypatch):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        session.library = lambda: []
        assert execute(broker, account.identity_id, 'startup').ok
        prior = service.storage.numilog_materialization_run_status(sid)
        session.library = explode
        result = execute(broker, account.identity_id, 'startup')
        assert result.ok and result.facts['status'] == 'not_due'
        status = broker.numilog_materialization_run_status(sid)
        assert service.storage.numilog_materialization_run_status(sid) == prior
        summary = status['diagnostic']
        assert summary['status'] == summary['code'] == 'not_due'
        assert summary['cause_phase'] == 'durable_claim'
        assert summary['failed'] == summary['attempted'] == 0
        assert 'Aucun nouvel essai' in describe_run('on_start', status)
        assert calls == []
    finally: service.close()



def test_not_due_preserves_previous_durable_failed_run_and_its_cause(tmp_path):
    """A later throttled opening cannot erase the prior user's P0 failure."""
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        session.library = explode
        first = execute(broker, account.identity_id, "startup")
        assert first.ok is False and first.facts["status"] == "failed"
        old = broker.numilog_materialization_run_status(sid)
        assert old["status"] == "failed"
        assert old["diagnostic"]["cause_phase"] == "manifest_fetch"
        assert old["diagnostic"]["code"] == "manifest_failed"
        receipt = service.storage.numilog_materialization_run_status(sid)
        second = execute(broker, account.identity_id, "startup")
        assert second.ok is True and second.facts["status"] == "not_due"
        # The no-op still emits its own current diagnostic event, but does
        # not overwrite the previous failure's durable auxiliary evidence.
        events = terminal(service)
        assert [event["code"] for event in events] == [
            "manifest_failed", "not_due",
        ]
        now = broker.numilog_materialization_run_status(sid)
        assert now == old
        assert service.storage.numilog_materialization_run_status(sid) == receipt
        text = describe_run("on_start", now)
        assert "Dernier essai en échec" in text
        assert "Phase : manifest_fetch ; cause : manifest_failed" in text
        assert "Aucun nouvel essai" not in text
        assert calls == []
        assert_private_absent(service, second, now, tmp_path)
    finally:
        service.close()


def test_partial_accounts_have_distinct_ephemeral_runs_exact_counts_one_task(tmp_path, monkeypatch):
    service, broker, first, second = _two_accounts(tmp_path)
    a, sa, ca, sida = first; b, sb, cb, sidb = second
    pool = PriorityExecutor(max_workers=1)
    try:
        service.diagnostics = Diagnostics()
        sa.library = explode
        sb.library = lambda: [_row(b.credential_id, 0)]
        _safe_mock_downloads(monkeypatch, service)
        result = pool.submit(0, lambda: execute(broker, 'default', 'startup')).result(timeout=4)
        assert result.facts['status'] == 'partial' and result.changed == 1
        left = broker.numilog_materialization_run_status(sida)['diagnostic']
        right = broker.numilog_materialization_run_status(sidb)['diagnostic']
        assert left['code'] == 'manifest_failed' and left['attempted'] == 0
        assert right['code'] == 'complete' and right['downloaded'] == right['attempted'] == 1
        assert left['task_ref'] == right['task_ref']
        assert left['run_ref'] != right['run_ref']
        assert len(result.facts['source_results']) == 2
        assert_private_absent(service, result, {'left':left,'right':right}, tmp_path)
        assert ca == cb == []
    finally:
        pool.shutdown(wait=True); service.close()


def test_first_cause_survives_secondary_receipt_failure_even_with_zero_attempts(tmp_path, monkeypatch):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        session.library = explode
        monkeypatch.setattr(service.storage, 'numilog_materialization_finish_startup', explode)
        result = execute(broker, account.identity_id, 'startup')
        summary = broker.numilog_materialization_run_status(sid)['diagnostic']
        assert summary['code'] == 'manifest_failed' and summary['cause_phase'] == 'manifest_fetch'
        assert summary['failed'] == 2 and summary['attempted'] == 0
        assert '2 échec(s)' in describe_run('on_start', broker.numilog_materialization_run_status(sid))
        assert result.facts['failed'] == 2 and calls == []
    finally: service.close()


def test_cancelled_batch_keeps_interrupted_phase_and_reraises(tmp_path, monkeypatch):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        session.library = lambda: (_ for _ in ()).throw(Cancelled(PRIVATE))
        with pytest.raises(Cancelled): execute(broker, account.identity_id, 'startup')
        summary = broker.numilog_materialization_run_status(sid)['diagnostic']
        assert summary['code'] == 'interrupted' and summary['cause_phase'] == 'manifest_fetch'
        assert summary['status'] == 'blocked' and summary['attempted'] == 0
    finally: service.close()


def valid():
    return dict(phase='completed', cause_phase='manifest_fetch', code='manifest_failed',
                status='failed', task_kind='source-materialize', attempted=0, downloaded=0,
                skipped=0, failed=1, task_ref='abcdef012345', run_ref='012345abcdef')


@pytest.mark.parametrize('key,value', [('phase', PRIVATE), ('cause_phase',PRIVATE), ('code',PRIVATE),
    ('status', PRIVATE), ('task_kind',PRIVATE), ('task_ref',PRIVATE), ('run_ref',PRIVATE),
    ('failed',True), ('attempted',9), ('skipped',2049), ('downloaded',1), ('url',PRIVATE)])
def test_projection_and_render_reject_untrusted_telemetry(tmp_path, key, value):
    detail = dict(valid(), **{key:value})
    assert public_fields(detail) == {}
    row = dict(timestamp='2026-10-18T16:00:00+00:00', category='NUMILOG',
               operation='startup_materialization', status='error', detail=detail)
    payload = query_diagnostic_events(NS(snapshot=lambda:[row]), source='core_events', log_path=tmp_path/'absent')
    assert not payload['events'][0].get('detail')
    assert 'PRIVATE' not in json.dumps(payload)


def test_diagnostic_cannot_replace_newer_claim_or_authoritative_receipt(tmp_path):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        revision = service.storage.numilog_materialization_consent_revision(sid)
        first = 2_000_000_000
        assert service.storage.numilog_materialization_claim_startup(source_id=sid, now=first, expected_revision=revision)
        save(service.storage, sid, str(first), valid(), claimed=True)
        assert read(service.storage, sid) == valid()
        # A second worker denied its claim must not mask a still-running run.
        save(service.storage, sid, str(first), dict(valid(), code='not_due', status='not_due'))
        assert read(service.storage, sid) == valid()
        assert service.storage.numilog_materialization_claim_startup(source_id=sid, now=first+3600, expected_revision=revision)
        save(service.storage, sid, str(first), valid(), claimed=True)
        assert read(service.storage, sid) == {}
        assert service.storage.numilog_materialization_run_status(sid)['status'] == 'running'
    finally: service.close()


def test_real_taskrunner_auto_failure_preserves_reference_without_a_modal(tmp_path, caplog):
    from tests.test_core_p0_981_redacted_task_traces import _runner
    from cifereca.gui.numilog_materialization_startup import dispatch_after_opening
    service, account, session, calls, broker, sid = _setup(tmp_path)
    runner, displayed, flush, logger = _runner()
    runner.pool = PriorityExecutor(max_workers=1)
    from cifereca.plugin_api import AttentionRequired, UserFacingError
    runner.present_error.__func__.__globals__.update(AttentionRequired=AttentionRequired, UserFacingError=UserFacingError)
    window = runner.window
    window.service = service
    window.plugin_manager = NS(broker=lambda _name:broker)
    window.runner = runner
    from unittest.mock import Mock
    window.toast = Mock()
    try:
        _authorize(broker, sid)
        session.library = explode
        with caplog.at_level(logging.INFO):
            assert dispatch_after_opening(window)
            runner.pool.submit(100, lambda:None).result(timeout=4)
            flush()
        summary = broker.numilog_materialization_run_status(sid)['diagnostic']
        ref = summary['task_ref']
        assert f'task_ref={ref} status=failed' in caplog.text
        assert 'kind=source-materialize' in caplog.text
        assert summary['code'] == 'manifest_failed'
        assert summary['attempted'] == summary['downloaded'] == 0
        assert any(row[0]=='finished' and row[1][3] is False for row in displayed)
        window.show_error.assert_not_called()
        window.toast.assert_called_once_with(
            'Sauvegarde Numilog à vérifier ; consultez Synchronisation.')
        assert 'PRIVATE' not in repr(window.toast.call_args)
        assert 'PRIVATE' not in caplog.text and calls == []
    finally:
        runner.close(); service.close()


@pytest.mark.parametrize('fault,phase,code', [('admission','admission','admission_failed'),
                                           ('claim','durable_claim','claim_failed')])
def test_failure_before_claim_still_has_zero_attempt_bilan(tmp_path, monkeypatch, fault, phase, code):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        if fault == 'admission':
            monkeypatch.setattr(service.storage, 'numilog_materialization_consent_revision', explode)
        else:
            monkeypatch.setattr(service.storage, 'numilog_materialization_claim_startup', explode)
        session.library = explode
        result = execute(broker, account.identity_id, 'startup')
        summary = broker.numilog_materialization_run_status(sid)['diagnostic']
        assert result.ok is False and summary['status'] == 'failed'
        assert summary['code'] == code and summary['cause_phase'] == phase
        assert summary['attempted'] == summary['downloaded'] == 0
        assert '0 tenté(s)' in describe_run('on_start', broker.numilog_materialization_run_status(sid))
        assert service.storage.numilog_materialization_run_status(sid)['status'] == 'never'
        assert calls == []
    finally: service.close()


def test_optional_diagnostic_failure_does_not_change_success_or_leave_adept_scope(tmp_path, monkeypatch):
    from cifereca.core.numilog_startup_diagnostics import _ACTIVE
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        session.library = lambda: [_row(account.credential_id, 0)]
        _safe_mock_downloads(monkeypatch, service)
        monkeypatch.setattr(service.diagnostics, 'add', explode)
        monkeypatch.setattr('cifereca.core.numilog_startup_diagnostics.save', explode)
        result = execute(broker, account.identity_id, 'startup')
        assert result.ok and result.changed == 1 and _ACTIVE.get() is None
        assert broker.numilog_materialization_run_status(sid)['status'] == 'complete'
        assert calls == []
    finally: service.close()
