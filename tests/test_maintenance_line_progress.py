"""Headless behavioural contracts for independent Tools row progress."""
from pathlib import Path
from threading import Thread, get_ident

import pytest

from cifereca.gui.maintenance_line_progress import MaintenanceLineProgress
from cifereca.gui.maintenance_tools import MaintenanceCancelControl


def mounted(*, alive=lambda: True):
    pending, rendered = [], []
    progress = MaintenanceLineProgress(idle_add=pending.append, render=rendered.append, alive=alive)
    return progress, pending, rendered


def running(progress):
    ticket = progress.begin()
    progress.confirmation(ticket)
    progress.running(ticket)
    return ticket


def test_worker_reports_are_coalesced_and_render_only_on_dispatch_thread():
    main_thread = get_ident()
    pending, renders = [], []
    progress = MaintenanceLineProgress(
        idle_add=pending.append,
        render=lambda state: renders.append((get_ident(), state)),
    )
    ticket = running(progress)
    worker = Thread(target=lambda: [progress.report(ticket, current=n, total=100) for n in range(50)])
    worker.start()
    worker.join()
    assert renders == []
    assert len(pending) == 1
    assert pending.pop()() is False
    assert renders[0][0] == main_thread
    assert renders[0][1].fraction == .49


@pytest.mark.parametrize('current,total', [(None,None),(1,0),(-1,10),(11,10),(True,10),(1,False),(1.0,10)])
def test_invalid_counts_remain_indeterminate(current, total):
    progress, pending, rendered = mounted()
    ticket = running(progress)
    progress.report(ticket, current=current, total=total, message='/private/path secret')
    pending.pop()()
    assert rendered[-1].fraction is None
    assert '/private' not in rendered[-1].label


def test_full_report_is_not_success_and_error_does_not_claim_completion():
    progress, pending, rendered = mounted()
    ticket = running(progress)
    progress.report(ticket, current=10, total=10)
    pending.pop()()
    assert rendered[-1].fraction == .99
    progress.finish(ticket, 'error')
    progress.report(ticket, current=10, total=10)
    progress.finish(ticket, 'success')
    pending.pop()()
    assert rendered[-1].phase == 'error'
    assert rendered[-1].fraction is None


def test_success_alone_publishes_hundred_percent():
    progress, pending, rendered = mounted()
    ticket = running(progress)
    progress.finish(ticket, 'success')
    pending.pop()()
    assert rendered[-1].phase == 'success'
    assert rendered[-1].fraction == 1


def test_new_generation_rejects_old_reports_confirmation_and_completion():
    progress, pending, rendered = mounted()
    old = running(progress)
    current = progress.begin()
    progress.report(old, current=9, total=10)
    progress.confirmation(old)
    progress.finish(old, 'success')
    pending.pop()()
    assert rendered[-1].phase == 'analysis'
    assert not progress.valid(old)
    assert progress.valid(current)


def test_dispose_suppresses_already_queued_callback_and_future_reports():
    progress, pending, rendered = mounted()
    ticket = running(progress)
    progress.dispose()
    pending.pop()()
    progress.report(ticket, current=1, total=1)
    progress.finish(ticket, 'success')
    progress.begin()
    assert rendered == []
    assert pending == []
    assert not progress.valid(ticket)


def test_dead_owner_drops_delivery():
    progress, pending, rendered = mounted(alive=lambda: False)
    running(progress)
    pending.pop()()
    assert rendered == []


def test_each_row_keeps_its_own_state_and_dispatch():
    left, lp, lr = mounted()
    right, rp, rr = mounted()
    lt, rt = running(left), running(right)
    left.report(lt, current=1, total=4)
    right.finish(rt, 'cancelled')
    lp.pop()()
    rp.pop()()
    assert lr[-1].fraction == .25
    assert rr[-1].phase == 'cancelled'


def test_scheduler_failure_does_not_escape_or_prevent_later_publication():
    pending = []
    def schedule(callback):
        if not pending:
            pending.append(None)
            raise RuntimeError('main loop gone')
        pending.append(callback)
    rendered = []
    progress = MaintenanceLineProgress(idle_add=schedule, render=rendered.append)
    ticket = progress.begin()
    progress.confirmation(ticket)
    pending[-1]()
    assert rendered[-1].phase == 'confirmation'


def test_control_relays_worker_counts_and_cancellation_stops_reports():
    calls = []
    control = MaintenanceCancelControl(on_progress=lambda *args: calls.append(args))
    worker = Thread(target=lambda: control.report(current=3, total=8, message='working'))
    worker.start()
    worker.join()
    assert calls == [(3, 8, 'working')]
    control.cancel()
    control.report(current=8, total=8)
    assert calls == [(3, 8, 'working')]


def test_control_owner_close_suppresses_reports_and_renderer_failure_is_isolated():
    closed = False
    calls = []
    def report(*args):
        calls.append(args)
        raise RuntimeError('presentation failed')
    control = MaintenanceCancelControl(owner_closed=lambda: closed, on_progress=report)
    control.report(current=1, total=2)
    closed = True
    control.report(current=2, total=2)
    assert len(calls) == 1


def test_retired_reconciliation_label_is_not_a_live_maintenance_mode():
    pending, rendered = [], []
    progress = MaintenanceLineProgress(
        idle_add=pending.append, render=rendered.append,
        activity_label="Réconciliation",
    )
    ticket = running(progress)
    pending.pop()()
    assert rendered[-1].label == "Traitement en cours…"
    progress.report(ticket, current=4, total=10,
                    message="/PRIVATE/account/SECRET")
    pending.pop()()
    assert rendered[-1].label == "Traitement en cours · 40 %"
    assert "/PRIVATE" not in rendered[-1].label


def test_trace_kind_allowlist_remains_closed_without_private_plugin_labels():
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/task_runner.py").read_text(encoding="utf-8")
    assert '"maintenance-plan", "maintenance-tool"' in source
    assert 'else "other"' in source
