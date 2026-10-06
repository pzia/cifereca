from __future__ import annotations
import importlib.util
from pathlib import Path
from types import MethodType
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("cifereca_kobo_adept_runtime",ROOT/"cifereca_kobo_adept_runtime.py")
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class ProfileLifecycleTests(unittest.TestCase):
    def runtime(self):
        value=object.__new__(mod.KoboAdeptRuntime)
        value.context=None
        value._available=MethodType(lambda self,*keys: True,value)
        value.calls=[]
        def run(self,args,**kwargs):
            self.calls.append(args)
            return {"ok":True}
        value.run_json=MethodType(run,value)
        value.profile=MethodType(lambda self,name:{
            "name":name,"complete":True,"fetch_capable":True,
            "provisioning":{"acsm_fetch_ready":True}},value)
        return value

    def test_provision_uses_json_add_and_verifies_ready(self):
        runtime=self.runtime()
        result=runtime.provision_profile("alexandre","a@example.test")
        self.assertTrue(runtime._profile_ready(result))
        self.assertEqual(runtime.calls[0],["profile","add","alexandre","--username","a@example.test","--json"])

    def test_associate_and_remove_are_not_forced(self):
        runtime=self.runtime()
        with tempfile.TemporaryDirectory() as mount:
            runtime.associate_profile("alexandre",mount)
        runtime.remove_profile("alexandre")
        self.assertIn(["profile","remove","alexandre","--json"],runtime.calls)
        self.assertFalse(any("--force" in call for call in runtime.calls))

if __name__=="__main__":
    unittest.main()
