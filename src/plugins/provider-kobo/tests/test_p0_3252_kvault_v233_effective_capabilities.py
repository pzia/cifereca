"""#3252: match pzia/KVault/src/worker.js actual Provider API v2.3.3 status.

KVault v2Status() returns an anonymous/global capability list and a
bearer-filtered list (NOT token_scopes). This test mirrors that exact public
contract with httpx.MockTransport; no real token, Kobo data or file writes.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_kobo import DESCRIPTOR, KoboSession
from cifereca_kobo_vault import KoboVaultClient


PUBLIC = (
    ("catalog_search", "kobo.catalog.read"),
    ("library_search", "kobo.library.read"),
    ("library_manifest", "kobo.library.read"),
    ("acquisition_info", "kobo.acquisition.read"),
    ("acquisition_drm_metadata", "kobo.acquisition.read"),
    ("acquisition_display_profiles", "kobo.acquisition.read"),
    ("acquisition_acsm_export", "kobo.acquisition.read"),
    ("acquisition_library_sync", "kobo.acquisition.read"),
    ("acquisition_delegation", "kobo.acquisition.delegate"),
)
DEFAULT_SCOPES = frozenset({
    "kobo.catalog.read", "kobo.library.read", "kobo.acquisition.read",
})
GRANTED_SCOPES = DEFAULT_SCOPES | {"kobo.acquisition.delegate"}


def _session(scopes, *, stored_credentials=True):
    calls = []

    def handler(request):
        auth = request.headers.get("Authorization")
        calls.append((request.method, request.url.path, bool(auth)))
        if request.method == "GET" and request.url.path == "/api/v2/status":
            # KVault uses optionalApiClient() and hasApiScope() to filter
            # capabilities, but capability_details advertises ALL features.
            present = (
                [feature for feature, scope in PUBLIC]
                if auth is None else
                [feature for feature, scope in PUBLIC if scope in scopes]
            )
            return httpx.Response(200, json={
                "ok": True, "api_version": "2.3",
                "contract_revision": "2.3.3",
                "acquisition_schema_version": "1.4",
                "service": "kobo-vault", "role": "kobo-library-provider",
                "capabilities": present,
                "capability_details": [
                    {"id": feature, "scope": scope, "available": True,
                     "access": "authenticated"}
                    for feature, scope in PUBLIC
                ],
                "acquisition_access": (
                    "unknown" if auth is None
                    else "configured" if stored_credentials
                    else "not_configured"
                ),
            })
        if request.method == "GET" and request.url.path == "/api/v2/account":
            assert auth == "Bearer kvt_synthetic_only"
            return httpx.Response(200, json={
                "account": {"id": "account-A", "display_name": "Fixture"}
            })
        if request.method == "POST" and request.url.path == (
            "/api/v2/library/edition-A/acquisition"
        ):
            assert auth == "Bearer kvt_synthetic_only"
            return httpx.Response(200, json={"acquisition": {"mode": "delegated"}})
        raise AssertionError("Unexpected HTTP call")

    session = KoboSession(
        descriptor=DESCRIPTOR, credential_id="credential-A", label="Kobo",
        credentials={"api_token": "kvt_synthetic_only"},
        options={"base_url": "https://kvault.invalid"},
        config={}, storage={}, diagnostics=NS(add=lambda *_a, **_k: None),
        runtime_capabilities=NS(acquisition=lambda _id: NS(
            available=True, compatible=True, operational=True,
            modes=("adobe_adept", "kobo_kdrm"),
        )),
    )
    session.client.close()
    session.client = KoboVaultClient(
        "https://kvault.invalid", "kvt_synthetic_only",
        transport=httpx.MockTransport(handler),
    )
    session.connect()
    return session, calls


@pytest.mark.parametrize(
    "scopes,stored_credentials,allowed",
    [
        (DEFAULT_SCOPES, True, False),
        (GRANTED_SCOPES, True, True),
        (GRANTED_SCOPES, False, False),
    ],
)
def test_real_v233_caps_allow_delegate_only_when_token_and_upstream_are_ready(
    scopes, stored_credentials, allowed,
):
    session, calls = _session(scopes, stored_credentials=stored_credentials)
    try:
        assert calls == [
            ("GET", "/api/v2/status", False),
            ("GET", "/api/v2/account", True),
            ("GET", "/api/v2/status", True),
        ]
        assert "acquisition_info" in session.capability_names
        assert "acquisition" not in session.capability_names
        # Actual v2 naming must not disable legacy ACSM/profile routing.
        assert session.supports_acquisition_acsm is True
        assert session.supports_acquisition_display_profile is True
        assert session.supports_acquisition_delegation is allowed
        assert session.diagnostic_state()["delegation_available"] is allowed
        assert session.acquisition_access == (
            "configured" if stored_credentials else "not_configured"
        )
        if allowed:
            session.resolve_acquisition("edition-A", mode="delegated")
            assert calls[-1] == (
                "POST", "/api/v2/library/edition-A/acquisition", True,
            )
        else:
            with pytest.raises(ProviderCapabilityError, match="missing_delegation_scope"):
                session.resolve_acquisition("edition-A", mode="delegated")
            assert not any(method == "POST" for method, *_ in calls)
    finally:
        session.close()


def test_public_status_is_not_bearer_permission_even_when_all_caps_advertised():
    session, calls = _session(DEFAULT_SCOPES)
    try:
        # Production KVault's anonymous v2Status() *does* advertise the
        # delegation capability. It is only the authenticated copy that
        # must filter it out for a default-scoped bearer.
        assert "acquisition_delegation" in session.capability_details
        assert "acquisition_delegation" in session.capability_names
        assert session.supports_acquisition_delegation is False
        assert calls[-1] == ("GET", "/api/v2/status", True)
    finally:
        session.close()
