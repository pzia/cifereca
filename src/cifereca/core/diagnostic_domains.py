"""Closed, extensible business taxonomy for attested diagnostic events (#2745).

A domain is attached ONLY after validating a Core producer or a known structured
log schema. Logger prefixes, category, message text and plugin-provided metadata
are not provenance. This table describes query names, never authorizes an event.
"""
from __future__ import annotations

import unicodedata

# Order is stable for clients. Add a code only alongside an attested producer,
# a safe public projection and negative spoofing tests.
DIAGNOSTIC_DOMAINS = (
    "browser_recorder",
    "reconciliation",
    "provider_connection",
    "source_sync",
    "loan_download",
    "kobo_backup",
    "numilog",
    "adept",
)

_ALIASES = {
    "browser": "browser_recorder",
    "recorder": "browser_recorder",
    "browser recorder": "browser_recorder",
    "reconcilitation": "reconciliation",  # known UI typo, not fuzzy matching
    "provider connection": "provider_connection",
    "connexion fournisseur": "provider_connection",
    "synchronisation": "source_sync",
    "synchronisations": "source_sync",
    "loan download": "loan_download",
    "telechargement de pret": "loan_download",
    "sauvegarde kobo": "kobo_backup",
}


def canonical_diagnostic_domain(value: str) -> str:
    """Accept only a known ID or documented alias; never run fuzzy searches."""
    if type(value) is not str or len(value) > 64:
        raise ValueError("unknown diagnostic domain")
    normalized = unicodedata.normalize("NFKD", value.strip().casefold())
    key = "".join(char for char in normalized if not unicodedata.combining(char))
    key = key.replace("-", "_")
    if key in DIAGNOSTIC_DOMAINS:
        return key
    result = _ALIASES.get(key.replace("_", " "))
    if result is None:
        raise ValueError("unknown diagnostic domain")
    return result
