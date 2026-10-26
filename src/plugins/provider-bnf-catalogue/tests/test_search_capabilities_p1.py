"""#1917: BnF SRU native vs page-local search modes are declared honestly.

All requests use a synthetic client. No network, private account or acquisition.
"""
from types import SimpleNamespace

import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_bnf_catalogue import BnfCatalogueSession, DESCRIPTOR, ENDPOINT


class FakeClient:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def get(self, url, *, params):
        self.calls.append((url, dict(params)))
        if self.fail:
            raise httpx.ConnectError("PRIVATE_CREDENTIAL")
        return SimpleNamespace(
            content=b'<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"><numberOfRecords>0</numberOfRecords></searchRetrieveResponse>',
            raise_for_status=lambda: None,
        )

    def close(self):
        pass


def make_session(client):
    session = BnfCatalogueSession(
        credential_id="public", label="", credentials={}, options={},
        diagnostics=SimpleNamespace(add=lambda *a, **kw: None),
    )
    session.client.close()
    session.client = client
    return session


def test_bnf_search_declares_native_and_page_local_fields():
    cap = DESCRIPTOR.search_capabilities
    assert cap.declared
    assert cap.fields == ("all", "title", "author", "isbn", "subject", "series", "collection")
    assert cap.scopes == ("all", "physical", "digital")
    assert cap.pagination == "page" and cap.page_size == 50
    assert cap.max_pages == 101
    assert any("post-filtre" in note for note in cap.notes)
    assert not cap.supports_field("language")


@pytest.mark.parametrize("field,scope", [
    ("language", "all"), ("not-a-field", "all"),
    ("author", "private"), ("isbn", "owned"),
])
def test_unsupported_request_is_explicit_without_http(field, scope):
    client = FakeClient()
    session = make_session(client)
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported_"):
            session.search("a", field=field, scope=scope)
        assert client.calls == []
    finally:
        session.close()


@pytest.mark.parametrize("field,prefix", [
    ("all", "bib.anywhere all"),
    ("title", "bib.title all"),
    ("author", "bib.author all"),
    ("subject", "bib.subject all"),
    ("series", "bib.anywhere all"),
    ("collection", "bib.anywhere all"),
    ("isbn", "bib.isbn adj"),
])
def test_supported_field_uses_documented_sru_route_and_page(field, prefix):
    client = FakeClient()
    session = make_session(client)
    term = "9780306406157" if field == "isbn" else "Conan Doyle"
    try:
        assert session.search(term, scope="digital", page=2, field=field) == []
        assert len(client.calls) == 1
        url, params = client.calls[0]
        assert url == ENDPOINT
        assert params["startRecord"] == "101"
        assert params["maximumRecords"] == "50"
        assert params["query"].startswith(prefix + " ")
    finally:
        session.close()


def test_zero_results_after_actual_request_is_not_failure():
    client = FakeClient()
    session = make_session(client)
    try:
        assert session.search("unknown title", field="all") == []
        assert len(client.calls) == 1
    finally:
        session.close()


def test_sru_network_failure_does_not_expose_provider_exception():
    client = FakeClient(fail=True)
    session = make_session(client)
    try:
        with pytest.raises(ProviderError, match="catalogue temporairement indisponible") as caught:
            session.search("Doyle", field="author")
        assert "PRIVATE_CREDENTIAL" not in str(caught.value)
    finally:
        session.close()



@pytest.mark.parametrize("page", (-1, True, False, "2", "bad", 2.5, None, 101, 10**100))
def test_invalid_page_rejected_before_http_even_for_short_query(page):
    client = FakeClient()
    session = make_session(client)
    try:
        with pytest.raises(ProviderCapabilityError, match="invalid_page"):
            session.search("x", page=page)
        assert client.calls == []
        assert session.last_search_warnings == []
    finally:
        session.close()


@pytest.mark.parametrize("page,first_record", ((0, "1"), (1, "51"), (100, "5001")))
def test_zero_index_page_offsets_are_not_silently_clamped(page, first_record):
    client = FakeClient()
    session = make_session(client)
    try:
        assert session.search("Doyle", page=page) == []
        assert len(client.calls) == 1
        assert client.calls[0][1]["startRecord"] == first_record
    finally:
        session.close()


@pytest.mark.parametrize("field", ["all", "author", "title", "subject"])
def test_one_character_query_is_skipped_not_attested_zero(field):
    client = FakeClient()
    session = make_session(client)
    try:
        session.last_search_warnings = ["previous"]
        assert session.search("x", field=field) == []
        assert client.calls == []
        assert session.last_search_warnings == [
            "partial_results : requête trop courte ; BnF non interrogée",
        ]
        # Clear query is an intentional UI reset, not a user search result.
        assert session.search("", field=field) == []
        assert session.last_search_warnings == []
        # A real requested SRU zero must still not report partial coverage.
        assert session.search("Doyle", field=field) == []
        assert len(client.calls) == 1
        assert session.last_search_warnings == []
    finally:
        session.close()
