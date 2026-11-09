"""P0 #5057: executable getattr dispatch is distinct from passive metadata."""
import unittest
from scripts.ci_provider_core_coupling_inventory import inspect_source


class ProviderGetattrDispatchTest(unittest.TestCase):
    def test_executable_calls_are_signals(self):
        source = '''
getattr(service, "sync_kobo_backup")()
getattr(broker, "numilog_selected_acquisition")(book)
getattr(adapter, "bnf_borrow")()
getattr(service, "biblioaccess")()
'''
        found = inspect_source(source)["provider_dynamic_calls"]
        self.assertEqual([row["line"] for row in found], [2, 3, 4, 5])
        self.assertIn("kobo", found[0]["providers"])
        self.assertIn("numilog", found[1]["providers"])
        self.assertIn("bnf", found[2]["providers"])
        self.assertIn("biblioaccess", found[3]["providers"])

    def test_passive_and_generic_are_not_dispatch(self):
        source = '''
operation = getattr(service, "sync_kobo_backup")
doc = "getattr(service, 'sync_kobo_backup')()"
description = getattr(info, "display_name")
getattr(service, "sync_" + provider_id)()
getattr(service, "sync_kobold_backup")()
'''
        self.assertEqual(inspect_source(source)["provider_dynamic_calls"], [])

    def test_new_provider_from_manifest_is_detected(self):
        providers = ("library_example",)
        source = 'getattr(service, "sync_library_example")()'
        self.assertEqual(inspect_source(source, providers)["provider_dynamic_calls"][0]["providers"],
                         ["library_example"])


if __name__ == "__main__":
    unittest.main()
