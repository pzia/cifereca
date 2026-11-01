"""P0 #2055: one synthetic BnF activation POST, never a live loan or ADEPT.

The transport is reachable ONLY from the Core-confirmed manual adapter;
legacy ProviderSession.borrow remains guarded. All I/O uses MockTransport.
"""
from pathlib import Path
import sys
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1]))
from cifereca.plugin_api import LendingActionAdmission
from cifereca_bnf_pret_http import ReaderHTTPClient, ReaderHTTPError
from cifereca_bnf_pret_borrow import BorrowForm, activation_form
from cifereca_bnf_pret_web import WebParseError

ORIGIN = "https://pret.bnf.fr"
RESOURCE = "book-synthetic"
CONFIRM_URL = ORIGIN + "/resources/book-synthetic/loans/new"
HTML = ('<html><a href="/resources/book-synthetic">Book</a>'
        '<form action="/loans/activation" method="post">'
        '<input type="hidden" name="authenticity_token" value="synthetic-csrf">'
        '<input type="hidden" name="resource_id" value="book-synthetic">'
        '</form></html>')
CLAIM = {"schema": 1, "operation": "loan.borrow",
         "status": "request_sent", "operation_id": "synthetic-claim"}
ADMISSION = LendingActionAdmission(
    "loan.borrow", "manual", "admitted", "admission_ready",
)


class BorrowPostOnceTests(unittest.TestCase):
    def make(self, *, status=200, mime="text/html", body="<html>submitted</html>",
             failure=None):
        calls = []

        def handler(request):
            calls.append(request)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.scheme, "https")
            self.assertEqual(request.url.host, "pret.bnf.fr")
            self.assertEqual(request.url.path, "/loans/activation")
            if failure is not None:
                raise failure
            return httpx.Response(status, text=body,
                                  headers={"content-type": mime,
                                           **({"location": "/my_profile/activity"}
                                              if status in (302, 307) else {})})

        client = ReaderHTTPClient(transport=httpx.MockTransport(handler))
        return client, calls

    def execute(self, client, **kwargs):
        return client.borrow_activation_once(
            activation_form(HTML, page_url=CONFIRM_URL, resource_id=RESOURCE),
            confirmation_url=CONFIRM_URL, resource_id=RESOURCE,
            admission=ADMISSION, claimed_receipt=CLAIM, confirmed=True,
            **kwargs,
        )

    def test_single_post_is_not_an_observed_loan_or_adept_import(self):
        client, calls = self.make()
        try:
            html = self.execute(client)
            self.assertEqual(html, "<html>submitted</html>")
            self.assertEqual(len(calls), 1)
            self.assertIn(b"authenticity_token=synthetic-csrf", calls[0].content)
            self.assertIn(b"resource_id=book-synthetic", calls[0].content)
            self.assertNotIn("confirmed", html)
        finally:
            client.close()

    def test_no_post_without_exact_manual_admission_and_claim(self):
        client, calls = self.make()
        try:
            form = activation_form(HTML, page_url=CONFIRM_URL, resource_id=RESOURCE)
            rejected = (
                {"confirmed": False}, {"confirmed": 1},
                {"admission": None},
                {"admission": LendingActionAdmission(
                    "loan.borrow", "scheduled", "admitted", "admission_ready")},
                {"admission": LendingActionAdmission(
                    "loan.borrow", "manual", "blocked", "confirmation_required")},
                {"admission": LendingActionAdmission(
                    "loan.return", "manual", "admitted", "admission_ready")},
                {"claimed_receipt": None},
                {"claimed_receipt": {**CLAIM, "status": "submitted"}},
                {"claimed_receipt": {**CLAIM, "operation": "loan.return"}},
                {"claimed_receipt": {**CLAIM, "operation_id": ""}},
            )
            defaults = dict(confirmation_url=CONFIRM_URL, resource_id=RESOURCE,
                            admission=ADMISSION, claimed_receipt=CLAIM,
                            confirmed=True)
            for override in rejected:
                with self.subTest(override=override):
                    params = {**defaults, **override}
                    with self.assertRaisesRegex(
                            ReaderHTTPError, "^borrow_not_authorized$"):
                        client.borrow_activation_once(form, **params)
            self.assertEqual(calls, [])
        finally:
            client.close()

    def test_wrong_book_or_form_cannot_post(self):
        client, calls = self.make()
        form = activation_form(HTML, page_url=CONFIRM_URL, resource_id=RESOURCE)
        try:
            for change in (
                {"resource_id": "wrong"},
                {"confirmation_url": ORIGIN + "/resources/wrong/loans/new"},
                {"form": BorrowForm(ORIGIN + "/loans/return", form.fields)},
                {"form": BorrowForm(ORIGIN + "/loans/activation",
                                    form.fields + (("authenticity_token", "duplicate"),))},
                {"form": BorrowForm(ORIGIN + "/loans/activation",
                                    (("resource_id", RESOURCE),))},
                {"form": BorrowForm(ORIGIN + "/loans/activation",
                                    (("authenticity_token", "valid"),
                                     ("resource_id", "other")))},
            ):
                with self.subTest(change=change):
                    with self.assertRaises((ReaderHTTPError, WebParseError)):
                        client.borrow_activation_once(
                            change.get("form", form),
                            confirmation_url=change.get("confirmation_url", CONFIRM_URL),
                            resource_id=change.get("resource_id", RESOURCE),
                            admission=ADMISSION, claimed_receipt=CLAIM,
                            confirmed=True,
                        )
            self.assertEqual(calls, [])
        finally:
            client.close()

    def test_any_redirect_is_refused_and_never_replayed(self):
        for status in (301, 302, 303, 307, 308):
            client, calls = self.make(status=status)
            try:
                with self.assertRaisesRegex(
                        ReaderHTTPError, "^post_redirect_refused$"):
                    self.execute(client)
                self.assertEqual(len(calls), 1)
            finally:
                client.close()

    def test_timeout_refusal_server_error_and_binary_are_one_post_only(self):
        cases = [
            dict(status=403, code="access_denied"),
            dict(status=429, code="rate_limited"),
            dict(status=503, code="http_unavailable"),
            dict(mime="application/octet-stream", code="unexpected_content"),
            dict(body="x" * (512 * 1024 + 1), code="html_limit"),
            dict(failure=httpx.ReadTimeout("synthetic timeout"),
                 code="transport_unavailable"),
        ]
        for case in cases:
            code = case.pop("code")
            client, calls = self.make(**case)
            try:
                with self.subTest(code=code):
                    with self.assertRaisesRegex(
                            ReaderHTTPError, "^" + code + "$"):
                        self.execute(client)
                self.assertEqual(len(calls), 1)
            finally:
                client.close()

    def test_public_bnf_provider_advertises_manual_only_borrow(self):
        from cifereca_bnf_pret import PLUGIN
        import json
        self.assertTrue(PLUGIN.descriptor.capabilities.borrow)
        manifest = json.loads((ROOT / "cifereca-plugin.json").read_text())
        capabilities = manifest["plugins"][0]["capabilities"]
        self.assertIn("acquisition.borrow", capabilities)


if __name__ == "__main__":
    unittest.main()
