"""Pure, fail-closed settings and an explicit synthetic LLM connectivity probe.

This module deliberately imports neither GTK nor a configured live service. No
network operation occurs during normalization, status display or saving.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping
from urllib.parse import urlsplit

from cifereca.core.llm_service import OptionalLLMService, _allowed_endpoint
from cifereca.plugin_api import LLMRequest


# Retain the historical import surface for GTK and its tests.
from cifereca.core.llm_profiles import LLMSettingsError, normalize_llm_settings


def destination_summary(settings: Mapping[str, Any]) -> tuple[str, bool]:
    """Disclosure is host-only, never URL paths, credentials or key values."""
    try:
        cfg = normalize_llm_settings(settings)
        host = urlsplit(cfg["endpoint"]).hostname or ""
        allowed, remote = _allowed_endpoint(cfg["endpoint"], allow_remote=True)
        return ((host[:180] if allowed else "Destination invalide"), bool(remote))
    except (LLMSettingsError, ValueError):
        return ("Non configurée", False)


def env_key_is_set(settings: Mapping[str, Any]) -> bool:
    name = settings.get("api_key_env", "") if isinstance(settings, Mapping) else ""
    return bool(type(name) is str and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,127}", name)
                and os.environ.get(name))


# Only these non-sensitive codes may be returned by a synthetic probe, used
# by UI and tracing. Do not add endpoint, host, model or free-form exceptions.
LLMProbeStatus = Literal[
    "success", "cancelled", "timeout", "auth_refused", "not_found",
    "rate_limited", "invalid_request", "backend_unavailable", "busy",
    "invalid_config", "inconclusive", "internal_error",
]
_PROBE_LABELS: dict[str, str] = {
    "success": "Test réussi",
    "cancelled": "Test annulé",
    "timeout": "Délai de connexion dépassé",
    "auth_refused": "Authentification refusée",
    "not_found": "Modèle ou endpoint introuvable",
    "rate_limited": "Service limité temporairement",
    "invalid_request": "Requête de test invalide",
    "backend_unavailable": "Serveur injoignable ou réponse non concluante",
    "busy": "Service momentanément occupé",
    "invalid_config": "Configuration invalide",
    "inconclusive": "Test non concluant",
    "internal_error": "Test non concluant ; vérifier la configuration",
}
_PROBE_REASON_CODES = frozenset({
    "cancelled", "timeout", "auth_refused", "not_found", "rate_limited",
    "invalid_request", "backend_unavailable", "busy",
})


@dataclass(frozen=True)
class LLMProbeResult:
    """A status category and a fixed UI label; never untrusted provider text."""
    status: LLMProbeStatus
    label: str


def _probe_outcome(code: object) -> LLMProbeResult:
    # Typed annotations do not validate runtime data. Never relay a provider
    # supplied or malformed status into tracing or UI labels.
    status = code if type(code) is str and code in _PROBE_LABELS else "inconclusive"
    return LLMProbeResult(status=status, label=_PROBE_LABELS[status])


def probe_llm_connection_result(
    settings: Mapping[str, Any], *, remote_confirmed: bool = False,
    service_factory: Callable[..., Any] = OptionalLLMService,
) -> LLMProbeResult:
    """One explicit synthetic probe; never return payloads or free error text."""
    cfg = normalize_llm_settings(settings)
    if not cfg["enabled"]:
        return _probe_outcome("invalid_config")
    _host, remote = destination_summary(cfg)
    if remote and not remote_confirmed:
        raise LLMSettingsError("Confirmer l’essai distant avant tout envoi")
    service = service_factory(cfg)
    if not service.available():
        return _probe_outcome("invalid_config")
    result = service.generate(LLMRequest(
        "Test de connexion technique. Réponds uniquement OK.",
        task="generate", max_output_tokens=8,
    ))
    if result.ok:
        if type(result.text) is str and result.text.strip():
            return _probe_outcome("success")
        return _probe_outcome("inconclusive")
    reason = result.reason if type(result.reason) is str else ""
    if reason == "unavailable":
        return _probe_outcome("invalid_config")
    if reason == "request_too_large":
        return _probe_outcome("invalid_request")
    return _probe_outcome(reason if reason in _PROBE_REASON_CODES else "inconclusive")


def probe_llm_connection(
    settings: Mapping[str, Any], *, remote_confirmed: bool = False,
    service_factory: Callable[..., Any] = OptionalLLMService,
) -> str:
    """Legacy UI text contract; the typed result drives new observability."""
    cfg = normalize_llm_settings(settings)
    if not cfg["enabled"]:
        return "Désactivé"
    return probe_llm_connection_result(
        cfg, remote_confirmed=remote_confirmed, service_factory=service_factory,
    ).label
