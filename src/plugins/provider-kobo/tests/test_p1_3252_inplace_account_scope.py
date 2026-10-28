"""#3252: an in-place Kobo Account rebind is not a new session generation.

Synthetic Vault and catalogue only; no HTTP, acquisition or Core mutation.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import ProviderError
from tests.test_p1_3252_inflight_personal_manifest import FakeVault, _session
from tests.test_p1_3252_personal_search_generation import _catalog


@pytest.mark.parametrize("entrypoint", ["library", "backup_manifest"])
@pytest.mark.parametrize("phase", ["network", "projection"])
def test_kobo_inplace_account_id_change_blocks_personal_manifest(
    entrypoint, phase,
):
    vault = FakeVault([{
        "product_id": "book-1", "account_id": "alice",
        "owned": True, "downloadable": True,
    }])
    session = _session(vault)
    bound = session._accounts[0]
    generation = session._personal_binding_generation
    original = vault.library_manifest

    def changed_get():
        rows = original()
        bound.unique_id = "bob"
        return rows

    def changed_cover(_row):
        bound.unique_id = "bob"
        return None

    if phase == "network":
        vault.library_manifest = changed_get
    else:
        session.catalog.cover_url = changed_cover
    session.normalize_item = lambda row, **kwargs: row
    try:
        with pytest.raises(ProviderError, match="session modifiée pendant lecture"):
            getattr(session, entrypoint)()
        assert vault.calls == 1
        assert session._accounts[0] is bound
        assert session._personal_binding_generation == generation
        if entrypoint == "backup_manifest":
            assert session.backup_manifest_rejection_count == 0
            assert session.backup_manifest_policy["manifest_authoritative"] is False
            assert session.backup_manifest_policy["acquisition_authorized"] is False
    finally:
        session.close()


@pytest.mark.parametrize("phase", ["network", "projection"])
def test_kobo_inplace_account_id_change_blocks_personal_search(phase):
    session = _session(FakeVault([]))
    bound = session._accounts[0]
    generation = session._personal_binding_generation
    requests = []

    class Result:
        owned = True
        source_collection = "library"
        thumbnail_url = None

        @property
        def metadata(self):
            if phase == "projection":
                bound.unique_id = "bob"
            return {"account_id": "alice"}

    def search(query, **kwargs):
        requests.append(kwargs)
        if phase == "network":
            bound.unique_id = "bob"
        return [Result()]

    _catalog(session, search)
    try:
        with pytest.raises(ProviderError, match="session modifiée pendant lecture"):
            session.search("synthetic", scope="all")
        assert requests == [{"field": "all", "limit": 50, "page": 0}]
        assert session._accounts[0] is bound
        assert session._personal_binding_generation == generation
    finally:
        session.close()


def test_kobo_unchanged_bound_account_still_allows_personal_reads():
    vault = FakeVault([{
        "product_id": "book-1", "account_id": "alice",
        "owned": True, "downloadable": True,
    }])
    session = _session(vault)
    session.normalize_item = lambda row, **kwargs: row
    try:
        assert len(session.library()) == 1
        assert len(session.backup_manifest()) == 1
        assert vault.calls == 2
    finally:
        session.close()
