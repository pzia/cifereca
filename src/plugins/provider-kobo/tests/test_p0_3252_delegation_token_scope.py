"""P0 #3252: delegation is not authorized by public Vault capabilities alone.

All bearer/status/account responses and acquisition POSTs are in-memory fakes.
No Kobo account, remote status endpoint, real token or DRM runtime is used.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_kobo import DESCRIPTOR, KoboSession, _authenticated_delegate_scope
from cifereca_kobo_vault import KoboVaultError


class Vault:
    base_url = "https://vault.invalid"
    def __init__(self, *, public=True, scope=False, scopes=None, introspection=True):
        self.token = "synthetic-bearer-A"
        self.public = public
        self.scope = scope
        self.scopes = scopes
        self.introspection = introspection
        self.calls = []
        self.denied_modes = set()

    def status(self, *, authenticated=False):
        self.calls.append(("status", authenticated))
        # pzia/KVault/src/worker.js v2Status: public lists *all* server
        # capabilities; bearer status lists only those permitted by scopes.
        capabilities = (
            ["acquisition_info", "acquisition_display_profiles",
             "acquisition_delegation"] if self.public
            else ["acquisition_info", "acquisition_display_profiles"]
        )
        result = {
            "api_version": "2.3", "contract_revision": "2.3.3",
            "service": "kobo-vault", "capabilities": capabilities,
            "acquisition_access": "configured" if authenticated else "unknown",
        }
        if authenticated:
            if not self.introspection:
                raise KoboVaultError("synthetic unavailable", code="http_error", status_code=404)
            if not self.scope:
                result["capabilities"] = [
                    item for item in capabilities
                    if item != "acquisition_delegation"
                ]
            if self.scopes is not None:
                result["token_scopes"] = self.scopes
        return result

    def authenticated_capabilities(self):
        return self.status(authenticated=True)

    def account(self):
        self.calls.append(("account", True))
        return {"id": "reader-A", "display_name": "Synthetic reader"}

    def acquisition(self, product_id, **kwargs):
        mode = kwargs["mode"]
        self.calls.append(("POST", mode))
        if mode in self.denied_modes:
            raise KoboVaultError("synthetic denied", code="forbidden", status_code=403)
        return {"acquisition": {"mode": mode}}

    def close(self):
        pass


def _connect(vault, *, credential="credential-A", runtime=True):
    session = KoboSession(
        descriptor=DESCRIPTOR, credential_id=credential, label="Kobo",
        credentials={"api_token": "synthetic-bearer-A"},
        options={"base_url": "https://vault.invalid"},
        config={}, storage={}, diagnostics=NS(add=lambda *_a, **_kw: None),
        runtime_capabilities=NS(acquisition=lambda _p: NS(
            available=runtime, compatible=True, operational=True,
            modes=("kobo_kdrm",),
        )),
    )
    session.client.close()
    session.client = vault
    session.connect()
    return session


@pytest.mark.parametrize("public,scope,runtime,allowed", [
    (True, False, True, False), (False, True, True, False),
    (True, True, False, False), (True, True, True, True),
])
def test_delegate_requires_public_capability_bearer_scope_and_runtime(
    public, scope, runtime, allowed,
):
    vault = Vault(public=public, scope=scope)
    session = _connect(vault, runtime=runtime)
    try:
        assert vault.calls[:3] == [
            ("status", False), ("account", True), ("status", True),
        ]
        assert session.supports_acquisition_delegation is allowed
        assert session.diagnostic_state()["delegation_available"] is allowed
        if allowed:
            assert session.resolve_acquisition("edition", mode="delegated")["acquisition"]["mode"] == "delegated"
            assert ("POST", "delegated") in vault.calls
        else:
            with pytest.raises(ProviderCapabilityError, match="missing_delegation_scope"):
                session.resolve_acquisition("edition", mode="delegated")
            assert ("POST", "delegated") not in vault.calls
            # The independently admitted descriptor remains callable.
            assert session.resolve_acquisition("edition", mode="descriptor")["acquisition"]["mode"] == "descriptor"
    finally:
        session.close()


@pytest.mark.parametrize("scopes", [
    [], ["catalog.read"], ["kobo.acquisition.delegate", 123],
    "kobo.acquisition.delegate", True, ["kobo.acquisition.delegated"],
])
def test_invalid_or_unrelated_bearer_claims_never_trigger_delegated_post(scopes):
    vault = Vault(scope=True, scopes=scopes)
    session = _connect(vault)
    try:
        with pytest.raises(ProviderCapabilityError, match="missing_delegation_scope"):
            session.resolve_acquisition("edition", mode="delegated")
        assert ("POST", "delegated") not in vault.calls
    finally:
        session.close()


def test_authenticated_status_failure_does_not_break_descriptor_or_grant_delegation():
    vault = Vault(scope=True, introspection=False)
    session = _connect(vault)
    try:
        assert session.supports_acquisition_delegation is False
        with pytest.raises(ProviderCapabilityError, match="missing_delegation_scope"):
            session.resolve_acquisition("edition", mode="delegated")
        session.resolve_acquisition("edition", mode="descriptor")
        assert ("POST", "delegated") not in vault.calls
    finally:
        session.close()


def test_reconnected_same_account_with_new_bearer_rechecks_scope_and_clears_old_403():
    vault = Vault(scope=False)
    session = _connect(vault)
    try:
        with pytest.raises(ProviderCapabilityError, match="missing_delegation_scope"):
            session.resolve_acquisition("edition", mode="delegated")
        session._cifereca_delegation_forbidden = True
        vault.token = "new-synthetic-bearer"
        vault.scope = True
        session.connect()
        assert session.supports_acquisition_delegation is True
        assert session._cifereca_delegation_forbidden is False
        session.resolve_acquisition("edition", mode="delegated")
        assert vault.calls.count(("POST", "delegated")) == 1

        # A token swap without a NEW authenticated handshake is not admitted.
        vault.token = "changed-without-authenticated-status"
        with pytest.raises(ProviderCapabilityError, match="missing_delegation_scope"):
            session.resolve_acquisition("edition", mode="delegated")
        assert vault.calls.count(("POST", "delegated")) == 1
    finally:
        session.close()


def test_descriptor_403_does_not_poison_delegated_mode():
    vault = Vault(scope=True)
    session = _connect(vault)
    try:
        vault.denied_modes.add("descriptor")
        with pytest.raises(KoboVaultError):
            session.resolve_acquisition("edition", mode="descriptor")
        assert session._cifereca_delegation_forbidden is False
        session.resolve_acquisition("edition", mode="delegated")
        assert vault.calls.count(("POST", "delegated")) == 1
    finally:
        session.close()


def test_separate_credentials_do_not_share_delegation_attestation():
    denied = Vault(scope=False)
    allowed = Vault(scope=True)
    sessions = [
        _connect(denied, credential="credential-A"),
        _connect(allowed, credential="credential-B"),
    ]
    try:
        with pytest.raises(ProviderCapabilityError):
            sessions[0].resolve_acquisition("edition", mode="delegated")
        sessions[1].resolve_acquisition("edition", mode="delegated")
        assert ("POST", "delegated") not in denied.calls
        assert allowed.calls.count(("POST", "delegated")) == 1
    finally:
        for session in sessions:
            session.close()


def test_public_and_authenticated_status_use_distinct_http_bearer_headers():
    """The status flag is a transport contract, not merely a mock argument."""
    import httpx
    from cifereca_kobo_vault import KoboVaultClient

    observed = []
    def respond(request):
        observed.append((request.method, request.url.path,
                         request.headers.get("Authorization")))
        payload = {
            "api_version": "2.3", "contract_revision": "2.3.3",
            "service": "kobo-vault",
            "capabilities": ["acquisition_info", "acquisition_delegation"],
            "acquisition_access": (
                "configured" if request.headers.get("Authorization")
                else "unknown"
            ),
        }
        return httpx.Response(200, json=payload)

    client = KoboVaultClient(
        "https://vault.invalid", "test-bearer",
        transport=httpx.MockTransport(respond),
    )
    try:
        public = client.status(authenticated=False)
        authenticated = client.authenticated_capabilities()
        # The anonymous status advertises acquisition_delegation too.
        # It MUST NOT be used as evidence: connect() only passes the
        # separately bearer-authenticated status to the scope qualifier.
        assert "acquisition_delegation" in public["capabilities"]
        assert _authenticated_delegate_scope(public) is False
        assert _authenticated_delegate_scope(authenticated)
        assert observed == [
            ("GET", "/api/v2/status", None),
            ("GET", "/api/v2/status", "Bearer test-bearer"),
        ]
    finally:
        client.close()
