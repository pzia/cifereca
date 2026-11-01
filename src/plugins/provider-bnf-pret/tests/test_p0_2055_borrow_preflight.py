"""BnF P0 #2055: synthetic, zero-network Web borrowing preflight.

No private trace, cookie, signed URL, token, real user or library data here.
The capability and any mutation must remain disabled pending the Core action
admission contract and a user-confirmed post-success live recipe.
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from cifereca_bnf_pret_borrow import (
        BorrowForm, _transition_url, confirmation_link, activation_form,
    )
    from cifereca_bnf_pret_web import WebParseError, ORIGIN
finally:
    sys.path.pop(0)


RESOURCE = "syntheticR1"
DETAIL_URL = ORIGIN + "/resources/" + RESOURCE
CONFIRM_URL = ORIGIN + "/resources/" + RESOURCE + "/loans/new"
POST_URL = ORIGIN + "/loans/activation"
DETAIL = ('<article itemscope itemtype="https://schema.org/Book">'
          '<meta itemprop="name" content="Synthetic example">'
          '<a class="button-borrow" href="/resources/syntheticR1/loans/new">Emprunter</a>'
          '</article>')
CONFIRM = ('<html><a href="/resources/syntheticR1">Livre concerné</a>'
           '<form action="/loans/activation" method="post">'
           '<input type="hidden" name="authenticity_token" value="SECRET_DO_NOT_LOG">'
           '<input type="hidden" name="resource_id" value="syntheticR1">'
           '<input type="submit" value="Confirmer"></form></html>')


class BnfBorrowPreflightTests(unittest.TestCase):
    def test_borrow_link_and_csrf_bound_to_selected_resource(self):
        url = confirmation_link(DETAIL, page_url=DETAIL_URL, resource_id=RESOURCE)
        self.assertEqual(url, CONFIRM_URL)
        form = activation_form(CONFIRM, page_url=url, resource_id=RESOURCE)
        self.assertIsInstance(form, BorrowForm)
        self.assertEqual(form.action_url, POST_URL)
        self.assertEqual(dict(form.fields)["resource_id"], RESOURCE)
        self.assertEqual(dict(form.fields)["authenticity_token"], "SECRET_DO_NOT_LOG")
        self.assertEqual(repr(form), "BorrowForm(authorization=unverified)")
        self.assertNotIn("SECRET", repr(form))

    def test_catalogue_button_alone_is_never_a_web_action(self):
        for html in (
            DETAIL.replace("<a class=", "<button class=").replace("</a>", "</button>"),
            DETAIL.replace('href="/resources/syntheticR1/loans/new"', ""),
            DETAIL.replace("button-borrow", "button-booking"),
            DETAIL.replace("<a class=", '<a disabled class='),
            DETAIL.replace("<a class=", '<a aria-disabled="true" class='),
            DETAIL.replace("</article>", '<a class="button-borrow" href="/loans/new"></a></article>'),
            DETAIL.replace("</article>", '<span class="lb_unavailable">Indisponible</span></article>'),
        ):
            with self.subTest(markup=html[:100]):
                with self.assertRaises(WebParseError):
                    confirmation_link(html, page_url=DETAIL_URL, resource_id=RESOURCE)

    def test_link_scope_does_not_follow_a_different_book(self):
        for rid, url in (("different", DETAIL_URL),
                         (RESOURCE, ORIGIN + "/resources/different"),
                         ("", DETAIL_URL)):
            with self.subTest(resource=rid):
                with self.assertRaises(WebParseError):
                    confirmation_link(DETAIL, page_url=url, resource_id=rid)

    def test_observed_urls_are_not_permission_for_foreign_or_unrelated_routes(self):
        bad = (
            "//evil.example/loans/new",
            "http://pret.bnf.fr/loans/new",
            "https://pret.bnf.fr.evil.example/loans/new",
            "https://user@pret.bnf.fr/loans/new",
            "https://pret.bnf.fr:8443/loans/new",
            "/logout/loans/new",
            "/v1/loans",
            "/sso/loans/new",
            "/my_profile/loans/new",
            "/resources/different/loans/new",
            "/resources/syntheticR1",
            "/loans/../logout",
            "/loans/%2e%2e/logout",
            "/loans/new#token",
            "/loans/loan-synthetic",
            "/loans/loan-synthetic?device_type=reader&drm=acs4&token=synthetic",
            "/loans/loan-synthetic/return",
            "/loans/loan-synthetic/renew",
            "/loans/token",
            "/resources/syntheticR1/loans/return",
            "/loans/new?resource_id=different",
            "/loans/new?resource_id=syntheticR1&resource_id=different",
            "/loans/new?book_id=different",
            "/loans/new?resource%5Fid=different",
            "/loans/new?resource_id=%GG",
            "/loans/new\\evil",
            "",
        )
        for url in bad:
            with self.subTest(url=url):
                with self.assertRaisesRegex(WebParseError, "^borrow_url_refused$"):
                    _transition_url(DETAIL_URL, url, RESOURCE)
        self.assertEqual(
            _transition_url(DETAIL_URL, "/loans/new?resource_id=syntheticR1", RESOURCE),
            ORIGIN + "/loans/new?resource_id=syntheticR1",
        )

    def test_confirmation_rejects_csrf_absence_ambiguity_and_other_actions(self):
        bad = (
            CONFIRM.replace('name="authenticity_token"', 'name="something"'),
            CONFIRM.replace('value="SECRET_DO_NOT_LOG"', 'value=""'),
            CONFIRM.replace('method="post"', 'method="get"'),
            CONFIRM.replace('method="post"', 'method="POST"').replace(
                "</html>", '<form action="/loans/new" method="POST">'
                          '<input type="hidden" name="authenticity_token" value="X">'
                          '</form></html>'),
            CONFIRM.replace('value="syntheticR1"', 'value="different"'),
            CONFIRM.replace('href="/resources/syntheticR1"', 'href="/resources/different"'),
            CONFIRM.replace('</form>', '<input type="text" name="amount" value="1"></form>'),
            CONFIRM.replace('</form>', '<input type="hidden" name="_method" value="delete"></form>'),
            CONFIRM.replace('</form>', '<input type="hidden" name="authenticity_token" value="X"></form>'),
            CONFIRM.replace('/loans/activation', 'https://evil.example/loans/activation'),
        )
        for html in bad:
            with self.subTest(markup=html[:115]):
                with self.assertRaises(WebParseError):
                    activation_form(html, page_url=CONFIRM_URL, resource_id=RESOURCE)

    def test_never_promotes_saml_or_password_forms(self):
        for name in ("SAMLResponse", "password"):
            markup = CONFIRM.replace("resource_id", name)
            with self.assertRaises(WebParseError):
                activation_form(markup, page_url=CONFIRM_URL, resource_id=RESOURCE)

    def test_no_transport_or_implicit_mutation_in_parser(self):
        # All inputs are strings. No client, URL fetcher or credential accepted.
        link = confirmation_link(DETAIL, page_url=DETAIL_URL, resource_id=RESOURCE)
        form = activation_form(CONFIRM, page_url=link, resource_id=RESOURCE)
        self.assertEqual(len(form.fields), 2)


if __name__ == "__main__":
    unittest.main()
