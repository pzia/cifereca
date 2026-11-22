"""#4030: known public C suggestions, not an association with a local file.

The caller supplies local descriptive text after explicit review. This
component never extracts bytes, dispatches a supplier, or writes a Notice.
The existing C search owns source validation, pagination and freshness.
"""
from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from .notice_c_public_search import PublicNoticeCHit, search_public_notice_c
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


def suggest_known_public_documents(storage, *, title: str, author: str | None = None,
                                   page: int = 0, page_size: int = 20,
                                   now: int | None = None):
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
        if displayable(hit.title) and displayable(hit.author)
    )
    ordered = tuple(sorted(
        matches, key=lambda item: evidence_rank.get(item.comparison, 4),
    ))
    return DocumentarySuggestionPage(
        suggestions=ordered,
        known_matches=found.known_matches, status=found.status,
        has_more=found.has_more, page=page, page_size=page_size,
        unindexed_sources=found.unindexed_sources,
    )
