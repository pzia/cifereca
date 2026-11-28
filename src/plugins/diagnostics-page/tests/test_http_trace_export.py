"""Execute the export controller: consent, Trace gates, lifetime and errors."""
from concurrent.futures import Future
from datetime import datetime as RealDateTime
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import diagnostics_http_export as http_export
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
    assert re.fullmatch(r'cifereca-echanges-http-\d{8}-\d{6}\.zip', dialog.name)
    return dialog


def test_filename_uses_local_click_time_and_refreshes_on_each_open(setup, monkeypatch):
    class FrozenDateTime:
        instants = [
            RealDateTime(2026, 11, 3, 23, 59, 59),
            RealDateTime(2026, 11, 4, 0, 0, 0),
        ]

        @classmethod
        def now(cls):
            return cls.instants.pop(0)

    monkeypatch.setattr(http_export, "datetime", FrozenDateTime)
    s = setup
    # Construction and first confirmation must not consume the time.
    assert len(FrozenDateTime.instants) == 2
    s.export.clicked(s.button)
    assert s.export.chooser is None
    assert len(FrozenDateTime.instants) == 2
    s.export.clicked(s.button)
    first = s.export.chooser
    assert first.name == "cifereca-echanges-http-20261103-235959.zip"
    assert first.folder == str(s.tmp)
    s.export.chose_file(first, 0)  # Cancel: no write or worker.
    assert first.destroyed and s.export.chooser is None
    s.broker.export_http_trace.assert_not_called()
    second = open_chooser(s)
    assert second.name == "cifereca-echanges-http-20261104-000000.zip"
    assert second.folder == str(s.tmp)
    assert len(FrozenDateTime.instants) == 0
    s.export.chose_file(second, 0)
    assert not hasattr(s.tasks, "fn")


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


class CheckButton(Button):
    def __init__(self):
        super().__init__()
        self.active = False

    def set_active(self, value):
        if self.active != value:
            self.active = value
            self.callback(self)

    def get_active(self):
        return self.active


def test_binary_policy_is_core_owned_independent_of_trace_and_revokes_consent(setup):
    s = setup
    s.export.close()
    policy = {"include_binary": True}
    s.broker.http_trace_status = lambda: {"active": s.state["active"], **policy}
    s.broker.set_http_trace_include_binary = Mock(
        side_effect=lambda value: policy.update(include_binary=value))
    checkbox = CheckButton()
    s.export = HTTPTraceExport(
        s.button, broker=s.broker, tasks=s.tasks, host=s.host,
        current=lambda: s.state["current"], folder=lambda: str(s.tmp),
        gtk=s.export.gtk, gio=s.export.gio, glib=s.glib, include_binary=checkbox,
    )
    assert checkbox.active and checkbox.sensitive
    s.broker.set_http_trace_include_binary.assert_not_called()
    dialog = open_chooser(s)
    checkbox.set_active(False)
    assert dialog.destroyed and s.export.chooser is None
    assert policy["include_binary"] is False
    s.state["active"] = False
    s.export.refresh()
    assert checkbox.sensitive and not s.button.sensitive
    checkbox.set_active(True)
    assert policy["include_binary"] is True
    s.broker.set_http_trace_include_binary.side_effect = RuntimeError("PRIVATE")
    checkbox.set_active(False)
    assert checkbox.active and "PRIVATE" not in s.host.toast.call_args.args[0]
    s.export.close()
    assert not checkbox.sensitive


def test_binary_policy_is_unavailable_on_legacy_core(setup):
    s = setup
    s.export.close()
    checkbox = CheckButton()
    s.export = HTTPTraceExport(
        s.button, broker=s.broker, tasks=s.tasks, host=s.host,
        current=lambda: s.state["current"], folder=lambda: str(s.tmp),
        gtk=s.export.gtk, gio=s.export.gio, glib=s.glib, include_binary=checkbox,
    )
    assert checkbox.active and not checkbox.sensitive
    assert s.button.sensitive


@pytest.mark.parametrize("change", ["none", "selection", "policy", "revoked", "invalidated"])
def test_scoped_archive_pins_exact_scope_and_revokes_queued_consent(setup, change):
    from cifereca.plugin_api.capture_vault import CaptureScope
    from diagnostics_http_export import ScopedHTTPArchive
    s = setup
    s.export.close()
    scope = CaptureScope("fixture", "instance", "account", "owner")
    selected = ScopedHTTPArchive("capture", scope, "loans", "evidence")
    state = {"selected": selected, "policy": True, "authorized": True}
    s.broker.http_trace_status = lambda: {"active": False, "include_binary": state["policy"]}
    vault = SimpleNamespace(export_http_archive=Mock())
    s.export = HTTPTraceExport(
        s.button, broker=s.broker, tasks=s.tasks, host=s.host,
        current=lambda: s.state["current"], folder=lambda: str(s.tmp),
        gtk=s.export.gtk, gio=s.export.gio, glib=s.glib, vault=vault,
        archive_source=lambda: state["selected"],
        archive_current=lambda token: token == state["selected"] and state["authorized"],
    )
    dialog = open_chooser(s)
    dialog.output = str(s.tmp / "private.zip")
    s.export.chose_file(dialog, 1)
    if change == "selection":
        state["selected"] = ScopedHTTPArchive(
            "capture", CaptureScope("fixture", "instance", "other", "owner"),
            "loans", "evidence")
        s.export.refresh()
    elif change == "policy":
        state["policy"] = False
    elif change == "revoked":
        state["authorized"] = False
    elif change == "invalidated":
        s.export.invalidate()
    s.tasks.run()
    if change == "none":
        vault.export_http_archive.assert_called_once_with(
            "capture", dialog.output, confirmed=True, scope=scope)
    else:
        vault.export_http_archive.assert_not_called()
    s.broker.export_http_trace.assert_not_called()


def test_manual_archive_keeps_legacy_export_signature(setup):
    s = setup
    s.export.close()
    s.broker.http_trace_status = lambda: {"active": False, "include_binary": True}
    vault = SimpleNamespace(export_http_archive=Mock())
    s.export = HTTPTraceExport(
        s.button, broker=s.broker, tasks=s.tasks, host=s.host,
        current=lambda: s.state["current"], folder=lambda: str(s.tmp),
        gtk=s.export.gtk, gio=s.export.gio, glib=s.glib, vault=vault,
        archive_source=lambda: "manual-capture",
    )
    dialog = open_chooser(s)
    dialog.output = str(s.tmp / "manual.zip")
    s.export.chose_file(dialog, 1)
    s.tasks.run()
    vault.export_http_archive.assert_called_once_with(
        "manual-capture", dialog.output, confirmed=True)
