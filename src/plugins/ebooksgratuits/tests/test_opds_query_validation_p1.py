"""#1917: EbooksGratuits public search never stringifies arbitrary query data.

Both acquisition backends reject before HTTP. No tokens, accounts or downloads.
"""
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_ebooksgratuits import EbooksSession, _opds_search


class ForbiddenText:
    def __str__(self):
        raise AssertionError("PRIVATE_CREDENTIAL")


class PrivateString(str):
    pass


class NeverHTTP:
    def __init__(self):
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("HTTP request on invalid query")

    def close(self):
        pass


def create(backend):
    session = EbooksSession(
        credential_id="public", label="", credentials={},
        options={"acquisition_backend": backend},
        config={}, diagnostics=None, transport=None,
    )
    session.client.close()
    session.client = NeverHTTP()
    session.connect()
    return session


@pytest.mark.parametrize("bad", [
    ForbiddenText(), PrivateString("secret"),
    {}, [], 42, True, b"PRIVATE", "x" * 513,
    "a\u202ePRIVATE", "x\x00y", "x\ny",
])
@pytest.mark.parametrize("backend", ["opds", "html"])
def test_malformed_query_typed_before_any_network(bad, backend):
    session = create(backend)
    try:
        session.last_search_warnings = ["previous identity"]
        with pytest.raises(ProviderCapabilityError, match="invalid_query") as err:
            session.search(bad)
        assert "PRIVATE" not in str(err.value)
        assert session.client.calls == []
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_internal_opds_search_also_rejects_unsafe_query_without_fetching():
    session = create("opds")
    try:
        with pytest.raises(ProviderCapabilityError, match="invalid_query"):
            _opds_search(session, ForbiddenText())
        assert session.client.calls == []
    finally:
        session.close()


def test_empty_none_query_retains_browse_semantics_without_stringifying():
    session = create("opds")
    try:
        # Validation is safe and preserves the historical empty browse path.
        from cifereca_ebooksgratuits import _validated_public_query
        assert _validated_public_query(None) == ""
        assert _validated_public_query("") == ""
        assert _validated_public_query("Les Misérables") == "Les Misérables"
    finally:
        session.close()
