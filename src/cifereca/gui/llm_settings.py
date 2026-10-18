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

from cifereca.config_defaults import DEFAULT_CONFIG
from cifereca.core.llm_service import OptionalLLMService, _allowed_endpoint
from cifereca.plugin_api import LLMRequest


class LLMSettingsError(ValueError):
    """Non-sensitive, user-facing validation failure."""


def _text(value: object, field: str, maximum: int, *, required: bool = False) -> str:
    if type(value) is not str or len(value) > maximum or any(
        ord(ch) < 32 or ord(ch) == 127 or 0x80 <= ord(ch) <= 0x9F
        or 0x202A <= ord(ch) <= 0x202E or 0x2066 <= ord(ch) <= 0x2069
        or 0xD800 <= ord(ch) <= 0xDFFF
        for ch in value
    ):
        raise LLMSettingsError(f"Champ {field} invalide")
    result = value.strip()
    if required and not result:
        raise LLMSettingsError(f"Champ {field} obligatoire")
    return result


def normalize_llm_settings(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return exactly the persistable keys; never accept an API key value."""
    if not isinstance(raw, Mapping):
        raise LLMSettingsError("Configuration IA invalide")
    defaults = DEFAULT_CONFIG["llm"]
    enabled = raw.get("enabled", defaults["enabled"])
    remote_allowed = raw.get("allow_remote", defaults["allow_remote"])
    if type(enabled) is not bool or type(remote_allowed) is not bool:
        raise LLMSettingsError("Autorisation IA invalide")
    provider = _text(raw.get("provider", defaults["provider"]), "fournisseur", 80)
    if provider != "openai-compatible":
        raise LLMSettingsError("Seule l’API compatible OpenAI est prise en charge")
    endpoint = _text(raw.get("endpoint", defaults["endpoint"]), "adresse", 1024)
    model = _text(raw.get("model", defaults["model"]), "modèle", 128)
    key_name = _text(raw.get("api_key_env", defaults["api_key_env"]), "variable d’environnement", 128)
    if key_name and not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,127}", key_name):
        raise LLMSettingsError("Nom de variable d’environnement invalide")
    try:
        timeout = float(raw.get("timeout_seconds", defaults["timeout_seconds"]))
        max_input = int(raw.get("max_input_chars", defaults["max_input_chars"]))
        max_output = int(raw.get("max_output_tokens", defaults["max_output_tokens"]))
    except (TypeError, ValueError, OverflowError) as exc:
        raise LLMSettingsError("Limites ou délai invalides") from exc
    if not (0.2 <= timeout <= 15) or not (1 <= max_input <= 16000) or not (1 <= max_output <= 1024):
        raise LLMSettingsError("Délai 0,2–15 s ; entrée 1–16000 ; sortie 1–1024")
    if endpoint:
        try:
            parsed = urlsplit(endpoint)
            _ = parsed.port
        except ValueError as exc:
            raise LLMSettingsError("Adresse IA invalide") from exc
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise LLMSettingsError("Adresse IA interdite (identifiants, query ou fragment)")
        # Also reject alternate URL authorities that could hide the destination.
        if any(c in endpoint for c in ("\n", "\r", "\t")):
            raise LLMSettingsError("Adresse IA invalide")
    if enabled:
        if not endpoint or not model:
            raise LLMSettingsError("Indiquer l’adresse et le modèle avant activation")
        allowed, _remote = _allowed_endpoint(endpoint, allow_remote=remote_allowed)
        if not allowed:
            raise LLMSettingsError("Destination non autorisée : HTTPS et accord requis hors machine")
    return {
        "enabled": enabled, "provider": provider, "endpoint": endpoint,
        "model": model, "allow_remote": remote_allowed, "api_key_env": key_name,
        "timeout_seconds": timeout, "max_input_chars": max_input,
        "max_output_tokens": max_output,
    }


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
