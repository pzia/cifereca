"""P0 #5083: read-only, provider-neutral search connection notices.

Only already-loaded catalogue capabilities and identity-scoped configuration
are inspected. Source-specific wording belongs to versioned ProviderDescriptor
contributions; none of this can select a route, authenticate or start a GET.
"""
from __future__ import annotations

import re
from typing import Any

from ..core.provider_identifier import valid_provider_identifier


_SCHEMA = "cifereca.provider-search-ui/1"
_FRAGMENTS = {
    "unknown": "source non connectée ; état du compte indéterminé.",
    "plugin_missing": "plugin absent ; recherche en ligne indisponible.",
    "plugin_disabled": "plugin désactivé ; recherche en ligne indisponible.",
    "catalog_unavailable": "catalogue non disponible dans ce profil.",
    "account_disconnected": "compte configuré mais source non connectée ; recherche en ligne indisponible.",
    "account_disabled": "compte désactivé pour ce profil ; recherche en ligne indisponible.",
    "account_unscoped": "source non connectée ; profil du compte à vérifier.",
    "account_missing": "aucun compte rattaché à ce profil ; recherche en ligne indisponible.",
}
_MAX_PROVIDERS = 64
_MAX_ACCOUNTS = 512
_MAX_TEXT = 240


def _safe_text(value: object) -> bool:
    return (
        type(value) is str and 1 <= len(value) <= _MAX_TEXT
        and value == value.strip()
        and chr(92) not in value  # also reject literal escaped control/bidi sequences
        and not any(ord(ch) < 32 or ord(ch) == 127
                    or ch in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
                    for ch in value)
        and re.search(r"(?i)(?:https?://|bearer\s+|(?:token|password|secret)=)", value) is None
    )


def _messages(plugin: object) -> dict[str, str]:
    descriptor = getattr(plugin, "descriptor", None)
    if getattr(descriptor, "search_status_notice_schema", None) != _SCHEMA:
        return {}
    rows = getattr(descriptor, "search_status_notices", None)
    if type(rows) is not tuple or not 1 <= len(rows) <= len(_FRAGMENTS):
        return {}
    result: dict[str, str] = {}
    for row in rows:
        if (type(row) is not tuple or len(row) != 2
                or type(row[0]) is not str or row[0] not in _FRAGMENTS
                or row[0] in result or not _safe_text(row[1])):
            return {}
        result[row[0]] = row[1]
    return result


def _qualified_id(value: object) -> bool:
    return type(value) is str and valid_provider_identifier(value)


def search_status_notice(window: Any) -> str:
    """One bounded, identity-fenced projection, with no provider I/O."""
    service = getattr(window, "service", None)
    identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
    reader = getattr(service, "catalogue_source_capabilities", None)
    if type(identity) is not str or not identity or not callable(reader):
        return ""
    try:
        snapshot = reader(identity_id=identity)
        if type(snapshot) not in (list, tuple) or len(snapshot) > _MAX_PROVIDERS:
            return ""
        # Public catalogues cannot imply that an authenticated account exists.
        capabilities: dict[str, list[object]] = {}
        for row in snapshot:
            provider = getattr(row, "provider_id", None)
            if (_qualified_id(provider)
                    and getattr(row, "identity_scope", None) == "authenticated"
                    and getattr(row, "catalog_search_supported", None) is True):
                capabilities.setdefault(provider, []).append(row)
        config = getattr(service, "config", None)
        config_reader = getattr(config, "provider_accounts", None)
        if not callable(config_reader):
            all_accounts = None
        else:
            try:
                all_accounts = config_reader()
            except TypeError:
                # Older fixture APIs accept only provider_id. The capability
                # inventory still provides candidate IDs for local inspection.
                all_accounts = None
            except Exception:
                all_accounts = None
        if all_accounts is not None and (
                type(all_accounts) not in (list, tuple)
                or len(all_accounts) > _MAX_ACCOUNTS):
            all_accounts = None
        candidates = set(capabilities)
        if all_accounts is not None:
            for row in all_accounts:
                if type(row) is dict:
                    provider = row.get("provider_id", row.get("provider"))
                    if _qualified_id(provider):
                        candidates.add(provider)
        if len(candidates) > _MAX_PROVIDERS:
            return ""
        registry = getattr(service, "registry", None)
        plugin_reader = getattr(registry, "maybe_get", None)
        enabled_reader = getattr(service, "_provider_enabled_for_identity", None)
        notices: list[str] = []
        for provider in sorted(candidates):
            matching = capabilities.get(provider, ())
            if any(getattr(row, "catalog_search_available", None) is True
                   for row in matching):
                continue
            if all_accounts is not None:
                rows = [
                    row for row in all_accounts if type(row) is dict
                    and row.get("provider_id", row.get("provider")) == provider
                ]
                qualified = True
            elif callable(config_reader):
                try:
                    rows = config_reader(provider)
                    qualified = type(rows) in (list, tuple) and len(rows) <= _MAX_ACCOUNTS
                except Exception:
                    rows, qualified = (), False
            else:
                rows, qualified = (), False
            if not qualified:
                code = "unknown" if matching else ""
            else:
                own = [
                    row for row in rows
                    if type(row) is dict and row.get("identity_id") == identity
                ]
                if not matching:
                    if not own:
                        continue
                    try:
                        plugin = plugin_reader(provider) if callable(plugin_reader) else None
                    except Exception:
                        plugin = None
                    if plugin is None:
                        code = "plugin_missing"
                    elif callable(enabled_reader) and enabled_reader(provider, identity) is False:
                        code = "plugin_disabled"
                    else:
                        code = "catalog_unavailable"
                elif any(row.get("enabled", True) is True for row in own):
                    code = "account_disconnected"
                elif own:
                    code = "account_disabled"
                elif any(type(row) is dict and not row.get("identity_id") for row in rows):
                    code = "account_unscoped"
                else:
                    code = "account_missing"
            if not code:
                continue
            try:
                plugin = plugin_reader(provider) if callable(plugin_reader) else None
            except Exception:
                plugin = None
            contribution = _messages(plugin).get(code)
            notices.append(contribution if contribution else "Source : " + _FRAGMENTS[code])
            if len(notices) >= 4:
                break
        # Identity rotation while evaluating sources revokes the whole
        # projection; no notice from profile A may appear under profile B.
        if getattr(getattr(service, "current_identity", None), "identity_id", None) != identity:
            return ""
        return " · ".join(notices)
    except Exception:
        # Neither provider exception text nor credential/config objects are UI.
        return ""
