from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from typing import Iterable, Mapping
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import httpx

BASE = "https://www.booktrust.org.uk/"
BOOKFINDER = urljoin(BASE, "book-recommendations/bookfinder/")
RECOMMENDATIONS = urljoin(BOOKFINDER, "bookfinder-recommendations/")
BOOK_PATH_PREFIX = "/book-recommendations/bookfinder/"
AGE_GROUPS = (
    (0, 1, "0 to 1 year"),
    (1, 2, "1 to 2 years"),
    (2, 3, "2 to 3 years"),
    (3, 4, "3 to 4 years"),
    (4, 5, "4 to 5 years"),
    (5, 7, "5 to 7 years"),
    (7, 9, "7 to 9 years"),
    (9, 11, "9 to 11 years"),
    (11, 14, "11 to 14 years"),
)
_WS = re.compile(r"\s+")
AGE_LABEL_RE = re.compile(
    r"\b(?P<prefix>Interest age|Reading age|For ages?)\s*:?\s*"
    r"(?P<lo>\d{1,2})(?:\s*(?:to|[-–—])\s*(?P<hi>\d{1,2})|\s*(?P<plus>\+|and up))?",
    re.I,
)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _text(value: object) -> str:
    return _WS.sub(" ", str(value or "").replace("\xa0", " ").strip())


def _fold(value: object) -> str:
    return _text(value).casefold()


def _allowed_url(value: object) -> str:
    raw = _text(value)
    if not raw:
        return ""
    absolute = urljoin(BASE, raw)
    try:
        parts = urlsplit(absolute)
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme != "https" or parts.hostname not in {"booktrust.org.uk", "www.booktrust.org.uk"}:
        return ""
    if parts.username or parts.password or port not in (None, 443):
        return ""
    return urlunsplit(("https", "www.booktrust.org.uk", parts.path or "/", parts.query, ""))


def age_group_for(age: int) -> str:
    value = int(age)
    if not 0 <= value < 18:
        raise ValueError("age must be between 0 and 17")
    matches = [label for lo, hi, label in AGE_GROUPS if lo <= value <= hi]
    if not matches:
        return ""
    return min(matches, key=lambda label: next(hi - lo for lo, hi, candidate in AGE_GROUPS if candidate == label))


@dataclass(frozen=True, slots=True)
class BookCard:
    title: str
    url: str
    author: str = ""
    year: int | None = None
    recommended_age: str = ""
    themes: tuple[str, ...] = ()


class _SemanticTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple[str, str, str]] = []
        self._href = ""
        self._anchor_parts: list[str] = []
        self._tag_stack: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        name = tag.casefold()
        self._tag_stack.append(name)
        if name in {"script", "style", "noscript", "svg"}:
            self._skip += 1
            return
        if name == "a" and not self._skip:
            self._href = dict(attrs).get("href", "")
            self._anchor_parts = []

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name in {"script", "style", "noscript", "svg"}:
            if self._skip:
                self._skip -= 1
            if self._tag_stack:
                self._tag_stack.pop()
            return
        if name == "a" and self._href and not self._skip:
            label = _text(" ".join(self._anchor_parts))
            self.tokens.append(("link", label, self._href))
            self._href = ""
            self._anchor_parts = []
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        value = _text(data)
        if not value:
            return
        if self._href:
            self._anchor_parts.append(value)
            return
        self.tokens.append(("text", value, ""))


def _tokens(html: str) -> list[tuple[str, str, str]]:
    parser = _SemanticTextParser()
    parser.feed(html or "")
    parser.close()
    return parser.tokens


def _is_book_url(url: str) -> bool:
    allowed = _allowed_url(url)
    if not allowed:
        return False
    path = urlsplit(allowed).path.rstrip("/") + "/"
    if not path.startswith(BOOK_PATH_PREFIX):
        return False
    return path not in {
        BOOK_PATH_PREFIX,
        BOOK_PATH_PREFIX + "bookfinder-recommendations/",
    }


def _age_label(text: str, prefix: str = "For ages") -> str:
    for match in AGE_LABEL_RE.finditer(text):
        if _fold(match.group("prefix")) != _fold(prefix):
            continue
        lo = match.group("lo")
        hi = match.group("hi")
        plus = match.group("plus")
        return f"{lo} to {hi}" if hi else f"{lo}+" if plus else lo
    return ""


def parse_recommendations(html: str, *, text_filter: str = "", limit: int = 50) -> tuple[BookCard, ...]:
    tokens = _tokens(html)
    starts: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for index, (kind, label, href) in enumerate(tokens):
        if kind != "link":
            continue
        url = _allowed_url(href)
        if not url or not _is_book_url(url) or url in seen:
            continue
        title = _text(label)
        if not title:
            continue
        seen.add(url)
        starts.append((index, title, url))

    cards: list[BookCard] = []
    q = _fold(text_filter)
    for pos, (start, title, url) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(tokens)
        segment = [_text(value) for kind, value, _ in tokens[start + 1:end] if kind == "text" and _text(value)]
        blob = " ".join(segment)
        author = ""
        for value in segment[:10]:
            m = re.search(r"^(?:by|edited by)\s+(.+?)(?:\s+(?:19|20)\d{2})?$", value, re.I)
            if m:
                author = _text(m.group(1))
                break
        year_match = YEAR_RE.search(blob)
        year = int(year_match.group(0)) if year_match else None
        recommended = _age_label(blob, "For ages") or _age_label(blob, "For age")
        themes: list[str] = []
        for value in segment:
            low = _fold(value)
            if (
                value == title
                or low.startswith(("by ", "edited by ", "for age", "interest age", "reading age"))
                or YEAR_RE.fullmatch(value)
                or len(value) > 48
            ):
                continue
            if value not in themes:
                themes.append(value)
        haystack = " ".join((title, author, *themes)).casefold()
        if q and q not in haystack:
            continue
        cards.append(BookCard(title, url, author, year, recommended, tuple(themes[:12])))
        if len(cards) >= max(1, min(int(limit), 100)):
            break
    return tuple(cards)


def parse_detail(html: str, *, url: str) -> Mapping[str, object]:
    tokens = _tokens(html)
    text_values = [_text(value) for kind, value, _ in tokens if kind == "text" and _text(value)]
    title = ""
    author = ""
    publisher = ""
    interest_age = ""
    reading_age = ""
    year: int | None = None
    themes: list[str] = []

    for kind, label, href in tokens:
        if kind == "link" and _allowed_url(href).rstrip("/") == _allowed_url(url).rstrip("/") and _text(label):
            title = _text(label)
            break
    for index, value in enumerate(text_values):
        low = _fold(value)
        if not title and low not in {"find a book", "donate", "bookfinder"} and not low.startswith(("interest age", "reading age", "published by")):
            title = value
        if not author:
            m = re.match(r"^(?:by|edited by)\s+(.+)$", value, re.I)
            if m:
                author = _text(m.group(1))
        if low.startswith("interest age"):
            interest_age = _age_label(value, "Interest age")
        elif low.startswith("reading age"):
            reading_age = _age_label(value, "Reading age")
        elif low.startswith("published by"):
            publisher = _text(re.sub(r"^published by\s+", "", value, flags=re.I))
            y = YEAR_RE.search(value)
            if y:
                year = int(y.group(0))
                publisher = _text(re.sub(r",?\s*(19|20)\d{2}\s*$", "", publisher))
        elif 0 < index < len(text_values) - 1 and len(value) <= 40:
            if low not in {"about this book", "about the author", "more books like this", "show more"} and not low.startswith(("interest age", "reading age", "published by")):
                if value not in themes and not re.match(r"^(?:by|edited by)\b", value, re.I):
                    themes.append(value)

    return {
        "record_id": urlsplit(_allowed_url(url)).path.rstrip("/").split("/")[-1],
        "title": title,
        "author": author,
        "notice_url": _allowed_url(url),
        "friendly_url": _allowed_url(url),
        "interest_age": interest_age,
        "reading_age": reading_age,
        "publisher": publisher,
        "published_year": year,
        "themes": tuple(themes[:20]),
        "metadata": {"age_evidence": "booktrust.detail.explicit"},
    }


def next_cursor(html: str, *, current_page: int) -> str:
    expected = int(current_page) + 1
    if expected > 100:
        return ""
    for kind, _label, href in _tokens(html):
        if kind != "link":
            continue
        url = _allowed_url(href)
        if not url or urlsplit(url).path.rstrip("/") + "/" != urlsplit(RECOMMENDATIONS).path.rstrip("/") + "/":
            continue
        query = parse_qs(urlsplit(url).query)
        try:
            page = int((query.get("page") or [""])[0])
        except (TypeError, ValueError):
            continue
        if page == expected:
            return str(page)
    return ""


def _query_url(*, age: int | None = None, themes: Iterable[str] = (), page: int = 1) -> str:
    params: list[tuple[str, str]] = []
    if age is not None:
        group = age_group_for(age)
        if group:
            params.append(("ageGroups", group))
    for theme in themes:
        cleaned = _text(theme)
        if cleaned:
            params.append(("bookfinderCategories[]", cleaned))
    params.append(("page", str(max(1, int(page)))))
    return RECOMMENDATIONS + "?" + urlencode(params)


class BookTrustSource:
    def __init__(self, *, client: httpx.Client | None = None, timeout_seconds: float = 12.0) -> None:
        self._own_client = client is None
        self.client = client or httpx.Client(
            timeout=max(1.0, min(float(timeout_seconds), 30.0)),
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": "Cifereca-Youth-BookTrust/0.1",
                "Accept-Language": "en-GB,en;q=0.8",
            },
        )

    def source_info(self) -> Mapping[str, str]:
        return {
            "source_id": "booktrust",
            "display_name": "BookTrust Bookfinder",
            "homepage_url": BOOKFINDER,
        }

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    def _get_text(self, url: str) -> str:
        allowed = _allowed_url(url)
        if not allowed:
            raise ValueError("BookTrust URL outside allowlist")
        response = self.client.get(allowed)
        if response.status_code >= 300:
            raise RuntimeError(f"BookTrust HTTP {response.status_code}")
        content_type = _fold(response.headers.get("content-type", ""))
        if content_type and "html" not in content_type:
            raise RuntimeError("BookTrust response is not HTML")
        if len(response.content) > 2_000_000:
            raise RuntimeError("BookTrust response exceeds 2 MB")
        return response.text

    def browse(
        self,
        *,
        age: int | None = None,
        themes: Iterable[str] = (),
        text_filter: str = "",
        cursor: str | None = None,
        limit: int = 50,
    ) -> Mapping[str, object]:
        try:
            page = int(cursor or "1")
        except (TypeError, ValueError):
            raise ValueError("invalid BookTrust page cursor")
        if not 1 <= page <= 100:
            raise ValueError("invalid BookTrust page cursor")
        if age is not None and not age_group_for(int(age)):
            return {"items": (), "next_cursor": "", "fresh_for_seconds": 86_400}
        bounded = max(1, min(int(limit), 50))
        url = _query_url(age=age, themes=themes, page=page)
        html = self._get_text(url)
        cards = parse_recommendations(html, text_filter=text_filter, limit=bounded)
        items = [
            {
                "record_id": card.url.rstrip("/").split("/")[-1],
                "title": card.title,
                "author": card.author,
                "notice_url": card.url,
                "friendly_url": card.url,
                "recommended_age": card.recommended_age,
                "themes": card.themes,
                "published_year": card.year,
                "metadata": {"age_evidence": "booktrust.bookfinder.explicit" if card.recommended_age else ""},
            }
            for card in cards
        ]
        return {
            "items": items,
            "next_cursor": next_cursor(html, current_page=page),
            "fresh_for_seconds": 86_400,
        }

    def search(self, query: str, *, cursor: str | None = None, limit: int = 50) -> Mapping[str, object]:
        return self.browse(text_filter=_text(query), cursor=cursor, limit=limit)

    def detail(self, url: str) -> Mapping[str, object]:
        allowed = _allowed_url(url)
        if not allowed or not _is_book_url(allowed):
            raise ValueError("not a BookTrust Bookfinder book URL")
        return parse_detail(self._get_text(allowed), url=allowed)


PLUGIN = BookTrustSource()
