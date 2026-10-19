"""#1917: malformed short ISBN cannot masquerade as BnF zero results."""
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import ProviderError
from cifereca_bnf_catalogue import BnfCatalogueSession


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, *_args, **kwargs):
        self.calls.append(kwargs)
        raise AssertionError("no HTTP should be attempted")

    def close(self):
        pass


def session():
    provider = BnfCatalogueSession(
        credential_id="public", label="", credentials={}, options={},
        diagnostics=SimpleNamespace(add=lambda *_a, **_kw: None),
    )
    provider.client.close()
    provider.client = FakeClient()
    return provider


@pytest.mark.parametrize("invalid", ["", " ", "x", "X", "1", "12", "123456789", "978"])
def test_malformed_short_or_empty_isbn_has_typed_error_without_http(invalid):
    provider = session()
    try:
        with pytest.raises(ProviderError, match="ISBN attendu") as err:
            provider.search(invalid, field="isbn")
        assert provider.client.calls == []
        assert provider.last_search_warnings == []
        assert invalid not in str(err.value)
    finally:
        provider.close()


def test_empty_general_search_still_skips_http_without_false_errors():
    provider = session()
    try:
        assert provider.search(" ", field="all") == []
        assert provider.client.calls == []
    finally:
        provider.close()
