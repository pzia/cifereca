"""Native GTK4 opt-in diagnostic .brzip end-to-end on fictional local RAM data."""
from __future__ import annotations

from io import BytesIO
import json
from zipfile import ZipFile
import pytest

pytest.importorskip("cryptography")
gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib  # noqa: E402

import browser_recorder_review_ui as ui
from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_private_zip import decrypt_private_zip
from browser_recorder_vault import PrivateSessionVault

ORIGIN = "https://fictional-books.example.org"
PASSWORD = "FICTIONAL-CIFERECA-VAULT-2026"
EXPORT_PASSWORD = "FICTIONAL-SEPARATE-EXPORT-KEY-2026"
ACCOUNT = "acct-FICTIONAL-300"
TOKEN = "FAKE_PRIVATE_BODY_TOKEN_TEST"


class Handle:
    def __init__(self, fn):
        self.fn, self.cb, self.value, self.complete = fn, None, None, False

    def done(self):
        return self.complete

    def add_done_callback(self, callback):
        self.cb = callback

    def result(self):
        assert self.complete
        return self.value

    def finish(self):
        self.value = self.fn(None)
        self.complete = True
        self.cb(self)


class Tasks:
    def __init__(self):
        self.handle = None

    def submit(self, fn, **kw):
        assert kw["kind"] == "browser-recorder"
        self.handle = Handle(fn)
        return self.handle


def flush():
    ctx = GLib.MainContext.default()
    for _ in range(120):
        if not ctx.pending():
            return
        ctx.iteration(False)


def test_real_gtk_private_zip_requires_its_own_confirmation(tmp_path, monkeypatch):
    Gtk.init()
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "POST", ORIGIN + "/api?AccountId=" + ACCOUNT,
        status=200, mime="application/json",
        request_body=json.dumps({"access_token": TOKEN, "AccountId": ACCOUNT}),
        request_body_state="captured",
        response_body='{"ProductId":214,"status":"ready"}',
        response_body_state="captured",
        request_headers={"content-type": "application/json"},
        response_headers={"content-type": "application/json"},
    ))

    def recorder(start, stop, **kwargs):
        assert start == ORIGIN + "/catalog" and stop.is_set()
        writer = kwargs["vault_writer"]
        writer.save(trace, "recording", force=True)
        writer.save(trace, "completed", force=True)
        return trace

    monkeypatch.setattr(ui, "record_http", recorder)
    site = Gtk.Entry()
    site.set_text(ORIGIN + "/catalog")
    consent = Gtk.CheckButton()
    consent.set_active(True)
    tasks = Tasks()
    panel = ui.PrivateReviewPanel(
        Gtk, GLib, tasks, site, consent, lambda: False,
        export_directory=tmp_path / "public",
        vault_directory=tmp_path / "vault",
    )
    window = Gtk.Window()
    window.set_default_size(1024, 768)
    scroller = Gtk.ScrolledWindow()
    scroller.set_child(panel.widget)
    window.set_child(scroller)
    window.present()
    flush()
    try:
        assert not (tmp_path / "vault").exists()
        assert not panel.export_button.get_sensitive()
        assert not panel.private_export_button.get_sensitive()
        panel.start.emit("clicked")
        assert tasks.handle is None  # storage consent required
        panel.vault_consent.set_active(True)
        panel.vault_passphrase.set_text(PASSWORD)
        panel.start.emit("clicked")
        assert tasks.handle is not None
        panel.stop.emit("clicked")
        tasks.handle.finish()
        flush()
        assert trace.vault_session_id
        assert not panel.export_button.get_sensitive()
        assert (tmp_path / "vault" / (trace.vault_session_id + ".cbrv")).is_file()
        panel.sessions_refresh.emit("clicked")
        flush()
        assert panel.session_list.get_active_id() == trace.vault_session_id
        folder = tmp_path / "private-destination"
        folder.mkdir(mode=0o700)
        panel.private_export_directory.set_text(str(folder))
        panel.vault_passphrase.set_text(PASSWORD)
        panel.private_export_passphrase.set_text(EXPORT_PASSWORD)
        panel.private_export_button.emit("clicked")
        assert not list(folder.iterdir())
        assert not panel.private_export_button.get_sensitive()
        panel.private_export_confirm.set_active(True)
        flush()
        assert panel.private_export_button.get_sensitive()
        panel.private_export_button.emit("clicked")
        flush()
        files = list(folder.glob("*.brzip"))
        assert len(files) == 1
        assert files[0].stat().st_mode & 0o777 == 0o600
        with ZipFile(BytesIO(decrypt_private_zip(files[0].read_bytes(), EXPORT_PASSWORD))) as zipped:
            data = json.loads(zipped.read("trace.json"))
            assert data["exchanges"][0]["url"].endswith(ACCOUNT)
            assert TOKEN in data["exchanges"][0]["request_body"]
        if (tmp_path / "public").exists():
            assert not list((tmp_path / "public").glob("*.zip"))
        assert not panel.private_export_confirm.get_active()
        assert not panel.private_export_button.get_sensitive()
        # A second explicit selection + unlock restores a fresh review offline.
        panel.vault_passphrase.set_text(PASSWORD)
        panel.sessions_open.emit("clicked")
        flush()
        assert panel._review is not None and panel._review.pending() > 0
        assert panel._trace.vault_session_id == trace.vault_session_id
        assert len(PrivateSessionVault(tmp_path / "vault").list_ids()) == 1
    finally:
        panel.destroy()
        window.close()
        flush()
