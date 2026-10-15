"""Specialized Youth search page. Rendering reads the local youth cache only."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from itertools import islice
from math import isfinite
from unicodedata import category
import ipaddress
import re
from time import perf_counter
from urllib.parse import parse_qsl, unquote, urlsplit

from cifereca.plugin_api import (
    BookRef,
    DetailField,
    DetailSection,
    DocumentLink,
    UiActionContribution,
    UiBookDetailPresentation,
    UiBookDetailAction,
    UiBookListEntry,
    UiPageContribution,
)
from youth_catalog import (InvalidYouthSourcePage, YouthAgeRange, _extract_page,
                           collection_failure_diagnostic, normalize_isbn,
                           safe_ingest_rejection_categories,
                           source_yield_diagnostic, youth_source_support_snapshot,
                           KNOWN_AGE_SOURCES, KNOWN_BIBLIOGRAPHY_PLUGINS,
                           public_source_id)


KIND_LABELS = {
    "": "Tous les types d’âge",
    "recommended": "Âge recommandé",
    "interest": "Âge d’intérêt",
    "reading": "Âge de lecture",
    "publisher": "Âge éditeur",
    "inferred": "Âge inféré",
}
KIND_VALUES = tuple(KIND_LABELS)
_SPLIT = re.compile(r"[,;]")
# Returned only if the explicit source task was cancelled before HTTP.
_CANCELLED_BEFORE_REQUEST = object()


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _fold(value: object) -> str:
    return _text(value).casefold()


def public_url(value: object) -> str:
    """Public notice/cover URL only; never render a local or signed link.

    This is a display boundary, not proof that a library holds an edition.
    """
    if not isinstance(value, str) or not value or len(value) > 2048:
        return ""
    if any(ord(ch) < 33 or ord(ch) == 127 for ch in value):
        return ""
    if any(ch in value for ch in '<>\\'):
        return ""
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return ""
    if (parts.scheme not in {"http", "https"} or not host
            or parts.username is not None or parts.password is not None
            or parts.fragment or "%" in host):
        return ""
    if port not in (None, 443 if parts.scheme == "https" else 80):
        return ""
    hostname = host.rstrip(".").casefold()
    if (hostname in {"localhost", "local", "internal"}
            or hostname.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa"))):
        return ""
    # A DNS name must not conceal a loopback alias (127.1, 0177.0.0.1),
    # nor a Unicode alternate dot. Use only ordinary DNS labels/punycode.
    if (not hostname.isascii() or len(hostname) > 253
            or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                       for part in hostname.split("."))):
        return ""
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if "." not in hostname or hostname.rsplit(".", 1)[-1].isdigit():
            return ""
    else:
        if not address.is_global:
            return ""

    # Decode only for *rejecting* unsafe links, never to rewrite an URL or
    # infer acquisition rights. Double-encoded control bytes or path segments
    # must not become a public detail/cover link downstream.
    forbidden_segments = {
        "download", "downloads", "acquire", "acquisition",
        "license", "licence", "licenses", "licences", "drm",
        "token", "tokens", "oauth", "authorize", "authorization",
    }
    decoded_path, decoded_query = parts.path, parts.query
    for _ in range(4):
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded_path + decoded_query):
            return ""
        path_parts = (part.casefold() for part in decoded_path.split("/") if part)
        if any(
            part in forbidden_segments
            or part.endswith((".acsm", ".lcpl", ".epub", ".kepub"))
            for part in path_parts
        ):
            return ""
        parameters = parse_qsl(decoded_query, keep_blank_values=True)
        if len(parameters) > 32:
            return ""
        for key, val in parameters:
            field = re.sub(r"[^a-z0-9]", "", key.casefold())
            if any(word in field for word in (
                    "token", "secret", "password", "session", "auth",
                    "signature", "apikey", "accesskey", "credential",
                    "jwt", "csrf", "signed", "policy", "expires",
            )) or field in {"sig", "key"} or field.startswith(("xamz", "xgoog")):
                return ""
            if "@" in val or re.search(r"\bbearer\s", val, re.I):
                return ""
        path_next, query_next = unquote(decoded_path), unquote(decoded_query)
        if (path_next, query_next) == (decoded_path, decoded_query):
            return value
        decoded_path, decoded_query = path_next, query_next
    # Uninspected nesting of percent-encodings is not a public attestation.
    return ""


def age_text(age_range: object) -> str:
    lo = getattr(age_range, "age_min", None)
    hi = getattr(age_range, "age_max", None)
    if lo == 0 and hi is not None:
        bounds = f"jusqu’à {hi} ans"
    elif lo is not None and hi is None:
        bounds = f"à partir de {lo} ans"
    elif lo is not None and hi == lo:
        bounds = f"{lo} ans"
    elif lo is not None and hi is not None:
        bounds = f"{lo}–{hi} ans"
    elif hi is not None:
        bounds = f"jusqu’à {hi} ans"
    else:
        bounds = "âge inconnu"
    raw_kind = getattr(age_range, "age_kind", "")
    kind = KIND_LABELS.get(raw_kind if type(raw_kind) is str else "", "Âge")
    return f"{kind} · {bounds}"


def age_detail(age_range: object) -> str:
    parts = [age_text(age_range)]
    source_id = public_source_id(getattr(age_range, "source_id", ""))
    label = _public_age_string(getattr(age_range, "source_label", ""))
    confidence = getattr(age_range, "confidence", None)
    if source_id:
        parts.append(f"source {source_id}")
    if label:
        parts.append(f"« {label} »")
    if (type(confidence) in (int, float) and isfinite(confidence)
            and 0 <= confidence <= 1):
        parts.append(f"confiance {round(float(confidence) * 100)} %")
    return " · ".join(parts)


def _public_age_string(value: object) -> str:
    """Only visible, bounded text can document a public age attestation."""
    if type(value) is not str or len(value) > 180 or any(
        category(char) in {"Cc", "Cf", "Cs"} for char in value
    ):
        return ""
    return _text(value)


def _public_age_ranges(book: object) -> tuple[YouthAgeRange, ...]:
    """Do not mistake malformed cache iterables for attested age brackets."""
    raw = getattr(book, "age_ranges", ())
    if type(raw) not in (list, tuple):
        return ()
    attested = []
    for row in raw[:32]:
        if type(row) is not YouthAgeRange:
            continue
        lo, hi = row.age_min, row.age_max
        if (type(lo) is not int or not 0 <= lo < 18
                or (hi is not None and
                    (type(hi) is not int or not lo <= hi < 18))
                or type(row.age_kind) is not str
                or row.age_kind not in {"recommended", "interest", "reading", "publisher", "inferred"}):
            continue
        attested.append(row)
    return tuple(attested)


def book_key(book: object) -> str:
    return f"{_text(getattr(book, 'source_id', 'source'))}:{_text(getattr(book, 'record_id', 'record'))}"


_PUBLIC_YOUTH_IDENTIFIER_KEYS = frozenset({
    "isbn", "isbn10", "isbn13", "provider", "source_item_id",
    "doi", "ean", "issn", "oclc", "lccn",
})


def _public_identifier_value(value: object) -> str:
    """No signed URLs, credentials, provider objects or control bytes in IDs."""
    if (
        type(value) is not str or not 1 <= len(value) <= 256
        or any(not 33 <= ord(char) <= 126 for char in value)
        or any(char in value for char in "?&#@\\%")
    ):
        return ""
    return value


def _safe_book_identifiers(book: object) -> dict[str, str]:
    """Use only public bibliographic coordinates in detail/host BookRef.

    The source ingest accepts arbitrary identifier keys for its own cache,
    but a cache key named auth/session/token is not public bibliographic
    evidence and must not escape through a BookRef or edition detail.
    """
    raw = getattr(book, "identifiers", None)
    if not isinstance(raw, Mapping):
        return {}
    try:
        fields = dict(raw)
    except Exception:
        # A custom/restored Mapping may fail iteration or indexed lookup;
        # its error payload is not an attestation or a public diagnostic.
        return {}
    result: dict[str, str] = {}
    for key, value in fields.items():
        if type(key) is not str or key not in _PUBLIC_YOUTH_IDENTIFIER_KEYS:
            continue
        clean = _public_identifier_value(value)
        if not clean:
            continue
        if key == "provider" and not public_source_id(clean):
            continue
        if key in {"isbn", "isbn10", "isbn13"} and not normalize_isbn(clean):
            continue
        result[key] = clean
    return result


def book_ref(book: object) -> BookRef:
    identifiers = _safe_book_identifiers(book)
    return BookRef(
        book_id=_text(getattr(book, "record_id", "")),
        title=_text(getattr(book, "title", "")),
        # BookRef is handed to the host for navigation: apply the same public
        # author projection as the list and detail before crossing that API.
        authors=_public_authors(book),
        identifiers=identifiers,
        metadata={
            "youth_age_ranges": tuple(
                {
                    "age_min": row.age_min,
                    "age_max": row.age_max,
                    "age_kind": row.age_kind,
                    "source_label": _public_age_string(row.source_label),
                    "source_id": public_source_id(row.source_id),
                    "confidence": (
                        row.confidence
                        if type(row.confidence) in (int, float)
                        and isfinite(row.confidence)
                        and 0 <= row.confidence <= 1 else None
                    ),
                }
                for row in _public_age_ranges(book)
            )
        },
    )


def age_badge(age_range: object) -> str:
    """Short age chip: preserve the precise kind and provenance in the detail."""
    lo = getattr(age_range, "age_min", None)
    hi = getattr(age_range, "age_max", None)
    if lo is None and hi is None:
        return "Âge inconnu"
    if lo is None:
        bounds = f"≤{hi}"
    elif hi is None:
        bounds = f"{lo}+"
    elif lo == hi:
        bounds = str(lo)
    else:
        bounds = f"{lo}–{hi}"
    raw_kind = getattr(age_range, "age_kind", "")
    kind = raw_kind if type(raw_kind) is str else ""
    prefix = {
        "interest": "Intérêt ",
        "reading": "Lecture ",
        "publisher": "Éditeur ",
        "inferred": "Inféré ",
    }.get(kind, "")
    return f"{prefix}{bounds} ans"


def edition_reference(book: object) -> tuple[str, str, str]:
    """Bibliographic evidence first; a source-local reference is not an ISBN.

    Labels are explicit so same-title editions stay distinguishable without
    inferring an edition or merging by author/title.
    """
    identifiers = _safe_book_identifiers(book)
    isbn = normalize_isbn(identifiers.get("isbn"))
    if isbn:
        return "isbn", "ISBN", isbn
    source = public_source_id(getattr(book, "source_id", ""))
    # A signed URL or account token is not a source-local edition reference.
    local_id = identifiers.get("source_item_id") or _public_identifier_value(
        getattr(book, "record_id", "")
    )
    if source and local_id:
        return "source-reference", "Référence source", f"{source}:{local_id}"
    return "", "", ""


def _public_youth_values(raw: object, *, limit: int = 32) -> tuple[str, ...]:
    """Only bounded, visible bibliographic values, never mapping field keys.

    The cache can restore malformed provider metadata. Iterating a mapping
    would publish internal attribute names as attested authors or sources;
    a str subclass can be split into characters. Neither is evidence.
    """
    if type(raw) is str:
        raw = (raw,)
    elif isinstance(raw, (Mapping, str)):
        return ()
    values = []
    try:
        for value in islice(iter(raw if raw is not None else ()), limit):
            if type(value) is not str or any(
                category(char) in {"Cc", "Cf", "Cs"} for char in value
            ):
                continue
            cleaned = _text(value)
            if 0 < len(cleaned) <= 180:
                values.append(cleaned)
    except Exception:
        # A malformed cache iterable or provider object must not leak its
        # exception text into a public row or suppress independent values.
        pass
    return tuple(values)


def _public_authors(book: object) -> tuple[str, ...]:
    """One shared safe author projection for list and detail."""
    return _public_youth_values(getattr(book, "authors", ()))


def list_entry(book: object) -> UiBookListEntry:
    ranges = _public_age_ranges(book)
    age_summary = " · ".join(age_text(row) for row in ranges[:2])
    language = _text(getattr(book, "language", ""))
    formats = _public_youth_values(getattr(book, "formats", ()))
    source = ", ".join(_public_youth_values(getattr(book, "provenance", ()), limit=3))
    _, reference_label, reference_value = edition_reference(book)
    edition = f"{reference_label} {reference_value}" if reference_value else ""
    # Lead with edition evidence: the narrow (~440px) GTK master pane
    # truncates long metadata from the end, not from the beginning.
    meta = " · ".join(filter(None, (edition, age_summary, language, ", ".join(formats), source)))
    badge = age_badge(ranges[0]) if ranges else "Âge inconnu"
    return UiBookListEntry(
        key=book_key(book),
        title=_text(getattr(book, "title", "")),
        subtitle=", ".join(_public_authors(book)),
        meta=meta,
        badge=badge,
        thumbnail_url=public_url(getattr(book, "cover_url", "")) or None,
    )


def _mapped_youth_entries(books) -> tuple[dict[str, object], tuple[UiBookListEntry, ...]]:
    """Keep every cached notice selectable even when source-local IDs collide.

    A provider may expose two different editions with the same source ID and
    record ID. The list and detail lookup must agree on one unique *UI* key;
    synthetic suffixes are never fed back to the provider or book reference.
    Reserve future raw keys first so an earlier generated suffix cannot
    overwrite a genuine later record ID.
    """
    snapshot = tuple(books)
    original_keys = tuple(book_key(book) for book in snapshot)
    reserved = set(original_keys)
    indexed: dict[str, object] = {}
    entries = []
    for index, (book, raw_key) in enumerate(zip(snapshot, original_keys)):
        key = raw_key
        if key in indexed:
            suffix = index
            key = f"{raw_key}:{suffix}"
            while key in indexed or key in reserved:
                suffix += 1
                key = f"{raw_key}:{suffix}"
        indexed[key] = book
        entries.append(replace(list_entry(book), key=key))
    return indexed, tuple(entries)


def detail_presentation(book: object) -> UiBookDetailPresentation:
    ranges = _public_age_ranges(book)
    age_fields = tuple(
        DetailField(f"age-{index}", KIND_LABELS.get(_fold(getattr(row, "age_kind", "")), "Âge"), age_detail(row))
        for index, row in enumerate(ranges)
    )
    if not age_fields:
        age_fields = (DetailField("age-empty", "Âge", "Aucune tranche d’âge attestée en cache."),)
    metadata_fields = []
    title = _text(getattr(book, "title", ""))
    if title:
        metadata_fields.append(DetailField("title", "Titre", title, kind="title"))
    authors = _public_authors(book)
    for index, author in enumerate(authors):
        metadata_fields.append(DetailField(f"author-{index}", "Auteur", author, kind="author"))
    reference_id, reference_label, reference_value = edition_reference(book)
    if reference_value:
        metadata_fields.append(DetailField(reference_id, reference_label, reference_value, kind="isbn" if reference_id == "isbn" else "copy"))
    for index, source in enumerate(_public_youth_values(getattr(book, "provenance", ()))):
        metadata_fields.append(DetailField(f"source-{index}", "Source", source, kind="copy"))
    language = _text(getattr(book, "language", ""))
    if language:
        metadata_fields.append(DetailField("language", "Langue", language, kind="language"))
    formats = _public_youth_values(getattr(book, "formats", ()))
    for index, cleaned in enumerate(formats):
        metadata_fields.append(DetailField(f"format-{index}", "Format", cleaned, kind="all"))
    book_type = _text(getattr(book, "book_type", ""))
    if book_type:
        metadata_fields.append(DetailField("type", "Type", book_type, kind="all"))
    themes = _public_youth_values(getattr(book, "themes", ()))
    for index, cleaned in enumerate(themes):
        metadata_fields.append(DetailField(f"theme-{index}", "Thème", cleaned, kind="subject"))
    publishers = _public_youth_values(getattr(book, "publishers", ()))
    for index, cleaned in enumerate(publishers):
        metadata_fields.append(DetailField(f"publisher-{index}", "Éditeur", cleaned, kind="all"))
    published_year = getattr(book, "published_year", None)
    if published_year:
        metadata_fields.append(DetailField("published-year", "Première publication", str(published_year), kind="copy"))
    collection = _text(getattr(book, "collection", ""))
    if collection:
        metadata_fields.append(DetailField("collection", "Collection", collection, kind="all"))
    description = _text(getattr(book, "description", ""))
    if description:
        metadata_fields.append(DetailField("summary", "Résumé éditeur", description, kind="copy"))
    bibliography_sources = _public_youth_values(getattr(book, "bibliography_sources", ()))
    for index, cleaned in enumerate(bibliography_sources):
        metadata_fields.append(DetailField(
            f"bibliography-source-{index}", "Source bibliographique", cleaned, kind="copy"
        ))
    # An unsafe friendly link must not hide an independently safe notice URL.
    link = (public_url(getattr(book, "friendly_url", ""))
            or public_url(getattr(book, "notice_url", "")))
    links = (DocumentLink("notice", "Voir la notice publique", link),) if link else ()
    sections = [DetailSection("ages", "Âges attestés", age_fields)]
    if metadata_fields:
        sections.append(DetailSection("metadata-detail", "Détail", tuple(metadata_fields), order=1000))
    return UiBookDetailPresentation(
        title=_text(getattr(book, "title", "")),
        subtitle=", ".join(authors),
        thumbnail_url=public_url(getattr(book, "cover_url", "")) or None,
        badges=tuple(age_text(row) for row in ranges[:3]),
        sections=tuple(sections),
        links=links,
    )


def page_parts(value: object) -> tuple[tuple[object, ...], str, int]:
    """Use the catalog's strict structural contract, without silent empties."""
    rows, cursor, fresh = _extract_page(value)
    try:
        return tuple(rows), cursor, fresh
    except (TypeError, ValueError) as exc:
        raise InvalidYouthSourcePage("invalid items sequence") from exc


def source_info(row: object) -> tuple[str, str]:
    service = getattr(row, "service", row)
    method = getattr(service, "source_info", None)
    try:
        raw = method() if callable(method) else {}
    except Exception:
        raw = {}
    if not isinstance(raw, Mapping):
        raw = {}
    source_id = _fold(raw.get("source_id") or raw.get("id") or getattr(row, "plugin_id", ""))
    label = _text(raw.get("display_name") or raw.get("name") or source_id)
    return source_id, label


class YouthSourceCancelled(RuntimeError):
    """User-cancelled explicit collection; no raw source data in diagnostics."""

    def __init__(self, request_count: object = None):
        super().__init__("Youth source collection cancelled")
        self.collection_request_count = request_count


def fetch_source_page(
    service: object,
    query: str,
    *,
    age: int | None,
    themes: tuple[str, ...],
    cursor: str | None,
    limit: int = 100,
    cancelled: Callable[[], bool] | None = None,
) -> object:
    """Explicit source call with cooperative cancellation at network boundaries.

    Synchronous HTTPX cannot abort an already-running GET here. If cancellation
    happens while it runs, discard its returned rows *before* ingestion. Only
    keep an HTTP count attested by this exact return value or raised exception.
    """
    if cancelled is not None and cancelled():
        raise YouthSourceCancelled(0)
    try:
        browse = getattr(service, "browse", None)
        cooperative_browse = getattr(service, "browse_with_cancellation", None)
        if cancelled is not None and callable(cooperative_browse):
            # Opt-in only: legacy providers keep their existing browse API.
            result = cooperative_browse(
                age=age, themes=themes, text_filter=_text(query),
                cursor=cursor, limit=max(1, min(int(limit), 200)),
                cancelled=cancelled,
            )
        elif callable(browse):
            result = browse(
                age=age,
                themes=themes,
                text_filter=_text(query),
                cursor=cursor,
                limit=max(1, min(int(limit), 200)),
            )
        else:
            search = getattr(service, "search", None)
            if not callable(search):
                raise TypeError("source jeunesse incompatible : search() absent")
            result = search(_text(query), cursor=cursor, limit=max(1, min(int(limit), 200)))
    except Exception as exc:
        if cancelled is not None and cancelled():
            raise YouthSourceCancelled(
                getattr(exc, "collection_request_count", None)
            ) from None
        raise
    if cancelled is not None and cancelled():
        raise YouthSourceCancelled(
            result.get("collection_request_count") if isinstance(result, Mapping)
            else None
        )
    return result


def fetch_bibliography(service: object, isbn: object) -> tuple[object, ...]:
    normalized = normalize_isbn(isbn)
    if not normalized:
        raise ValueError("ISBN valide requis")
    lookup = getattr(service, "lookup_isbn", None)
    if not callable(lookup):
        raise TypeError("service bibliographique incompatible : lookup_isbn() absent")
    rows = lookup(normalized)
    if rows is None or isinstance(rows, (str, bytes, Mapping)):
        return ()
    try:
        return tuple(rows)
    except TypeError:
        return ()


def parse_themes(value: object) -> tuple[str, ...]:
    out = []
    for part in _SPLIT.split(_text(value)):
        item = _fold(part)
        if item and item not in out:
            out.append(item)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class FilterState:
    query: str = ""
    age: int | None = None
    age_kind: str = ""
    source_id: str = ""
    language: str = ""
    format: str = ""
    book_type: str = ""
    themes: tuple[str, ...] = ()
    include_inferred: bool = False


class YouthEnterSequence:
    """Two deliberate Enter activations; reset on any filter/identity change."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._token = None
        self._stage = 0

    def advance(self, *, token: object, query: str, source_selected: bool) -> str:
        if token != self._token:
            self._token = token
            self._stage = 0
        if self._stage == 0:
            self._stage = 1
            return "local"
        if not source_selected:
            return "choose_source"
        if len(_text(query)) < 2:
            return "short_query"
        if self._stage == 1:
            self._stage = 2
            return "remote"
        return "already_requested"


def source_diagnostic_presentation(rows):
    """Plain-text, network-free status view; no identity or source payloads."""
    sources = tuple(row for row in rows if isinstance(row, Mapping))
    if not sources:
        return "Aucun profil actif", ("Sélectionnez un profil pour consulter son cache Jeunesse.",)
    age_count = sum(row.get("family") == "age" for row in sources)
    bibliography_count = sum(row.get("family") == "bibliography" for row in sources)
    failures = {"invalid", "http_error", "timeout", "error", "cancelled"}
    error_count = sum(row.get("collection_state") in failures for row in sources)
    headline = (
        f"{age_count} source(s) d’âge · {bibliography_count} bibliographie"
        f" · {error_count} dernier(s) essai(s) en échec"
    )
    state_labels = {
        "not_tested": "non testée",
        "items": "collecte avec notices",
        "empty_unverified": "collecte vide (non vérifiée)",
        "invalid": "réponse/rejets invalides",
        "http_error": "erreur HTTP",
        "timeout": "délai dépassé",
        "error": "erreur de collecte",
        "cancelled": "collecte annulée",
    }
    lines = []
    for row in sources:
        # Runtime-provided labels may contain private URLs, account names or
        # signed queries. Render canonical names or public source slugs only.
        sid = public_source_id(row.get("source_id"))
        name = (KNOWN_AGE_SOURCES.get(sid)
                or KNOWN_BIBLIOGRAPHY_PLUGINS.get(sid)
                or sid or "Source inconnue")
        plugin_state = _text(row.get("plugin_state"))
        active = (
            "non installée" if plugin_state == "not_installed"
            else "désactivée" if not row.get("enabled")
            else "activée" if plugin_state == "ready"
            else "dégradée/incompatible"
        )
        if row.get("family") == "bibliography":
            lines.append(f"{name} · bibliographie uniquement (pas de preuve d’âge) · {active}")
            continue
        state = str(row.get("collection_state") or "not_tested")
        outcome = state_labels.get(state, "état inconnu")
        http_status = row.get("http_status")
        if state == "http_error" and isinstance(http_status, int) and 100 <= http_status <= 599:
            outcome += f" {http_status}"
        count = max(0, int(row.get("cache_count") or 0))
        line = f"{name} · {active} · dernier essai : {outcome} · cache : {count} notice(s)"
        stage = row.get("collection_stage")
        if stage in {"search", "ingest", "listing", "parsing", "products", "complete"}:
            line += f" · étape : {stage}"
        error_type = row.get("error_type")
        if state in failures and isinstance(error_type, str) and error_type.isidentifier() and len(error_type) <= 64:
            line += f" · exception : {error_type}"
        if state in failures and count:
            line += " (conservé)"
        if row.get("attempted_at_utc"):
            line += f" · essai UTC : {str(row['attempted_at_utc'])[:16].replace('T', ' ')}"
        if row.get("fresh_until") and count:
            line += f" · fraîcheur annoncée jusqu’au {str(row['fresh_until'])[:16].replace('T', ' ')} UTC"
        requests = row.get("request_count")
        if type(requests) is int and requests >= 0:
            line += f" · requêtes HTTP : {requests}"
        received = row.get("received_count")
        requested = row.get("requested_limit")
        funnel = row.get("pipeline")
        funnel = funnel if isinstance(funnel, Mapping) else {}
        if type(received) is int and received >= 0:
            line += f" · reçues : {received}"
            if type(requested) is int and requested > 0:
                line += f" · limite demandée : {requested} (pas un total de notices disponibles)"
        mode = funnel.get("query_mode")
        if mode == "remote":
            line += " · recherche distante (une page)"
        elif mode == "local":
            line += " · filtre local (catalogue partiel)"
        source_limit = funnel.get("effective_limit")
        if type(source_limit) is int and 1 <= source_limit <= 500:
            line += f" · borne source : {source_limit}"
        evidence = (
            ("http_status", "HTTP"),
            ("html_bytes", "HTML octets"),
            ("anchor_count", "ancres"),
            ("product_link_count", "liens produit"),
            ("candidate_count", "candidats"),
            ("analyzed_count", "examinés"),
            ("isbn_valid_count", "ISBN valides"),
            ("age_qualified_count", "âges attestés"),
            ("age_unknown_count", "âges inconnus"),
            ("returned_items", "notices source"),
            ("cards_detected", "cartes catalogue"),
            ("cards_complete", "cartes qualifiées"),
            ("missing_isbn", "notices sans ISBN publié"),
            ("covers_seen", "images présentes"),
            ("cover_urls_valid", "couvertures publiques valides"),
            ("author_count", "mentions d’auteur"),
            ("thumbnail_card_count", "vignettes secondaires"),
            ("thumbnail_duplicate_count", "vignettes d’éditions déjà présentes"),
        )
        for key, label in evidence:
            amount = funnel.get(key)
            if type(amount) is int and 0 <= amount <= 2_000_000:
                line += f" · {label} : {amount}"
        digest = funnel.get("html_sha256_prefix")
        if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{16}", digest):
            line += f" · HTML hash : {digest}"
        for key, label in (
            ("http_ms", "HTTP ms"), ("parse_ms", "parsing ms"),
            ("total_ms", "source ms"),
        ):
            value = funnel.get(key)
            if type(value) is int and 0 <= value <= 600_000:
                line += f" · {label} : {value}"
        if funnel.get("truncated_by_limit") is True:
            line += " · limite source atteinte"
        safe_reasons = funnel.get("rejection_reasons")
        if isinstance(safe_reasons, Mapping):
            cause_labels = {
                "age_unattested": "sans âge publié",
                "isbn_invalid": "ISBN non valide",
                "unsupported_product_type": "hors livre",
                "duplicate": "doublons",
                "text_filter_excluded": "exclus par titre",
                "age_filter_excluded": "exclus par âge",
                "unexpected_markup": "structure inconnue",
                "title_missing": "sans titre",
            }
            for key, label in cause_labels.items():
                n = safe_reasons.get(key)
                if type(n) is int and 0 < n <= 100_000:
                    line += f" · {label} : {n}"
        if int(row.get("rejected") or 0) > 0:
            line += f" · {int(row['rejected'])} rejet(s)"
            reasons = row.get("rejection_categories")
            if isinstance(reasons, Mapping):
                labels = []
                if type(reasons.get("missing_title")) is int and reasons["missing_title"] > 0:
                    labels.append(f"{reasons['missing_title']} sans titre")
                if type(reasons.get("other")) is int and reasons["other"] > 0:
                    labels.append(f"{reasons['other']} autres")
                if labels:
                    line += " (" + ", ".join(labels) + ")"
        diagnostic = source_yield_diagnostic(row)
        cause = diagnostic["cause"]
        if cause != "not_teste":
            line += " · rendement : " + str(cause).replace("_", " ")
            if diagnostic["confidence"] == "inferred":
                line += " (hypothèse, à vérifier)"
        if diagnostic["truncated_by_limit"]:
            line += " · collecte partielle (limite de source)"
        lines.append(line)
    return headline, tuple(lines)


class YouthPagePlugin:
    def activate(self, context):
        self.context, self.view, self.pending = context, None, None
        self.pending_identity = ""
        return self

    def pages(self):
        return [UiPageContribution(
            "youth",
            "Jeunesse",
            "system-search-symbolic",
            35,
            self.build,
            actions=(UiActionContribution(
                "youth.from-book",
                "Rechercher en Jeunesse",
                self.from_book,
                target="book.detail",
                visible_when=lambda context: context.book is not None,
            ),),
        )]

    def from_book(self, host):
        action = host.action_context()
        self.pending = action.book
        # The origin identity is an attested action-context coordinate, not
        # a title/ISBN match with the currently visible Youth profile.
        self.pending_identity = str(getattr(action, "identity_id", "") or "")
        if self.view and self.view.seed(
            self.pending, identity_id=self.pending_identity,
        ):
            self.pending = None
            self.pending_identity = ""
        host.navigate("youth")

    def build(self, host):
        from gi.repository import Gtk, GLib
        self.view = YouthView(self, host, Gtk, GLib)
        return self.view.widget

    def deactivate(self):
        if self.view:
            self.view.dispose()
        self.view = self.pending = None
        self.pending_identity = ""


def youth_action_states(*, identity, source, page, book, isbn, bibliography, local_detail, busy):
    """Network-free UI affordances with explicit failure reasons (#923)."""
    if not identity:
        collect = "Sélectionnez un profil de bibliothèque."
    elif not source:
        collect = "Choisir une source Jeunesse précise pour lancer une collecte."
    elif busy:
        collect = "Collecte déjà en cours pour cette source."
    else:
        collect = ""
    return {
        "Collecter cette source": (not collect, collect or "Collecte réseau uniquement sur clic explicite."),
        "Plus de résultats": (
            bool(identity and source and page and not busy),
            "Page suivante disponible pour la source choisie."
            if identity and source and page and not busy else
            "Aucune page suivante attestée pour cette source et ces filtres.",
        ),
        "Enrichir la bibliographie": (
            bool(identity and book and isbn and bibliography and not busy),
            "Lancer l’enrichissement pour cet ISBN." if identity and book and isbn and bibliography and not busy
            else "Sélectionnez un livre avec ISBN valide et activez une source bibliographique.",
        ),
        "Ouvrir la fiche Bibliothèque": (
            bool(identity and book and local_detail),
            "Ouvre uniquement une fiche locale reconnue dans ce profil ; aucune disponibilité distante garantie."
            if identity and book and local_detail else
            "Sélectionnez un livre Jeunesse ; l’ouverture exige une correspondance locale non ambiguë.",
        ),
    }


class YouthView:
    def __init__(self, plugin, host, Gtk, GLib):
        self.plugin, self.host, self.Gtk, self.GLib = plugin, host, Gtk, GLib
        self.catalog = plugin.context.service("catalog.youth")
        self.rows, self.selected_book = {}, None
        self.generation, self.task, self.disposed = 0, None, False
        self._reloading_identities = False
        self._profile_selection_required = False
        self.enter_sequence = YouthEnterSequence()
        self._remote_running_token = None
        self.next_cursor, self.next_source_plugin = "", ""
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.status = Gtk.Label(
            label="Cache local uniquement. Une collecte distante nécessite le bouton « Collecter cette source ».",
            wrap=True, xalign=0,
        )
        self.widget.append(self.status)

        self.identities = self._identity_rows()
        self.identity = Gtk.DropDown.new_from_strings([
            str(row.get("label") or row.get("name") or row.get("identity_id") or "Profil")
            for row in self.identities
        ])
        self.identity.set_tooltip_text("Choisit le profil dont le cache Jeunesse est affiché. Aucun changement d’identité implicite.")
        # Remember which profile owns the currently rendered cache and actions.
        self._last_cache_identity = self.identity_id()
        # GTK emits transient selections while the identity model changes.
        # No cache query or task may run under a temporary other profile.
        self.identity.connect(
            "notify::selected",
            lambda *_: None if self._reloading_identities else self.identity_changed(),
        )
        identity_label = Gtk.Label(label="_Profil", use_underline=True, xalign=0)
        identity_label.set_mnemonic_widget(self.identity)
        self.widget.append(identity_label)
        self.widget.append(self.identity)

        filters = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5)
        self.widget.append(filters)
        self.query = Gtk.Entry(placeholder_text="Titre, auteur ou thème — cache local")
        self.age = Gtk.DropDown.new_from_strings(["Tous les âges"] + [f"{age} ans" for age in range(18)])
        self.age_kind = Gtk.DropDown.new_from_strings([KIND_LABELS[value] for value in KIND_VALUES])
        self.source = Gtk.DropDown.new_from_strings(["Toutes les sources"])
        self.source.set_tooltip_text("Filtre le cache Jeunesse ; « Toutes les sources » ne lance aucune collecte.")
        self.language = Gtk.Entry(placeholder_text="Langue (fr, en…)")
        self.format = Gtk.Entry(placeholder_text="Format (epub, pdf…)")
        self.book_type = Gtk.Entry(placeholder_text="Type (fiction, documentaire…)")
        self.themes = Gtk.Entry(placeholder_text="Thèmes, séparés par des virgules")
        self.inferred = Gtk.CheckButton(label="Inclure les âges inférés", active=False)
        # Visible names persist after typing; Gtk.Label mnemonic relations also
        # expose the controls to keyboard navigation and assistive technology.
        controls = (
            ("_Recherche", self.query, "Filtre le cache local par titre, auteur ou thème. Première Entrée : cache ; deuxième Entrée : collecte de la source choisie uniquement."),
            ("_Âge", self.age, "Filtre les tranches d’âge connues de 0 à 17 ans ; les notices sans âge attesté peuvent être exclues."),
            ("_Type d’âge", self.age_kind, "Distingue âge recommandé, d’intérêt, de lecture ou indiqué par l’éditeur."),
            ("_Source", self.source, "Filtre l’origine des notices en cache. Choisir une source précise pour la collecter ; toutes les sources ne déclenche pas de réseau."),
            ("_Langue", self.language, "Code de langue des notices, par exemple fr ou en."),
            ("_Format", self.format, "Format connu, par exemple epub ou pdf. Inconnu ne signifie pas absent."),
            ("T_ype de livre", self.book_type, "Catégorie de notice, par exemple fiction ou documentaire."),
            ("_Thèmes", self.themes, "Filtre les thèmes renseignés ; séparer plusieurs thèmes par une virgule."),
        )
        for caption, control, help_text in controls:
            field = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            label = Gtk.Label(label=caption, use_underline=True, xalign=0)
            label.set_mnemonic_widget(control)
            control.set_tooltip_text(help_text)
            field.append(label)
            field.append(control)
            filters.append(field)
        self.inferred.set_tooltip_text(
            "Inclut les estimations d’âge dans le filtre ; désactivé, seuls les âges attestés sont retenus."
        )
        filters.append(self.inferred)
        for index in range(len(controls) + 1):
            child = filters.get_child_at_index(index)
            if child is not None:
                child.set_focusable(False)
        for entry in (self.query, self.language, self.format, self.book_type, self.themes):
            entry.connect("changed", lambda *_: self.filters_changed())
        self.query.connect("activate", lambda *_: self.query_activated())
        for dropdown in (self.age, self.age_kind, self.source):
            dropdown.connect("notify::selected", lambda *_: self.filters_changed())
        self.inferred.connect("toggled", lambda *_: self.filters_changed())

        actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE)
        self.widget.append(actions)
        self._action_buttons = {}
        actions.append(self.button("Collecter cette source", self.collect))
        # Keep the four legacy single-source actions in _action_buttons:
        # E2E and public affordance tests depend on that stable registry.
        self.federated_button = Gtk.Button(
            label="Rechercher dans toutes les sources Jeunesse",
        )
        self.federated_button.set_tooltip_text(
            "Réseau seulement au clic : 8 sources au plus, 2 connexions, 2 pages de 50 notices."
        )
        self.federated_button.connect("clicked", lambda *_: self.collect_federated())
        actions.append(self.federated_button)
        actions.append(self.button("Plus de résultats", self.more))
        actions.append(self.button("Enrichir la bibliographie", self.enrich_bibliography))
        actions.append(self.button("Ouvrir la fiche Bibliothèque", self.open_selected))
        self.actions_hint = Gtk.Label(
            label="« Toutes les sources » consulte seulement le cache. Choisissez une source pour collecter.",
            wrap=True, xalign=0,
        )
        self.widget.append(self.actions_hint)
        self.federated_status = Gtk.Label(
            label="Recherche fédérée non lancée. Aucune requête automatique.",
            wrap=True, xalign=0,
        )
        self.widget.append(self.federated_status)

        # Only local state and the public plugin-inventory broker are read.
        # Collapse by default to preserve the original 1024px reading viewport.
        self.diagnostic_expander = Gtk.Expander(label="Diagnostic des sources Jeunesse · cache local")
        self.diagnostic_expander.set_tooltip_text(
            "Par profil : état des plugins, dernier essai et cache. Aucune collecte automatique."
        )
        diagnostic_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.diagnostic_copy = Gtk.Button(label="Copier le diagnostic des sources (expurgé)")
        self.diagnostic_copy.set_tooltip_text("Copie seulement des compteurs et causes sûres depuis le cache, sans réseau")
        self.diagnostic_copy.connect("clicked", self.copy_source_diagnostics)
        diagnostic_container.append(self.diagnostic_copy)
        self.diagnostic_rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        diagnostic_container.append(self.diagnostic_rows_box)
        self.diagnostic_expander.set_child(diagnostic_container)
        self.widget.append(self.diagnostic_expander)

        self.master = host.build_book_master_detail(
            title="Catalogue Jeunesse",
            subtitle="Âges et provenance conservés source par source",
            empty_title="Aucun titre Jeunesse",
            empty_desc="Le cache est vide pour ces filtres. Choisissez une source puis collectez-la explicitement.",
        )
        self.master.connect_selected(self.selected)
        self.widget.append(self.master.widget)

        self.sources = ()
        self.unsubscribe_sources = plugin.context.subscribe_services("youth.source", lambda: GLib.idle_add(self.reload_sources))
        self.unsubscribe_identity = host.subscribe_identity_overview(lambda: GLib.idle_add(self.reload_identities)) if hasattr(host, "subscribe_identity_overview") else lambda: None
        self.reload_sources()
        if plugin.pending:
            if self.seed(plugin.pending, identity_id=plugin.pending_identity):
                plugin.pending = None
                plugin.pending_identity = ""
            else:
                # An unavailable origin must never leak a different user's
                # selected book title into the active profile's query.
                self.render_cached()
        else:
            self.render_cached()

    def button(self, label, callback):
        button = self.Gtk.Button(label=label)
        self._action_buttons[label] = button
        def clicked(*_args):
            self.enter_sequence.reset()
            callback()
        button.connect("clicked", clicked)
        return button

    def update_action_availability(self):
        """Refresh GTK sensitivity/tooltips, never querying a remote provider."""
        buttons = getattr(self, "_action_buttons", None)
        if not buttons:
            return
        source = self.selected_source()
        service = getattr(source, "service", None) if source is not None else None
        compatible = bool(service is not None and (
            callable(getattr(service, "browse", None))
            or callable(getattr(service, "search", None))
        ))
        identity = bool(self.identity_id())
        book = getattr(self, "selected_book", None)
        current_key = book_key(book) if book is not None else ""
        current = bool(current_key and current_key in getattr(self, "rows", {}))
        isbn = ""
        if current:
            identifiers = getattr(book, "identifiers", {}) or {}
            if isinstance(identifiers, Mapping):
                isbn = normalize_isbn(identifiers.get("isbn"))
        bibliography = False
        if current and isbn:
            try:
                bibliography = bool(tuple(self.plugin.context.services(
                    "youth.bibliography", contract="cifereca.youth-bibliography",
                    version=">=1,<2", capabilities=("youth.bibliography.lookup",),
                )))
            except Exception:
                bibliography = False
        busy = getattr(self, "_remote_running_token", None) is not None
        states = youth_action_states(
            identity=identity, source=compatible, book=current, isbn=bool(isbn),
            page=bool(compatible and self.next_cursor and
                      self.next_source_plugin == self.selected_source_plugin()),
            bibliography=bibliography,
            local_detail=callable(getattr(self.host, "open_book_detail", None)),
            busy=busy,
        )
        for label, button in buttons.items():
            enabled, reason = states[label]
            button.set_sensitive(enabled)
            button.set_tooltip_text(reason)
        federated_button = getattr(self, "federated_button", None)
        if federated_button is not None:
            # Check only static capabilities; importing the new optional
            # coordinator or making HTTP calls during render is forbidden.
            eligible = any(
                getattr(row, "enabled", True) is not False
                and getattr(getattr(row, "service", row), "enabled", True) is not False
                and any(callable(getattr(getattr(row, "service", row), method, None))
                        for method in ("browse", "search", "browse_with_cancellation"))
                for row in getattr(self, "sources", ())
            )
            enabled = bool(identity and not busy and eligible)
            federated_button.set_sensitive(enabled)
            federated_button.set_tooltip_text(
                "Réseau uniquement au clic : 8 sources max, 2 connexions simultanées, "
                "2 pages de 50 notices par source."
                if enabled else
                "Sélectionnez un profil avec des sources Jeunesse actives et compatibles."
            )
        hint = getattr(self, "actions_hint", None)
        if hint is not None:
            hint.set_label(
                "Une collecte est déjà en cours. Les autres sources restent consultables en cache."
                if busy else
                "« Toutes les sources » affiche uniquement le cache. Choisir une source pour lancer une collecte."
                if source is None else
                "Source choisie : la collecte et la pagination restent des actions réseau explicites."
            )

    def identity_changed(self):
        self.filters_changed()
        self.refresh_source_diagnostics()

    def refresh_source_diagnostics(self):
        """Render per-identity source status, never testing remote availability."""
        container = getattr(self, "diagnostic_rows_box", None)
        if container is None:
            return False  # Lightweight plugin tests can instantiate a partial view.
        try:
            diagnostics = self.plugin.context.broker("diagnostics")
            inventory_fn = getattr(diagnostics, "inventory", None)
            inventory = tuple(inventory_fn()) if callable(inventory_fn) else ()
            getter = getattr(self.catalog, "source_diagnostics", None)
            rows = getter(self.identity_id(), inventory=inventory) if callable(getter) else ()
            summary, lines = source_diagnostic_presentation(rows)
        except Exception:
            # No exception details or possibly sensitive broker results in the UI.
            summary, lines = "Indisponible", ("Impossible de lire le diagnostic local.",)
        self.diagnostic_expander.set_label("Diagnostic des sources Jeunesse · " + summary)
        child = container.get_first_child()
        while child is not None:
            successor = child.get_next_sibling()
            container.remove(child)
            child = successor
        for line in lines:
            container.append(self.Gtk.Label(label=line, wrap=True, xalign=0))
        return False

    def copy_source_diagnostics(self, _button):
        """Explicit, cache-only, cross-source export without identity or ISBN."""
        import json
        from gi.repository import Gdk
        try:
            broker = self.plugin.context.broker("diagnostics")
            inventory_fn = getattr(broker, "inventory", None)
            inventory = tuple(inventory_fn()) if callable(inventory_fn) else ()
            rows = self.catalog.source_diagnostics(self.identity_id(), inventory=inventory)
            safe = youth_source_support_snapshot(rows)
            display = Gdk.Display.get_default()
            if display is None:
                self.status.set_label("Presse-papiers indisponible ; aucune collecte déclenchée.")
                return
            display.get_clipboard().set(json.dumps(safe, ensure_ascii=False, indent=2))
            self.status.set_label("Diagnostic Jeunesse expurgé copié (cache local uniquement).")
        except Exception:
            self.status.set_label("Copie du diagnostic indisponible ; cache conservé.")

    def _identity_rows(self):
        broker = self.plugin.context.broker("identities")
        return list(broker.identities()) if broker is not None and hasattr(broker, "identities") else []

    def identity_id(self):
        index = self.identity.get_selected()
        if type(index) is not int or not 0 <= index < len(self.identities):
            return ""
        row = self.identities[index]
        return str(row.get("identity_id") or "") if isinstance(row, Mapping) else ""

    def reload_identities(self):
        if self.disposed:
            return False
        current = self.identity_id()
        if current:
            # The user explicitly selected a valid profile after any prior
            # disappearance. Reordering must preserve this identity.
            self._profile_selection_required = False
        rows = self._identity_rows()
        self._reloading_identities = True
        try:
            self.identities = rows
            self.identity.set_model(self.Gtk.StringList.new([
                str(row.get("label") or row.get("name") or row.get("identity_id") or "Profil")
                for row in rows
            ]))
            selected = next((
                index for index, row in enumerate(rows)
                if str(row.get("identity_id") or "") == current
            ), None)
            # Only initial activation selects a first profile implicitly.
            # Persist an invalid selection across *later* overview updates
            # after the selected account disappears. Otherwise current == ""
            # on the second update, silently choosing another user's cache.
            if current and selected is None:
                self._profile_selection_required = True
            self.identity.set_selected(
                selected if selected is not None else (
                    0xFFFFFFFF
                    if getattr(self, "_profile_selection_required", False) or not rows
                    else 0
                )
            )
        finally:
            self._reloading_identities = False
        self.filters_changed()
        self.refresh_source_diagnostics()
        return False

    def reload_sources(self):
        if self.disposed:
            return False
        current = self.selected_source_plugin()
        self.sources = tuple(self.plugin.context.services(
            "youth.source", contract="cifereca.youth-source", version=">=1,<2"
        ))
        labels = ["Toutes les sources"] + [source_info(row)[1] or row.plugin_id for row in self.sources]
        self.source.set_model(self.Gtk.StringList.new(labels))
        selected = next((index + 1 for index, row in enumerate(self.sources) if row.plugin_id == current), 0)
        self.source.set_selected(selected)
        self.filters_changed()
        self.refresh_source_diagnostics()
        return False

    def selected_source(self):
        index = self.source.get_selected()
        return self.sources[index - 1] if 0 < index <= len(self.sources) else None

    def selected_source_plugin(self):
        row = self.selected_source()
        return str(row.plugin_id) if row else ""

    def age_value(self):
        index = self.age.get_selected()
        return index - 1 if 1 <= index <= 18 else None

    def filter_state(self):
        source = self.selected_source()
        source_id = source_info(source)[0] if source else ""
        kind_index = self.age_kind.get_selected()
        kind = KIND_VALUES[kind_index] if 0 <= kind_index < len(KIND_VALUES) else ""
        return FilterState(
            query=self.query.get_text().strip(),
            age=self.age_value(),
            age_kind=kind,
            source_id=source_id,
            language=self.language.get_text().strip(),
            format=self.format.get_text().strip(),
            book_type=self.book_type.get_text().strip(),
            themes=parse_themes(self.themes.get_text()),
            include_inferred=self.inferred.get_active(),
        )

    def filters_changed(self):
        self.generation += 1
        sequence = getattr(self, "enter_sequence", None)
        if sequence is not None:
            sequence.reset()
        self._remote_running_token = None
        if self.task:
            self.task.cancel()
        self.next_cursor = self.next_source_plugin = ""
        self.render_cached()
        self.update_action_availability()

    def query_activated(self):
        """Return and KP_Enter: local first, chosen-source remote second."""
        state = self.filter_state()
        selected = self.selected_source_plugin()
        action = self.enter_sequence.advance(
            token=(self.identity_id(), selected, state),
            query=state.query, source_selected=bool(selected),
        )
        if action == "local":
            self.render_cached()
            count = len(self.rows)
            next_step = (
                "Appuyez une seconde fois sur Entrée pour collecter cette source."
                if selected else "Choisissez une source pour étendre la recherche."
            )
            self.status.set_label(f"Recherche en cache : {count} résultat(s). {next_step}")
        elif action == "choose_source":
            self.status.set_label("Choisissez une source Jeunesse précise avant toute collecte en ligne.")
        elif action == "short_query":
            self.status.set_label("Saisissez au moins 2 caractères pour étendre la recherche en ligne.")
        elif action == "remote":
            self._remote_request("")
        else:
            self.status.set_label("Collecte déjà demandée pour cette recherche. Modifiez un filtre pour recommencer.")

    def seed(self, book, *, identity_id=""):
        if book is None or getattr(self, "disposed", False):
            return False
        if identity_id and identity_id != self.identity_id():
            selected = next((
                index for index, row in enumerate(self.identities)
                if str(row.get("identity_id") or "") == identity_id
            ), None)
            if selected is None:
                self.status.set_label(
                    "Le profil de la fiche d’origine n’est plus disponible. "
                    "Le cache Jeunesse actuel est conservé."
                )
                return False
            self.identity.set_selected(selected)
            if self.identity_id() != identity_id:
                return False
        title = _text(getattr(book, "title", ""))
        if title:
            self.query.set_text(title)
        self.render_cached()
        return True

    def render_cached(self):
        if not self.catalog:
            self.rows = {}
            self.master.set_list_state(
                "Plugin catalogue Jeunesse indisponible",
                "Activez le moteur Jeunesse en même temps que cette page.",
                "dialog-warning-symbolic",
            )
            self.master.empty_detail()
            self.update_action_availability()
            return
        identity = self.identity_id()
        if identity and getattr(self, "_last_cache_identity", identity) != identity:
            # Equal record keys in different accounts do not attest ownership.
            self.selected_book = None
            self._detail_selection_generation = (
                getattr(self, "_detail_selection_generation", 0) + 1
            )
            self.master.empty_detail()
        self._last_cache_identity = identity
        if not identity:
            self.rows = {}
            self.selected_book = None
            self._detail_selection_generation = (
                getattr(self, "_detail_selection_generation", 0) + 1
            )
            self.master.set_list_state("Aucun profil disponible", "Créez ou activez un profil Cifereca.")
            self.master.empty_detail()
            self.update_action_availability()
            return
        state = self.filter_state()
        books = self.catalog.search_cached(
            identity,
            state.query,
            age=state.age,
            age_kind=state.age_kind,
            source_id=state.source_id,
            language=state.language,
            format=state.format,
            book_type=state.book_type,
            themes=state.themes,
            include_inferred=state.include_inferred,
        )
        self.rows, entries = _mapped_youth_entries(books)
        self.master.show_rows(entries, total_count=len(entries))
        if not entries:
            self.master.set_list_state(
                "Aucun résultat en cache",
                "Les filtres restent locaux. Une collecte distante n’a lieu que sur demande explicite.",
            )
            self.master.empty_detail()
        self.update_action_availability()

    def selected(self, row):
        if getattr(self, "disposed", False):
            # A late GTK selection callback must not resurrect the detail.
            return
        # Invalidate callbacks from the previous book on *every* selection,
        # including an empty selection and a new edition with the same title.
        # The public bridge also invokes this method on a lightweight host.
        self._detail_selection_generation = (
            getattr(self, "_detail_selection_generation", 0) + 1
        )
        # The public bridge may reuse this callback without the whole GTK view.
        # A real YouthView always exposes the updater; a partial host may not.
        updater = getattr(self, 'update_action_availability', None)
        refresh_actions = updater if callable(updater) else lambda: None
        if row is None:
            self.selected_book = None
            self.master.empty_detail()
            refresh_actions()
            return
        self.selected_book = self.rows.get(row.key)
        if self.selected_book is None:
            self.master.empty_detail()
            refresh_actions()
            return
        presentation = detail_presentation(self.selected_book)
        title = _text(getattr(self.selected_book, "title", ""))
        search = getattr(self.host, "search_books", None)
        if callable(search):
            identity_id = self.identity_id()
            selection_generation = self._detail_selection_generation
            selected_book = self.selected_book
            selection_key = row.key
            actions = list(presentation.actions)
            if 2 <= len(title) <= 512:
                actions.append(UiBookDetailAction(
                    id="search-library",
                    title="Rechercher en bibliothèque",
                    icon_name="system-search-symbolic",
                    callback=lambda q=title, identity=identity_id, token=selection_generation, key=selection_key, book=selected_book: self._search_selected_book(
                        q, identity, token, key, book, field="title",
                    ),
                ))
            # ISBN is a bibliographic *lookup* only. It does not establish
            # inventory, ownership, availability or a direct notice link.
            identifiers = getattr(self.selected_book, "identifiers", {})
            getter = getattr(identifiers, "get", None)
            raw_isbn = getter("isbn", "") if callable(getter) else ""
            # The public host bridge can reuse this method without importing
            # the whole Youth module. No normalization is needed when the
            # selected row provides no ISBN mapping.
            isbn = normalize_isbn(raw_isbn) if raw_isbn else ""
            if isbn:
                actions.append(UiBookDetailAction(
                    id="search-library-isbn",
                    title="Rechercher cette édition par ISBN",
                    icon_name="system-search-symbolic",
                    callback=lambda q=isbn, identity=identity_id, token=selection_generation, key=selection_key, book=selected_book: self._search_selected_book(
                        q, identity, token, key, book, field="isbn",
                    ),
                ))
            if actions:
                presentation = replace(presentation, actions=tuple(actions))
        self.master.show_detail(presentation)
        refresh_actions()

    def _search_selected_book(
        self, query: str, identity_id: str, generation: int,
        selection_key: str, book: object, *, field: str = "title",
    ) -> bool:
        """Never invoke an action from an old book edition or old profile.

        The host owns real library search and availability. The callback only
        attests the current UI selection; it grants no ownership or download.
        """
        if (
            getattr(self, "disposed", False)
            or generation != getattr(self, "_detail_selection_generation", 0)
            or self.identity_id() != identity_id
            or getattr(self, "selected_book", None) is not book
            or getattr(self, "rows", {}).get(selection_key) is not book
        ):
            return False
        self._search_library(query, identity_id, field=field)
        return True

    def _search_library(self, query: str, identity_id: str, *, field: str = "title") -> None:
        """Explicit local search; no proof of holdings or automatic network I/O."""
        if field not in {"title", "isbn"}:
            return
        method = getattr(self.host, "search_books", None)
        if not callable(method) or not method(
            query, field=field, online=False, identity_id=identity_id,
        ):
            self.host.toast("Recherche impossible : vérifiez le profil sélectionné.")

    def open_selected(self):
        if not self.selected_book:
            self.host.toast("Sélectionnez un livre Jeunesse.")
            return
        method = getattr(self.host, "open_book_detail", None)
        if callable(method) and method(book_ref(self.selected_book), identity_id=self.identity_id()):
            return
        self.host.toast("Ce livre n’est pas présent de façon non ambiguë dans la bibliothèque de ce profil.")

    def enrich_bibliography(self):
        if not self.selected_book:
            self.status.set_label("Sélectionnez un livre Jeunesse à enrichir.")
            return
        isbn = normalize_isbn(dict(getattr(self.selected_book, "identifiers", {}) or {}).get("isbn", ""))
        if not isbn:
            self.status.set_label("Enrichissement impossible sans ISBN valide : aucun rapprochement titre/auteur automatique.")
            return
        identity = self.identity_id()
        if not identity:
            self.status.set_label("Aucun profil actif.")
            return
        services = tuple(self.plugin.context.services(
            "youth.bibliography",
            contract="cifereca.youth-bibliography",
            version=">=1,<2",
            capabilities=("youth.bibliography.lookup",),
        ))[:4]
        if not services:
            self.status.set_label("Aucun service d’enrichissement bibliographique actif.")
            return
        selected_key = book_key(self.selected_book)

        def operation(control):
            out = []
            for row in services:
                if control.cancelled():
                    break
                source_id, label = source_info(row)
                try:
                    rows = fetch_bibliography(row.service, isbn)
                    out.append((source_id, label, rows, ""))
                except Exception as exc:
                    out.append((source_id, label, (), str(exc)))
            return tuple(out)

        self.run_bibliography(
            operation,
            lambda result: self.bibliography_collected(result, isbn=isbn),
            token=(identity, selected_key),
        )

    def run_bibliography(self, operation, complete, token):
        broker = self.plugin.context.broker("tasks")
        if broker is None:
            self.status.set_label("Broker de tâches indisponible.")
            return
        self.generation += 1
        generation = self.generation
        if self.task:
            self.task.cancel()
        self._remote_running_token = None
        self.status.set_label("Enrichissement bibliographique explicite en cours…")
        self.task = broker.submit(operation, label="Bibliographie Jeunesse", kind="youth")
        def done(handle):
            try:
                result, error = handle.result(), False
            except Exception:
                result, error = None, True
            def apply():
                current_key = book_key(self.selected_book) if self.selected_book is not None else ""
                current_token = (self.identity_id(), current_key)
                if self.disposed or generation != self.generation or current_token != token:
                    return False
                if error or result is None:
                    self.status.set_label("Échec de l’enrichissement. Le cache Jeunesse précédent est conservé.")
                else:
                    complete(result)
                return False
            self.GLib.idle_add(apply)
        self.task.add_done_callback(done)

    def bibliography_collected(self, result, *, isbn):
        accepted = 0
        failures = []
        for source_id, label, rows, error in result:
            if error:
                failures.append(label or source_id)
                continue
            report = self.catalog.ingest_bibliography(
                self.identity_id(), source_id, isbn, rows,
            )
            accepted += report.accepted
            if report.errors:
                failures.append(label or source_id)
        message = f"{accepted} enrichissement(s) bibliographique(s) appliqué(s)"
        if failures:
            message += f" · {len(failures)} source(s) sans résultat exploitable"
        self.status.set_label(message)
        self.render_cached()

    def _remote_request(self, cursor):
        row = self.selected_source()
        if row is None:
            self.status.set_label("Choisissez une source Jeunesse avant de collecter.")
            return
        state = self.filter_state()
        identity = self.identity_id()
        if not identity:
            self.status.set_label("Aucun profil actif.")
            return
        service = row.service
        source_id, label = source_info(row)
        append = bool(cursor)
        token = (identity, row.plugin_id, state.query, state.age, state.themes)
        started = perf_counter()
        self.run_remote(
            lambda control: _CANCELLED_BEFORE_REQUEST if control.cancelled() else fetch_source_page(
                service, state.query, age=state.age, themes=state.themes,
                cursor=cursor or None, limit=100,
                cancelled=control.cancelled,
            ),
            lambda raw: self.collected(
                raw, source_id=source_id, label=label, append=append,
                source_plugin=row.plugin_id, requested_limit=100,
                duration_ms=max(0, int((perf_counter() - started) * 1000)),
            ),
            token,
        )

    def collect(self):
        self._remote_request("")

    def collect_federated(self):
        """Click-only network federation. No network on typing/filtering."""
        from youth_federation_ui import run_federated_action
        run_federated_action(self)

    def more(self):
        if not self.next_cursor or self.next_source_plugin != self.selected_source_plugin():
            self.status.set_label("Aucune page suivante disponible pour cette source.")
            return
        self._remote_request(self.next_cursor)

    def run_remote(self, operation, complete, token):
        if getattr(self, "_remote_running_token", None) == token:
            self.status.set_label("Cette collecte est déjà en cours.")
            return
        broker = self.plugin.context.broker("tasks")
        if broker is None:
            self.status.set_label("Broker de tâches indisponible.")
            return
        self.generation += 1
        generation = self.generation
        if self.task:
            self.task.cancel()
        self._remote_running_token = token
        started = perf_counter()
        self.status.set_label("Collecte explicite en cours…")
        self.task = broker.submit(operation, label="Catalogue Jeunesse", kind="youth")
        self.update_action_availability()
        def done(handle):
            try:
                result, error = handle.result(), False
                if result is _CANCELLED_BEFORE_REQUEST:
                    failure = {
                        "state": "cancelled", "error_type": "",
                        "http_status": None, "request_count": 0,
                    }
                elif result is None:
                    # A malformed None response is not proof of cancellation.
                    failure = {
                        "state": "invalid", "error_type": "",
                        "http_status": None, "request_count": None,
                    }
            except Exception as exc:
                result, error = None, True
                failure = collection_failure_diagnostic(exc)
            def apply():
                current = self.filter_state()
                current_token = (self.identity_id(), self.selected_source_plugin(), current.query, current.age, current.themes)
                if self.disposed or generation != self.generation or current_token != token:
                    return False
                self._remote_running_token = None
                # Cancellation can arrive after result() completes but before
                # this GTK idle callback runs. Never ingest a stale success.
                control = getattr(handle, "control", None)
                was_cancelled = getattr(control, "cancelled", None)
                late_cancelled = callable(was_cancelled) and was_cancelled()
                # Never rebind the outer 'failure' captured by this callback:
                # doing so would mask it on every ordinary error path.
                late_failure = None
                if late_cancelled and result is not _CANCELLED_BEFORE_REQUEST:
                    count = (
                        failure.get("request_count")
                        if error or result is None else
                        result.get("collection_request_count")
                        if isinstance(result, Mapping) else None
                    )
                    late_failure = {
                        "state": "cancelled",
                        "error_type": "YouthSourceCancelled",
                        "http_status": None, "request_count": count,
                    }
                if error or result is None or result is _CANCELLED_BEFORE_REQUEST or late_cancelled:
                    diagnostic = late_failure if late_failure is not None else failure
                    reporter = getattr(self.catalog, "record_collection", None)
                    row = self.selected_source()
                    if callable(reporter) and row is not None:
                        sid, _label = source_info(row)
                        reporter(
                            token[0], sid, **diagnostic,
                            requested_limit=100,
                            duration_ms=max(0, int((perf_counter() - started) * 1000)),
                        )
                    self.status.set_label(
                        "Collecte annulée. Le cache précédent est conservé."
                        if diagnostic["state"] == "cancelled"
                        else "Échec de la source. Le cache précédent est conservé."
                    )
                    self.refresh_source_diagnostics()
                else:
                    complete(result)
                self.update_action_availability()
                return False
            self.GLib.idle_add(apply)
        self.task.add_done_callback(done)

    def collected(
        self, raw, *, source_id, label, append, source_plugin,
        duration_ms=0, requested_limit=None,
    ):
        try:
            rows, cursor, fresh = page_parts(raw)
        except InvalidYouthSourcePage:
            # Do not erase an identity's prior notices or pagination when an
            # upstream HTML/API change produced a malformed page.
            self.catalog.record_collection(
                self.identity_id(), source_id, state="invalid",
                stage="parsing", rejected=1,
                requested_limit=requested_limit,
                request_count=(
                    raw.get("collection_request_count")
                    if isinstance(raw, Mapping) else None
                ),
                pipeline=raw.get("collection_pipeline") if isinstance(raw, Mapping) else None,
                duration_ms=duration_ms,
            )
            self.status.set_label("Réponse de source invalide. Le cache précédent est conservé.")
            self.refresh_source_diagnostics()
            self.update_action_availability()
            return
        report = self.catalog.ingest(
            self.identity_id(),
            source_id,
            rows,
            replace_source=not append,
            fresh_for_seconds=fresh,
        )
        reporter = getattr(self.catalog, "record_collection", None)
        if callable(reporter):
            reporter(
                self.identity_id(), source_id,
                state=(
                    "items" if report.accepted
                    else "invalid" if report.rejected
                    else "empty_unverified"
                ),
                accepted=report.accepted, rejected=report.rejected,
                received_count=report.accepted + report.rejected,
                requested_limit=requested_limit,
                request_count=(
                    raw.get("collection_request_count") if isinstance(raw, Mapping)
                    else None
                ),
                pipeline=raw.get("collection_pipeline") if isinstance(raw, Mapping) else None,
                rejection_categories=safe_ingest_rejection_categories(report.errors),
                stage="ingest",
                duration_ms=duration_ms, next_cursor_present=bool(cursor),
            )
        # Emit a fixed-shape technical event only after this user-triggered
        # collection; never log the query, user identity, title, URL or HTML.
        plugin_context = getattr(getattr(self, "plugin", None), "context", None)
        diagnostic_report = getattr(plugin_context, "report_diagnostic", None)
        if callable(diagnostic_report):
            from youth_catalog import sanitize_collection_pipeline
            state = "items" if report.accepted else "invalid" if report.rejected else "empty_unverified"
            try:
                diagnostic_report(
                    "Jeunesse source collection outcome",
                    "ok" if report.accepted else "warning",
                    detail={
                        "state": state,
                        "accepted": min(100000, max(0, report.accepted)),
                        "rejected": min(100000, max(0, report.rejected)),
                        "pipeline": sanitize_collection_pipeline(
                            raw.get("collection_pipeline") if isinstance(raw, Mapping) else None
                        ),
                    },
                    level="normal",
                )
            except Exception:
                pass  # A diagnostic event must never break ingestion.
        self.next_cursor = cursor
        self.next_source_plugin = str(source_plugin) if cursor else ""
        message = f"{report.accepted} notice(s) reçue(s) de {label or source_id}"
        if report.rejected:
            message += f" · {report.rejected} rejet(s)"
        if cursor:
            message += " · page suivante disponible"
        self.status.set_label(message)
        self.render_cached()
        self.refresh_source_diagnostics()

    def dispose(self):
        self.disposed = True
        self.generation += 1
        # Actions in the shared detail can outlive GTK widgets if a callback
        # was captured externally. Closing revokes their selection lease.
        self._detail_selection_generation = (
            getattr(self, "_detail_selection_generation", 0) + 1
        )
        self.selected_book = None
        if self.task:
            self.task.cancel()
        self.unsubscribe_sources()
        self.unsubscribe_identity()


PLUGIN = YouthPagePlugin()
