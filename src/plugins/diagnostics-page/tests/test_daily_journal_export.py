"""#3649 — fake GTK native chooser and fake Core broker, no real IO/network."""
from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from diagnostics_today_export import DailyJournalExport


class Window:
    pass


class Button:
    def __init__(self):
        self.root = Window()
        self.label = DailyJournalExport.LABEL

    def connect(self, _signal, callback):
        self.callback = callback

    def get_root(self):
        return self.root

    def set_sensitive(self, enabled):
        self.sensitive = enabled

    def set_tooltip_text(self, value):
        self.tooltip = value

    def set_label(self, value):
        self.label = value


class Dialog:
    def __init__(self, title, parent, action, *_labels):
        self.parent, self.action = parent, action
        self.output = None
        self.destroyed = False

    def set_modal(self, value):
        self.modal = value

    def set_current_folder(self, folder):
        self.folder = folder

    def set_current_name(self, name):
        self.name = name

    def connect(self, _signal, callback):
        self.callback = callback

    def get_file(self):
        return SimpleNamespace(get_path=lambda: self.output)

    def show(self):
        pass

    def destroy(self):
        self.destroyed = True


class Tasks:
    def submit(self, fn, **kwargs):
        self.fn, self.kwargs = fn, kwargs
        self.future = Future()
        return self.future

    def run(self, *, cancelled=False):
        try:
            self.future.set_result(
                self.fn(SimpleNamespace(cancelled=lambda: cancelled)),
            )
        except Exception as exc:
            self.future.set_exception(exc)


@pytest.fixture
def setup(tmp_path):
    state = {"current": True}
    button, tasks = Button(), Tasks()
    host = SimpleNamespace(toast=Mock())
    broker = SimpleNamespace(
        export_today_journal_zip=Mock(side_effect=lambda d, **_: d),
        export_raw_journal=Mock(),
    )
    gtk = SimpleNamespace(
        Window=Window, FileChooserNative=SimpleNamespace(new=Dialog),
        FileChooserAction=SimpleNamespace(SAVE=1),
        ResponseType=SimpleNamespace(ACCEPT=1, CANCEL=0),
    )
    controller = DailyJournalExport(
        button, broker=broker, tasks=tasks, host=host,
        current=lambda: state["current"], folder=lambda: str(tmp_path),
        gtk=gtk, gio=SimpleNamespace(
            File=SimpleNamespace(new_for_path=lambda p: p),
        ), glib=SimpleNamespace(idle_add=lambda fn: fn()),
    )
    return SimpleNamespace(
        controller=controller, button=button, tasks=tasks, host=host,
        broker=broker, state=state, tmp=tmp_path,
    )


def chooser(s):
    s.controller.clicked(s.button)
    assert s.controller.chooser is None
    assert s.button.label == DailyJournalExport.CONFIRM
    assert "cookies" in s.host.toast.call_args.args[0]
    s.controller.clicked(s.button)
    dialog = s.controller.chooser
    assert dialog.parent is s.button.root and dialog.modal is True
    assert dialog.folder == str(s.tmp)
    assert dialog.name.startswith("cifereca-journal-brut-")
    assert dialog.name.endswith(".zip")
    return dialog


def test_primary_zip_confirmation_is_independent_of_journal_filters(setup):
    s = setup
    dialog = chooser(s)
    dialog.output = str(s.tmp / "custom.zip")
    s.controller.chose_file(dialog, 1)
    s.broker.export_today_journal_zip.assert_not_called()
    s.broker.export_raw_journal.assert_not_called()
    assert s.tasks.kwargs["kind"] == "diagnostics"
    s.tasks.run()
    s.broker.export_today_journal_zip.assert_called_once()
    args, kwargs = s.broker.export_today_journal_zip.call_args
    assert args == (str(s.tmp / "custom.zip"),)
    assert callable(kwargs["cancelled"])
    assert dialog.destroyed and s.controller.chooser is None
    assert "exporté en ZIP" in s.host.toast.call_args.args[0]


@pytest.mark.parametrize("change", ["cancel", "detached", "closed", "invalid_suffix", "cancelled_worker"])
def test_cancelled_or_stale_dialog_does_not_access_journal(setup, change):
    s = setup
    dialog = chooser(s)
    dialog.output = str(s.tmp / ("nope.json" if change == "invalid_suffix" else "chosen.zip"))
    if change == "detached":
        s.state["current"] = False
    if change == "closed":
        s.controller.close()
    s.controller.chose_file(dialog, 0 if change == "cancel" else 1)
    if hasattr(s.tasks, "fn") and change == "cancelled_worker":
        s.tasks.run(cancelled=True)
    s.broker.export_today_journal_zip.assert_not_called()
    assert dialog.destroyed


def test_second_click_during_running_export_is_rejected(setup):
    s = setup
    dialog = chooser(s)
    dialog.output = str(s.tmp / "chosen.zip")
    s.controller.chose_file(dialog, 1)
    s.controller.clicked(s.button)
    assert s.controller.chooser is None
    s.tasks.run()
    assert s.broker.export_today_journal_zip.call_count == 1


@pytest.mark.parametrize(("error", "message"), [
    (FileExistsError(), "existe déjà"),
    (PermissionError(), "Accès refusé"),
    (InterruptedError(), "annulé"),
    (ValueError("journal_timezone_unavailable"), "Fuseau local"),
    (OSError("disk full with private path"), "Erreur disque"),
])
def test_real_error_class_remains_actionable_without_echoing_private_paths(
    setup, error, message,
):
    s = setup
    s.broker.export_today_journal_zip.side_effect = error
    dialog = chooser(s)
    dialog.output = str(s.tmp / "chosen.zip")
    s.controller.chose_file(dialog, 1)
    s.tasks.run()
    shown = s.host.toast.call_args.args[0]
    assert message in shown
    assert "private path" not in shown
    assert "exporté en ZIP" not in shown


def test_unsupported_core_disables_zip_without_removing_legacy_json(setup):
    s = setup
    s.broker.export_today_journal_zip = None
    s.controller.refresh()
    assert s.button.sensitive is False
    s.controller.clicked(s.button)
    s.broker.export_raw_journal.assert_not_called()
    assert s.controller.chooser is None
