from __future__ import annotations
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("cifereca_diagnostics_page",ROOT/"cifereca_diagnostics_page.py")
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class Runtime:
    def available(self): return True
    def probe(self): return {"contract":{"contract_version":8},"resolved_executable":"/usr/bin/fake"}

class Context:
    def services(self, category, **kwargs):
        if category=="runtime.adapter": return (SimpleNamespace(plugin_id="runtime.test",plugin_version="1",service=Runtime()),)
        if category=="provider.library": return (SimpleNamespace(plugin_id="provider.test"),)
        return ()

class Sync:
    def sources(self):
        return (SimpleNamespace(label="Paris",provider_id="paris",connected=True,sync_capable=True,enabled=True,last_success="now",error=""),)

class ActiveChecksTests(unittest.TestCase):
    def test_checks_cover_sources_runtime_and_providers(self):
        rows=mod.active_checks(Context(),Sync())
        self.assertTrue(any(r["message"]=="Source Paris" and r["status"]=="ok" for r in rows))
        self.assertTrue(any(r["message"]=="runtime.test" and r["status"]=="ok" for r in rows))
        self.assertTrue(any(r["message"]=="Providers chargés" for r in rows))

if __name__=="__main__":
    unittest.main()
