from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from cifereca.plugin_api import (
    BookRef,
    CachedBookDetail,
    DetailField,
    DetailSection,
    Provenance,
    UiBookDetailAction,
)

_ISBN_CLEAN = re.compile(r"[^0-9Xx]")
_KIND_LABELS = {
    "interest": "Âge d’intérêt",
    "reading": "Âge de lecture",
    "recommended": "Âge recommandé",
    "publisher": "Âge éditeur",
    "inferred": "Âge inféré",
}


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _fold(value: object) -> str:
    return _text(value).casefold()


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
    if len(raw) == 13 and raw.isdigit():
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(raw))
        return raw if total % 10 == 0 else ""
    return ""


def canonical_url(value: object) -> str:
    raw = _text(value)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return ""
    scheme = parts.scheme.casefold()
    host = parts.hostname.casefold()
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _identifiers(value: object) -> Mapping[str, object]:
    return _mapping(getattr(value, "identifiers", {}))


def _candidate_notice_urls(candidate: object) -> tuple[str, ...]:
    urls = []
    for name in ("notice_url", "friendly_url"):
        url = canonical_url(getattr(candidate, name, ""))
        if url and url not in urls:
            urls.append(url)
    return tuple(urls)


def _book_notice_urls(book: BookRef) -> tuple[str, ...]:
    urls = []
    metadata = _mapping(book.metadata)
    identifiers = _mapping(book.identifiers)
    for source in (metadata, identifiers):
        for name in ("notice_url", "friendly_url", "canonical_url", "url"):
            url = canonical_url(source.get(name))
            if url and url not in urls:
                urls.append(url)
    return tuple(urls)


@dataclass(frozen=True, slots=True)
class Match:
    candidate: object
    score: int
    reason: str


def _match(book: BookRef, candidate: object) -> Match | None:
    book_ids = _mapping(book.identifiers)
    candidate_ids = _identifiers(candidate)

    book_isbn = normalize_isbn(book_ids.get("isbn") or book_ids.get("isbn13") or book_ids.get("isbn10"))
    candidate_isbn = normalize_isbn(candidate_ids.get("isbn") or candidate_ids.get("isbn13") or candidate_ids.get("isbn10"))
    if book_isbn and candidate_isbn and book_isbn == candidate_isbn:
        return Match(candidate, 100, f"isbn:{book_isbn}")

    book_provider = _fold(book_ids.get("provider") or book.provenance.provider_id if book.provenance else "")
    book_source_item = _text(book_ids.get("source_item_id"))
    candidate_provider = _fold(candidate_ids.get("provider") or getattr(candidate, "source_id", ""))
    candidate_source_item = _text(candidate_ids.get("source_item_id") or getattr(candidate, "record_id", ""))
    if book_provider and book_provider == candidate_provider and book_source_item and book_source_item == candidate_source_item:
        return Match(candidate, 90, "source-item")

    book_urls = set(_book_notice_urls(book))
    candidate_urls = set(_candidate_notice_urls(candidate))
    if book_urls and candidate_urls and book_urls & candidate_urls:
        return Match(candidate, 85, "notice-url")

    return None


def select_candidate(book: BookRef, candidates: Iterable[object]) -> Match | None:
    matches = [match for candidate in candidates if (match := _match(book, candidate)) is not None]
    if not matches:
        return None
    top = max(match.score for match in matches)
    best = [match for match in matches if match.score == top]
    if len(best) != 1:
        fingerprints = {
            (
                normalize_isbn(_identifiers(match.candidate).get("isbn")),
                _fold(_identifiers(match.candidate).get("provider") or getattr(match.candidate, "source_id", "")),
                _text(_identifiers(match.candidate).get("source_item_id") or getattr(match.candidate, "record_id", "")),
            )
            for match in best
        }
        if len(fingerprints) != 1:
            return None
    return best[0]


def age_label(row: object) -> str:
    lo = getattr(row, "age_min", None)
    hi = getattr(row, "age_max", None)
    if lo is None and hi is None:
        bounds = "âge inconnu"
    elif lo is None:
        bounds = f"jusqu’à {hi} ans"
    elif hi is None:
        bounds = f"à partir de {lo} ans"
    elif lo == hi:
        bounds = f"{lo} ans"
    else:
        bounds = f"{lo}–{hi} ans"
    kind = _KIND_LABELS.get(_fold(getattr(row, "age_kind", "")), "Âge")
    return f"{kind} · {bounds}"


def age_value(row: object) -> str:
    parts = [age_label(row)]
    source_id = _text(getattr(row, "source_id", ""))
    source_label = _text(getattr(row, "source_label", ""))
    confidence = getattr(row, "confidence", None)
    if source_id:
        parts.append(f"source {source_id}")
    if source_label:
        parts.append(f"« {source_label} »")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        parts.append(f"confiance {round(float(confidence) * 100)} %")
    return " · ".join(parts)


def _freshness_token(candidate: object, reason: str) -> str:
    parts = [reason, _text(getattr(candidate, "source_id", "")), _text(getattr(candidate, "record_id", ""))]
    for row in tuple(getattr(candidate, "age_ranges", ()) or ()):
        parts.extend([
            _text(getattr(row, "age_kind", "")),
            _text(getattr(row, "age_min", "")),
            _text(getattr(row, "age_max", "")),
            _text(getattr(row, "source_label", "")),
            _text(getattr(row, "source_id", "")),
            _text(getattr(row, "confidence", "")),
        ])
    return sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]


class YouthDetailEnrichment:
    def activate(self, context):
        self.context = context
        return self

    def deactivate(self):
        self.context = None

    def _catalog(self):
        context = getattr(self, "context", None)
        service = context.service("catalog.youth") if context is not None else None
        return service if callable(getattr(service, "search_cached", None)) else None

    def cached_detail(self, book: BookRef, *, identity_id: str) -> CachedBookDetail | None:
        if not isinstance(book, BookRef) or not _text(identity_id):
            return None
        catalog = self._catalog()
        if catalog is None:
            return None

        query = _text(book.title)
        if not query:
            return None

        try:
            candidates = tuple(catalog.search_cached(_text(identity_id), query))[:200]
        except Exception:
            return None

        match = select_candidate(book, candidates)
        if match is None:
            return None

        candidate = match.candidate
        ranges = tuple(getattr(candidate, "age_ranges", ()) or ())
        if not ranges:
            return None

        explicit = [row for row in ranges if _fold(getattr(row, "age_kind", "")) != "inferred"]
        inferred = [row for row in ranges if _fold(getattr(row, "age_kind", "")) == "inferred"]
        ordered = tuple(explicit + inferred)

        fields = [
            DetailField(
                id=f"age-{index + 1}",
                label=_KIND_LABELS.get(_fold(getattr(row, "age_kind", "")), "Âge"),
                value=age_value(row),
                provenance=Provenance(
                    source="Jeunesse",
                    provider_id=_fold(_identifiers(candidate).get("provider")),
                    source_id=_text(getattr(row, "source_id", "") or getattr(candidate, "source_id", "")),
                    metadata={"match": match.reason},
                ),
            )
            for index, row in enumerate(ordered[:4])
        ]

        sources = tuple(getattr(candidate, "provenance", ()) or ())
        if sources and len(fields) < 5:
            fields.append(DetailField(
                id="sources",
                label="Sources jeunesse",
                value=", ".join(_text(value) for value in sources if _text(value)),
            ))

        url = next(iter(_candidate_notice_urls(candidate)), "")
        actions = (
            UiBookDetailAction(
                id="youth-notice",
                title="Voir la source Jeunesse",
                icon_name="external-link-symbolic",
                uri=url,
            ),
        ) if url else ()

        source_id = _text(getattr(candidate, "source_id", "")) or _fold(_identifiers(candidate).get("provider"))
        return CachedBookDetail(
            book_id=book.book_id,
            identity_id=_text(identity_id),
            sections=(DetailSection("youth", "Jeunesse", tuple(fields), order=55),),
            actions=actions,
            source=Provenance(
                source="Jeunesse",
                provider_id=_fold(_identifiers(candidate).get("provider")),
                source_id=source_id,
                metadata={"match": match.reason},
            ),
            freshness_token=_freshness_token(candidate, match.reason),
            stale=False,
        )


PLUGIN = YouthDetailEnrichment()
