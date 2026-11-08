"""Structural discovery of book links on BiblioAccess 1409 MyLoans HTML.

This is deliberately NOT a loan parser: other book links may appear in menus,
recommendations, or a public landing page. A candidate is neither an
entitlement nor evidence of an authenticated borrower. No network access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

from biblioaccess_observations import book_id_from_url

MY_LOANS_URL = "https://www.biblioaccess.com/1409/Catalog/MyLoans"
MAX_HTML_BYTES = 256 * 1024
MAX_ANCHORS = 512
MAX_CANDIDATES = 128
MAX_LOAN_ROWS = 128
_MAX_TEXT = 512
_ROW_CLASS = re.compile(r"JSProductRow-([0-9]{1,18})")


class LoanPageError(ValueError):
    """Stable structural error codes, never include HTML or links."""


@dataclass(frozen=True)
class ObservedLoanRow:
    """Positive row structure, NOT an entitlement or authoritative loan ID.

    The template product reference and the catalog book ID are independent.
    Never retain signed ACSM URLs or grant their download from this evidence.
    """
    product_ref: str = field(repr=False)
    book_id: str = field(repr=False)
    title: str = field(repr=False)
    due_at_local: str = field(repr=False)
    acsm_offer_present: bool = False


@dataclass(frozen=True)
class LoanPageCandidates:
    """Ephemeral structural hints; never evidence of a user loan."""

    page_book_link_count: int
    page_book_unique_count: int
    inventory_verified: bool = False
    identity_verified: bool = False
    completeness: str = "unknown"
    # Strictly recognized rows of the observed tenant 1409 MyLoans layout.
    # A positive row is not by itself proof of the authenticated borrower.
    page_loan_row_count: int = 0
    page_loan_row_partial_count: int = 0
    logout_form_observed: bool = False
    _loan_rows: tuple[ObservedLoanRow, ...] = field(
        default=(), repr=False, compare=False,
    )
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


class _ObservedRows(HTMLParser):
    """Strict, bounded read of loan-specific DOM; no free-text heuristics.

    The current observed page contains div.JSProductRow-N with a
    p.titleLoans, span.jsDateEnd, and a same-tenant Catalog/Book link.
    These elements have to occur within the SAME row. The independent
    template product ref is not interchangeable with the Book ID.
    """
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.div_depth = 0
        self.row_depth = 0
        self.row = None
        self.download_depth = 0
        self.capture = None
        self.capture_data = []
        self.rows = []
        self.partial_rows = 0
        self.logout_form_observed = False
        self._seen_refs = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = values.get("class", "").split()
        if tag == "form" and (
            values.get("id") == "logoutForm"
            and values.get("method", "").lower() == "post"
            and values.get("action") == "/1409/Account/LogOff"
        ):
            self.logout_form_observed = True
        if tag == "div":
            self.div_depth += 1
            ids = [match.group(1) for name in classes
                   if (match := _ROW_CLASS.fullmatch(name))]
            if ids:
                if self.row is not None or len(ids) != 1:
                    raise LoanPageError("loans_page_nested_row")
                if len(self.rows) + self.partial_rows >= MAX_LOAN_ROWS:
                    raise LoanPageError("loans_page_row_limit")
                self.row_depth = self.div_depth
                self.row = {
                    "ref": ids[0], "titles": [], "dates": [], "book_ids": set(),
                    "acsm": False, "ambiguous": False,
                }
            if (self.row is not None and "downloadbook" in classes
                    and "JSdownloadbook-" + self.row["ref"] in classes):
                self.download_depth = self.div_depth

        if self.row is None:
            return
        if tag == "p" and "titleLoans" in classes:
            self._start_capture("title")
        elif tag == "span" and "jsDateEnd" in classes:
            self._start_capture("date")
        elif tag == "a":
            href = values.get("href")
            if type(href) is not str or len(href) > 4096:
                return
            try:
                target = urljoin(MY_LOANS_URL, href)
                book_id = book_id_from_url(target)
                if book_id is not None:
                    self.row["book_ids"].add(book_id)
                if self.download_depth:
                    dest = urlsplit(href)
                    if (dest.scheme == "https"
                            and dest.hostname == "entrepot2r.numilog.com"
                            and dest.username is None and dest.password is None
                            and dest.port in (None, 443) and not dest.fragment
                            and dest.path == "/fulfillment/URLLink.acsm"
                            and bool(dest.query)):
                        # Boolean observation only. NEVER store href or query.
                        self.row["acsm"] = True
            except ValueError:
                pass

    def _start_capture(self, kind):
        if self.capture is not None:
            self.row["ambiguous"] = True
            return
        self.capture = kind
        self.capture_data = []

    def handle_data(self, text):
        if self.row is not None and self.capture is not None:
            self.capture_data.append(text)

    def handle_endtag(self, tag):
        if self.row is not None and self.capture is not None:
            if ((self.capture == "title" and tag == "p")
                    or (self.capture == "date" and tag == "span")):
                text = " ".join("".join(self.capture_data).split())
                if len(text) > _MAX_TEXT:
                    self.row["ambiguous"] = True
                self.row["titles" if self.capture == "title" else "dates"].append(text)
                self.capture = None
                self.capture_data = []
        if tag == "div":
            if self.download_depth == self.div_depth:
                self.download_depth = 0
            if self.row is not None and self.div_depth == self.row_depth:
                self._finish_row()
            self.div_depth = max(0, self.div_depth - 1)

    def _finish_row(self):
        row = self.row
        self.row = None
        self.row_depth = 0
        self.download_depth = 0
        titles, dates = row["titles"], row["dates"]
        valid_date = False
        if len(dates) == 1 and re.fullmatch(
            r"[0-9]{2}/[0-9]{2}/[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2}", dates[0],
        ):
            try:
                date = datetime.strptime(dates[0], "%d/%m/%Y %H:%M:%S")
                valid_date = 2000 <= date.year <= 2100
            except ValueError:
                pass
        if (not row["ambiguous"] and row["ref"] not in self._seen_refs
                and len(titles) == 1 and bool(titles[0])
                and len(row["book_ids"]) == 1 and valid_date):
            self.rows.append(ObservedLoanRow(
                product_ref=row["ref"],
                book_id=next(iter(row["book_ids"])),
                title=titles[0], due_at_local=dates[0],
                acsm_offer_present=row["acsm"],
            ))
        else:
            self.partial_rows += 1
        self._seen_refs.add(row["ref"])


def inspect_myloans_html(html: str) -> LoanPageCandidates:
    """Extract distinct same-tenant bibliographic links without loan claims."""
    if (type(html) is not str or len(html.encode("utf-8")) > MAX_HTML_BYTES):
        raise LoanPageError("loans_page_body_limit")
    parser = _CandidateLinks()
    rows = _ObservedRows()
    try:
        parser.feed(html)
        parser.close()
        rows.feed(html)
        rows.close()
        if rows.row is not None:
            # Truncated DOM cannot publish a positive row or a zero inventory.
            raise LoanPageError("loans_page_row_unclosed")
    except LoanPageError:
        raise
    except (ValueError, UnicodeError):
        raise LoanPageError("loans_page_html_invalid") from None
    return LoanPageCandidates(
        page_book_link_count=parser.book_links,
        page_book_unique_count=len(parser.urls),
        page_loan_row_count=len(rows.rows),
        page_loan_row_partial_count=rows.partial_rows,
        logout_form_observed=rows.logout_form_observed,
        _loan_rows=tuple(rows.rows),
        _book_ids=tuple(parser.urls),
        _book_urls=tuple(parser.urls.items()),
    )
