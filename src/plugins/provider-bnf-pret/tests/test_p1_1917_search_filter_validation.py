"""#1917: BnF public catalogue filter rejection is typed and network-free.

Only synthetic session inputs: no login, SSO, browser or remote service.
"""
import pytest

import cifereca_bnf_pret as provider
from cifereca.plugin_api import ProviderCapabilityError


class NeverClient:
    def __init__(self):
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("Invalid filter reached network")

    def clear(self):
        pass

    def close(self):
        pass


def session():
    client = NeverClient()
    return provider.BnfProviderSession(
        client=client, credential_id="synthetic-card", label="BnF",
        credentials={}, options={},
    ), client


@pytest.mark.parametrize("invalid_scope", [
    [], {}, ["all"], {"digital": True}, 1, True, None, "physical",
])
def test_bad_scope_never_calls_public_search(invalid_scope):
    s, client = session()
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported_search"):
            s.search("synthetic", scope=invalid_scope)
        assert client.calls == []
    finally:
        s.close()


@pytest.mark.parametrize("invalid_filter", [
    1, True, "ebook", {"ebook": True}, [["ebook"]], [None],
    ["ebook", 3], ["x" * 81], {"ebook", "x" * 81},
])
def test_bad_content_type_never_calls_public_search(invalid_filter):
    s, client = session()
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported_content_type"):
            s.search("synthetic", content_types=invalid_filter)
        assert client.calls == []
    finally:
        s.close()


@pytest.mark.parametrize("invalid_page", [False, True, 1, 0.0, [], {}])
def test_bad_page_remains_explicit_rejection(invalid_page):
    s, client = session()
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported_search"):
            s.search("synthetic", page=invalid_page)
        assert client.calls == []
    finally:
        s.close()


@pytest.mark.parametrize("filter_value", [None, (), [], ["ebook"], ("ebook",), {"ebook", "audio"}])
def test_valid_or_omitted_ebook_filter_keeps_partial_public_search(filter_value, monkeypatch):
    s, client = session()
    visited = []
    monkeypatch.setattr(provider, "read_search", lambda *args, **kwargs: (
        visited.append("catalog") or ((), ("inventory_not_exhaustive",))
    ))
    try:
        assert s.search("synthetic", content_types=filter_value) == []
        assert visited == ["catalog"]
        assert s.last_search_warnings == ["inventory_not_exhaustive"]
        assert client.calls == []
    finally:
        s.close()
