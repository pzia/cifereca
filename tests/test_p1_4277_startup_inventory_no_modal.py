"""#4277: failed automatic inventory must never open a modal after startup.

These are GTK-free terminal callbacks. TaskRunner owns the diagnostic/activity
error receipt; the handler owns only non-modal feedback and no extra GET.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui import source_inventory_startup as inventory
from tests.test_core_p0_1346_source_inventory_timer import rig as inventory_rig, SOURCE_ID


def make_window(monkeypatch):
    """Independent generic opening-inventory fixture; no Numilog worker."""
    source = NS(
        identity_id="alice", source_id=SOURCE_ID, provider_id="numilog",
        enabled=True, connected=True, sync_capable=True,
        status="idle", last_attempt="",
        phase_states=(NS(
            phase="discover_rights", status="idle", last_attempt="",
        ),),
    )
    r = inventory_rig(
        accounts=[source],
        modes={SOURCE_ID: {"mode": "on_start", "interval_minutes": 60}},
    )
    modals = []
    r.window.runner.present_error = modals.append
    return r, modals


@pytest.mark.parametrize("change", ["closed", "broker_replaced"])
def test_late_failed_inventory_callback_does_not_surface_on_stale_window(
    monkeypatch, change,
):
    rig, modals = make_window(monkeypatch)
    hints = []
    rig.window.toast = hints.append
    queued = []
    rig.window.runner.submit = lambda label, work, done, err, **opts: queued.append(
        NS(work=work, done=done, err=err, options=opts)
    )
    assert inventory.dispatch_after_opening(rig.window)
    assert len(queued) == 1
    assert queued[0].options["origin"] == "automatic"

    if change == "closed":
        rig.window._closed = True
    else:
        rig.window.plugin_manager.broker = lambda _name: None
    queued[0].err(RuntimeError("https://provider.invalid?token=PRIVATE"))

    assert hints == []
    assert modals == []


def test_failed_inventory_without_toast_port_never_starts_legacy_backup(
    monkeypatch,
):
    rig, modals = make_window(monkeypatch)
    queued = []
    rig.window.runner.submit = lambda label, work, done, err, **opts: queued.append(
        NS(work=work, done=done, err=err, options=opts)
    )
    assert inventory.dispatch_after_opening(rig.window)
    # Old headless hosts and port-less shells must stay safe.
    queued[0].err(RuntimeError("PRIVATE PROVIDER FAILURE"))

    assert len(queued) == 1  # no secondary provider-specific worker
    assert modals == []
