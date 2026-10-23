"""Deterministic, offline BnF loan observation contracts (#2055)."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from cifereca_bnf_pret_web import LoanSnapshot, LoanRecord, ProfileScope, WebParseError
    from cifereca_bnf_pret_observations import ObservedLoans
finally:
    sys.path.pop(0)


class BnfLoanObservationsTests(unittest.TestCase):
    def setUp(self):
        self.scope = ProfileScope("identity-a", "account-a", "epoch-1")
        self.index = ObservedLoans(self.scope)

    def snapshot(self, *pairs, status="observed", scope=None, exhaustive=False):
        return LoanSnapshot(
            scope or self.scope,
            tuple(LoanRecord(loan, resource) for loan, resource in pairs),
            status, exhaustive,
        )

    def test_counts_never_claim_current_entitlement(self):
        first = self.index.observe(self.snapshot(("loan-1", "book-1")))
        self.assertEqual((first.on_last_page, first.newly_seen, first.seen_in_session),
                         (1, 1, 1))
        self.assertEqual(first.status, "partial")
        second = self.index.observe(self.snapshot(status="partial"))
        self.assertEqual((second.on_last_page, second.newly_seen, second.seen_in_session),
                         (0, 0, 1))
        third = self.index.observe(self.snapshot(("loan-2", "book-2")))
        self.assertEqual((third.on_last_page, third.newly_seen, third.seen_in_session),
                         (1, 1, 2))

    def test_repeated_observation_deduplicates_without_deleting(self):
        one = self.snapshot(("loan-1", "book-1"))
        self.index.observe(one)
        result = self.index.observe(one)
        self.assertEqual((result.newly_seen, result.seen_in_session), (0, 1))

    def test_rejects_other_account_or_epoch(self):
        self.index.observe(self.snapshot(("loan-1", "book-1")))
        for scope in (ProfileScope("other", "account-a", "epoch-1"),
                      ProfileScope("identity-a", "other", "epoch-1"),
                      ProfileScope("identity-a", "account-a", "epoch-2")):
            with self.assertRaisesRegex(WebParseError, "^scope_mismatch$"):
                self.index.observe(self.snapshot(("loan-2", "book-2"), scope=scope))
        result = self.index.observe(self.snapshot(status="partial"))
        self.assertEqual(result.seen_in_session, 1)

    def test_conflicting_resource_does_not_pollute_previous_observation(self):
        self.index.observe(self.snapshot(("loan-1", "book-1")))
        with self.assertRaisesRegex(WebParseError, "^ambiguous_loans$"):
            self.index.observe(self.snapshot(("loan-1", "book-2")))
        later = self.index.observe(self.snapshot(("loan-1", "book-1")))
        self.assertEqual((later.newly_seen, later.seen_in_session), (0, 1))

    def test_does_not_trust_claimed_exhaustive_or_invalid_status(self):
        for bad in (self.snapshot(exhaustive=True),
                    self.snapshot(status="server_complete"),
                    self.snapshot(status="ok")):
            with self.assertRaisesRegex(WebParseError, "^invalid_observation$"):
                self.index.observe(bad)
        self.assertEqual(self.index.observe(self.snapshot(status="partial")).seen_in_session, 0)

    def test_bounded_unique_history_and_invalid_identifiers(self):
        full = self.snapshot(*((f"loan-{n}", f"book-{n}") for n in range(100)))
        self.assertEqual(self.index.observe(full).seen_in_session, 100)
        with self.assertRaisesRegex(WebParseError, "^item_limit$"):
            self.index.observe(self.snapshot(("loan-101", "book-101")))
        for raw in (("bad/id", "book-1"), ("loan-x", "bad/resource"), ("", "book-x")):
            with self.assertRaisesRegex(WebParseError, "^invalid_observation$"):
                self.index.observe(self.snapshot(raw))
        self.assertEqual(self.index.observe(self.snapshot(status="partial")).seen_in_session, 100)

    def test_stale_errors_are_not_accepted_as_page_snapshots(self):
        self.index.observe(self.snapshot(("loan-1", "book-1")))
        with self.assertRaisesRegex(WebParseError, "^scope_mismatch$"):
            self.index.observe(None)
        result = self.index.observe(self.snapshot(status="partial"))
        self.assertEqual(result.seen_in_session, 1)

    def test_close_revokes_all_without_reuse_or_identifiers_in_repr(self):
        result = self.index.observe(self.snapshot(("loan-1", "book-1")))
        self.index.close()
        self.index.close()
        self.assertNotIn("identity-a", repr(self.index))
        self.assertNotIn("loan-1", repr(result))
        self.assertNotIn("book-1", repr(result))
        with self.assertRaisesRegex(WebParseError, "^session_closed$"):
            self.index.observe(self.snapshot(("loan-2", "book-2")))


if __name__ == "__main__":
    unittest.main()
