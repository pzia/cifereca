"""#1346: the session boundary never broadens an unknown scope into private search.

The real Vault + catalogue + session are exercised with local MockTransport.
No personal Kobo account, downloaded content, filesystem mutation or DRM.
"""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import ProviderError
from tests.test_kobo_search_public_only_unbound_p1 import _case


@pytest.mark.parametrize("scope", [
    "library", "unknown", "", "catalogue", "ALL", " physical ",
    "catalog,library", "PRIVATE_SIGNED_URL?access_token=PRIVATE_SECRET",
    "personal", None, False, 0, 1, [], ["catalog"], {}, {"scope": "all"},
])
def test_unknown_scope_never_requests_public_or_private_api(scope):
    session, paths = _case(library_status=200)
    try:
        for _ in range(2):
            with pytest.raises(ProviderError, match="portée de recherche invalide") as error:
                session.search("book", scope=scope)
            assert paths == []
            for secret in ("PRIVATE_", "Bearer", "access_token", "library"):
                assert secret not in str(error.value)
    finally:
        session.close()


@pytest.mark.parametrize("scope,expected_paths,expected_records", [
    ("catalog", ["/api/v2/catalog/search"], ["public-edition"]),
    ("digital", ["/api/v2/catalog/search", "/api/v2/library/search"],
     ["public-edition", "owned-edition"]),
    ("all", ["/api/v2/catalog/search", "/api/v2/library/search"],
     ["public-edition", "owned-edition"]),
])
def test_valid_host_search_scopes_keep_bounded_http_and_rights(
    scope, expected_paths, expected_records,
):
    session, paths = _case(library_status=200)
    try:
        results = session.search("book", scope=scope)
        assert paths == expected_paths
        assert [result.record_id for result in results] == expected_records
        if scope == "catalog":
            assert results[0].owned is False
            assert results[0].downloadable is None
            assert results[0].revision_id is None
        elif scope in {"all", "digital"}:
            assert results[0].owned is False
            assert results[1].owned is True
            assert results[1].revision_id == "revision-1"
    finally:
        session.close()


def test_unbound_digital_scope_stays_public_and_redacts_personal_metadata():
    session, paths = _case(library_status=503)
    try:
        session.connected = False
        results = session.search("book", scope="digital")
        assert paths == ["/api/v2/catalog/search"]
        assert [result.record_id for result in results] == ["public-edition"]
        assert results[0].owned is False
        assert results[0].revision_id is None
        exported = json.dumps(results[0].metadata)
        for secret in ("PRIVATE_", "account_id", "download_url", "revision_id"):
            assert secret not in exported
    finally:
        session.close()
