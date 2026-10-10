"""Gallimard Jeunesse catalogue adapter, based on the user-supplied HTML snapshot.

No undocumented API, scripted browsing, CAPTCHA bypass or product-page crawling.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from time import perf_counter
from typing import Callable, Mapping
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
import re

import httpx

BASE = "https://www.gallimard-jeunesse.fr"
CATALOG = BASE + "/catalogue.html"
BOOK_PATH = re.compile(r"^/(97[89]\d{10})/[a-z0-9][a-z0-9-]*\.html$")
_RANGE = re.compile(r"^de\s+(\d{1,2})\s+à\s+(\d{1,2})\s+ans?$", re.I)
_FROM = re.compile(r"^à\s+partir\s+de\s+(\d{1,2})\s+ans?$", re.I)
_WS = re.compile(r"\s+")
_EAN = re.compile(r"\b(?:EAN\s*:\s*)?(97[89]\d{10})\b", re.I)
_SKIP = {"script", "style", "noscript", "svg", "template"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_MAX_HTML_BYTES = 2_000_000
_MAX_RESULTS = 40
_MAX_PAGE = 100


def clean(value: object) -> str:
    return _WS.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def valid_isbn(value: object) -> str:
    s = re.sub(r"[^0-9]", "", clean(value))
    if len(s) != 13 or not s.startswith(("978", "979")):
        return ""
    return s if sum(int(x) * (1 if i % 2 == 0 else 3) for i, x in enumerate(s)) % 10 == 0 else ""


def allowed_url(value: object) -> str:
    raw = clean(value)
    if not raw or raw.startswith(("//", "\\")):
        return ""
    candidate = urljoin(BASE + "/", raw)
    try:
        parts = urlsplit(candidate)
        if parts.scheme != "https" or parts.hostname != "www.gallimard-jeunesse.fr":
            return ""
        if parts.username or parts.password or parts.port not in (None, 443):
            return ""
    except ValueError:
        return ""
    return urlunsplit(("https", "www.gallimard-jeunesse.fr", parts.path or "/", parts.query, ""))


def book_url(value: object, isbn: str = "") -> str:
    url = allowed_url(value)
    if not url:
        return ""
    match = BOOK_PATH.fullmatch(urlsplit(url).path)
    if not match or (isbn and match.group(1) != isbn):
        return ""
    return url


def cover_url(value: object) -> str:
    """Never return the browser's saved catalogue_files paths as public covers."""
    raw = clean(value)
    if not raw.startswith("https://"):
        return ""
    url = allowed_url(raw)
    if not url:
        return ""
    path = urlsplit(url).path.casefold()
    return url if re.search(r"\.(?:png|jpe?g|webp)$", path) else ""


def parse_age(value: object) -> tuple[int, int | None, str] | None:
    label = clean(value)
    match = _RANGE.fullmatch(label)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if 0 <= lower <= upper < 18:
            return lower, upper, label
        return None
    match = _FROM.fullmatch(label)
    if match:
        minimum = int(match.group(1))
        if 0 <= minimum < 18:
            return minimum, None, label
    return None


class Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str = "", attrs: dict | None = None) -> None:
        self.tag, self.attrs, self.children = tag, attrs or {}, []

    def has(self, classname: str) -> bool:
        return classname in (self.attrs.get("class") or "").split()

    def descendants(self, classname: str):
        for child in self.children:
            if isinstance(child, Node):
                if child.has(classname):
                    yield child
                yield from child.descendants(classname)

    def first(self, classname: str):
        return next(self.descendants(classname), None)

    def elements(self, tag: str):
        for child in self.children:
            if isinstance(child, Node):
                if child.tag == tag:
                    yield child
                yield from child.elements(tag)

    def text(self) -> str:
        parts = []
        for child in self.children:
            if isinstance(child, Node):
                parts.append(child.text())
            else:
                parts.append(child)
        return clean(" ".join(parts))


class _DOM(HTMLParser):
    """Minimal stdlib tree. Scripts and telemetry are ignored completely."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.skip:
            if tag in _SKIP:
                self.skip += 1
            return
        if tag in _SKIP:
            self.skip = 1
            return
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip:
            if tag in _SKIP:
                self.skip -= 1
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not self.skip and clean(data):
            self.stack[-1].children.append(data)


def document(html: str) -> Node:
    parser = _DOM()
    parser.feed(html)
    parser.close()
    return parser.root


def _card(row: Node, reasons: dict[str, int]) -> dict | None:
    def reject(reason):
        reasons[reason] = reasons.get(reason, 0) + 1
        return None

    title_node = row.first("BookItem-title")
    anchor = next(title_node.elements("a"), None) if title_node else None
    title = title_node.text() if title_node else ""
    ean_node = row.first("BookItem-ean")
    match = _EAN.search(ean_node.text()) if ean_node else None
    isbn = valid_isbn(match.group(1)) if match else ""
    if not isbn:
        return reject("isbn_invalid_or_absent")
    url = book_url(anchor.attrs.get("href") if anchor else "", isbn)
    if not url or not title:
        return reject("book_link_or_title_invalid")
    age_node = row.first("BookItem-infosAge")
    age = parse_age(age_node.text() if age_node else "")
    if age is None:
        return reject("publisher_age_missing_or_invalid")
    # Contributors are direct <p> elements of the text block, not nested links;
    # the role is published in the paragraph rather than inferred from names.
    authors, illustrators = [], []
    for paragraph in row.descendants("BookItem-contributor"):
        if paragraph.tag != "p":
            continue
        role = "illustrator" if paragraph.text().casefold().startswith("illustré par") else "author"
        for element in paragraph.elements("a"):
            person = clean(element.attrs.get("title") or element.text())
            if person:
                (illustrators if role == "illustrator" else authors).append(person)
    authors = tuple(dict.fromkeys(authors))
    illustrators = tuple(dict.fromkeys(illustrators))
    front = row.first("BookFlip-coverFront")
    back = row.first("BookFlip-coverBack")
    front_url = cover_url((front.attrs.get("src") or front.attrs.get("data-src")) if front else "")
    back_url = cover_url((back.attrs.get("src") or back.attrs.get("data-src")) if back else "")
    subtitle_node = row.first("BookItem-subtext")
    price_node = row.first("BookItem-infosPrice")
    minimum, maximum, label = age
    metadata = {
        "age_evidence": "gallimard.catalogue.explicit",
        "age_label": label,
        "contributor_roles": {"authors": authors, "illustrators": illustrators},
    }
    if illustrators:
        metadata["illustrators"] = illustrators
    if subtitle_node and subtitle_node.text():
        metadata["subtitle"] = subtitle_node.text()
    if price_node and price_node.text():
        metadata["publisher_price_label"] = price_node.text()
    if back_url:
        metadata["cover_back_url"] = back_url
    record = {
        "record_id": isbn,
        "source_item_id": isbn,
        "title": title,
        "authors": authors,
        "isbn13": isbn,
        "notice_url": url,
        "friendly_url": url,
        "age_ranges": [{
            "age_min": minimum, "age_max": maximum, "age_kind": "publisher",
            "source_label": label, "source_id": "gallimard-jeunesse", "confidence": 1.0,
        }],
        "metadata": metadata,
    }
    if front_url:
        record["cover_url"] = front_url
    return record


def parse_listing(html: str, *, limit: int = 40, diagnostic: dict | None = None) -> tuple[dict, ...]:
    root = document(html)
    cards = tuple(root.descendants("BookItem"))
    reasons: dict[str, int] = {}
    found: dict[str, dict] = {}
    for row in cards:
        record = _card(row, reasons)
        if record is not None:
            isbn = record["isbn13"]
            if isbn in found:
                reasons["duplicate_edition"] = reasons.get("duplicate_edition", 0) + 1
            else:
                found[isbn] = record
    bounded = max(1, min(int(limit), _MAX_RESULTS))
    out = tuple(list(found.values())[:bounded])
    count = root.first("SearchResults-count")
    declared_count = None
    if count and count.text().replace(" ", "").isdigit():
        declared_count = int(count.text().replace(" ", ""))
    if not cards and declared_count != 0:
        raise InvalidCatalogueStructureError("Gallimard catalogue layout not recognized")
    if diagnostic is not None:
        diagnostic.update({
            "query_mode": "remote", "candidate_count": len(cards),
            "isbn_valid_count": len(found), "age_qualified_count": len(found),
            "returned_items": len(out), "declared_count": declared_count,
            "truncated_by_limit": len(found) > bounded,
            "rejection_reasons": reasons,
        })
    return out


def search_url(text: str, *, page: int = 1, age: int | None = None, themes=()) -> str:
    if not 1 <= page <= _MAX_PAGE:
        raise ValueError("page out of range")
    params = [("text", clean(text)[:200])]
    if age is not None:
        if not isinstance(age, int) or isinstance(age, bool) or not 0 <= age <= 17:
            raise ValueError("invalid juvenile age")
        params.append(("age[0]", str(age)))
    if themes:
        params.append(("topic[0]", clean(themes[0])[:100]))
    if page > 1:
        params.append(("page", str(page)))
    return CATALOG + "?" + urlencode(params)


def next_cursor(html: str, *, current_page: int, query: str = "", age=None, themes=()) -> str:
    """No invented pagination: only follow a real same-origin next-page link."""
    if current_page >= _MAX_PAGE:
        return ""
    root = document(html)
    for anchor in root.elements("a"):
        href = allowed_url(anchor.attrs.get("href", ""))
        if not href or urlsplit(href).path != "/catalogue.html":
            continue
        qs = parse_qs(urlsplit(href).query)
        expected = current_page + 1
        if qs.get("page") != [str(expected)]:
            continue
        if qs.get("text", [""])[0] != clean(query)[:200]:
            continue
        if age is not None and qs.get("age[0]") != [str(age)]:
            continue
        if themes and qs.get("topic[0]") != [clean(themes[0])[:100]]:
            continue
        if anchor.attrs.get("rel") == "next" or "suivant" in anchor.text().casefold():
            return str(expected)
    return ""


class InvalidCatalogueStructureError(RuntimeError):
    pass


class GallimardYouthSource:
    def __init__(self, *, client: httpx.Client | None = None, timeout_seconds: float = 12.0):
        self.context = None
        self._own_client = client is None
        self.client = client or httpx.Client(
            follow_redirects=False, trust_env=False,
            timeout=max(1.0, min(float(timeout_seconds), 30.0)),
            headers={"User-Agent": "Cifereca-Youth-Gallimard/0.1", "Accept": "text/html,application/xhtml+xml", "Accept-Language": "fr"},
        )
        self._last_diagnostic: dict = {}
        self._call_requests: ContextVar[int | None] = ContextVar("gallimard_requests", default=None)

    def activate(self, context):
        self.context = context
        return self

    def close(self):
        if self._own_client:
            self.client.close()

    def source_info(self) -> Mapping[str, str]:
        return {"source_id": "gallimard-jeunesse", "display_name": "Gallimard Jeunesse", "homepage_url": CATALOG}

    def diagnostic_snapshot(self) -> dict:
        return dict(self._last_diagnostic)

    def _publish_diagnostic(self, status: str):
        if self.context is not None:
            try:
                self.context.report_diagnostic("Gallimard Jeunesse catalogue", status, detail=self.diagnostic_snapshot(), level="normal")
            except Exception:
                pass

    def _get(self, url: str) -> str:
        safe = allowed_url(url)
        if not safe or urlsplit(safe).path != "/catalogue.html":
            raise ValueError("URL outside Gallimard catalogue")
        self._last_diagnostic["request_count"] += 1
        counter = self._call_requests.get()
        if counter is not None:
            self._call_requests.set(counter + 1)
        response = self.client.get(safe)
        self._last_diagnostic["http_status"] = response.status_code
        if response.status_code != 200:
            error = RuntimeError(f"Gallimard HTTP {response.status_code}")
            error.http_status = response.status_code
            raise error
        if "html" not in response.headers.get("content-type", "text/html").casefold():
            raise RuntimeError("Gallimard response is not HTML")
        if len(response.content) > _MAX_HTML_BYTES:
            raise RuntimeError("Gallimard response exceeds 2 MB")
        self._last_diagnostic["html_sha256_prefix"] = sha256(response.content).hexdigest()[:16]
        return response.text

    def browse(self, *, text_filter: str = "", cursor: str | None = None,
               limit: int = 40, age: int | None = None, themes=(),
               cancelled: Callable[[], bool] | None = None) -> Mapping[str, object]:
        if age is not None and (not isinstance(age, int) or isinstance(age, bool) or not 0 <= age < 18):
            raise ValueError("invalid juvenile age")
        if cursor is not None and (not isinstance(cursor, str) or not re.fullmatch(r"[1-9]\d{0,2}", cursor)):
            raise ValueError("invalid page cursor")
        page = int(cursor or 1)
        if not 1 <= page <= _MAX_PAGE:
            raise ValueError("invalid page cursor")
        if cancelled and cancelled():
            raise RuntimeError("Gallimard collection cancelled")
        bounded = max(1, min(int(limit), _MAX_RESULTS))
        self._last_diagnostic = {
            "source": "gallimard-jeunesse", "attempted_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "page": page, "request_count": 0, "text_filter_present": bool(clean(text_filter)), "phase": "listing",
        }
        token = self._call_requests.set(0)
        pipeline = {"query_mode": "remote", "effective_limit": bounded}
        started = perf_counter()
        try:
            url = search_url(text_filter, page=page, age=age, themes=themes)
            html = self._get(url)
            if cancelled and cancelled():
                raise RuntimeError("Gallimard collection cancelled")
            rows = parse_listing(html, limit=bounded, diagnostic=pipeline)
            nxt = next_cursor(html, current_page=page, query=text_filter, age=age, themes=themes)
            pipeline["total_ms"] = max(0, int((perf_counter() - started) * 1000))
            self._last_diagnostic.update({k: v for k, v in pipeline.items() if k != "rejection_reasons"})
            self._last_diagnostic["rejection_reasons"] = dict(pipeline.get("rejection_reasons", {}))
            self._last_diagnostic.update({"phase": "complete", "state": "items" if rows else "empty", "next_cursor_present": bool(nxt)})
            self._publish_diagnostic("ok" if rows else "empty")
            return {"items": rows, "next_cursor": nxt, "fresh_for_seconds": 86400,
                    "collection_request_count": self._call_requests.get(), "collection_pipeline": dict(pipeline)}
        except Exception as exc:
            exc.collection_pipeline = dict(pipeline)
            exc.collection_request_count = self._call_requests.get()
            self._last_diagnostic.update({"state": "invalid" if isinstance(exc, InvalidCatalogueStructureError) else "error",
                                          "error_type": type(exc).__name__})
            self._publish_diagnostic("error")
            raise
        finally:
            self._call_requests.reset(token)

    def browse_with_cancellation(self, *, cancelled, text_filter: str = "", cursor=None,
                                 limit=40, age=None, themes=()):
        return self.browse(text_filter=text_filter, cursor=cursor, limit=limit,
                           age=age, themes=themes, cancelled=cancelled)

    def search(self, query: str, *, cursor: str | None = None, limit: int = 40) -> Mapping[str, object]:
        return self.browse(text_filter=query, cursor=cursor, limit=limit)


PLUGIN = GallimardYouthSource()
