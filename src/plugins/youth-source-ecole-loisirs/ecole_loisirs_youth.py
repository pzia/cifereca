from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from time import perf_counter
import re
from typing import Mapping
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import httpx

BASE = "https://www.ecoledesloisirs.fr"
SEARCH = BASE + "/recherche-avancee"
_WS = re.compile(r"\s+")
_RANGE = re.compile(r"^\s*(\d{1,2})\s*à\s*(\d{1,2})\s*ans\s*$", re.I)
_FROM = re.compile(r"^\s*à\s+partir\s+de\s+(\d{1,2})\s*ans\s*$", re.I)
_PLUS = re.compile(r"^\s*(\d{1,2})\s*ans\s+et\s*\+\s*$", re.I)
_ISBN = re.compile(r"\bISBN\s*:\s*([0-9Xx -]{10,20})\b", re.I)
_MAX_QUERY_CHARS = 300
_TYPES = {
    "album": "album",
    "roman": "roman",
    "théâtre": "theatre",
    "theatre": "theatre",
    "jeu": "jeu",
    "cahier": "cahier",
    "documentaire": "documentaire",
}


def _text(value: object) -> str:
    return _WS.sub(" ", str(value or "").replace("\xa0", " ").strip())


def _fold(value: object) -> str:
    return _text(value).casefold()


def normalize_isbn13(value: object) -> str:
    raw = re.sub(r"[^0-9Xx]", "", _text(value)).upper()
    if len(raw) == 10 and re.fullmatch(r"[0-9]{9}[0-9X]", raw):
        total = sum((10 - i) * int(d) for i, d in enumerate(raw[:9]))
        total += 10 if raw[-1] == "X" else int(raw[-1])
        if total % 11:
            return ""
        prefix = "978" + raw[:9]
        check = (10 - sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(prefix)) % 10) % 10
        return prefix + str(check)
    # A valid GTIN/EAN checksum does not make a product identifier an ISBN:
    # only Bookland 978/979 are ISBN-13 prefixes (e.g. toys use other EANs).
    if len(raw) == 13 and raw.isdigit() and raw.startswith(("978", "979")):
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(raw))
        return raw if total % 10 == 0 else ""
    return ""


@dataclass(frozen=True, slots=True)
class PublishedAge:
    label: str
    age_min: int | None
    age_max: int | None
    qualified: bool = True


def parse_age_label(value: object) -> PublishedAge | None:
    label = _text(value)
    if not label:
        return None
    if _fold(label) == "indéterminé":
        return PublishedAge(label, None, None, False)
    match = _RANGE.fullmatch(label)
    if match:
        lo, hi = int(match.group(1)), int(match.group(2))
        if 0 <= lo <= hi < 18:
            return PublishedAge(label, lo, hi)
        return None
    match = _FROM.fullmatch(label)
    if match:
        lo = int(match.group(1))
        return PublishedAge(label, lo, None) if 0 <= lo < 18 else None
    match = _PLUS.fullmatch(label)
    if match:
        lo = int(match.group(1))
        return PublishedAge(label, lo, None) if 0 <= lo < 18 else None
    return None


def _allowed_url(value: object) -> str:
    raw = _text(value)
    if not raw:
        return ""
    url = urljoin(BASE + "/", raw)
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme.casefold() != "https" or parts.hostname != "www.ecoledesloisirs.fr":
        return ""
    if parts.username or parts.password or port not in (None, 443):
        return ""
    return urlunsplit(("https", parts.hostname, parts.path or "/", parts.query, ""))


def _product_url(value: object) -> str:
    url = _allowed_url(value)
    return url if url and urlsplit(url).path.startswith("/livre/") else ""


class _TokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple[str, str, str]] = []
        self._skip = 0
        self._href = ""
        self._anchor: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        name = tag.casefold()
        if name in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        if name == "a" and not self._skip:
            self._href = dict(attrs).get("href", "")
            self._anchor = []

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name == "a" and self._href and not self._skip:
            label = _text(" ".join(self._anchor))
            if label:
                self.tokens.append(("link", label, self._href))
            self._href = ""
            self._anchor = []
        if name in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        value = _text(data)
        if not value:
            return
        if self._href:
            self._anchor.append(value)
        else:
            self.tokens.append(("text", value, ""))


def _tokens(html: str) -> tuple[tuple[str, str, str], ...]:
    parser = _TokenParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return ()
    return tuple(parser.tokens)


def _nearest_age(tokens, start: int) -> PublishedAge | None:
    for index in range(start - 1, max(-1, start - 10), -1):
        kind, value, href = tokens[index]
        if kind == "link" and _product_url(href):
            return None
        if kind != "text":
            continue
        age = parse_age_label(value)
        if age is not None:
            return age
    return None


def _card_isbn(tokens, start: int, end: int) -> str:
    for kind, value, _ in tokens[start + 1:end]:
        if kind != "text":
            continue
        match = _ISBN.search(value)
        if not match:
            continue
        isbn = normalize_isbn13(match.group(1))
        if isbn:
            return isbn
    return ""


def _card_type(tokens, start: int, end: int) -> str:
    for kind, value, _ in tokens[start + 1:end]:
        if kind != "text":
            continue
        typ = _TYPES.get(_fold(value))
        if typ:
            return typ
    return ""


def _card_authors(tokens, start: int, end: int) -> tuple[str, ...]:
    for kind, value, _ in tokens[start + 1:end]:
        if kind != "text":
            continue
        cleaned = _text(value)
        low = _fold(cleaned)
        if not cleaned:
            continue
        if low in _TYPES or low.startswith("- collection") or low.startswith("isbn:"):
            return ()
        if len(cleaned) > 140 or cleaned.endswith("..."):
            return ()
        return tuple(part.strip() for part in cleaned.split(",") if part.strip())
    return ()



class _ProductCards(HTMLParser):
    """Parse separate product-item cards, ignoring duplicate thumbnail cards."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.card = None
        self.cards = []
        self.field = None
        self._ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored += 1
            return
        if self._ignored:
            return
        a = dict(attrs)
        classes = set(a.get("class", "").split())
        if tag in {"script", "style", "noscript", "svg"}:
            return
        if self.card is None:
            if tag == "div" and "product-item" in classes:
                self.card = {"title": [], "authors": [], "info": [], "summary": [], "url": "", "cover_url": "", "cover_seen": False}
                self.stack = [tag]
            return
        if tag not in {"img", "br", "input", "hr", "meta", "link", "source"}:
            self.stack.append(tag)
        if tag == "img":
            self.card["cover_seen"] = True
        if tag == "img" and not self.card["cover_url"]:
            for candidate in (a.get("data-src"), a.get("src"), (a.get("srcset") or "").split(",")[0].strip().split(" ")[0]):
                url = _allowed_url(candidate)
                if url and urlsplit(url).path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) and "_files/" not in url:
                    self.card["cover_url"] = url
                    break
        if tag == "a":
            self.card["url"] = self.card["url"] or _product_url(a.get("href"))
        if "card-title" in classes:
            self.field = ("title", len(self.stack))
        elif "card-author" in classes:
            self.field = ("authors", len(self.stack))
        elif "card-infos" in classes:
            self.field = ("info", len(self.stack))
        elif "card-text" in classes:
            self.field = ("summary", len(self.stack))

    def handle_data(self, data):
        if self.card is not None and self.field and not self._ignored:
            value = _text(data)
            if value:
                self.card[self.field[0]].append(value)

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1
            return
        if self._ignored or self.card is None:
            return
        if self.field and self.field[1] == len(self.stack):
            self.field = None
        if self.stack:
            self.stack.pop()
        if not self.stack:
            self.cards.append(self.card)
            self.card = None


def parse_catalogue_cards(html: str, *, limit: int = 40, diagnostic=None):
    parser = _ProductCards()
    parser.feed(html or "")
    parser.close()
    out, seen = [], set()
    for card in parser.cards:
        url = card["url"]
        if not url or url in seen:
            continue
        seen.add(url)
        info = _text(" ".join(card["info"]))
        age_match = re.search(r"(?:âge|age)\s*:\s*(\d{1,2}\s*à\s*\d{1,2}\s*ans|\d{1,2}\s*ans\s*et\s*\+)", info, re.I)
        age = parse_age_label(age_match.group(1)) if age_match else None
        title = _text(" ".join(card["title"]))
        if not title or age is None or not age.qualified:
            continue
        slug = urlsplit(url).path.removeprefix("/livre/")
        item = {
            "record_id": slug, "source_item_id": slug, "title": title,
            "authors": tuple(dict.fromkeys(card["authors"])),
            "notice_url": url, "friendly_url": url, "book_type": "roman",
            "age_ranges": [{"age_min": age.age_min, "age_max": age.age_max,
                            "age_kind": "publisher", "source_label": age.label,
                            "source_id": "ecole-loisirs", "confidence": 1.0}],
            "metadata": {"age_label": age.label, "age_evidence": "ecole-loisirs.catalogue.explicit"},
        }
        if card["cover_url"]:
            item["cover_url"] = card["cover_url"]
        if card["summary"]:
            item["description"] = _text(" ".join(card["summary"]))
        if info:
            collection = _text(info.replace(age_match.group(0), "")) if age_match else info
            if collection:
                item["collection"] = collection
        out.append(item)
        if len(out) >= max(1, min(int(limit), 40)):
            break
    if diagnostic is not None:
        diagnostic.update({
            "cards_detected": len(parser.cards), "cards_complete": len(out),
            "candidate_count": len(seen), "analyzed_count": len(seen),
            "age_qualified_count": len(out), "returned_items": len(out),
            "missing_isbn": len(out), "isbn_valid_count": 0,
            "author_count": sum(len(row["authors"]) for row in out),
            "covers_seen": sum(bool(card["cover_seen"]) for card in parser.cards),
            "cover_urls_valid": sum(bool(row.get("cover_url")) for row in out),
        })
    return tuple(out)


def parse_listing(
    html: str, *, limit: int = 40,
    diagnostic: dict[str, object] | None = None,
) -> tuple[Mapping[str, object], ...]:
    """Parse only attested book editions; optionally count each filtering stage.

    The diagnostic contains integers and fixed public reason identifiers,
    never titles, ISBNs, product URLs, HTML or search terms.
    """
    if re.search(r'class\s*=\s*["\'][^"\']*\bproduct-item\b', html or ''):
        return parse_catalogue_cards(html, limit=limit, diagnostic=diagnostic)
    tokens = _tokens(html)
    products = []
    anchor_count = 0
    for index, (kind, label, href) in enumerate(tokens):
        if kind != "link":
            continue
        anchor_count += 1
        url = _product_url(href)
        if url and _fold(label) not in {"lire la suite"}:
            products.append((index, _text(label), url))

    bounded = max(1, min(int(limit), 40))
    reasons = {
        "age_unattested": 0, "isbn_invalid": 0,
        "unsupported_product_type": 0, "duplicate": 0,
    }
    out = []
    seen = set()
    examined = 0
    age_published = 0
    isbn_valid = 0
    age_qualified = 0
    age_unknown = 0
    truncated = False
    for pos, (start, title, url) in enumerate(products):
        if url in seen:
            reasons["duplicate"] += 1
            continue
        if len(out) >= bounded:
            truncated = True
            break
        examined += 1
        end = products[pos + 1][0] if pos + 1 < len(products) else len(tokens)
        age = _nearest_age(tokens, start)
        if age is None:
            reasons["age_unattested"] += 1
            continue
        age_published += 1
        isbn = _card_isbn(tokens, start, end)
        if not isbn:
            reasons["isbn_invalid"] += 1
            continue
        isbn_valid += 1
        book_type = _card_type(tokens, start, end)
        if book_type == "jeu":
            reasons["unsupported_product_type"] += 1
            continue
        item: dict[str, object] = {
            "record_id": isbn,
            "source_item_id": isbn,
            "title": title,
            "authors": _card_authors(tokens, start, end),
            "isbn13": isbn,
            "notice_url": url,
            "friendly_url": url,
            "book_type": book_type,
            "metadata": {
                "age_evidence": "ecole-loisirs.search.explicit" if age.qualified else "ecole-loisirs.search.unknown",
                "age_label": age.label,
            },
        }
        if age.qualified:
            item["age_ranges"] = [{
                "age_min": age.age_min,
                "age_max": age.age_max,
                "age_kind": "publisher",
                "source_label": age.label,
                "source_id": "ecole-loisirs",
                "confidence": 1.0,
            }]
            age_qualified += 1
        else:
            item["age_ranges"] = []
            age_unknown += 1
        out.append(item)
        seen.add(url)

    if diagnostic is not None:
        diagnostic.update({
            "query_mode": "remote", "effective_limit": bounded,
            "anchor_count": anchor_count,
            "product_link_count": len(products),
            "candidate_count": len(set(url for _, _, url in products)),
            "analyzed_count": examined,
            "isbn_valid_count": isbn_valid,
            "age_published_count": age_published,
            "age_qualified_count": age_qualified,
            "age_unknown_count": age_unknown,
            "returned_items": len(out),
            "truncated_by_limit": truncated,
            "rejection_reasons": {key: val for key, val in reasons.items() if val},
        })
    return tuple(out)

def next_cursor(html: str, *, current_page: int) -> str:
    for kind, label, href in _tokens(html):
        if kind != "link" or _fold(label) != "afficher plus de résultats":
            continue
        raw = _text(href)
        if raw.startswith("?"):
            url = SEARCH + raw
        else:
            url = _allowed_url(raw)
        if not url or urlsplit(url).path != "/recherche-avancee":
            continue
        query = parse_qs(urlsplit(url).query)
        try:
            page = int((query.get("page") or [""])[0])
        except ValueError:
            continue
        if page == int(current_page) + 1 and page <= 100:
            return str(page)
    return ""


def search_url(query: str, *, page: int) -> str:
    params = {
        "page": str(max(1, min(int(page), 100))),
        "search_api_views_fulltext": _text(query)[:_MAX_QUERY_CHARS],
        "types": "livre",
    }
    return SEARCH + "?" + urlencode(params)


class InvalidCatalogueStructureError(RuntimeError):
    """Publisher layout mismatch; never contains source HTML or private data."""


class EcoleLoisirsYouthSource:
    def __init__(self, *, client: httpx.Client | None = None, timeout_seconds: float = 12.0) -> None:
        self._own_client = client is None
        self.context = None
        self._last_diagnostic: dict[str, object] = {}
        self._call_requests: ContextVar[int | None] = ContextVar("youth_ecole_call_requests", default=None)
        self.client = client or httpx.Client(
            timeout=max(1.0, min(float(timeout_seconds), 30.0)),
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": "Cifereca-Youth-EcoleLoisirs/0.1",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "fr",
            },
        )

    def activate(self, context):
        self.context = context
        return self

    def diagnostic_snapshot(self) -> dict[str, object]:
        """Last explicit search outcome only; no URL, query, title or HTML."""
        return dict(self._last_diagnostic)

    def _publish_diagnostic(self, status: str) -> None:
        if self.context is not None:
            try:
                self.context.report_diagnostic(
                    "Ecole des loisirs youth catalogue collection", status,
                    detail=self.diagnostic_snapshot(), level="normal",
                )
            except Exception:
                # Diagnostics must never affect a publisher query.
                pass

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    def source_info(self) -> Mapping[str, str]:
        return {
            "source_id": "ecole-loisirs",
            "display_name": "l'école des loisirs",
            "homepage_url": SEARCH,
        }

    def _get(self, url: str, *, diagnostic: dict[str, object] | None = None) -> str:
        allowed = _allowed_url(url)
        if not allowed or urlsplit(allowed).path not in {"/recherche-avancee", "/catalogue/romans"}:
            raise ValueError("only l'école des loisirs advanced search is allowed")
        self._last_diagnostic["request_count"] = int(self._last_diagnostic.get("request_count", 0)) + 1
        counter = self._call_requests.get()
        if counter is not None:
            self._call_requests.set(counter + 1)
        response = self.client.get(allowed)
        evidence = {
            "http_status": response.status_code,
            "content_type_is_html": "html" in _fold(response.headers.get("content-type")),
            "html_bytes": len(response.content),
            "html_sha256_prefix": sha256(response.content).hexdigest()[:16],
        }
        self._last_diagnostic.update(evidence)
        if diagnostic is not None:
            diagnostic.update(evidence)
        if response.status_code >= 300:
            error = RuntimeError(f"l'école des loisirs HTTP {response.status_code}")
            error.http_status = response.status_code
            raise error
        content_type = _fold(response.headers.get("content-type"))
        if content_type and "html" not in content_type:
            raise RuntimeError("l'école des loisirs response is not HTML")
        if len(response.content) > 2_000_000:
            raise RuntimeError("l'école des loisirs response exceeds 2 MB")
        return response.text

    def search_catalogue(self, query: str, *, page: int = 0, limit: int = 40) -> Mapping[str, object]:
        """One explicit publisher catalogue page; never fetch book detail pages."""
        if type(page) is not int or not 0 <= page <= 100:
            raise ValueError("invalid catalogue page")
        bounded = max(1, min(int(limit), 40))
        term = _text(query)[:_MAX_QUERY_CHARS]
        started = perf_counter()
        self._last_diagnostic = {
            "source": "ecole-loisirs", "page": page,
            "attempted_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "requested_limit": bounded, "text_filter_present": bool(term),
            "phase": "listing", "request_count": 0,
        }
        token = self._call_requests.set(0)
        diagnostic: dict[str, object] = {"query_mode": "remote", "effective_limit": bounded}
        try:
            params = urlencode({"type": "romans", "search": term, "page": page})
            url = BASE + "/catalogue/romans?" + params
            http_started = perf_counter()
            try:
                html = self._get(url, diagnostic=diagnostic)
            finally:
                diagnostic["http_ms"] = max(0, int((perf_counter() - http_started) * 1000))
            self._last_diagnostic["phase"] = "parsing"
            parse_started = perf_counter()
            try:
                rows = parse_catalogue_cards(html, limit=bounded, diagnostic=diagnostic)
                links = _tokens(html)
            finally:
                diagnostic["parse_ms"] = max(0, int((perf_counter() - parse_started) * 1000))
            next_page = ""
            for kind, _label, href in links:
                if kind != "link":
                    continue
                target = _allowed_url(href)
                if not target or urlsplit(target).path != "/catalogue/romans":
                    continue
                values = parse_qs(urlsplit(target).query)
                if values.get("search") != [term] or values.get("type") != ["romans"]:
                    continue
                try:
                    proposed = int((values.get("page") or [""])[0])
                except ValueError:
                    continue
                if proposed == page + 1 and proposed <= 100:
                    next_page = str(proposed)
                    break
            if next_page and not rows:
                raise InvalidCatalogueStructureError(
                    "Ecole des loisirs paginated catalogue has no qualifying product cards"
                )
            diagnostic["pages_available"] = int(bool(next_page))
            diagnostic["total_ms"] = max(0, int((perf_counter() - started) * 1000))
            self._last_diagnostic.update({
                "phase": "complete", "state": "items" if rows else "empty_unverified",
                "returned_items": len(rows), "next_cursor_present": bool(next_page),
                "duration_ms": diagnostic["total_ms"],
            })
            self._publish_diagnostic("ok" if rows else "empty")
            return {"items": rows, "next_cursor": next_page, "fresh_for_seconds": 86400,
                    "collection_request_count": self._call_requests.get(),
                    "collection_pipeline": dict(diagnostic)}
        except Exception as exc:
            exc.collection_request_count = self._call_requests.get()
            exc.collection_pipeline = dict(diagnostic)
            self._last_diagnostic.update({
                "phase": "error",
                "state": "invalid" if isinstance(exc, InvalidCatalogueStructureError) else "error",
                "error_type": type(exc).__name__,
                "duration_ms": max(0, int((perf_counter() - started) * 1000)),
            })
            self._publish_diagnostic("error")
            raise
        finally:
            self._call_requests.reset(token)

    def browse(self, *, age: int | None = None, themes: tuple[str, ...] = (),
               text_filter: str = "", cursor: str | None = None,
               limit: int = 40) -> Mapping[str, object]:
        """Explicit UI browse: one catalogue page; age/theme filters remain local.

        No network request happens until the user clicks collect/more.
        The catalogue endpoint has not demonstrated remote age/theme filtering.
        """
        if cursor is not None and (not isinstance(cursor, str) or
                                   not re.fullmatch(r"(?:0|[1-9][0-9]{0,2})", cursor)):
            raise ValueError("invalid catalogue page cursor")
        return self.search_catalogue(text_filter, page=int(cursor or "0"), limit=limit)

    def search(self, query: str, *, cursor: str | None = None, limit: int = 40) -> Mapping[str, object]:
        try:
            page = int(cursor or "1")
        except ValueError:
            raise ValueError("invalid l'école des loisirs page cursor")
        if not 1 <= page <= 100:
            raise ValueError("invalid l'école des loisirs page cursor")
        started = perf_counter()
        self._last_diagnostic = {
            "source": "ecole-loisirs",
            "attempted_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "page": page,
            "requested_limit": max(1, min(int(limit), 40)),
            "text_filter_present": bool(_text(query)),
            "phase": "listing",
            "request_count": 0,
        }
        request_token = self._call_requests.set(0)
        pipeline: dict[str, object] = {
            "query_mode": "remote", "effective_limit": max(1, min(int(limit), 40)),
        }
        try:
            url = search_url(_text(query), page=page)
            http_started = perf_counter()
            try:
                html = self._get(url, diagnostic=pipeline)
            finally:
                pipeline["http_ms"] = max(0, int((perf_counter() - http_started) * 1000))
            self._last_diagnostic["phase"] = "parsing"
            parse_started = perf_counter()
            try:
                rows = parse_listing(
                    html, limit=max(1, min(int(limit), 40)), diagnostic=pipeline,
                )
                tokens = _tokens(html)
            finally:
                pipeline["parse_ms"] = max(0, int((perf_counter() - parse_started) * 1000))
            product_urls = {
                _product_url(href)
                for kind, label, href in tokens
                if kind == "link" and _product_url(href)
                and _fold(label) != "lire la suite"
            }
            cursor_out = next_cursor(html, current_page=page)
            if cursor_out and not product_urls:
                raise InvalidCatalogueStructureError(
                    "Ecole des loisirs paginated catalogue has no recognizable book links"
                )
            pipeline["total_ms"] = max(0, int((perf_counter() - started) * 1000))
            self._last_diagnostic.update({
                "anchor_count": sum(kind == "link" for kind, _label, _href in tokens),
                "product_url_count": len(product_urls),
                "analyzed_count": pipeline.get("analyzed_count", 0),
                "isbn_valid_count": pipeline.get("isbn_valid_count", 0),
                "effective_limit": pipeline["effective_limit"],
                "rejection_reasons": dict(pipeline.get("rejection_reasons", {})),
                "http_ms": pipeline.get("http_ms", 0),
                "parse_ms": pipeline.get("parse_ms", 0),
                "returned_items": len(rows),
                "qualified_age_count": sum(bool(row.get("age_ranges")) for row in rows),
                "unqualified_age_count": sum(not row.get("age_ranges") for row in rows),
                "next_cursor_present": bool(cursor_out),
                "phase": "complete",
                "state": "items" if rows else "empty_unverified",
                "duration_ms": max(0, int((perf_counter() - started) * 1000)),
            })
            self._publish_diagnostic("ok" if rows else "empty")
            return {
                "items": rows,
                "next_cursor": cursor_out,
                "fresh_for_seconds": 86400,
                "collection_request_count": self._call_requests.get(),
                "collection_pipeline": dict(pipeline),
            }
        except Exception as exc:
            exc.collection_request_count = self._call_requests.get()
            exc.collection_pipeline = dict(pipeline)
            self._last_diagnostic.update({
                "state": (
                    "invalid" if isinstance(exc, InvalidCatalogueStructureError)
                    else "error"
                ),
                "error_type": type(exc).__name__,
                "duration_ms": max(0, int((perf_counter() - started) * 1000)),
            })
            self._publish_diagnostic("error")
            raise
        finally:
            self._call_requests.reset(request_token)


PLUGIN = EcoleLoisirsYouthSource()
