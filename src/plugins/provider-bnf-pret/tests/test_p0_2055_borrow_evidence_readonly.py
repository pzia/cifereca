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
            self.assertTrue(session.capabilities.borrow)
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

    def test_conflicting_unqualified_labels_never_grant_permission(self):
        activity = (
            '<p>Vous avez encore droit à 0 prêt</p>'
            '<p>Vous avez encore droit à 4 prêts</p>'
            '<p>Pass BnF lecture/culture valide</p>'
            '<p>Pass BnF lecture/culture expiré</p>' + ACTIVITY
        )
        detail = DETAIL.replace(
            "</article>", "<p>Plusieurs exemplaires disponibles</p></article>",
        )
        session, account, item, calls = self.make(activity=activity, detail=detail)
        try:
            facts = session.borrower_eligibility_for(item, account)
            self.assertEqual(
                (facts.account_state, facts.quota_state,
                 facts.item_offer, facts.permission),
                ("unknown", "unknown", "unknown", "unknown"),
            )
            self.assertEqual(calls, [
                ("GET", "/my_profile/activity"),
                ("GET", "/resources/" + RID),
            ])
            self.assertTrue(session.capabilities.borrow)
            self.assertEqual(session._borrow_post_attempts, set())
        finally:
            session.close()

    def test_negative_detail_is_not_published_as_core_admission_evidence(self):
        session, account, item, calls = self.make(detail=UNAVAILABLE)
        try:
            facts = session.borrower_eligibility_for(item, account)
            self.assertEqual(facts.item_offer, "unavailable")
            self.assertIsNone(vars(session).get("borrower_eligibility_snapshot"))
            self.assertTrue(session.capabilities.borrow)
            self.assertEqual(session._borrow_post_attempts, set())
            self.assertEqual(len(calls), 2)
        finally:
            session.close()

    def test_epoch_change_during_detail_discards_even_negative_evidence(self):
        changed = {}
        def change():
            changed["session"]._epoch = "synthetic-rebound-epoch"
        session, account, item, calls = self.make(
            detail=UNAVAILABLE, after_detail=change)
        changed["session"] = session
        try:
            with self.assertRaisesRegex(
                    ProviderConfigurationError, "account_scope_mismatch"):
                session.borrower_eligibility_for(item, account)
            self.assertIsNone(vars(session).get("borrower_eligibility_snapshot"))
            self.assertEqual(len(calls), 2)
            self.assertEqual(session._borrow_post_attempts, set())
        finally:
            session.close()

    def test_hidden_or_nested_offer_marker_is_unknown(self):
        for marker in (
            '<div style="display: none"><span class="lb_unavailable">No</span></div>',
            '<div style="visibility:hidden"><span class="lb_unavailable">No</span></div>',
            '<div itemscope itemtype="https://schema.org/Organization">'
            '<span class="lb_unavailable">No</span></div>',
            '<span class="lb_unavailable">No',
        ):
            with self.subTest(marker=marker):
                session, account, item, calls = self.make(
                    detail=DETAIL.replace("</article>", marker + "</article>"))
                try:
                    facts = session.borrower_eligibility_for(item, account)
                    self.assertEqual(facts.item_offer, "unknown")
                    self.assertEqual(len(calls), 2)
                finally:
                    session.close()

    def test_css_hidden_book_ancestor_does_not_attest_unavailability(self):
        for style in ("display: none", "visibility: hidden"):
            with self.subTest(style=style):
                session, account, item, calls = self.make(
                    detail=f'<div style="{style}">' + UNAVAILABLE + '</div>')
                try:
                    facts = session.borrower_eligibility_for(item, account)
                    self.assertEqual(facts.item_offer, "unknown")
                    self.assertEqual(len(calls), 2)
                finally:
                    session.close()

    def test_unavailable_marker_requires_own_visible_text(self):
        for child in (
            '<span style="display:none">Unavailable</span>',
            '<span style="visibility:hidden">Unavailable</span>',
            '<span itemscope itemtype="https://schema.org/Organization">Unavailable</span>',
            '<span>Unavailable',
        ):
            with self.subTest(child=child):
                marker = '<div class="lb_unavailable">' + child + '</div>'
                session, account, item, calls = self.make(
                    detail=DETAIL.replace("</article>", marker + "</article>"))
                try:
                    facts = session.borrower_eligibility_for(item, account)
                    self.assertEqual(facts.item_offer, "unknown")
                    self.assertEqual(len(calls), 2)
                finally:
                    session.close()

    def test_informational_observation_is_separate_scoped_and_dated(self):
        from time import time
        from cifereca_bnf_pret_borrow_qualification import BorrowerInformationalObservation
        session, account, item, calls = self.make(detail=UNAVAILABLE)
        try:
            before = int(time())
            observation = session.borrower_information_for(item, account)
            self.assertIs(type(observation), BorrowerInformationalObservation)
            self.assertEqual(observation.item_offer, "unavailable")
            self.assertEqual(observation.pass_state, "unknown")
            self.assertIsNone(observation.remaining_quota)
            self.assertIsNone(observation.available_copies)
            self.assertEqual(observation.permission, "unknown")
            self.assertEqual(observation.session_epoch, session._epoch)
            self.assertEqual(observation.identity_id, account.identity_id)
            self.assertEqual(observation.borrower_id, account.unique_id)
            self.assertEqual(observation.credential_id, account.credential_id)
            self.assertEqual(observation.provider_id, account.provider_id)
            self.assertEqual(observation.record_id, RID)
            self.assertLessEqual(before, observation.observed_at)
            self.assertLessEqual(observation.observed_at, int(time()))
            self.assertNotIn("borrower_eligibility_snapshot", vars(session))
            self.assertNotIn(RID, repr(observation))
            self.assertEqual(len(calls), 2)
            self.assertEqual(session._borrow_post_attempts, set())
        finally:
            session.close()

    def test_advisory_display_projection_is_local_and_session_scoped(self):
        session, account, item, calls = self.make(detail=UNAVAILABLE)
        try:
            observation = session.borrower_information_for(item, account)
            values = session.borrower_information_display(observation, item, account)
            self.assertEqual(values, {
                "observed_at": observation.observed_at,
                "pass_state": "unknown", "remaining_quota": None,
                "available_copies": None, "item_offer": "unavailable",
                "permission": "unknown",
            })
            self.assertIsNone(session.borrower_information_display(
                observation, replace(item, record_id="other"), account))
            self.assertIsNone(session.borrower_information_display(
                observation, item, replace(account)))
            session._epoch = "changed"
            self.assertIsNone(session.borrower_information_display(
                observation, item, account))
            self.assertEqual(len(calls), 2)
            self.assertEqual(session._borrow_post_attempts, set())
        finally:
            session.close()

    def test_advisory_foreign_account_and_documentary_notice_never_get(self):
        session, account, item, calls = self.make()
        try:
            for candidate in (
                replace(item, source_account_id="foreign"),
                replace(item, metadata={"catalogue_access": "shared_read_only"}),
                replace(item, metadata={"search_provenance": "known_catalog_stale"}),
            ):
                with self.assertRaisesRegex(ProviderConfigurationError, "account_scope_mismatch"):
                    session.borrower_information_for(candidate, account)
            self.assertEqual(calls, [])
        finally:
            session.close()

    def test_advisory_notice_account_rebind_during_read_is_discarded(self):
        changed = {}
        def change():
            changed["item"].source_account_id = "foreign"
        session, account, item, calls = self.make(after_detail=change)
        changed["item"] = item
        try:
            with self.assertRaisesRegex(ProviderConfigurationError, "account_scope_mismatch"):
                session.borrower_information_for(item, account)
            self.assertEqual(len(calls), 2)
        finally:
            session.close()

    def test_advisory_projection_rejects_changed_binding_and_invalid_date(self):
        session, account, item, calls = self.make()
        try:
            observation = session.borrower_information_for(item, account)
            for candidate in (
                replace(item, source_account_id="foreign"),
                replace(item, source_account_id=account.unique_id),
                replace(item, metadata={"search_provenance": "known_catalog_stale"}),
            ):
                self.assertIsNone(session.borrower_information_display(
                    observation, candidate, account))
            self.assertIsNone(session.borrower_information_display(
                replace(observation, observed_at=True), item, account))
            self.assertEqual(len(calls), 2)
        finally:
            session.close()

    def test_integrated_core_advisory_uses_real_scope_and_provider(self):
        from types import SimpleNamespace as NS
        from cifereca.core.borrower_information import prepare_borrower_information
        from cifereca.gui.borrower_information_action import advisory_text

        for revocation in ("none", "source_before", "provider_before",
                           "source_during", "connection_before", "account_before"):
            with self.subTest(revocation=revocation):
                state = {}
                def after_detail():
                    if revocation == "source_during":
                        state["service"]._source_context_epoch += 1

                session, account, item, calls = self.make(
                    detail=UNAVAILABLE, after_detail=after_detail)
                try:
                    identity = NS(identity_id=account.identity_id)
                    connection = {
                        "identity_id": account.identity_id,
                        "provider_id": account.provider_id,
                        "credential_id": account.credential_id,
                        "enabled": True,
                    }
                    service = NS(
                        _source_context_epoch=0,
                        accounts=[account],
                        _sessions={(account.provider_id, account.credential_id): session},
                        _session_key=lambda p, c: (p, c),
                        identity_by_id=lambda i: identity if i == identity.identity_id else None,
                        _provider_enabled_for_identity=lambda p, i: True,
                        _session_for_account=lambda a: session if a is account else None,
                        storage=NS(
                            provider_connections=lambda **kwargs: [connection],
                            identity_for_connection=lambda p, c: identity.identity_id,
                        ),
                    )
                    state["service"] = service
                    prepared = prepare_borrower_information(
                        service, item, identity_id=identity.identity_id,
                        account_id=account.unique_id,
                    )
                    self.assertTrue(callable(prepared))
                    self.assertEqual(calls, [])
                    if revocation == "source_before":
                        service._source_context_epoch += 2
                    elif revocation == "provider_before":
                        session._epoch = "synthetic-reconnected"
                    elif revocation == "connection_before":
                        connection["enabled"] = False
                    elif revocation == "account_before":
                        service.accounts = [replace(account)]
                    values = prepared()
                    if revocation == "none":
                        self.assertEqual(values["item_offer"], "unavailable")
                        self.assertIsNone(values["remaining_quota"])
                        self.assertEqual(values["permission"], "unknown")
                        message = advisory_text(values)
                        self.assertIn("Indisponibilité observée", message)
                        self.assertIn("Permission : inconnue", message)
                        self.assertNotIn(account.unique_id, message)
                    else:
                        self.assertIsNone(values)
                    self.assertEqual(calls, [
                        ("GET", "/my_profile/activity"),
                        ("GET", "/resources/" + RID),
                    ] if revocation in ("none", "source_during") else [])
                    self.assertIsNone(prepared())
                    self.assertEqual(session._borrow_post_attempts, set())
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

    def test_manual_only_borrow_is_published_but_legacy_direct_borrow_is_refused(self):
        from cifereca_bnf_pret import PLUGIN
        import json
        self.assertTrue(PLUGIN.descriptor.capabilities.borrow)
        manifest = json.loads((ROOT / "cifereca-plugin.json").read_text())
        self.assertIn("acquisition.borrow",
                         manifest["plugins"][0]["capabilities"])
        from cifereca.plugin_api import ProviderCapabilityError
        session, account, item, calls = self.make()
        try:
            with self.assertRaisesRegex(
                    ProviderCapabilityError,
                    "bnf_borrow_requires_core_dispatch"):
                session.borrow(item, account)
            self.assertEqual(calls, [])
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
