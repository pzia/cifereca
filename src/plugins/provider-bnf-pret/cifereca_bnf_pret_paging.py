"""Fail-closed validation for an observed BnF HTML search pagination link.

The browser owns the link and must capture it from a visible anchor on the
current results page. This offline validator does not create, guess or fetch
routes, never infers completeness, and never receives credentials or loan IDs.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urljoin, urlsplit

from cifereca_bnf_pret_web import ORIGIN, WebParseError

_ALLOWED = frozenset({"q", "availability", "sort_by", "page",
                      "category", "category_standard"})
_MAX_PAGE = 100


def _params(url):
    if (type(url) is not str or len(url) > 4096
            or any(ord(c) < 33 or ord(c) == 127 or c == "\\" for c in url)):
        raise WebParseError("next_page_unavailable")
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.netloc != "pret.bnf.fr"
                or parts.path != "/results" or parts.fragment):
            raise ValueError("route")
        items = parse_qsl(parts.query, keep_blank_values=True,
                          strict_parsing=True, max_num_fields=12)
        if len(items) != len(set(k for k, _ in items)):
            raise ValueError("duplicate")
        if any(k not in _ALLOWED or len(v) > 2048 or
               any(ord(ch) < 32 or ord(ch) == 127 for ch in v)
               for k, v in items):
            raise ValueError("invalid_value")
        result = dict(items)
        if "page" in result:
            page = result["page"]
            if not page.isascii() or not page.isdecimal() or not 1 <= int(page) <= _MAX_PAGE:
                raise ValueError("invalid_page")
        return result
    except (ValueError, UnicodeError, OverflowError):
        raise WebParseError("next_page_unavailable") from None


def observed_next_results_url(current_url, observed_href):
    """Permit exactly +1 page with unchanged search/filter parameters.

    The candidate MUST be extracted from the visible DOM. Never call this to
    derive a URL from a page number or from arbitrary user input. The returned
    URL remains private to the browser thread; it is not a catalogue result.
    """
    current = _params(current_url)
    if (type(observed_href) is not str or not observed_href
            or len(observed_href) > 4096
            or any(ord(ch) < 33 or ord(ch) == 127 or ch == "\\"
                   for ch in observed_href)):
        raise WebParseError("next_page_unavailable")
    try:
        target_url = urljoin(current_url, observed_href)
    except (ValueError, UnicodeError):
        raise WebParseError("next_page_unavailable") from None
    target = _params(target_url)
    present = current.get("page", "1")
    if (int(present) >= _MAX_PAGE or
            int(target.get("page", "0")) != int(present) + 1
            or {k: v for k, v in current.items() if k != "page"} !=
               {k: v for k, v in target.items() if k != "page"}):
        raise WebParseError("next_page_unavailable")
    return target_url
