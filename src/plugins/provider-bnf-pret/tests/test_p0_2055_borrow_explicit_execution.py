"""#2055 dormant manual BnF execution: one POST, independent loan evidence.

All endpoints synthetic, via httpx.MockTransport. The public BnF capability
remains OFF and no CoreUI path calls this method.
"""
from pathlib import Path
import sys
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1]))

from cifereca.models import Account, Loan
from cifereca.plugin_api import (
    LendingActionAdmission, ProviderCapabilityError, ProviderConfigurationError,
    BorrowRequestNotSubmitted,
)
from cifereca_bnf_pret import BnfProviderSession
from cifereca_bnf_pret_http import ReaderHTTPClient
from cifereca_bnf_pret_web import CatalogRecord

RID = "synthetic-book"
BEFORE = '<section class="active-loans"></section>'
AFTER = ('<section class="active-loans"><div class="js-loan">'
         '<a href="/resources/synthetic-book">Synthetic title</a>'
         '<a href="/loans/loan-synthetic">Open loan</a>'
         '</div></section>')
DETAIL = ('<article itemscope itemtype="https://schema.org/Book">'
          '<a class="button-borrow" href="/resources/synthetic-book/loans/new">'
          'Emprunter</a></article>')
CONFIRM = ('<a href="/resources/synthetic-book">Book</a>'
           '<form method="post" action="/loans/activation">'
           '<input type="hidden" name="authenticity_token" value="synthetic-csrf">'
           '<input type="hidden" name="resource_id" value="synthetic-book">'
           '</form>')
RECEIPT = '<a href="/loans/loan-synthetic">Prêt obtenu</a>'
ADMISSION = LendingActionAdmission(
    "loan.borrow", "manual", "admitted", "admission_ready")
CLAIM = {"schema": 1, "operation": "loan.borrow",
         "status": "request_sent", "operation_id": "op-once"}
MEDIA = "synthetic-warehouse-publication-9780000000000-epub"
MEDIA_CONFIRM = "/media/" + MEDIA + "/loans_confirmation"
MEDIA_ACTIVATE = "/media/" + MEDIA + "/loans_activation"
MEDIA_DETAIL = (
    '<article itemscope itemtype="https://schema.org/Book">'
    '<form method="get" action="' + MEDIA_CONFIRM + '">'
    '<input type="hidden" name="_resource_id" value="' + RID + '">'
    '<input type="hidden" name="notify" value="1">'
    '<input type="hidden" name="email" value="synthetic@example.invalid">'
    '<button type="submit" class="button-borrow button">Emprunter</button>'
    '</form></article>')
MEDIA_FORM = ('<a href="/resources/' + RID + '">Book</a>'
              '<form method="post" action="' + MEDIA_ACTIVATE
              + '?email=synthetic%40example.invalid&amp;notify=1&amp;_resource_id='
              + RID + '">'
              '<input type="hidden" name="authenticity_token" value="synthetic-csrf">'
              '<button type="submit" class="button-borrow">Confirmer</button>'
              '</form>')


class BnfExplicitExecutionTests(unittest.TestCase):
    def make(self, *, existing=False, after_detail=None, after_post=None,
             post_status=200, post_body=RECEIPT, activity_after=AFTER,
             post_failure=None, detail_html=DETAIL, media=False,
             media_get_mutates=False, media_get_fails=False,
             media_form_html=MEDIA_FORM):
        state = {"post_count": 0, "activity_reads": 0, "calls": [],
                 "media_get_count": 0, "media_loan_observed": False}

        def handler(request):
            path = request.url.path
            state["calls"].append((request.method, path))
            self.assertEqual(request.url.host, "pret.bnf.fr")
            if request.method == "GET" and path == "/my_profile/activity":
                state["activity_reads"] += 1
                return httpx.Response(200, text=(
                    AFTER if existing else activity_after
                    if (state["post_count"] or state["media_loan_observed"])
                    else BEFORE),
                    headers={"content-type": "text/html"})
            if request.method == "GET" and path == "/resources/" + RID:
                if after_detail:
                    after_detail()
                return httpx.Response(200, text=detail_html,
                                      headers={"content-type": "text/html"})
            if request.method == "GET" and path == "/resources/" + RID + "/loans/new":
                return httpx.Response(200, text=CONFIRM,
                                      headers={"content-type": "text/html"})
            if request.method == "GET" and path == MEDIA_CONFIRM:
                self.assertTrue(media)
                self.assertEqual(dict(request.url.params),
                                 {"_resource_id": RID, "notify": "1",
                                  "email": "synthetic@example.invalid"})
                state["media_get_count"] += 1
                if media_get_mutates:
                    state["media_loan_observed"] = True
                if media_get_fails:
                    raise httpx.ReadTimeout("synthetic")
                return httpx.Response(200, text=media_form_html,
                                      headers={"content-type": "text/html"})
            if request.method == "POST" and path in {"/loans/activation", MEDIA_ACTIVATE}:
                state["post_count"] += 1
                if path == MEDIA_ACTIVATE:
                    self.assertEqual(dict(request.url.params),
                                     {"_resource_id": RID, "notify": "1",
                                      "email": "synthetic@example.invalid"})
                    self.assertNotIn(b"_resource_id=", request.content)
                self.assertIn(b"authenticity_token=synthetic-csrf", request.content)
                if after_post:
                    after_post()
                if post_failure:
                    raise post_failure
                return httpx.Response(post_status, text=post_body,
                                      headers={"content-type": "text/html"})
            self.fail("unexpected request")

        client = ReaderHTTPClient(transport=httpx.MockTransport(handler))
        session = BnfProviderSession(
            client=client, credential_id="credential-synthetic",
            label="BnF", credentials={}, options={})
        account = Account(
            display_name="Synthetic account", unique_id="credential-synthetic",
            provider_id="bnf-pret", credential_id="credential-synthetic",
            identity_id="default")
        session._accounts = [account]
        session.connected = True
        item = session._search_result(CatalogRecord(RID, "Synthetic title"))
        return session, account, item, state

    def execute(self, session, account, item, **overrides):
        kwargs = dict(admission=ADMISSION, claimed_receipt=CLAIM,
                      confirmed=True, core_scope_current=lambda: True)
        kwargs.update(overrides)
        return session.borrow_explicit_with_claim(item, account, **kwargs)

    def test_single_post_then_independent_positive_loan_not_adept(self):
        session, account, item, state = self.make()
        try:
            loan = self.execute(session, account, item)
            self.assertIsInstance(loan, Loan)
            self.assertEqual((loan.id, loan.record_id),
                             ("loan-synthetic", RID))
            self.assertEqual((loan.source_account_id, loan.identity_id),
                             ("credential-synthetic", "default"))
            self.assertEqual(loan.state, "active")
            self.assertEqual(state["post_count"], 1)
            self.assertEqual(state["activity_reads"], 2)
            self.assertEqual(state["calls"], [
                ("GET", "/my_profile/activity"),
                ("GET", "/resources/" + RID),
                ("GET", "/resources/" + RID + "/loans/new"),
                ("POST", "/loans/activation"),
                ("GET", "/my_profile/activity"),
            ])
            self.assertTrue(session.capabilities.borrow)
            self.assertEqual(session._observed_loans, {"loan-synthetic": RID})
            with self.assertRaisesRegex(
                    ProviderCapabilityError, "borrow_claim_spent"):
                self.execute(session, account, item)
            self.assertEqual(state["post_count"], 1)
        finally:
            session.close()

    def test_observed_media_get_confirmation_then_single_post(self):
        session, account, item, state = self.make(
            media=True, detail_html=MEDIA_DETAIL)
        try:
            loan = self.execute(session, account, item)
            self.assertEqual((loan.id, loan.record_id), ("loan-synthetic", RID))
            self.assertEqual(state["post_count"], 1)
            self.assertEqual(state["media_get_count"], 1)
            self.assertEqual(state["activity_reads"], 3)
            self.assertEqual(state["calls"], [
                ("GET", "/my_profile/activity"),
                ("GET", "/resources/" + RID),
                ("GET", MEDIA_CONFIRM),
                ("GET", "/my_profile/activity"),
                ("POST", MEDIA_ACTIVATE),
                ("GET", "/my_profile/activity"),
            ])
            with self.assertRaisesRegex(
                    ProviderCapabilityError, "borrow_claim_spent"):
                self.execute(session, account, item)
        finally:
            session.close()

    def test_media_get_possible_mutation_never_triggers_post(self):
        session, account, item, state = self.make(
            media=True, detail_html=MEDIA_DETAIL, media_get_mutates=True)
        try:
            with self.assertRaisesRegex(
                    Exception, "borrow_confirmation_side_effect_unverified") as caught:
                self.execute(session, account, item)
            self.assertNotIsInstance(caught.exception, BorrowRequestNotSubmitted)
            self.assertEqual(state["media_get_count"], 1)
            self.assertEqual(state["post_count"], 0)
            with self.assertRaisesRegex(
                    ProviderCapabilityError, "borrow_claim_spent"):
                self.execute(session, account, item)
        finally:
            session.close()

    def test_media_get_timeout_keeps_claim_spent_without_post(self):
        session, account, item, state = self.make(
            media=True, detail_html=MEDIA_DETAIL, media_get_fails=True)
        try:
            with self.assertRaisesRegex(
                    Exception, "transport_unavailable") as caught:
                self.execute(session, account, item)
            self.assertNotIsInstance(caught.exception, BorrowRequestNotSubmitted)
            self.assertEqual(state["media_get_count"], 1)
            self.assertEqual(state["post_count"], 0)
            with self.assertRaisesRegex(
                    ProviderCapabilityError, "borrow_claim_spent"):
                self.execute(session, account, item)
        finally:
            session.close()

    def test_media_get_invalid_post_action_never_releases_claim(self):
        # 01/11 capture reached GET confirmation 200 but rejected the
        # following POST action before sending it. GET is potentially
        # mutating: a parser refusal must not re-arm this edition.
        mismatched = MEDIA_FORM.replace("notify=1", "notify=0")
        session, account, item, state = self.make(
            media=True, detail_html=MEDIA_DETAIL,
            media_form_html=mismatched)
        try:
            with self.assertRaisesRegex(Exception, "borrow_url_refused") as caught:
                self.execute(session, account, item)
            self.assertNotIsInstance(caught.exception, BorrowRequestNotSubmitted)
            self.assertEqual(state["media_get_count"], 1)
            self.assertEqual(state["post_count"], 0)
            with self.assertRaisesRegex(
                    ProviderCapabilityError, "borrow_claim_spent"):
                self.execute(session, account, item)
        finally:
            session.close()

    def test_missing_permission_or_claim_blocks_all_network(self):
        session, account, item, state = self.make()
        try:
            tests = (
                {"confirmed": False},
                {"confirmed": 1},
                {"admission": None},
                {"admission": LendingActionAdmission(
                    "loan.borrow", "manual", "blocked", "permission_unverified")},
                {"claimed_receipt": {**CLAIM, "status": "submitted"}},
                {"core_scope_current": None},
                {"core_scope_current": lambda: False},
            )
            for kw in tests:
                with self.subTest(kwargs=list(kw)):
                    with self.assertRaises((ProviderCapabilityError, ProviderConfigurationError)):
                        self.execute(session, account, item, **kw)
            self.assertEqual(state["calls"], [])
        finally:
            session.close()

    def test_positive_existing_loan_refuses_before_post(self):
        session, account, item, state = self.make(existing=True)
        try:
            with self.assertRaisesRegex(
                    ProviderCapabilityError, "borrow_existing_loan"):
                self.execute(session, account, item)
            self.assertEqual(state["post_count"], 0)
            self.assertEqual(state["activity_reads"], 1)
        finally:
            session.close()

    def test_scope_rebind_after_fresh_detail_blocks_post(self):
        holder = {}
        def change():
            holder["account"].identity_id = "other"
        session, account, item, state = self.make(after_detail=change)
        holder["account"] = account
        try:
            with self.assertRaisesRegex(Exception, "account_scope_mismatch"):
                self.execute(session, account, item)
            self.assertEqual(state["post_count"], 0)
        finally:
            session.close()

    def test_scope_rebind_after_post_never_proves_loan_or_retries(self):
        holder = {}
        def change():
            holder["account"].unique_id = "other"
        session, account, item, state = self.make(after_post=change)
        holder["account"] = account
        try:
            with self.assertRaisesRegex(Exception, "account_scope_mismatch"):
                self.execute(session, account, item)
            self.assertEqual(state["post_count"], 1)
            self.assertEqual(state["activity_reads"], 1)
            self.assertEqual(session._observed_loans, {})
        finally:
            session.close()

    def test_ambiguous_receipt_does_not_confirm_a_loan(self):
        session, account, item, state = self.make(post_body="<p>OK</p>")
        try:
            with self.assertRaisesRegex(Exception, "borrow_receipt_unverified"):
                self.execute(session, account, item)
            self.assertEqual(state["post_count"], 1)
            self.assertEqual(session._observed_loans, {})
            with self.assertRaisesRegex(
                    ProviderCapabilityError, "borrow_claim_spent"):
                self.execute(session, account, item)
            self.assertEqual(state["post_count"], 1)
        finally:
            session.close()

    def test_last_second_disabled_book_control_refuses_before_post(self):
        disabled = DETAIL.replace(
            '<a class="button-borrow"',
            '<a disabled class="button-borrow"',
        )
        session, account, item, state = self.make(detail_html=disabled)
        try:
            with self.assertRaisesRegex(
                    BorrowRequestNotSubmitted, "borrow_action_unverified"):
                self.execute(session, account, item)
            self.assertEqual(state["post_count"], 0)
            self.assertEqual(state["calls"], [
                ("GET", "/my_profile/activity"),
                ("GET", "/resources/" + RID),
            ])
            self.assertEqual(session._observed_loans, {})
        finally:
            session.close()

    def test_availability_badge_with_active_borrow_button_defers_to_bnf(self):
        # Real capture exposed both classes on one Book: the manual user
        # has visually checked the selected book on the official portal.
        # The ambiguous status badge is not the permission authority.
        ambiguous = DETAIL.replace(
            "</article>",
            '<span class="lb_unavailable">Indisponible</span></article>',
        )
        session, account, item, state = self.make(detail_html=ambiguous)
        try:
            loan = self.execute(session, account, item)
            self.assertEqual((loan.id, loan.record_id),
                             ("loan-synthetic", RID))
            self.assertEqual(state["post_count"], 1)
            self.assertEqual(state["activity_reads"], 2)
        finally:
            session.close()

    def test_bnf_quota_refusal_one_post_and_no_second_attempt(self):
        session, account, item, state = self.make(post_status=403)
        try:
            with self.assertRaisesRegex(Exception, "access_denied") as error:
                self.execute(session, account, item)
            self.assertNotIsInstance(
                error.exception, BorrowRequestNotSubmitted,
            )
            self.assertEqual(state["post_count"], 1)
            with self.assertRaisesRegex(
                    ProviderCapabilityError, "borrow_claim_spent"):
                self.execute(session, account, item)
            self.assertEqual(state["post_count"], 1)
            self.assertEqual(session._observed_loans, {})
        finally:
            session.close()

    def test_reconnect_reissues_epoch_and_cannot_reuse_prior_scope(self):
        session, account, item, state = self.make()
        try:
            original_epoch = session._epoch
            session.credentials = {
                "username": "synthetic-user", "password": "synthetic-password"}
            session.client.login = lambda _u, _p: BEFORE
            replacement, = session.connect()
            self.assertNotEqual(session._epoch, original_epoch)
            self.assertIsNot(replacement, account)
            self.assertEqual(replacement.credential_id, account.credential_id)
            self.assertFalse(session._borrow_post_attempts)
            self.assertEqual(state["calls"], [])
        finally:
            session.close()

    def test_timeout_still_spends_claim_without_automatic_second_post(self):
        session, account, item, state = self.make(
            post_failure=httpx.ReadTimeout("synthetic"))
        try:
            with self.assertRaisesRegex(Exception, "transport_unavailable"):
                self.execute(session, account, item)
            self.assertEqual(state["post_count"], 1)
            with self.assertRaisesRegex(
                    ProviderCapabilityError, "borrow_claim_spent"):
                self.execute(session, account, item)
            self.assertEqual(state["post_count"], 1)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
