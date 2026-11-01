"""#2055: safe read-only BnF borrower evidence remains unqualified.

These tests are synthetic, run only against httpx.MockTransport and must
never authorize a lender POST from a button, a successful login, or a CSRF.
"""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1]))

from cifereca.models import Account
from cifereca.plugin_api import BorrowerOperationEvidence, ProviderCapabilityError, ProviderConfigurationError
from cifereca_bnf_pret import BnfProviderSession
from cifereca_bnf_pret_http import ReaderHTTPClient
from cifereca_bnf_pret_web import CatalogRecord

RID = "synthetic-book"
ACTIVITY = '<section class="active-loans"></section>'
EXISTING = (
    '<section class="active-loans"><div class="js-loan">'
    '<a href="/resources/synthetic-book">Synthetic title</a>'
    '<a href="/loans/loan-synthetic">My loan</a></div></section>'
)
DETAIL = (
    '<article itemscope itemtype="https://schema.org/Book">'
    '<meta itemprop="name" content="Synthetic title">'
    '<a class="button-borrow" href="/resources/synthetic-book/loans/new">'
    'Borrow</a></article>'
)
UNAVAILABLE = DETAIL.replace(
    "</article>", '<span class="lb_unavailable">Unavailable</span></article>',
)


class EvidenceReadOnlyTests(unittest.TestCase):
    def make(self, *, detail=DETAIL, activity=ACTIVITY, after_activity=None,
             after_detail=None):
        calls = []
        def handler(request):
            calls.append((request.method, request.url.path))
            if request.method != "GET":
                self.fail("a read-only eligibility probe attempted mutation")
            if request.url.path == "/my_profile/activity":
                if after_activity:
                    after_activity()
                html = activity
            elif request.url.path == "/resources/" + RID:
                if after_detail:
                    after_detail()
                html = detail
            else:
                self.fail("unexpected API, confirmation, token or SSO request")
            return httpx.Response(200, text=html,
                                  headers={"content-type": "text/html"})
        client = ReaderHTTPClient(transport=httpx.MockTransport(handler))
        session = BnfProviderSession(
            client=client, credential_id="credential-synthetic",
            label="BnF synthetic", credentials={}, options={})
        account = Account(
            display_name="Synthetic", unique_id="credential-synthetic",
            identity_id="default", provider_id="bnf-pret",
            credential_id="credential-synthetic",
        )
        session._accounts = [account]
        session.connected = True
        item = session._search_result(CatalogRecord(RID, "Synthetic title"))
        return session, account, item, calls

    def test_book_button_and_authentication_are_not_positive_permission(self):
        session, account, item, calls = self.make()
        try:
            facts = session.borrower_eligibility_for(item, account)
            self.assertIs(type(facts), BorrowerOperationEvidence)
            self.assertEqual(
                (facts.account_state, facts.quota_state,
                 facts.item_offer, facts.permission),
                ("unknown", "unknown", "unknown", "unknown"),
            )
            self.assertEqual(
                (facts.identity_id, facts.borrower_id, facts.record_id),
                ("default", account.unique_id, RID),
            )
            self.assertEqual(calls, [
                ("GET", "/my_profile/activity"),
                ("GET", "/resources/" + RID),
            ])
            self.assertFalse(session.capabilities.borrow)
            self.assertEqual(session._borrow_post_attempts, set())
        finally:
            session.close()

    def test_explicit_negative_marker_does_not_invent_available_quota(self):
        session, account, item, calls = self.make(detail=UNAVAILABLE)
        try:
            facts = session.borrower_eligibility_for(item, account)
            self.assertEqual(facts.item_offer, "unavailable")
            self.assertEqual(facts.quota_state, "unknown")
            self.assertEqual(facts.permission, "unknown")
            self.assertEqual(facts.account_state, "unknown")
            self.assertEqual(len(calls), 2)
        finally:
            session.close()

    def test_already_observed_loan_refuses_without_detail_get(self):
        session, account, item, calls = self.make(activity=EXISTING)
        try:
            with self.assertRaisesRegex(
                    ProviderCapabilityError, "borrow_existing_loan"):
                session.borrower_eligibility_for(item, account)
            self.assertEqual(calls, [("GET", "/my_profile/activity")])
        finally:
            session.close()

    def test_other_account_and_wrong_resource_never_get(self):
        session, account, item, calls = self.make()
        try:
            for a, candidate in (
                (replace(account), item),
                (account, replace(item, source_credential="wrong")),
                (account, replace(item, source_provider="paris")),
                (account, replace(item, record_id="")),
                (account, replace(item, medium="physical")),
            ):
                with self.subTest(a=a is account, rid=candidate.record_id):
                    with self.assertRaisesRegex(
                            ProviderConfigurationError, "account_scope_mismatch"):
                        session.borrower_eligibility_for(candidate, a)
            self.assertEqual(calls, [])
        finally:
            session.close()

    def test_rebind_during_detail_read_invalidates_observation(self):
        changed = {}
        def change():
            changed["item"].source_collection = "other-collection"
        session, account, item, calls = self.make(after_detail=change)
        changed["item"] = item
        try:
            with self.assertRaisesRegex(
                    ProviderConfigurationError, "account_scope_mismatch"):
                session.borrower_eligibility_for(item, account)
            self.assertEqual(len(calls), 2)
        finally:
            session.close()

    def test_rebind_during_activity_read_never_gets_book(self):
        changed = {}
        def change():
            changed["account"].identity_id = "other"
        session, account, item, calls = self.make(after_activity=change)
        changed["account"] = account
        try:
            with self.assertRaisesRegex(
                    ProviderConfigurationError, "account_scope_mismatch"):
                session.borrower_eligibility_for(item, account)
            self.assertEqual(calls, [("GET", "/my_profile/activity")])
        finally:
            session.close()

    def test_no_borrow_while_unqualified_even_if_new_method_exists(self):
        from cifereca_bnf_pret import PLUGIN
        import json
        self.assertFalse(PLUGIN.descriptor.capabilities.borrow)
        manifest = json.loads((ROOT / "cifereca-plugin.json").read_text())
        self.assertNotIn("acquisition.borrow",
                         manifest["plugins"][0]["capabilities"])


if __name__ == "__main__":
    unittest.main()
