"""Real GTK4 Diagnostics page + native SAVE chooser + real Core ZIP export.

Run under the repository headless GTK runtime. No supplier or private account.
"""
from pathlib import Path
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
            httpx.MockTransport(lambda r:httpx.Response(200,stream=httpx.ByteStream(content))),
            capture=capture),trust_env=False) as client:
        assert client.get('https://example.test/synthetic').content==content
    set_diagnostics_level('normal')

    class Broker(SelectionBroker):
        exports=0
        def http_trace_status(self):
            return capture.status()
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
        controller=next(x for x in plugin._support_exports if hasattr(x,'broker'))
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
        assert not (tmp_path/'cifereca-echanges-http.zip').exists()

        button.emit('clicked')
        button.emit('clicked')
        chooser=controller.chooser
        target=tmp_path/'cifereca-echanges-http.zip'
        spin_until(lambda:chooser.get_file() is not None
                   and chooser.get_file().get_path()==str(target))
        chooser.emit('response',Gtk.ResponseType.ACCEPT)
        spin_until(target.exists)
        assert broker.exports==1 and stat.S_IMODE(target.stat().st_mode)==0o600
        with zipfile.ZipFile(target) as archive:
            index=json.loads(archive.read('index.json'))
            body=index['exchanges'][0]['response_body']
            assert archive.read(body['blob'])==content and body['state']=='complete'
        set_diagnostics_level('normal')
        spin_until(lambda:not button.get_sensitive())
        assert target.exists()  # disabling Trace never removes a chosen export
    finally:
        plugin.deactivate()
        window.destroy()
        set_diagnostics_level(previous)
