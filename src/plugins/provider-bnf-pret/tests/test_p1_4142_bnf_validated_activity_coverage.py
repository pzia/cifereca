"""P1 #4142: active-loan coverage is scoped and refuses ambiguous HTML.

Synthetic DOM and fake client only; no network, tokens, ADEPT or rights writes.
"""
from dataclasses import replace

import pytest

from cifereca_bnf_pret import BnfProviderSession
from cifereca_bnf_pret_web import ORIGIN, ProfileScope, parse_loans
from cifereca.plugin_api import ProviderConfigurationError


SCOPE = ProfileScope("credential-a", "credential-a", "epoch-1")


def card(loan="loan-1", resource="book-1"):
    return (
        '<div class="js-loan">'
        f'<a href="/resources/{resource}">Synthetic book</a>'
        f'<a href="/loans/{loan}">Read</a>'
        '</div>'
    )


def page(content):
    return '<section class="active-loans">' + content + '</section>'


def parsed(html):
    return parse_loans(
        html, page_url=ORIGIN + "/my_profile/activity",
        expected=SCOPE, observed=SCOPE,
    )


@pytest.mark.parametrize("cards", [
    card(),
    card() + card("loan-2", "book-2"),
    card() + card("loan-2", "book-2") + card("loan-3", "book-3"),
])
def test_closed_scoped_nonempty_widget_attests_only_positive_inventory(cards):
    result = parsed(page(cards))
    assert result.status == "observed"
    assert result.exhaustive is True
    assert len(result.loans) == cards.count('class="js-loan"')


@pytest.mark.parametrize("html", [
    page(card() + '<div class="js-loan">Unloaded</div>'),
    page(card() + '<button class="load-more">More</button>'),
    page(card() + '<a rel="next" href="?page=2">Next</a>'),
    page(card() + '<div class="pagination">1 2</div>'),
    page(card() + '<div aria-busy="true"></div>'),
    page(card() + '<div data-has-more="true"></div>'),
    page(card() + '<div data-next-page="2"></div>'),
    page(card() + card("loan-1", "another-book")),
])
def test_partial_lazy_or_contradictory_html_never_attests_exhaustive(html):
    result = parsed(html)
    assert result.exhaustive is False


def test_unrelated_catalogue_pagination_never_pollutes_active_loan_coverage():
    result = parsed(
        page(card()) + '<section class="catalogue">'
        '<button class="load-more">More books</button></section>'
    )
    assert result.exhaustive is True


class Client:
    def __init__(self):
        self.html = page(card())
        self.calls = []

    def login(self, *args):
        self.calls.append("login")
        return self.html

    def activity(self):
        self.calls.append("activity")
        return self.html

    def clear(self):
        self.calls.append("clear")

    def close(self):
        self.calls.append("close")


def test_complete_partial_complete_never_reuses_prior_coverage_or_identity():
    client = Client()
    session = BnfProviderSession(
        client=client, credential_id="credential-a",
        label="Synthetic BnF", credentials={
            "username": "synthetic", "password": "synthetic",
        }, options={},
    )
    (account,) = session.connect()
    account.identity_id = "identity-a"
    try:
        complete = session.loan_inventory_snapshot(replace(account))
        assert complete.status == "success"
        assert complete.branch_claims_complete("bnf_html", "digital")
        assert complete.branches[0].pagination_complete is True

        client.html = page("")
        empty = session.loan_inventory_snapshot(replace(account))
        assert empty.status == "success" and empty.loans == ()
        assert empty.branch_claims_complete("bnf_html", "digital")

        client.html = page(card() + '<button class="load-more">More</button>')
        paged = session.loan_inventory_snapshot(replace(account))
        assert paged.status == "partial"
        assert len(paged.loans) == 1
        assert not paged.branch_claims_complete("bnf_html", "digital")

        client.html = page(card())
        again = session.loan_inventory_snapshot(replace(account))
        assert again.status == "success"
        assert len(again.loans) == 1

        wrong = replace(account, identity_id="identity-b")
        with pytest.raises(ProviderConfigurationError):
            session.loan_inventory_snapshot(wrong)
        assert client.calls == ["login", "activity", "activity", "activity", "activity"]
    finally:
        session.close()


def test_no_forged_typed_completeness_when_v1_reader_is_replaced():
    client = Client()
    session = BnfProviderSession(
        client=client, credential_id="credential-a",
        label="Synthetic BnF", credentials={
            "username": "synthetic", "password": "synthetic",
        }, options={},
    )
    (account,) = session.connect()
    account.identity_id = "identity-a"
    try:
        from_v1 = session.loans(account)
        session._read_live_loans_locked = lambda _account: list(from_v1)
        result = session.loan_inventory_snapshot(account)
        assert result.status == "partial"
        assert not result.branch_claims_complete("bnf_html", "digital")
        assert client.calls == ["login", "activity"]
    finally:
        session.close()


@pytest.mark.parametrize("hidden_wrapper", [
    '<div hidden>{card}</div>',
    '<div aria-hidden="true">{card}</div>',
    '<template>{card}</template>',
])
def test_hidden_cards_never_complete_visible_inventory(hidden_wrapper):
    result = parsed(page(card() + hidden_wrapper.format(
        card=card("loan-hidden", "book-hidden"),
    )))
    assert result.exhaustive is False
    assert [row.loan_id for row in result.loans] == ["loan-1"]
    only_hidden = parsed(page(hidden_wrapper.format(card=card())))
    assert only_hidden.loans == ()
    assert only_hidden.status == "partial"
    assert only_hidden.exhaustive is False


def test_hidden_loan_card_itself_never_completes_visible_inventory():
    hidden = card("loan-hidden", "book-hidden").replace(
        'class="js-loan"', 'class="js-loan" hidden',
    )
    result = parsed(page(card() + hidden))
    assert len(result.loans) == 1
    assert result.exhaustive is False


@pytest.mark.parametrize("html", [
    page(""),
    page("<p>Aucun emprunt en cours</p>"),
])
def test_qualified_empty_active_widget_is_complete(html):
    result = parsed(html)
    assert result.status == "observed"
    assert result.loans == ()
    assert result.exhaustive is True


@pytest.mark.parametrize("html", [
    page('<button class="load-more">Suite</button>'),
    page('<div aria-busy="true"></div>'),
    page('<div data-has-more="true"></div>'),
    page('<div class="js-loan">Incomplete</div>'),
])
def test_empty_but_incomplete_widget_does_not_claim_zero_loans(html):
    result = parsed(html)
    assert result.exhaustive is False
    assert result.status == "partial"
