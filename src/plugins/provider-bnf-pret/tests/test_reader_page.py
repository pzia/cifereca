"""Offline contracts for the opt-in reader page; never launch GTK/Playwright."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    import cifereca_bnf_pret_page as page
finally:
    sys.path.pop(0)


class FakeWorker:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeHandle:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class BnfReaderPageTests(unittest.TestCase):
    def test_requires_exactly_one_selected_core_profile(self):
        a = {"identity_id": "a", "label": "Premier", "selected": False}
        b = {"identity_id": "b", "label": "Second", "selected": True}
        self.assertEqual(page.selected_identity([a, b]), ("b", "Second"))
        self.assertIsNone(page.selected_identity([a]))
        self.assertIsNone(page.selected_identity([dict(a, selected=True), b]))
        self.assertIsNone(page.selected_identity([dict(b, identity_id="")]))
        self.assertIsNone(page.selected_identity([dict(b, selected=1)]))
        self.assertIsNone(page.selected_identity([b] * 129))
        self.assertIsNone(page.selected_identity({"selected": b}))

    def test_no_implicit_browser_on_activation(self):
        calls = []
        context = SimpleNamespace(
            broker=lambda name: calls.append(name) or object()
        )
        instance = page.BnfReaderPage()
        instance.activate(context)
        self.assertEqual(calls, ["tasks", "identities"])
        self.assertIsNone(instance.worker)
        self.assertIsNone(instance.handle)
        self.assertEqual(instance.pages()[0].route, "bnf-pret-reader")

    def test_teardown_revokes_worker_and_task_without_browser_calls(self):
        instance = page.BnfReaderPage()
        worker, handle = FakeWorker(), FakeHandle()
        instance.worker, instance.handle = worker, handle
        instance.deactivate()
        self.assertTrue(worker.stopped)
        self.assertTrue(handle.cancelled)
        self.assertIsNone(instance.worker)
        self.assertIsNone(instance.handle)

    def test_browser_exception_is_never_rendered(self):
        secret = "reader@example.test?SAMLResponse=PRIVATE"
        self.assertNotIn(secret, page.safe_error(RuntimeError(secret)))
        self.assertEqual(page.safe_error(RuntimeError(secret)), page._DEFAULT_ERROR)
        self.assertEqual(page.safe_error(page.BrowserSessionError("scope_mismatch")),
                         "Identité changée : session interrompue.")
        self.assertEqual(page.safe_error(page.BrowserSessionError(secret)),
                         page._DEFAULT_ERROR)


if __name__ == "__main__":
    unittest.main()
