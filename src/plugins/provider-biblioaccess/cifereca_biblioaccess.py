"""La Rochelle tenant 1409 — fail-closed provider foundation (#3691).

The observed CAS-like browser trace is protocol evidence, NOT an authenticated
session transport contract. No network or implicit browser access is attempted.
"""
from __future__ import annotations

from typing import Any
from pathlib import Path
from secrets import token_hex
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import logging
from threading import RLock
from urllib.parse import urlsplit

from cifereca.plugin_api import (
    ProviderCapabilities, ProviderCapabilityError, ProviderConfigurationError,
    ProviderDescriptor, ProviderPlugin, ProviderSession, CredentialField,
    AccountOptionField, Account, Loan,
    LoanInventoryScope, LoanInventorySnapshot, LoanBranchCoverage,
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
    display_name="Médiathèques de La Rochelle",
    description="Compte lecteur : synchronisation des prêts et téléchargement ADEPT autorisé.",
    # Live account, positive loans and opt-in automatic ACSM acquisition.
    state="ready",
    capabilities=ProviderCapabilities(
        authentication=True, multiple_credentials=True, loans=True,
        digital_download=True, opening_loan_auto_acquisition=True,
    ),
    credential_fields=(
        CredentialField("username", "Identifiant de bibliothèque", required=True),
        CredentialField("password", "Mot de passe", secret=True, required=True),
    ),
    # Only normal account controls belong in Settings. Previously saved
    # experimental_http_probe, AJAX and JS options remain in per-account
    # configuration and are recognized by the diagnostic implementation, but
    # are intentionally not re-declared as editable production account fields.
    account_options=(
        AccountOptionField(
            "authenticated_sync", "Synchronisation des prêts (on/off)", default="off",
            help_text="on : autorise la connexion SSO, la synchronisation de "
                      "vos prêts numériques et l'acquisition automatique ACSM "
                      "des prêts disponibles via votre profil Kobo-Adept. "
                      "off : aucun SSO ni téléchargement automatique. "
                      "Vous pouvez retélécharger un prêt au besoin.",
        ),
    ),
    homepage="https://www.biblioaccess.com/1409/",
    # Interactive sources require exact per-account authenticated_sync consent.
    interactive_authentication=True,
    background_authentication_option_key="authenticated_sync",
    notes=(
        "Prêts numériques positifs, inventaire partiel, sans clôture par absence.",
        "authenticated_sync=on requis pour le SSO HTTP en arrière-plan.",
        "Téléchargement ACSM des prêts actifs ; fulfillment par Kobo-Adept Core.",
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
    "cas_script_consent_required", "cas_script_already_attempted",
    "cas_script_unobserved", "cas_script_unavailable",
    "cas_script_unexpected_content", "cas_script_transport_unavailable",
    "cas_login_consent_required", "cas_login_dynamic_inputs_unavailable",
    "cas_post_not_confirmed", "cas_already_attempted",
    "cas_ticket_unverified", "cas_ticket_refused",
    "cas_landing_unverified", "cas_loans_already_attempted",
    "cas_landing_required", "cas_loans_unverified",
    "cas_loans_structure_unverified", "cas_authenticated_page_unverified",
    "biblioaccess_authenticated_page_unverified",
    "ajax_logon_consent_required", "ajax_logon_shape_mismatch",
    "ajax_logon_credentials_missing", "ajax_logon_challenge_missing",
    "ajax_logon_response_invalid", "ajax_logon_response_type",
    "ajax_logon_json_invalid", "ajax_logon_json_shape",
    "ajax_logon_response_limit", "ajax_logon_outcome_unknown",
    "ajax_logon_client_closed", "ajax_logon_already_attempted",
    "login_preflight_limit", "login_preflight_invalid_html",
    "login_preflight_url_refused",
))


_CATALOGUE_PROBE_CODES = frozenset((
    "catalogue_invalid_query", "catalogue_page_limit",
    "catalogue_client_closed", "catalogue_url_refused",
    "catalogue_pagination_refused", "catalogue_unexpected_content",
    "catalogue_body_limit", "catalogue_bad_encoding",
    "catalogue_auth_required", "catalogue_rate_limited",
    "catalogue_remote_failure", "catalogue_redirect_refused",
    "catalogue_page_unavailable", "catalogue_transport_unavailable",
    "catalogue_book_not_observed", "catalogue_book_id_refused",
    "catalogue_book_url_missing", "catalogue_book_unavailable",
))

def _observed_logon_script(paths: tuple[str, ...]) -> str:
    """Unique login script advertised by fresh institution HTML.

    Selection is structural only; the inspected JS is never executed or
    converted into dynamic POST fields. Do not guess a path absent from HTML.
    """
    if type(paths) is not tuple:
        return ""
    candidates = [
        path for path in paths
        if type(path) is str
        and (path.rsplit("/", 1)[-1] == "logon.js"
             or (path.rsplit("/", 1)[-1].startswith("logon.js.pagespeed.")
                 and path.endswith(".js")))
    ]
    return candidates[0] if len(candidates) == 1 else ""


class LaRochelleSession(ProviderSession):
    """Per-credential HTTP CAS session with read-only loan sync or explicit test."""

    def __init__(self, *, diagnostics=None, client_factory=None, **kwargs: Any) -> None:
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        self._diagnostics = diagnostics
        self._client_factory = client_factory
        self._lock = RLock()
        self._closed = False
        self._attempted = False
        self._last_probe_phase = "not_attempted"
        self._http = None
        self._sync_auth_live = False
        # A closed, provider-owned signal consumed only by an opt-in Core
        # source Sync after its *existing* session loses CAS authority.
        self.sync_reconnect_reason = None

    def _event(self, phase: str, status: str, **facts: Any) -> None:
        # Structured facts are included in the canonical raw journal.
        # No new raw auth request or response dumps are introduced here.
        detail = {
            "provider": PROVIDER_ID, "tenant": TENANT_ID,
            "credential_id": self.credential_id,
            "phase": phase, "probe": "experimental_http",
            "connected": False, **facts,
        }
        # Live inventory events have no need for a local account identifier.
        # It may be user-configured private text. The session scope is
        # verified in memory, not reconstructed from diagnostic payloads.
        if phase in {"loan_inventory", "acsm_stage"}:
            detail.pop("credential_id", None)
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
            # Diagnostic sink exceptions can contain signed links, credentials
            # or raw upstream data. Never log exception text or traceback.
            _LOG.warning("biblioaccess_probe_diagnostic_sink_failed")

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

    def _probe_public_catalogue(self):
        """Optional separate public session, strictly read-only and partial."""
        query = self.options.get("experimental_catalogue_query", "")
        if not query:
            return
        if (type(query) is not str or not 1 <= len(query.strip()) <= 120
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in query)):
            self._event("catalogue", "error", error_code="catalogue_invalid_query")
            return
        catalogue = None
        try:
            from biblioaccess_catalogue_http import (
                BiblioAccessCatalogueHTTP, CatalogueHTTPError,
            )
            catalogue = BiblioAccessCatalogueHTTP()
            self._event("catalogue_search", "started", request_method="GET")
            found = catalogue.search(query, max_pages=1)
            self._event("catalogue_search", "observed",
                        observation=found.status,
                        pages_observed=found.pages_observed,
                        book_ids_observed=len(found.book_ids),
                        completeness="partial", entitlements_verified=False)
            if found.book_ids:
                self._event("catalogue_detail", "started", request_method="GET")
                notice = catalogue.read_observed_notice(found.book_ids[0])
                self._event("catalogue_detail", "observed",
                            observation=("structured_book_unverified"
                                         if notice else "metadata_unknown"),
                            entitlements_verified=False)
        except Exception as exc:
            from biblioaccess_catalogue_http import CatalogueHTTPError
            code = str(exc) if isinstance(exc, CatalogueHTTPError) else ""
            if code not in _CATALOGUE_PROBE_CODES:
                code = "catalogue_probe_unavailable"
            self._event("catalogue", "error", error_code=code)
        finally:
            if catalogue is not None:
                catalogue.close()

    def connect(self):
        with self._lock:
            self.sync_reconnect_reason = None
            self.connected = False
            self._accounts = []
            if self._closed:
                raise ProviderConfigurationError("biblioaccess_probe_session_closed")
            mode = self.options.get("experimental_http_probe")
            sync_opt_in = self.options.get("authenticated_sync") == "on"
            if not sync_opt_in and mode not in ("on", "post", "auto-post"):
                raise ProviderCapabilityError("biblioaccess_probe_opt_in_required")
            # Reject malformed optional script selection before any HTTP.
            # The client separately checks exact membership in the freshly
            # observed HTML; no arbitrary third-party GET is allowed.
            script_path = (
                "" if sync_opt_in else
                self.options.get("experimental_login_script_path", "")
            )
            if (type(script_path) is not str or len(script_path) > 256
                    or (script_path and (
                        not script_path.startswith("/")
                        or script_path.startswith("//")
                        or "?" in script_path or "#" in script_path
                        or any(ord(ch) < 33 or ord(ch) > 126 for ch in script_path)
                    ))):
                raise ProviderConfigurationError("biblioaccess_script_path_invalid")
            # Validate all POST inputs BEFORE ANY remote GET. None are invented.
            query, fields = (
                self._post_payload()
                if mode == "post" and not sync_opt_in else (None, None)
            )
            if self._attempted:
                raise ProviderCapabilityError("biblioaccess_probe_already_attempted")
            self._attempted = True
            self._last_probe_phase = "started"
            # An explicitly requested POST remains a diagnostic, NEVER a successful connect().
            # Do not require actual password values for this pre-auth probe.
            # Independent public C read even if the institution SSO GET fails.
            if not sync_opt_in:
                self._probe_public_catalogue()
            client = None
            # Keep the in-flight stage separate from the last completed one.
            active_phase = "entry"
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
                active_phase = "institution_login"
                self._event("institution_login", "started")
                login = client.observe_login_once()
                self._last_probe_phase = login.phase
                self._event("institution_login", "observed", response_status=login.status,
                            observation=login.phase)
                active_phase = "login_preflight"
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
                # A second, independently selected GET is opt-in by exact
                # script path, and only on a previously observed IdP script.
                # Never log the script source, query, URL or response bytes.
                script_path = (
                "" if sync_opt_in else
                self.options.get("experimental_login_script_path", "")
            )
                selection = "configured" if script_path else ""
                if not script_path and mode == "on" and not sync_opt_in:
                    script_path = _observed_logon_script(
                        preflight.same_origin_script_paths)
                    if script_path:
                        selection = "auto"
                if script_path:
                    active_phase = "login_script"
                    self._event("login_script", "started",
                                request_method="GET", selection=selection)
                    evidence = client.inspect_login_script_once(
                        path=script_path, consent=True)
                    self._last_probe_phase = "script_structure_unverified"
                    self._event(
                        "login_script", "observed",
                        selection=selection,
                        identifier_names=list(evidence.identifier_names),
                        ajax_endpoint_referenced=evidence.ajax_endpoint_referenced,
                        executable=False, value_provenance_verified=False,
                        challenge_resolved=False,
                    )
                if mode in ("post", "auto-post") or sync_opt_in:
                    # Exactly one consented credential POST on this client.
                    # Background auth requires the separately persisted
                    # authenticated_sync=on; probe remains explicit UI only.
                    # The query/fields are ephemeral and never journaled.
                    active_phase = "ajax_logon"
                    self._event("ajax_logon", "started", request_method="POST")
                    if mode == "auto-post" or sync_opt_in:
                        answer = client.submit_observed_login_once(
                            username=self.credentials.get("username"),
                            password=self.credentials.get("password"), consent=True)
                    else:
                        answer = client.submit_once(query=query, fields=fields, consent=True)
                    self._last_probe_phase = "ajax_" + answer.outcome
                    self._event("ajax_logon", "observed",
                                response_status=answer.status, outcome=answer.outcome,
                                cas_completed=False, identity_verified=False,
                                reported_error_count=getattr(answer, "error_count", None),
                                response_message_present=getattr(answer, "message_present", False))
                    if answer.outcome == "success_reported":
                        active_phase = "cas_return"
                        self._event("cas_return", "started", request_method="GET")
                        cas = client.observe_cas_once()
                        self._last_probe_phase = cas.phase
                        self._event("cas_return", "observed",
                                    response_status=cas.status, observation=cas.phase)
                        active_phase = "loans_page"
                        self._event("loans_page", "started", request_method="GET")
                        loans = client.observe_loans_once()
                        self._last_probe_phase = loans.phase
                        self._event("loans_page", "observed",
                                    response_status=loans.status, observation=loans.phase,
                                    identity_verified=False, loans_verified=False,
                                    inventory_verified=False,
                                    page_book_link_count=getattr(loans, "page_book_link_count", 0),
                                    page_book_unique_count=getattr(loans, "page_book_unique_count", 0),
                                    page_loan_row_count=getattr(loans, "page_loan_row_count", 0),
                                    page_loan_row_partial_count=getattr(loans, "page_loan_row_partial_count", 0),
                                    logout_form_observed=getattr(loans, "logout_form_observed", False))
                        if sync_opt_in:
                            if not getattr(loans, "logout_form_observed", False):
                                raise ProviderConfigurationError(
                                    "biblioaccess_authenticated_page_unverified")
                            # The identity is credential-scoped, not a guessed
                            # remote borrower ID. The HTTPX jar has no imported
                            # browser cookies. All account scopes are local.
                            self._accounts = [Account(
                                display_name=self.label, unique_id="primary",
                                is_primary=True, provider_id=PROVIDER_ID,
                                credential_id=self.credential_id,
                                provider_label=DESCRIPTOR.display_name,
                                identity_id="default",
                            )]
                            self._http = client
                            self._sync_auth_live = True
                            self.connected = True
                            self._last_probe_phase = "authenticated_partial_inventory"
                            client = None  # session now owns the one HTTPX jar
            except Exception as exc:
                # The provider remains disconnected even if the login HTML
                # cannot be parsed; preserve the exact technical step/code.
                from biblioaccess_ajax_cas import AjaxCasError
                from biblioaccess_ajax_logon import AjaxLogonError
                from biblioaccess_ajax_preflight import LoginPreflightError
                code = str(exc) if isinstance(
                    exc, (AjaxCasError, AjaxLogonError, LoginPreflightError,
                          ProviderConfigurationError)) else ""
                if code not in _HTTP_PROBE_CODES:
                    code = "biblioaccess_probe_http_failure"
                self._last_probe_phase = code
                self._event(active_phase, "error", error_code=code,
                            error_type=type(exc).__name__)
                raise ProviderConfigurationError(code) from None
            finally:
                if client is not None:
                    client.close()
            if sync_opt_in and self.connected:
                return self.accounts
            if mode in ("post", "auto-post") or sync_opt_in:
                raise ProviderCapabilityError("biblioaccess_probe_completed_identity_unverified")
            raise ProviderCapabilityError("biblioaccess_probe_completed_login_not_attempted")

    def _validate_local_account(self, account: Account) -> None:
        if (not self.connected or not self._sync_auth_live
                or self._http is None or self._closed
                or type(account) is not Account
                or len(self._accounts) != 1 or account is not self._accounts[0]
                or account.provider_id != PROVIDER_ID
                or account.credential_id != self.credential_id
                or account.unique_id != "primary"):
            raise ProviderCapabilityError("biblioaccess_loan_account_unbound")

    def loan_inventory_snapshot(self, account: Account) -> LoanInventorySnapshot:
        """Fresh, positive-only digital loans from one live CAS cookie jar.

        No deleted/returned loan is inferred by absence, even on a 200 page
        with zero rows. A session lost mid-read is revoked before re-use.
        """
        with self._lock:
            self._validate_local_account(account)
            try:
                observation = self._http.read_authenticated_loans()
                if not observation.logout_form_observed:
                    raise ProviderCapabilityError(
                        "biblioaccess_authenticated_page_unverified")
                loan_rows = []
                for row in observation._loan_rows:
                    due_local = datetime.strptime(
                        row.due_at_local, "%d/%m/%Y %H:%M:%S")
                    # Institution locale France; keep the source string
                    # separately to avoid treating a naive timestamp as UTC.
                    due_at = due_local.replace(
                        tzinfo=ZoneInfo("Europe/Paris")).isoformat()
                    loan_rows.append(Loan(
                        id="biblioaccess:1409:" + row.product_ref,
                        provider="BIBLIOACCESS", medium="digital",
                        record_id=row.book_id, title=row.title,
                        due_at=due_at, source_provider=PROVIDER_ID,
                        source_credential=self.credential_id,
                        source_account_id=account.unique_id,
                        identity_id=account.identity_id,
                        can_return=False, renewable=False, ebook_url=None,
                        # The Core needs only positive offer presence. Do not
                        # publish a signed link or attest a downloadable book
                        # for a row with no ACSM action.
                        raw=({"acsm_offer_present": True}
                             if row.acsm_offer_present else {}),
                    ))
                snapshot = LoanInventorySnapshot(
                    scope=LoanInventoryScope.for_account(account),
                    loans=tuple(loan_rows), status="partial",
                    branches=(LoanBranchCoverage(
                        upstream="biblioaccess", medium="digital",
                        completeness="partial",
                        reason_code="inventory_not_exhaustive",
                    ),),
                    reason_code="inventory_not_exhaustive",
                )
                self._last_probe_phase = "loan_inventory_observed"
                self.sync_reconnect_reason = None
                # Count and coverage only. No borrower, book, title,
                # response body, signed URL or cookie leaves this method.
                self._event(
                    "loan_inventory", "observed",
                    probe="authenticated_sync", connected=True,
                    page_loan_row_count=len(loan_rows),
                    page_loan_row_partial_count=getattr(
                        observation, "page_loan_row_partial_count", 0),
                    completeness="partial", session_revoked=False,
                )
                return snapshot
            except Exception as exc:
                # The exact observed failure is useful for field diagnosis,
                # but a provider/HTTP exception is never safe to log verbatim.
                from biblioaccess_ajax_cas import AjaxCasError
                allowed = frozenset({
                    "cas_loans_unverified", "cas_loans_structure_unverified",
                    "cas_authenticated_page_unverified",
                    "cas_landing_required", "cas_client_closed",
                    "cas_transport_unavailable",
                    "biblioaccess_authenticated_page_unverified",
                })
                code = "biblioaccess_loan_inventory_unavailable"
                if type(exc) in (AjaxCasError, ProviderCapabilityError):
                    candidate = str(exc)
                    if candidate in allowed:
                        code = candidate
                self._last_probe_phase = "loan_inventory_failed"
                # Only an observed redirect/invalid authenticated MyLoans
                # document can request ONE new CAS session. A slow network,
                # malformed inventory or unknown error is not consent.
                self.sync_reconnect_reason = (
                    "session_expired"
                    if code in {
                        "cas_loans_unverified", "cas_landing_required",
                        "cas_authenticated_page_unverified",
                    } else None
                )
                self._event(
                    "loan_inventory", "error",
                    probe="authenticated_sync", connected=False,
                    error_code=code, session_revoked=True,
                )
                # An exception or expiry voids the account and cookie jar.
                # Never silently reuse stale loans or fall back to another
                # identity's source. The next reconnect makes a new client.
                self.connected = False
                self._sync_auth_live = False
                self._accounts = []
                http, self._http = self._http, None
                if http is not None:
                    try:
                        http.close()
                    except Exception:
                        pass
                raise ProviderCapabilityError(
                    "biblioaccess_loan_inventory_unavailable") from None

    def download_digital(self, loan: Loan, account: Account,
                         target_dir: Path) -> tuple[Path, str]:
        """Download a fresh signed ACSM offer for this exact live loan.

        Source URLs never leave the plugin: the second return value is empty.
        Core handles ADEPT fulfillment into an EPUB/PDF, post-download hooks,
        local publication, caching and force-refresh when requested.
        """
        with self._lock:
            self._validate_local_account(account)
            if (type(loan) is not Loan
                    or loan.source_provider != PROVIDER_ID
                    or loan.source_credential != self.credential_id
                    or loan.source_account_id != account.unique_id
                    or loan.identity_id != account.identity_id
                    or type(loan.id) is not str
                    or not loan.id.startswith("biblioaccess:1409:")):
                raise ProviderCapabilityError("biblioaccess_loan_account_unbound")
            product_ref = loan.id[len("biblioaccess:1409:"):]
            if not product_ref.isascii() or not product_ref.isdigit():
                raise ProviderCapabilityError("biblioaccess_loan_account_unbound")
            # Fresh MyLoans GET each time. No reuse prevention: request a
            # new signed link when redownloading the same active loan.
            try:
                observation = self._http.read_authenticated_loans()
            except Exception:
                self._last_probe_phase = "acsm_session_failed"
                self._event(
                    "acsm_stage", "error", probe="authenticated_sync",
                    connected=False, stage="myloans_refresh",
                    error_code="biblioaccess_acsm_session_unavailable",
                    session_revoked=True,
                )
                # Only a loss of the authenticated page revokes the session.
                # An ACSM delivery 403/429 may be retried with a new offer.
                self.connected = False
                self._sync_auth_live = False
                self._accounts = []
                client, self._http = self._http, None
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
                raise ProviderCapabilityError(
                    "biblioaccess_acsm_session_unavailable") from None
            matches = [
                row for row in observation._loan_rows
                if row.product_ref == product_ref
                and row.book_id == loan.record_id
                and row.acsm_offer_present
                and type(row.acsm_url) is str
            ]
            if len(matches) != 1:
                self._event(
                    "acsm_stage", "warning", probe="authenticated_sync",
                    connected=True, stage="offer_selection",
                    error_code="biblioaccess_acsm_offer_unavailable",
                    session_revoked=False,
                )
                raise ProviderCapabilityError(
                    "biblioaccess_acsm_offer_unavailable")
            from biblioaccess_acsm_delivery import (
                stage_loan_acsm, ACSMDeliveryError,
            )
            from biblioaccess_delivery_url import classify_loan_delivery
            kind = classify_loan_delivery(matches[0].acsm_url)
            if kind is None:
                raise ProviderCapabilityError("biblioaccess_acsm_offer_unavailable")
            # Random invocation-local correlation, not an account, title,
            # book identifier, persistent fingerprint or private signed URL.
            loan_ref = token_hex(8)
            self._event(
                "acsm_stage", "started", probe="authenticated_sync",
                connected=True, stage="signed_get",
                loan_ref=loan_ref, delivery_kind=kind,
                download_link_observed=True, session_revoked=False,
            )
            try:
                path = stage_loan_acsm(matches[0].acsm_url, target_dir)
            except Exception as exc:
                allowed = frozenset({
                    "biblioaccess_acsm_url_invalid",
                    "biblioaccess_acsm_destination_invalid",
                    "biblioaccess_acsm_unavailable",
                    "biblioaccess_acsm_redirect",
                    "biblioaccess_acsm_redirect_cycle",
                    "biblioaccess_acsm_redirect_refused",
                    "biblioaccess_acsm_redirect_limit",
                    "biblioaccess_acsm_denied",
                    "biblioaccess_acsm_missing",
                    "biblioaccess_acsm_rate_limited",
                    "biblioaccess_acsm_remote_error",
                    "biblioaccess_acsm_body_limit",
                    "biblioaccess_acsm_transport_unavailable",
                    "biblioaccess_acsm_structure_invalid",
                    "biblioaccess_acsm_content_type_invalid",
                    "biblioaccess_acsm_stage_unavailable",
                })
                code = (
                    exc.args[0] if type(exc) is ACSMDeliveryError
                    and len(exc.args) == 1 and type(exc.args[0]) is str
                    and exc.args[0] in allowed
                    else "biblioaccess_acsm_stage_unavailable"
                )
                self._last_probe_phase = "acsm_failed"
                self._event(
                    "acsm_stage", "error", probe="authenticated_sync",
                    connected=True, stage="signed_get", error_code=code,
                    loan_ref=loan_ref, delivery_kind=kind,
                    session_revoked=False,
                )
                # Preserve the historical Numilog compatibility error;
                # publish precise *allowlisted* failures for PNB /do, so the
                # Core can distinguish expired/denied/redirect/invalid XML.
                raise ProviderCapabilityError(
                    code if kind == "pnb_do"
                    else "biblioaccess_acsm_stage_unavailable"
                ) from None
            self._last_probe_phase = "acsm_staged"
            self._event(
                "acsm_stage", "observed", probe="authenticated_sync",
                connected=True, stage="staged", signed_url_logged=False,
                loan_ref=loan_ref, delivery_kind=kind,
            )
            # Never return the signed ACSM link to Core/receipts/hooks.
            return path, ""

    def loans(self, account: Account) -> list[Loan]:
        return list(self.loan_inventory_snapshot(account).loans)

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
            self._sync_auth_live = False
            http, self._http = self._http, None
            try:
                if http is not None:
                    http.close()
            finally:
                self.credentials.clear()

 
class LaRochellePlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def create_session(
        self, *, credential_id: str, label: str, credentials: dict[str, Any],
        options: dict[str, Any], config: Any, storage: Any, diagnostics: Any,
    ) -> ProviderSession:
        # Credentials are isolated per account. The exact persisted opt-in
        # decides whether HTTP SSO, loan sync and acquisition are available.
        return LaRochelleSession(
            credential_id=credential_id, label=label,
            credentials=credentials, options=options, diagnostics=diagnostics,
        )


PLUGIN = LaRochellePlugin()
