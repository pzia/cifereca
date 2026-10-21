"""Real GTK4 offline session-index unlock and cancelled async delivery."""
from __future__ import annotations

import pytest
pytest.importorskip("cryptography")
gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_review_ui import PrivateReviewPanel
from browser_recorder_vault import PrivateSessionVault

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
        assert label == "Indexer le coffre privé"
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
    for index, passphrase in enumerate((KEY, KEY, OTHER)):
        trace = PrivateHttpTrace((ORIGIN,))
        trace.exchanges.append(PrivateExchange(
            1, "GET", ORIGIN + "/catalog?private_id=FICTIONAL-" + str(index),
            status=200,
        ))
        writer = vault.begin(passphrase)
        writer.save(trace, "completed" if index != 1 else "recording",
                    force=True)
        ids.append(writer.session_id)
        trace.destroy()
    return ids


def panel_fixture(tmp_path):
    Gtk.init()
    vault = PrivateSessionVault(tmp_path / "vault")
    ids = populate(vault)
    site = Gtk.Entry()
    site.set_text(ORIGIN)
    consent = Gtk.CheckButton()
    tasks = Tasks()
    panel = PrivateReviewPanel(
        Gtk, GLib, tasks, site, consent, lambda: False,
        vault_directory=vault.path,
    )
    window = Gtk.Window()
    window.set_default_size(1024, 768)
    scroll = Gtk.ScrolledWindow()
    scroll.set_child(panel.widget)
    window.set_child(scroll)
    window.present()
    flush()
    return panel, tasks, window, vault, ids


def test_gtk_metadata_stays_opaque_until_unlock_then_reveals_private_labels(tmp_path):
    panel, tasks, window, vault, ids = panel_fixture(tmp_path)
    try:
        panel.sessions_refresh.emit("clicked")
        flush()
        assert "3 sessions chiffrées" in panel.status.get_label()
        assert ORIGIN not in panel.status.get_label()
        assert all(ORIGIN not in panel.session_list.get_active_text()
                   for _ in range(1))
        panel.vault_passphrase.set_text(KEY)
        panel.sessions_unlock.emit("clicked")
        assert tasks.handle is not None
        assert panel.is_scanning()
        assert panel.vault_passphrase.get_text() == ""
        assert not panel.sessions_unlock.get_sensitive()
        assert not panel.start.get_sensitive()
        tasks.handle.finish()
        flush()
        assert not panel.is_scanning()
        assert "2 sessions déverrouillées" in panel.status.get_label()
        assert "1 indisponibles" in panel.status.get_label()
        for identity in ids:
            panel.session_list.set_active_id(identity)
            label = panel.session_list.get_active_text()
            assert label is not None
            if identity == ids[2]:
                assert "verrouillée/illisible" in label and ORIGIN not in label
            else:
                assert ORIGIN in label
                assert "échanges" in label
        panel.session_list.set_active_id(ids[1])
        assert "partial" in panel.session_list.get_active_text()
        panel.session_list.set_active_id(ids[0])
        assert "completed" in panel.session_list.get_active_text()
        assert vault.list_ids() and len(vault.list_ids()) == 3
        assert not list(tmp_path.rglob("*.zip"))
    finally:
        panel.destroy()
        window.close()
        flush()


def test_scanning_completion_after_page_destroy_cannot_reveal_session(tmp_path):
    panel, tasks, window, vault, ids = panel_fixture(tmp_path)
    panel.sessions_refresh.emit("clicked")
    panel.vault_passphrase.set_text(KEY)
    panel.sessions_unlock.emit("clicked")
    assert panel.is_scanning()
    panel.destroy()
    tasks.handle.finish()
    flush()
    assert len(vault.list_ids()) == 3
    assert panel.vault_passphrase.get_text() == ""
    window.close()
    flush()
