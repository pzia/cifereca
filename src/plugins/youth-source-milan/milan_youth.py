from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from typing import Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

BASE = "https://www.editionsmilan.com"
CATALOG = BASE + "/livres/"
_PRODUCT_PATH = re.compile(r"^/livres/\d+-[^/]+/?$")
_LISTING_PATH = re.compile(r"^/livres(?:/page/(\d+))?/?$")
_AGE_RE = re.compile(r"^\s*À\s+partir\s+de\s+(\d{1,2})\s+ans?\s*$", re.I)
_ISBN_RE = re.compile(r"([0-9Xx][0-9Xx -]{8,24}[0-9Xx])")
_WS = re.compile(r"\s+")
_GENERIC_LINKS = {
    "lire la suite",
    "où acheter ?",
    "ou acheter ?",
    "extrait",
}


def _text(value: object) -> str:
    return _WS.sub(" ", str(value or "").replace("\xa0", " ").strip())


def _fold(value: object) -> str:
    return _text(value).casefold()


def normalize_isbn(value: object) -> str:
    raw = re.sub(r"[^0-9Xx]", "", _text(value)).upper()
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


def allowed_url(value: object) -> str:
    raw = _text(value)
    if not raw:
        return ""
    absolute = urljoin(BASE + "/", raw)
    try:
        parts = urlsplit(absolute)
        port = parts.port
    except ValueError:
        return ""
    host = (parts.hostname or "").casefold()
    if parts.scheme.casefold() != "https" or host not in {"editionsmilan.com", "www.editionsmilan.com"}:
        return ""
    if parts.username or parts.password or port not in (None, 443):
        return ""
    return urlunsplit(("https", "www.editionsmilan.com", parts.path or "/", parts.query, ""))


def product_url(value: object) -> str:
    url = allowed_url(value)
    return url if url and _PRODUCT_PATH.fullmatch(urlsplit(url).path) else ""


def listing_url(*, page: int) -> str:
    bounded = max(1, min(int(page), 100))
    return CATALOG if bounded == 1 else f"{CATALOG}page/{bounded}/"


@dataclass(frozen=True, slots=True)
class PublishedAge:
    label: str
    age_min: int


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple[str, str, str]] = []
        self._skip = 0
        self._href = ""
        self._anchor: list[str] = []
        self._anchor_fallback = ""
        self._heading = 0
        self._heading_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        name = tag.casefold()
        if name in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        if self._skip:
            return
        if name == "a":
            attributes = dict(attrs)
            self._href = attributes.get("href", "")
            self._anchor = []
            self._anchor_fallback = _text(
                attributes.get("aria-label") or attributes.get("title")
            )
        elif name == "img" and self._href and not self._anchor_fallback:
            # Product cards may contain only a cover image inside the link.
            # Its alt text is a published accessible label, not an inferred age.
            self._anchor_fallback = _text(dict(attrs).get("alt"))
        if name in {"h1", "h2", "h3"}:
            self._heading += 1
            self._heading_parts = []

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name in {"script", "style", "noscript", "svg"}:
            if self._skip:
                self._skip -= 1
            return
        if self._skip:
            return
        if name == "a" and self._href:
            label = _text(" ".join(self._anchor)) or self._anchor_fallback
            label = re.sub(r"^couverture\s+(?:de\s+|du\s+|des\s+)", "", label, flags=re.I)
            if label:
                self.tokens.append(("link", label, self._href))
            self._href = ""
            self._anchor = []
            self._anchor_fallback = ""
        if name in {"h1", "h2", "h3"} and self._heading:
            label = _text(" ".join(self._heading_parts))
            if label:
                self.tokens.append(("heading", label, ""))
            self._heading = 0
            self._heading_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        value = _text(data)
        if not value:
            return
        if self._href:
            self._anchor.append(value)
            return
        if self._heading:
            self._heading_parts.append(value)
            return
        self.tokens.append(("text", value, ""))


def tokens(html: object) -> tuple[tuple[str, str, str], ...]:
    parser = _Parser()
    try:
        parser.feed(str(html or ""))
        parser.close()
    except Exception:
        return ()
    return tuple(parser.tokens)


def parse_published_age(value: object) -> PublishedAge | None:
    label = _text(value)
    match = _AGE_RE.fullmatch(label)
    if not match:
        return None
    age = int(match.group(1))
    if not 0 <= age < 18:
        return None
    return PublishedAge(label, age)


def listing_products(html: object, *, text_filter: str = "", limit: int = 12) -> tuple[tuple[str, str], ...]:
    query = _fold(text_filter)
    found: dict[str, str] = {}
    for kind, label, href in tokens(html):
        if kind != "link":
            continue
        url = product_url(href)
        if not url:
            continue
        clean = _text(label)
        low = _fold(clean)
        if low in _GENERIC_LINKS or low.startswith("voir "):
            continue
        previous = found.get(url, "")
        if not previous or len(clean) > len(previous):
            found[url] = clean
    out = []
    for url, title in found.items():
        if query and query not in _fold(title):
            continue
        out.append((title, url))
        if len(out) >= max(1, min(int(limit), 36)):
            break
    return tuple(out)


def next_cursor(html: object, *, current_page: int) -> str:
    expected = int(current_page) + 1
    if expected > 100:
        return ""
    for kind, label, href in tokens(html):
        if kind != "link":
            continue
        url = allowed_url(href)
        if not url:
            continue
        match = _LISTING_PATH.fullmatch(urlsplit(url).path)
        if not match:
            continue
        page = int(match.group(1) or "1")
        if page == expected:
            return str(page)
    return ""


def _next_value(rows: tuple[tuple[str, str, str], ...], label_names: set[str]) -> str:
    for index, (kind, value, _href) in enumerate(rows):
        if kind != "text" or _fold(value) not in label_names:
            continue
        for next_kind, next_value, _next_href in rows[index + 1:index + 5]:
            if next_kind in {"text", "heading"}:
                candidate = _text(next_value)
                if candidate and _fold(candidate) not in label_names:
                    return candidate
    return ""


def _detail_title(rows: tuple[tuple[str, str, str], ...], fallback: str) -> str:
    for kind, value, _ in rows:
        if kind == "heading" and _text(value):
            return _text(value)
    return _text(fallback)


def parse_detail(html: object, *, url: str, fallback_title: str = "") -> Mapping[str, object] | None:
    notice = product_url(url)
    if not notice:
        return None
    rows = tokens(html)
    age_label = _next_value(rows, {"âge", "age"})
    age = parse_published_age(age_label)
    if age is None:
        return None

    isbn_raw = _next_value(rows, {"code isbn", "isbn"})
    if not isbn_raw:
        for kind, value, _ in rows:
            if kind != "text":
                continue
            match = _ISBN_RE.search(value)
            if match and ("isbn" in _fold(value)):
                isbn_raw = match.group(1)
                break
    isbn = normalize_isbn(isbn_raw)
    if not isbn:
        return None

    authors = ()
    raw_author = _next_value(rows, {"auteur", "auteurs", "auteur-illustrateur", "autrice", "autrices"})
    if raw_author:
        authors = tuple(part.strip() for part in re.split(r"\s*[,;]\s*", raw_author) if part.strip())

    collection = _next_value(rows, {"collection"})
    themes_raw = _next_value(rows, {"thème", "thèmes", "theme", "themes"})
    themes = tuple(part for part in re.split(r"\s{2,}|[,;]", themes_raw) if _text(part))

    title = _detail_title(rows, fallback_title)
    return {
        "record_id": isbn,
        "source_item_id": isbn,
        "title": title,
        "authors": authors,
        "isbn13": isbn,
        "notice_url": notice,
        "friendly_url": notice,
        "age_ranges": [{
            "age_min": age.age_min,
            "age_max": None,
            "age_kind": "publisher",
            "source_label": age.label,
            "source_id": "milan",
            "confidence": 1.0,
        }],
        "themes": tuple(_fold(value) for value in themes if _fold(value)),
        "metadata": {
            "age_evidence": "milan.product.explicit",
            "collection": collection,
        },
    }


class MilanYouthSource:
    def __init__(self, *, client: httpx.Client | None = None, timeout_seconds: float = 12.0) -> None:
        self._own_client = client is None
        self.client = client or httpx.Client(
            timeout=max(1.0, min(float(timeout_seconds), 30.0)),
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": "Cifereca-Youth-Milan/0.1",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "fr",
            },
        )

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    def source_info(self) -> Mapping[str, str]:
        return {
            "source_id": "milan",
            "display_name": "Éditions Milan",
            "homepage_url": CATALOG,
        }

    def _get(self, url: str, *, kind: str) -> str:
        allowed = allowed_url(url)
        if not allowed:
            raise ValueError("Milan URL rejected")
        path = urlsplit(allowed).path
        if kind == "listing":
            if not _LISTING_PATH.fullmatch(path):
                raise ValueError("only Milan catalogue pages are allowed")
        elif kind == "product":
            if not _PRODUCT_PATH.fullmatch(path):
                raise ValueError("only Milan product pages are allowed")
        else:
            raise ValueError("invalid Milan request kind")
        response = self.client.get(allowed)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"Milan HTTP {response.status_code}")
        content_type = _fold(response.headers.get("content-type"))
        if content_type and "html" not in content_type:
            raise RuntimeError("Milan response is not HTML")
        if len(response.content) > 2_000_000:
            raise RuntimeError("Milan response exceeds 2 MB")
        return response.text

    def browse(
        self,
        *,
        text_filter: str = "",
        cursor: str | None = None,
        limit: int = 8,
        age: int | None = None,
        themes: tuple[str, ...] = (),
    ) -> Mapping[str, object]:
        del themes
        try:
            page = int(cursor or "1")
        except ValueError:
            raise ValueError("invalid Milan page cursor")
        if not 1 <= page <= 100:
            raise ValueError("invalid Milan page cursor")
        # Validate age before requesting even the listing: an empty page
        # must never hide a malformed/out-of-range youth filter.
        wanted_age = None
        if age is not None:
            try:
                wanted_age = int(age)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("age must be an integer") from exc
            if not 0 <= wanted_age < 18:
                raise ValueError("age must be between 0 and 17")
        bounded = max(1, min(int(limit), 8))
        listing_html = self._get(listing_url(page=page), kind="listing")
        products = listing_products(listing_html, text_filter=text_filter, limit=36)
        # An unfiltered paginated catalogue with no recognizable cards is a
        # parser/markup compatibility problem, not a valid empty book result.
        if not products and not text_filter and next_cursor(listing_html, current_page=page):
            raise RuntimeError("Milan paginated catalogue has no recognizable book links")

        out = []
        inspected = 0
        for title, url in products:
            if len(out) >= bounded or inspected >= 8:
                break
            inspected += 1
            detail = parse_detail(self._get(url, kind="product"), url=url, fallback_title=title)
            if detail is None:
                continue
            if wanted_age is not None and int(detail["age_ranges"][0]["age_min"]) > wanted_age:
                continue
            out.append(detail)

        return {
            "items": tuple(out),
            "next_cursor": next_cursor(listing_html, current_page=page),
            "fresh_for_seconds": 86400,
        }

    def search(self, query: str, *, cursor: str | None = None, limit: int = 8) -> Mapping[str, object]:
        return self.browse(text_filter=_text(query), cursor=cursor, limit=limit)


PLUGIN = MilanYouthSource()
