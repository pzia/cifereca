"""#3252: Kobo exact selected delivery offer attestation, never a content GET.

Consumes the *already returned* private Vault POST descriptor. No HTTP,
consent, right inference, ADEPT, final-byte receipt or Core persistence.
Unknown and conflicting candidate shapes deliberately remain unoffered.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit


def _safe_https_get(candidate: dict[str, Any]) -> bool:
    if (candidate.get("transport") != "https"
            or candidate.get("method") != "GET"):
        return False
    raw = candidate.get("url")
    if (type(raw) is not str or not 0 < len(raw) <= 4096
            or any(ord(c) < 33 or ord(c) > 126 or c == "\\" for c in raw)):
        return False
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").casefold()
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or parsed.fragment or not parsed.path.startswith("/")
                or host.endswith((".", ".localhost", ".local", ".internal",
                                   ".home.arpa", ".invalid", ".test", ".example"))
                or host in {"localhost", "metadata.google.internal"}):
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            pass
        labels = host.split(".")
        return len(labels) >= 2 and all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        )
    except (ValueError, TypeError, AttributeError):
        return False


def _current_expiry(envelope: dict[str, Any]) -> bool:
    """Refuse an explicitly expired or unparseable upstream expiry.

    A missing/null expiry means no expiration *was attested*; it is not a
    permanent-URL guarantee. No clock, token, credential or URL is exported.
    """
    validity = envelope.get("validity")
    if validity is not None and type(validity) is not dict:
        return False
    source = validity if type(validity) is dict else {}
    for entry in (source.get("expires_at"), envelope.get("expires_at")):
        if entry is None:
            continue
        if type(entry) is not str or not 20 <= len(entry) <= 48:
            return False
        try:
            expires = datetime.fromisoformat(
                entry[:-1] + "+00:00" if entry.endswith("Z") else entry
            )
            if (expires.tzinfo is None or expires.utcoffset() is None
                    or not 2000 <= expires.year <= 2100
                    or expires <= datetime.now(timezone.utc)):
                return False
        except (ValueError, OverflowError):
            return False
    return True


def exact_kobo_offer_id(
    payload: Any, *, identity_id: str, credential_id: str,
    account_id: str, product_id: str,
) -> str:
    """Qualify an observed, single, executable selected GET candidate.

    Descriptor fields are provider-supplied. A credential's owned manifest
    alone is not an offer; a generic Vault schema or GET-less KDRM recipe
    cannot be converted into one. The signed URL never enters the ID.
    """
    coordinates = (identity_id, credential_id, account_id, product_id)
    if (not all(type(x) is str and 0 < len(x) <= 256 for x in coordinates)
            or type(payload) is not dict or not _current_expiry(payload)):
        return ""
    # Vault descriptor metadata is provider-controlled, not an extension of
    # Core's attested owner. An explicit conflicting scope anywhere in this
    # response invalidates the WHOLE offer, rather than letting a matching
    # candidate in the same envelope win. Missing optional fields retain
    # backwards compatibility with older Vault descriptors.
    for key, expected in (
        ("provider", "kobo"),
        ("source_provider", "kobo"),
        ("product_id", product_id),
        ("account_id", account_id),
        ("credential_id", credential_id),
        ("identity_id", identity_id),
    ):
        if key in payload and (
            type(payload[key]) is not str or payload[key] != expected
        ):
            return ""
    acquisition = payload.get("acquisition")
    if type(acquisition) is not dict or not _current_expiry(acquisition):
        return ""
    if (type(acquisition.get("product_id")) is not str
            or acquisition["product_id"] != product_id):
        return ""
    for key, expected in (
        ("provider", "kobo"),
        ("source_provider", "kobo"),
        ("account_id", account_id),
        ("credential_id", credential_id),
        ("identity_id", identity_id),
        ("mode", "descriptor"),
    ):
        if key in acquisition and (
            type(acquisition[key]) is not str or acquisition[key] != expected
        ):
            return ""
    candidates = acquisition.get("candidates")
    if type(candidates) is not list or not 1 <= len(candidates) <= 50:
        return ""
    eligible: list[tuple[str, str, str, str]] = []
    for candidate in candidates:
        if type(candidate) is not dict:
            continue
        # A foreign candidate taints the complete upstream descriptor even
        # when another sibling advertises a valid HTTPS GET. Never choose
        # "the one usable" entry while discarding contradictory owner facts.
        for key, expected in (
            ("provider", "kobo"),
            ("source_provider", "kobo"),
            ("product_id", product_id),
            ("account_id", account_id),
            ("credential_id", credential_id),
            ("identity_id", identity_id),
        ):
            if key in candidate and (
                type(candidate[key]) is not str or candidate[key] != expected
            ):
                return ""
        if not _safe_https_get(candidate) or not _current_expiry(candidate):
            continue
        kind, handling = candidate.get("kind"), candidate.get("handling")
        drm = candidate.get("drm")
        raw_drm = (drm.get("raw_type") if type(drm) is dict
                   else candidate.get("drm_type"))
        if type(raw_drm) is not str:
            continue
        media = candidate.get("media_format") or candidate.get("source_format")
        if type(media) is not str:
            continue
        fmt = media.lower().strip()
        # A verified HTTPS acquisition link is not by itself proof of an
        # executable ADEPT/KDRM pipeline. Only conservative known paths.
        if (kind == "acsm" and handling == "adobe_adept"
                and raw_drm == "AdobeDrm" and fmt == "acsm"):
            pass
        elif (kind == "http" and handling == "http_plain"
              and raw_drm == "SignedNoDrm" and fmt in {"epub", "pdf", "kepub"}):
            pass
        else:
            continue
        source = candidate.get("source")
        if source is None and type(candidate.get("kobo")) is dict:
            source = candidate["kobo"].get("source")
        if source is not None and (type(source) is not str or len(source) > 128):
            continue
        eligible.append((kind, handling, raw_drm, fmt + ":" + (source or "")))
    # Two different candidate routes must not be arbitrarily conflated,
    # including multiple signed URLs of the same declared format.
    if len(eligible) != 1:
        return ""
    fields = coordinates + eligible[0]
    return "kobo.descriptor:" + hashlib.sha256(
        "\x00".join(fields).encode("utf-8")
    ).hexdigest()
