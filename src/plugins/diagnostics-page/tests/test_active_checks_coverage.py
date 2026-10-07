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


if __name__=="__main__":
    unittest.main()
