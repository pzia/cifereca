"""#1917: Numilog private and public routes reject invalid pages before I/O."""
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog import DESCRIPTOR as OWNED_DESCRIPTOR, NumilogSession
from cifereca_numilog_public_provider import DESCRIPTOR as PUBLIC_DESCRIPTOR


def session():
    return NumilogSession(
        credential_id="offline-account", label="Offline", credentials={},
        options={}, config={}, diagnostics=None,
    )


INVALID = (-1, 101, True, False, 0.0, 1.0, "0", "1", None, [], {}, object())


@pytest.mark.parametrize("scope", ("all", "digital", "catalog"))
@pytest.mark.parametrize("page", INVALID)
def test_invalid_page_is_rejected_before_private_or_public_io(scope, page, monkeypatch):
    s = session()
    events = []
    monkeypatch.setattr(s, "library", lambda: events.append("private") or [])
    monkeypatch.setattr(s, "search_public", lambda *_a, **_kw: events.append("public") or [])
    try:
        with pytest.raises(ProviderCapabilityError, match="Numilog : invalid_page"):
            s.search("roman", scope=scope, page=page)
        assert events == []
    finally:
        s.close()


@pytest.mark.parametrize("scope", ("all", "digital"))
@pytest.mark.parametrize("page,expected", ((0, 50), (1, 50), (2, 1), (100, 0)))
def test_valid_private_page_is_sliced_without_coercion(scope, page, expected, monkeypatch):
    s = session()
    rows = [SimpleNamespace(title=f"Roman {index}") for index in range(101)]
    called = []
    monkeypatch.setattr(s, "library", lambda: called.append("private") or rows)
    monkeypatch.setattr(s, "search_public", lambda *_a, **_kw: pytest.fail("not public"))
    try:
        result = s.search("roman", scope=scope, page=page)
        assert len(result) == expected
        assert called == ["private"]
        if page <= 2:
            assert result[0] is rows[page * 50]
    finally:
        s.close()


@pytest.mark.parametrize("page", (0, 1, 100))
def test_valid_public_page_remains_isolated_from_private_library(page, monkeypatch):
    s = session()
    called = []
    monkeypatch.setattr(s, "library", lambda: pytest.fail("public must not read purchases"))
    monkeypatch.setattr(
        s, "search_public",
        lambda query, *, page, field: called.append((query, page, field)) or ["public"],
    )
    try:
        assert s.search("roman", scope="catalog", page=page) == ["public"]
        assert called == [("roman", page, "all")]
    finally:
        s.close()


def test_search_descriptors_expose_same_valid_page_bound():
    owned = OWNED_DESCRIPTOR.search_capabilities
    public = PUBLIC_DESCRIPTOR.search_capabilities
    assert owned.pagination == public.pagination == "page"
    assert owned.max_pages == public.max_pages == 101
    assert owned.page_size == 50
    assert public.page_size == 20
