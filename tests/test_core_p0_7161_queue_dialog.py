"""#7161: real SQLite queue and local confirmation through the actual GUI controller.

The GTK primitives are inert test doubles. No provider or DRM invocation.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.canonical_adept_attempt import begin_loan_adept_attempt
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.loan_download_retry import (
    FAILURE_LIMIT, record_loan_download_failure, read_loan_download_retry,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_convergence_execution import rig
from tests.test_core_p1_7161_loan_download_retry import loan_snapshot
from tests.test_core_p0_3171_atomic_publication_v14 import rows


def load_gui_without_gtk(monkeypatch):
    """Load the controller under its real package but isolate TaskRunner's gi import."""
    import importlib.util
    import sys
    from pathlib import Path
    from types import ModuleType
    from cifereca.task_queue import TaskPriority

    stub = ModuleType("cifereca.gui.task_runner")
    stub.TaskPriority = TaskPriority
    path = (Path(__file__).resolve().parents[1] /
            "src/cifereca/gui/loan_download_retry_dialog.py")
    spec = importlib.util.spec_from_file_location(
        "cifereca.gui._retry_dialog_inert_test", path,
    )
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as local:
        local.setitem(sys.modules, "cifereca.gui.task_runner", stub)
        spec.loader.exec_module(module)
    return module


class Widget:
    def __init__(self, label="", **_kwargs):
        self.label = label
        self.children = []
        self.callbacks = {}
        self.enabled = True
        self.presented = self.closed = False

    def append(self, child):
        self.children.append(child)

    def set_child(self, child):
        self.append(child)

    def set_extra_child(self, child):
        self.append(child)

    def set_label(self, value):
        self.label = value

    def set_sensitive(self, enabled):
        self.enabled = enabled

    def connect(self, name, callback):
        self.callbacks[name] = callback

    def click(self):
        assert self.enabled and "clicked" in self.callbacks
        self.callbacks["clicked"](self)

    def present(self):
        self.presented = True

    def close(self):
        self.closed = True

    def __getattr__(self, name):
        if name in ("set_wrap", "set_policy", "set_max_content_height",
                    "set_propagate_natural_height", "add_css_class",
                    "add_response", "set_visible"):
            return lambda *_a, **_kw: None
        raise AttributeError(name)


class Dialog(Widget):
    @classmethod
    def new(cls, _window, title, body):
        created = cls()
        created.title, created.body = title, body
        return created


def all_widgets(root):
    yield root
    for child in root.children:
        yield from all_widgets(child)


@pytest.mark.parametrize("surface", ["sync", "maintenance"])
@pytest.mark.parametrize("case", ["reset", "route_revoked", "adept_hold"])
def test_sync_retry_dialog_exact_three_attempts_and_safe_individual_action(
    rig, monkeypatch, case, surface,
):
    gui = load_gui_without_gtk(monkeypatch)
    service, account, source, snap, root, lock_path = loan_snapshot(rig)
    service.current_identity = NS(identity_id="A")
    persist_attested_source_references(service.storage, snap)
    service.execute_acquisition_request = lambda *_a, **_kw: pytest.fail("no provider call")
    monkeypatch.setattr(gui, "sync_action_scope_current",
                        lambda window, scope: window.live and
                        window.service.current_identity.identity_id == scope.identity_id)
    monkeypatch.setattr("cifereca.core.loan_download_retry_review.acquisition_lock_file",
                        lambda: lock_path)
    with InterProcessLock(lock_path) as lease:
        for _ in range(FAILURE_LIMIT):
            record_loan_download_failure(
                service.storage, source, reason="canonical_delivery_failed",
                shared_lock=lease,
            )
        if case == "adept_hold":
            begin_loan_adept_attempt(
                service.storage, source=source, offer_id="offer-1",
                shared_lock=lease,
            )
    before = rows(service.storage)
    actions, messages, dialogs = [], [], []

    def submit(label, work, done, **_kw):
        actions.append(label)
        done(work())
        return True

    def dialog_factory(_window, title, body):
        dlg = Dialog.new(_window, title, body)
        dialogs.append(dlg)
        return dlg

    Gtk = NS(ScrolledWindow=Widget, Box=Widget, Label=Widget, Button=Widget,
             Orientation=NS(VERTICAL=1),
             PolicyType=NS(NEVER=0, AUTOMATIC=1))
    Adw = NS(MessageDialog=NS(new=dialog_factory))
    window = NS(
        service=service, live=True,
        runner=NS(submit=submit),
        toast=lambda message: messages.append(message),
    )
    refreshes = []
    window._refresh_sync_page = lambda: refreshes.append(True)
    if surface == "sync":
        gui.open_loan_download_retry_dialog(
            window, NS(identity_id="A"), Gtk=Gtk, Adw=Adw, control=Widget(),
        )
    else:
        from cifereca.plugin_api.page_lifecycle import PageViewContext
        from tests.test_core_ui_1358_maintenance_preview_cancellation import Button, Container
        window._closed = False
        window._identity_context_generation = 1
        window._maintenance_mount_owner = object()
        window._guided_maintenance_disposers = []
        window.action_button = lambda label, icon: Button(label)
        Adw.PreferencesGroup = Adw.ActionRow = Container
        view = PageViewContext("tools", "A", None, 1, "show", lambda _: window.live)
        box = Container()
        assert gui.add_maintenance_retry_entry(window, box, view, Gtk=Gtk, Adw=Adw)
        assert not actions
        entry, = box.children[0].children[0].children
        entry.click()
    dlg, = dialogs
    assert dlg.presented and dlg.title == "Téléchargements à reprendre"
    expected_pause = 0 if case == "adept_hold" else 1
    assert "1 téléchargement(s)" in dlg.body and f"{expected_pause} en pause" in dlg.body
    widgets = tuple(all_widgets(dlg))
    texts = [item.label for item in widgets if type(item.label) is str]
    assert any("3/3 échec(s)" in value and "En pause" in value for value in texts
               ) if case != "adept_hold" else any(
                   "Intervention ADEPT requise" in value for value in texts)
    assert all("PRIVATE_" not in value for value in texts)
    buttons = [item for item in widgets
               if item.label == "Réessayer à la prochaine synchronisation"]
    assert len(buttons) == (0 if case == "adept_hold" else 1)
    if case == "reset":
        buttons[0].click()
        assert dlg.closed and messages == ["Tentatives réinitialisées"]
        assert refreshes == ([True] if surface == "sync" else [])
        assert read_loan_download_retry(service.storage, source).failures == 0
    elif case == "route_revoked":
        window.live = False
        buttons[0].click()
        assert not dlg.closed and not messages and not refreshes
    else:
        assert not messages and not refreshes
    assert rows(service.storage) == before
    if case != "reset":
        assert read_loan_download_retry(service.storage, source).paused
    assert service.calls == 0
