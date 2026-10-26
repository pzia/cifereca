"""#1917: Gutenberg facet parser accepts real text only.

No network and no real Gutenberg requests, public or private.
"""
import httpx
import pytest
from cifereca.plugin_api import ProviderCapabilityError
from cifereca_gutenberg import GutenbergSession


class Poison:
    def __str__(self):
        raise AssertionError("PRIVATE_IDENTITY_TOKEN")


class SecretString(str):
    pass


def session():
    calls = []
    instance = GutenbergSession(
        credential_id="public", label="", credentials={},
        options={"contact": "plugin-tests@example.test"},
        config={}, diagnostics=None,
    )
    instance.client.close()
    instance.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: (calls.append(str(req.url)) or httpx.Response(200, content=b""))
        )
    )
    return instance, calls


@pytest.mark.parametrize("query", [
    Poison(), SecretString("PRIVATE_TOKEN"), [], {}, 0, False,
    b"PRIVATE", "x" * 2049, "foo\nbar", "x\x00y",
    "author:\u202ePRIVATE",
])
@pytest.mark.parametrize("field", ["all", "author", "language"])
def test_invalid_query_fails_before_facet_or_http(query, field):
    s, calls = session()
    try:
        s.last_search_warnings = ["old warning"]
        with pytest.raises(ProviderCapabilityError, match="invalid_query") as err:
            s.search(query, field=field)
        assert calls == []
        assert s.last_search_warnings == []
        assert "PRIVATE" not in str(err.value)
    finally:
        s.close()


def test_none_and_short_plain_text_keep_original_empty_search_contract():
    s, calls = session()
    try:
        assert s.search(None) == []
        assert s.search("x") == []
        assert calls == []
    finally:
        s.close()


@pytest.mark.parametrize("field", ["all", "title", "author", "subject", "language"])
def test_one_character_query_is_not_an_attested_gutenberg_zero(field):
    s, calls = session()
    try:
        s.last_search_warnings = ["stale warning"]
        assert s.search("x", field=field) == []
        assert calls == []
        assert s.last_search_warnings == [
            "partial_results : requête trop courte ; Gutenberg non interrogé",
        ]
        assert s.search(None, field=field) == []
        assert s.last_search_warnings == []
        assert calls == []
    finally:
        s.close()
