"""Execute the export controller: consent, Trace gates, lifetime and errors."""
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from diagnostics_http_export import HTTPTraceExport


class Button:
    def __init__(self):
        self.root = Window()
        self.label = HTTPTraceExport.LABEL

    def connect(self, _signal, fn):
        self.callback = fn

    def get_root(self):
        return self.root

    def set_sensitive(self, value):
        self.sensitive = value

    def set_tooltip_text(self, value):
        self.tooltip = value

    def set_label(self, value):
        self.label = value


class Window:
    pass


class Dialog:
    def __init__(self, _title, parent, action, *_labels):
        self.parent, self.action = parent, action
        self.output = None
        self.destroyed = False

    def set_modal(self, value):
        self.modal = value

    def set_current_folder(self, value):
        self.folder = value

    def set_current_name(self, value):
        self.name = value

    def connect(self, _signal, fn):
        self.callback = fn

    def show(self):
        pass

    def get_file(self):
        return SimpleNamespace(get_path=lambda: self.output)

    def destroy(self):
        self.destroyed = True


class Tasks:
    def submit(self, fn, **kwargs):
        self.kwargs = kwargs
        self.fn = fn
        self.future = Future()
        return self.future

    def run(self, *, cancelled=False):
        try:
            result = self.fn(SimpleNamespace(cancelled=lambda: cancelled))
            self.future.set_result(result)
        except Exception as exc:
            self.future.set_exception(exc)


@pytest.fixture
def setup(tmp_path):
    state = {'active': True, 'current': True}
    button, tasks, host = Button(), Tasks(), SimpleNamespace(toast=Mock())
    broker = SimpleNamespace(http_trace_status=lambda: {'active':state['active']},
                             export_http_trace=Mock(return_value='unused'))
    glib = SimpleNamespace(timeout_add=Mock(return_value=7), source_remove=Mock(),
                            idle_add=lambda fn: fn())
    gtk = SimpleNamespace(Window=Window, FileChooserNative=SimpleNamespace(new=Dialog),
                          FileChooserAction=SimpleNamespace(SAVE=1),
                          ResponseType=SimpleNamespace(ACCEPT=1, CANCEL=0))
    export = HTTPTraceExport(button, broker=broker, tasks=tasks, host=host,
                             current=lambda:state['current'],folder=lambda:str(tmp_path),
                             gtk=gtk,gio=SimpleNamespace(File=SimpleNamespace(new_for_path=lambda p:p)),
                             glib=glib)
    return SimpleNamespace(export=export, button=button, broker=broker, tasks=tasks,
                           host=host, state=state, glib=glib, tmp=tmp_path)


def open_chooser(s):
    s.export.clicked(s.button)
    assert s.export.chooser is None
    assert s.button.label == HTTPTraceExport.CONFIRM
    s.broker.export_http_trace.assert_not_called()
    s.export.clicked(s.button)
    dialog = s.export.chooser
    assert dialog.parent is s.button.root and dialog.modal
    assert dialog.name == 'cifereca-echanges-http.zip'
    return dialog


def test_trace_off_disables_button_and_revokes_confirmation(setup):
    s=setup
    s.export.clicked(s.button)
    s.state['active'] = False
    s.export.refresh()
    assert not s.button.sensitive and 'Trace inactif' in s.button.tooltip
    assert not s.export.armed
    s.export.clicked(s.button)
    assert s.export.chooser is None
    s.broker.export_http_trace.assert_not_called()
    s.state['active'] = True
    s.export.refresh()
    assert s.button.sensitive


def test_cancel_does_not_submit_or_export(setup):
    s=setup
    dialog=open_chooser(s)
    s.export.chose_file(dialog,0)
    assert dialog.destroyed and s.export.chooser is None
    assert not hasattr(s.tasks,'fn')
    s.broker.export_http_trace.assert_not_called()


def test_confirmation_uses_only_http_broker_and_exact_selected_path(setup):
    s=setup
    dialog=open_chooser(s)
    dialog.output=str(s.tmp/'chosen.zip')
    s.export.chose_file(dialog,1)
    s.broker.export_http_trace.assert_not_called()  # worker, not GTK thread
    assert s.tasks.kwargs['kind'] == 'diagnostics'
    s.tasks.run()
    s.broker.export_http_trace.assert_called_once_with(dialog.output)
    assert s.export.chooser is None and dialog.destroyed
    assert 'exportés' in s.host.toast.call_args.args[0]


@pytest.mark.parametrize('change', ['trace_off','detached','cancelled','closed'])
def test_invalidation_before_response_or_worker_prevents_export(setup,change):
    s=setup
    dialog=open_chooser(s)
    dialog.output=str(s.tmp/'chosen.zip')
    if change == 'trace_off':s.state['active']=False
    if change == 'detached':s.state['current']=False
    if change == 'closed':s.export.close()
    s.export.chose_file(dialog,1)
    if hasattr(s.tasks,'fn'):
        s.tasks.run(cancelled=change == 'cancelled')
    s.broker.export_http_trace.assert_not_called()


@pytest.mark.parametrize('exception', [FileExistsError('exists'), OSError('disk full')])
def test_export_failure_is_reported_and_never_claims_success(setup,exception):
    s=setup
    s.broker.export_http_trace.side_effect=exception
    dialog=open_chooser(s)
    dialog.output=str(s.tmp/'chosen.zip')
    s.export.chose_file(dialog,1)
    s.tasks.run()
    assert 'exportés' not in s.host.toast.call_args.args[0]
    assert s.export.chooser is None and dialog.destroyed


def test_nonlocal_destination_and_stale_response_never_write(setup):
    s=setup
    dialog=open_chooser(s)
    dialog.output=None
    s.export.chose_file(dialog,1)
    assert 'locale' in s.host.toast.call_args.args[0]
    dialog.output=str(s.tmp/'stale.zip')
    s.export.chose_file(dialog,1)
    assert not hasattr(s.tasks,'fn')


def test_close_revokes_dialog_and_timer(setup):
    s=setup
    dialog=open_chooser(s)
    s.export.close()
    s.export.close()
    assert dialog.destroyed and s.export.chooser is None
    assert s.export.refresh() is False
    s.glib.source_remove.assert_called_once_with(7)
