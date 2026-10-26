"""Core-only cover download failure observations (#3108).

The cover loader, not a provider or a generic Diagnostics.add call, chooses
from this finite vocabulary.  Never infer a provider, HTTP status number,
account, book or unique network request from a cover failure.
"""
from __future__ import annotations

from typing import Any

LABELS = {
    "timeout": "couverture · délai dépassé",
    "http_status": "couverture · échec HTTP",
    "request_failed": "couverture · requête échouée",
    "storage_failed": "couverture · écriture échouée",
    "invalid_image": "couverture · image invalide",
    "unexpected": "couverture · erreur inattendue",
}


def public_fields(value: object) -> dict[str, Any]:
    """Require the exact Core payload, without coercion of hostile objects."""
    if type(value) is not dict or set(value) != {"code", "duration_ms"}:
        return {}
    code = value.get("code")
    duration = value.get("duration_ms")
    if (type(code) is not str or code not in LABELS
            or type(duration) is not int or not 0 <= duration <= 600000):
        return {}
    return {"code": code, "duration_ms": duration}


def public_message(value: object) -> str | None:
    fields = public_fields(value)
    return LABELS[fields["code"]] if fields else None
