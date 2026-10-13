from __future__ import annotations

import inspect
import logging
import time
from pathlib import Path
from typing import Any

from ..acquisition import Acquisition
from ..acquisition_engine import AcquisitionOffer, AcquisitionPlanner, Artifact
from ..models import SearchResult
from ..identities import AdeptProfileResolutionError
from ..providers import ProviderConfigurationError
from ..plugin_api import UserFacingError, ProviderError
from .errors import KoboAdeptError
from .acquisition import KoboOfferAdapter
from .vault import KoboVaultError


_TRACE_LOG = logging.getLogger("cifereca.trace")


def execute_kobo_acquisition(service: Any, item: SearchResult, target_dir: Path) -> Acquisition:
    """Reconcile a failed Kobo attempt instead of leaving it in flight."""
    started: list[str] = []
    try:
        return _execute_kobo_acquisition_impl(service, item, target_dir, started=started)
    except Exception as exc:
        if started:
            ledger_id = started[-1]
            try:
                row = service.storage.acquisition_get(ledger_id)
                if row is not None and row.get("status") == "acquiring":
                    error_code = getattr(exc, "code", None)
                    if not isinstance(error_code, str) or not error_code.isidentifier():
                        error_code = "kobo_acquisition_failed"
                    retryable_hint = getattr(exc, "retryable", None)
                    # A 401/403 or a denied delegated scope needs an explicit
                    # permission change, not a blind retry of the same recipe.
                    non_retryable_permission = (
                        isinstance(exc, ProviderError)
                        and (
                            getattr(exc, "status_code", None) in {401, 403}
                            or getattr(exc, "code", None) in {
                                "missing_delegation_scope", "archived_book",
                            }
                        )
                    )
                    # Permission denials are terminal until explicit
                    # reauthorization, even when an upstream provider
                    # incorrectly marks the HTTP failure retryable.
                    retryable = (
                        False if non_retryable_permission
                        else retryable_hint if isinstance(retryable_hint, bool)
                        else not isinstance(exc, ProviderConfigurationError)
                    )
                    service.acquisition_ledger.fail(
                        ledger_id,
                        code=error_code,
                        message="Échec de l'acquisition Kobo ; consulter les diagnostics.",
                        retryable=retryable,
                    )
            except Exception:
                _TRACE_LOG.exception(
                    "TRACE kobo acquisition ledger reconciliation failed item_id=%s",
                    item.record_id,
                )
        raise


def _execute_kobo_acquisition_impl(
    service: Any, item: SearchResult, target_dir: Path, *, started: list[str],
) -> Acquisition:
    session = service._session_for_source(item.source_provider, item.source_credential)
    source_account_id = session.accounts[0].unique_id if len(session.accounts) == 1 else None
    identity = service.identity_for_connection(item.source_provider, item.source_credential)
    _TRACE_LOG.info(
        "TRACE kobo acquisition begin identity=%s credential=%s item_id=%s",
        identity.identity_id, item.source_credential, item.record_id,
    )
    resolver = getattr(session, "resolve_acquisition", None)
    if resolver is None:
        raise ProviderConfigurationError("Kobo : le provider ne publie pas l'interface d'acquisition Vault v2.x")
    adept_state = service.kobo_adept_state()
    if not (adept_state.get("available") and adept_state.get("compatible") and adept_state.get("operational")):
        raise ProviderConfigurationError("Le runtime Kobo-Adept n'est pas prêt pour l'acquisition")

    preferred = str(service.config.get("kobo_backup.format", "epub") or "epub").lower()
    adept_profile = ""
    adept_profile_source: str | None = None
    try:
        adept_profile, adept_profile_source = service.resolve_adept_profile(
            provider_id=item.source_provider, credential_id=item.source_credential, account_id=source_account_id,
        )
    except AdeptProfileResolutionError as exc:
        _TRACE_LOG.warning(
            "TRACE kobo adept profile-resolution-failed identity=%s credential=%s item_id=%s error=%s",
            identity.identity_id, item.source_credential, item.record_id, exc,
        )
        adept_profile = ""
    else:
        _TRACE_LOG.info(
            "TRACE kobo adept profile-resolved identity=%s credential=%s item_id=%s profile=%s source=%s",
            identity.identity_id, item.source_credential, item.record_id, adept_profile, adept_profile_source,
        )
    supports_profiles = bool(getattr(session, "supports_acquisition_display_profile", False))
    supports_acsm = bool(getattr(session, "supports_acquisition_acsm", False))
    if supports_profiles:
        try:
            signature = inspect.signature(resolver)
            supports_profiles = (
                "display_profile" in signature.parameters
                or any(param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
            )
            if supports_acsm:
                supports_acsm = (
                    "include_acsm" in signature.parameters
                    or any(param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
                )
        except (TypeError, ValueError):
            pass
    configured_profile = str(
        getattr(session, "display_profile", None)
        or service.config.get("kobo_backup.display_profile", "Android")
        or "Android"
    ).strip()
    if configured_profile not in {"Android", "Desktop"}:
        configured_profile = "Android"
    profiles: list[str | None] = [configured_profile] if supports_profiles else [None]
    if supports_profiles:
        alternate = "Desktop" if configured_profile == "Android" else "Android"
        if alternate not in profiles:
            profiles.append(alternate)

    resolved_cache: dict[tuple[str, str | None], dict[str, Any]] = {}

    def resolve(mode: str, profile: str | None, *, refresh: bool = False) -> dict[str, Any]:
        # This session belongs to one Kobo credential. A denied delegated
        # capability cannot become available for the next item in this run.
        # The provider clears the marker on an explicit reconnection.
        if mode == "delegated" and getattr(session, "_cifereca_delegation_forbidden", False):
            session._cifereca_delegation_attempts_saved = (
                getattr(session, "_cifereca_delegation_attempts_saved", 0) + 1
            )
            raise KoboVaultError(
                "Kobo-Vault : autorisation de délégation requise (scope kobo.acquisition.delegate)",
                code="missing_delegation_scope", status_code=403,
            )
        cache_key = (mode, profile)
        if not refresh and cache_key in resolved_cache:
            _TRACE_LOG.debug(
                "TRACE kobo vault resolve cache-hit identity=%s credential=%s item_id=%s mode=%s profile=%s",
                identity.identity_id, item.source_credential, item.record_id, mode, profile,
            )
            return resolved_cache[cache_key]
        kwargs: dict[str, Any] = {"revision_id": item.revision_id, "mode": mode}
        if supports_profiles and profile is not None:
            kwargs["display_profile"] = profile
        if supports_acsm:
            kwargs["include_acsm"] = True
        started = time.perf_counter()
        _TRACE_LOG.info(
            "TRACE kobo vault resolve begin identity=%s credential=%s item_id=%s mode=%s profile=%s include_acsm=%s refresh=%s",
            identity.identity_id, item.source_credential, item.record_id, mode, profile, kwargs.get("include_acsm"), refresh,
        )
        try:
            value = resolver(item.record_id, **kwargs)
        except Exception as exc:
            _TRACE_LOG.warning(
                "TRACE kobo vault resolve failed identity=%s credential=%s item_id=%s mode=%s profile=%s exception=%s status=%s code=%s dt=%.3fs error=%s",
                identity.identity_id, item.source_credential, item.record_id, mode, profile, type(exc).__name__,
                getattr(exc, "status_code", None), getattr(exc, "code", None), time.perf_counter() - started, exc,
            )
            # Core and the external provider use distinct KoboVaultError
            # classes. Normalize at the boundary without importing plugin
            # implementation types or exposing upstream response bodies.
            status = getattr(exc, "status_code", None)
            if isinstance(exc, ProviderError) and isinstance(status, int):
                if mode == "delegated" and status == 403:
                    session._cifereca_delegation_forbidden = True
                    raise KoboVaultError(
                        "Kobo-Vault : autorisation de délégation requise (scope kobo.acquisition.delegate)",
                        code="missing_delegation_scope", status_code=403,
                    ) from exc
                if not isinstance(exc, KoboVaultError):
                    safe_code = str(getattr(exc, "code", "") or "").strip()
                    if not safe_code.isidentifier():
                        safe_code = "provider_http_error"
                    raise KoboVaultError(
                        f"Kobo-Vault : erreur HTTP {status}",
                        code=safe_code, status_code=status,
                    ) from exc
            raise
        acquisition = value.get("acquisition") if isinstance(value, dict) else None
        candidates = acquisition.get("candidates") if isinstance(acquisition, dict) else None
        if not isinstance(candidates, list):
            candidates = value.get("candidates") if isinstance(value, dict) else None
        _TRACE_LOG.info(
            "TRACE kobo vault resolve end identity=%s credential=%s item_id=%s mode=%s profile=%s candidates=%s dt=%.3fs",
            identity.identity_id, item.source_credential, item.record_id, mode, profile,
            len(candidates) if isinstance(candidates, list) else None, time.perf_counter() - started,
        )
        resolved_cache[cache_key] = value
        return value

    def base_metadata(candidate_value: dict[str, Any], profile: str | None) -> tuple[dict[str, Any], str, str]:
        original_format = str(
            candidate_value.get("source_format")
            or service._kobo_candidate_media_format(candidate_value)
            or preferred
        ).lower()
        fmt = service._kobo_adept_format(service._kobo_candidate_media_format(candidate_value) or original_format)
        if fmt is None:
            raise ProviderConfigurationError(
                f"Kobo : format Vault non mappable vers Kobo-Adept ({original_format or 'inconnu'})"
            )
        metadata: dict[str, Any] = {
            "title": item.title,
            "authors": [x.strip() for x in str(item.author or "").split(",") if x.strip()],
            "isbn": item.metadata.get("isbn") if isinstance(item.metadata, dict) else None,
            "product_id": item.record_id,
            "revision_id": item.revision_id,
            "original_format": original_format,
            "vault_handling": service._kobo_candidate_handling(candidate_value),
        }
        if profile is not None:
            metadata["vault_display_profile"] = profile
        candidate_profile = service._kobo_candidate_profile(candidate_value)
        if candidate_profile is not None:
            metadata["candidate_display_profile"] = candidate_profile
        drm = candidate_value.get("drm")
        drm_family = drm.get("family") if isinstance(drm, dict) else candidate_value.get("drm_family")
        if drm_family is not None:
            metadata["vault_drm_family"] = drm_family
        candidate_source = service._kobo_candidate_source(candidate_value)
        if candidate_source is not None:
            metadata["vault_candidate_source"] = candidate_source
        return metadata, original_format, fmt

    def http_request_for(candidate_value: dict[str, Any], profile: str | None) -> tuple[dict[str, Any], Path]:
        handling = service._kobo_candidate_handling(candidate_value)
        if handling != "http_plain":
            raise ProviderConfigurationError(
                "Kobo : le transport HTTP direct est réservé aux candidats http_plain "
                f"(candidat reçu: {handling or 'unknown'})"
            )
        if not KoboOfferAdapter.secure_http_candidate(KoboOfferAdapter.normalize_candidate(candidate_value)):
            raise ProviderConfigurationError(
                "Kobo : candidat http_plain invalide (URL HTTPS et méthode GET requises)"
            )
        metadata, _original_format, fmt = base_metadata(candidate_value, profile)
        destination = service._kobo_destination(item, target_dir, fmt)
        headers = candidate_value.get("headers")
        drm_field = candidate_value.get("drm")
        if isinstance(drm_field, dict):
            drm_value = drm_field.get("raw_type") or drm_field.get("family")
        else:
            drm_value = candidate_value.get("drm_type") or drm_field or candidate_value.get("drm_family")
        request = {
            "schema_version": 2,
            "source": {
                "type": "http",
                "provider": "kobo",
                "drm": str(drm_value or "unknown"),
                "handling": "http_plain",
                "url": str(candidate_value.get("url") or ""),
                "headers": dict(headers) if isinstance(headers, dict) else {},
            },
            "destination": str(destination),
            "format": fmt,
            "drm": str(drm_value or "unknown"),
            "metadata": metadata,
            "options": {"force": False},
        }
        return request, destination

    def acsm_request_for(candidate_value: dict[str, Any], profile: str | None) -> tuple[dict[str, Any], Path]:
        normalized = KoboOfferAdapter.normalize_candidate(candidate_value)
        if (
            normalized.handling != "adobe_adept"
            or not (normalized.kind == "acsm" or normalized.media_format == "acsm")
            or not KoboOfferAdapter.secure_http_candidate(normalized)
        ):
            raise ProviderConfigurationError(
                "Kobo : candidat ACSM invalide (URL HTTPS et méthode GET requises)"
            )
        if not adept_profile:
            raise ProviderConfigurationError(
                "Kobo : un lien ACSM est disponible mais aucun profil ADEPT n'est configuré dans cifereca"
            )
        fmt = service._kobo_adept_format(getattr(item, "format", None)) or service._kobo_adept_format(preferred) or "epub"
        destination = service._kobo_destination(item, target_dir, fmt)
        headers = candidate_value.get("headers")
        metadata: dict[str, Any] = {
            "title": item.title,
            "authors": [x.strip() for x in str(item.author or "").split(",") if x.strip()],
            "isbn": item.metadata.get("isbn") if isinstance(item.metadata, dict) else None,
            "product_id": item.record_id,
            "revision_id": item.revision_id,
            "original_format": "acsm",
            "vault_handling": "adobe_adept",
            "vault_candidate_source": service._kobo_candidate_source(candidate_value) or "library_download_link",
        }
        if profile is not None:
            metadata["vault_display_profile"] = profile
        request = {
            "schema_version": 2,
            "source": {
                "type": "acsm",
                "provider": "kobo",
                "url": str(candidate_value.get("url") or ""),
                "headers": dict(headers) if isinstance(headers, dict) else {},
            },
            "destination": str(destination),
            "profile": adept_profile,
            "metadata": metadata,
            "options": {"force": False},
        }
        return request, destination

    def delegated_request_for(
        delegated_payload: dict[str, Any],
        candidate_value: dict[str, Any],
        profile: str | None,
    ) -> tuple[dict[str, Any], Path]:
        metadata, _original_format, fmt = base_metadata(candidate_value, profile)
        destination = service._kobo_destination(item, target_dir, fmt)
        recipe = service._kobo_delegated_request(delegated_payload, candidate_value)
        if recipe is None:
            raise ProviderConfigurationError(
                "Kobo-Vault : le mode delegated ne fournit pas de recette content_access_book exploitable"
            )
        # The caller-selected profile remains authoritative when Vault did not
        # already encode it in the delegated query.
        query = dict(recipe.get("query") or {}) if isinstance(recipe.get("query"), dict) else {}
        if profile is not None and "DisplayProfile" not in query:
            query["DisplayProfile"] = profile
        if query:
            recipe["query"] = query

        drm_value = service._kobo_candidate_drm(candidate_value)
        candidate_source = service._kobo_candidate_source(candidate_value)
        handling = service._kobo_candidate_handling(candidate_value)
        pinned_library_sync = bool(handling == "kobo_kdrm" and candidate_source == "library_sync")

        source: dict[str, Any] = {
            "type": "kobo",
            "provider": "kobo-vault" if pinned_library_sync else "kobo",
            "request": recipe,
        }

        if pinned_library_sync:
            # Kobo-Adept 0.13.2 / contract v8 additive capability: the URL
            # delivered by library_sync is pinned, while content_access_book
            # is replayed locally only to retrieve ContentKeys. Never flatten
            # this recipe to source.type=http: the payload is KDRM-protected.
            missing: list[str] = []
            if not adept_state.get("kobo_pinned_delivery_candidate"):
                missing.append("pinned_delivery_candidate")
            if not adept_state.get("kobo_separate_content_keys_replay"):
                missing.append("separate_content_keys_replay")
            if not adept_state.get("kobo_kdrm_available"):
                missing.append("KDRM")
            formats = {str(x).lower() for x in (adept_state.get("kobo_formats") or [])}
            if fmt not in formats:
                missing.append(f"format:{fmt}")
            if missing:
                raise ProviderConfigurationError(
                    "Kobo-Adept : le candidat library_sync/KDRM exige les capacités "
                    + ", ".join(missing)
                )

            kobo_meta = candidate_value.get("kobo") if isinstance(candidate_value.get("kobo"), dict) else {}
            raw_media = str(candidate_value.get("media_format") or "unknown").strip().lower() or "unknown"
            if raw_media not in {"epub", "kepub", "pdf", "unknown"}:
                raw_media = "unknown"
            pinned: dict[str, Any] = {
                "url": str(candidate_value.get("url") or ""),
                "headers": dict(candidate_value.get("headers")) if isinstance(candidate_value.get("headers"), dict) else {},
                "drm_type": drm_value,
                "media_format": raw_media,
                "source_format": str(candidate_value.get("source_format") or service._kobo_url_format(candidate_value) or ""),
                "delivery_source": candidate_source,
            }
            platform = str(kobo_meta.get("platform") or "").strip()
            if platform:
                pinned["platform"] = platform
            source["candidate"] = pinned
        else:
            selection: dict[str, Any] = {}
            if drm_value and drm_value != "unknown":
                selection["drm_type"] = drm_value
            url_format = service._kobo_url_format(candidate_value)
            if url_format:
                selection["url_format"] = url_format
            if selection:
                source["selection"] = selection

        if drm_value == "KDRM":
            context = service._kobo_kdrm_context(delegated_payload, candidate_value)
            if context is None:
                raise ProviderConfigurationError(
                    "Kobo-Vault : recette KDRM sans contexte device_id/user_id exploitable"
                )
            source["kdrm"] = context

        request = {
            "schema_version": 2,
            "source": source,
            "destination": str(destination),
            "format": fmt,
            "metadata": metadata,
            "options": {"force": False},
        }
        return request, destination

    # Selection policy 0.8.0 (inherited from validated 0.7.12 bridge):
    #   ACSM/ADEPT > SignedNoDrm/http_plain > KDRM/kobo_kdrm > legacy fallback.
    # Vault v2.3.3 keeps ``candidate.handling`` normative for transport. In
    # particular, an Adept v8 installation advertising Kobo delegated support
    # must never divert an ``http_plain`` candidate through source.type=kobo.
    # ACSM is used only when Vault publishes an explicit ACSM candidate, Adept
    # declares the ACSM capability, and Paris has an ADEPT profile.
    use_kobo_delegated = bool(adept_state.get("kobo_delegated_available"))
    prefer_acsm = bool(supports_acsm and adept_state.get("acsm_available") and adept_profile)
    payload: dict[str, Any] | None = None
    candidate: dict[str, Any] | None = None
    selected_profile: str | None = None
    request: dict[str, Any] | None = None
    destination: Path | None = None
    acquisition_mode = "descriptor-http"
    delegated_error: Exception | None = None
    descriptor_error: Exception | None = None
    delegation_forbidden = False

    # First ask for a descriptor so an autonomous ACSM fulfillment URL can be
    # used without exposing a Kobo bearer. If the ACSM itself needs Kobo auth,
    # resolve the same candidate in delegated mode and pass its headers on stdin.
    if prefer_acsm:
        # Display profiles can expose different delivery candidates. Before
        # falling back to plain HTTP, try the other profile for a usable ACSM.
        for profile in profiles:
            descriptor = resolve("descriptor", profile)
            acsm_candidate = service._kobo_acsm_candidate(descriptor)
            if acsm_candidate is None:
                continue
            if service._kobo_candidate_requires_authorization(acsm_candidate):
                try:
                    delegated_acsm = resolve("delegated", profile)
                    delegated_candidate = service._kobo_acsm_candidate(delegated_acsm)
                    if delegated_candidate is not None:
                        request, destination = acsm_request_for(delegated_candidate, profile)
                        payload = delegated_acsm
                        candidate = delegated_candidate
                        selected_profile = profile
                        acquisition_mode = "acsm-delegated"
                except KoboVaultError as exc:
                    if exc.status_code == 401:
                        raise
                    if exc.status_code == 403:
                        delegation_forbidden = True
                        delegated_error = exc
                    else:
                        raise
            else:
                request, destination = acsm_request_for(acsm_candidate, profile)
                payload = descriptor
                candidate = acsm_candidate
                selected_profile = profile
                acquisition_mode = "acsm-descriptor"
            if request is not None:
                break

    # Before asking Vault for a delegated Kobo recipe, inspect *all* display
    # profiles for the normative http_plain transport. A later Desktop
    # descriptor can expose a plain EPUB even when Android exposes only KDRM.
    # The global ACSM > plain HTTP > KDRM policy must not depend on profile order.
    if request is None:
        for profile in profiles:
            try:
                current_payload = resolve("descriptor", profile)
                current_candidate = service._kobo_candidate(current_payload, preferred)
            except KoboVaultError:
                raise
            except ProviderConfigurationError as exc:
                descriptor_error = exc
                continue
            if service._kobo_candidate_handling(current_candidate) != "http_plain":
                continue
            payload = current_payload
            candidate = current_candidate
            selected_profile = profile
            request, destination = http_request_for(current_candidate, profile)
            acquisition_mode = "descriptor-http"
            break

    # If no plain HTTP candidate was found, contract v8 may execute a true
    # Kobo KDRM content_access_book recipe locally. A delegated response can
    # still expose ``http_plain``; handling remains authoritative in that case.
    if request is None and use_kobo_delegated:
        for profile in profiles:
            try:
                delegated = resolve("delegated", profile)
                delegated_candidate = service._kobo_delegated_candidate(delegated, preferred)
                if service._kobo_candidate_handling(delegated_candidate) == "http_plain":
                    delegated_request, delegated_destination = http_request_for(delegated_candidate, profile)
                    delegated_mode = "delegated-http"
                else:
                    delegated_request, delegated_destination = delegated_request_for(delegated, delegated_candidate, profile)
                    delegated_mode = "delegated-kobo"
            except KoboVaultError as exc:
                if exc.status_code == 401:
                    raise
                if exc.status_code == 403:
                    delegation_forbidden = True
                    delegated_error = exc
                    break
                raise
            except ProviderConfigurationError as exc:
                if "library_sync/KDRM exige" in str(exc):
                    raise
                delegated_error = exc
                continue
            payload = delegated
            candidate = delegated_candidate
            selected_profile = profile
            request = delegated_request
            destination = delegated_destination
            acquisition_mode = delegated_mode
            break

    # Compatibility fallback when the local executor does not publish Kobo
    # delegated support. This preserves the historical direct HTTP behavior
    # for older/partial installations without weakening the v2.3.1 handling
    # rule above for supported v8 runtimes.
    if request is None:
        for profile in profiles:
            try:
                current_payload = resolve("descriptor", profile)
                current_candidate = service._kobo_candidate(current_payload, preferred)
            except KoboVaultError:
                raise
            except ProviderConfigurationError as exc:
                descriptor_error = exc
                continue
            if service._kobo_candidate_handling(current_candidate) != "http_plain":
                descriptor_error = ProviderConfigurationError(
                    "Kobo-Vault : le candidat descriptor n'est pas exécutable via HTTP direct "
                    f"({service._kobo_candidate_handling(current_candidate) or 'unknown'})"
                )
                continue
            payload = current_payload
            candidate = current_candidate
            selected_profile = profile
            request, destination = http_request_for(current_candidate, profile)
            acquisition_mode = "descriptor-http"
            break

    # Last-resort transport fallback for v8: if the delegated Kobo recipe is
    # unavailable but Vault still exposes a usable delegated HTTP candidate,
    # let Adept execute that source without Paris downloading it itself.
    if request is None:
        for profile in profiles:
            try:
                delegated = resolve("delegated", profile)
                current_candidate = service._kobo_candidate(delegated, preferred)
            except KoboVaultError as exc:
                if exc.status_code == 401:
                    raise
                if exc.status_code == 403:
                    delegation_forbidden = True
                    delegated_error = exc
                    break
                raise
            except ProviderConfigurationError as exc:
                delegated_error = exc
                continue
            if service._kobo_candidate_handling(current_candidate) != "http_plain":
                delegated_error = ProviderConfigurationError(
                    "Kobo-Vault : le candidat delegated n'est pas exécutable via HTTP direct "
                    f"({service._kobo_candidate_handling(current_candidate) or 'unknown'})"
                )
                continue
            payload = delegated
            candidate = current_candidate
            selected_profile = profile
            request, destination = http_request_for(current_candidate, profile)
            acquisition_mode = "delegated-http"
            break

    if request is None or destination is None or candidate is None or payload is None:
        if delegation_forbidden:
            raise KoboVaultError(
                "Kobo-Vault : autorisation de délégation requise (scope kobo.acquisition.delegate)",
                code="missing_delegation_scope", status_code=403,
            ) from delegated_error
        if delegated_error is not None and descriptor_error is None:
            raise delegated_error
        if descriptor_error is not None:
            raise descriptor_error
        if delegated_error is not None:
            raise delegated_error
        raise ProviderConfigurationError("Kobo-Vault : aucun candidat d'acquisition exploitable")

    _TRACE_LOG.info(
        "TRACE kobo candidate selected identity=%s credential=%s item_id=%s handling=%s candidate=%s mode=%s prefer_acsm=%s",
        identity.identity_id, item.source_credential, item.record_id,
        service._kobo_candidate_handling(candidate), KoboOfferAdapter.normalize_candidate(candidate).safe_shape(),
        acquisition_mode, prefer_acsm,
    )

    # Project the selected provider recipe into the generic 0.8 offer/plan/ledger.
    # The Kobo adapter still owns protocol details; LibraryService no longer owns
    # candidate policy or the persistent acquisition state machine.
    generic_offer = KoboOfferAdapter.offer(
        payload,
        credential_id=item.source_credential,
        account_id=source_account_id,
        item_id=item.record_id,
        revision_id=item.revision_id,
        identity_id=identity.identity_id,
    )
    selected_generic = KoboOfferAdapter.normalize_candidate(candidate)
    selected_offer = AcquisitionOffer(
        entitlement=generic_offer.entitlement,
        candidates=(selected_generic,),
        provider_contract=generic_offer.provider_contract,
        expires_at=generic_offer.expires_at,
        metadata={**generic_offer.metadata, "acquisition_mode": acquisition_mode},
    )
    plan = AcquisitionPlanner().plan(
        selected_offer,
        preferred_format=preferred,
        executor_for=lambda _candidate: "kobo-adept",
    )
    ledger_id = service.acquisition_ledger.discover(selected_offer, title=item.title)
    if plan is not None:
        service.acquisition_ledger.set_plan(ledger_id, plan)
    ledger_row = service.storage.acquisition_get(ledger_id)
    if ledger_row is not None and ledger_row.get("status") != "acquiring":
        service.acquisition_ledger.begin(ledger_id)
    started.append(ledger_id)

    def execute_download() -> dict[str, Any]:
        source = request.get("source") if isinstance(request, dict) else {}
        _TRACE_LOG.info(
            "TRACE kobo adept begin identity=%s credential=%s item_id=%s source_type=%s format=%s mode=%s",
            identity.identity_id, item.source_credential, item.record_id,
            source.get("type") if isinstance(source, dict) else None, request.get("format"), acquisition_mode,
        )
        try:
            value = service.execute_acquisition_request(request)
        except (KoboAdeptError, UserFacingError) as exc:
            # Supported Adept v8+ reports a transport-contract violation if
            # stdout is not exactly one JSON document. Keep the historical
            # invalid_json branch solely to reconcile artifacts created by an
            # older runtime before upgrading Paris.
            if getattr(exc, "code", "") in {"invalid_json", "json_transport_violation"} and getattr(exc, "exit_code", None) == 0 and destination.is_file():
                details = dict(getattr(exc, "details", {}) or {})
                details.update({
                    "artifact_path": str(destination),
                    "artifact_size": destination.stat().st_size,
                    "artifact_sha256": service._sha256_file(destination),
                })
                service.diagnostics.add(
                    "KOBO-ADEPT", "download result unparseable", "warning",
                    duration_ms=details.get("duration_ms"),
                    detail=details,
                    level="detailed",
                )
                raise KoboAdeptError(
                    "Kobo-Adept a terminé avec succès et a produit un artefact, "
                    "mais Paris n'a pas pu réconcilier son résultat JSON",
                    code="result_unparseable_artifact_present",
                    exit_code=0,
                    details=details,
                ) from exc
            raise
        process_diag = service.runtime_diagnostic("runtime.kobo-adept")
        _TRACE_LOG.info(
            "TRACE kobo adept end identity=%s credential=%s item_id=%s operation=%s duration_ms=%s stdout_mode=%s",
            identity.identity_id, item.source_credential, item.record_id, value.get("operation"),
            process_diag.get("duration_ms"), process_diag.get("stdout_mode"),
        )
        if process_diag.get("stdout_mode") not in {None, "exact"}:
            service.diagnostics.add(
                "KOBO-ADEPT", "stdout JSON recovered", "warning",
                duration_ms=process_diag.get("duration_ms"),
                detail=process_diag,
                level="detailed",
            )
        return value

    try:
        result = execute_download()
    except (KoboAdeptError, UserFacingError) as exc:
        # A descriptor/direct URL can expire between resolution and download.
        # Refresh once through delegated mode. On v8, translate the delegated
        # recipe to source.type=kobo when possible; older contracts retain HTTP.
        if getattr(exc, "code", "") not in {"source_auth_expired", "not_authorized"}:
            raise
        # resolve() normalizes plugin HTTP 403 into missing_delegation_scope
        # and caches the capability refusal for this credential. Keep its
        # structured classification instead of masking it as configuration.
        refreshed = resolve("delegated", selected_profile, refresh=True)
        if acquisition_mode.startswith("acsm"):
            refreshed_acsm = service._kobo_acsm_candidate(refreshed)
            if refreshed_acsm is not None:
                candidate = refreshed_acsm
                request, destination = acsm_request_for(candidate, selected_profile)
                acquisition_mode = "acsm-delegated"
            elif use_kobo_delegated:
                candidate = service._kobo_delegated_candidate(refreshed, preferred)
                if service._kobo_candidate_handling(candidate) == "http_plain":
                    request, destination = http_request_for(candidate, selected_profile)
                    acquisition_mode = "delegated-http"
                else:
                    request, destination = delegated_request_for(refreshed, candidate, selected_profile)
                    acquisition_mode = "delegated-kobo"
            else:
                candidate = service._kobo_candidate(refreshed, preferred)
                if service._kobo_candidate_handling(candidate) != "http_plain":
                    raise ProviderConfigurationError(
                        "Kobo-Vault : retry ACSM sans délégation Kobo, candidat non-http_plain "
                        f"({service._kobo_candidate_handling(candidate) or 'unknown'})"
                    )
                request, destination = http_request_for(candidate, selected_profile)
                acquisition_mode = "delegated-http"
        elif use_kobo_delegated:
            try:
                candidate = service._kobo_delegated_candidate(refreshed, preferred)
                if service._kobo_candidate_handling(candidate) == "http_plain":
                    request, destination = http_request_for(candidate, selected_profile)
                    acquisition_mode = "delegated-http"
                else:
                    request, destination = delegated_request_for(refreshed, candidate, selected_profile)
                    acquisition_mode = "delegated-kobo"
            except ProviderConfigurationError as delegated_plan_exc:
                candidate = service._kobo_candidate(refreshed, preferred)
                if service._kobo_candidate_handling(candidate) != "http_plain":
                    raise delegated_plan_exc
                request, destination = http_request_for(candidate, selected_profile)
                acquisition_mode = "delegated-http"
        else:
            candidate = service._kobo_candidate(refreshed, preferred)
            if service._kobo_candidate_handling(candidate) != "http_plain":
                raise ProviderConfigurationError(
                    "Kobo-Vault : retry sans délégation Kobo, candidat non-http_plain "
                    f"({service._kobo_candidate_handling(candidate) or 'unknown'})"
                )
            request, destination = http_request_for(candidate, selected_profile)
            acquisition_mode = "delegated-http"
        payload = refreshed
        # The selected candidate (and possibly handling/format) can change
        # after an expired URL. Persist the actual retry plan before invoking
        # the runtime, rather than leaving the original candidate in the ledger.
        refreshed_offer = KoboOfferAdapter.offer(
            refreshed,
            credential_id=item.source_credential,
            account_id=source_account_id,
            item_id=item.record_id,
            revision_id=item.revision_id,
            identity_id=identity.identity_id,
        )
        selected_retry_offer = AcquisitionOffer(
            entitlement=refreshed_offer.entitlement,
            candidates=(KoboOfferAdapter.normalize_candidate(candidate),),
            provider_contract=refreshed_offer.provider_contract,
            expires_at=refreshed_offer.expires_at,
            metadata={**refreshed_offer.metadata, "acquisition_mode": acquisition_mode},
        )
        retry_plan = AcquisitionPlanner().plan(
            selected_retry_offer,
            preferred_format=preferred,
            executor_for=lambda _candidate: "kobo-adept",
        )
        if retry_plan is None:
            raise ProviderConfigurationError("Kobo-Vault : aucun plan d'acquisition valide après rafraîchissement")
        service.acquisition_ledger.begin(
            ledger_id, plan=retry_plan, metadata={"acquisition_mode": acquisition_mode},
        )
        result = execute_download()

    if str(result.get("operation") or "download") != "download":
        raise ProviderConfigurationError("Kobo-Adept : type de résultat inattendu")
    artifact = result.get("artifact") if isinstance(result, dict) else None
    if not isinstance(artifact, dict):
        raise ProviderConfigurationError("Kobo-Adept : résultat sans artefact")
    path = Path(str(artifact.get("path") or destination))
    if path.resolve() != destination.resolve():
        raise ProviderConfigurationError("Kobo-Adept : chemin d'artefact inattendu")
    if not path.is_file():
        raise ProviderConfigurationError("Kobo-Adept : artefact final absent")
    _TRACE_LOG.info(
        "TRACE kobo artifact ready identity=%s credential=%s item_id=%s path=%s format=%s drm=%s",
        identity.identity_id, item.source_credential, item.record_id, path, artifact.get("format"), artifact.get("drm"),
    )
    original_format = str(
        candidate.get("source_format")
        or service._kobo_candidate_media_format(candidate)
        or ""
    ).lower() or None
    observed = service._observed_backup_artifact(path)
    fmt = observed["format"].lower()
    drm = str(artifact.get("drm") or service._kobo_candidate_drm(candidate) or "unknown")
    candidate_source = service._kobo_candidate_source(candidate)
    candidate_handling = service._kobo_candidate_handling(candidate)
    validity = payload.get("acquisition", {}).get("validity", {}) if isinstance(payload.get("acquisition"), dict) else {}
    expires_at = str(validity.get("expires_at") or "").strip() or None if isinstance(validity, dict) else None
    vault_contract = str(getattr(session, "api_version", "") or "2.x")
    source_result = result.get("source") if isinstance(result.get("source"), dict) else {}
    source_kobo = source_result.get("kobo") if isinstance(source_result.get("kobo"), dict) else {}
    service.diagnostics.add(
        "KOBO", "local acquisition", "ok",
        detail={
            "credential_id": item.source_credential,
            "product_id": item.record_id,
            "format": fmt,
            "vault_format": original_format,
            "drm": drm,
            "display_profile": selected_profile,
            "bytes": artifact.get("size"),
            "adept_contract": adept_state.get("contract_version"),
            "adept_strict_json": adept_state.get("strict_json_stdout"),
            "adept_kobo_delegated": adept_state.get("kobo_delegated_available"),
            "adept_pinned_delivery_candidate": adept_state.get("kobo_pinned_delivery_candidate"),
            "adept_separate_content_keys_replay": adept_state.get("kobo_separate_content_keys_replay"),
            "acquisition_mode": acquisition_mode,
            "vault_candidate_source": candidate_source,
            "vault_handling": candidate_handling,
            "acsm_preferred": prefer_acsm,
            "kdrm_removed": source_kobo.get("kdrm_removed"),
            "vault_contract": vault_contract,
        },
    )
    metadata = {
        "title": item.title,
        "author": item.author,
        "content_type": item.content_type or "ebook",
        "format": fmt.upper(),
        "original_format": original_format,
        "announced_format": item.format,
        "preferred_format": preferred,
        "observed_format": fmt,
        "format_evidence": {"method": "container_inspection", "sha256": observed["sha256"], "size": observed["size"]},
        "revision_id": item.revision_id,
        "size": observed["size"],
        "sha256": observed["sha256"],
        "download_id": result.get("download_id"),
        "vault_contract": vault_contract,
        "vault_display_profile": selected_profile,
        "acquisition_mode": acquisition_mode,
        "vault_candidate_source": candidate_source,
        "vault_handling": candidate_handling,
        "adept_profile": adept_profile or None,
        "adept_profile_source": adept_profile_source,
    }
    if source_kobo:
        metadata["kobo_execution"] = {
            key: source_kobo.get(key)
            for key in ("drm_type", "url_format", "display_profile", "content_keys_count", "kdrm_removed")
            if source_kobo.get(key) is not None
        }
    if isinstance(result.get("hook"), dict):
        metadata["hook"] = dict(result["hook"])
    artifact_sha = observed["sha256"]
    if artifact_sha:
        service.acquisition_ledger.complete(
            ledger_id,
            Artifact.from_file(
                path,
                sha256=artifact_sha,
                media_format=fmt,
                mime_type=service.acquisition.mime_type_for(path, item),
                metadata={"provider": "kobo", "item_id": item.record_id,
                          "announced_format": item.format, "candidate_format": original_format,
                          "preferred_format": preferred, "observed_format": fmt,
                          "format_evidence": metadata["format_evidence"]},
            ),
        )
    return Acquisition(
        provider_id="kobo",
        credential_id=item.source_credential,
        account_id=source_account_id,
        item_id=item.record_id,
        loan_id=None,
        kind="direct_file",
        source_url=None,
        local_path=path,
        mime_type=service.acquisition.mime_type_for(path, item),
        drm=drm,
        expires_at=expires_at,
        metadata=metadata,
        artifact_path=path,
        target_dir=target_dir,
        action="kobo-adept",
    )

