"""P1 #4978: source-level BnF cover diagnosis, without an authenticated session.

Every image is scoped to an exact resource anchor. All fixtures are synthetic;
the diagnostic has fixed statuses and cannot assert image availability,
copyright rights, a GET result or an account/loan crosswalk.
"""
from __future__ import annotations

import pytest

from cifereca_bnf_pret_web import (
    _cover_candidates_diagnosis, _dom, _loan_cover,
    _loan_cover_diagnosis, _public_cover_url,
)


FEEDBOOKS = "https://covers.feedbooks.net/item/test_alpha.jpg?size=large&t=12345"
BNF = "https://pret.bnf.fr/covers/test_alpha.jpg"
LEGACY = "https://imagedelivery.net/abcdefgh/12345678/public"


def card(*images, resource="edition-alpha", other=()):
    def img(url):
        return '<img src="' + url.replace("&", "&amp;") + '">'
    html = (
        '<section><div class="js-loan">'
        f'<a href="/resources/{resource}">' +
        "".join(img(src) for src in images) +
        'Titre synthétique</a>'
        '<a href="/resources/edition-other">' +
        "".join(img(src) for src in other) +
        '</a>'
        '<a href="/loans/loan-alpha">Lien</a></div></section>'
    )
    return next(n for n in _dom(html).walk() if n.has_class("js-loan"))


@pytest.mark.parametrize(("sources", "limit", "status", "url"), [
    ([], 3, "missing", None),
    ([FEEDBOOKS], 3, "observed", FEEDBOOKS),
    ([BNF, BNF], 3, "observed", BNF),
    ([LEGACY], 3, "observed", LEGACY),
    ([FEEDBOOKS, BNF], 3, "conflicting", None),
    ([FEEDBOOKS, FEEDBOOKS, FEEDBOOKS, FEEDBOOKS], 3, "ambiguous", None),
    (["https://forbidden.example/cover.jpg"], 3, "rejected", None),
    (["https://pret.bnf.fr/covers/a.jpg?token=synthetic-secret"], 3, "rejected", None),
    ([BNF, "https://forbidden.example/image.jpg"], 3, "rejected", None),
])
def test_candidates_have_fixed_diagnostic_status_without_weak_fallback(
    sources, limit, status, url,
):
    observation = _cover_candidates_diagnosis(sources, limit=limit)
    assert observation == (status, url)
    assert "synthetic-secret" not in observation[0]


def test_resource_anchor_is_exact_and_neighbor_never_supplies_a_cover():
    for status, expected in (
        ("missing", None),
        ("observed", BNF),
    ):
        own = (BNF,) if status == "observed" else ()
        document = card(*own, other=(FEEDBOOKS,))
        assert _loan_cover_diagnosis(document, "edition-alpha") == (
            status, expected,
        )
        assert _loan_cover(document, "edition-alpha") == expected
    assert _loan_cover_diagnosis(card(BNF), "edition-unknown") == (
        "missing", None,
    )


def test_multiple_exact_resource_images_are_not_first_wins():
    document = card(BNF, FEEDBOOKS, other=(LEGACY,))
    assert _loan_cover_diagnosis(document, "edition-alpha") == (
        "conflicting", None,
    )
    assert _loan_cover(document, "edition-alpha") is None


def test_invalid_and_signed_sources_never_become_public_image():
    sources = (
        "https://pret.bnf.fr/loans/loan-alpha?token=synthetic-secret",
        "https://covers.feedbooks.net/item/test_alpha.jpg?size=large&t=1&auth=abc",
        "//covers.feedbooks.net/item/test_alpha.jpg?size=large&t=1",
        "/covers/../admin.jpg",
    )
    for source in sources:
        assert _public_cover_url(source) is None
        assert _loan_cover_diagnosis(card(source), "edition-alpha") == (
            "rejected", None,
        )


def test_public_family_is_normalized_only_for_exact_source():
    document = card("/covers/test_alpha.jpg", other=(FEEDBOOKS,))
    assert _loan_cover_diagnosis(document, "edition-alpha") == (
        "observed", BNF,
    )


def test_diagnostic_does_not_change_loan_eligibility_or_make_io():
    document = card(FEEDBOOKS)
    status, url = _loan_cover_diagnosis(document, "edition-alpha")
    assert status == "observed" and url == FEEDBOOKS
    # Neither diagnostics nor a public URL attest account, loan right or CDN
    # availability. The parser is pure and performs no HTTP, GET or borrow.
