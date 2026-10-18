"""#1917: Paris search capabilities reflect upstream general text query.

All calls are synthetic: no account login, network, personal collections or DRM.
"""
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_paris import DESCRIPTOR, ParisProviderSession


class Config:
    def get(self, key, default=None):
        return default


class Diagnostics:
    def add(self, *args, **kwargs):
        return None


class FakeClient:
    def __init__(self, *, failure=None):
        self.calls = []
        self.last_search_warnings = []
        self.failure = failure

    def search(self, query, scope, page):
        self.calls.append((query, scope, page))
        if self.failure:
            raise self.failure
        if scope == "all":
            self.last_search_warnings = ["Catalogue physique momentanément indisponible"]
        else:
            self.last_search_warnings = []
        return []

    def close(self):
        return None


def make_session(client):
    session = ParisProviderSession(
        credential_id="paris-test",
        label="Paris",
        credentials={},
        options={},
        config=Config(),
        diagnostics=Diagnostics(),
    )
    session.client.close()
    session.client = client
    return session


def test_declaration_is_honest_about_general_query_and_catalogues():
    cap = DESCRIPTOR.search_capabilities
    assert cap.declared
    assert cap.fields == ("all",)
    assert cap.scopes == ("all", "digital", "physical")
    assert cap.pagination == "page" and cap.page_size == 50
    assert not cap.supports_field("author")
    assert not cap.supports_field("isbn")
    assert cap.supports_scope("digital")


@pytest.mark.parametrize("field", ["author", "title", "isbn", "subject", "language", ""])
def test_unsupported_field_is_rejected_before_client_search(field):
    client = FakeClient()
    session = make_session(client)
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported_field"):
            session.search("Doyle", field=field)
        assert client.calls == []
    finally:
        session.close()


@pytest.mark.parametrize("scope", ["all", "digital", "physical"])
def test_global_query_forwards_scope_and_page_and_preserves_warning(scope):
    client = FakeClient()
    session = make_session(client)
    try:
        assert session.search("Doyle", scope=scope, page=2, field="all") == []
        assert client.calls == [("Doyle", scope, 2)]
        assert session.last_search_warnings == (
            ["Catalogue physique momentanément indisponible"] if scope == "all" else []
        )
    finally:
        session.close()


def test_invalid_scope_does_not_fetch_any_catalogue():
    client = FakeClient()
    session = make_session(client)
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported_scope"):
            session.search("Doyle", scope="private-account")
        assert client.calls == []
    finally:
        session.close()


def test_upstream_failure_is_not_misreported_as_empty_result():
    class SyntheticUnavailable(RuntimeError):
        pass

    client = FakeClient(failure=SyntheticUnavailable("service unavailable"))
    session = make_session(client)
    try:
        with pytest.raises(SyntheticUnavailable):
            session.search("Doyle", field="all")
        assert client.calls == [("Doyle", "all", 0)]
    finally:
        session.close()


@pytest.mark.parametrize("invalid", (
    {"field": "author"}, {"field": "isbn"},
    {"scope": "PRIVATE_SIGNED_URL"}, {"scope": "unknown"},
))
def test_rejected_search_revokes_previous_partial_branch_warning(invalid):
    client = FakeClient()
    session = make_session(client)
    try:
        assert session.search("Doyle", scope="all", field="all") == []
        assert session.last_search_warnings == [
            "Catalogue physique momentanément indisponible",
        ]
        client.calls.clear()
        with pytest.raises(ProviderCapabilityError, match="unsupported_") as caught:
            session.search("Doyle", **invalid)
        assert client.calls == []
        assert session.last_search_warnings == []
        assert "PRIVATE_" not in str(caught.value)
        # A valid search still conveys the real branch warning normally.
        session.search("Doyle", scope="all")
        assert session.last_search_warnings == [
            "Catalogue physique momentanément indisponible",
        ]
    finally:
        session.close()
