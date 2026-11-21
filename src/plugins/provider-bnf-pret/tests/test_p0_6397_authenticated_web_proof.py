"""#6397 — existing native BnF read, private scope and exact resource link.

HTML approximates the observed full result DOM; no live SSO, browser launch,
new GET or borrow permission is established.
"""
import pytest

from cifereca.plugin_api import Account
from cifereca_bnf_pret import BnfProviderSession
from cifereca_bnf_pret_web import ORIGIN, parse_search_page


def card(record="record-a", title="Fiction", extra=""):
    return ('<li class="list-details__item"><article class="view-details" '
            'itemscope itemtype="https://schema.org/Book">'
            '<a href="/resources/' + record + '"><h3 class="details-content__title">'
            + title + '</h3></a>' + extra + '</article></li>')


def results(*cards):
    return '<ul class="list-details">' + ''.join(cards) + '</ul>'


class Client:
    def __init__(self, body, during=None):
        self.body = body
        self.during = during
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        assert method == "GET" and url.startswith(ORIGIN + "/resources?q=")
        if self.during:
            self.during()
        return url, self.body

    def clear(self):
        pass

    def close(self):
        pass


def active(body, *, connected=True):
    client = Client(body)
    session = BnfProviderSession(
        client=client, credential_id="fixture-card", label="Fixture",
        credentials={}, options={},
    )
    account = Account(
        identity_id="alice", unique_id="fixture-card",
        provider_id="bnf-pret", credential_id="fixture-card",
        display_name="Fixture",
    )
    session._accounts = [account]
    session.connected = connected
    return session, account, client


def test_real_search_projects_exact_authenticated_native_card_without_new_get():
    session, account, client = active(results(card(), card("record-b")))
    try:
        rows = session.search("fiction")
        assert len(rows) == 2
        for row in rows:
            assert row.source_account_id == account.unique_id
            assert row.friendly_url == ORIGIN + "/resources/" + row.record_id
            assert row.metadata["authenticated_web_evidence"] == {
                "schema": 1, "provider_id": "bnf-pret", "record_id": row.record_id,
                "docbase": "BNF_PRET", "medium": "digital", "source_revision": None,
                "scenario": "BNF_PRET", "access_mode": "authenticated",
                "source_field": "results.Book.resource_link",
                "evidence": "bnf-pret.authenticated_search_record_identifier",
            }
            assert row.metadata.get("public_url_evidence") is None
            assert row.owned is None and row.downloadable is False and row.download_url is None
        assert client.calls == [("GET", ORIGIN + "/resources?q=fiction")]
    finally:
        session.close()


@pytest.mark.parametrize("body", [
    '<ul class="search-results__list"><li class="search-resource-result">'
    '<a href="/resources/record-a">Fiction</a></li></ul>',
    results(card(), card(title="Different edition")),
    results(card(), card("record-b", extra='<a href="/resources/foreign">Other</a>')),
])
def test_suggestion_partial_or_conflicting_result_never_attests_web(body):
    session, account, client = active(body)
    try:
        rows = session.search("fiction")
        assert rows
        assert all("authenticated_web_evidence" not in r.metadata
                   and r.source_account_id is None for r in rows)
        assert len(client.calls) == 1
    finally:
        session.close()


@pytest.mark.parametrize("change", ["disconnected", "replace", "owner", "credential", "duplicate"])
def test_same_call_account_scope_is_required(change):
    session, account, client = active(results(card()), connected=change != "disconnected")
    try:
        if change == "replace":
            from dataclasses import replace
            client.during = lambda: setattr(session, "_accounts", [replace(account)])
        elif change == "owner":
            client.during = lambda: setattr(account, "identity_id", "bob")
        elif change == "credential":
            client.during = lambda: setattr(account, "credential_id", "other-card")
        elif change == "duplicate":
            client.during = lambda: session._accounts.append(account)
        row, = session.search("fiction")
        assert "authenticated_web_evidence" not in row.metadata
        assert row.source_account_id is None
        assert len(client.calls) == 1
    finally:
        session.close()


def test_parser_never_promotes_partial_page_reference_inventory():
    complete = parse_search_page(results(card()))
    assert complete.status == "ok" and complete.records[0].web_reference_evidence is True
    incomplete = parse_search_page(results(card(), card(title="Different book")))
    assert incomplete.status == "partial" and not incomplete.records[0].web_reference_evidence
