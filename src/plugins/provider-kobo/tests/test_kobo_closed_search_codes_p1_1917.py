"""#1917: only the Kobo public SEARCH boundary maps Vault errors to Core.

Actual KoboSession + Vault adapter backed by a fake HTTP transport. No user
accounts, remote service, purchase, download or DRM actions.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca_kobo_vault import KoboVaultError
from cifereca.search_failures import search_failure_code
from cifereca.core.known_catalog_fallback import public_service_unavailable
from tests.test_kobo_search_public_only_unbound_p1 import _case


@pytest.mark.parametrize("status,closed", (
    (400, None),
    (401, "SearchAuthenticationRequired"),
    (403, "SearchAccessDenied"),
    (404, None),
    (408, "SearchTemporarilyUnavailable"),
    (429, "SearchTemporarilyUnavailable"),
    (503, "SearchTemporarilyUnavailable"),
))
def test_unbound_catalog_search_preserves_http_and_closed_core_code(
    status, closed,
):
    session, paths = _case(catalog_status=status)
    session.connected = False
    try:
        with pytest.raises(KoboVaultError) as caught:
            session.search("Public Book", scope="catalog", field="all")
        assert caught.value.status_code == status
        assert search_failure_code(caught.value) == closed
        assert public_service_unavailable(caught.value) is (
            closed == "SearchTemporarilyUnavailable"
        )
        assert paths == ["/api/v2/catalog/search"]
        assert session.last_search_warnings == []
        assert caught.value.__cause__ is None
        assert "PRIVATE" not in str(caught.value)
    finally:
        session.close()


@pytest.mark.parametrize("status", (429, 503))
def test_private_server_outage_does_not_downgrade_to_partial_public_ownership(status):
    session, paths = _case(library_status=status)
    try:
        with pytest.raises(KoboVaultError) as caught:
            session.search("Public Book", scope="all")
        assert paths == ["/api/v2/catalog/search", "/api/v2/library/search"]
        assert caught.value.status_code == status
        assert search_failure_code(caught.value) == "SearchTemporarilyUnavailable"
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_public_only_401_private_denial_still_preserves_catalogue_without_rights():
    session, paths = _case(library_status=401)
    try:
        items = session.search("Public Book", scope="all")
        assert paths == ["/api/v2/catalog/search", "/api/v2/library/search",
                         "/api/v2/catalog/search"]
        assert items and all(item.owned is False and item.downloadable is False
                             for item in items)
        assert any("partial_results" in warning for warning in session.last_search_warnings)
    finally:
        session.close()


def test_vendor_transport_exception_cause_is_not_exported():
    session, paths = _case(catalog_status=200)
    try:
        session.connected = False
        def broken(*args, **kwargs):
            original = httpx.ConnectError("PRIVATE_TOKEN https://signed.invalid/")
            raise KoboVaultError(
                "Kobo-Vault : erreur de transport", code="network_error",
            ) from original
        session.catalog.search = broken
        with pytest.raises(KoboVaultError) as caught:
            session.search("Public Book", scope="catalog")
        assert search_failure_code(caught.value) == "SearchTemporarilyUnavailable"
        assert caught.value.__cause__ is None
        assert caught.value.__suppress_context__ is True
        assert "PRIVATE_TOKEN" not in str(caught.value)
        assert paths == []  # No hidden second HTTP request or fallback.
    finally:
        session.close()
