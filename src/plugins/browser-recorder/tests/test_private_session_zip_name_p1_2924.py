"""#2924 — suggested ZIP name comes solely from authenticated private metadata."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

import browser_recorder_review_ui as ui
from tests.test_browser_recorder_review_ui import Gtk, GLib, FakeTasks, ORIGIN


SECRET = "SYNTHETIC-TEST-KEY-PASSPHRASE"


def panel(tmp_path):
    return ui.PrivateReviewPanel(
        Gtk, GLib, FakeTasks(), NS(get_text=lambda: ORIGIN),
        NS(get_active=lambda: True), lambda: False,
        vault_directory=tmp_path / "vault",
    )


@pytest.mark.parametrize("label,expected", [
    ("Parcours catalogue BnF", "cifereca-session-privee-parcours-catalogue-bnf.zip"),
    ("Été à Paris : prêt EPUB !", "cifereca-session-privee-ete-a-paris-pret-epub.zip"),
    ("../secret\\password./", "cifereca-session-privee-secret-password.zip"),
    ("\u202e../../", "cifereca-session-privee.zip"),
    ("   ", "cifereca-session-privee.zip"),
    ("🪄 東京", "cifereca-session-privee.zip"),
    ("A" * 80, "cifereca-session-privee-" + "a" * 64 + ".zip"),
    ("A\u200dB\u0000C", "cifereca-session-privee-abc.zip"),
    ("", "cifereca-session-privee.zip"),
    (None, "cifereca-session-privee.zip"),
])
def test_portable_suggestion_never_contains_path_traversal(label, expected):
    suggested = ui._private_zip_name(label)
    assert suggested == expected
    assert len(suggested) <= len("cifereca-session-privee-") + 64 + 4
    assert "/" not in suggested and "\\" not in suggested and ".." not in suggested
    assert suggested.endswith(".zip")


def test_name_source_is_exact_exported_session_not_selected_caption(monkeypatch, tmp_path):
    view = panel(tmp_path)
    aa, bb = "a" * 32, "b" * 32
    view.session_list.append(aa, "PRIVATE_CAPTION_NOT_A_SOURCE")
    view.session_list.append(bb, "PRIVATE_OTHER_CAPTION")
    view.session_list.set_active_id(aa)
    view._vault_secret = bytearray(SECRET.encode("utf-8"))
    view._private_labels = {
        aa: "Première session", bb: "Deuxième BnF privée",
    }
    class Vault:
        def __init__(self, path): pass
        def open(self, sid, secret):
            assert secret == SECRET
            assert sid in {aa, bb}
            return NS(trace=NS(destroy=lambda: None))
    monkeypatch.setattr(ui, "PrivateSessionVault", Vault)
    monkeypatch.setattr(ui, "save_session_llm_zip", lambda *a, **k: 99)

    names = []
    view._prompt_private_zip_file = lambda selected, cancelled: names.append(
        view._private_export_suggested_name
    )
    try:
        view.export_private_zip()
        assert names[-1] == "cifereca-session-privee-premiere-session.zip"
        view._trace = NS(vault_session_id=bb, destroy=lambda: None)
        view.export_private_zip(current=True)
        assert names[-1] == "cifereca-session-privee-deuxieme-bnf-privee.zip"
        view.session_list.set_active_id(bb)
        view.export_private_zip()
        assert names[-1] == "cifereca-session-privee-deuxieme-bnf-privee.zip"
        assert all("CAPTION" not in s and "PRIVATE" not in s for s in names)
    finally:
        view.destroy()
    assert view._private_labels == {}
    assert view._private_export_suggested_name == ui._PRIVATE_ZIP_DEFAULT


def test_opaque_refresh_lock_and_missing_label_clear_names(monkeypatch, tmp_path):
    view = panel(tmp_path)
    sid = "1" * 32
    class Vault:
        def __init__(self, path): pass
        def list_ids(self):
            return (sid,)
    monkeypatch.setattr(ui, "PrivateSessionVault", Vault)
    try:
        view._private_labels[sid] = "Sensitive-title"
        assert view.refresh_sessions()
        assert view._private_labels == {}
        assert view._private_export_suggested_name == ui._PRIVATE_ZIP_DEFAULT
        view._private_labels[sid] = "Sensitive-title"
        view._vault_secret = bytearray(SECRET.encode("utf-8"))
        view.lock_vault()
        assert view._private_labels == {}
        assert view._vault_secret is None
    finally:
        view.destroy()


def test_save_as_chooser_receives_suggestion_and_user_can_cancel(tmp_path):
    view = panel(tmp_path)
    calls = []
    class Chooser:
        def set_current_name(self, value):
            calls.append(("suggested", value))
        def connect(self, name, callback):
            calls.append(("signal", name))
            self.responded = callback
        def show(self):
            calls.append(("shown", None))
        def destroy(self):
            calls.append(("destroyed", None))
    chooser = Chooser()
    class GtkWithNative:
        Window = Gtk.Widget
        FileChooserAction = NS(SAVE=1)
        ResponseType = NS(ACCEPT=2, CANCEL=3)
        FileChooserNative = NS(new=lambda *args: chooser)
    view.Gtk = GtkWithNative
    view._private_export_suggested_name = ui._private_zip_name("Ma session BnF")
    view.widget.get_root = lambda: None
    selected, cancelled = [], []
    try:
        view._prompt_private_zip_file(selected.append, cancelled.append)
        assert calls[0] == ("suggested", "cifereca-session-privee-ma-session-bnf.zip")
        chooser.responded(chooser, GtkWithNative.ResponseType.CANCEL)
        assert not selected and cancelled == [True]
        assert view._private_destination_dialog is None
    finally:
        view.destroy()
