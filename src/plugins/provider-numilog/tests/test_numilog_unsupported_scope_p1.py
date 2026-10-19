"""#1917: unsupported Numilog scope/field is not a false empty catalogue."""
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog import NumilogSession


def session():
    return NumilogSession(
        credential_id="synthetic", label="", credentials={},
        options={}, config={}, diagnostics=None,
    )


@pytest.mark.parametrize("scope", [
    "physical", "unknown", "", "CATALOG", None, False, 10, [],
    ["all"], {}, {"scope": "catalog"},
])
def test_invalid_scope_never_reads_owned_inventory_or_public_catalog(monkeypatch, scope):
    s = session()
    try:
        def forbidden(*args, **kwargs):
            pytest.fail("invalid scope attempted provider I/O")
        monkeypatch.setattr(s, "library", forbidden)
        monkeypatch.setattr(s, "search_public", forbidden)
        with pytest.raises(ProviderCapabilityError, match="unsupported_scope"):
            s.search("book", scope=scope, page=0, field="all")
    finally:
        s.close()


@pytest.mark.parametrize("field", [None, False, [], {}, "author", "isbn"])
def test_invalid_private_field_is_explicit_before_inventory(monkeypatch, field):
    s = session()
    try:
        monkeypatch.setattr(
            s, "library",
            lambda: pytest.fail("unsupported field must not scan purchases"),
        )
        with pytest.raises(ProviderCapabilityError, match="unsupported_field"):
            s.search("book", scope="all", field=field)
    finally:
        s.close()


@pytest.mark.parametrize("query", [None, False, [], {}, 10])
def test_invalid_private_query_is_not_a_transport_exception(monkeypatch, query):
    s = session()
    try:
        monkeypatch.setattr(
            s, "library",
            lambda: pytest.fail("invalid query must not scan purchases"),
        )
        with pytest.raises(ProviderCapabilityError, match="invalid_query"):
            s.search(query, scope="digital")
    finally:
        s.close()


def test_valid_private_search_still_returns_legitimate_empty_result(monkeypatch):
    s = session()
    try:
        monkeypatch.setattr(s, "library", lambda: [])
        assert s.search("no-title", scope="digital", field="all") == []
        assert s.search("no-title", scope="all", field="title") == []
    finally:
        s.close()
