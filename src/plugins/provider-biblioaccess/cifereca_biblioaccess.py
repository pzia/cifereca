"""La Rochelle tenant 1409 — fail-closed provider foundation (#3691).

The observed CAS-like browser trace is protocol evidence, NOT an authenticated
session transport contract. No network or implicit browser access is attempted.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from cifereca.plugin_api import (
    ProviderCapabilities, ProviderCapabilityError, ProviderDescriptor,
    ProviderPlugin, ProviderSession,
)

PROVIDER_ID = "biblioaccess.la-rochelle"
TENANT_ID = "1409"
BIBLIO_HOST = "www.biblioaccess.com"
IDP_HOST = "mediatheques.agglo-larochelle.fr"


def classify_sso_transition(url: str, status: int) -> str:
    """Classify *synthetic* SSO routing; deliberately discard query and ticket.

    This is an offline structural validator, not a browser callback, an auth
    exchange, a cookie importer, or a login implementation.
    """
    if not isinstance(url, str) or not isinstance(status, int):
        return "unsupported"
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.username is not None
                or parts.password is not None or parts.port not in (None, 443)
                or parts.fragment or not parts.hostname):
            return "unsupported"
        host = parts.hostname.lower()
        path = parts.path
    except ValueError:
        return "unsupported"
    if host == BIBLIO_HOST:
        if path == f"/{TENANT_ID}/Account/Login" and status == 302:
            return "redirect_to_idp"
        if path in (f"/{TENANT_ID}", f"/{TENANT_ID}/") and status == 200:
            # A 200 page is NOT proof of a valid session or verified identity.
            return "landing_unverified"
        if path == f"/{TENANT_ID}/Catalog/MyLoans" and status == 200:
            return "loans_page_unverified"
    if host == IDP_HOST:
        if path == "/login" and status == 200:
            return "interactive_login_page"
        if path == "/cas/login" and status == 302:
            return "cas_redirect_unverified"
    return "unsupported"


DESCRIPTOR = ProviderDescriptor(
    id=PROVIDER_ID,
    display_name="La Rochelle — BiblioAccess",
    description="Tenant 1409, SSO institutionnel CAS-like, intégration en qualification.",
    state="skeleton",
    capabilities=ProviderCapabilities(),
    homepage="https://www.biblioaccess.com/1409/",
    # Suppress the automatic startup sweep until direct HTTP CAS is qualified.
    # This flag does not authorize an interactive browser in the provider.
    interactive_authentication=True,
    notes=(
        "SSO CAS institutionnel via futur client HTTP du provider ; POST /logon.svc/logon non qualifié.",
        "Aucune recherche, synchronisation de prêts ni acquisition disponible dans ce prototype.",
    ),
)


class LaRochelleSession(ProviderSession):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(descriptor=DESCRIPTOR, **kwargs)

    def connect(self):
        self.connected = False
        self._accounts = []
        raise ProviderCapabilityError(
            "biblioaccess_http_sso_logon_unqualified"
        )

    def close(self) -> None:
        self.connected = False
        self._accounts = []


class LaRochellePlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def create_session(
        self, *, credential_id: str, label: str, credentials: dict[str, Any],
        options: dict[str, Any], config: Any, storage: Any, diagnostics: Any,
    ) -> ProviderSession:
        # No storage, cookies, tickets, browser profile, or global UI profile.
        return LaRochelleSession(
            credential_id=credential_id, label=label,
            credentials={}, options={},
        )


PLUGIN = LaRochellePlugin()
