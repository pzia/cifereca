"""P0 #2531 — real headed Chromium from the installed Browser Recorder ZIP.

Playwright is NOT mocked. Only this test's exact randomly-bound localhost
origin is permitted; the production HTTPS/private-host policy is unchanged.
HTTP traffic, identities and archives are exclusively synthetic and local.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
from time import monotonic, sleep
from urllib.parse import urlsplit
from zipfile import ZipFile

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from cifereca.plugins.configuration import PluginConfigStore
from tests.test_browser_recorder_installed_gtk_blackbox import (
    _real_package_zip, _children, _flush, PACKAGE_ID, PLUGIN_ID, SOURCE,
)

pytest.importorskip("playwright.sync_api")

ACCOUNT = "acct-FICTIONAL-7"
PRODUCT = "214"
UUID = "550e8400-e29b-41d4-a716-446655440000"
TOKEN = "FICTIONAL-ACCESS-TOKEN-LOCAL-ONLY"
COOKIE = "FICTIONAL-SESSION-COOKIE-LOCAL-ONLY"


@pytest.fixture
def local_bookstore():
    """No external network, fixtures or raw vendor data."""
    completed = Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass  # Never print captured paths, identities or query strings.

        def send(self, status, data, mime="application/json", headers=None):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if data:
                self.wfile.write(data)
            self.wfile.flush()

        def do_POST(self):
            target = urlsplit(self.path).path
            requests.append(("POST", target))
            count = min(32000, int(self.headers.get("Content-Length", "0")))
            self.rfile.read(count)
            if target == "/login":
                self.send(302, b"", headers={
                    "Location": "/welcome?session=" + COOKIE,
                    "Set-Cookie": "session=" + COOKIE + "; SameSite=Lax",
                })
            else:
                self.send(404, b"{}")

        def do_GET(self):
            target = urlsplit(self.path).path
            requests.append(("GET", target))
            if target == "/catalog":
                page = """<!doctype html><html><body><script>
                (async () => {
                  let r = await fetch('/login', {
                    method: 'POST',
                    headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({
                      accountId: 'acct-FICTIONAL-7',
                      password: 'FICTIONAL_PASSWORD',
                      access_token: 'FICTIONAL-ACCESS-TOKEN-LOCAL-ONLY',
                      ProductId: 214
                    })
                  });
                  await r.text();
                  r = await fetch('/api/books?AccountId=acct-FICTIONAL-7&ProductId=214');
                  await r.text();
                  r = await fetch('/meta.xml'); await r.text();
                  r = await fetch('/ratelimit'); await r.text();
                  r = await fetch('/unauthorized'); await r.text();
                  r = await fetch('/forbidden'); await r.text();
                  r = await fetch('/binary.png'); await r.arrayBuffer();
                  r = await fetch('/uuid?uuid=550e8400-e29b-41d4-a716-446655440000');
                  await r.text();
                  r = await fetch('/done'); await r.text();
                })();
                </script></body></html>"""
                self.send(200, page.encode(), "text/html")
            elif target == "/welcome":
                self.send(200, b"<html>FICTIONAL welcome</html>", "text/html")
            elif target == "/api/books":
                self.send(200, json.dumps({
                    "AccountId": ACCOUNT, "ProductId": 214,
                    "access_token": TOKEN, "books": [
                        {"ProductId": PRODUCT, "title": "FICTIONAL_PRIVATE_TITLE"},
                    ],
                }).encode())
            elif target == "/meta.xml":
                self.send(200, b"<books><BookId>214</BookId></books>",
                          "application/xml")
            elif target == "/ratelimit":
                self.send(429, b'{"error":"rate_limited"}')
            elif target == "/unauthorized":
                self.send(401, b'{"error":"not_authorized"}')
            elif target == "/forbidden":
                self.send(403, b'{"error":"denied"}')
            elif target == "/binary.png":
                self.send(200, b"\x89PNG\r\n\x1a\nFICTIONAL_BINARY", "image/png")
            elif target == "/uuid":
                self.send(200, json.dumps({"uuid": UUID, "ProductId": PRODUCT}).encode())
            elif target == "/done":
                self.send(200, b'{"done":true}')
                completed.set()
            elif target == "/favicon.ico":
                self.send(404, b"")
            else:
                self.send(404, b"{}")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, completed, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class TaskBroker:
    """Real worker-thread semantics, preserving the Core broker's Future API."""
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.handles = []

    def submit(self, work, *, kind, label):
        assert kind == "browser-recorder"
        future = self.pool.submit(work, None)
        self.handles.append(future)
        return future

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)


def _pump_until(predicate, seconds=24):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        _flush()
        if predicate():
            return True
        sleep(.05)
    return False


def _check_independent_zip(path, members):
    """Independent subprocess; no plugin ZIP/redaction implementation imported."""
    oracle = {
        "members": [{"name": row["name"],
                     "sha256": sha256(row["text"].encode()).hexdigest()}
                    for row in members],
        "deny": [ACCOUNT, UUID, TOKEN, COOKIE, "FICTIONAL_PASSWORD",
                 "FICTIONAL_PRIVATE_TITLE"],
        "keep": PRODUCT,
    }
    child = r"""
import hashlib, json, sys
from zipfile import ZipFile
def verify():
    expected = json.load(sys.stdin)
    with ZipFile(sys.argv[1]) as archive:
        names = archive.namelist()
        if sorted(names) != sorted(row["name"] for row in expected["members"]):
            return False
        if archive.testzip() is not None:
            return False
        bodies = []
        for member in expected["members"]:
            raw = archive.read(member["name"])
            if hashlib.sha256(raw).hexdigest() != member["sha256"]:
                return False
            bodies.append(raw)
        content = b"\n".join(bodies)
        if any(token.encode() in content for token in expected["deny"]):
            return False
        if expected["keep"].encode() not in content or b"PSEUDO_" not in content:
            return False
    return True
try:
    valid = verify()
except Exception:
    valid = False
sys.exit(0 if valid else 3)
"""
    proc = subprocess.run(
        [sys.executable, "-c", child, str(path)],
        input=json.dumps(oracle), capture_output=True, text=True,
        timeout=10, check=False,
    )
    assert proc.returncode == 0
    assert proc.stdout == proc.stderr == ""


def test_installed_plugin_drives_real_chromium_to_confirmed_zip(
    monkeypatch, tmp_path, local_bookstore,
):
    from playwright.sync_api import sync_playwright  # installed; never faked
    assert sync_playwright is not None
    assert str(SOURCE.resolve()) not in sys.path
    assert "browser_recorder_page" not in sys.modules
    Gtk.init()
    server, completed, calls = local_bookstore
    origin = f"http://127.0.0.1:{server.server_port}"

    monkeypatch.setattr("cifereca.plugins.manager.data_dir", lambda: tmp_path / "core-data")
    monkeypatch.setattr("cifereca.plugins.manager.cache_dir", lambda: tmp_path / "core-cache")
    tasks = TaskBroker()
    store = PackageStore(tmp_path / "packages")
    manager = PluginManager(
        package_store=store,
        state_store=PluginStateStore(tmp_path / "state.json"),
        config_store=PluginConfigStore(tmp_path / "config.json"),
        brokers={"tasks": tasks},
    )
    window = Gtk.Window()
    window.set_default_size(1366, 850)
    panel = None
    try:
        manager.installer.install(
            _real_package_zip(tmp_path / "package.zip"), activate=True,
        )
        manager.activate()
        assert manager.service(PLUGIN_ID) is None
        manager.set_enabled(PLUGIN_ID, True)
        assert manager.status(PLUGIN_ID).state == "ready"
        plugin = manager.service(PLUGIN_ID)
        assert plugin is not None
        assert Path(sys.modules["browser_recorder_page"].__file__).resolve().is_relative_to(
            store.version_root(PACKAGE_ID, "0.2.20").resolve()
        )
        root = plugin.pages()[0].factory(None)

        # Only the exact random-port localhost fixture may bypass the
        # production HTTPS/loopback validation. No global policy is modified.
        ui = importlib.import_module("browser_recorder_review_ui")
        network = importlib.import_module("browser_recorder_http")
        zipped = importlib.import_module("browser_recorder_zip")
        for module in (ui, network, zipped):
            assert Path(module.__file__).resolve().is_relative_to(
                store.version_root(PACKAGE_ID, "0.2.20").resolve()
            )
        original = network.site_origin

        def local_only(value):
            parsed = urlsplit(value)
            if (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                    and parsed.port == server.server_port
                    and not parsed.username and not parsed.password):
                return origin
            return original(value)

        for module in (ui, network, zipped):
            monkeypatch.setattr(module, "site_origin", local_only)

        window.set_child(root)
        window.present()
        _flush()
        site, consent = [plugin.private_panel.site, plugin.private_panel.consent]
        site.set_text(origin + "/catalog")
        consent.set_active(True)
        panel = plugin.private_panel
        panel.vault_consent.set_active(True)
        panel.vault_passphrase.set_text("FICTIONAL-TEST-PASSPHRASE-2026")
        assert not panel.export_button.get_sensitive()
        assert not tasks.handles
        panel.start.emit("clicked")  # actual page → installed HTTP collector
        assert len(tasks.handles) == 1
        assert _pump_until(lambda: completed.is_set(), seconds=30), (
            "synthetic user JavaScript journey did not complete"
        )
        # Allow async Chromium requestfinished listeners to drain.
        sleep(.35)
        panel.stop.emit("clicked")
        assert _pump_until(lambda: panel._review is not None, seconds=20), (
            "review unavailable after Chromium session"
        )
        trace = panel._trace
        assert trace is not None
        assert trace.vault_session_id
        from browser_recorder_vault import PrivateSessionVault
        vault = PrivateSessionVault(
            tmp_path / "core-data" / "plugins" / "data" / PLUGIN_ID
            / "private-sessions"
        )
        session = vault.open(
            trace.vault_session_id, "FICTIONAL-TEST-PASSPHRASE-2026",
        )
        assert session.state == "completed"
        assert len(session.trace.exchanges) == len(trace.exchanges)
        session.trace.destroy()
        assert len(trace.exchanges) >= 9
        assert {401, 403, 429}.issubset({x.status for x in trace.exchanges})
        assert any(x.method == "POST" and x.status == 302 for x in trace.exchanges)
        assert any(x.mime == "application/xml" for x in trace.exchanges)
        assert all(x.response_body is None for x in trace.exchanges
                   if x.mime == "image/png")
        # Passive instrumentation never retries or replays sensitive POSTs.
        assert [x for x in calls if x[0] == "POST"] == [("POST", "/login")]
        assert calls.count(("GET", "/api/books")) == 1
        assert calls.count(("GET", "/unauthorized")) == 1
        assert calls.count(("GET", "/forbidden")) == 1
        assert all("?" not in path for _, path in calls)

        choices = {row["value"]: row for row in panel._review.local_rows()}
        assert ACCOUNT in choices and PRODUCT in choices and UUID in choices
        assert TOKEN not in choices and COOKIE not in choices
        assert not panel.plan_button.get_sensitive()
        # Exercise actual per-value GTK selectors, not direct .decide().
        for current_page in range(1 + (len(choices) - 1) // 24):
            for line in _children(panel.candidates_box):
                chooser = line.get_last_child()
                key = chooser.get_active_id()
                assert key == "choose"
                label = line.get_first_child().get_label()
                row = next(row for row in panel._review.local_rows()
                           if row["value"] in label)
                action = ("keep" if row["value"] == PRODUCT else
                          "exclude" if row["value"] == UUID else "anonymize")
                chooser.set_active_id(action)
            if current_page * 24 + 24 < len(choices):
                panel.candidate_next.emit("clicked")
                _flush()
        assert panel._review.pending() == 0
        assert panel.plan_button.get_sensitive()
        exports = tmp_path / "core-data" / "plugins" / "data" / PLUGIN_ID / "exports"
        assert not exports.exists()
        panel.plan_button.emit("clicked")
        _flush()
        preview = panel._bundle_preview
        assert preview is not None
        assert not exports.exists()
        assert not panel.export_button.get_sensitive()
        for member in preview["members"]:
            panel.bundle_member.set_active_id(member["name"])
            view = panel.bundle_text.get_buffer()
            assert view.get_text(view.get_start_iter(), view.get_end_iter(), True) == member["text"]

        # No archive exists until the operator explicitly confirms.
        panel.export_button.emit("clicked")
        assert not exports.exists()
        panel.export_confirm.set_active(True)
        def choose(done):
            exports.mkdir(mode=0o700, exist_ok=True)
            done(exports / "chromium-reviewed.zip")
        panel._prompt_export_file = choose
        panel.export_button.emit("clicked")
        packages = sorted(exports.glob("*.zip"))
        assert len(packages) == 1
        assert packages[0].stat().st_mode & 0o777 == 0o600
        _check_independent_zip(packages[0], preview["members"])
        panel.clear.emit("clicked")
        _flush()
        assert not panel.has_private_data()
    finally:
        if panel is not None and panel.is_recording():
            panel.stop_capture()
        # Join after stop before deactivating; worker owns Chromium lifetime.
        try:
            tasks.close()
        finally:
            window.close()
            manager.close()
            _flush()
