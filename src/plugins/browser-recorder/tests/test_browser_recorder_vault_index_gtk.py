"""Real GTK4 common-vault session index, restart, pins and cancelled delivery."""
from __future__ import annotations

import pytest
pytest.importorskip("cryptography")
gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_review_ui import PrivateReviewPanel
from browser_recorder_vault import RecorderVault
from cifereca.core.capture_vault_service import CoreCaptureVault
from cifereca.plugin_api.capture_vault import CaptureScope, VaultError

ORIGIN = "https://synthetic-library.example.org"
KEY = "ONLY-FICTIONAL-VISIBLE-METADATA-KEY"
OTHER = "OTHER-FICTIONAL-INDEPENDENT-KEY"


class Handle:
    def __init__(self, work):
        self.work, self.callback = work, None
        self.ready, self.value = False, None

    def done(self):
        return self.ready

    def add_done_callback(self, callback):
        self.callback = callback

    def result(self):
        assert self.ready
        return self.value

    def finish(self):
        self.value = self.work(None)
        self.ready = True
        self.callback(self)


class Tasks:
    def __init__(self):
        self.handle = None

    def submit(self, fn, *, kind, label):
        assert kind == "browser-recorder"
        assert label in {"Indexer le coffre privé", "Nommer la session chiffrée", "Modifier l\'épingle du coffre"}
        self.handle = Handle(fn)
        return self.handle


def flush():
    context = GLib.MainContext.default()
    for _ in range(120):
        if not context.pending():
            return
        context.iteration(False)


def populate(vault):
    ids = []
    for i, state in enumerate(("completed", "recording", "interrupted")):
        trace = PrivateHttpTrace((ORIGIN,))
        trace.exchanges.append(PrivateExchange(
            1, "GET", ORIGIN + "/catalog?private_id=FICTIONAL-" + str(i),
            status=200,
        ))
        writer = vault.begin(consented=True)
        writer.save(trace, state, force=True)
        ids.append(writer.session_id)
        trace.destroy()
    return ids


def core_fixture(tmp_path):
    return CoreCaptureVault(tmp_path / "vault", tmp_path / "keys",
                            authorize_scope=lambda *_: True)


def panel_fixture(tmp_path):
    Gtk.init()
    initial = core_fixture(tmp_path)
    ids = populate(RecorderVault(initial.for_plugin("ui.page.browser-recorder")))
    initial.close()
    core = core_fixture(tmp_path)
    vault = RecorderVault(core.for_plugin("ui.page.browser-recorder"))
    site = Gtk.Entry()
    site.set_text(ORIGIN)
    consent = Gtk.CheckButton()
    tasks = Tasks()
    panel = PrivateReviewPanel(
        Gtk, GLib, tasks, site, consent, lambda: False, vault=vault,
    )
    window = Gtk.Window()
    window.set_default_size(1024, 768)
    scroll = Gtk.ScrolledWindow()
    scroll.set_child(panel.widget)
    window.set_child(scroll)
    window.present()
    flush()
    return panel, tasks, window, core, ids


def test_gtk_restart_lists_and_reopens_without_unlock(tmp_path):
    panel, tasks, window, core, ids = panel_fixture(tmp_path)
    try:
        for name in ("vault_passphrase", "vault_open_button", "vault_close_button",
                     "sessions_unlock", "_vault_secret"):
            assert not hasattr(panel, name)
        panel.sessions_refresh.emit("clicked")
        assert panel.is_scanning()
        assert not panel.start.get_sensitive()
        assert not panel.private_export_button.get_sensitive()
        tasks.handle.finish()
        assert panel.is_scanning()  # until GTK idle delivery
        flush()
        assert not panel.is_scanning()
        assert "3 sessions chiffrées" in panel.status.get_label()
        for i, sid in enumerate(ids):
            panel.session_list.set_active_id(sid)
            assert ORIGIN in panel.session_list.get_active_text()
            assert ("completed", "partial", "interrupted")[i] in panel.session_list.get_active_text()
            panel.sessions_open.emit("clicked")
            assert panel._trace.vault_session_id == sid
        assert not list(tmp_path.rglob("*.zip"))
        panel.session_list.set_active_id(ids[0])
        panel.session_name.set_text("Mon parcours fictif privé")
        panel.sessions_rename.emit("clicked")
        tasks.handle.finish()
        flush()
        tasks.handle.finish()
        flush()
        assert "Mon parcours fictif privé" in panel.session_list.get_active_text()
        panel.sessions_pin.emit("clicked")
        tasks.handle.finish()
        flush()
        tasks.handle.finish()
        flush()
        assert "épinglée" in panel.session_list.get_active_text()
        panel.sessions_unpin.emit("clicked")
        tasks.handle.finish()
        flush()
        tasks.handle.finish()
        flush()
        assert "épinglée" not in panel.session_list.get_active_text()
        panel.sessions_delete.emit("clicked")
        assert len(panel.vault.sessions()) == 3
        panel.delete_confirm.set_active(True)
        panel.sessions_delete.emit("clicked")
        tasks.handle.finish()
        flush()
        assert len(panel.vault.sessions()) == 2
    finally:
        panel.destroy()
        core.close()
        window.close()
        flush()


def test_gtk_key_loss_refuses_read_and_new_capture(tmp_path):
    panel, tasks, window, core, ids = panel_fixture(tmp_path)
    try:
        panel.sessions_refresh.emit("clicked")
        tasks.handle.finish()
        flush()
        panel.session_list.set_active_id(ids[0])
        (tmp_path / "keys" / "key").unlink()
        panel.sessions_open.emit("clicked")
        assert "Ouverture refusée" in panel.status.get_label()
        assert panel._trace is None
        with pytest.raises(VaultError, match="vault_key_unavailable"):
            panel.vault.begin(consented=True)
        assert not (tmp_path / "keys" / "key").exists()
        assert len(list((tmp_path / "vault").glob("*.cbrv"))) == 3
    finally:
        panel.destroy()
        core.close()
        window.close()
        flush()


def test_scanning_completion_after_page_destroy_cannot_reveal_session(tmp_path):
    panel, tasks, window, core, ids = panel_fixture(tmp_path)
    try:
        panel.sessions_refresh.emit("clicked")
        assert panel.is_scanning()
        panel.destroy()
        tasks.handle.finish()
        flush()
        assert panel.session_list.get_active_id() is None
        assert panel._private_labels == {}
        assert ORIGIN not in panel.status.get_label()
    finally:
        core.close()
        window.close()
        flush()


def test_gtk_manual_download_chooser_requires_actual_operator_selection(
    tmp_path, monkeypatch,
):
    """Real GTK event loop; fake native chooser response (no OS chooser automation).

    Browser-controlled URL/suggested filename is never used to populate UI.
    No file is saved until a distinct explicit Save-As selection is returned.
    """
    from threading import Thread
    from time import monotonic, sleep
    from types import SimpleNamespace as NS

    panel, tasks, window, vault, ids = panel_fixture(tmp_path)
    dialogs = []

    class Dialog:
        def __init__(self, label):
            self.title = label
            self.name = None
            self.callback = None
            self.destroyed = False

        def set_current_name(self, name):
            self.name = name

        def connect(self, event, callback):
            assert event == "response"
            self.callback = callback

        def show(self):
            assert self.callback is not None
            dialogs.append(self)

        def get_file(self):
            return NS(get_path=lambda: str(tmp_path / "chosen-fictional.acsm"))

        def destroy(self):
            self.destroyed = True

        def reply(self, action):
            self.callback(self, action)

    facade = NS(
        Window=Gtk.Window,
        FileChooserAction=Gtk.FileChooserAction,
        ResponseType=Gtk.ResponseType,
        FileChooserNative=NS(new=lambda label, *_args: Dialog(label)),
    )
    monkeypatch.setattr(panel, "Gtk", facade)
    responses = []
    def choose():
        responses.append(panel._choose_download_from_worker(1))

    try:
        thread = Thread(target=choose, daemon=True)
        thread.start()
        limit = monotonic() + 5
        while not dialogs and monotonic() < limit:
            flush()
            sleep(.01)
        assert len(dialogs) == 1
        one = dialogs.pop()
        assert one.name == "cifereca-telechargement-01.bin"
        assert "fictional" not in one.title.lower()
        assert thread.is_alive()
        assert not list(tmp_path.glob("*.acsm"))
        one.reply(Gtk.ResponseType.CANCEL)
        thread.join(timeout=5)
        assert not thread.is_alive() and responses == [None]
        assert one.destroyed and not list(tmp_path.glob("*.acsm"))
        responses.clear()

        thread2 = Thread(target=choose, daemon=True)
        thread2.start()
        limit = monotonic() + 5
        while not dialogs and monotonic() < limit:
            flush()
            sleep(.01)
        assert len(dialogs) == 1
        two = dialogs.pop()
        two.reply(Gtk.ResponseType.ACCEPT)
        thread2.join(timeout=5)
        assert not thread2.is_alive()
        assert responses == [tmp_path / "chosen-fictional.acsm"]
        assert two.destroyed
        assert not list(tmp_path.glob("*.acsm"))  # chooser ≠ write
    finally:
        panel.destroy()
        vault.close()
        window.close()
        flush()


def test_gtk_closing_page_cancels_pending_manual_download_chooser(
    tmp_path, monkeypatch,
):
    from threading import Thread
    from time import monotonic, sleep
    from types import SimpleNamespace as NS

    panel, tasks, window, vault, ids = panel_fixture(tmp_path)
    dialogs = []

    class Dialog:
        def set_current_name(self, name):
            assert name == "cifereca-telechargement-01.bin"

        def connect(self, event, callback):
            self.respond = callback

        def show(self):
            dialogs.append(self)

        def destroy(self):
            self.destroyed = True

    facade = NS(
        Window=Gtk.Window,
        FileChooserAction=Gtk.FileChooserAction,
        ResponseType=Gtk.ResponseType,
        FileChooserNative=NS(new=lambda *_args: Dialog()),
    )
    monkeypatch.setattr(panel, "Gtk", facade)
    results = []
    thread = Thread(
        target=lambda: results.append(panel._choose_download_from_worker(1)),
        daemon=True,
    )
    try:
        thread.start()
        stop_at = monotonic() + 5
        while not dialogs and monotonic() < stop_at:
            flush()
            sleep(.01)
        assert len(dialogs) == 1 and thread.is_alive()
        panel.destroy()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert results == [None]
        assert dialogs[0].destroyed
        assert not list(tmp_path.glob("*.acsm"))
    finally:
        panel.destroy()
        vault.close()
        window.close()
        flush()
