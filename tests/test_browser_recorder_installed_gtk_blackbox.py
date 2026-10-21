"""P0 #2531: installed Browser Recorder ZIP → PluginManager → real GTK route.

Unlike source-tree UI tests, no Browser Recorder Python module is imported
or put on PYTHONPATH before Core activation. All test resources are synthetic.
A real Playwright *Python distribution* is required in the isolated GTK test
venv; no Chromium binary, HTTP capture or download is used in this case.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import zipfile

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from cifereca.plugins.configuration import PluginConfigStore


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/plugins/browser-recorder"
PACKAGE_ID = "org.cifereca.technical.browser-recorder"
PLUGIN_ID = "ui.page.browser-recorder"


def _flush():
    main = GLib.MainContext.default()
    for _ in range(80):
        if not main.pending():
            return
        main.iteration(False)


def _children(widget):
    result = []
    child = widget.get_first_child()
    while child is not None:
        result.append(child)
        child = child.get_next_sibling()
    return result


def _real_package_zip(path):
    """Use the *production* release packager and validate its real artifact."""
    from scripts.plugin_packaging import build_plugin_zip, validate_plugin_zip
    archive = build_plugin_zip(SOURCE, path.parent)
    verified = validate_plugin_zip(archive)
    assert verified["package"]["id"] == PACKAGE_ID
    assert verified["package"]["version"] == "0.1.3"
    with zipfile.ZipFile(archive) as bundle:
        assert "browser_recorder_page.py" in bundle.namelist()
        assert "browser_recorder_review_ui.py" in bundle.namelist()
        assert not any(name.startswith("tests/") for name in bundle.namelist())
    return archive


class FailClosedTasks:
    def __init__(self):
        self.submissions = []

    def submit(self, task, **metadata):
        # No capture should occur while opening the installed UI.
        self.submissions.append(metadata)
        raise AssertionError("unexpected browser capture during plugin activation")


@pytest.mark.parametrize("width", (1280, 1366, 1600))
def test_install_zip_activate_via_core_and_open_public_gtk_page(
    monkeypatch, tmp_path, width,
):
    Gtk.init()
    assert "playwright" in {m.name for m in __import__("pkgutil").iter_modules()}
    # In this dedicated test process, the plugin package can ONLY be resolved
    # from PackageStore; no source directory is added as a Python search root.
    assert str(SOURCE.resolve()) not in sys.path
    assert "browser_recorder_page" not in sys.modules
    assert "browser_recorder_review_ui" not in sys.modules
    monkeypatch.setattr("cifereca.plugins.manager.data_dir", lambda: tmp_path / "core-data")
    monkeypatch.setattr("cifereca.plugins.manager.cache_dir", lambda: tmp_path / "core-cache")
    archive = _real_package_zip(tmp_path / "browser-recorder.zip")
    tasks = FailClosedTasks()
    store = PackageStore(tmp_path / "packages")
    manager = PluginManager(
        package_store=store,
        state_store=PluginStateStore(tmp_path / "state.json"),
        config_store=PluginConfigStore(tmp_path / "config.json"),
        brokers={"tasks": tasks},
    )
    window = Gtk.Window()
    window.set_default_size(width, 850)
    try:
        # PackageInstaller exercises archive validation, staging, manifest
        # extraction, version pinning and active pointer. We deliberately
        # avoid PluginManager.install(... activate=True) here: its Core-owned
        # pip resolver is tested elsewhere and may download dependencies.
        package = manager.installer.install(archive, activate=True)
        assert package.root == store.version_root(PACKAGE_ID, "0.1.3")
        assert package.root.is_dir()
        assert package.sha256
        assert manager.packages.active_version(PACKAGE_ID) == "0.1.3"
        manager.activate()
        assert manager.status(PLUGIN_ID).enabled is False
        assert manager.service(PLUGIN_ID) is None
        manager.set_enabled(PLUGIN_ID, True)
        status = manager.status(PLUGIN_ID)
        assert status.enabled and status.state == "ready", (
            status.reason, status.reason_code,
        )

        # Public plugin API; never import browser_recorder_page directly.
        service = manager.service(PLUGIN_ID)
        assert service is not None
        assert "browser_recorder_page" in sys.modules
        assert Path(sys.modules["browser_recorder_page"].__file__).resolve().is_relative_to(
            package.root.resolve()
        )
        contributions = service.pages()
        assert len(contributions) == 1
        page = contributions[0]
        assert page.route == "browser-recorder"
        assert page.title == "Browser Recorder"
        root = page.factory(None)
        window.set_child(root)
        window.present()
        _flush()
        description, site, consent, workflow, private, advanced = _children(root)
        assert isinstance(site, Gtk.Entry)
        assert isinstance(consent, Gtk.CheckButton)
        assert isinstance(advanced, Gtk.Expander)
        assert not advanced.get_expanded()
        assert "HTTP" in description.get_label()
        assert "ZIP" in workflow.get_label()
        assert "HAR" in advanced.get_label()
        assert not advanced.get_child().get_mapped()

        # No manual login, browser launch or ZIP writes on display.
        assert tasks.submissions == []
        assert list((tmp_path / "core-data" / "plugins" / "data"
                     / PLUGIN_ID).glob("exports/*.zip")) == []
        assert site.grab_focus()
        focused = window.get_focus()
        assert focused is site or focused.is_ancestor(site)
        advanced.set_expanded(True)
        _flush()
        assert advanced.get_child().get_mapped()
        assert tasks.submissions == []
    finally:
        window.close()
        manager.close()
        _flush()
        # Cached plugin modules may be detached at teardown; this test runs
        # alone, without the source-tree Browser Recorder test suite.



def test_installed_zip_full_review_and_independent_archive_oracle(
    monkeypatch, tmp_path,
):
    """Installed plugin GTK → 81 choices → ZIP; synthetic HTTP injected at seam.

    This executes the packaged, installed implementation and independently
    reads exported ZIP files with Python stdlib. The HTTP capture boundary
    is explicitly substituted; this is NOT a real browser-capture recipe.
    """
    from hashlib import sha256

    Gtk.init()
    assert "browser_recorder_page" not in sys.modules
    monkeypatch.setattr("cifereca.plugins.manager.data_dir", lambda: tmp_path / "core-data")
    monkeypatch.setattr("cifereca.plugins.manager.cache_dir", lambda: tmp_path / "core-cache")

    class Handle:
        def __init__(self, task):
            self.task = task
            self._complete = False
            self._callback = None
            self._result = None

        def done(self):
            return self._complete

        def result(self):
            assert self._complete
            return self._result

        def add_done_callback(self, callback):
            self._callback = callback

        def finish(self):
            self._result = self.task(None)
            self._complete = True
            self._callback(self)

    class Tasks:
        def __init__(self):
            self.handle = None

        def submit(self, work, **options):
            assert options["kind"] == "browser-recorder"
            self.handle = Handle(work)
            return self.handle

    tasks = Tasks()
    store = PackageStore(tmp_path / "packages")
    manager = PluginManager(
        package_store=store,
        state_store=PluginStateStore(tmp_path / "state.json"),
        config_store=PluginConfigStore(tmp_path / "config.json"),
        brokers={"tasks": tasks},
    )
    window = Gtk.Window()
    window.set_default_size(1366, 850)
    trace = None
    try:
        manager.installer.install(
            _real_package_zip(tmp_path / "browser-recorder.zip"), activate=True,
        )
        manager.activate()
        assert manager.service(PLUGIN_ID) is None  # disabled by default
        manager.set_enabled(PLUGIN_ID, True)
        assert manager.status(PLUGIN_ID).state == "ready"
        plugin = manager.service(PLUGIN_ID)
        assert plugin is not None
        page = plugin.pages()[0]
        root = page.factory(None)

        # Synthetic-only boundary, installed *module*, never the source tree:
        # production origin guards, Playwright and capture limits are NOT
        # weakened globally to admit localhost test HTTP.
        review_module = importlib.import_module("browser_recorder_review_ui")
        http_module = importlib.import_module("browser_recorder_http")
        for module in (review_module, http_module):
            assert Path(module.__file__).resolve().is_relative_to(
                store.version_root(PACKAGE_ID, "0.1.3").resolve()
            )
        origin = "https://books.example.org"
        ids = tuple(str(2000 + n) for n in range(81))
        trace = http_module.PrivateHttpTrace((origin,))
        trace.exchanges.append(http_module.PrivateExchange(
            1, "GET", url=origin + "/api/books?" +
            "&".join("BookId=" + n for n in ids),
            status=200, mime="application/json",
            response_headers={"content-type": "application/json"},
            response_body='{"access_token":"FICTIONAL_NONEXPORTABLE_KEY",'
                          '"books":[{"BookId":2000,"title":"STRUCTURE_FAKE"}]}',
            response_body_state="captured",
        ))

        def synthetic_only(start, stop, **kwargs):
            assert start == origin + "/catalog"
            assert stop.is_set()
            assert kwargs["pause_control"] is not None
            return trace

        monkeypatch.setattr(review_module, "record_http", synthetic_only)
        window.set_child(root)
        window.present()
        _flush()
        site = _children(root)[1]
        consent = _children(root)[2]
        site.set_text(origin + "/catalog")
        consent.set_active(True)
        # Use only exposed native GTK controls to complete the flow.
        assert tasks.handle is None
        assert not plugin.private_panel.export_button.get_sensitive()
        plugin.private_panel.start.emit("clicked")
        assert tasks.handle is not None
        plugin.private_panel.stop.emit("clicked")
        tasks.handle.finish()
        _flush()
        panel = plugin.private_panel
        assert panel._review is not None
        assert len(panel._review.local_rows()) == 81
        assert not panel.plan_button.get_sensitive()
        assert not panel.export_button.get_sensitive()

        for page_index in range(4):
            rows = _children(panel.candidates_box)
            assert len(rows) == (24 if page_index < 3 else 9)
            for index, line in enumerate(rows):
                choice = "keep" if (page_index == 0 and index == 0) else "anonymize"
                line.get_last_child().set_active_id(choice)
            assert panel._review.pending() == max(0, 81 - min((page_index + 1) * 24, 81))
            if page_index < 3:
                panel.candidate_next.emit("clicked")
                _flush()

        assert panel.plan_button.get_sensitive()
        exports = tmp_path / "core-data" / "plugins" / "data" / PLUGIN_ID / "exports"
        assert not exports.exists()
        panel.plan_button.emit("clicked")
        _flush()
        exact = panel._bundle_preview
        assert exact is not None
        assert not panel.export_button.get_sensitive()
        assert not exports.exists()
        for member in exact["members"]:
            panel.bundle_member.set_active_id(member["name"])
            buffer = panel.bundle_text.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
            assert text == member["text"]
            assert sha256(text.encode("utf-8")).hexdigest() == member["sha256"]

        panel.export_button.emit("clicked")  # Cannot export without consent
        assert not exports.exists()
        panel.export_confirm.set_active(True)
        assert panel.export_button.get_sensitive()
        panel.export_button.emit("clicked")
        outputs = sorted(exports.glob("*.zip"))
        assert len(outputs) == 1
        assert outputs[0].stat().st_mode & 0o777 == 0o600
        with zipfile.ZipFile(outputs[0]) as archive:
            assert archive.testzip() is None
            members = archive.namelist()
            assert "manifest.json" in members
            assert "timeline.json" in members
            assert sorted(members) == sorted(x["name"] for x in exact["members"])
            for item in exact["members"]:
                raw = archive.read(item["name"])
                assert raw == item["text"].encode("utf-8")
                assert sha256(raw).hexdigest() == item["sha256"]
            plaintext = "\n".join(archive.read(n).decode("utf-8") for n in members)
            assert "FICTIONAL_NONEXPORTABLE_KEY" not in plaintext
            assert ids[0] in plaintext  # explicitly approved business ID
            assert ids[-1] not in plaintext  # individually anonymized
            assert "PSEUDO_" in plaintext
        panel.clear.emit("clicked")
        _flush()
        assert not trace.exchanges
        assert not panel.has_private_data()
    finally:
        window.close()
        manager.close()
        _flush()
