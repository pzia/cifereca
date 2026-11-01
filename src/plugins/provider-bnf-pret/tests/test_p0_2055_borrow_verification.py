"""#2055: a POST 200 is not a created loan; require independent positive activity."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from cifereca_bnf_pret_borrow import (
        BorrowActivationProof, verify_borrow_activation,
    )
    from cifereca_bnf_pret_web import (
        ProfileScope, parse_loans, WebParseError, ORIGIN,
    )
finally:
    sys.path.pop(0)

SCOPE = ProfileScope("credential-synthetic", "credential-synthetic", "epoch-synthetic")
ACTIVITY_URL = ORIGIN + "/my_profile/activity"
BEFORE = '<section class="active-loans"></section>'
AFTER = ('<section class="active-loans">'
         '<div class="js-loan"><a href="/resources/book-synthetic">Example</a>'
         '<a href="/loans/loan-synthetic">Open loan</a></div>'
         '</section>')
RECEIPT = ('<main><p>Request received</p>'
           '<a href="/loans/loan-synthetic">View loan</a></main>')


def snapshot(html):
    return parse_loans(
        html, page_url=ACTIVITY_URL, expected=SCOPE, observed=SCOPE,
    )


class ActivationVerificationTests(unittest.TestCase):
    def test_200_receipt_correlated_with_fresh_upstream_loan(self):
        proof = verify_borrow_activation(
            RECEIPT, AFTER, resource_id="book-synthetic",
            before=snapshot(BEFORE), expected_scope=SCOPE,
        )
        self.assertIsInstance(proof, BorrowActivationProof)
        self.assertEqual((proof.loan_id, proof.resource_id),
                         ("loan-synthetic", "book-synthetic"))
        self.assertEqual(repr(proof),
                         "BorrowActivationProof(active_loan_observed=True)")
        self.assertNotIn("loan-synthetic", repr(proof))

    def test_http_success_without_loan_receipt_never_confirms(self):
        for response in (
            "<html>200 OK</html>",
            '<main><p>Quota atteint</p></main>',
            RECEIPT.replace("loan-synthetic", "loan-other") +
            '<a href="/loans/loan-synthetic">Multiple</a>',
        ):
            with self.subTest(response=response[:60]):
                with self.assertRaisesRegex(
                        WebParseError, "^borrow_receipt_unverified$"):
                    verify_borrow_activation(
                        response, AFTER, resource_id="book-synthetic",
                        before=snapshot(BEFORE), expected_scope=SCOPE,
                    )

    def test_stale_empty_activity_is_not_confirmation(self):
        with self.assertRaisesRegex(WebParseError, "^borrow_activity_unverified$"):
            verify_borrow_activation(
                RECEIPT, BEFORE, resource_id="book-synthetic",
                before=snapshot(BEFORE), expected_scope=SCOPE,
            )

    def test_positive_unrelated_loan_does_not_prove_selected_resource(self):
        wrong = AFTER.replace("book-synthetic", "another-book")
        with self.assertRaisesRegex(WebParseError, "^borrow_receipt_unverified$"):
            verify_borrow_activation(
                RECEIPT, wrong, resource_id="book-synthetic",
                before=snapshot(BEFORE), expected_scope=SCOPE,
            )

    def test_changed_loan_id_is_not_confirmation(self):
        wrong = AFTER.replace("loan-synthetic", "loan-other")
        with self.assertRaisesRegex(WebParseError, "^borrow_receipt_unverified$"):
            verify_borrow_activation(
                RECEIPT, wrong, resource_id="book-synthetic",
                before=snapshot(BEFORE), expected_scope=SCOPE,
            )

    def test_known_existing_loan_refuses_even_when_activity_is_positive(self):
        with self.assertRaisesRegex(WebParseError, "^borrow_existing_loan$"):
            verify_borrow_activation(
                RECEIPT, AFTER, resource_id="book-synthetic",
                before=snapshot(AFTER), expected_scope=SCOPE,
            )

    def test_wrong_scope_or_missing_scope_is_denied(self):
        different = ProfileScope("credential-other", "credential-synthetic", "epoch-synthetic")
        for scope in (different, None, "synthetic"):
            with self.subTest(scope=type(scope).__name__):
                with self.assertRaisesRegex(WebParseError, "^borrow_scope_mismatch$"):
                    verify_borrow_activation(
                        RECEIPT, AFTER, resource_id="book-synthetic",
                        before=snapshot(BEFORE), expected_scope=scope,
                    )

    def test_partial_or_ambiguous_activity_never_confirms(self):
        cases = (
            AFTER.replace('</section>',
                         '<div class="js-loan"><a href="/resources/other">Other</a>'
                         '<a href="/loans/loan-synthetic">Same ID</a></div></section>'),
            AFTER.replace('</section>',
                         '<div class="js-loan"><a href="/resources/book-synthetic">Other</a>'
                         '<a href="/loans/loan-second">Different ID</a></div></section>'),
            AFTER.replace('</section>', '<div class="js-loan">truncated</div></section>'),
        )
        for activity in cases:
            with self.subTest(activity=activity[:70]):
                with self.assertRaises(WebParseError):
                    verify_borrow_activation(
                        RECEIPT, activity, resource_id="book-synthetic",
                        before=snapshot(BEFORE), expected_scope=SCOPE,
                    )


if __name__ == "__main__":
    unittest.main()
