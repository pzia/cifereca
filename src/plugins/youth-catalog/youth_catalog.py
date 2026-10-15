from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from time import perf_counter
import re
import unicodedata
from typing import Iterable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit, urlunsplit

from cifereca.plugin_api.youth_ages import youth_age_from_label
from cifereca.plugin_api import description_text

_WS = re.compile(r"\s+")
_ISBN_CLEAN = re.compile(r"[^0-9Xx]")
AGE_KINDS = {"interest", "reading", "recommended", "publisher", "inferred"}

# Inventory placeholders are intentionally not claims of remote availability.
KNOWN_AGE_SOURCES = {
    "milan": "Éditions Milan",
    "ecole-loisirs": "L'École des loisirs",
}
KNOWN_BIBLIOGRAPHY_PLUGINS = {"youth.bibliography.openlibrary": "Open Library"}
COLLECTION_STATES = {
    "items", "empty_unverified", "invalid", "http_error",
    "timeout", "error", "cancelled",
}
_PUBLIC_SOURCE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,49}")
# Class names are provider-controlled (a dynamic exception type can include a
# token). Only these static, diagnostic-safe categories may reach diagnostics.
_PUBLIC_COLLECTION_ERROR_TYPES = frozenset({
    "RuntimeError", "ValueError", "TypeError", "KeyError", "OSError",
    "ConnectionError", "TimeoutError", "ReadTimeout", "ConnectTimeout",
    "PoolTimeout", "HTTPStatusError", "HTTPError", "ConnectError",
    "ReadError", "RemoteProtocolError", "InvalidCatalogueStructureError",
    "InvalidYouthSourcePage", "YouthSourceCancelled", "CancelledError",
    "ResponseError", "SourceError",
})


def public_source_id(value: object) -> str:
    """An opaque, non-personal plugin/source slug safe for public diagnostics."""
    return value if type(value) is str and _PUBLIC_SOURCE_ID.fullmatch(value) else ""



def _text(value: object) -> str:
    return _WS.sub(" ", str(value or "").strip())


def _fold(value: object) -> str:
    return _text(value).casefold()


def collection_failure_diagnostic(exc: BaseException) -> dict[str, object]:
    """Safe, typed classification of one failed explicit youth collection.

    Provider adapters attach an HTTP status to their RuntimeError without
    changing the public exception contract. Never parse exception messages:
    they may contain URLs, credentials or user data.
    """
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if type(code) is not int or not 100 <= code <= 599:
        code = getattr(exc, "http_status", None)
    status = code if type(code) is int and 100 <= code <= 599 else None
    raw_error_type = type(exc).__name__
    error_type = (
        raw_error_type if raw_error_type in _PUBLIC_COLLECTION_ERROR_TYPES
        else "SourceError"
    )
    state = (
        "cancelled" if error_type in {"YouthSourceCancelled", "CancelledError"}
        else "timeout" if error_type in {"TimeoutError", "ReadTimeout", "ConnectTimeout", "PoolTimeout"}
        else "http_error" if status is not None
        else "invalid" if error_type in {"InvalidCatalogueStructureError", "InvalidYouthSourcePage"}
        else "error"
    )
    # Only a directly attached per-call integer is authoritative; never read
    # a provider's shared last diagnostic (it may belong to another identity).
    result = {
        "state": state, "error_type": error_type, "http_status": status,
        "request_count": getattr(exc, "collection_request_count", None),
    }
    # Preserve the existing exception projection when no funnel exists.
    pipeline = getattr(exc, "collection_pipeline", None)
    if isinstance(pipeline, Mapping):
        result["pipeline"] = pipeline
    return result


def safe_ingest_rejection_categories(errors: Iterable[str]) -> dict[str, int]:
    """Aggregate ingestion failures into fixed public labels, not raw messages.

    A source record or plugin exception may contain private query parameters.
    Unknown failures are counted, never published or interpreted.
    """
    counts = {"missing_title": 0, "other": 0}
    for raw in errors:
        label = "missing_title" if isinstance(raw, str) and re.fullmatch(
            r"item\[\d+\]: title is required", raw
        ) else "other"
        counts[label] = min(100_000, counts[label] + 1)
    return counts


def sanitize_collection_pipeline(value: object) -> dict[str, object]:
    """Fixed-shape source funnel from the *same* explicit request, cache-only.

    Discard arbitrary source keys and values, even if a plugin sends raw HTML,
    a title, an ISBN, a signed URL or account metadata by mistake.
    """
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, object] = {}
    if value.get("query_mode") in ("remote", "local"):
        out["query_mode"] = value["query_mode"]
    counts = (
        "anchor_count", "product_link_count", "candidate_count",
        "analyzed_count", "isbn_valid_count", "age_published_count",
        "age_qualified_count", "age_unknown_count", "returned_items",
        # Catalogue cards from École des loisirs may have explicit published
        # ages and stable source references without ANY printed ISBN. Keep
        # their bounded counters in Diagnostics instead of dropping them.
        "cards_detected", "cards_complete", "missing_isbn",
        "covers_seen", "cover_urls_valid", "author_count",
        "thumbnail_card_count", "thumbnail_duplicate_count",
    )
    for key in counts:
        item = value.get(key)
        if type(item) is int and 0 <= item <= 100_000:
            out[key] = item
    limits = {
        "effective_limit": 500, "html_bytes": 2_000_000,
        "http_ms": 600_000, "parse_ms": 600_000,
        "total_ms": 600_000,
    }
    for key, cap in limits.items():
        item = value.get(key)
        if type(item) is int and 0 <= item <= cap:
            out[key] = item
    status = value.get("http_status")
    if type(status) is int and 100 <= status <= 599:
        out["http_status"] = status
    digest = value.get("html_sha256_prefix")
    if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{16}", digest):
        out["html_sha256_prefix"] = digest
    if type(value.get("truncated_by_limit")) is bool:
        out["truncated_by_limit"] = value["truncated_by_limit"]
    raw_reasons = value.get("rejection_reasons")
    reasons = {}
    if isinstance(raw_reasons, Mapping):
        for key in (
            "age_unattested", "isbn_invalid", "unsupported_product_type",
            "duplicate", "text_filter_excluded", "age_filter_excluded",
            "unexpected_markup", "title_missing",
        ):
            num = raw_reasons.get(key)
            if type(num) is int and 0 < num <= 100_000:
                reasons[key] = num
    if reasons:
        out["rejection_reasons"] = reasons
    return out


def source_yield_diagnostic(row: Mapping[str, object]) -> dict[str, object]:
    """Explain observed collection yield, never infer provider availability.

    Input is a cache-only, per-identity source_diagnostics projection. Never
    copy URLs, HTML, bibliographic metadata or identity IDs into the result.
    """
    state = str(row.get("collection_state") or "not_tested")
    family = str(row.get("family") or "age")
    pipeline = sanitize_collection_pipeline(row.get("pipeline"))
    accepted = row.get("accepted")
    rejected = row.get("rejected")
    counts = {
        key: pipeline[key] for key in (
            "anchor_count", "product_link_count", "candidate_count",
            "analyzed_count", "isbn_valid_count", "age_qualified_count",
            "age_unknown_count", "returned_items", "effective_limit",
            "cards_detected", "cards_complete", "missing_isbn",
            "covers_seen", "cover_urls_valid", "author_count",
            "thumbnail_card_count", "thumbnail_duplicate_count",
            "html_bytes", "http_ms", "parse_ms", "total_ms",
        ) if key in pipeline
    }
    for key, value in (("accepted", accepted), ("rejected", rejected),
                       ("received_count", row.get("received_count")),
                       ("request_count", row.get("request_count")),
                       ("requested_limit", row.get("requested_limit"))):
        if type(value) is int and 0 <= value <= 100_000:
            counts[key] = value
    reasons = dict(pipeline.get("rejection_reasons") or {})
    explanation = "non_teste"
    confidence = "unknown"
    if family == "bibliography":
        explanation = "bibliographie_sans_preuve_age"
    elif state in {"http_error", "timeout", "error", "cancelled"}:
        explanation = {
            "http_error": "http_echec", "timeout": "delai_depasse",
            "error": "transport_ou_source_echec", "cancelled": "annule",
        }[state]
        confidence = "observed"
    elif state == "invalid" or reasons.get("unexpected_markup", 0):
        explanation = "structure_invalide_ou_rejet"
        confidence = "observed" if state == "invalid" else "inferred"
    elif state == "items" and type(accepted) is int and accepted > 0:
        explanation = "notices_qualifiees"
        confidence = "observed"
    elif state == "empty_unverified":
        candidates = pipeline.get("candidate_count")
        anchors = pipeline.get("anchor_count")
        isbn = pipeline.get("isbn_valid_count")
        ages = pipeline.get("age_qualified_count")
        returned = pipeline.get("returned_items")
        if type(candidates) is int and candidates > 0:
            # ISBN can be legitimately absent (notably École des loisirs).
            # Zero ISBNs alone cannot prove why no notices were accepted.
            if reasons.get("isbn_invalid", 0):
                explanation = "candidats_avec_isbn_rejete"
            elif ages == 0:
                explanation = "candidats_sans_age_atteste"
            else:
                explanation = "perte_apres_qualification_a_examiner"
            confidence = "inferred"
        elif candidates == 0 and type(anchors) is int and anchors > 0:
            explanation = "ancres_sans_fiches_reconnues"
            confidence = "inferred"
        elif candidates == 0 and anchors == 0:
            explanation = "aucune_ancre_ni_fiche_reconnue"
            confidence = "inferred"
        elif type(returned) is int and returned > 0:
            explanation = "notices_exclues_apres_source"
            confidence = "inferred"
        else:
            explanation = "vide_non_verifie"
    return {
        "cause": explanation, "confidence": confidence,
        "counts": counts, "rejection_reasons": reasons,
        "state": state if state in COLLECTION_STATES | {"not_tested"} else "unknown",
        "family": family if family in {"age", "bibliography", "discovery"} else "unknown",
        "query_mode": pipeline.get("query_mode", "unknown"),
        "html_sha256_prefix": pipeline.get("html_sha256_prefix", ""),
        "truncated_by_limit": pipeline.get("truncated_by_limit") is True,
    }


def youth_source_support_snapshot(rows: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Bounded, public-only export; no identity, HTML or URL values."""
    records = []
    for row in rows:
        if len(records) >= 30:
            break
        if not isinstance(row, Mapping):
            continue
        source = public_source_id(row.get("source_id")) or "unknown"
        records.append({
            "source_id": source,
            "plugin_state": str(row.get("plugin_state") or "unknown")[:35]
                if str(row.get("plugin_state") or "unknown") in {
                    "ready", "disabled", "not_installed", "incompatible", "degraded"
                } else "unknown",
            "enabled": row.get("enabled") is True,
            "cache_count": min(100_000, max(0, int(row.get("cache_count") or 0)))
                if type(row.get("cache_count")) is int else None,
            **source_yield_diagnostic(row),
        })
    return {
        "report": "jeunesse_sources_cache_only",
        "scope": "sources_sans_identite",
        "sources": records,
        "limitations": "Etat cache local, sans vérification HTTP ni preuve d'âge déduite.",
    }


def _field(value: object, name: str, default: object = "") -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _iter_values(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    if isinstance(value, Mapping):
        return tuple(value.values())
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError:
        return (value,)


def _names(value: object) -> tuple[str, ...]:
    out: list[str] = []
    for raw in _iter_values(value):
        if isinstance(raw, Mapping):
            raw = raw.get("name") or raw.get("label") or ""
        for part in str(raw or "").split(";"):
            item = _text(part)
            if item and item not in out:
                out.append(item)
    return tuple(out)


def _tokens(value: object) -> tuple[str, ...]:
    out: list[str] = []
    for raw in _iter_values(value):
        if isinstance(raw, Mapping):
            raw = raw.get("name") or raw.get("label") or raw.get("value") or ""
        for part in re.split(r"[,;|]", str(raw or "")):
            item = _fold(part)
            if item and item not in out:
                out.append(item)
    return tuple(out)


def canonical_url(value: object) -> str:
    raw = _text(value)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return ""
    scheme = parts.scheme.casefold()
    if scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return ""
    host = parts.hostname.casefold()
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def normalize_isbn(value: object) -> str:
    raw = _ISBN_CLEAN.sub("", _text(value)).upper()
    if len(raw) == 10 and re.fullmatch(r"[0-9]{9}[0-9X]", raw):
        total = sum((10 - i) * int(d) for i, d in enumerate(raw[:9]))
        total += 10 if raw[-1] == "X" else int(raw[-1])
        if total % 11:
            return ""
        prefix = "978" + raw[:9]
        check = (10 - sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(prefix)) % 10) % 10
        return prefix + str(check)
    # A valid GTIN/EAN checksum does not make a product identifier an ISBN:
    # only Bookland 978/979 are ISBN-13 prefixes (e.g. toys use other EANs).
    if len(raw) == 13 and raw.isdigit() and raw.startswith(("978", "979")):
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(raw))
        return raw if total % 10 == 0 else ""
    return ""


def normalize_age_kind(value: object, *, label: str = "", inferred: bool = False) -> str:
    if inferred:
        return "inferred"
    raw = _fold(value)
    aliases = {
        "interest": "interest", "interest age": "interest", "âge d’intérêt": "interest", "age d'intérêt": "interest",
        "reading": "reading", "reading age": "reading", "âge de lecture": "reading", "age de lecture": "reading",
        "recommended": "recommended", "recommendation": "recommended", "âge recommandé": "recommended",
        "publisher": "publisher", "éditeur": "publisher", "editeur": "publisher",
        "inferred": "inferred", "infered": "inferred",
    }
    if raw in aliases:
        return aliases[raw]
    low = _fold(label)
    if "interest age" in low or "âge d’intérêt" in low or "age d'intérêt" in low:
        return "interest"
    if "reading age" in low or "âge de lecture" in low or "age de lecture" in low:
        return "reading"
    if "publisher" in low or "éditeur" in low or "editeur" in low:
        return "publisher"
    return "recommended"


@dataclass(frozen=True, slots=True)
class YouthAgeRange:
    age_min: int
    age_max: int | None = None
    age_kind: str = "recommended"
    source_label: str = ""
    source_id: str = ""
    confidence: float = 1.0
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.age_min < 18:
            raise ValueError("age_min must be between 0 and 17")
        if self.age_max is not None and not self.age_min <= self.age_max < 18:
            raise ValueError("age_max must be between age_min and 17")
        if self.age_kind not in AGE_KINDS:
            raise ValueError("invalid age_kind")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    def contains(self, age: int) -> bool:
        return 0 <= int(age) < 18 and int(age) >= self.age_min and (self.age_max is None or int(age) <= self.age_max)


def parse_age_label(
    label: object,
    *,
    source_id: str = "",
    age_kind: str = "",
    inferred: bool = False,
    confidence: float | None = None,
) -> tuple[YouthAgeRange, ...]:
    # Only full labels are evidence; English labels are translated to the
    # public parser's grammar, never extracted from prose.
    if not isinstance(label, str) or not 0 < len(label) <= 120:
        return ()
    if any(ord(char) < 32 or ord(char) == 127 for char in label):
        return ()
    text = _text(label)
    kind = normalize_age_kind(age_kind, label=text, inferred=inferred)
    try:
        conf = float(confidence) if confidence is not None else (0.55 if kind == "inferred" else 1.0)
    except (TypeError, ValueError):
        return ()
    from math import isfinite
    if not isfinite(conf) or not 0 <= conf <= 1:
        return ()
    normalized = youth_age_from_label(label, age_kind=kind, confidence=conf)
    if normalized is None:
        explicit = re.sub(r"^(?:interest age|reading age)\s+", "", text, flags=re.I)
        canonical = ""
        ranged = re.fullmatch(r"(?:ages?\s+)?(\d{1,2})\s*(?:[-–—]|to)\s*(\d{1,2})(?:\s+years?)?", explicit, re.I)
        lower = re.fullmatch(r"(?:from|ages? from)\s+(\d{1,2})\s+years?", explicit, re.I)
        plus = re.fullmatch(r"(\d{1,2})\s*(?:\+|years? and up)", explicit, re.I)
        upper = re.fullmatch(r"up to\s+(\d{1,2})\s+years?", explicit, re.I)
        if ranged:
            canonical = f"{ranged[1]}-{ranged[2]} ans"
        elif lower or plus:
            match = lower or plus
            canonical = f"{match[1]}+"
        elif upper:
            canonical = f"jusqu'à {upper[1]} ans"
        if canonical:
            normalized = youth_age_from_label(canonical, age_kind=kind, confidence=conf)
    if normalized is None:
        return ()
    lo = normalized.age_min if normalized.age_min is not None else 0
    hi = normalized.age_max
    if lo >= 18:
        return ()
    if hi is not None:
        hi = min(17, hi)
    sid = _fold(source_id)
    return (YouthAgeRange(
        age_min=lo, age_max=hi, age_kind=kind, source_label=label, source_id=sid,
        confidence=conf, provenance=tuple(filter(None, (sid,))),
    ),)


def age_range_from_structured(value: object, *, source_id: str = "") -> YouthAgeRange | None:
    if not isinstance(value, Mapping):
        return None
    label = _text(value.get("source_label") or value.get("label") or "")
    inferred = bool(value.get("inferred"))
    kind = normalize_age_kind(value.get("age_kind") or value.get("kind"), label=label, inferred=inferred)
    try:
        lo = int(value.get("age_min"))
    except (TypeError, ValueError):
        parsed = parse_age_label(label, source_id=source_id, age_kind=kind, inferred=inferred, confidence=value.get("confidence"))
        return parsed[0] if parsed else None
    try:
        raw_max = value.get("age_max")
        hi = int(raw_max) if raw_max not in (None, "") else None
        confidence = float(value.get("confidence", 0.55 if inferred else 1.0))
        sid = _fold(value.get("source_id") or source_id)
        return YouthAgeRange(lo, hi, kind, label, sid, confidence, tuple(filter(None, (sid,))))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class SourceInfo:
    source_id: str
    display_name: str
    homepage_url: str = ""


class YouthSource(Protocol):
    def source_info(self) -> object: ...
    def search(self, query: str, *, cursor: str | None = None, limit: int = 50) -> object: ...


def source_info(source: object) -> SourceInfo:
    method = getattr(source, "source_info", None)
    if not callable(method):
        raise TypeError("youth source must expose source_info()")
    raw = method()
    source_id = _fold(_field(raw, "source_id") or _field(raw, "id"))
    display_name = _text(_field(raw, "display_name") or _field(raw, "name"))
    homepage_url = canonical_url(_field(raw, "homepage_url") or _field(raw, "url"))
    if not source_id or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,127}", source_id):
        raise ValueError("invalid youth source_id")
    if not display_name:
        raise ValueError("youth source display_name is required")
    return SourceInfo(source_id, display_name, homepage_url)


def validate_source_contract(source: object) -> SourceInfo:
    info = source_info(source)
    if not callable(getattr(source, "search", None)):
        raise TypeError("youth source must expose search()")
    return info


@dataclass(frozen=True, slots=True)
class YouthBook:
    record_id: str
    source_id: str
    title: str
    authors: tuple[str, ...] = ()
    identifiers: Mapping[str, str] = field(default_factory=dict)
    notice_url: str = ""
    friendly_url: str = ""
    age_ranges: tuple[YouthAgeRange, ...] = ()
    language: str = ""
    formats: tuple[str, ...] = ()
    book_type: str = ""
    themes: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    publishers: tuple[str, ...] = ()
    published_year: int | None = None
    cover_url: str = ""
    bibliography_sources: tuple[str, ...] = ()
    collection: str = ""
    description: str = ""
    series: tuple[str, ...] = ()
    collections: tuple[str, ...] = ()
    awards: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LibraryNoticeEvidence:
    """A catalogue notice for this *edition*, not evidence of a copy or loan."""

    source_provider: str
    record_id: str
    isbn13: str
    notice_url: str
    status: str = "catalogue_reference"
    freshness: str = "unknown"


def _attested_public_notice_url(raw: object) -> str:
    """Only public, canonical HTTPS links without user/session parameters."""
    if not isinstance(raw, str) or len(raw) > 2048 or not raw.strip():
        return ""
    # Reject URL grammar ambiguities before presenting an attested public link.
    if (any(ord(ch) < 33 or ord(ch) == 127 for ch in raw)
            or any(ch in raw for ch in '<>\\?#')):
        return ""
    try:
        parts = urlsplit(raw)
        host = parts.hostname
        port = parts.port
        if (parts.scheme.lower() != "https" or not host
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or port not in (None, 443)
                or "%" in host):
            return ""
        host = host.lower().rstrip(".")
        if (not host or host == "localhost" or "." not in host
                or host.endswith((".local", ".localhost", ".internal",
                                   ".lan", ".home.arpa"))
                or any(not label for label in host.split("."))):
            return ""
        try:
            address = ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return ""
        # Never surface a search page in place of an edition notice.
        if not parts.path or parts.path == "/":
            return ""
        return urlunsplit(("https", host, parts.path, "", ""))
    except (TypeError, ValueError):
        return ""


def _notice_evidence_isbn(value: object) -> str:
    """Only a whole formatted ISBN can attest an edition-level match.

    General ingestion normalizes messy publisher strings, but an attested
    library lookup must not silently strip private text, URL parameters or
    a second identifier around 13 valid ISBN digits.
    """
    if type(value) is not str or not 9 <= len(value) <= 40:
        return ""
    # Spaces/hyphens are normal book typography; other characters are not
    # evidence of the identifier itself. ASCII only for canonical check.
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return ""
    raw = value.strip(" ")
    if not re.fullmatch(r"[0-9Xx][0-9Xx -]*[0-9Xx]", raw, re.ASCII):
        return ""
    return normalize_isbn(raw)


def attest_library_notices(
    book: YouthBook,
    results: Iterable[object],
    *,
    limit: int = 50,
) -> tuple[LibraryNoticeEvidence, ...]:
    """Project explicit, already-retrieved library rows into edition notices.

    No HTTP, no cache mutation, and no title/author inference. The caller
    selects the active identity and performs its explicit library search.
    A row must carry a matching strong ISBN *and* a direct-notice assertion;
    free-form provider text, stock labels and loan flags are never promoted.
    """
    if not isinstance(book, YouthBook):
        return ()
    identifiers = book.identifiers
    isbn = _notice_evidence_isbn(identifiers.get("isbn")) if isinstance(identifiers, Mapping) else ""
    if not isbn or type(limit) is not int or not 1 <= limit <= 100:
        return ()
    notices: list[LibraryNoticeEvidence] = []
    seen: set[tuple[str, str]] = set()
    for row_index, raw in enumerate(results):
        if row_index >= 500 or len(notices) >= limit:
            break
        metadata = _field(raw, "metadata", {})
        if not isinstance(metadata, Mapping):
            continue
        candidate_isbn = _notice_evidence_isbn(metadata.get("isbn13") or metadata.get("isbn"))
        if candidate_isbn != isbn or metadata.get("direct_notice_attested") is not True:
            continue
        source = _field(raw, "source_provider")
        record = _field(raw, "record_id")
        if (not isinstance(source, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,127}", source)
                or not isinstance(record, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", record)):
            continue
        url = _attested_public_notice_url(_field(raw, "friendly_url"))
        if not url or (source, record) in seen:
            continue
        seen.add((source, record))
        notices.append(LibraryNoticeEvidence(
            source_provider=source, record_id=record, isbn13=isbn,
            notice_url=url,
        ))
    return tuple(notices)


@dataclass(frozen=True, slots=True)
class BibliographyEnrichment:
    source_id: str
    isbn: str
    record_id: str = ""
    authors: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    publishers: tuple[str, ...] = ()
    published_year: int | None = None
    notice_url: str = ""
    cover_url: str = ""


@dataclass(frozen=True, slots=True)
class BibliographyReport:
    identity_id: str
    source_id: str
    isbn: str
    accepted: int = 0
    rejected: int = 0
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class YouthMatch:
    status: str
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IngestReport:
    identity_id: str
    source_id: str
    accepted: int = 0
    rejected: int = 0
    errors: tuple[str, ...] = ()
    next_cursor: str = ""
    fresh_until: str = ""


def _extract_age_ranges(raw: object, source_id: str) -> tuple[YouthAgeRange, ...]:
    rows: list[YouthAgeRange] = []
    for value in _iter_values(_field(raw, "age_ranges", ())):
        item = age_range_from_structured(value, source_id=source_id)
        if item is not None and item not in rows:
            rows.append(item)
    labels: list[tuple[str, object]] = []
    for name in ("age_label", "recommended_age", "reading_age", "interest_age", "publisher_age"):
        value = _field(raw, name, "")
        if value not in (None, ""):
            labels.append((name, value))
    metadata = _field(raw, "metadata", {})
    if isinstance(metadata, Mapping):
        for name in ("age_label", "recommended_age", "reading_age", "interest_age", "publisher_age"):
            value = metadata.get(name)
            if value not in (None, ""):
                labels.append((name, value))
    for name, value in labels:
        kind = {"reading_age": "reading", "interest_age": "interest", "publisher_age": "publisher"}.get(name, "recommended")
        for label in _iter_values(value):
            for item in parse_age_label(label, source_id=source_id, age_kind=kind):
                if item not in rows:
                    rows.append(item)
    return tuple(rows)


def _public_cover_url(value: object) -> str:
    """Covers are public HTTPS images, never signed/session URLs."""
    url = canonical_url(value)
    if not url:
        return ""
    parts = urlsplit(url)
    # Query strings may carry expiring tokens or user identifiers. The current
    # catalogue contract cannot prove which parameters are harmless.
    return url if parts.scheme == "https" and not parts.query else ""


def _publication_year(raw: object) -> int | None:
    """An attested source year/date only; no guesses from text or URL."""
    for name in ("published_year", "publication_year", "first_publish_year",
                 "publication_date", "published_at"):
        raw_value = _field(raw, name, None)
        if raw_value is None or isinstance(raw_value, bool):
            continue
        value = _text(raw_value)
        if re.fullmatch(r"[12][0-9]{3}", value):
            year = int(value)
        elif re.fullmatch(r"[12][0-9]{3}-[0-9]{2}-[0-9]{2}", value):
            try:
                from datetime import date
                year = date.fromisoformat(value).year
            except ValueError:
                continue
        else:
            continue
        if 1450 <= year <= 2100:
            return year
    return None


def _attested_field(raw: object, *names: str) -> object:
    # Only explicitly recognized public bibliographic keys, never a recursive
    # walk through arbitrary metadata that might contain account/session data.
    metadata = _field(raw, "metadata", {})
    for name in names:
        value = _field(raw, name, None)
        if value not in (None, "", (), [], {}):
            return value
        if isinstance(metadata, Mapping):
            value = metadata.get(name)
            if value not in (None, "", (), [], {}):
                return value
    return ""


def normalize_record(raw: object, *, source_id: str) -> YouthBook:
    title = _text(_field(raw, "title"))
    if not title:
        raise ValueError("title is required")
    authors = _names(_field(raw, "authors") or _field(raw, "author"))
    raw_ids = _field(raw, "identifiers", {})
    identifiers: dict[str, str] = {}
    if isinstance(raw_ids, Mapping):
        for key, value in raw_ids.items():
            if _text(value):
                identifiers[str(key).casefold()] = _text(value)
    for name in ("isbn", "isbn10", "isbn13"):
        value = _field(raw, name, "")
        isbn = normalize_isbn(value) if value else ""
        if isbn:
            identifiers["isbn"] = isbn
            break
    if "isbn" in identifiers:
        isbn = normalize_isbn(identifiers["isbn"])
        if isbn:
            identifiers["isbn"] = isbn
        else:
            identifiers.pop("isbn", None)
    source_item = _text(_field(raw, "source_item_id") or _field(raw, "item_id") or _field(raw, "record_id"))
    if source_item:
        identifiers.setdefault("source_item_id", source_item)
    sid = _fold(source_id)
    identifiers.setdefault("provider", sid)
    notice_url = canonical_url(_field(raw, "notice_url") or _field(raw, "canonical_url"))
    friendly_url = canonical_url(_field(raw, "friendly_url") or notice_url)
    record_id = _text(_field(raw, "record_id") or _field(raw, "item_id") or source_item or notice_url or title)
    return YouthBook(
        record_id=record_id, source_id=sid, title=title, authors=authors, identifiers=identifiers,
        notice_url=notice_url, friendly_url=friendly_url, age_ranges=_extract_age_ranges(raw, sid),
        language=_fold(_field(raw, "language") or _field(raw, "lang")),
        formats=_tokens(_field(raw, "formats") or _field(raw, "format")),
        book_type=_fold(_field(raw, "book_type") or _field(raw, "type")),
        themes=_tokens(_field(raw, "themes") or _field(raw, "subjects") or _field(raw, "subject")),
        provenance=(sid,),
        publishers=_names(_field(raw, "publishers") or _field(raw, "publisher")),
        published_year=_publication_year(raw),
        cover_url=_public_cover_url(_field(raw, "cover_url") or _field(raw, "thumbnail_url")),
        collection=_text(_field(raw, "collection"))[:200],
        description=(description_text(_attested_field(raw, "description", "summary", "synopsis")) or "")[:4000],
        series=_names(_attested_field(raw, "series", "series_name"))[:20],
        collections=_names(_attested_field(raw, "collections", "collection"))[:20],
        awards=_names(_attested_field(raw, "awards", "prizes", "literary_awards"))[:20],
    )


def normalize_bibliography_record(raw: object, *, source_id: str, expected_isbn: str) -> BibliographyEnrichment:
    metadata = _field(raw, "metadata", {})
    if not isinstance(metadata, Mapping) or metadata.get("bibliographic_only") is not True or _fold(metadata.get("age_evidence")) != "none":
        raise ValueError("bibliographic record must explicitly declare age_evidence=none")
    if tuple(_iter_values(_field(raw, "age_ranges", ()))) != ():
        raise ValueError("bibliographic record must not contain age ranges")
    expected = normalize_isbn(expected_isbn)
    if not expected:
        raise ValueError("valid ISBN is required")
    isbns = []
    for value in _iter_values(_field(raw, "isbns", ())):
        isbn = normalize_isbn(value)
        if isbn and isbn not in isbns:
            isbns.append(isbn)
    for name in ("isbn", "isbn10", "isbn13"):
        value = _field(raw, name, "")
        isbn = normalize_isbn(value) if value else ""
        if isbn and isbn not in isbns:
            isbns.append(isbn)
    if expected not in isbns:
        raise ValueError("bibliographic ISBN does not match the selected youth edition")
    try:
        year_raw = _field(raw, "first_publish_year", None)
        year = int(year_raw) if year_raw not in (None, "") else None
    except (TypeError, ValueError):
        year = None
    return BibliographyEnrichment(
        source_id=_fold(source_id),
        isbn=expected,
        record_id=_text(_field(raw, "record_id")),
        authors=_names(_field(raw, "authors") or _field(raw, "author")),
        languages=_tokens(_field(raw, "languages") or _field(raw, "language")),
        subjects=_tokens(_field(raw, "subjects") or _field(raw, "subject")),
        publishers=_names(_field(raw, "publishers") or _field(raw, "publisher")),
        published_year=year,
        notice_url=canonical_url(_field(raw, "notice_url")),
        cover_url=canonical_url(_field(raw, "cover_url")),
    )


def _apply_bibliography(book: YouthBook, enrichment: BibliographyEnrichment) -> YouthBook:
    isbn = normalize_isbn(book.identifiers.get("isbn", ""))
    if not isbn or isbn != enrichment.isbn:
        return book
    identifiers = dict(book.identifiers)
    if enrichment.record_id:
        identifiers.setdefault(f"{enrichment.source_id}_record_id", enrichment.record_id)
    language = book.language
    if not language and len(enrichment.languages) == 1:
        language = enrichment.languages[0]
    return replace(
        book,
        authors=tuple(dict.fromkeys(book.authors + enrichment.authors)),
        identifiers=identifiers,
        language=language,
        themes=tuple(dict.fromkeys(book.themes + enrichment.subjects)),
        publishers=tuple(dict.fromkeys(book.publishers + enrichment.publishers)),
        published_year=book.published_year or enrichment.published_year,
        cover_url=book.cover_url or enrichment.cover_url,
        bibliography_sources=tuple(dict.fromkeys(book.bibliography_sources + (enrichment.source_id,))),
    )


def bibliographic_match(left: YouthBook, right: YouthBook) -> YouthMatch:
    left_isbn = normalize_isbn(left.identifiers.get("isbn", ""))
    right_isbn = normalize_isbn(right.identifiers.get("isbn", ""))
    if left_isbn and left_isbn == right_isbn:
        return YouthMatch("confirmed", 100, (f"isbn:{left_isbn}",))
    if left.notice_url and left.notice_url == right.notice_url:
        return YouthMatch("confirmed", 90, ("notice-url",))
    lp, rp = _fold(left.identifiers.get("provider")), _fold(right.identifiers.get("provider"))
    li, ri = _text(left.identifiers.get("source_item_id")), _text(right.identifiers.get("source_item_id"))
    if lp and lp == rp and li and li == ri:
        return YouthMatch("confirmed", 85, ("source-item",))
    if _fold(left.title) == _fold(right.title) and _fold(left.title):
        la = {_fold(value) for value in left.authors if _fold(value)}
        ra = {_fold(value) for value in right.authors if _fold(value)}
        if la & ra:
            return YouthMatch("suggested", 60, ("title-exact", "author-exact"))
    return YouthMatch("none", 0, ())


def _merge(left: YouthBook, right: YouthBook) -> YouthBook:
    identifiers = dict(left.identifiers)
    for key, value in right.identifiers.items():
        identifiers.setdefault(key, value)
    return replace(
        left,
        authors=tuple(dict.fromkeys(left.authors + right.authors)),
        identifiers=identifiers,
        friendly_url=left.friendly_url or right.friendly_url,
        notice_url=left.notice_url or right.notice_url,
        age_ranges=tuple(dict.fromkeys(left.age_ranges + right.age_ranges)),
        language=left.language or right.language,
        formats=tuple(dict.fromkeys(left.formats + right.formats)),
        book_type=left.book_type or right.book_type,
        themes=tuple(dict.fromkeys(left.themes + right.themes)),
        provenance=tuple(dict.fromkeys(left.provenance + right.provenance)),
        # A matching ISBN is a confirmed edition, but empty fields in the
        # first provider must not hide bibliographic facts from the second.
        publishers=tuple(dict.fromkeys(left.publishers + right.publishers)),
        published_year=left.published_year or right.published_year,
        cover_url=left.cover_url or right.cover_url,
        collection=left.collection or right.collection,
        description=left.description or right.description,
        series=tuple(dict.fromkeys(left.series + right.series)),
        collections=tuple(dict.fromkeys(left.collections + right.collections)),
        awards=tuple(dict.fromkeys(left.awards + right.awards)),
        bibliography_sources=tuple(dict.fromkeys(
            left.bibliography_sources + right.bibliography_sources
        )),
    )


def merge_confirmed(records: Sequence[YouthBook]) -> tuple[YouthBook, ...]:
    groups: list[YouthBook] = []
    for record in records:
        for index, current in enumerate(groups):
            if bibliographic_match(current, record).status == "confirmed":
                groups[index] = _merge(current, record)
                break
        else:
            groups.append(record)
    return tuple(groups)


class InvalidYouthSourcePage(ValueError):
    """Structural provider response error; no raw payload in its message."""


def _extract_page(value: object) -> tuple[Iterable[object], str, int]:
    """Validate a provider page before replacing its last usable local cache.

    A missing/invalid items field is not an authoritative empty catalogue.
    Keep the historical iterable shorthand and numeric-string freshness.
    Never interpolate provider data into errors or diagnostics.
    """
    if isinstance(value, Mapping):
        if "items" in value:
            rows = value["items"]
        elif "results" in value:
            rows = value["results"]
        else:
            raise InvalidYouthSourcePage("missing items or results")
        if rows is None or isinstance(rows, (str, bytes, Mapping)):
            raise InvalidYouthSourcePage("items must be an iterable of records")
        try:
            iter(rows)
        except TypeError as exc:
            raise InvalidYouthSourcePage("items must be an iterable of records") from exc
        cursor_value = value.get("next_cursor", "")
        if cursor_value is None:
            cursor = ""
        elif (isinstance(cursor_value, str) and len(cursor_value) <= 512
              and all(32 <= ord(ch) < 127 or ord(ch) >= 160 for ch in cursor_value)):
            cursor = cursor_value
        else:
            raise InvalidYouthSourcePage("invalid pagination cursor")
        freshness = value.get("fresh_for_seconds", 0)
        if freshness is None or freshness == "":
            fresh = 0
        elif type(freshness) is int and 0 <= freshness <= 31_536_000:
            fresh = freshness
        elif (isinstance(freshness, str) and freshness.isascii()
              and freshness.isdecimal() and len(freshness) <= 8
              and int(freshness) <= 31_536_000):
            fresh = int(freshness)
        else:
            raise InvalidYouthSourcePage("invalid freshness metadata")
        return rows, cursor, fresh
    if value is None or isinstance(value, (str, bytes)):
        raise InvalidYouthSourcePage("invalid source response")
    try:
        iter(value)
    except TypeError as exc:
        raise InvalidYouthSourcePage("invalid source response") from exc
    return value, "", 0


_SEARCH_STOPWORDS = frozenset({
    "l", "d", "de", "du", "des", "la", "le", "les", "un", "une", "au", "aux",
    "a", "et", "en", "sur", "dans", "par", "pour", "avec", "the", "of",
})


def _search_fold(value: object) -> str:
    """Accent, punctuation and apostrophe insensitive matching, local only."""
    text = unicodedata.normalize("NFKD", _text(value).casefold())
    unaccented = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^\w]+", " ", unaccented, flags=re.UNICODE).strip()


def _search_score(book: YouthBook, query: str) -> int:
    """Strict meaningful tokens across attested fields; weighted, deterministic."""
    if not isinstance(query, str) or not query.strip() or len(query) > 256:
        return 0
    phrases = [
        _search_fold(match.group(1) or match.group(2))
        for match in re.finditer(r'"([^"]{2,80})"|«([^»]{2,80})»', query)
    ]
    cleaned = _search_fold(query)
    raw_tokens = tuple(dict.fromkeys(cleaned.split()))
    tokens = tuple(token for token in raw_tokens if token not in _SEARCH_STOPWORDS) or raw_tokens
    if not tokens or len(tokens) > 24:
        return 0
    fields = (
        (book.title, 100),
        *((value, 75) for value in book.authors[:12]),
        *((value, 68) for value in book.series[:12]),
        *((value, 65) for value in book.collections[:12]),
        *((value, 63) for value in book.awards[:12]),
        *((value, 60) for value in book.themes[:24]),
        *((value, 45) for value in book.publishers[:12]),
        (book.description[:4000], 24),
        *((str(value), 35) for value in (
            book.identifiers.get("isbn", ""),
            book.identifiers.get("source_item_id", ""),
            book.record_id,
        )),
        (book.book_type, 15),
        (book.language, 15),
        *((value, 12) for value in book.formats[:10]),
        *((value, 10) for value in book.provenance[:10]),
        (str(book.published_year or ""), 10),
        *((row.source_label, 10) for row in book.age_ranges[:10]
          if row.age_kind != "inferred"),
    )
    normalized = tuple((text, weight) for value, weight in fields
                       if (text := _search_fold(value)))
    if not normalized:
        return 0
    if phrases and any(not any(phrase in value for value, _ in normalized)
                       for phrase in phrases):
        return 0
    score = 0
    for token in tokens:
        best = max((weight for value, weight in normalized
                    if any(word.startswith(token) for word in value.split())), default=0)
        if not best:
            return 0
        score += best
    if cleaned and cleaned in _search_fold(book.title):
        score += 150
    elif phrases:
        score += 30
    return score


class YouthCatalogIndex:
    def __init__(self) -> None:
        self._sources: dict[str, dict[str, tuple[YouthBook, ...]]] = {}
        self._bibliography: dict[str, dict[str, dict[str, BibliographyEnrichment]]] = {}
        self._merged: dict[str, tuple[YouthBook, ...]] = {}
        self._fresh_until: dict[tuple[str, str], str] = {}
        # Session-local per-identity collection outcomes. Raw error strings,
        # notices, URLs and credentials are never stored here.
        self._collection_health: dict[str, dict[str, dict[str, object]]] = {}

    @staticmethod
    def identity_key(identity_id: object) -> str:
        return _text(identity_id) or "default"

    def ingest(
        self,
        identity_id: str,
        source_id: str,
        rows: Iterable[object],
        *,
        replace_source: bool = True,
        fresh_for_seconds: int = 0,
    ) -> IngestReport:
        identity = self.identity_key(identity_id)
        sid = _fold(source_id)
        if not sid:
            raise ValueError("source_id is required")
        accepted: list[YouthBook] = []
        errors: list[str] = []
        for index, raw in enumerate(rows or ()):
            try:
                accepted.append(normalize_record(raw, source_id=sid))
            except Exception:
                # Provider rows and exception text are untrusted. A raw error
                # can contain signed URLs, credentials or private searches.
                title = raw.get("title") if type(raw) is dict else object()
                kind = ("title is required"
                        if title is None or (type(title) is str and not title.strip())
                        else "invalid record")
                errors.append(f"item[{index}]: {kind}")
        # Reject an entirely malformed refresh atomically: never replace
        # the last usable cache with zero valid records and parsing failures.
        # A genuinely empty (error-free) page is still an authoritative result.
        if errors and not accepted:
            return IngestReport(
                identity, sid, 0, len(errors), tuple(errors),
                fresh_until=self.fresh_until(identity, sid),
            )
        per_source = self._sources.setdefault(identity, {})
        per_source[sid] = tuple(accepted) if replace_source else per_source.get(sid, ()) + tuple(accepted)
        self._merged.pop(identity, None)
        fresh_until = ""
        if fresh_for_seconds > 0:
            until = datetime.now(timezone.utc) + timedelta(seconds=int(fresh_for_seconds))
            fresh_until = until.replace(microsecond=0).isoformat()
            self._fresh_until[(identity, sid)] = fresh_until
        elif replace_source:
            # A successful authoritative refresh without freshness must not
            # inherit an obsolete TTL from the previous response.
            self._fresh_until.pop((identity, sid), None)
        return IngestReport(identity, sid, len(accepted), len(errors), tuple(errors), fresh_until=fresh_until)

    def ingest_bibliography(
        self,
        identity_id: str,
        source_id: str,
        isbn: object,
        rows: Iterable[object],
    ) -> BibliographyReport:
        identity = self.identity_key(identity_id)
        sid = _fold(source_id)
        expected = normalize_isbn(isbn)
        if not sid or not expected:
            return BibliographyReport(identity, sid, expected, rejected=1, errors=("source_id and valid ISBN are required",))
        accepted: list[BibliographyEnrichment] = []
        errors: list[str] = []
        for index, raw in enumerate(rows or ()):
            try:
                accepted.append(normalize_bibliography_record(raw, source_id=sid, expected_isbn=expected))
            except Exception:
                # Never include provider/bibliography exception payloads in a
                # cache report, even when the library page does not display it.
                errors.append(f"item[{index}]: invalid bibliography record")
        unique = {item.record_id or item.notice_url or repr(item): item for item in accepted}
        if len(unique) > 1:
            return BibliographyReport(identity, sid, expected, rejected=len(accepted), errors=("ambiguous bibliography result",))
        if unique:
            self._bibliography.setdefault(identity, {}).setdefault(expected, {})[sid] = next(iter(unique.values()))
            self._merged.pop(identity, None)
        return BibliographyReport(identity, sid, expected, accepted=1 if unique else 0, rejected=len(errors), errors=tuple(errors))

    def record_collection(
        self,
        identity_id: str,
        source_id: str,
        *,
        state: str,
        accepted: int = 0,
        rejected: int = 0,
        received_count: int | None = None,
        requested_limit: int | None = None,
        request_count: int | None = None,
        pipeline: Mapping[str, object] | None = None,
        rejection_categories: Mapping[str, object] | None = None,
        duration_ms: int = 0,
        next_cursor_present: bool = False,
        error_type: str = "",
        http_status: int | None = None,
        stage: str = "",
    ) -> None:
        """Record an explicitly requested collection, without source payloads.

        The per-identity cache and outcome are session-local. The caller must
        not pass an exception string, title, URL, raw HTML or account detail.
        """
        identity = _text(identity_id)
        sid = _fold(source_id)
        if not identity or not sid:
            return
        if state not in COLLECTION_STATES:
            raise ValueError("unknown youth collection state")
        previous = self._collection_health.get(identity, {}).get(sid, {})
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        event: dict[str, object] = {
            "state": state,
            "stage": stage if stage in {"search", "ingest", "listing", "parsing", "products", "complete"} else "",
            "attempted_at_utc": now,
            "last_success_at_utc": (
                now if state in {"items", "empty_unverified"}
                else previous.get("last_success_at_utc", "")
            ),
            "accepted": max(0, min(int(accepted), 100_000)),
            "rejected": max(0, min(int(rejected), 100_000)),
            # None means unknown, never silently claim that zero rows or zero
            # network requests were observed after a transport failure.
            "received_count": (
                max(0, min(received_count, 100_000))
                if type(received_count) is int else None
            ),
            "requested_limit": (
                max(1, min(requested_limit, 500))
                if type(requested_limit) is int else None
            ),
            "request_count": (
                max(0, min(request_count, 100_000))
                if type(request_count) is int and request_count >= 0 else None
            ),
            "pipeline": sanitize_collection_pipeline(pipeline),
            "rejection_categories": {
                key: max(0, min(value, 100_000))
                for key in ("missing_title", "other")
                for value in (
                    (rejection_categories or {}).get(key, 0)
                    if isinstance(rejection_categories, Mapping) else 0,
                )
                if type(value) is int and value > 0
            },
            "duration_ms": max(0, min(int(duration_ms), 600_000)),
            "next_cursor_present": bool(next_cursor_present),
            "error_type": (
                error_type
                if type(error_type) is str and error_type in _PUBLIC_COLLECTION_ERROR_TYPES
                else ""
            ),
            "http_status": (
                int(http_status)
                if isinstance(http_status, int) and 100 <= http_status <= 599
                else None
            ),
        }
        self._collection_health.setdefault(identity, {})[sid] = event

    def source_diagnostics(
        self,
        identity_id: str,
        inventory: Iterable[Mapping[str, object]] = (),
    ) -> tuple[dict[str, object], ...]:
        """Read-only, network-free overview, including never-used sources.

        Inventory comes from the public diagnostics broker. A ready/active
        plugin must still read 'not_tested' until explicit acquisition occurs.
        Bibliography and age providers are never conflated.
        """
        identity = _text(identity_id)
        if not identity:
            return ()
        providers: dict[str, dict[str, object]] = {
            sid: {
                "source_id": sid, "plugin_id": f"youth.source.{sid}",
                "label": label, "family": "age",
                "plugin_state": "not_installed", "enabled": False,
                "package_id": "",
            } for sid, label in KNOWN_AGE_SOURCES.items()
        }
        for pid, label in KNOWN_BIBLIOGRAPHY_PLUGINS.items():
            providers[pid] = {
                "source_id": pid, "plugin_id": pid, "label": label,
                "family": "bibliography", "plugin_state": "not_installed",
                "enabled": False, "package_id": "",
            }
        for row in inventory:
            if not isinstance(row, Mapping):
                continue
            typ = _text(row.get("type"))
            if typ not in {"youth.source", "youth.bibliography"}:
                continue
            pid = _text(row.get("plugin_id"))
            if not pid or len(pid) > 128:
                continue
            sid = pid.removeprefix("youth.source.") if typ == "youth.source" else pid
            if typ == "youth.source" and not pid.startswith("youth.source."):
                continue
            if typ == "youth.bibliography" and not pid.startswith("youth.bibliography."):
                continue
            # Diagnostics are exported/copyable. Do not promote malformed
            # runtime identifiers (emails, URLs, signed queries) to UI labels.
            if not public_source_id(sid):
                continue
            record = providers.setdefault(sid, {
                "source_id": sid, "plugin_id": pid, "label": sid,
                "family": "age" if typ == "youth.source" else "bibliography",
            })
            package = _text(row.get("package_id"))
            record.update({
                "plugin_state": _text(row.get("state"))[:40] or "unknown",
                "enabled": bool(row.get("enabled")),
                "package_id": (
                    package if public_source_id(package) else ""
                ),
            })
        cached = self._sources.get(identity, {})
        recent = self._collection_health.get(identity, {})
        for sid in set(cached) | set(recent):
            if not public_source_id(sid):
                continue
            if sid not in providers:
                providers[sid] = {
                    "source_id": sid, "plugin_id": f"youth.source.{sid}",
                    "label": sid, "family": "age",
                    "plugin_state": "not_installed", "enabled": False,
                    "package_id": "",
                }
        out = []
        for sid, entry in sorted(providers.items(), key=lambda pair: (pair[1]["family"], pair[1]["label"])):
            event = dict(recent.get(sid, {})) if entry["family"] == "age" else {}
            out.append({
                **entry,
                "collection_state": event.get("state", "not_tested"),
                "collection_stage": event.get("stage", ""),
                "attempted_at_utc": event.get("attempted_at_utc", ""),
                "last_success_at_utc": event.get("last_success_at_utc", ""),
                "accepted": event.get("accepted", 0),
                "rejected": event.get("rejected", 0),
                "received_count": event.get("received_count"),
                "requested_limit": event.get("requested_limit"),
                "request_count": event.get("request_count"),
                "pipeline": {
                    **event.get("pipeline", {}),
                    **({
                        "rejection_reasons": dict(event["pipeline"]["rejection_reasons"])
                    } if isinstance(event.get("pipeline"), Mapping)
                         and isinstance(event["pipeline"].get("rejection_reasons"), Mapping)
                       else {}),
                },
                "rejection_categories": dict(event.get("rejection_categories", {})),
                "duration_ms": event.get("duration_ms", 0),
                "next_cursor_present": event.get("next_cursor_present", False),
                "http_status": event.get("http_status"),
                "error_type": event.get("error_type", ""),
                "cache_count": len(cached.get(sid, ())) if entry["family"] == "age" else 0,
                "fresh_until": self.fresh_until(identity, sid) if entry["family"] == "age" else "",
            })
        return tuple(out)

    def fresh_until(self, identity_id: str, source_id: str) -> str:
        return self._fresh_until.get((self.identity_key(identity_id), _fold(source_id)), "")

    def entries(self, identity_id: str) -> tuple[YouthBook, ...]:
        identity = self.identity_key(identity_id)
        if identity not in self._merged:
            rows = [record for records in self._sources.get(identity, {}).values() for record in records]
            merged = list(merge_confirmed(rows))
            overlays = self._bibliography.get(identity, {})
            for index, book in enumerate(merged):
                isbn = normalize_isbn(book.identifiers.get("isbn", ""))
                if not isbn:
                    continue
                for enrichment in overlays.get(isbn, {}).values():
                    book = _apply_bibliography(book, enrichment)
                merged[index] = book
            self._merged[identity] = tuple(merged)
        return self._merged[identity]

    def find(
        self,
        identity_id: str,
        *,
        isbn: object = "",
        provider: object = "",
        source_item_id: object = "",
        notice_url: object = "",
        limit: int = 20,
    ) -> tuple[YouthBook, ...]:
        identity = self.identity_key(identity_id)
        wanted_isbn = normalize_isbn(isbn)
        wanted_provider = _fold(provider)
        wanted_item = _text(source_item_id)
        wanted_notice = canonical_url(notice_url)
        bounded = max(1, min(int(limit), 100))

        if not any((wanted_isbn, wanted_provider and wanted_item, wanted_notice)):
            return ()

        out: list[YouthBook] = []
        for book in self.entries(identity):
            identifiers = dict(book.identifiers or {})
            matched = False
            if wanted_isbn and normalize_isbn(identifiers.get("isbn", "")) == wanted_isbn:
                matched = True
            elif wanted_provider and wanted_item:
                if (
                    _fold(identifiers.get("provider") or book.source_id) == wanted_provider
                    and _text(identifiers.get("source_item_id") or book.record_id) == wanted_item
                ):
                    matched = True
            if not matched and wanted_notice:
                urls = {
                    canonical_url(book.notice_url),
                    canonical_url(book.friendly_url),
                }
                if wanted_notice in urls:
                    matched = True
            if matched:
                out.append(book)
                if len(out) >= bounded:
                    break
        return tuple(out)

    def search(
        self,
        identity_id: str,
        query: str = "",
        *,
        age: int | None = None,
        age_kind: str = "",
        source_id: str = "",
        language: str = "",
        format: str = "",
        book_type: str = "",
        themes: Iterable[str] = (),
        include_inferred: bool = False,
    ) -> tuple[YouthBook, ...]:
        q, source, lang, fmt, typ = _text(query), _fold(source_id), _fold(language), _fold(format), _fold(book_type)
        kind = normalize_age_kind(age_kind) if age_kind else ""
        wanted_themes = {_fold(value) for value in themes if _fold(value)}
        ranked: list[tuple[int, int, YouthBook]] = []
        for position, book in enumerate(self.entries(identity_id)):
            score = _search_score(book, q) if q else 0
            if q and not score:
                continue
            if source and source not in book.provenance:
                continue
            if lang and book.language != lang:
                continue
            if fmt and fmt not in book.formats:
                continue
            if typ and book.book_type != typ:
                continue
            if wanted_themes and not wanted_themes.issubset(set(book.themes)):
                continue
            if age is not None:
                matches = [
                    rng for rng in book.age_ranges
                    if rng.contains(int(age)) and (not kind or rng.age_kind == kind)
                    and (include_inferred or rng.age_kind != "inferred")
                ]
                if not matches:
                    continue
            elif kind and not any(rng.age_kind == kind for rng in book.age_ranges):
                continue
            ranked.append((score, position, book))
        if q:
            ranked.sort(key=lambda hit: (-hit[0], hit[1]))
        return tuple(book for _score, _position, book in ranked)

    def clear_identity(self, identity_id: str) -> None:
        identity = self.identity_key(identity_id)
        self._sources.pop(identity, None)
        self._bibliography.pop(identity, None)
        self._merged.pop(identity, None)
        self._collection_health.pop(identity, None)
        for key in [key for key in self._fresh_until if key[0] == identity]:
            self._fresh_until.pop(key, None)


class YouthCatalogPlugin:
    @staticmethod
    def attested_library_notices(
        book: YouthBook, results: Iterable[object], *, limit: int = 50,
    ) -> tuple[LibraryNoticeEvidence, ...]:
        """Project only explicit, edition-matching library notice evidence."""
        return attest_library_notices(book, results, limit=limit)

    def __init__(self) -> None:
        self.index = YouthCatalogIndex()
        self.context: object | None = None

    def activate(self, context: object) -> None:
        self.context = context

    def deactivate(self) -> None:
        self.context = None

    def ingest(self, identity_id: str, source_id: str, rows: Iterable[object], **options: object) -> IngestReport:
        return self.index.ingest(identity_id, source_id, rows, **options)

    def search_cached(self, identity_id: str, query: str = "", **filters: object) -> tuple[YouthBook, ...]:
        """Search cached rows only. Never calls a remote source."""
        return self.index.search(identity_id, query, **filters)

    def record_collection(self, identity_id: str, source_id: str, **result: object) -> None:
        """Record only sanitized results of an explicit UI collection."""
        self.index.record_collection(identity_id, source_id, **result)

    def source_diagnostics(
        self, identity_id: str, inventory: Iterable[Mapping[str, object]] = (),
    ) -> tuple[dict[str, object], ...]:
        """Cache-only, identity-scoped snapshot suitable for Diagnostics/Jeunesse."""
        return self.index.source_diagnostics(identity_id, inventory)

    def find_cached(
        self,
        identity_id: str,
        *,
        isbn: object = "",
        provider: object = "",
        source_item_id: object = "",
        notice_url: object = "",
        limit: int = 20,
    ) -> tuple[YouthBook, ...]:
        """Find cached rows by strong identifiers only. Never uses title/author or network."""
        return self.index.find(
            identity_id,
            isbn=isbn,
            provider=provider,
            source_item_id=source_item_id,
            notice_url=notice_url,
            limit=limit,
        )

    def ingest_bibliography(
        self, identity_id: str, source_id: str, isbn: object, rows: Iterable[object],
    ) -> BibliographyReport:
        """Merge already-fetched bibliographic metadata; never performs network I/O."""
        return self.index.ingest_bibliography(identity_id, source_id, isbn, rows)

    def collect_source(
        self,
        identity_id: str,
        source: object,
        query: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
        replace_source: bool = True,
    ) -> IngestReport:
        """Collect one chosen source and record its outcome for this identity.

        Reading source_diagnostics() never calls search(). Error codes in
        IngestReport are fixed, never raw provider text or source payloads.
        """
        try:
            info = validate_source_contract(source)
        except Exception as exc:
            return IngestReport(self.index.identity_key(identity_id), "", rejected=1, errors=("invalid source contract",))
        bounded = max(1, min(int(limit), 500))
        started = perf_counter()
        stage = "search"
        try:
            raw = source.search(_text(query), cursor=cursor, limit=bounded)  # type: ignore[attr-defined]
            stage = "ingest"
            rows, next_cursor, fresh = _extract_page(raw)
            report = self.index.ingest(
                identity_id, info.source_id, rows,
                replace_source=replace_source, fresh_for_seconds=fresh,
            )
            report = replace(report, next_cursor=next_cursor)
            self.index.record_collection(
                identity_id, info.source_id,
                state=(
                    "items" if report.accepted else
                    "invalid" if report.rejected else
                    "empty_unverified"
                ),
                stage=stage,
                accepted=report.accepted, rejected=report.rejected,
                received_count=report.accepted + report.rejected,
                requested_limit=bounded,
                request_count=(
                    raw.get("collection_request_count") if isinstance(raw, Mapping)
                    else None
                ),
                pipeline=raw.get("collection_pipeline") if isinstance(raw, Mapping) else None,
                rejection_categories=safe_ingest_rejection_categories(report.errors),
                next_cursor_present=bool(next_cursor),
                duration_ms=max(0, int((perf_counter() - started) * 1000)),
            )
            return report
        except InvalidYouthSourcePage:
            # A malformed response must not replace the previous cache or
            # claim a successful empty page. Preserve only an attested count
            # from this invocation; never read the adapter's shared snapshot.
            self.index.record_collection(
                identity_id, info.source_id, state="invalid",
                stage="parsing", rejected=1, requested_limit=bounded,
                request_count=(
                    raw.get("collection_request_count") if isinstance(raw, Mapping)
                    else None
                ),
                pipeline=raw.get("collection_pipeline") if isinstance(raw, Mapping) else None,
                duration_ms=max(0, int((perf_counter() - started) * 1000)),
            )
            return IngestReport(
                self.index.identity_key(identity_id), info.source_id,
                rejected=1, errors=("invalid source page",),
            )
        except Exception as exc:
            failure = collection_failure_diagnostic(exc)
            self.index.record_collection(
                identity_id, info.source_id, **failure,
                stage=stage, rejected=1, requested_limit=bounded,
                duration_ms=max(0, int((perf_counter() - started) * 1000)),
            )
            return IngestReport(
                self.index.identity_key(identity_id), info.source_id,
                rejected=1, errors=("source search failed",),
            )

PLUGIN = YouthCatalogPlugin()
