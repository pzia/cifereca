"""#4030: known public C suggestions, not an association with a local file.

The caller supplies local descriptive text after explicit review. This
component never extracts bytes, dispatches a supplier, or writes a Notice.
The existing C search owns source validation, pagination and freshness.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import sqlite3
import unicodedata

from .notice_c_cover_index import is_canonical_cover_document
from .notice_c_public_search import PublicNoticeCHit, search_public_notice_c
from .notice_c_web_choices import _read as _read_current_public_web_references
from .public_search_projection import _public_bibliographic_text


@dataclass(frozen=True, slots=True)
class DocumentarySuggestion:
    hit: PublicNoticeCHit
    comparison: str
    association_authorized: bool = False
    provider_origin_attested: bool = False


@dataclass(frozen=True, slots=True)
class DocumentarySuggestionPage:
    suggestions: tuple[DocumentarySuggestion, ...]
    known_matches: int
    status: str
    has_more: bool
    page: int
    page_size: int
    unindexed_sources: int
    coverage_complete: bool = False
    provider_access_performed: bool = False
    may_write_origin_or_right: bool = False


def _fold(value):
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def _comparison(hit, title, author):
    if _fold(hit.title) != _fold(title):
        return "title_related"
    if not author or not hit.author:
        return "title_concordant_author_unknown"
    if _fold(author) != _fold(hit.author):
        return "title_concordant_author_divergent"
    return "title_author_concordant"


def _matching_source_url(storage, matches, url):
    """Return a single corroborated hit index from one bounded C snapshot."""
    if (type(url) is not str or not 0 < len(url) <= 4096
            or any(unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp")
                   for ch in url) or not matches):
        return None
    by_document = {}
    matched = []
    try:
        with storage._lock:
            db = storage._connect()
            # A borrowed connection may belong to an unrelated writer.
            # Never enter its context manager and accidentally commit it.
            if db.in_transaction:
                return None
            with db:
                db.execute("BEGIN")
                for index, match in enumerate(matches):
                    hit = match.hit
                    if not is_canonical_cover_document(hit.document):
                        continue
                    if hit.document not in by_document:
                        by_document[hit.document] = _read_current_public_web_references(db, hit.document)
                    snapshot, pairs = by_document[hit.document]
                    if snapshot is None or snapshot.epoch != hit.epoch:
                        return None
                    if any(
                        (ref.provider_id, ref.record_id, ref.edition_id, ref.observed_at, ref.url)
                        == (hit.provider_id, hit.record_id, hit.edition_id, hit.observed_at, url)
                        for _, ref in pairs
                    ):
                        matched.append(index)
    except (sqlite3.Error, ValueError, TypeError, RuntimeError):
        # A failed optional reference read leaves the title/author comparison.
        return None
    if len(matched) == 1 and matches[matched[0]].comparison == "title_author_concordant":
        return matched[0]
    return None


def suggest_known_public_documents(storage, *, title: str, author: str | None = None,
                                   page: int = 0, page_size: int = 20,
                                   now: int | None = None, source_urls=frozenset()):
    """One bounded title query; classify, never collapse source editions.

    A descriptive match is not an exact identifier proof. All results retain
    the source DTO, including record, edition, revision, dates and stale flag.
    Ranking is local to the C page: stronger descriptive evidence first,
    stable ties retain C's exact order. No global or cross-page rank claimed.
    Even no known matches is partial evidence, not a negative catalogue result.
    No caller-supplied provider/account scope or arbitrary search callback.
    """
    if (not _public_bibliographic_text(title, required=True, limit=160)
            or type(title) is not str or not 2 <= len(title.strip()) <= 160
            # This local documentary preview must never use a forged
            # multiline/bidi title as a query or apparent consent label.
            # Global public C retains its own independent text contract.
            or any(unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp")
                   for ch in title)
            or not _public_bibliographic_text(author, limit=512)
            or type(author) not in (str, type(None))
            or (author is not None and any(
                unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp")
                for ch in author))
            or (now is not None and (type(now) is not int or now < 0))
            or type(page) is not int or not 0 <= page <= 200
            or type(page_size) is not int or not 1 <= page_size <= 50):
        raise ValueError("invalid_documentary_suggestion_request")
    found = search_public_notice_c(
        storage, title, field="title", scope="all",
        page=page, page_size=page_size, max_scan=2048, now=now,
        group_by_document=False,
    )
    # Ranking is advisory only. Do not infer origin/right from similarity,
    # reorder across pagination windows, or collapse source editions.
    evidence_rank = {
        "opf_source_url_concordant": -1,
        "title_author_concordant": 0,
        "title_concordant_author_unknown": 1,
        "title_concordant_author_divergent": 2,
        "title_related": 3,
    }
    # C is public descriptive evidence, not an authority for a local
    # origin label. Keep the C page/counts intact but do not display
    # spoofed title/author fields as candidate matches.
    def displayable(value):
        return (value is None or (type(value) is str and
                not any(unicodedata.category(ch) in
                        ("Cc", "Cf", "Cs", "Zl", "Zp") for ch in value)))

    matches = tuple(
        DocumentarySuggestion(hit, _comparison(hit, title, author))
        for hit in found.hits
        if (type(hit) is PublicNoticeCHit and type(hit.title) is str
            and bool(hit.title) and displayable(hit.title)
            and displayable(hit.author)
            # The collection/docbase is a source coordinate displayed by
            # the local-origin detail and its cross-surface ranking. An
            # injected/malformed C hit must not spoof a different origin.
            and type(hit.docbase) is str and 0 < len(hit.docbase) <= 512
            and displayable(hit.docbase)
            # These coordinates are rendered alongside the title in the
            # local review; even a malformed C DTO must not spoof a label.
            and all((value is None or (type(value) is str and len(value) <= 512))
                    and displayable(value)
                    for value in (getattr(hit, field) for field in (
                        "provider_id", "record_id", "edition_id",
                        "source_revision", "medium", "media_format",
                    ))))
    )
    if (type(source_urls) is frozenset and len(source_urls) == 1
            and page == 0 and not found.has_more and len(matches) == found.known_matches):
        # The URL is an OPF claim. Compare it without exposing it or using
        # it as a request. Do not pick a winner across unseen title hits.
        index = _matching_source_url(storage, matches, next(iter(source_urls)))
        if index is not None:
            matches = (*matches[:index],
                       replace(matches[index], comparison="opf_source_url_concordant"),
                       *matches[index + 1:])
    ordered = tuple(sorted(
        matches, key=lambda item: evidence_rank.get(item.comparison, 4),
    ))
    return DocumentarySuggestionPage(
        suggestions=ordered,
        known_matches=found.known_matches, status=found.status,
        has_more=found.has_more, page=page, page_size=page_size,
        unindexed_sources=found.unindexed_sources,
    )
