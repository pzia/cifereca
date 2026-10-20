"""#2024: acquisition auto fails in milliseconds but manual delivery works.

All account/library/file values are fake. The closed startup diagnostic
identifies the refused boundary, never a private URL or exception message.
No vendor HTTP, reattempt, ACSM or GUI interaction is performed here.
"""
from __future__ import annotations

from pathlib import Path
import hashlib

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.numilog_materialization_startup import execute
from cifereca.core.numilog_startup_diagnostics import StartupTrace
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_1862_numilog_store_mirror import _row
from tests.test_numilog_owned_library_download import case

PRIVATE = 'PRIVATE https://signed.example.invalid/?token=PRIVATE user@example.invalid'


def terminal(service):
    return [
        event['detail']
        for event in service.diagnostics.snapshot()
        if event.get('operation') == 'startup_materialization'
        and event['detail'].get('phase') == 'completed'
    ]


def fail_private(*args, **kwargs):
    raise RuntimeError(PRIVATE)


@pytest.mark.parametrize('gate', [
    'acquisition_source_unavailable',
    'acquisition_local_conflict',
    'acquisition_root_unavailable',
    'acquisition_file_conflict',
    'acquisition_publication_guard',
    'acquisition_admission_failed',
    'acquisition_delivery_failed',
    'acquisition_artifact_invalid',
])
def test_closed_refusal_boundary_without_private_data_or_provider_io(
    case, monkeypatch, gate,
):
    service = case.service
    callback = lambda: case.item
    if gate == 'acquisition_source_unavailable':
        monkeypatch.setattr(
            'cifereca.core.numilog_manual_acquisition.require_source',
            fail_private,
        )
    elif gate == 'acquisition_local_conflict':
        monkeypatch.setattr(
            service.storage, 'numilog_purchase_has_local_conflict',
            lambda **kw: True,
        )
    elif gate == 'acquisition_root_unavailable':
        monkeypatch.setattr(
            service, 'library_identity_provider_dir',
            lambda *a: Path('/does-not-exist/numilog'),
        )
    elif gate == 'acquisition_file_conflict':
        folder = service.library_identity_provider_dir(
            case.account.identity_id, 'numilog',
        )
        token = hashlib.sha256(case.item.record_id.encode()).hexdigest()[:32]
        (folder / ('numilog-' + token + '.epub')).write_bytes(b'FICTIONAL')
    elif gate == 'acquisition_publication_guard':
        monkeypatch.setattr(
            'cifereca.core.numilog_publication_guard.require_tracked_publications',
            fail_private,
        )
    elif gate == 'acquisition_admission_failed':
        callback = fail_private
    elif gate == 'acquisition_delivery_failed':
        case.session.download_direct = fail_private
    elif gate == 'acquisition_artifact_invalid':
        case.session.download_direct = (
            lambda *_a: (Path('/does-not-exist/private.epub'), PRIVATE)
        )

    broker = SynchronizationBrokerImpl(service)
    trace = StartupTrace(service, broker._source_id(case.account))
    trace.counts.update(attempted=1, failed=1)
    with trace.acquisition():
        with pytest.raises(Exception):
            service.acquire_direct_item(case.item, _admission=callback)
    trace.finish('failed')

    summary = terminal(service)[-1]
    assert summary['cause_phase'] == 'admission'
    assert summary['code'] == gate
    assert summary['attempted'] == summary['failed'] == 1
    assert 'PRIVATE' not in repr(service.diagnostics.snapshot())
    assert 'user@example.invalid' not in service.diagnostics.text('test')
    assert case.calls == []


def test_busy_lock_carries_closed_code_and_never_reaches_provider(case, monkeypatch):
    from cifereca.locking import LockBusyError

    class Locked:
        def __init__(self, path): pass
        def __enter__(self): raise LockBusyError(PRIVATE)
        def __exit__(self, *_a): raise AssertionError('must not enter')

    monkeypatch.setattr('cifereca.services.InterProcessLock', Locked)
    trace = StartupTrace(case.service, SynchronizationBrokerImpl(case.service)._source_id(case.account))
    trace.counts.update(attempted=1, failed=1)
    with trace.acquisition():
        with pytest.raises(Exception):
            case.service.acquire_direct_item(case.item)
    trace.finish('failed')
    assert terminal(case.service)[-1]['code'] == 'acquisition_lock_busy'
    assert case.calls == []
    assert 'PRIVATE' not in repr(case.service.diagnostics.snapshot())


def test_real_startup_failure_preserves_refusal_boundary_in_durable_receipt(
    tmp_path, monkeypatch,
):
    """No stub around acquire_direct_item: test the real startup→lock path."""
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        item = _row(account.credential_id, 0)
        session.library = lambda: [item]
        monkeypatch.setattr(
            service.storage, 'numilog_purchase_has_local_conflict',
            lambda **kw: True,
        )
        result = execute(broker, account.identity_id, 'startup')
        assert result.facts['status'] == 'failed'
        assert result.facts['attempted'] == result.facts['failed'] == 1
        assert result.facts['downloaded'] == 0
        status = broker.numilog_materialization_run_status(sid)
        assert status['status'] == 'failed'
        assert status['diagnostic']['code'] == 'acquisition_local_conflict'
        assert status['diagnostic']['cause_phase'] == 'acquisition'
        assert calls == []
        assert 'PRIVATE' not in repr(service.diagnostics.snapshot())
    finally:
        service.close()
