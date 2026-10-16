"""#1917: Baen declares general-only public catalogue search.

No live network or private account: unsupported fields are rejected before HTTP,
while an actual general query may legitimately return zero rows.
"""
import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_baen import BaenSession, DESCRIPTOR, FREE


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeClient:
    def __init__(self, text='<a href="/allbooks/sample">Sample Book</a>', error=None):
        self.text = text
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return FakeResponse(self.text)

    def close(self):
        pass


def session_for_test(client):
    session = BaenSession(
        credential_id="public", label="", credentials={}, options={},
        config={}, diagnostics=None, transport=None,
    )
    session.client.close()
    session.client = client
    session.connect()
    return session


def test_capabilities_disclose_general_only_search_and_page_scope():
    capability = DESCRIPTOR.search_capabilities
    assert capability.declared
    assert capability.fields == ("all",)
    assert capability.scopes == ("all", "digital")
    assert capability.pagination == "page"
    assert capability.page_size == 50
    assert capability.supports_field("all")
    assert not capability.supports_field("author")
    assert not capability.supports_field("isbn")
    assert not capability.supports_scope("physical")


@pytest.mark.parametrize("field", ["title", "author", "isbn", "subject", "language", ""])
def test_unsupported_field_is_explicit_without_network(field):
    client = FakeClient()
    session = session_for_test(client)
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported_field"):
            session.search("Sample", field=field)
        assert client.calls == []
    finally:
        session.close()


def test_physical_scope_is_not_silently_reported_as_zero():
    client = FakeClient()
    session = session_for_test(client)
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported_scope"):
            session.search("Sample", scope="physical")
        assert client.calls == []
    finally:
        session.close()


def test_general_query_paginates_and_never_marks_public_books_owned():
    client = FakeClient()
    session = session_for_test(client)
    try:
        rows = session.search("Sample", scope="digital", page=2, field="all")
        assert len(rows) == 1
        assert rows[0].title == "Sample Book"
        assert rows[0].owned is False
        assert rows[0].downloadable is False
        assert client.calls == [(FREE, {"params": {
            "page": 3, "pageSize": 50, "hideBundles": 0,
            "hideEarcs": 0, "q": "Sample",
        }})]
    finally:
        session.close()


def test_zero_results_and_service_failure_are_distinct():
    empty_client = FakeClient(text="<html>No books</html>")
    session = session_for_test(empty_client)
    try:
        assert session.search("Missing", field="all") == []
        assert len(empty_client.calls) == 1
    finally:
        session.close()

    failed = FakeClient(error=httpx.ConnectError("PRIVATE_SIGNED_TOKEN"))
    session = session_for_test(failed)
    try:
        with pytest.raises(ProviderError, match="Baen : catalogue inaccessible") as caught:
            session.search("Missing", field="all")
        assert "PRIVATE_SIGNED_TOKEN" not in str(caught.value)
        assert len(failed.calls) == 1
    finally:
        session.close()
