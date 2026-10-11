"""Fail-closed Baen Free Library product-card extraction.

The public /catalog/category/view/s/free-library/id/2012 route currently
lists product pages named /<slug>.html (not /allbooks/<slug>).
Never treat navigation, cart, My Books, review or auth links as titles.
No raw HTML fixture is claimed: tests use explicitly synthetic fragments.
"""
from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


BASE = "https://www.baen.com/"
FREE_LIBRARY = BASE + "catalog/category/view/s/free-library/id/2012"
MAX_HTML_BYTES = 1_500_000
MAX_PAGE_SIZE = 36


def public_product_url(href: str) -> str:
    """Only top-level, same-origin *.html product pages without credentials."""
    if not isinstance(href, str) or not href or len(href) > 512:
        return ""
    try:
        raw = urlsplit(href)
        if any(component in (".", "..") for component in raw.path.split("/")):
            return ""
        value = urljoin(BASE, href)
        parts = urlsplit(value)
        if (parts.scheme != "https" or parts.hostname not in ("www.baen.com", "baen.com")
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.port not in (None, 443)):
            return ""
        if not parts.path.startswith("/") or parts.path.count("/") != 1:
            return ""
        slug = parts.path[1:]
        if not slug.endswith(".html") or not 2 <= len(slug) <= 120:
            return ""
        if not all(c.isascii() and (c.isalnum() or c in ".-_") for c in slug):
            return ""
        if slug.lower() in {"faq.html", "contact.html", "terms.html", "privacy.html"}:
            return ""
        return parts.scheme + "://www.baen.com" + parts.path
    except (ValueError, AttributeError):
        return ""


class FreeLibraryCards(HTMLParser):
    """Parse only explicitly marked Magento-like product links inside cards."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.card_depth = 0
        self.anchor_depth = 0
        self.anchor_url = ""
        self.text_parts = []
        self.links: list[tuple[str, str]] = []
        self.seen = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(str(attrs.get("class") or "").lower().split())
        # HTML void elements (img/input/meta/br/...) have no closing tag.
        # Counting them as nesting levels would incorrectly leak card scope.
        if tag not in ("area", "base", "br", "col", "embed", "hr", "img",
                       "input", "link", "meta", "param", "source", "track", "wbr"):
            self.depth += 1
        if self.card_depth == 0 and (
                ("product-item" in classes and tag in ("li", "article", "div"))
                or ("product-item-info" in classes and tag in ("div", "article"))):
            self.card_depth = self.depth
        if tag != "a" or self.anchor_depth:
            return
        href = public_product_url(attrs.get("href") or "")
        is_product_link = self.card_depth and (
            "product-item-link" in classes
            or "product-name-link" in classes
            or "product-item-name" in classes
        )
        if href and is_product_link:
            self.anchor_depth = self.depth
            self.anchor_url = href
            self.text_parts = []

    def handle_data(self, data):
        if self.anchor_depth:
            self.text_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor_depth:
            title = " ".join(" ".join(self.text_parts).split())[:300]
            if title and self.anchor_url not in self.seen:
                self.seen.add(self.anchor_url)
                self.links.append((self.anchor_url, title))
            self.anchor_depth = 0
            self.anchor_url = ""
            self.text_parts = []
        if self.card_depth and self.depth == self.card_depth:
            self.card_depth = 0
        if tag not in ("area", "base", "br", "col", "embed", "hr", "img",
                       "input", "link", "meta", "param", "source", "track", "wbr"):
            self.depth = max(0, self.depth - 1)


def catalog_cards(document: str, *, limit: int = 36) -> tuple[tuple[str, str], ...]:
    if not isinstance(document, str) or len(document.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError("baen_catalog_oversize")
    if not 0 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("baen_invalid_page_limit")
    parser = FreeLibraryCards()
    parser.feed(document)
    parser.close()
    if not parser.links:
        normalized = " ".join(document.casefold().split())
        # Explicitly empty product-list pages may occur at the end of pagination.
        if "no results" in normalized or "items 0-0 of 0" in normalized:
            return ()
        raise ValueError("baen_catalog_layout_unrecognized")
    return tuple(parser.links[:limit])
