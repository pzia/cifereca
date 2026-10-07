from __future__ import annotations
import importlib.util
from pathlib import Path
import unittest

from cifereca.plugin_api import HealthCheckResult

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("cifereca_diagnostics_page",ROOT/"cifereca_diagnostics_page.py")
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)


class Diagnostics:
    def active_checks(self):
        return (
            HealthCheckResult("core.sync.paris","paris","ok","Source Paris",{"connected":True},"core"),
            HealthCheckResult("runtime.test","runtime.test","ok","runtime.test",{"resolved_executable":True},"plugin"),
            HealthCheckResult("core.providers.loaded","provider.library","ok","Providers chargés",{"count":1},"core"),
        )


class ActiveChecksTests(unittest.TestCase):
    def test_checks_render_core_broker_results(self):
        rows=mod.active_checks(Diagnostics())
        self.assertTrue(any(r["message"]=="Source Paris" and r["status"]=="ok" for r in rows))
        self.assertTrue(any(r["message"]=="runtime.test" and r["status"]=="ok" for r in rows))
        self.assertTrue(any(r["message"]=="Providers chargés" for r in rows))


class FakeWidget:
    def __init__(self, mapped=False):
        self.mapped=mapped
        self.callback=None
    def connect(self, signal, callback):
        assert signal=="map"
        self.callback=callback
        return 1
    def get_mapped(self):
        return self.mapped
    def emit_map(self):
        self.mapped=True
        self.callback(self)


class LazyInitialRenderTests(unittest.TestCase):
    def test_waits_for_first_map_and_runs_once(self):
        widget=FakeWidget()
        calls=[]
        mod._run_once_on_first_map(widget, lambda: calls.append("render"))
        self.assertEqual(calls,[])
        widget.emit_map()
        self.assertEqual(calls,["render"])
        widget.emit_map()
        self.assertEqual(calls,["render"])

    def test_already_mapped_widget_runs_immediately_once(self):
        widget=FakeWidget(mapped=True)
        calls=[]
        mod._run_once_on_first_map(widget, lambda: calls.append("render"))
        self.assertEqual(calls,["render"])
        widget.emit_map()
        self.assertEqual(calls,["render"])


if __name__=="__main__":
    unittest.main()
