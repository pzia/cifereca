"""Offline diagnostic-only BnF DOM selector contract."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from cifereca_bnf_pret_dom_probe import DomProbe, DOM_PROBE_JS
    from cifereca_bnf_pret_web import ProfileScope, WebParseError
finally:
    sys.path.pop(0)


class BnfDomProbeContractTests(unittest.TestCase):
    def setUp(self):
        self.scope = ProfileScope("private-identity", "private-account", "epoch")
        self.raw = {
            "code": "ok", "loan_sections": 1, "loan_cards": 2,
            "search_sections": 0, "search_cards": 0, "book_scopes": 0,
            "resource_links": 2, "loan_links": 2,
        }

    def test_safe_numeric_projection_without_private_repr(self):
        result = DomProbe.from_browser(self.raw, scope=self.scope, kind="activity")
        self.assertEqual((result.kind, result.loan_cards), ("activity", 2))
        self.assertNotIn("private", repr(result))
        self.assertNotIn("private", str(result))
        self.assertEqual(result.scope, self.scope)

    def test_strict_schema_and_bounded_non_boolean_counts(self):
        for raw in (None, [], {}, {**self.raw, "url": "private"},
                    {**self.raw, "html": "private"},
                    {**self.raw, "loan_cards": -1},
                    {**self.raw, "loan_cards": 102},
                    {**self.raw, "loan_cards": True},
                    {**self.raw, "loan_cards": "1"},
                    {**self.raw, "code": "failed"}):
            with self.assertRaisesRegex(WebParseError, "^unrecognized_dom$"):
                DomProbe.from_browser(raw, scope=self.scope, kind="activity")

    def test_scope_kind_and_saturation_boundaries(self):
        with self.assertRaisesRegex(WebParseError, "^scope_mismatch$"):
            DomProbe.from_browser(self.raw, scope=None, kind="activity")
        with self.assertRaisesRegex(WebParseError, "^unrecognized_dom$"):
            DomProbe.from_browser(self.raw, scope=self.scope, kind="login")
        saturated = DomProbe.from_browser({**self.raw, "loan_cards": 101},
                                          scope=self.scope, kind="activity")
        self.assertEqual(saturated.loan_cards, 101)

    def test_js_does_not_read_dom_values_or_browser_storage(self):
        for forbidden in ("innerHTML", "outerHTML", "textContent", "getAttribute",
                          "cookie", "fetch(", "location.", "XMLHttpRequest",
                          "localStorage", "sessionStorage"):
            self.assertNotIn(forbidden, DOM_PROBE_JS)
        self.assertIn("querySelectorAll", DOM_PROBE_JS)


if __name__ == "__main__":
    unittest.main()
