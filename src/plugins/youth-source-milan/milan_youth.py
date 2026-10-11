from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from html.parser import HTMLParser
from hashlib import sha256
from time import perf_counter
import re
from typing import Mapping
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import httpx

BASE = "https://www.editionsmilan.com"
CATALOG = BASE + "/livres/"
SEARCH = BASE + "/recherche/books/"
_SEARCH_PATH = re.compile(r"^/recherche/books(?:/page/(\d+))?/?$")
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
    if len(raw) == 13 and raw.isdigit() and raw.startswith(("978", "979")):
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
    return url if url and not urlsplit(url).query and _PRODUCT_PATH.fullmatch(urlsplit(url).path) else ""


def listing_url(*, page: int) -> str:
    bounded = max(1, min(int(page), 100))
    return CATALOG if bounded == 1 else f"{CATALOG}page/{bounded}/"


def search_url(value: str) -> str:
    """Only the publisher's observed /recherche/books/?text=... endpoint."""
    query = _text(value)
    if not query or len(query) > 120 or any(ord(c) < 32 for c in query):
        raise ValueError("invalid Milan search text")
    return SEARCH + "?" + urlencode({"text": query})


def _search_page(value: object, *, query: str) -> int:
    """Validate an actual pagination link, never infer a remote page URL."""
    url = allowed_url(value)
    if not url:
        return 0
    parts = urlsplit(url)
    match = _SEARCH_PATH.fullmatch(parts.path)
    if not match or len(parts.query) > 512:
        return 0
    params = parse_qs(parts.query, keep_blank_values=True)
    if not set(params).issubset({"text", "page"}) or params.get("text") != [query]:
        return 0
    if any(len(values) != 1 for values in params.values()):
        return 0
    try:
        number = int(params.get("page", [match.group(1) or "1"])[0])
    except ValueError:
        return 0
    if not 1 <= number <= 100 or (match.group(1) and number != int(match.group(1))):
        return 0
    return number


def search_next_cursor(html: object, *, query: str, current_url: str) -> str:
    """Opaque next-page cursor is the published and validated search link."""
    current = _search_page(current_url, query=query)
    if not current or current == 100:
        return ""
    for kind, _label, href in tokens(html):
        if kind != "link":
            continue
        if _search_page(href, query=query) == current + 1:
            return allowed_url(href)
    return ""


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
        self.listing_stats = {
            "anchor_count": 0, "milan_catalogue_href_count": 0,
            "product_href_count": 0, "product_link_text_count": 0,
            "product_link_accessible_count": 0, "product_link_unnamed_count": 0,
            "unrecognized_catalogue_href_count": 0,
        }
        self._link_is_product = False

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
            self.listing_stats["anchor_count"] += 1
            allowed = allowed_url(self._href)
            catalogue_path = urlsplit(allowed).path if allowed else ""
            if catalogue_path.startswith("/livres/"):
                self.listing_stats["milan_catalogue_href_count"] += 1
                if not product_url(self._href):
                    self.listing_stats["unrecognized_catalogue_href_count"] += 1
            self._link_is_product = bool(product_url(self._href))
            if self._link_is_product:
                self.listing_stats["product_href_count"] += 1
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
            if self._link_is_product:
                if _text(" ".join(self._anchor)):
                    self.listing_stats["product_link_text_count"] += 1
                elif self._anchor_fallback:
                    self.listing_stats["product_link_accessible_count"] += 1
                else:
                    self.listing_stats["product_link_unnamed_count"] += 1
            label = _text(" ".join(self._anchor)) or self._anchor_fallback
            label = re.sub(r"^couverture\s+(?:de\s+|du\s+|des\s+)", "", label, flags=re.I)
            if label:
                self.tokens.append(("link", label, self._href))
            self._href = ""
            self._anchor = []
            self._anchor_fallback = ""
            self._link_is_product = False
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


def tokens(html: object, *, listing_diagnostic: dict | None = None) -> tuple[tuple[str, str, str], ...]:
    parser = _Parser()
    try:
        parser.feed(str(html or ""))
        parser.close()
    except Exception:
        return ()
    if listing_diagnostic is not None:
        listing_diagnostic.update(parser.listing_stats)
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


def listing_products(
    html: object, *, text_filter: str = "", limit: int = 12,
    listing_diagnostic: dict | None = None,
) -> tuple[tuple[str, str], ...]:
    query = _fold(text_filter)
    # A catalogue card can use only "Voir le livre" as anchor text.
    # Keep its *strictly allowlisted* product URL and obtain the real
    # published title, age and ISBN from the detail page during browse().
    found: dict[str, tuple[str, bool]] = {}
    generic_count = 0
    for kind, label, href in tokens(html, listing_diagnostic=listing_diagnostic):
        if kind != "link":
            continue
        url = product_url(href)
        if not url:
            continue
        clean = _text(label)
        low = _fold(clean)
        fallback = low in _GENERIC_LINKS or low.startswith("voir ")
        if fallback:
            generic_count += 1
            slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1].partition("-")[2]
            clean = _text(slug.replace("-", " "))[:160]
        if not clean:
            continue
        previous_title, previous_fallback = found.get(url, ("", True))
        if (not previous_title or (previous_fallback and not fallback)
                or (previous_fallback == fallback and len(clean) > len(previous_title))):
            found[url] = (clean, fallback)
    out = []
    text_excluded = 0
    truncated = False
    for pos, (url, (title, _fallback)) in enumerate(found.items()):
        if query and query not in _fold(title):
            text_excluded += 1
            continue
        out.append((title, url))
        if len(out) >= max(1, min(int(limit), 36)):
            truncated = pos < len(found) - 1
            break
    if listing_diagnostic is not None:
        listing_diagnostic.update({
            "candidate_count": len(found),
            "product_link_count": len(found),
            "recognized_product_count": len(out),
            "generic_product_label_count": generic_count,
            "truncated_by_limit": truncated,
        })
        if text_excluded:
            listing_diagnostic.setdefault("rejection_reasons", {})[
                "text_filter_excluded"
            ] = text_excluded
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
        if kind not in {"text", "heading"} or _fold(value) not in label_names:
            continue
        for next_kind, next_value, _next_href in rows[index + 1:index + 5]:
            if next_kind in {"text", "heading"}:
                candidate = _text(next_value)
                if candidate and _fold(candidate) not in label_names:
                    return candidate
    return ""


def _cover_url(html: object) -> str:
    """Use only explicit, public cover imagery from approved publisher hosts."""
    class Covers(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.cover = ""

        def handle_starttag(self, tag, attrs):
            if self.cover or tag.casefold() != "img":
                return
            data = dict(attrs)
            candidate = _text(data.get("src") or data.get("data-src"))
            alt = _fold(data.get("alt"))
            if not candidate or not ("couverture" in alt or "/couvertures/" in candidate):
                return
            url = urljoin(BASE + "/", candidate)
            try:
                parts = urlsplit(url)
                port = parts.port
            except ValueError:
                return
            if (parts.scheme == "https" and parts.hostname in
                    {"www.editionsmilan.com", "editionsmilan.com", "imagine.bayard.io"}
                    and not parts.query and not parts.username and not parts.password
                    and port in (None, 443)):
                self.cover = url

    parser = Covers()
    try:
        parser.feed(str(html or "")[:2_000_000])
    except Exception:
        return ""
    return parser.cover


def _detail_title(rows: tuple[tuple[str, str, str], ...], fallback: str) -> str:
    for kind, value, _ in rows:
        if kind == "heading" and _text(value):
            return _text(value)
    return _text(fallback)


def parse_detail(
    html: object, *, url: str, fallback_title: str = "",
    diagnostic: dict[str, object] | None = None,
) -> Mapping[str, object] | None:
    notice = product_url(url)
    if not notice:
        return None
    rows = tokens(html)
    age_label = _next_value(rows, {"âge", "age"})
    age = parse_published_age(age_label)
    if age is None:
        if diagnostic is not None:
            reasons = diagnostic.setdefault("rejection_reasons", {})
            reasons["age_unattested"] = reasons.get("age_unattested", 0) + 1
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
        if diagnostic is not None:
            reasons = diagnostic.setdefault("rejection_reasons", {})
            reasons["isbn_invalid"] = reasons.get("isbn_invalid", 0) + 1
        return None
    if diagnostic is not None:
        diagnostic["isbn_valid_count"] = diagnostic.get("isbn_valid_count", 0) + 1
        diagnostic["age_published_count"] = diagnostic.get("age_published_count", 0) + 1
        diagnostic["age_qualified_count"] = diagnostic.get("age_qualified_count", 0) + 1

    authors = ()
    raw_author = _next_value(rows, {"auteur", "auteurs", "auteur-illustrateur", "autrice", "autrices"})
    if raw_author:
        authors = tuple(part.strip() for part in re.split(r"\s*[,;]\s*", raw_author) if part.strip())

    collection = _next_value(rows, {"collection"})
    themes_raw = _next_value(rows, {"thème", "thèmes", "theme", "themes"})
    themes = tuple(_text(part) for part in re.split(r"\s{2,}|[,;]", themes_raw) if _text(part))
    illustrator = _next_value(rows, {"illustrateur", "illustratrice", "illustrateurs", "illustrations"})
    illustrators = tuple(_text(v) for v in re.split(r"[,;]", illustrator) if _text(v))
    summary = _next_value(rows, {"résumé", "resume", "description", "présentation"})
    publication = _next_value(rows, {"date de parution", "parution", "date de publication"})
    pages_raw = _next_value(rows, {"nombre de pages", "pages"})
    format_raw = _next_value(rows, {"format", "reliure"})
    price = _next_value(rows, {"prix", "prix papier"})
    digital_price = _next_value(rows, {"prix numérique", "prix ebook", "prix e-book"})
    pages = int(pages_raw) if re.fullmatch(r"[1-9][0-9]{0,3}", pages_raw) else None
    date_match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", publication)
    published_date = ""
    if date_match:
        from datetime import date
        try:
            published_date = date(int(date_match[3]), int(date_match[2]), int(date_match[1])).isoformat()
        except ValueError:
            pass
    metadata = {
        "age_evidence": "milan.product.explicit",
        "collection": collection,
    }
    # Only safe, explicitly labelled bibliographic values; no page HTML or cookies.
    for key, value in (
        ("illustrators", illustrators), ("summary", summary[:3000]),
        ("publication_date", published_date), ("pages", pages),
        ("format", format_raw[:100]), ("price", price[:100]),
        ("digital_price", digital_price[:100]),
    ):
        if value:
            metadata[key] = value

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
        "cover_url": _cover_url(html),
        "published_year": int(published_date[:4]) if published_date else None,
        "description": summary[:3000],
        "illustrators": illustrators,
        "metadata": metadata,
    }


class MilanCollectionCancelled(RuntimeError):
    """Cooperative cancellation between network requests, with no private data."""


class InvalidCatalogueStructureError(RuntimeError):
    """Publisher layout mismatch; never contains source HTML or private data."""


class MilanYouthSource:
    def __init__(self, *, client: httpx.Client | None = None, timeout_seconds: float = 12.0) -> None:
        self._own_client = client is None
        self.context = None
        self._last_diagnostic: dict[str, object] = {}
        self._call_requests: ContextVar[int | None] = ContextVar("youth_milan_call_requests", default=None)
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

    def activate(self, context):
        self.context = context
        return self

    def diagnostic_snapshot(self) -> dict[str, object]:
        """Last explicit collection summary; no raw HTML, URLs or request headers."""
        return dict(self._last_diagnostic)

    def _publish_diagnostic(self, status: str) -> None:
        context = self.context
        if context is not None:
            try:
                context.report_diagnostic(
                    "Milan catalogue collection", status,
                    detail=self.diagnostic_snapshot(), level="normal",
                )
            except Exception:
                # Diagnostics must never interfere with collection or its errors.
                pass

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    def source_info(self) -> Mapping[str, str]:
        return {
            "source_id": "milan",
            "display_name": "Éditions Milan",
            "homepage_url": CATALOG,
        }

    def _get(
        self, url: str, *, kind: str,
        evidence: dict[str, object] | None = None,
    ) -> str:
        allowed = allowed_url(url)
        if not allowed:
            raise ValueError("Milan URL rejected")
        path = urlsplit(allowed).path
        if kind == "listing":
            if not (_LISTING_PATH.fullmatch(path) and not urlsplit(allowed).query
                    or _SEARCH_PATH.fullmatch(path) and
                    _search_page(allowed, query=parse_qs(urlsplit(allowed).query).get("text", [""])[0])):
                raise ValueError("only Milan catalogue/search pages are allowed")
        elif kind == "product":
            if not _PRODUCT_PATH.fullmatch(path):
                raise ValueError("only Milan product pages are allowed")
        else:
            raise ValueError("invalid Milan request kind")
        counter = self._call_requests.get()
        if counter is not None:
            self._call_requests.set(counter + 1)
        response = self.client.get(allowed)
        if kind == "listing":
            observed = {
                "http_status": response.status_code,
                "html_bytes": len(response.content),
                "html_sha256_prefix": sha256(response.content).hexdigest()[:16],
                "content_type_is_html": "html" in _fold(response.headers.get("content-type", "")),
            }
            self._last_diagnostic.update(observed)
            if evidence is not None:
                evidence.update(observed)
        if not 200 <= response.status_code < 300:
            error = RuntimeError(f"Milan HTTP {response.status_code}")
            error.http_status = response.status_code
            raise error
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
        cancelled: Callable[[], bool] | None = None,
    ) -> Mapping[str, object]:
        def check_cancelled() -> None:
            if cancelled is not None and cancelled():
                raise MilanCollectionCancelled("Milan collection cancelled")

        del themes
        query = _text(text_filter)
        remote = bool(query and (not cursor or str(cursor).startswith("https://")))
        if remote:
            first_url = search_url(query)
            selected_url = first_url if not cursor else allowed_url(cursor)
            page = _search_page(selected_url, query=query)
            if not page:
                raise ValueError("invalid Milan search cursor")
        else:
            try:
                page = int(cursor or "1")
            except (TypeError, ValueError):
                raise ValueError("invalid Milan page cursor") from None
            if not 1 <= page <= 100:
                raise ValueError("invalid Milan page cursor")
            selected_url = listing_url(page=page)
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
        # This snapshot contains only bounded structural counts and a content
        # digest; never a page body, URL, cookie, user identity or book title.
        self._last_diagnostic = {
            "source": "milan", "page": page, "requested_limit": bounded,
            "age_filter_present": wanted_age is not None,
            "phase": "listing", "detail_requests": 0,
        }
        request_token = self._call_requests.set(0)
        started = perf_counter()
        pipeline: dict[str, object] = {
            "query_mode": "remote" if remote else "local", "effective_limit": bounded,
            "isbn_valid_count": 0, "age_published_count": 0,
            "age_qualified_count": 0,
        }
        http_elapsed_ms = 0
        parse_elapsed_ms = 0

        def fetch(url: str, *, kind: str) -> str:
            nonlocal http_elapsed_ms
            http_started = perf_counter()
            try:
                return self._get(
                    url, kind=kind,
                    evidence=pipeline if kind == "listing" else None,
                )
            finally:
                http_elapsed_ms += max(0, int((perf_counter() - http_started) * 1000))
                pipeline["http_ms"] = http_elapsed_ms

        try:
            check_cancelled()  # no network call if already cancelled
            listing_html = fetch(selected_url, kind="listing")
            check_cancelled()  # don't parse a cancelled listing
            parse_started = perf_counter()
            try:
                products = listing_products(
                    listing_html, text_filter="" if remote else text_filter, limit=36,
                    listing_diagnostic=pipeline,
                )
            finally:
                parse_elapsed_ms += max(0, int((perf_counter() - parse_started) * 1000))
                pipeline["parse_ms"] = parse_elapsed_ms
            self._last_diagnostic.update({
                key: value for key, value in pipeline.items()
                if key not in {"query_mode", "effective_limit", "rejection_reasons"}
            })
            cursor = (search_next_cursor(listing_html, query=query, current_url=selected_url)
                      if remote else next_cursor(listing_html, current_page=page))
            self._last_diagnostic["next_cursor_present"] = bool(cursor)
            # This is an incompatibility, not a successful empty catalogue.
            if not products and not text_filter and cursor:
                # The manual qualifier captures exception messages as JSON.
                # Only bounded counts/digest are included; no HTML or URL.
                diag = self._last_diagnostic
                raise InvalidCatalogueStructureError(
                    "Milan paginated catalogue has no recognizable book links: "
                    f"anchors={diag.get('anchor_count', 0)}, "
                    f"milan_links={diag.get('milan_catalogue_href_count', 0)}, "
                    f"product_links={diag.get('product_href_count', 0)}, "
                    f"unnamed_links={diag.get('product_link_unnamed_count', 0)}, "
                    f"html_sha256={diag.get('html_sha256_prefix', '')}"
                )

            out = []
            seen_isbns: set[str] = set()
            inspected = 0
            self._last_diagnostic["phase"] = "products"
            for title, url in products:
                check_cancelled()  # stop before starting any further detail GET
                if len(out) >= bounded or inspected >= 8:
                    break
                inspected += 1
                self._last_diagnostic["detail_requests"] = inspected
                detail_html = fetch(url, kind="product")
                check_cancelled()  # skip expensive parsing after cancellation
                parse_started = perf_counter()
                try:
                    detail = parse_detail(
                        detail_html, url=url, fallback_title=title,
                        diagnostic=pipeline,
                    )
                finally:
                    parse_elapsed_ms += max(0, int((perf_counter() - parse_started) * 1000))
                    pipeline["parse_ms"] = parse_elapsed_ms
                pipeline["analyzed_count"] = inspected
                if detail is None:
                    continue
                if detail["isbn13"] in seen_isbns:
                    reasons = pipeline.setdefault("rejection_reasons", {})
                    reasons["duplicate"] = reasons.get("duplicate", 0) + 1
                    continue
                seen_isbns.add(detail["isbn13"])
                if wanted_age is not None and int(detail["age_ranges"][0]["age_min"]) > wanted_age:
                    reasons = pipeline.setdefault("rejection_reasons", {})
                    reasons["age_filter_excluded"] = reasons.get("age_filter_excluded", 0) + 1
                    continue
                out.append(detail)
            check_cancelled()  # don't report success after the final detail
            pipeline.update({
                "analyzed_count": inspected,
                "returned_items": len(out),
                "truncated_by_limit": (
                    bool(pipeline.get("truncated_by_limit"))
                    or inspected < len(products)
                ),
                "total_ms": max(0, int((perf_counter() - started) * 1000)),
            })
            self._last_diagnostic.update({
                key: value for key, value in pipeline.items()
                if key not in {"query_mode", "effective_limit", "rejection_reasons"}
            })
            self._last_diagnostic["qualified_items"] = len(out)
            self._last_diagnostic["phase"] = "complete"
            self._publish_diagnostic("ok")
            return {
                "items": tuple(out),
                "next_cursor": cursor,
                "fresh_for_seconds": 86400,
                "collection_request_count": self._call_requests.get(),
                "collection_pipeline": dict(pipeline),
            }
        except Exception as exc:
            exc.collection_pipeline = dict(pipeline)
            exc.collection_request_count = self._call_requests.get()
            self._last_diagnostic["error_type"] = type(exc).__name__
            self._last_diagnostic["state"] = (
                "cancelled" if isinstance(exc, MilanCollectionCancelled)
                else "invalid" if isinstance(exc, InvalidCatalogueStructureError)
                else "error"
            )
            self._publish_diagnostic("error")
            raise
        finally:
            self._call_requests.reset(request_token)

    def browse_with_cancellation(
        self, *, cancelled: Callable[[], bool],
        text_filter: str = "", cursor: str | None = None,
        limit: int = 8, age: int | None = None,
        themes: tuple[str, ...] = (),
    ) -> Mapping[str, object]:
        """Opt-in extension for the Youth task broker; browse stays compatible."""
        return self.browse(
            text_filter=text_filter, cursor=cursor, limit=limit,
            age=age, themes=themes, cancelled=cancelled,
        )

    def search(self, query: str, *, cursor: str | None = None, limit: int = 8) -> Mapping[str, object]:
        return self.browse(text_filter=_text(query), cursor=cursor, limit=limit)


PLUGIN = MilanYouthSource()
