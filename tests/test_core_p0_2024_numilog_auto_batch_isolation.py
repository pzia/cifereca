"""#2024: one failed owned Numilog edition must not starve other purchases.

No live downloads, ACSM links, accounts or files. Only fake in-memory
acquisitions are used; the real worker retains its lock/admission callbacks.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.numilog_materialization_startup import execute
from cifereca.plugin_api import Cancelled, SyncResult
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_1862_numilog_store_mirror import _row


def rig(tmp_path, monkeypatch, count=3):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    _authorize(broker, sid)
    rows = [_row(account.credential_id, i) for i in range(count)]
    session.library = lambda: list(rows)
    monkeypatch.setattr(
        SynchronizationBrokerImpl, 'preflight_numilog_purchase',
        lambda *_a: SyncResult(ok=True, facts={'status': 'candidate_unverified'}),
    )
    return service, account, session, calls, broker, sid, rows


def test_failure_of_first_edition_does_not_starve_other_two(tmp_path, monkeypatch):
    service, account, session, calls, broker, sid, rows = rig(tmp_path, monkeypatch)
    visited, indexed = [], []

    def acquire(row, *, _admission=None):
        assert _admission() is row
        visited.append(row.record_id)
        if row is rows[0]:
            raise RuntimeError('PRIVATE_TOKEN and secret vendor URL')
        return NS(metadata={'numilog_checkpoint_id': 'FICTIONAL'})

    monkeypatch.setattr(service, 'acquire_direct_item', acquire)
    monkeypatch.setattr(
        'cifereca.core.numilog_materialization_startup.index_delivered',
        lambda service, a, *, title: indexed.append(title),
    )
    try:
        result = execute(broker, account.identity_id, 'startup')
        assert result.ok is False and result.facts['status'] == 'partial'
        assert result.facts['attempted'] == 3
        assert result.facts['downloaded'] == 2
        assert result.facts['failed'] == 1
        assert visited == [row.record_id for row in rows]
        assert indexed == [row.title for row in rows[1:]]
        status = broker.numilog_materialization_run_status(sid)
        assert status['status'] == 'partial'
        assert status['diagnostic']['code'] == 'acquisition_failed'
        assert status['diagnostic']['cause_phase'] == 'acquisition'
        assert 'PRIVATE_TOKEN' not in repr(service.diagnostics.snapshot())
        assert calls == []
        # The 60 minute durable throttle remains in force, no automatic
        # replay of the first failed edition on a second opening.
        again = execute(broker, account.identity_id, 'startup')
        assert again.facts['status'] == 'not_due' and len(visited) == 3
    finally:
        service.close()


def test_index_failure_does_not_reacquire_first_edition(tmp_path, monkeypatch):
    service, account, session, calls, broker, sid, rows = rig(tmp_path, monkeypatch, 2)
    visited, indexed = [], []

    def acquire(row, *, _admission=None):
        assert _admission() is row
        visited.append(row.record_id)
        return NS(metadata={'numilog_checkpoint_id': 'FICTIONAL'})

    def index(_service, _acq, *, title):
        indexed.append(title)
        if title == rows[0].title:
            raise RuntimeError('PRIVATE INDEX ERROR')

    monkeypatch.setattr(service, 'acquire_direct_item', acquire)
    monkeypatch.setattr('cifereca.core.numilog_materialization_startup.index_delivered', index)
    try:
        outcome = execute(broker, account.identity_id, 'startup')
        assert outcome.facts['status'] == 'partial'
        assert outcome.facts['attempted'] == 2
        assert outcome.facts['downloaded'] == 1
        assert outcome.facts['failed'] == 1
        assert visited == [rows[0].record_id, rows[1].record_id]
        assert indexed == [rows[0].title, rows[1].title]
        assert broker.numilog_materialization_run_status(sid)['diagnostic']['code'] == 'index_failed'
        assert calls == []
        assert 'PRIVATE' not in repr(service.diagnostics.snapshot())
    finally:
        service.close()


def test_cancellation_never_continues_to_next_edition(tmp_path, monkeypatch):
    service, account, session, calls, broker, sid, rows = rig(tmp_path, monkeypatch, 2)
    visited = []

    def cancel(row, *, _admission=None):
        assert _admission() is row
        visited.append(row.record_id)
        raise Cancelled()

    monkeypatch.setattr(service, 'acquire_direct_item', cancel)
    try:
        with pytest.raises(Cancelled):
            execute(broker, account.identity_id, 'startup')
        assert visited == [rows[0].record_id] and calls == []
        assert broker.numilog_materialization_run_status(sid)['status'] == 'blocked'
    finally:
        service.close()


def test_consent_revocation_after_first_error_blocks_second(tmp_path, monkeypatch):
    service, account, session, calls, broker, sid, rows = rig(tmp_path, monkeypatch, 2)
    visited = []

    def revoke(row, *, _admission=None):
        assert _admission() is row
        visited.append(row.record_id)
        broker.revoke_numilog_materialization_consent(sid)
        _admission()
        pytest.fail('revoked source cannot acquire')

    monkeypatch.setattr(service, 'acquire_direct_item', revoke)
    try:
        outcome = execute(broker, account.identity_id, 'startup')
        assert outcome.facts['status'] == 'failed'
        assert len(visited) == 1
        assert calls == []
    finally:
        service.close()
