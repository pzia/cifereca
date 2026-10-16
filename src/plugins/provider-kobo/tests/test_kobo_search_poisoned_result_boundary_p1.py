"""#1346: poisoned Kobo catalogue search rows cannot cross account boundaries.

No HTTP, Vault requests, metadata writes, downloads or DRM are involved.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace as NS

import pytest

from tests.test_kobo_manifest_policy_p1 import FakeVault, session_with_vault


def row(name, *, owned=False, collection="catalog", metadata=None, cover=None):
    return NS(
        title=name,
        record_id=name,
        owned=owned,
        source_collection=collection,
        metadata=metadata,
        thumbnail_url=cover,
    )


class GetterBomb:
    def __init__(self, field):
        self.field = field

    def __getattribute__(self, field):
        if field == object.__getattribute__(self, "field"):
            PrivateException = type("Bearer_PRIVATE_ACCOUNT", (RuntimeError,), {})
            raise PrivateException("/home/PRIVATE_READER/secret.epub")
        return object.__getattribute__(self, field)


class DictBomb(dict):
    def __contains__(self, key):
        raise RuntimeError("Bearer PRIVATE_ACCOUNT")

    def __getitem__(self, key):
        raise RuntimeError("Bearer PRIVATE_ACCOUNT")


@pytest.mark.parametrize("bad", [
    GetterBomb("metadata"),
    GetterBomb("owned"),
    GetterBomb("source_collection"),
    row("invalid-collection", collection=["library"]),
    row("invalid-owned", owned="true"),
    row("invalid-metadata", metadata=DictBomb(account_id="PRIVATE_ACCOUNT")),
    row("foreign", owned=True, collection="library", metadata={"account_id": "other"}),
])
def test_bad_or_foreign_row_is_skipped_without_abort_or_token_leak(bad, caplog):
    session = session_with_vault(FakeVault([]))
    valid = row("safe", owned=False, cover="https://covers.example/a.jpg")
    try:
        session.catalog = NS(search=lambda query, **kw: [bad, valid])
        with caplog.at_level(logging.DEBUG, logger="cifereca"):
            results = session.search("query", scope="digital")
        assert results == [valid]
        assert "source=search items=1 with_cover=1" in caplog.text
        assert "PRIVATE" not in caplog.text
        assert "Bearer" not in caplog.text
    finally:
        session.close()


def test_connected_profile_never_accepts_a_poisoned_foreign_account_binding():
    session = session_with_vault(FakeVault([]), account_id="current")
    own = row("own", owned=True, collection="library", metadata={"account_id": "current"})
    other = row("other", owned=True, collection="library", metadata={"account_id": "other"})
    unknown = row("invalid", owned=True, collection="library", metadata={"account_id": False})
    try:
        session.catalog = NS(search=lambda query, **kw: [other, own, unknown])
        assert session.search("query", scope="all") == [own]
    finally:
        session.close()


def test_unbound_session_keeps_public_book_and_never_fetches_private_results():
    session = session_with_vault(FakeVault([]), account_id="current")
    public = row("public", metadata={})
    private = row("private", owned=True, collection="library")
    try:
        session.connected = False
        requests = []
        session.catalog = NS(
            search=lambda query, **options:
                requests.append(options) or [private, GetterBomb("owned"), public],
        )
        assert session.search("query") == [public]
        assert requests == [{"field": "all", "limit": 50, "page": 0, "scope": "catalog"}]
    finally:
        session.close()


def test_malformed_cover_getter_cannot_abort_trace_or_harmless_search():
    session = session_with_vault(FakeVault([]))
    good = row("good")
    class CoverBomb:
        metadata = {}
        owned = False
        source_collection = "catalog"
        def __getattribute__(self, key):
            if key == "thumbnail_url":
                raise RuntimeError("Bearer PRIVATE_COVER")
            return object.__getattribute__(self, key)
    private_cover = CoverBomb()
    try:
        session.catalog = NS(search=lambda *_a, **_kw: [private_cover, good])
        assert session.search("query") == [private_cover, good]
    finally:
        session.close()
