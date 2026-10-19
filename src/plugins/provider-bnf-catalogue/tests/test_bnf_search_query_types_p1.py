"""#1917: BnF SRU search never coerces arbitrary provider query objects.

Offline failure boundary: no SRU HTTP, private identifier or account access.
"""
from types import SimpleNamespace
import pytest
from cifereca.plugin_api import ProviderCapabilityError
from cifereca_bnf_catalogue import BnfCatalogueSession


class NonTextQuery:
    def __str__(self):
        raise AssertionError("PRIVATE_CREDENTIAL_TOKEN")


class PrivateString(str):
    pass


class UnusedHttp:
    def __init__(self):
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("unexpected HTTP")

    def close(self):
        pass


def session():
    instance = BnfCatalogueSession(
        credential_id="public", label="", credentials={}, options={},
        diagnostics=SimpleNamespace(add=lambda *_a, **_kw: None),
    )
    instance.client.close()
    instance.client = UnusedHttp()
    return instance


@pytest.mark.parametrize("bad", [
    NonTextQuery(), PrivateString("PRIVATE_PROFILE"), {}, [], 23, False,
    b"SECRET", "x" * 2049, "x\ny", "x\x00y", "x\u202ePRIVATE",
])
@pytest.mark.parametrize("field", ["all", "author", "isbn"])
def test_invalid_query_is_typed_before_sru_access(bad, field):
    instance = session()
    try:
        instance.last_search_warnings = ["stale warning"]
        with pytest.raises(ProviderCapabilityError, match="invalid_query") as caught:
            instance.search(bad, field=field)
        assert instance.client.calls == []
        assert instance.last_search_warnings == []
        assert "PRIVATE" not in str(caught.value)
    finally:
        instance.close()


def test_none_remains_empty_non_isbn_search_without_http():
    instance = session()
    try:
        assert instance.search(None, field="all") == []
        assert instance.client.calls == []
    finally:
        instance.close()


def test_none_isbn_does_not_become_an_attested_zero():
    from cifereca.plugin_api import ProviderError
    instance = session()
    try:
        with pytest.raises(ProviderError, match="ISBN attendu"):
            instance.search(None, field="isbn")
        assert instance.client.calls == []
    finally:
        instance.close()
