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

    def test_reader_actions_wrap_on_narrow_gtk_windows(self):
        # The 8 controls used to be a single overflowing Gtk.Box; their
        # availability, including the explicit Close action, is a P0 tester
        # requirement on laptops. Avoid GTK imports in the offline contract.
        source = (ROOT / "cifereca_bnf_pret_page.py").read_text(encoding="utf-8")
        self.assertIn("row = Gtk.FlowBox()", source)
        self.assertIn("row.set_min_children_per_line(1)", source)
        self.assertIn("row.set_max_children_per_line(3)", source)
        self.assertIn("row.set_row_spacing(6)", source)
        self.assertIn("row.set_selection_mode(Gtk.SelectionMode.NONE)", source)
        self.assertNotIn(
            'row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)', source
        )
        self.assertIn('stop_button = Gtk.Button(label="Fermer la session")', source)
        self.assertIn('row.append(button)', source)

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

    def test_local_detail_text_is_bounded_and_not_interpreted_as_markup(self):
        self.assertEqual(page.readable("  Book  <safe>   ", 24), "Book <safe>")
        self.assertEqual(page.readable("a" * 300), "a" * 180)
        self.assertEqual(page.readable({"secret": "x"}), "")

    def test_suggestions_are_bounded_local_text_not_html_markup(self):
        from cifereca_bnf_pret_web import CatalogPage, CatalogRecord
        records = tuple(CatalogRecord(f"id-{n}", "  Fiction  <Titre>  ",
                          authors=("Auteur  fictif",)) for n in range(18))
        lines = page.suggestion_lines(CatalogPage(records, "partial"))
        self.assertEqual(len(lines), 12)
        self.assertEqual(lines[0], "1. Fiction <Titre> — Auteur fictif [BnF id-0]")
        self.assertIn("id-11", lines[-1])
        self.assertFalse(any("id-12" in text for text in lines))
        self.assertEqual(page.suggestion_lines(CatalogPage((), "partial")), ())
        self.assertEqual(page.suggestion_lines(CatalogPage(records, "partial"), limit=1000), ())
        self.assertNotIn("https://", "\n".join(lines))

    def test_suggestions_display_explicit_number_for_selected_resource(self):
        from cifereca_bnf_pret_web import CatalogPage, CatalogRecord
        lines = page.suggestion_lines(CatalogPage((
            CatalogRecord("ref1", "Alpha"), CatalogRecord("ref2", "Bêta"),
        ), "partial"))
        self.assertEqual(lines, (
            "1. Alpha [BnF ref1]", "2. Bêta [BnF ref2]",
        ))

    def test_stop_revokes_visible_catalog_selection(self):
        from cifereca_bnf_pret_selection import VisibleResourceSelection
        from cifereca_bnf_pret_web import ProfileScope, CatalogPage, CatalogRecord
        scope = ProfileScope("identity-private", "local-account", "epoch-private")
        selection = VisibleResourceSelection(scope)
        selection.observe(CatalogPage((CatalogRecord("resource-only", "Fiction"),),
                                      "partial"), expected=scope)
        instance = page.BnfReaderPage()
        instance.selection = selection
        instance._stop()
        self.assertIsNone(instance.selection)
        self.assertIn("closed", repr(selection))

    def test_stop_revokes_visible_loan_book_choices(self):
        from cifereca_bnf_pret_loan_selection import VisibleLoanBookSelection
        from cifereca_bnf_pret_web import ProfileScope, LoanSnapshot, LoanRecord
        scope = ProfileScope("private-identity", "private-account", "epoch")
        loan_choices = VisibleLoanBookSelection(scope)
        loan_choices.observe(LoanSnapshot(scope, (LoanRecord("private-loan", "book-one"),),
                                         "observed"), expected=scope)
        instance = page.BnfReaderPage()
        instance.loan_selection = loan_choices
        instance._stop()
        self.assertIsNone(instance.loan_selection)
        self.assertIn("closed", repr(loan_choices))
        self.assertNotIn("private-loan", repr(loan_choices))

    def test_unknown_projection_and_untrusted_ids_fail_closed(self):
        from cifereca_bnf_pret_web import CatalogPage, CatalogRecord
        self.assertEqual(page.suggestion_lines(object()), ())
        self.assertEqual(page.suggestion_lines(
            CatalogPage((CatalogRecord("abc", "X"),), "ok", scope="detail")), ())
        self.assertEqual(page.suggestion_lines(
            CatalogPage((CatalogRecord("bad/path", "X"),), "partial")), ())
        self.assertEqual(page.suggestion_lines(
            CatalogPage((CatalogRecord("abc", "X"),)*101, "partial")), ())

    def test_ui_loan_summary_is_bounded_and_non_authorizing(self):
        from cifereca_bnf_pret_observations import ObservedLoanCounts
        result = page.loan_observation_status(ObservedLoanCounts(0, 0, 4))
        self.assertIn("0 prêt(s) visibles sur cette page", result)
        self.assertIn("4 référence(s) vues", result)
        self.assertIn("aucun droit actif attesté", result)
        self.assertEqual(page.loan_observation_status(object()), page._DEFAULT_ERROR)

    def test_ui_stop_retracts_observation_state(self):
        instance = page.BnfReaderPage()
        worker, handle = FakeWorker(), FakeHandle()
        from cifereca_bnf_pret_web import ProfileScope, LoanSnapshot, LoanRecord
        from cifereca_bnf_pret_observations import ObservedLoans
        obs = ObservedLoans(ProfileScope("profile", "reader", "epoch"))
        obs.observe(LoanSnapshot(ProfileScope("profile", "reader", "epoch"),
                                 (LoanRecord("loan1", "resource1"),), "observed"))
        instance.worker, instance.handle, instance.observations = worker, handle, obs
        instance._stop()
        self.assertTrue(worker.stopped)
        self.assertTrue(handle.cancelled)
        self.assertIsNone(instance.observations)
        self.assertIn("closed", repr(obs))

    def test_explicit_search_text_validation(self):
        self.assertTrue(page.valid_search_text("Fiction française"))
        self.assertTrue(page.valid_search_text("  Auteur  "))
        for query in (None, 1, "", "x", " ", "x" * 129,
                      "contact@example.com\nprivate", "unsafe\tquery"):
            self.assertFalse(page.valid_search_text(query))

    def test_probe_message_shows_only_bounded_counts(self):
        from cifereca_bnf_pret_dom_probe import DomProbe
        from cifereca_bnf_pret_web import ProfileScope
        scope = ProfileScope("private-identity", "private-account", "epoch")
        result = DomProbe(scope, "activity", 1, 2, 0, 0, 0, 2, 2)
        message = page.dom_probe_message(result)
        self.assertIn("blocs prêts : 2", message)
        self.assertIn("Aucun compte, droit ou inventaire attesté", message)
        self.assertNotIn("private-identity", message)
        self.assertNotIn("private-account", message)
        self.assertEqual(page.dom_probe_message(object()), page._DEFAULT_ERROR)

    def test_manual_bnf_account_attestation_cannot_default_to_first_profile(self):
        selected = ("cifereca-profile-a", "Profil actif")
        self.assertFalse(page.account_confirmation_ready(selected, "cifereca-profile-a", False))
        self.assertFalse(page.account_confirmation_ready(selected, "cifereca-profile-a", 1))
        self.assertFalse(page.account_confirmation_ready(selected, "cifereca-profile-b", True))
        self.assertFalse(page.account_confirmation_ready(None, "cifereca-profile-a", True))
        self.assertFalse(page.account_confirmation_ready([*selected], "cifereca-profile-a", True))
        self.assertFalse(page.account_confirmation_ready(("cifereca-profile-a",), "cifereca-profile-a", True))
        self.assertTrue(page.account_confirmation_ready(selected, "cifereca-profile-a", True))

    def test_selected_core_identity_is_unambiguous_before_attestation(self):
        a = {"identity_id": "a", "label": "A", "selected": True}
        b = {"identity_id": "b", "label": "B", "selected": True}
        self.assertIsNone(page.selected_identity([a, b]))
        self.assertFalse(page.account_confirmation_ready(
            page.selected_identity([a, b]), "a", True))
        self.assertTrue(page.account_confirmation_ready(
            page.selected_identity([a]), "a", True))

    def test_book_control_counters_never_authorize_loan(self):
        from cifereca_bnf_pret_book_actions import BookActionSignals
        from cifereca_bnf_pret_web import ProfileScope
        result = BookActionSignals(ProfileScope('private', 'private', 'epoch'), 1, 2, 1)
        summary = page.book_action_summary(result)
        self.assertIn('1 contrôle(s) Emprunter', summary)
        self.assertIn('aucune disponibilité ni autorisation', summary)
        self.assertNotIn('private', summary)
        self.assertEqual(page.book_action_summary(object()), page._DEFAULT_ERROR)
        self.assertEqual(page.book_action_summary(
            BookActionSignals(ProfileScope('i', 'a', 'e'), 102, 0, 0)),
            page._DEFAULT_ERROR)

    def test_next_page_error_is_safe_and_does_not_claim_completeness(self):
        message = page.safe_error(page.BrowserSessionError("next_page_unavailable"))
        self.assertIn("Aucun lien", message)
        self.assertNotIn("exhaustif", message)
        self.assertNotIn("cookie", message)

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
