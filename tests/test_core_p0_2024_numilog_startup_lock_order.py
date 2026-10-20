"""P0 #2024: startup inventory/Numilog backup must not race on global lock.

The real GTK-free startup dispatcher submits background tasks, but the
Numilog backup can be *queued* only after the inventory job's GTK terminal
callback, when sync_coordination has released local-acquisition.lock.
All sessions, sources, errors and identities below are synthetic.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui import numilog_materialization_startup as backup
from cifereca.gui import source_inventory_startup as inventory
from tests.test_core_p0_1346_source_inventory_timer import rig, SOURCE_ID


def make_window(monkeypatch, *, due=True):
    source = NS(
        identity_id='alice', source_id=SOURCE_ID, provider_id='numilog',
        enabled=True, connected=True, sync_capable=True,
        status='idle', last_attempt='',
        phase_states=(NS(
            phase='discover_rights', status='idle', last_attempt='',
        ),),
    )
    r = rig(
        accounts=[source],
        modes={SOURCE_ID: {
            'mode': 'on_start' if due else 'manual',
            'interval_minutes': 60,
        }},
    )
    forwarded = []
    presented = []
    monkeypatch.setattr(
        backup, 'dispatch_after_opening',
        lambda w: forwarded.append(w) or True,
    )
    r.window.runner.present_error = presented.append
    return r, forwarded, presented


def test_two_due_consents_queue_inventory_then_backup_after_lock_release(monkeypatch):
    r, forwarded, _presented = make_window(monkeypatch)
    assert inventory.dispatch_after_opening(r.window)
    assert len(r.jobs) == 1
    assert r.jobs[0].options['kind'] == 'source-sync'
    assert r.jobs[0].options['origin'] == 'automatic'
    assert forwarded == []
    # Reentrant startup dispatch may not leapfrog an in-flight sync.
    assert inventory.dispatch_after_opening(r.window) is False
    assert forwarded == []
    r.jobs[0].work()  # Mock sync completed and released its lock.
    assert forwarded == []
    r.jobs[0].done(None)  # TaskRunner calls this only in GTK terminal delivery.
    assert forwarded == [r.window]
    assert ('dispatch', 'alice', 'startup') in r.records['calls']


def test_failed_inventory_does_not_starve_independently_consented_backup(monkeypatch):
    r, forwarded, presented = make_window(monkeypatch)
    submitted = []
    def submit(label, work, done=None, err=None, **opts):
        submitted.append(NS(work=work, done=done, err=err, options=opts))
    r.window.runner.submit = submit
    r.broker.synchronize_scheduled_inventory = lambda *_args: (
        (_ for _ in ()).throw(RuntimeError('PRIVATE_SIGNED_URL_AND_COOKIE'))
    )
    assert inventory.dispatch_after_opening(r.window)
    assert len(submitted) == 1 and forwarded == []
    with pytest.raises(RuntimeError, match='Actualisation initiale'):
        submitted[0].work()
    submitted[0].err(RuntimeError('PRIVATE_SIGNED_URL_AND_COOKIE'))
    assert forwarded == [r.window]
    assert len(presented) == 1
    assert str(presented[0]) == (
        'Actualisation initiale des notices des comptes indisponible'
    )
    assert 'PRIVATE_SIGNED_URL_AND_COOKIE' not in str(presented)


@pytest.mark.parametrize('change', ['closed', 'rebound'])
def test_after_inventory_does_not_dispatch_to_dead_or_rebound_window(
    monkeypatch, change,
):
    r, forwarded, _presented = make_window(monkeypatch)
    assert inventory.dispatch_after_opening(r.window)
    if change == 'closed':
        r.window._closed = True
    else:
        r.window.plugin_manager.broker = lambda _name: None
    r.jobs[0].done(None)
    assert forwarded == []


def test_without_due_inventory_dispatches_backup_immediately(monkeypatch):
    r, forwarded, _presented = make_window(monkeypatch, due=False)
    assert inventory.dispatch_after_opening(r.window)
    assert r.jobs == []
    assert forwarded == [r.window]


def test_failed_inventory_queue_submission_still_allows_backup(monkeypatch):
    r, forwarded, _presented = make_window(monkeypatch)
    def refusing(*_args, **_kwargs):
        raise RuntimeError('PRIVATE_IDENTITY')
    r.window.runner.submit = refusing
    assert inventory.dispatch_after_opening(r.window)
    assert forwarded == [r.window]
    assert not getattr(r.window, '_source_inventory_startup_dispatched', False)
