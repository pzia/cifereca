"""Pure LLM settings and profile storage; no network or credential lookup."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from cifereca.config_defaults import DEFAULT_CONFIG
from .llm_service import _allowed_endpoint


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



_FLAT = frozenset(DEFAULT_CONFIG["llm"])
_FIELDS = _FLAT - {"enabled", "allow_remote"}
_SCHEMA = frozenset({"schema_version", "enabled", "active_profile",
                     "profiles", "remote_approval"})
_PROFILE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MAX_PROFILES = 32


def is_profile_config(raw: object) -> bool:
    """Any profile marker selects the new parser, including malformed schemas."""
    return isinstance(raw, Mapping) and bool((_SCHEMA - {"enabled"}) & raw.keys())


def _error() -> LLMSettingsError:
    return LLMSettingsError("Configuration des profils IA invalide")


def _fingerprint(name: str, profile: Mapping[str, Any]) -> str:
    """Bind the explicit permission to profile, exact endpoint, model and key reference."""
    fields = [name, profile["provider"], profile["endpoint"],
              profile["model"], profile["api_key_env"]]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=True).encode("ascii")).hexdigest()


def _normalize_profile(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict) or not set(raw) <= _FIELDS:
        raise _error()
    flat = normalize_llm_settings({**raw, "enabled": False, "allow_remote": False})
    return {key: flat[key] for key in sorted(_FIELDS)}


def normalize_profiles(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Migrate flat settings or validate canonical storage without I/O.

    A legacy remote opt-in is not a profile-bound approval. Preserve its
    destination and limits, but disable remote activation until explicit consent.
    """
    if not isinstance(raw, Mapping):
        raise _error()
    if not is_profile_config(raw):
        if not set(raw) <= _FLAT:
            raise _error()
        flat = normalize_llm_settings(raw)
        _, remote = _allowed_endpoint(flat["endpoint"], allow_remote=True)
        has_values = any(flat[k] != DEFAULT_CONFIG["llm"][k] for k in _FIELDS)
        raw = {
            "schema_version": 1,
            "enabled": bool(flat["enabled"] and not remote),
            "active_profile": "legacy" if has_values else None,
            "profiles": {"legacy": {k: flat[k] for k in _FIELDS}} if has_values else {},
            "remote_approval": None,
        }
    if (set(raw) != _SCHEMA or type(raw["schema_version"]) is not int
            or raw["schema_version"] != 1):
        raise _error()
    enabled, active, profiles = raw["enabled"], raw["active_profile"], raw["profiles"]
    approval = raw["remote_approval"]
    if (type(enabled) is not bool or type(profiles) is not dict
            or len(profiles) > _MAX_PROFILES):
        raise _error()
    if active is not None and (type(active) is not str or active not in profiles):
        raise _error()
    if enabled and active is None:
        raise _error()
    if approval is not None and (type(approval) is not str
                                 or not re.fullmatch(r"[a-f0-9]{64}", approval)):
        raise _error()
    normalized = {}
    for name, value in profiles.items():
        if type(name) is not str or not _PROFILE_ID.fullmatch(name):
            raise _error()
        normalized[name] = _normalize_profile(value)
    selected = normalized.get(active)
    if selected is None or approval != _fingerprint(active, selected):
        approval = None
    if selected is not None:
        allowed, remote = _allowed_endpoint(selected["endpoint"], allow_remote=True)
        if not (allowed and remote):
            approval = None
        if enabled:
            normalize_llm_settings({**selected, "enabled": True,
                                    "allow_remote": approval is not None})
    return {"schema_version": 1, "enabled": enabled, "active_profile": active,
            "profiles": normalized, "remote_approval": approval}


def selected_settings(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Flat view of the selected profile, also when the service is disabled."""
    cfg = normalize_profiles(raw)
    active = cfg["active_profile"]
    if active is None:
        return dict(DEFAULT_CONFIG["llm"])
    return normalize_llm_settings({**cfg["profiles"][active],
                                   "enabled": cfg["enabled"],
                                   "allow_remote": cfg["remote_approval"] is not None})


def select_profile(raw: Mapping[str, Any], name: str | None) -> dict[str, Any]:
    cfg = normalize_profiles(raw)
    if name != cfg["active_profile"]:
        cfg.update(active_profile=name, enabled=False, remote_approval=None)
    return normalize_profiles(cfg)


def store_profile(raw: Mapping[str, Any], name: str,
                  values: dict[str, Any]) -> dict[str, Any]:
    cfg = normalize_profiles(raw)
    profile = _normalize_profile(values)
    if name == cfg["active_profile"] and _fingerprint(name, profile) != _fingerprint(
            name, cfg["profiles"][name]):
        cfg.update(enabled=False, remote_approval=None)
    cfg["profiles"][name] = profile
    return normalize_profiles(cfg)


def delete_profile(raw: Mapping[str, Any], name: str) -> dict[str, Any]:
    cfg = normalize_profiles(raw)
    if name not in cfg["profiles"]:
        raise _error()
    if name == cfg["active_profile"]:
        cfg.update(active_profile=None, enabled=False, remote_approval=None)
    del cfg["profiles"][name]
    return normalize_profiles(cfg)


def enable_profile(raw: Mapping[str, Any], *, enabled: bool,
                   remote_confirmed: bool = False) -> dict[str, Any]:
    """Explicit local activation or destination-bound remote opt-in."""
    cfg = normalize_profiles(raw)
    if type(enabled) is not bool or type(remote_confirmed) is not bool:
        raise _error()
    active = cfg["active_profile"]
    cfg.update(enabled=enabled, remote_approval=None)
    if enabled and active is not None:
        profile = cfg["profiles"][active]
        allowed, remote = _allowed_endpoint(profile["endpoint"], allow_remote=True)
        if allowed and remote and remote_confirmed:
            cfg["remote_approval"] = _fingerprint(active, profile)
    return normalize_profiles(cfg)
