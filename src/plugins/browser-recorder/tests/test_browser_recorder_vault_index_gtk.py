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
        assert label in {"Indexer le coffre privé", "Nommer la session chiffrée", "Nettoyer les résidus chiffrés", "Prévisualiser la rétention du coffre", "Appliquer la rétention consentie"}
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
        panel.session_list.set_active_id(ids[0])
        panel.session_name.set_text("Mon parcours fictif privé")
        panel.vault_passphrase.set_text(KEY)
        panel.sessions_rename.emit("clicked")
        assert panel.is_scanning()
        assert panel.session_name.get_text() == ""
        assert panel.vault_passphrase.get_text() == ""
        tasks.handle.finish()
        flush()
        assert not panel.is_scanning()
        assert vault.get_label(ids[0], KEY) == "Mon parcours fictif privé"
        # Opaque after rename; explicit unlock is needed again.
        assert ORIGIN not in panel.session_list.get_active_text()
        panel.vault_passphrase.set_text(KEY)
        panel.sessions_unlock.emit("clicked")
        tasks.handle.finish()
        flush()
        panel.session_list.set_active_id(ids[0])
        assert "Mon parcours fictif privé" in panel.session_list.get_active_text()
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



def test_gtk_orphan_preview_never_deletes_without_distinct_confirmation(tmp_path):
    import os
    import time
    from browser_recorder_vault import MIN_STAGING_AGE_SECONDS

    panel, tasks, window, vault, ids = panel_fixture(tmp_path)
    try:
        source = vault.path / (ids[0] + ".cbrv")
        ciphertext = source.read_bytes()
        staged = vault.path / ("." + ids[0] + "-abcdef0123456789.part")
        staged.write_bytes(ciphertext)
        staged.chmod(0o600)
        old = time.time() - MIN_STAGING_AGE_SECONDS - 100
        os.utime(staged, (old, old))
        before = {identity: (vault.path / (identity + ".cbrv")).read_bytes()
                  for identity in ids}
        assert not panel.staging_prune.get_sensitive()
        panel.staging_inspect.emit("clicked")
        assert "1 résidus chiffrés" in panel.status.get_label()
        assert "1 nettoyables" in panel.status.get_label()
        assert staged.exists()
        panel.prune_staging()  # Bypass disabled button: still fails closed.
        assert staged.exists() and not panel.is_scanning()
        assert "confirmation" in panel.status.get_label()
        panel.staging_confirm.set_active(True)
        assert panel.staging_prune.get_sensitive()
        panel.staging_prune.emit("clicked")
        assert not panel.staging_confirm.get_active()
        assert panel.is_scanning()
        assert not panel.sessions_open.get_sensitive()
        tasks.handle.finish()
        flush()
        assert not staged.exists()
        assert "1 résidus chiffrés anciens nettoyés" in panel.status.get_label()
        assert not panel.is_scanning()
        assert before == {
            identity: (vault.path / (identity + ".cbrv")).read_bytes()
            for identity in ids
        }
    finally:
        panel.destroy()
        window.close()
        flush()



def test_gtk_retention_never_deletes_without_confirm_and_fresh_unlock(
    tmp_path, monkeypatch,
):
    import browser_recorder_vault as vault_api
    from datetime import datetime, timedelta, timezone

    panel, tasks, window, vault, original_ids = panel_fixture(tmp_path)
    # One strictly fictional completed capture is 140 days old.
    old = datetime.now(timezone.utc) - timedelta(days=140)
    original_datetime = vault_api.datetime

    class OldDate(original_datetime):
        @classmethod
        def now(cls, tz=None):
            return old if tz is not None else old.replace(tzinfo=None)

    writer = vault.begin(KEY)
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", ORIGIN + "/old?account=FICTIONAL_ACCOUNT_44",
        status=200,
    ))
    with monkeypatch.context() as m:
        m.setattr(vault_api, "datetime", OldDate)
        writer.save(trace, "completed", force=True)
    trace.destroy()
    previous = {x: (vault.path / (x + ".cbrv")).read_bytes()
                for x in original_ids}
    try:
        panel.sessions_refresh.emit("clicked")
        assert len(vault.list_ids()) == 4
        assert not panel.retention_apply_button.get_sensitive()
        panel.retention_age.set_active_id("90")
        panel.vault_passphrase.set_text(KEY)
        panel.retention_preview_button.emit("clicked")
        assert tasks.handle is not None and panel.is_scanning()
        assert panel.vault_passphrase.get_text() == ""
        assert not panel.retention_apply_button.get_sensitive()
        tasks.handle.finish()
        flush()
        assert not panel.is_scanning()
        assert "1 sessions TERMINÉES" in panel.status.get_label()
        assert writer.session_id in vault.list_ids()
        panel.apply_retention()  # must refuse without consent.
        assert writer.session_id in vault.list_ids()
        panel.retention_confirm.set_active(True)
        flush()
        assert panel.retention_apply_button.get_sensitive()
        panel.vault_passphrase.set_text(KEY)
        panel.retention_apply_button.emit("clicked")
        assert panel.is_scanning()
        assert not panel.retention_confirm.get_active()
        assert panel.vault_passphrase.get_text() == ""
        tasks.handle.finish()
        flush()
        assert writer.session_id not in vault.list_ids()
        assert len(vault.list_ids()) == 3
        assert previous == {x: (vault.path / (x + ".cbrv")).read_bytes()
                            for x in original_ids}
        assert not panel.retention_apply_button.get_sensitive()
        assert not list(tmp_path.rglob("*.zip"))
    finally:
        panel.destroy()
        window.close()
        flush()


def test_gtk_retention_changed_duration_invalidates_preview(tmp_path, monkeypatch):
    import browser_recorder_vault as vault_api
    from datetime import datetime, timedelta, timezone

    panel, tasks, window, vault, ids = panel_fixture(tmp_path)
    old = datetime.now(timezone.utc) - timedelta(days=160)
    original_datetime = vault_api.datetime

    class OldDate(original_datetime):
        @classmethod
        def now(cls, tz=None):
            return old if tz is not None else old.replace(tzinfo=None)

    writer = vault.begin(KEY)
    trace = PrivateHttpTrace((ORIGIN,))
    with monkeypatch.context() as m:
        m.setattr(vault_api, "datetime", OldDate)
        writer.save(trace, "completed", force=True)
    trace.destroy()
    try:
        panel.vault_passphrase.set_text(KEY)
        panel.retention_preview_button.emit("clicked")
        tasks.handle.finish()
        flush()
        assert panel._retention_plan.count == 1
        panel.retention_confirm.set_active(True)
        assert panel.retention_apply_button.get_sensitive()
        panel.retention_age.set_active_id("365")
        assert panel._retention_plan is None
        assert not panel.retention_confirm.get_active()
        assert not panel.retention_apply_button.get_sensitive()
        assert writer.session_id in vault.list_ids()
    finally:
        panel.destroy()
        window.close()
        flush()
