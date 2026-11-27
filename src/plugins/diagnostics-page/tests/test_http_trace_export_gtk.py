"""Real GTK4 Diagnostics page + native SAVE chooser + real Core ZIP export.

Run under the repository headless GTK runtime. No supplier or private account.
"""
from pathlib import Path
import re
import os
import stat
import sys
from time import monotonic, sleep
from types import SimpleNamespace
import zipfile
import json

import pytest

gi = pytest.importorskip('gi')
try:
    gi.require_version('Gtk','4.0')
except ValueError:
    pytest.skip('GTK4 unavailable', allow_module_level=True)
from gi.repository import Gtk, GLib

pytestmark = pytest.mark.skipif(not os.environ.get('DISPLAY'), reason='real GTK display required')
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'e2e'))
from diagnostics_page_probe import Context, Host, ImmediateHandle, SelectionBroker, SelectionTasks, walk, reveal_diagnostics_section

import httpx
import cifereca_diagnostics_page as page_module
from cifereca.http_trace import HTTPTraceCapture, TraceHTTPTransport
from cifereca.logging_utils import diagnostics_level, set_diagnostics_level


def spin_until(condition, seconds=8):
    deadline=monotonic()+seconds
    while monotonic()<deadline:
        while GLib.MainContext.default().iteration(False):
            pass
        if condition():
            return
        sleep(.01)
    raise AssertionError('GTK condition timed out')


def test_real_diagnostics_native_http_export(tmp_path, monkeypatch):
    Gtk.init()
    capture=HTTPTraceCapture()
    previous=diagnostics_level()
    set_diagnostics_level('trace')
    content=b'synthetic HTTP response with binary \x00\xff'
    with httpx.Client(transport=TraceHTTPTransport(
            httpx.MockTransport(lambda r:httpx.Response(200,headers={'Content-Type':'application/octet-stream'},stream=httpx.ByteStream(content))),
            capture=capture),trust_env=False) as client:
        assert client.get('https://example.test/synthetic').content==content
    set_diagnostics_level('normal')

    class Broker(SelectionBroker):
        exports=0
        def http_trace_status(self):
            return capture.status()
        def set_http_trace_include_binary(self, value):
            capture.set_include_binary(value)
            return value
        def export_http_trace(self,destination):
            result=capture.export(destination)
            self.exports+=1
            return str(result)

    class Tasks(SelectionTasks):
        def submit(self,fn,*,label,kind):
            if label=='Export local des échanges HTTP':
                self.calls.append((label,kind))
                return ImmediateHandle(fn(SimpleNamespace(cancelled=lambda:False)))
            return super().submit(fn,label=label,kind=kind)

    broker,tasks,host=Broker(),Tasks(),Host()
    plugin=page_module.DiagnosticsPagePlugin().activate(Context(broker,tasks))
    # The folder is set before show(), exactly like the real #3218 workaround.
    monkeypatch.setattr(page_module,'_readable_raw_export_folder',lambda:str(tmp_path))
    window=Gtk.Window()
    window.set_default_size(1250,780)
    host.window=window
    try:
        page=plugin._build(host)
        window.set_child(page)
        window.present()
        spin_until(lambda:page.get_mapped())
        button=next(w for w in walk(page) if isinstance(w,Gtk.Button)
                    and w.get_label()=='Exporter les échanges HTTP…')
        checkbox=next(w for w in walk(page) if isinstance(w,Gtk.CheckButton)
                      and w.get_label()=='Inclure les contenus binaires dans les captures HTTP')
        assert checkbox.get_active() and checkbox.get_sensitive()
        controller=plugin._http_trace_export
        assert controller not in plugin._support_exports
        assert not button.get_sensitive()
        assert 'Trace inactif' in button.get_tooltip_text()
        set_diagnostics_level('trace')
        spin_until(button.get_sensitive)
        reveal_diagnostics_section(button)
        button.emit('clicked')
        assert controller.chooser is None and broker.exports==0
        assert button.get_label()=='Confirmer l’export HTTP non masqué'
        button.emit('clicked')
        chooser=controller.chooser
        assert isinstance(chooser,Gtk.FileChooserNative)
        assert chooser.get_transient_for() is window and chooser.get_modal()
        spin_until(lambda:chooser.get_current_folder() is not None)
        chooser.emit('response',Gtk.ResponseType.CANCEL)
        assert controller.chooser is None and broker.exports==0
        assert not list(tmp_path.glob('cifereca-echanges-http-*.zip'))

        button.emit('clicked')
        button.emit('clicked')
        chooser=controller.chooser
        spin_until(lambda:chooser.get_file() is not None
                   and chooser.get_file().get_path() is not None)
        target=Path(chooser.get_file().get_path())
        assert target.parent == tmp_path
        assert re.fullmatch(r'cifereca-echanges-http-\d{8}-\d{6}\.zip', target.name)
        chooser.emit('response',Gtk.ResponseType.ACCEPT)
        spin_until(target.exists)
        assert broker.exports==1 and stat.S_IMODE(target.stat().st_mode)==0o600
        with zipfile.ZipFile(target) as archive:
            index=json.loads(archive.read('index.json'))
            body=index['exchanges'][0]['response_body']
            assert archive.read(body['blob'])==content and body['state']=='complete'
        # Existing binary content must disappear from the next export, even
        # when collected before the user changed the common preference.
        button.emit('clicked')
        assert controller.armed
        checkbox.set_active(False)
        assert not controller.armed and capture.status()['include_binary'] is False
        with httpx.Client(transport=TraceHTTPTransport(
                httpx.MockTransport(lambda r:httpx.Response(
                    200,headers={'Content-Type':'application/json'},
                    stream=httpx.ByteStream(b'{"document":true}'))),
                capture=capture),trust_env=False) as client:
            assert client.get('https://example.test/document').content==b'{"document":true}'
        button.emit('clicked')
        button.emit('clicked')
        chooser=controller.chooser
        chooser.set_current_name('without-binary.zip')
        filtered=tmp_path/'without-binary.zip'
        spin_until(lambda:chooser.get_file() is not None
                   and chooser.get_file().get_path()==str(filtered))
        chooser.emit('response',Gtk.ResponseType.ACCEPT)
        spin_until(filtered.exists)
        with zipfile.ZipFile(filtered) as archive:
            index=json.loads(archive.read('index.json'))
            binary,textual=[entry['response_body'] for entry in index['exchanges']]
            assert binary['state']=='binary_excluded' and not binary.get('blob')
            assert archive.read(textual['blob'])==b'{"document":true}'
        set_diagnostics_level('normal')
        spin_until(lambda:not button.get_sensitive())
        assert checkbox.get_sensitive() and not checkbox.get_active()
        checkbox.set_active(True)
        assert capture.status()['include_binary'] is True
        assert target.exists()  # disabling Trace never removes a chosen export
    finally:
        plugin.deactivate()
        window.destroy()
        set_diagnostics_level(previous)


def test_real_diagnostics_common_vault_restart(tmp_path, monkeypatch):
    from cifereca import http_trace
    from cifereca.core.capture_vault_service import CoreCaptureVault
    from cifereca.plugin_api.capture_vault import CaptureScope

    Gtk.init()
    previous = diagnostics_level()
    capture = HTTPTraceCapture()
    monkeypatch.setattr(http_trace, "HTTP_TRACE", capture)
    monkeypatch.setattr(page_module, "_readable_raw_export_folder", lambda: str(tmp_path))
    directory, keys = tmp_path / "vault", tmp_path / "keys"

    class Broker(SelectionBroker):
        def http_trace_status(self):
            return capture.status()
        def set_http_trace_include_binary(self, value):
            capture.set_include_binary(value)
            return value

    class Tasks(SelectionTasks):
        def submit(self, fn, *, label, kind):
            return ImmediateHandle(fn(SimpleNamespace(cancelled=lambda: False)))

    class VaultContext(Context):
        def __init__(self, port):
            super().__init__(Broker(), Tasks())
            self.port = port
        def broker(self, name):
            return self.port if name == "capture.vault" else super().broker(name)

    def mount(port):
        plugin = page_module.DiagnosticsPagePlugin().activate(VaultContext(port))
        host, window = Host(), Gtk.Window()
        host.window = window
        page = plugin._build(host)
        window.set_child(page)
        window.present()
        spin_until(page.get_mapped)
        return plugin, window

    try:
        set_diagnostics_level("trace")
        for mime, data in [("application/octet-stream", b"private-binary\\x00"),
                           ("application/json", b'{"ok":true}')]:
            with httpx.Client(transport=TraceHTTPTransport(
                    httpx.MockTransport(lambda r: httpx.Response(200, headers={"Content-Type": mime},
                        stream=httpx.ByteStream(data))), capture=capture), trust_env=False) as client:
                assert client.get("https://example.test/fixture").content == data
        core = CoreCaptureVault(directory, keys, authorize_scope=lambda *_: True)
        port = core.for_plugin("ui.page.diagnostics")
        plugin, window = mount(port)
        try:
            archive = plugin._http_vault_archive
            assert not archive.save.get_sensitive() and not directory.exists()
            archive.save.emit("clicked")
            assert not directory.exists()
            archive.consent.set_active(True)
            archive.save.emit("clicked")
            spin_until(lambda: archive.handle is None and bool(archive.rows.get_active_id()))
            sid = archive.rows.get_active_id()
            assert not archive.consent.get_active()
            assert list(directory.glob("*.cbrv")) == [directory / (sid + ".cbrv")]
            assert not list(tmp_path.glob("*.zip"))
            archive.pin.emit("clicked")
            spin_until(lambda: archive.handle is None and bool(port.sessions(CaptureScope())[0].pinned_until))
        finally:
            plugin.deactivate()
            window.destroy()
            core.close()
        set_diagnostics_level("normal")
        core = CoreCaptureVault(directory, keys, authorize_scope=lambda *_: True)
        port = core.for_plugin("ui.page.diagnostics")
        plugin, window = mount(port)
        try:
            archive = plugin._http_vault_archive
            archive.list.emit("clicked")
            spin_until(lambda: archive.handle is None and archive.rows.get_active_id() == sid)
            checkbox = next(w for w in walk(window) if isinstance(w, Gtk.CheckButton)
                            and w.get_label() == "Inclure les contenus binaires dans les captures HTTP")
            reveal_diagnostics_section(archive.export)
            archive.export.emit("clicked")
            assert archive.exporter.armed
            checkbox.set_active(False)
            assert not archive.exporter.armed
            archive.export.emit("clicked")
            archive.export.emit("clicked")
            chooser = archive.exporter.chooser
            assert isinstance(chooser, Gtk.FileChooserNative)
            chooser.emit("response", Gtk.ResponseType.CANCEL)
            assert not list(tmp_path.glob("*.zip"))
            archive.export.emit("clicked")
            archive.export.emit("clicked")
            chooser = archive.exporter.chooser
            chooser.set_current_name("archive-reopened.zip")
            target = tmp_path / "archive-reopened.zip"
            spin_until(lambda: chooser.get_file() is not None and chooser.get_file().get_path() == str(target))
            chooser.emit("response", Gtk.ResponseType.ACCEPT)
            spin_until(target.exists)
            assert stat.S_IMODE(target.stat().st_mode) == 0o600
            with zipfile.ZipFile(target) as bundle:
                index = json.loads(bundle.read("index.json"))
                binary, textual = [entry["response_body"] for entry in index["exchanges"]]
                assert binary["state"] == "binary_excluded" and not binary.get("blob")
                assert bundle.read(textual["blob"]) == b'{"ok":true}'
                assert "plugin-dev-kit/manifest.json" in bundle.namelist()
            archive.delete_confirm.set_active(True)
            archive.delete.emit("clicked")
            spin_until(lambda: archive.handle is None and not archive.rows.get_active_id())
            assert not port.sessions(CaptureScope()) and target.exists()
        finally:
            plugin.deactivate()
            window.destroy()
            core.close()
    finally:
        set_diagnostics_level(previous)
