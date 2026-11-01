"""Read-only, scoped HTTPX BnF borrowing preflight (#2055).

The tests exercise the *real provider session* and its Core HTTPX transport
with synthetic responses. They must never submit a borrow POST.
"""
from pathlib import Path
import sys
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1]))
from cifereca.plugin_api import Account, ProviderError, ProviderConfigurationError
from cifereca_bnf_pret import BnfProviderSession
from cifereca_bnf_pret_http import ReaderHTTPClient
from cifereca_bnf_pret_web import CatalogRecord

ORIGIN = "https://pret.bnf.fr"
RID = "syntheticR1"
ACT = '<section class="active-loans"></section>'
DETAIL = ('<article itemscope itemtype="https://schema.org/Book">'
          '<meta itemprop="name" content="Example">'
          '<a class="button-borrow" href="/resources/syntheticR1/loans/new">'
          'Emprunter</a></article>')
CONFIRM = ('<a href="/resources/syntheticR1">Example</a>'
           '<form method="post" action="/loans/activation">'
           '<input type="hidden" name="authenticity_token" value="DO_NOT_LOG">'
           '<input type="hidden" name="resource_id" value="syntheticR1">'
           '</form>')


class BorrowReadOnlyHTTPTests(unittest.TestCase):
    def make(self, *, activity=ACT, detail=DETAIL, confirmation=CONFIRM,
             after_detail=None, after_confirmation=None, confirmation_status=200):
        calls = []

        def handler(r):
            calls.append((r.method, r.url.path))
            self.assertEqual(r.method, "GET", "borrow preflight must NEVER POST")
            if r.url.path == "/my_profile/activity":
                return httpx.Response(200, text=activity, headers={"content-type": "text/html"})
            if r.url.path == "/resources/" + RID:
                if after_detail:
                    after_detail()
                return httpx.Response(200, text=detail, headers={"content-type": "text/html"})
            if r.url.path == "/resources/" + RID + "/loans/new":
                if after_confirmation:
                    after_confirmation()
                return httpx.Response(confirmation_status, text=confirmation,
                                      headers={"content-type": "text/html",
                                               **({"location": "/my_profile/activity"}
                                                  if confirmation_status == 302 else {})})
            self.fail("unexpected request")

        session = BnfProviderSession(
            client=ReaderHTTPClient(transport=httpx.MockTransport(handler)),
            credential_id="credential-synthetic", label="BnF", credentials={},
            options={},
        )
        account = Account(
            display_name="Synthetic reader", unique_id="credential-synthetic",
            provider_id="bnf-pret", credential_id="credential-synthetic",
            provider_label="BnF",
        )
        account.identity_id = "identity-synthetic"
        session._accounts = [account]
        session.connected = True  # Fixtures replace only the SSO, not the provider logic.
        item = session._search_result(CatalogRecord(RID, "Example"))
        return session, account, item, calls

    def test_real_session_observes_confirmation_without_mutating(self):
        session, account, item, calls = self.make()
        try:
            result = session.borrow_preflight(item, account)
            self.assertEqual(result, {
                "status": "unverified", "reason_code": "borrow_confirmation_observed"})
            self.assertEqual(calls, [
                ("GET", "/my_profile/activity"),
                ("GET", "/resources/" + RID),
                ("GET", "/resources/" + RID + "/loans/new"),
            ])
            self.assertFalse(session.capabilities.borrow)
            self.assertNotIn("DO_NOT_LOG", str(result))
            self.assertNotIn("authenticity_token", str(result))
        finally:
            session.close()

    def test_existing_positive_loan_refuses_before_book_confirmation(self):
        active = ('<section class="active-loans"><div class="js-loan">'
                  '<a href="/resources/syntheticR1">Example</a>'
                  '<a href="/loans/loan-synthetic">Jeton</a>'
                  '</div></section>')
        session, account, item, calls = self.make(activity=active)
        try:
            with self.assertRaisesRegex(ProviderError, "borrow_existing_loan"):
                session.borrow_preflight(item, account)
            self.assertEqual(calls, [("GET", "/my_profile/activity")])
        finally:
            session.close()

    def test_fresh_identity_change_prevents_confirmation_get(self):
        selected = []
        session, account, item, calls = self.make()
        session.client.http.close()

        def mutation():
            account.identity_id = "another-identity"
        session, account, item, calls = self.make(after_detail=mutation)
        try:
            with self.assertRaisesRegex(ProviderConfigurationError, "account_scope_mismatch"):
                session.borrow_preflight(item, account)
            self.assertEqual(len(calls), 2)
        finally:
            session.close()

    def test_rebind_during_confirmation_invalidates_result(self):
        captured = []
        session, account, item, calls = self.make()
        session.close()
        def hook():
            account.credential_id = "other-credential"
        session, account, item, calls = self.make(after_confirmation=hook)
        try:
            with self.assertRaisesRegex(ProviderConfigurationError, "account_scope_mismatch"):
                session.borrow_preflight(item, account)
            self.assertEqual(len(calls), 3)
        finally:
            session.close()

    def test_missing_csrf_is_refused_without_leaking_html_or_posting(self):
        session, account, item, calls = self.make(
            confirmation=CONFIRM.replace("authenticity_token", "other_field"))
        try:
            with self.assertRaisesRegex(ProviderError, "^borrow_csrf_unverified$"):
                session.borrow_preflight(item, account)
            self.assertEqual(len(calls), 3)
        finally:
            session.close()

    def test_post_or_redirect_is_never_converted_to_mutation(self):
        session, account, item, calls = self.make(confirmation_status=302)
        try:
            with self.assertRaisesRegex(ProviderError, "redirect_invalid"):
                session.borrow_preflight(item, account)
            self.assertEqual(len(calls), 3)
        finally:
            session.close()

    def test_foreign_account_and_item_refuse_before_any_request(self):
        session, account, item, calls = self.make()
        try:
            foreign = Account(display_name="Other", unique_id="other",
                              provider_id="bnf-pret", credential_id="credential-synthetic")
            with self.assertRaisesRegex(ProviderConfigurationError, "account_scope_mismatch"):
                session.borrow_preflight(item, foreign)
            item.source_credential = "another-credential"
            with self.assertRaisesRegex(ProviderConfigurationError, "account_scope_mismatch"):
                session.borrow_preflight(item, account)
            self.assertEqual(calls, [])
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
