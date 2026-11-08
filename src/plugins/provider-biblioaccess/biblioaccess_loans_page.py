"""Structural discovery of book links on BiblioAccess 1409 MyLoans HTML.

This is deliberately NOT a loan parser: other book links may appear in menus,
recommendations, or a public landing page. A candidate is neither an
entitlement nor evidence of an authenticated borrower. No network access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

from biblioaccess_observations import book_id_from_url

MY_LOANS_URL = "https://www.biblioaccess.com/1409/Catalog/MyLoans"
MAX_HTML_BYTES = 256 * 1024
MAX_ANCHORS = 512
MAX_CANDIDATES = 128


class LoanPageError(ValueError):
    """Stable structural error codes, never include HTML or links."""


@dataclass(frozen=True)
class LoanPageCandidates:
    """Ephemeral structural hints; never evidence of a user loan."""

    page_book_link_count: int
    page_book_unique_count: int
    inventory_verified: bool = False
    identity_verified: bool = False
    completeness: str = "unknown"
    _book_ids: tuple[str, ...] = field(default=(), repr=False, compare=False)
    _book_urls: tuple[tuple[str, str], ...] = field(
        default=(), repr=False, compare=False,
    )


class _CandidateLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors = 0
        self.book_links = 0
        self.urls: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        self.anchors += 1
        if self.anchors > MAX_ANCHORS:
            raise LoanPageError("loans_page_anchor_limit")
        values = dict(attrs)
        raw = values.get("href")
        if not isinstance(raw, str) or not raw or len(raw) > 4096:
            return
        try:
            target = urljoin(MY_LOANS_URL, raw)
            identifier = book_id_from_url(target)
        except (ValueError, TypeError):
            return
        if identifier is None:
            return
        self.book_links += 1
        if identifier not in self.urls:
            if len(self.urls) >= MAX_CANDIDATES:
                raise LoanPageError("loans_page_candidate_limit")
            # Preserve the exact, validated notice link for a later qualified
            # mapping; never reconstruct a path or use it for acquisition.
            self.urls[identifier] = target


def inspect_myloans_html(html: str) -> LoanPageCandidates:
    """Extract distinct same-tenant bibliographic links without loan claims."""
    if (type(html) is not str or len(html.encode("utf-8")) > MAX_HTML_BYTES):
        raise LoanPageError("loans_page_body_limit")
    parser = _CandidateLinks()
    try:
        parser.feed(html)
        parser.close()
    except LoanPageError:
        raise
    except (ValueError, UnicodeError):
        raise LoanPageError("loans_page_html_invalid") from None
    return LoanPageCandidates(
        page_book_link_count=parser.book_links,
        page_book_unique_count=len(parser.urls),
        _book_ids=tuple(parser.urls),
        _book_urls=tuple(parser.urls.items()),
    )
