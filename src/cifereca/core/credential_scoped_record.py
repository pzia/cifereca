"""Passive structural check for a credential-scoped provider record ID.

The credential's digest prevents a record from being *mistakenly* assigned
to another credential, but it is NOT proof of a purchase, of session
authentication, or permission to access a private catalogue.
"""
from __future__ import annotations

import hashlib
import re

_LID = re.compile(r"[a-zA-Z0-9_.-]{1,160}\Z")
_VARIANT = re.compile(r"[0-9a-f]{16}\Z")


def exact_credential_scoped_record(
    value: object, *, provider_id: str, credential_id: str,
) -> str | None:
    """Accept the exact provider:SHA256(credential)[:20]:lid:variant form.

    Invalid provider, credential, case, digest, variant or structural
    content fails closed. No network, storage access or rights projection.
    """
    if (type(value) is not str or len(value) > 512
            or type(provider_id) is not str or not provider_id
            or not re.fullmatch(r"[a-z][a-z0-9-]*", provider_id)
            or type(credential_id) is not str or not credential_id):
        return None
    digest = hashlib.sha256(credential_id.encode("utf-8")).hexdigest()[:20]
    prefix = provider_id + ":" + digest + ":"
    if not value.startswith(prefix):
        return None
    lid, sep, variant = value[len(prefix):].rpartition(":")
    if (not sep or _LID.fullmatch(lid) is None
            or _VARIANT.fullmatch(variant) is None):
        return None
    return value
