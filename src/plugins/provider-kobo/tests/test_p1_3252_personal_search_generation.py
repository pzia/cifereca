"""#3252: personal Kobo search results cannot outlive the bound account.

Synthetic catalogue rows only; no real HTTP, acquisitions, files or Core work.
"""
from types import SimpleNamespace as NS

import pytest
from cifereca.plugin_api import Account, ProviderError
from tests.test_p1_3252_inflight_personal_manifest import FakeVault, _session


def _row(*, private=True):
    return NS(
        title="Synthetic Kobo", metadata={"account_id": "alice"} if private else {},
        source_collection="library" if private else "catalog",
        owned=private, downloadable=False, thumbnail_url=None,
    )


def _catalog(session, handler):
    session.catalog = NS(
        search=handler,
        last_search_rejections=[],
        cover_url=lambda row: None,
    )


def _change_binding(session, race):
    if race == "disconnect":
        session.close()
    elif race == "same_account_reconnect":
        session._personal_binding_generation += 1
    elif race == "account_replaced":
        session._accounts = [Account(
            unique_id="alice", display_name="Other session",
            provider_id=session.provider_id, credential_id=session.credential_id,
        )]


@pytest.mark.parametrize("race", [
    "disconnect", "same_account_reconnect", "account_replaced",
])
@pytest.mark.parametrize("phase", ["upstream", "normalize"])
def test_personal_search_denies_post_read_identity_race(race, phase):
    vault = FakeVault([])
    session = _session(vault)
    calls = []
    class Row:
        source_collection = "library"
        owned = True
        downloadable = False
        thumbnail_url = None

        @property
        def metadata(self):
            if phase == "normalize":
                _change_binding(session, race)
            return {"account_id": "alice"}

    def search(query, **options):
        calls.append((query, options))
        if phase == "upstream":
            _change_binding(session, race)
        return [Row()]

    _catalog(session, search)
    try:
        with pytest.raises(ProviderError, match="session modifiée pendant lecture"):
            session.search("synthetic", scope="digital")
        assert len(calls) == 1
        assert calls[0][1].get("scope") is None
        assert vault.calls == 0
    finally:
        session.close()


def test_stable_personal_search_returns_only_same_account_book():
    session = _session(FakeVault([]))
    calls = []
    _catalog(session, lambda q, **kw: (calls.append(kw) or [
        _row(), _row(private=False),
        NS(metadata={"account_id": "bob"}, source_collection="library",
           owned=True, thumbnail_url=None),
    ]))
    try:
        results = session.search("synthetic", scope="all")
        assert len(results) == 2
        assert results[0].owned is True
        assert results[1].source_collection == "catalog"
        assert calls == [{"field": "all", "limit": 50, "page": 0}]
    finally:
        session.close()


def test_catalog_only_stays_public_during_account_close():
    session = _session(FakeVault([]))
    calls = []
    def public(query, **options):
        calls.append(options)
        session.close()
        return [_row(private=False)]
    _catalog(session, public)
    try:
        results = session.search("synthetic", scope="catalog")
        assert len(results) == 1
        assert results[0].owned is False
        assert calls == [{"field": "all", "limit": 50, "page": 0, "scope": "catalog"}]
    finally:
        session.close()


def test_unbound_search_uses_public_catalogue_without_claiming_private_rights():
    session = _session(FakeVault([]))
    session.connected = False
    calls = []
    _catalog(session, lambda q, **kw: (calls.append(kw) or [
        _row(private=True), _row(private=False),
    ]))
    try:
        result = session.search("synthetic", scope="all")
        assert len(result) == 1 and result[0].owned is False
        assert calls == [{"field": "all", "limit": 50, "page": 0, "scope": "catalog"}]
        assert any("partial_results" in v for v in session.last_search_warnings)
    finally:
        session.close()
