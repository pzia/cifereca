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


def _kdrm_raw_type(value: Any) -> bool:
    """Accept only Kobo Vault's normalized KDRM raw labels.

    The KVault worker classifies `KDRM` and `KoboDRM` (punctuation/case
    variants) into family kobo_kdrm, while preserving the exact source
    DRMType in `drm.raw_type`. This never upgrades arbitrary unknown DRM.
    """
    return (
        type(value) is str and 0 < len(value) <= 80
        and re.sub(r"[^a-z0-9]", "", value.casefold())
        in {"kdrm", "kobodrm"}
    )


def kdrm_runtime_ready(session: object) -> bool:
    """Positive capability for Kobo KDRM; never probe private data."""
    try:
        broker = session.runtime_capabilities
        caps = broker.acquisition("kobo")
        modes = caps.modes
        return (caps.available is True and caps.compatible is True
                and caps.operational is True
                and isinstance(modes, (tuple, list, frozenset, set))
                and "kobo_kdrm" in modes)
    except (AttributeError, TypeError, ValueError, KeyError):
        return False


def kdrm_delegated_runtime_ready(session: object) -> bool:
    """Legacy Kobo ContentAccess replay is a distinct negotiated mode.

    A KDRM backend alone does not attest that source.selection works.
    Neither runtime capability nor this probe is a Vault bearer/scope grant.
    """
    try:
        caps = session.runtime_capabilities.acquisition("kobo")
        modes = caps.modes
        return (caps.available is True and caps.compatible is True
                and caps.operational is True
                and isinstance(modes, (tuple, list, frozenset, set))
                and "kobo_kdrm" in modes and "kobo_delegated" in modes)
    except (AttributeError, TypeError, ValueError, KeyError):
        return False


def exact_kobo_offer_id(
    payload: Any, *, identity_id: str, credential_id: str,
    account_id: str, product_id: str, kdrm_runtime_ready: bool = False,
    kdrm_delegated_ready: bool = False,
    diagnostic: dict[str, object] | None = None,
) -> str:
    """Qualify an observed, single, executable selected GET candidate.

    Descriptor fields are provider-supplied. A credential's owned manifest
    alone is not an offer; a generic Vault schema or GET-less KDRM recipe
    cannot be converted into one. The signed URL never enters the ID.
    """
    # Diagnostic side channel only: never changes the opaque offer or eligibility.
    # All emitted attributes are closed categories, never URLs or auth material.
    if diagnostic is not None:
        diagnostic.clear()

    def reject(reason: str) -> str:
        if diagnostic is not None:
            diagnostic["qualification_reason"] = reason
        return ""

    coordinates = (identity_id, credential_id, account_id, product_id)
    if (not all(type(x) is str and 0 < len(x) <= 256 for x in coordinates)
            or type(payload) is not dict or not _current_expiry(payload)):
        return reject("descriptor_invalid_or_expired")
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
            return reject("descriptor_scope_conflict")
    acquisition = payload.get("acquisition")
    if type(acquisition) is not dict or not _current_expiry(acquisition):
        return reject("acquisition_invalid_or_expired")
    if (type(acquisition.get("product_id")) is not str
            or acquisition["product_id"] != product_id):
        return reject("acquisition_product_mismatch")
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
            return reject("acquisition_scope_conflict")
    candidates = acquisition.get("candidates")
    if type(candidates) is not list or not 1 <= len(candidates) <= 50:
        return reject("candidate_list_invalid")

    def skip(reason: str) -> None:
        if diagnostic is not None and len(candidates) == 1:
            diagnostic["candidate_reason"] = reason

    if diagnostic is not None and len(candidates) == 1:
        observed = candidates[0]
        if type(observed) is dict:
            drm = observed.get("drm")
            raw = (drm.get("raw_type") if type(drm) is dict
                   else observed.get("drm_type"))
            if _kdrm_raw_type(raw) or observed.get("handling") == "kobo_kdrm":
                source = observed.get("source")
                if source is None and type(observed.get("kobo")) is dict:
                    source = observed["kobo"].get("source")
                for name, value, allowed in (
                    ("candidate_source", source,
                     {"library_sync", "content_access_book"}),
                    ("candidate_media_format", observed.get("media_format"),
                     {"epub", "kepub", "pdf", "acsm"}),
                    ("candidate_source_format", observed.get("source_format"),
                     {"epub", "kepub", "flepub", "epub3", "epub3fl"}),
                ):
                    # Source is compared EXACTLY by admission; unlike the
                    # normalized format labels, case/whitespace matter.
                    normalized = (
                        value if name == "candidate_source"
                        else value.strip().lower() if type(value) is str
                        else value
                    )
                    diagnostic[name] = (
                        "missing" if value is None else
                        normalized if type(normalized) is str and normalized in allowed
                        else "other"
                    )
    eligible: list[tuple[str, str, str, str]] = []
    for candidate in candidates:
        if type(candidate) is not dict:
            skip("candidate_not_mapping")
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
                return reject("candidate_scope_conflict")
        if not _safe_https_get(candidate) or not _current_expiry(candidate):
            skip("candidate_transport_or_expiry_invalid")
            continue
        kind, handling = candidate.get("kind"), candidate.get("handling")
        drm = candidate.get("drm")
        raw_drm = (drm.get("raw_type") if type(drm) is dict
                   else candidate.get("drm_type"))
        if type(raw_drm) is not str:
            skip("candidate_drm_missing")
            continue
        # A signed candidate is not an executable plain/Adobe route if its
        # own DRM fields disagree. The executor may read family/protected
        # separately, so a plausible raw_type must not override contradictory
        # nested/root flags to manufacture one "available" offer.
        # KV v2.3.3 keeps Kobo's original DRMType as raw_type, while
        # normalizing the semantic family separately. Real raw values for
        # unprotected books include "None" and "DRMFree", not only the
        # legacy synthetic "SignedNoDrm" spelling.
        plain_raw = frozenset({
            "SignedNoDrm", "None", "NoDrm", "DRMFree", "drmfree",
        })
        adobe_raw = frozenset({"AdobeDrm", "Adobe"})
        known_drm = {
            **{name: (frozenset({"none"}), False) for name in plain_raw},
            **{name: (frozenset({"adobe", "adobe_adept"}), True)
               for name in adobe_raw},
        }
        if _kdrm_raw_type(raw_drm):
            families, required_protected = frozenset({"kobo_kdrm"}), True
        elif raw_drm in known_drm:
            families, required_protected = known_drm[raw_drm]
        else:
            skip("candidate_drm_unknown")
            continue
        nested = drm if type(drm) is dict else {}
        if ("drm_type" in candidate and candidate["drm_type"] != raw_drm):
            skip("candidate_drm_type_conflict")
            continue
        if any(
            key in container
            and (type(container[key]) is not str
                 or container[key] not in families)
            for container, key in (
                (nested, "family"), (candidate, "drm_family"),
            )
        ):
            skip("candidate_drm_family_mismatch")
            continue
        if any(
            key in container
            and (type(container[key]) is not bool
                 or container[key] is not required_protected)
            for container, key in (
                (nested, "protected"), (candidate, "protected"),
            )
        ):
            skip("candidate_drm_protection_mismatch")
            continue
        # Both claims are independently consumed by provider/DRM paths.
        # Contradictory or malformed explicit formats cannot be promoted
        # just because the first truthy field looks like a supported EPUB.
        declared = [
            candidate[key] for key in ("media_format", "source_format")
            if key in candidate and candidate[key] is not None
        ]
        if (not declared or any(type(v) is not str or not v.strip()
                                for v in declared)):
            skip("candidate_format_missing_or_invalid")
            continue
        fmt = declared[0].strip().lower()
        source = candidate.get("source")
        if source is None and type(candidate.get("kobo")) is dict:
            source = candidate["kobo"].get("source")
        if _kdrm_raw_type(raw_drm):
            # Pinned library_sync and legacy delegated ContentAccess are
            # separate ABI routes. Only a runtime explicitly advertising BOTH
            # delegated replay and KDRM may receive Kobo's EPUB3/EPUB3FL.
            # Neither SAMPLE nor an unknown source format can be promoted.
            source_fmt = candidate.get("source_format", declared[0])
            pairs = {
                ("kepub", "kepub"), ("epub", "flepub"), ("epub", "epub"),
            }
            if source == "content_access_book" and kdrm_delegated_ready is True:
                pairs = pairs | {("epub", "epub3"), ("epub", "epub3fl")}
            if (type(source_fmt) is not str or
                    (fmt, source_fmt.strip().lower()) not in pairs):
                skip("kdrm_format_pair_unqualified")
                continue
        else:
            # KVault distinguishes media_format (normalized, e.g. epub)
            # from source_format (upstream wire label, e.g. EPUB3).
            # Only explicitly known full-book source variants are equivalent.
            # NEVER accept *_SAMPLE or unknown labels as a full purchase.
            normalized_source = candidate.get("source_format", declared[0])
            if type(normalized_source) is not str:
                skip("candidate_source_format_invalid")
                continue
            accepted_formats = {
                "epub": frozenset({"epub", "epub3", "epub3fl", "flepub"}),
                "kepub": frozenset({"kepub"}),
                "pdf": frozenset({"pdf"}),
                "acsm": frozenset({"acsm"}),
            }
            if normalized_source.strip().casefold() not in accepted_formats.get(
                fmt, frozenset()
            ):
                skip("candidate_format_pair_unqualified")
                continue
        # A verified HTTPS acquisition link is not by itself proof of an
        # executable ADEPT/KDRM pipeline. Only conservative known paths.
        if (kind == "acsm" and handling == "adobe_adept"
                and raw_drm in adobe_raw and fmt == "acsm"):
            pass
        elif (kind == "http" and handling == "http_plain"
              and raw_drm in plain_raw and fmt in {"epub", "pdf", "kepub"}):
            pass
        elif (kind == "http" and handling == "kobo_kdrm"
              and _kdrm_raw_type(raw_drm) and fmt in {"epub", "kepub"}
              and kdrm_runtime_ready is True):
            # The pinned signed URL is NOT itself a readable EPUB. A delegated
            # Kobo content-access recipe and the DRM runtime are mandatory.
            pass
        else:
            skip("candidate_execution_route_unqualified")
            continue
        if source is not None and (type(source) is not str or len(source) > 128):
            skip("candidate_source_invalid")
            continue
        if _kdrm_raw_type(raw_drm) and not (
            source == "library_sync" or
            (source == "content_access_book" and kdrm_delegated_ready is True)
        ):
            # Unpinned replay is never authorized by KDRM-only capability.
            skip("kdrm_source_unsupported")
            continue
        # For KDRM the upstream wire-format affects the DRM recipe, so the
        # opaque offer must pin it as well. No change to legacy plain/ACSM IDs.
        route = (fmt + ":" + source_fmt.strip().lower()
                 if _kdrm_raw_type(raw_drm) else fmt)
        eligible.append((kind, handling, raw_drm, route + ":" + (source or "")))
    # Preserve a single proven plain, ACSM or library_sync route before
    # using the legacy delegated KDRM fallback. Do not choose between
    # multiple equally admissible routes by their upstream list order.
    def legacy_kdrm(row: tuple[str, str, str, str]) -> bool:
        return row[1] == "kobo_kdrm" and row[3].endswith(":content_access_book")

    preferred = [row for row in eligible if not legacy_kdrm(row)]
    if len(preferred) == 1 and all(
        row == preferred[0] or legacy_kdrm(row) for row in eligible
    ):
        eligible = preferred
    # Multiple independent routes remain ambiguous.
    if len(eligible) != 1:
        if diagnostic is not None:
            diagnostic["eligible_candidate_count"] = len(eligible)
        return reject(
            str(diagnostic.get("candidate_reason", "eligible_count_not_one"))
            if diagnostic is not None and len(candidates) == 1
            else "eligible_count_not_one"
        )
    fields = coordinates + eligible[0]
    # The opaque offer must also state WHICH Core fulfillment contract it
    # requires. KDRM always needs Core's one-shot runtime receipt; direct
    # HTTPS/ACSM must not accidentally enter that runtime-only branch. Both
    # routes bind the same full account/product/handling/format coordinates.
    prefix = ("kobo.runtime:" if eligible[0][1] == "kobo_kdrm"
              else "kobo.descriptor:")
    return prefix + hashlib.sha256(
        "\x00".join(fields).encode("utf-8")
    ).hexdigest()
