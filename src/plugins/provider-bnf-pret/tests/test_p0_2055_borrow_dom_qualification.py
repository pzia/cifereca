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
