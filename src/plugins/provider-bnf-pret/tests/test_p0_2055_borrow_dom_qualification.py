"""#2055: confidential BnF read-only DOM signal locator.

Everything is synthetic. Neither the module nor CLI authenticates, borrows,
downloads tokens, sends an HTTP request or saves private HTML.
"""
from pathlib import Path
import json
import subprocess
import sys
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parents[2]
sys.path.insert(0, str(PACKAGE))

from cifereca_bnf_pret_qualification import (
    SCHEMA, qualify_bnf_borrow_dom,
)

PROFILE = (
    '<html><body><main>'
    '<span>Pass BnF lecture/culture valide</span>'
    '<p>Prêts restants : 2 — lecteur privé@example.org</p>'
    '<form><input type="hidden" name="authenticity_token" '
    'value="PRIVATE_CSRF_CANARY_XYZ"></form>'
    '</main></body></html>'
)
DETAIL = (
    '<html><main>'
    '<article itemscope itemtype="https://schema.org/Book">'
    '<h2>Faux titre privé : Alice Identifiant 456</h2>'
    '<p>Plusieurs exemplaires disponibles</p>'
    '<a class="button-borrow" href="/loans/new?token=SECRET_XYZ">'
    'Emprunter</a></article></main></html>'
)
CONFIRM = (
    '<html><body><form action="/loans/activate?secret=PRIVATE" method="POST">'
    '<button>Confirmer l\'emprunt</button>'
    '<input type="hidden" name="resource_id" value="USER_BOOK_ID">'
    '<input type="hidden" name="authenticity_token" '
    'value="PRIVATE_CSRF_CANARY_XYZ"></form></body></html>'
)


class QualificationProbeTests(unittest.TestCase):
    def assert_no_authority(self, result):
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["eligibility"], "unverified")
        for key in (
            "identity_bound", "quota_verified", "offer_verified",
            "borrow_permission_verified", "authorized",
        ):
            self.assertIs(result[key], False)

    def assert_private_not_exposed(self, result):
        payload = json.dumps(result, ensure_ascii=False)
        for secret in (
            "PRIVATE", "Alice", "privé@", "456", "SECRET", "USER_BOOK_ID",
            "token", "csrf", "authenticity_token", "loans/new", "example.org",
        ):
            # Only normalized fixed keys may be emitted (csrf_field_names).
            if secret in ("csrf", "token"):
                self.assertNotIn("PRIVATE_CSRF", payload)
                self.assertNotIn("SECRET_XYZ", payload)
            elif secret == "authenticity_token":
                self.assertNotIn(secret, payload)
            else:
                self.assertNotIn(secret, payload)
        for field in (
            "quota_label", "pass_label", "copies_label",
            "confirmation_label",
        ):
            for path in result[field]["tag_paths"]:
                self.assertLessEqual(len(path), 80)
                self.assertRegex(path, r"^[a-z/]+$")

    def test_profile_labels_are_structural_candidates_not_entitlement(self):
        result = qualify_bnf_borrow_dom(PROFILE, page="profile")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["quota_label"]["count"], 1)
        self.assertEqual(result["pass_label"]["count"], 1)
        self.assertEqual(result["quota_label"]["tag_paths"], ["html/body/main/p"])
        self.assert_no_authority(result)
        self.assert_private_not_exposed(result)

    def test_observed_zero_quota_wording_is_only_a_structural_hint(self):
        # Observed BnF wording from #4480, entirely synthetic DOM.
        # 0 available is NOT 0 active loans, permission, or exhaustion proof.
        for phrase in ("Vous avez encore droit à 0 prêt",
                       "Vous avez encore droit à 4 prêts"):
            with self.subTest(phrase=phrase):
                html = "<html><body><p>" + phrase + "</p></body></html>"
                result = qualify_bnf_borrow_dom(html, page="profile")
                self.assertEqual(result["quota_label"]["count"], 1)
                self.assertEqual(result["quota_label"]["tag_paths"], ["html/body/p"])
                self.assert_no_authority(result)
                self.assertNotIn(phrase, json.dumps(result, ensure_ascii=False))

    def test_inline_formatted_quota_and_pass_are_single_non_authoritative_labels(self):
        html = (
            '<html><body><main>'
            '<p>Vous avez encore droit à <strong>0</strong> prêt</p>'
            '<p>Pass BnF <em>lecture/culture</em> valide</p>'
            '</main></body></html>'
        )
        result = qualify_bnf_borrow_dom(html, page="profile")
        self.assertEqual(result["quota_label"],
                         {"count": 1, "tag_paths": ["html/body/main/p"]})
        self.assertEqual(result["pass_label"],
                         {"count": 1, "tag_paths": ["html/body/main/p"]})
        self.assert_no_authority(result)
        self.assert_private_not_exposed(result)
        self.assertNotIn("0 prêt", json.dumps(result, ensure_ascii=False))

    def test_inline_formatting_never_joins_distinct_block_labels_or_hidden_content(self):
        html = (
            '<html><body><main>'
            '<p>Vous avez encore droit à</p><p>0 prêt</p>'
            '<p>Pass BnF</p><p>lecture/culture valide</p>'
            '<p>Vous avez encore droit à <span aria-hidden="true">0</span> prêt</p>'
            '<script>Vous avez encore droit à 12 prêts</script>'
            '<p hidden>Pass BnF lecture/culture valide</p>'
            '</main></body></html>'
        )
        result = qualify_bnf_borrow_dom(html, page="profile")
        self.assertEqual(result["quota_label"]["count"], 0)
        self.assertEqual(result["pass_label"]["count"], 0)
        self.assert_no_authority(result)

    def test_block_boundary_between_direct_text_chunks_is_not_a_marker(self):
        html = (
            '<html><body><main>'
            '<div>Vous avez encore droit à <p>Unrelated</p>0 prêt</div>'
            '<div>Pass BnF <section>Unrelated</section>lecture/culture valide</div>'
            '<p>Vous avez encore droit à <span hidden>Other</span>0 prêt</p>'
            '</main></body></html>'
        )
        result = qualify_bnf_borrow_dom(html, page="profile")
        self.assertEqual(result["quota_label"]["count"], 0)
        self.assertEqual(result["pass_label"]["count"], 0)
        self.assert_no_authority(result)

    def test_css_hidden_profile_labels_and_inline_chunks_are_not_candidates(self):
        for attrs in ('style="DISPLAY: none !important"',
                      'style="visibility: hidden"', 'aria-hidden=" TRUE "'):
            with self.subTest(attrs=attrs):
                html = (
                    f'<section {attrs}><p>Prêts restants : 4</p>'
                    '<p>Pass BnF lecture/culture valide</p></section>'
                    f'<p>Vous avez encore droit à <span {attrs}>0</span> prêt</p>'
                )
                report = qualify_bnf_borrow_dom(html, page="profile")
                self.assertEqual(report["quota_label"]["count"], 0)
                self.assertEqual(report["pass_label"]["count"], 0)
                self.assert_no_authority(report)

    def test_css_hidden_book_and_offer_markers_are_not_candidates(self):
        for style in ("display: none", "visibility:hidden"):
            with self.subTest(style=style):
                report = qualify_bnf_borrow_dom(
                    f'<div style="{style}">' + DETAIL + '</div>', page="detail")
                self.assertEqual(report["status"], "book_scope_unrecognized")
                self.assert_no_authority(report)
                detail = DETAIL.replace(
                    '<p>Plusieurs exemplaires disponibles</p>',
                    f'<div style="{style}"><p>Plusieurs exemplaires disponibles</p>'
                    '<span class="lb_unavailable">Indisponible</span></div>',
                )
                report = qualify_bnf_borrow_dom(detail, page="detail")
                self.assertEqual(report["copies_label"]["count"], 0)
                self.assertEqual(report["unavailable_markers"], 0)
                self.assertEqual(report["borrow_controls"], 1)
                self.assert_no_authority(report)
                self.assert_private_not_exposed(report)

    def test_nested_inline_confirmation_counted_once(self):
        html = CONFIRM.replace("Confirmer l'emprunt",
                               "<span>Confirmer l'<em>emprunt</em></span>")
        result = qualify_bnf_borrow_dom(html, page="confirmation")
        self.assertEqual(result["confirmation_label"]["count"], 1)
        self.assert_no_authority(result)
        self.assert_private_not_exposed(result)

    def test_inline_formatted_copies_do_not_grant_an_edition_offer(self):
        detail = DETAIL.replace(
            '<p>Plusieurs exemplaires disponibles</p>',
            '<p>Plusieurs <span>exemplaires</span> disponibles</p>',
        )
        result = qualify_bnf_borrow_dom(detail, page="detail")
        self.assertEqual(result["copies_label"]["count"], 1)
        self.assertEqual(result["copies_label"]["tag_paths"], ["article/p"])
        self.assertEqual(result["offer_verified"], False)
        self.assert_no_authority(result)
        self.assert_private_not_exposed(result)

    def test_inline_formatted_confirmation_stays_unverified(self):
        html = CONFIRM.replace(
            "Confirmer l'emprunt",
            "Confirmer l'<em>emprunt</em>",
        )
        result = qualify_bnf_borrow_dom(html, page="confirmation")
        self.assertEqual(result["confirmation_label"]["count"], 1)
        self.assert_no_authority(result)
        self.assert_private_not_exposed(result)

    def test_unrelated_droit_or_false_quota_does_not_qualify(self):
        for phrase in (
            "Vous avez encore droit à un prêt", "Droit à 0 prêt",
            "Vous avez encore droit à 0 téléchargements",
        ):
            with self.subTest(phrase=phrase):
                result = qualify_bnf_borrow_dom(
                    "<html><p>" + phrase + "</p></html>", page="profile",
                )
                self.assertEqual(result["quota_label"]["count"], 0)
                self.assert_no_authority(result)

    def test_book_scope_available_text_is_not_published_as_offer(self):
        result = qualify_bnf_borrow_dom(DETAIL, page="detail")
        self.assertEqual(result["book_scopes"], 1)
        self.assertEqual(result["copies_label"]["count"], 1)
        self.assertEqual(result["borrow_controls"], 1)
        self.assertEqual(result["unavailable_markers"], 0)
        self.assert_no_authority(result)
        self.assert_private_not_exposed(result)

    def test_negative_book_marker_is_structural_not_live_permission(self):
        source = DETAIL.replace(
            '</article>', '<span class="lb_unavailable">Indisponible</span></article>',
        )
        result = qualify_bnf_borrow_dom(source, page="detail")
        self.assertEqual(result["unavailable_markers"], 1)
        self.assert_no_authority(result)

    def test_outside_book_contamination_never_makes_offer(self):
        source = DETAIL.replace(
            "<main>", '<main><div>Plusieurs exemplaires disponibles'
            '<span class="lb_unavailable">Sans rapport</span></div>',
        ).replace("<p>Plusieurs exemplaires disponibles</p>", "<p>aucun</p>")
        result = qualify_bnf_borrow_dom(source, page="detail")
        self.assertEqual(result["copies_label"]["count"], 0)
        self.assertEqual(result["unavailable_markers"], 0)
        self.assert_no_authority(result)

    def test_two_books_or_malformed_book_is_fail_closed(self):
        for source in (
            DETAIL.replace("</main>", '<article itemscope '
                'itemtype="https://schema.org/Book"></article></main>'),
            DETAIL.replace("</article>", ""),
            '<section>Plusieurs exemplaires disponibles</section>',
        ):
            result = qualify_bnf_borrow_dom(source, page="detail")
            self.assertEqual(result["status"], "book_scope_unrecognized")
            self.assertEqual(result["copies_label"]["count"], 0)
            self.assert_no_authority(result)

    def test_confirmation_reports_form_shape_without_csrf_value(self):
        result = qualify_bnf_borrow_dom(CONFIRM, page="confirmation")
        self.assertEqual(result["post_forms"], 1)
        self.assertEqual(result["csrf_field_names"], 1)
        self.assertEqual(result["resource_field_names"], 1)
        self.assertEqual(result["confirmation_label"]["count"], 1)
        self.assert_no_authority(result)
        self.assert_private_not_exposed(result)

    def test_profile_login_form_is_not_a_qualified_session(self):
        html = PROFILE.replace(
            "</main>", '<input type="password" name="password" '
            'value="PRIVATE_PASSWORD"></main>',
        )
        result = qualify_bnf_borrow_dom(html, page="profile")
        self.assertEqual(result["status"], "unrecognized")
        self.assertEqual(result["quota_label"]["count"], 0)
        self.assert_no_authority(result)
        self.assert_private_not_exposed(result)

    def test_count_saturates_to_two_and_tags_cannot_embed_pii(self):
        html = (
            '<html><body><my-profile-PRIVATE_ID>'
            + "<span>Prêts restants: 3</span>" * 12
            + "</my-profile-PRIVATE_ID></body></html>"
        )
        result = qualify_bnf_borrow_dom(html, page="profile")
        self.assertEqual(result["quota_label"]["count"], 2)
        self.assertEqual(len(result["quota_label"]["tag_paths"]), 2)
        self.assertIn("other", result["quota_label"]["tag_paths"][0])
        self.assert_private_not_exposed(result)
        self.assert_no_authority(result)

    def test_invalid_input_unknown_page_and_html_limit_are_closed(self):
        for html, page, status in (
            (123, "profile", "invalid_input"),
            (PROFILE, "secret-PAGE", "unsupported_page"),
            ("X" * (512 * 1024 + 1), "profile", "limit"),
            ('<input name="SAMLResponse" value="secret">', "profile", "unrecognized"),
        ):
            result = qualify_bnf_borrow_dom(html, page=page)
            self.assertEqual(result["status"], status)
            self.assert_no_authority(result)
            self.assert_private_not_exposed(result)

    def test_activity_shape_never_attests_authentication_or_absence(self):
        for html, wanted, status in (
            ('<section class="active-loans"></section>', "empty_candidate", "ok"),
            ('<section class="active-loans" aria-busy="true"></section>', "partial", "ok"),
            ('<div style="display:none"><section class="active-loans"></section></div>',
             "partial", "ok"),
            ('<p>Vous avez encore droit à 0 prêt</p>', "unknown", "unrecognized"),
            ('<input type="password" value="PRIVATE_PASSWORD">', "unknown", "unrecognized"),
        ):
            with self.subTest(wanted=wanted, status=status):
                report = qualify_bnf_borrow_dom(html, page="activity")
                self.assertEqual(report["activity_shape"], wanted)
                self.assertEqual(report["status"], status)
                self.assertIs(report["activity_verified"], False)
                self.assert_no_authority(report)
                self.assert_private_not_exposed(report)

    def test_activity_quota_zero_with_loan_is_not_empty_and_never_exports_ids(self):
        html = ('<p>Vous avez encore droit à 0 prêt</p>'
                '<section class="active-loans"><div class="js-loan">'
                '<a href="/resources/USER_BOOK_ID">Alice PRIVATE title</a>'
                '<a href="/loans/PRIVATE_LOAN_ID?token=SECRET_XYZ">Prêt</a>'
                '</div></section>')
        report = qualify_bnf_borrow_dom(html, page="activity")
        self.assertEqual(report["activity_shape"], "nonempty_candidate")
        self.assertIs(report["activity_verified"], False)
        self.assert_no_authority(report)
        self.assert_private_not_exposed(report)

    def test_stdin_cli_never_echoes_private_document_or_name(self):
        cmd = [
            sys.executable, str(REPO / "scripts/bnf_borrow_dom_probe.py"),
            "--page", "profile",
        ]
        process = subprocess.run(
            cmd, input=PROFILE.encode("utf-8"), capture_output=True,
            timeout=20, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8")[:200])
        self.assertEqual(process.stderr, b"")
        value = json.loads(process.stdout)
        self.assertEqual(value["quota_label"]["count"], 1)
        self.assert_no_authority(value)
        self.assert_private_not_exposed(value)


if __name__ == "__main__":
    unittest.main()
