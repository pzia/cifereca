"""Three REAL headed Chromium captures survive plugin-manager restart.

Only the already-verified localhost synthetic shop is contacted. The test
does not take screenshots, create browser profile/HAR or publish archives.
A new manager and GTK window reopen three *encrypted* sessions without
another Chromium run and produce two independently reviewed ZIPs.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import importlib
import sys
from time import sleep
from urllib.parse import urlsplit
from zipfile import ZipFile

import pytest
gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402
pytest.importorskip("playwright.sync_api")

from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from cifereca.plugins.configuration import PluginConfigStore
from tests.test_browser_recorder_installed_chromium_e2e import (
    local_bookstore, TaskBroker, _pump_until, ACCOUNT, PRODUCT, UUID, TOKEN, COOKIE,
)
from tests.test_browser_recorder_installed_gtk_blackbox import (
    _real_package_zip, _children, _flush, PACKAGE_ID, PLUGIN_ID,
)

PASSPHRASE = "FICTIONAL-THREE-CAPTURES-VAULT-2026"


def make_manager(tmp_path, tasks):
    return PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=PluginStateStore(tmp_path / "state.json"),
        config_store=PluginConfigStore(tmp_path / "config.json"),
        brokers={"tasks": tasks},
    )


def test_three_real_captures_reopen_two_different_zips_no_replay(
    monkeypatch, tmp_path, local_bookstore,
):
    Gtk.init()
    server, completed, calls = local_bookstore
    origin = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(
        "cifereca.plugins.manager.data_dir", lambda: tmp_path / "core-data",
    )
    monkeypatch.setattr(
        "cifereca.plugins.manager.cache_dir", lambda: tmp_path / "core-cache",
    )
    tasks = TaskBroker()
    manager = make_manager(tmp_path, tasks)
    window = Gtk.Window()
    window.set_default_size(1024, 768)
    panel = None
    source_hashes = {}
    ids = []
    vault_path = (tmp_path / "core-data" / "plugins" / "data"
                  / PLUGIN_ID / "private-sessions")
    try:
        manager.installer.install(
            _real_package_zip(tmp_path / "fixture-package.zip"), activate=True,
        )
        manager.activate()
        manager.set_enabled(PLUGIN_ID, True)
        plugin = manager.service(PLUGIN_ID)
        assert plugin is not None
        root = plugin.pages()[0].factory(None)
        ui = importlib.import_module("browser_recorder_review_ui")
        net = importlib.import_module("browser_recorder_http")
        original_origin = net.site_origin

        def fixture_only(url):
            parsed = urlsplit(url)
            if (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                    and parsed.port == server.server_port
                    and not parsed.username and not parsed.password):
                return origin
            return original_origin(url)

        monkeypatch.setattr(net, "site_origin", fixture_only)
        monkeypatch.setattr(ui, "site_origin", fixture_only)
        window.set_child(root)
        window.present()
        _flush()
        site, consent = _children(root.get_child().get_child())[1:3]
        site.set_text(origin + "/catalog")
        consent.set_active(True)
        panel = plugin.private_panel
        from browser_recorder_vault import PrivateSessionVault
        vault = PrivateSessionVault(vault_path)

        for index in range(3):
            completed.clear()
            panel.vault_consent.set_active(True)
            panel.vault_passphrase.set_text(PASSPHRASE)
            panel.start.emit("clicked")
            assert panel.is_recording()
            assert _pump_until(lambda: completed.is_set(), seconds=30), (
                "fictional browser flow did not finish"
            )
            sleep(.35)
            panel.stop.emit("clicked")
            assert _pump_until(
                lambda: not panel.is_recording() and panel._review is not None,
                seconds=20,
            )
            sid = panel._trace.vault_session_id
            assert sid and sid not in ids
            ids.append(sid)
            stored = vault.open(sid, PASSPHRASE)
            try:
                assert stored.recovery_status == "completed"
                assert len(stored.trace.exchanges) >= 9
            finally:
                stored.trace.destroy()
            source_hashes[sid] = sha256(
                (vault_path / (sid + ".cbrv")).read_bytes()
            ).hexdigest()
            assert len(vault.list_ids()) == index + 1
            assert not list(vault_path.glob("*.zip"))
        assert calls.count(("POST", "/login")) == 3
        assert calls.count(("GET", "/api/books")) == 3
        assert calls.count(("GET", "/done")) == 3
    finally:
        if panel is not None and panel.is_recording():
            panel.stop_capture()
        tasks.close()
        window.close()
        manager.close()
        _flush()

    # A *new* plugin manager/window/PrivateReviewPanel works from disk.
    # This models the full GTK plugin component lifecycle without internet.
    tasks2 = TaskBroker()
    manager2 = make_manager(tmp_path, tasks2)
    window2 = Gtk.Window()
    window2.set_default_size(1280, 720)
    panel2 = None
    try:
        manager2.activate()
        manager2.set_enabled(PLUGIN_ID, True)
        plugin2 = manager2.service(PLUGIN_ID)
        assert plugin2 is not None
        root2 = plugin2.pages()[0].factory(None)
        window2.set_child(root2)
        window2.present()
        _flush()
        panel2 = plugin2.private_panel
        assert panel2 is not panel
        panel2.sessions_refresh.emit("clicked")
        assert set(vault.list_ids()) == set(ids)
        assert ACCOUNT not in panel2.status.get_label()
        assert origin not in panel2.status.get_label()
        for sid in ids:
            panel2.session_list.set_active_id(sid)
            assert origin not in panel2.session_list.get_active_text()

        panel2.vault_passphrase.set_text(PASSPHRASE)
        panel2.sessions_unlock.emit("clicked")
        assert _pump_until(lambda: not panel2.is_scanning(), seconds=30)
        assert "3 sessions déverrouillées" in panel2.status.get_label()
        for sid in ids:
            panel2.session_list.set_active_id(sid)
            assert origin in panel2.session_list.get_active_text()
            assert "completed" in panel2.session_list.get_active_text()
        from browser_recorder_zip import prepare_reviewed_zip
        exports = tmp_path / "chosen-zip-directory"
        exports.mkdir(mode=0o700)
        zip_digests = []
        for review_index in (0, 1):
            # Independently reopen the same archived source each time.
            panel2.session_list.set_active_id(ids[1])
            panel2.vault_passphrase.set_text(PASSPHRASE)
            panel2.sessions_open.emit("clicked")
            assert panel2._review is not None
            for candidate in panel2._review.candidates():
                action = ("keep" if candidate.value == PRODUCT and
                          review_index == 0 else
                          "exclude" if candidate.value in
                          ({UUID, PRODUCT} if review_index else {UUID})
                          else "anonymize")
                panel2._review.decide(candidate.key, action)
            assert panel2._review.pending() == 0
            plan = prepare_reviewed_zip(panel2._review)
            preview = plan.preview()
            output = exports / f"fictional-reviewed-{review_index}.zip"
            plan.save_as(output, user_confirmed=True)
            with ZipFile(output) as archive:
                assert archive.testzip() is None
                assert "plugin-dev-kit/manifest.json" in archive.namelist()
                combined = b"\n".join(archive.read(name)
                                      for name in archive.namelist())
                assert ACCOUNT.encode() not in combined
                assert TOKEN.encode() not in combined
                assert COOKIE.encode() not in combined
                assert UUID.encode() not in combined
            zip_digests.append(sha256(output.read_bytes()).hexdigest())
            assert sha256(
                (vault_path / (ids[1] + ".cbrv")).read_bytes()
            ).hexdigest() == source_hashes[ids[1]]
        assert zip_digests[0] != zip_digests[1]
        for sid in (ids[0], ids[2]):
            panel2.session_list.set_active_id(sid)
            panel2.vault_passphrase.set_text(PASSPHRASE)
            panel2.sessions_open.emit("clicked")
            assert panel2._trace.vault_session_id == sid
            assert panel2._review is not None
        assert calls.count(("POST", "/login")) == 3
        assert calls.count(("GET", "/api/books")) == 3
        assert {
            sid: sha256((vault_path / (sid + ".cbrv")).read_bytes()).hexdigest()
            for sid in ids
        } == source_hashes
    finally:
        tasks2.close()
        window2.close()
        manager2.close()
        _flush()
