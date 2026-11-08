"""La Rochelle tenant 1409 — fail-closed provider foundation (#3691).

The observed CAS-like browser trace is protocol evidence, NOT an authenticated
session transport contract. No network or implicit browser access is attempted.
"""
from __future__ import annotations

from typing import Any
import json
import logging
from threading import RLock
from urllib.parse import urlsplit

from cifereca.plugin_api import (
    ProviderCapabilities, ProviderCapabilityError, ProviderConfigurationError,
    ProviderDescriptor, ProviderPlugin, ProviderSession, CredentialField,
    AccountOptionField,
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
    # Ready for account configuration and an explicit diagnostic GET only.
    # No login, catalog, loans or acquisition capabilities are published.
    state="ready",
    capabilities=ProviderCapabilities(),
    credential_fields=(
        CredentialField("username", "Identifiant La Rochelle", required=True),
        CredentialField("password", "Mot de passe La Rochelle", secret=True, required=True),
    ),
    account_options=(
        AccountOptionField(
            "experimental_http_probe", "Recette HTTP expérimentale", default="off",
            help_text="off = aucun réseau, on = GET seul ; post = POST AJAX unique "
                      "explicitement consenti avec TOUS les paramètres fournis. "
                      "Aucune connexion Core ni synchronisation de prêt."),
        AccountOptionField("ajax_change_user", "POST AJAX : changeUser (valeur observée)"),
        AccountOptionField("ajax_provider", "POST AJAX : provider (valeur observée)"),
        AccountOptionField("ajax_dc", "POST AJAX : _dc (valeur fraîche)", secret=True),
        AccountOptionField("ajax_remember_me", "POST AJAX : rememberMe (valeur observée)"),
        AccountOptionField("ajax_captcha", "POST AJAX : captcha (valeur observée)", secret=True),
        AccountOptionField("ajax_captcha_check_value", "POST AJAX : captchaCheckValue (valeur fraîche)", secret=True),
    ),
    homepage="https://www.biblioaccess.com/1409/",
    # Suppress the automatic startup sweep until direct HTTP CAS is qualified.
    # This flag does not authorize an interactive browser in the provider.
    interactive_authentication=True,
    notes=(
        "Recette HTTP GET avec on ; POST AJAX et CAS avec post, paramètres explicites.",
        "Aucune synchronisation de prêts ni acquisition activée ; résultat jamais connecté.",
    ),
)


_LOG = logging.getLogger("cifereca.plugin.biblioaccess")

# Allowlisted technical outcomes only; no raw exception/URL/auth fields
# can be written to journal even when a dependency raises an odd ValueError.
_HTTP_PROBE_CODES = frozenset((
    "cas_url_refused", "cas_body_limit", "cas_client_closed",
    "cas_entry_already_attempted", "cas_entry_unverified",
    "cas_entry_redirect_refused", "cas_service_refused",
    "cas_login_unavailable", "cas_login_unverified",
    "cas_transport_unavailable", "cas_preflight_unavailable",
    "cas_post_not_confirmed", "cas_already_attempted",
    "cas_ticket_unverified", "cas_ticket_refused",
    "cas_landing_unverified", "cas_loans_already_attempted",
    "cas_landing_required", "cas_loans_unverified",
    "ajax_logon_consent_required", "ajax_logon_shape_mismatch",
    "ajax_logon_credentials_missing", "ajax_logon_challenge_missing",
    "ajax_logon_response_invalid", "ajax_logon_response_type",
    "ajax_logon_json_invalid", "ajax_logon_json_shape",
    "ajax_logon_response_limit", "ajax_logon_outcome_unknown",
    "ajax_logon_client_closed", "ajax_logon_already_attempted",
    "login_preflight_limit", "login_preflight_invalid_html",
    "login_preflight_url_refused",
))


class LaRochelleSession(ProviderSession):
    """Opt-in network diagnostic, deliberately not an authenticated session."""

    def __init__(self, *, diagnostics=None, client_factory=None, **kwargs: Any) -> None:
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        self._diagnostics = diagnostics
        self._client_factory = client_factory
        self._lock = RLock()
        self._closed = False
        self._attempted = False
        self._last_probe_phase = "not_attempted"

    def _event(self, phase: str, status: str, **facts: Any) -> None:
        # Structured facts are included in the canonical raw journal.
        # No new raw auth request or response dumps are introduced here.
        detail = {
            "provider": PROVIDER_ID, "tenant": TENANT_ID,
            "credential_id": self.credential_id,
            "phase": phase, "probe": "experimental_http",
            "connected": False, **facts,
        }
        try:
            if self._diagnostics is not None:
                self._diagnostics.add("API", "La Rochelle HTTP probe", status, detail=detail)
            else:
                _LOG.info(
                    "DIAG_JSON %s", json.dumps({
                        "schema_version": 1, "category": "API",
                        "operation": "La Rochelle HTTP probe",
                        "status": status, "detail": detail,
                    }, ensure_ascii=False),
                )
        except Exception:
            # Diagnostic failures are not authorization to proceed with login.
            _LOG.exception("biblioaccess_probe_diagnostic_sink_failed")

    def _post_payload(self):
        """No default/derived AJAX fields: require full observed input."""
        from biblioaccess_ajax_logon import _validate_params, AjaxLogonError
        keys = (
            "ajax_change_user", "ajax_provider", "ajax_dc",
            "ajax_remember_me", "ajax_captcha", "ajax_captcha_check_value",
        )
        if any(key not in self.options for key in keys):
            raise ProviderConfigurationError("biblioaccess_post_inputs_required")
        query = {
            "changeUser": self.options["ajax_change_user"],
            "provider": self.options["ajax_provider"],
            "_dc": self.options["ajax_dc"],
        }
        fields = {
            "username": self.credentials.get("username"),
            "password": self.credentials.get("password"),
            "rememberMe": self.options["ajax_remember_me"],
            "captcha": self.options["ajax_captcha"],
            "captchaCheckValue": self.options["ajax_captcha_check_value"],
        }
        try:
            _validate_params(query, fields)
        except AjaxLogonError as exc:
            raise ProviderConfigurationError(str(exc)) from None
        return query, fields

    def connect(self):
        with self._lock:
            self.connected = False
            self._accounts = []
            if self._closed:
                raise ProviderConfigurationError("biblioaccess_probe_session_closed")
            mode = self.options.get("experimental_http_probe")
            if mode not in ("on", "post"):
                raise ProviderCapabilityError("biblioaccess_probe_opt_in_required")
            # Validate all POST inputs BEFORE ANY remote GET. None are invented.
            query, fields = self._post_payload() if mode == "post" else (None, None)
            if self._attempted:
                raise ProviderCapabilityError("biblioaccess_probe_already_attempted")
            self._attempted = True
            self._last_probe_phase = "started"
            # An explicitly requested POST remains a diagnostic, NEVER a successful connect().
            # Do not require actual password values for this pre-auth probe.
            client = None
            try:
                if self._client_factory is None:
                    from biblioaccess_ajax_cas import ObservedAjaxCasClient
                    client = ObservedAjaxCasClient()
                else:
                    client = self._client_factory()
                self._event("entry", "started")
                entry = client.observe_entry_once()
                self._last_probe_phase = entry.phase
                self._event("entry", "observed", response_status=entry.status,
                            observation=entry.phase)
                self._event("institution_login", "started")
                login = client.observe_login_once()
                self._last_probe_phase = login.phase
                self._event("institution_login", "observed", response_status=login.status,
                            observation=login.phase)
                preflight = client.login_preflight()
                self._event(
                    "login_preflight", "observed",
                    script_count=preflight.script_count,
                    same_origin_script_paths=list(preflight.same_origin_script_paths),
                    external_script_count=preflight.external_script_count,
                    field_names=list(preflight.field_names),
                    inline_identifier_names=list(preflight.inline_identifier_names),
                    challenge_resolved=False,
                )
                if mode == "post":
                    # Exactly one explicitly consented credential POST on this
                    # client. The query/fields are ephemeral, never journaled.
                    self._event("ajax_logon", "started", request_method="POST")
                    answer = client.submit_once(query=query, fields=fields, consent=True)
                    self._last_probe_phase = "ajax_" + answer.outcome
                    self._event("ajax_logon", "observed",
                                response_status=answer.status, outcome=answer.outcome,
                                cas_completed=False, identity_verified=False)
                    if answer.outcome == "success_reported":
                        self._event("cas_return", "started", request_method="GET")
                        cas = client.observe_cas_once()
                        self._last_probe_phase = cas.phase
                        self._event("cas_return", "observed",
                                    response_status=cas.status, observation=cas.phase)
                        self._event("loans_page", "started", request_method="GET")
                        loans = client.observe_loans_once()
                        self._last_probe_phase = loans.phase
                        self._event("loans_page", "observed",
                                    response_status=loans.status, observation=loans.phase,
                                    identity_verified=False, loans_verified=False)
            except Exception as exc:
                # The provider remains disconnected even if the login HTML
                # cannot be parsed; preserve the exact technical step/code.
                from biblioaccess_ajax_cas import AjaxCasError
                from biblioaccess_ajax_logon import AjaxLogonError
                from biblioaccess_ajax_preflight import LoginPreflightError
                code = str(exc) if isinstance(
                    exc, (AjaxCasError, AjaxLogonError, LoginPreflightError)) else ""
                if code not in _HTTP_PROBE_CODES:
                    code = "biblioaccess_probe_http_failure"
                self._last_probe_phase = code
                self._event("preflight", "error", error_code=code,
                            error_type=type(exc).__name__)
                raise ProviderConfigurationError(code) from None
            finally:
                if client is not None:
                    client.close()
            if mode == "post":
                raise ProviderCapabilityError("biblioaccess_probe_completed_identity_unverified")
            raise ProviderCapabilityError("biblioaccess_probe_completed_login_not_attempted")

    def diagnostic_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                **super().diagnostic_state(),
                "probe_phase": self._last_probe_phase,
                "probe_attempted": self._attempted,
            }

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self.connected = False
            self._accounts = []
            self.credentials.clear()

 
class LaRochellePlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def create_session(
        self, *, credential_id: str, label: str, credentials: dict[str, Any],
        options: dict[str, Any], config: Any, storage: Any, diagnostics: Any,
    ) -> ProviderSession:
        # Account configuration is retained in the per-credential session;
        # authentication remains disabled pending a qualified AJAX flow.
        return LaRochelleSession(
            credential_id=credential_id, label=label,
            credentials=credentials, options=options, diagnostics=diagnostics,
        )


PLUGIN = LaRochellePlugin()
