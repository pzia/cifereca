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


class InventoryDiagnostics:
    def inventory(self):
        return (
            {
                "plugin_id": "runtime.kobo-adept",
                "type": "runtime.adapter",
                "version": "1.2.14",
                "package_version": "1.2.14",
                "state": "ready",
            },
            {
                "plugin_id": "provider.paris",
                "type": "provider.library",
                "version": "1.2.19",
                "package_version": "1.2.19",
                "state": "ready",
            },
        )

    def runtime_diagnostic(self, plugin_id):
        assert plugin_id == "runtime.kobo-adept"
        return {"operation": "inventory", "returncode": 0, "stdout_mode": "json"}


def test_public_inventory_and_bounded_runtime_diagnostic_are_consumed():
    broker = InventoryDiagnostics()
    inventory = mod.public_inventory(broker)
    assert [row["plugin_id"] for row in inventory] == [
        "runtime.kobo-adept",
        "provider.paris",
    ]
    runtime = mod.relevant_runtime_diagnostics(broker, inventory)
    assert runtime == [{
        "plugin_id": "runtime.kobo-adept",
        "operation": "inventory",
        "returncode": 0,
        "stdout_mode": "json",
    }]


if __name__=="__main__":
    unittest.main()
