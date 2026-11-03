"""#4277: failed automatic inventory must never open a modal after startup.

These are GTK-free terminal callbacks. TaskRunner owns the diagnostic/activity
error receipt; the handler owns only non-modal feedback and backup chaining.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui import source_inventory_startup as inventory
from tests.test_core_p0_2024_numilog_startup_lock_order import make_window


@pytest.mark.parametrize("change", ["closed", "broker_replaced"])
def test_late_failed_inventory_callback_does_not_surface_on_stale_window(
    monkeypatch, change,
):
    rig, backups, modals = make_window(monkeypatch)
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

    assert backups == []
    assert hints == []
    assert modals == []


def test_failed_inventory_without_toast_port_still_chains_independent_backup(
    monkeypatch,
):
    rig, backups, modals = make_window(monkeypatch)
    queued = []
    rig.window.runner.submit = lambda label, work, done, err, **opts: queued.append(
        NS(work=work, done=done, err=err, options=opts)
    )
    assert inventory.dispatch_after_opening(rig.window)
    # Old headless hosts and port-less shells must stay safe.
    queued[0].err(RuntimeError("PRIVATE PROVIDER FAILURE"))

    assert backups == [rig.window]
    assert modals == []
