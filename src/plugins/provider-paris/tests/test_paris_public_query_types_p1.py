"""#1917: Paris scope/field validation includes a trustworthy text query.

In-memory client only, no PNB user session, private HTTP or loan mutation.
"""
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_paris import ParisProviderSession


class PoisonQuery:
    def __str__(self):
        raise AssertionError("PRIVATE_BORROWER_ACCOUNT")


class StringSubclass(str):
    pass


class FakeClient:
    def __init__(self):
        self.calls = []
        self.last_search_warnings = []

    def search(self, query, scope, page):
        self.calls.append((query, scope, page))
        return []

    def close(self):
        pass


def session():
    result = ParisProviderSession(
        credential_id="paris-test", label="Paris", credentials={},
        options={}, config={}, diagnostics=None,
    )
    result.client.close()
    result.client = FakeClient()
    return result


@pytest.mark.parametrize("bad", [
    PoisonQuery(), StringSubclass("PRIVATE_SESSION"), {}, [], 42, True,
    b"SECRET", "x" * 2049, "x\nTOKEN", "x\x00y", "x\u202eTOKEN",
])
@pytest.mark.parametrize("scope", ["all", "digital", "physical"])
def test_query_invalid_before_any_catalogue_operation(bad, scope):
    provider = session()
    try:
        provider.last_search_warnings = ["previous catalogue partial"]
        with pytest.raises(ProviderCapabilityError, match="invalid_query") as caught:
            provider.search(bad, scope=scope)
        assert provider.client.calls == []
        assert provider.last_search_warnings == []
        assert "PRIVATE" not in str(caught.value)
    finally:
        provider.close()


def test_none_and_normal_text_preserve_general_search_contract():
    provider = session()
    try:
        assert provider.search(None, scope="digital") == []
        assert provider.search("Doyle", scope="physical") == []
        assert provider.client.calls == [
            ("", "digital", 0), ("Doyle", "physical", 0),
        ]
    finally:
        provider.close()
