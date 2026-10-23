"""Fail-closed projection for read-only BnF book actions (#2055)."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from cifereca_bnf_pret_book_actions import BookActionSignals, BOOK_ACTIONS_JS
    from cifereca_bnf_pret_web import ProfileScope, WebParseError
finally:
    sys.path.pop(0)


class BnfBookActionSignalsTests(unittest.TestCase):
    def setUp(self):
        self.scope = ProfileScope("private-identity", "private-account", "private-epoch")
        self.data = {
            "code": "ok", "borrow_controls": 1,
            "booking_controls": 2, "unavailable_markers": 0
        }

    def test_control_presence_never_authorizes_borrow(self):
        signals = BookActionSignals.from_browser(self.data, scope=self.scope)
        self.assertEqual((signals.borrow_controls, signals.booking_controls,
                          signals.unavailable_markers), (1, 2, 0))
        self.assertIs(signals.borrow_authorized, False)
        self.assertNotIn("private", repr(signals))

    def test_unavailable_and_zero_counts_are_not_outcomes(self):
        for values in (
            {**self.data, "borrow_controls": 0, "booking_controls": 0},
            {**self.data, "borrow_controls": 101, "unavailable_markers": 101},
        ):
            signals = BookActionSignals.from_browser(values, scope=self.scope)
            self.assertIs(signals.borrow_authorized, False)

    def test_no_text_urls_form_state_or_unexpected_fields(self):
        failures = (None, [], {}, {**self.data, "url": "private"},
                    {**self.data, "csrf": "secret"},
                    {**self.data, "html": "<input>"},
                    {**self.data, "borrow_controls": True},
                    {**self.data, "booking_controls": -1},
                    {**self.data, "unavailable_markers": 102},
                    {**self.data, "borrow_controls": "1"},
                    {**self.data, "code": "partial"})
        for candidate in failures:
            with self.subTest(candidate=type(candidate).__name__):
                with self.assertRaisesRegex(WebParseError, "^unrecognized_dom$"):
                    BookActionSignals.from_browser(candidate, scope=self.scope)

    def test_missing_scope_denied(self):
        with self.assertRaisesRegex(WebParseError, "^scope_mismatch$"):
            BookActionSignals.from_browser(self.data, scope=None)

    def test_js_never_reads_private_data_or_performs_actions(self):
        for forbidden in ("innerHTML", "outerHTML", "textContent", "getAttribute",
                          "cookie", "fetch(", "location.", "FormData", ".click(",
                          ".submit(", "XMLHttpRequest", "localStorage",
                          "sessionStorage", "value", "action=", "method=", "disabled"):
            self.assertNotIn(forbidden, BOOK_ACTIONS_JS)
        self.assertIn("querySelectorAll", BOOK_ACTIONS_JS)


if __name__ == "__main__":
    unittest.main()
