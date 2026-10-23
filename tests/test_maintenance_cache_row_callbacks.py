"""Exercise production Tools callbacks with inert GTK, not source assertions."""
from types import SimpleNamespace as NS

import pytest

from tests import test_core_ui_1358_maintenance_preview_cancellation as harness
from cifereca.gui.maintenance_line_widgets import build_maintenance_line
from cifereca.gui import maintenance_tools as guard


class Widget(harness.Container):
    def __init__(self, **kwargs):
        super().__init__()
        self.text = kwargs.get('label', '')
        self.visible = True
        self.running = False
        self.fraction = None
    def set_tooltip_text(self, value): pass
    def set_show_text(self, value): pass
    def set_visible(self, value): self.visible = value
    def set_text(self, value): self.text = value
    def set_fraction(self, value): self.fraction = value
    def start(self): self.running = True
    def stop(self): self.running = False


def mounted(tool_id="core.cache.cleanup", *, danger_level="readonly"):
    harness.Dialog.shown.clear()
    tool = harness.Tool()
    original = tool.describe
    def describe():
        info = original()
        info.id = tool_id
        info.danger_level = danger_level
        return info
    tool.describe = describe
    jobs, errors, activity, pending = [], [], [], []
    method = harness._method()
    namespace = getattr(method, 'production_globals', method.__globals__)
    namespace.update(
        build_maintenance_line=build_maintenance_line,
        GLib=NS(idle_add=pending.append),
    )
    namespace['Gtk'] = NS(
        Button=harness.Button, Label=Widget, Box=Widget, Spinner=Widget,
        ProgressBar=Widget, ScrolledWindow=harness.Container,
        PolicyType=NS(NEVER=0, AUTOMATIC=1),
        Orientation=NS(VERTICAL=0, HORIZONTAL=1),
    )
    host = NS(
        _closed=False, _identity_context_generation=0,
        _tools_box=harness.Container(), _maintenance_line_rows=[],
        plugin_manager=NS(services=lambda _name: [NS(service=tool, plugin_id='core')]),
        service=NS(current_identity=NS(identity_id='alice', label='Alice')),
        action_button=lambda title, _icon: harness.Button(title),
        runner=NS(submit=lambda *args, **kw: jobs.append((args, kw))),
        show_error=lambda *args: errors.append(args),
        _activity_result=lambda *args, **kw: activity.append((args, kw)),
    )
    method(host)
    row = host._tools_box.children[0].children[0]
    preview, cancel, status = row.children
    feedback = host._maintenance_line_rows[0][0]
    return NS(host=host, tool=tool, jobs=jobs, errors=errors, activity=activity,
              pending=pending, preview=preview, cancel=cancel, status=status,
              feedback=feedback, refresh=lambda: method(host))


def confirmation(m):
    m.preview.click()
    args, _kwargs = m.jobs[-1]
    args[2](args[1]())
    return harness.Dialog.shown[-1]


def execute(m):
    dialog = confirmation(m)
    dialog.handlers['response'](dialog, 'run')
    return m.jobs[-1][0]


def flush(m):
    while m.pending:
        m.pending.pop(0)()


def test_widgets_reflect_analysis_confirmation_progress_and_success():
    m = mounted()
    dialog = confirmation(m)
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert label.text == 'Confirmation attendue'
    assert not spinner.visible and not bar.visible
    dialog.handlers['response'](dialog, 'run')
    args, kwargs = m.jobs[-1]
    control = m.host._maintenance_line_rows[0][1]['control']
    control.report(current=1, total=4)
    flush(m)
    assert bar.visible and bar.fraction == .25
    assert not spinner.visible
    args[2](NS(ok=True, summary='Done'))
    flush(m)
    assert bar.fraction == 1
    assert label.text == 'Terminé · 100 %'
    assert len(m.activity) == 1


def test_superseded_confirmation_cannot_launch_old_plan():
    m = mounted()
    dialog = confirmation(m)
    m.preview.click()
    dialog.handlers['response'](dialog, 'run')
    assert len(m.jobs) == 2
    assert all(kw['kind'] == 'maintenance-plan' for _args, kw in m.jobs)
    assert m.feedback.state.phase == 'analysis'


def test_refresh_cancels_worker_and_drops_late_completion():
    m = mounted()
    args = execute(m)
    control = m.host._maintenance_line_rows[0][1]['control']
    m.refresh()
    assert control.cancelled()
    args[2](NS(ok=True, summary='stale'))
    flush(m)
    assert m.activity == []
    assert m.errors == []


def test_detached_confirmation_does_not_submit_mutation():
    m = mounted()
    dialog = confirmation(m)
    m.preview.root = None
    dialog.handlers['response'](dialog, 'run')
    assert len(m.jobs) == 1


def test_identity_generation_change_drops_preview_even_if_identity_id_matches():
    m = mounted()
    m.preview.click()
    args, _kwargs = m.jobs[-1]
    plan = args[1]()
    m.host._identity_context_generation += 1
    args[2](plan)
    assert harness.Dialog.shown == []


def test_identity_generation_change_drops_confirmation_execution():
    m = mounted()
    dialog = confirmation(m)
    m.host._identity_context_generation += 1
    dialog.handlers['response'](dialog, 'run')
    assert len(m.jobs) == 1


def test_worker_failure_is_sanitized_before_task_runner_observes_it():
    m = mounted()
    m.tool.plan = lambda _context: (_ for _ in ()).throw(ValueError('/secret/path bearer-token'))
    m.preview.click()
    args, _kwargs = m.jobs[-1]
    with pytest.raises(RuntimeError) as caught:
        args[1]()
    assert str(caught.value) == 'Analyse des caches impossible.'
    assert caught.value.__suppress_context__


def test_cancelled_mutation_cannot_return_success_to_task_runner():
    m = mounted()
    def run(_plan, control):
        control.cancel()
        return NS(ok=True, summary='misleading success')
    m.tool.run = run
    args = execute(m)
    with pytest.raises(guard.MaintenanceCancelledError):
        args[1]()


def test_cancel_prevents_late_success_from_overwriting_row():
    m = mounted()
    args = execute(m)
    m.cancel.click()
    args[2](NS(ok=True, summary='late'))
    flush(m)
    assert m.feedback.state.phase == 'cancelled'
    assert not any(kwargs.get('success') for _args, kwargs in m.activity)


@pytest.mark.parametrize('error', [guard.MaintenanceBusyError(guard.BUSY_MESSAGE),
                                  guard.MaintenancePlanChangedError(guard.CHANGED_MESSAGE)])
def test_worker_preserves_safe_actionable_maintenance_guard_errors(error):
    m = mounted()
    m.tool.plan = lambda _context: (_ for _ in ()).throw(error)
    m.preview.click()
    args, _kwargs = m.jobs[-1]
    with pytest.raises(type(error)) as caught:
        args[1]()
    assert str(caught.value) == str(error)
    assert caught.value.__suppress_context__


def test_widget_shows_indeterminate_spinner_before_counts_arrive():
    m = mounted()
    m.preview.click()
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert label.text == 'Analyse…'
    assert spinner.visible and spinner.running
    assert not bar.visible
    args, _kwargs = m.jobs[-1]
    args[2](args[1]())
    dialog = harness.Dialog.shown[-1]
    dialog.handlers['response'](dialog, 'run')
    flush(m)
    assert label.text == 'Purge en cours…'
    assert spinner.visible and spinner.running
    assert not bar.visible

@pytest.mark.parametrize('error_type,message', [
    (guard.MaintenanceBusyError, guard.BUSY_MESSAGE),
    (guard.MaintenancePlanChangedError, guard.CHANGED_MESSAGE),
])
@pytest.mark.parametrize('phase', ['preview', 'run'])
def test_guard_reasons_remain_actionable_without_private_payload(error_type, message, phase):
    m = mounted()
    private_error = error_type('/secret/path bearer-token')
    if phase == 'preview':
        m.preview.click()
        args, _kwargs = m.jobs[-1]
    else:
        args = execute(m)
    args[3](private_error)
    flush(m)
    assert m.errors[-1][0] == message
    assert m.feedback.state.phase == 'error'


@pytest.mark.parametrize('error_type,message', [
    (guard.MaintenanceBusyError, guard.BUSY_MESSAGE),
    (guard.MaintenancePlanChangedError, guard.CHANGED_MESSAGE),
])
@pytest.mark.parametrize('phase', ['preview', 'run'])
def test_worker_guard_reason_is_safe_before_global_activity(error_type, message, phase):
    m = mounted()
    def fail(*_args):
        raise error_type('/secret/path bearer-token')
    if phase == 'preview':
        m.tool.plan = fail
        m.preview.click()
        args, _kwargs = m.jobs[-1]
    else:
        m.tool.run = fail
        args = execute(m)
    with pytest.raises(error_type) as caught:
        args[1]()
    assert str(caught.value) == message
    assert caught.value.__suppress_context__



def test_unrecognised_maintenance_tool_gets_independent_guarded_progress():
    # A third-party/future maintenance.tool must not silently drop the
    # per-tool status, cancellation guard or the safe error boundary.
    m = mounted(tool_id="third.party.maintenance.audit")
    assert len(m.host._maintenance_line_rows) == 1
    assert m.status is not None
    assert m.feedback.state.phase == "idle"

    def plan_with_progress(context, control):
        control.report(current=1, total=4,
                       message="PRIVATE_ACCOUNT /home/private/book.epub")
        return m.tool.plan(context)

    m.tool.plan_with_progress = plan_with_progress
    m.preview.click()
    args, _kw = m.jobs[-1]
    plan = args[1]()
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert m.feedback.state.phase == "analysis"
    assert label.text == "Traitement en cours · 25 %"
    assert bar.visible and bar.fraction == .25
    assert not spinner.visible
    assert "PRIVATE_" not in label.text
    assert "/home/private" not in label.text

    args[2](plan)
    flush(m)
    assert m.feedback.state.phase == "confirmation"
    dialog = harness.Dialog.shown[-1]
    dialog.handlers["response"](dialog, "run")
    worker_args, _ = m.jobs[-1]
    control = m.host._maintenance_line_rows[0][1]["control"]
    control.report(current=2, total=4,
                   message="PRIVATE_TOKEN sensitive bearer /secret/")
    flush(m)
    assert m.feedback.state.phase == "running"
    assert label.text == "Traitement en cours · 50 %"
    assert "PRIVATE_TOKEN" not in label.text
    # Invalid counts from a future producer are not a credible progress
    # percentage and must not flow into the GTK row unchanged.
    control.report(current=1_000_001, total=1_000_001,
                   message="PRIVATE_TOKEN bogus maximum")
    flush(m)
    assert label.text == "Traitement en cours…"
    assert spinner.visible and not bar.visible
    worker_args[2](NS(ok=True, summary="Terminé"))
    flush(m)
    assert m.feedback.state.phase == "success"
    assert label.text == "Terminé · 100 %"


def test_legacy_unknown_tool_shows_honest_indeterminate_progress_and_safe_failure():
    m = mounted(tool_id="third.party.legacy")
    assert not hasattr(m.tool, "plan_with_progress")
    m.preview.click()
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert m.feedback.state.phase == "analysis"
    assert label.text == "Analyse…"
    assert spinner.visible and not bar.visible
    args, _ = m.jobs[-1]
    args[2](args[1]())
    flush(m)
    dialog = harness.Dialog.shown[-1]
    dialog.handlers["response"](dialog, "run")
    flush(m)
    assert label.text == "Traitement en cours…"
    assert spinner.visible and not bar.visible
    m.tool.run = lambda *_args: (_ for _ in ()).throw(
        ValueError("PRIVATE_ACCOUNT /secret/files.xml")
    )
    worker_args, _ = m.jobs[-1]
    with pytest.raises(RuntimeError) as caught:
        worker_args[1]()
    assert str(caught.value) == "Nettoyage des caches impossible."
    assert "PRIVATE_ACCOUNT" not in str(caught.value)
    worker_args[3](caught.value)
    flush(m)
    assert m.feedback.state.phase == "error"
    assert m.errors[-1][0] == "Nettoyage des caches impossible."


def test_failed_foreign_plugin_description_never_exposes_exception_or_plugin_id():
    method = harness._method()
    namespace = method.production_globals
    namespace["Gtk"].Label = lambda **kw: NS(label=kw.get("label"))
    secret_tool = NS(describe=lambda: (_ for _ in ()).throw(
        ValueError("PRIVATE_PASSWORD /home/sensitive/export.zip")
    ))
    host = NS(
        _closed=False, _tools_box=harness.Container(),
        plugin_manager=NS(services=lambda _name: [
            NS(service=secret_tool, plugin_id="PRIVATE_PLUGIN_ORIGIN")
        ]),
    )
    method(host)
    assert len(host._tools_box.children) == 1
    label = host._tools_box.children[0].label
    assert label == "Outil de maintenance : description indisponible."
    assert "PRIVATE_" not in label
    assert "/home/" not in label
