from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from time import perf_counter
import math
import re
from typing import Mapping
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import httpx

BASE = "https://usborne.com"
BROWSE = BASE + "/us/books/browse-by-age"
_PRODUCT_RE = re.compile(r"-(\d{13})/?$")
_AGE_RE = re.compile(r"\bAge\s*:?\s*(\d{1,3})\+\s*(months?)?", re.I)
_BADGES = {"new", "award winner", "coming soon", "bestseller", "best seller"}
_WS = re.compile(r"\s+")
_MAX_FILTER_CHARS = 300
AGE_PATHS = (
    (0, 2, "0-3-years"),
    (3, 4, "3-5-years"),
    (5, 6, "5-7-years"),
    (7, 10, "7-11-years"),
    (11, 12, "11-13-years"),
    (13, 17, "13-years"),
)


def _text(value: object) -> str:
    return _WS.sub(" ", str(value or "").replace("\xa0", " ").strip())


def _fold(value: object) -> str:
    return _text(value).casefold()


def normalize_isbn13(value: object) -> str:
    raw = re.sub(r"[^0-9]", "", _text(value))
    if len(raw) != 13 or not raw.startswith(("978", "979")):
        return ""
    total = sum(int(d) * (1 if index % 2 == 0 else 3) for index, d in enumerate(raw))
    return raw if total % 10 == 0 else ""


def _allowed_url(value: object) -> str:
    raw = _text(value)
    if not raw:
        return ""
    absolute = urljoin(BASE + "/", raw)
    try:
        parts = urlsplit(absolute)
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme.casefold() != "https" or parts.hostname not in {"usborne.com", "www.usborne.com"}:
        return ""
    if parts.username or parts.password or port not in (None, 443):
        return ""
    host = parts.hostname.casefold()
    netloc = host
    return urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))


def _product_url(value: object) -> tuple[str, str]:
    url = _allowed_url(value)
    if not url:
        return "", ""
    parts = urlsplit(url)
    if not parts.path.startswith("/us/"):
        return "", ""
    match = _PRODUCT_RE.search(parts.path)
    if not match:
        return "", ""
    isbn = normalize_isbn13(match.group(1))
    return (urlunsplit(("https", parts.netloc, parts.path.rstrip("/"), "", "")), isbn) if isbn else ("", "")


def age_path_for(age: int | None) -> str:
    if age is None:
        return ""
    value = int(age)
    if not 0 <= value < 18:
        raise ValueError("age must be between 0 and 17")
    for lo, hi, path in AGE_PATHS:
        if lo <= value <= hi:
            return path
    return ""


@dataclass(frozen=True, slots=True)
class PublishedAge:
    source_label: str
    age_min: int
    age_min_months: int | None
    confidence: float


def parse_published_age(value: object) -> PublishedAge | None:
    label = _text(value)
    match = _AGE_RE.search(label)
    if not match:
        return None
    amount = int(match.group(1))
    months = bool(match.group(2))
    if months:
        if not 0 <= amount < 18 * 12:
            return None
        projected = int(math.ceil(amount / 12)) if amount else 0
        if projected >= 18:
            return None
        return PublishedAge(
            source_label=_text(match.group(0)),
            age_min=projected,
            age_min_months=amount,
            confidence=0.8,
        )
    if not 0 <= amount < 18:
        return None
    return PublishedAge(
        source_label=_text(match.group(0)),
        age_min=amount,
        age_min_months=None,
        confidence=1.0,
    )


class _TokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple[str, str, str]] = []
        self._href = ""
        self._anchor: list[str] = []
        self._skip = 0

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
            return
        self.tokens.append(("text", value, ""))


def _tokens(html: str) -> tuple[tuple[str, str, str], ...]:
    parser = _TokenParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return ()
    return tuple(parser.tokens)


def parse_listing(
    html: str,
    *,
    text_filter: str = "",
    age: int | None = None,
    limit: int = 40,
    diagnostic: dict[str, object] | None = None,
) -> tuple[Mapping[str, object], ...]:
    tokens = _tokens(html)
    product_link_count = 0
    products: dict[str, tuple[int, str, str]] = {}
    for index, (kind, label, href) in enumerate(tokens):
        if kind != "link":
            continue
        url, isbn = _product_url(href)
        if not url:
            continue
        product_link_count += 1
        cleaned = _text(label)
        prior = products.get(url)
        if prior is None:
            products[url] = (index, cleaned, isbn)
            continue
        previous_index, previous_label, previous_isbn = prior
        previous_badge = _fold(previous_label) in _BADGES
        current_badge = _fold(cleaned) in _BADGES
        if previous_badge and not current_badge:
            products[url] = (index, cleaned, previous_isbn)
        elif not previous_badge and current_badge:
            products[url] = (previous_index, previous_label, previous_isbn)
        elif not current_badge and len(cleaned) > len(previous_label):
            products[url] = (index, cleaned, previous_isbn)

    ordered = sorted(
        ((position, title, url, isbn) for url, (position, title, isbn) in products.items()),
        key=lambda row: row[0],
    )
    out: list[Mapping[str, object]] = []
    query = _fold(text_filter)
    requested_age = int(age) if age is not None else None
    examined = 0
    qualified_age = 0
    reasons = {"age_unattested": 0, "text_filter_excluded": 0,
               "age_filter_excluded": 0, "title_missing": 0,
               "duplicate": max(0, product_link_count - len(products))}
    truncated = False

    for pos, (start, title, url, isbn) in enumerate(ordered):
        if not title or _fold(title) in _BADGES:
            reasons["title_missing"] += 1
            continue
        if len(out) >= max(1, min(int(limit), 40)):
            truncated = True
            break
        examined += 1
        end = ordered[pos + 1][0] if pos + 1 < len(ordered) else len(tokens)
        segment = " ".join(
            value for kind, value, _href in tokens[start + 1:end]
            if kind == "text" and _text(value)
        )
        published = parse_published_age(segment)
        if published is None:
            reasons["age_unattested"] += 1
            continue
        qualified_age += 1
        if requested_age is not None and published.age_min > requested_age:
            reasons["age_filter_excluded"] += 1
            continue
        if query and query not in _fold(title):
            reasons["text_filter_excluded"] += 1
            continue
        metadata: dict[str, object] = {
            "age_evidence": "usborne.catalog.explicit",
            "age_unit": "months" if published.age_min_months is not None else "years",
        }
        if published.age_min_months is not None:
            metadata["age_min_months"] = published.age_min_months
            metadata["age_projection"] = "ceil-months-to-years"
        out.append({
            "record_id": isbn,
            "source_item_id": isbn,
            "title": title,
            "isbn13": isbn,
            "notice_url": url,
            "friendly_url": url,
            "age_ranges": [{
                "age_min": published.age_min,
                "age_max": None,
                "age_kind": "publisher",
                "source_label": published.source_label,
                "source_id": "usborne",
                "confidence": published.confidence,
            }],
            "metadata": metadata,
        })
    if diagnostic is not None:
        diagnostic.update({
            "query_mode": "local",
            "effective_limit": max(1, min(int(limit), 40)),
            "anchor_count": sum(kind == "link" for kind, _, _ in tokens),
            "product_link_count": product_link_count,
            "candidate_count": len(products),
            "analyzed_count": examined,
            "isbn_valid_count": len(products),
            "age_published_count": qualified_age,
            "age_qualified_count": qualified_age,
            "returned_items": len(out),
            "truncated_by_limit": truncated,
            "rejection_reasons": {key: value for key, value in reasons.items() if value},
        })
    return tuple(out)


def _browse_url(*, age: int | None, page: int) -> str:
    path = age_path_for(age)
    base = BROWSE + ("/" + path if path else "")
    bounded_page = max(1, min(int(page), 100))
    return base if bounded_page == 1 else base + "?" + urlencode({"p": bounded_page})


def next_page_cursor(html: str, *, current_page: int) -> str:
    for kind, label, href in _tokens(html):
        if kind != "link" or _fold(label) != "next page":
            continue
        raw = _text(href)
        if raw.startswith("?"):
            query = raw[1:]
        else:
            url = _allowed_url(raw)
            if not url or not urlsplit(url).path.startswith("/us/books/browse-by-age"):
                continue
            query = urlsplit(url).query
        try:
            params = {}
            for part in query.split("&"):
                if "=" in part:
                    key, value = part.split("=", 1)
                    params[key] = value
            page = int(params.get("p", ""))
        except (TypeError, ValueError):
            continue
        if page == int(current_page) + 1 and page <= 100:
            return str(page)
    return ""


class InvalidCatalogueStructureError(RuntimeError):
    """Publisher layout mismatch; never contains source HTML or private data."""


class UsborneYouthSource:
    def __init__(self, *, client: httpx.Client | None = None, timeout_seconds: float = 12.0) -> None:
        self._own_client = client is None
        self.context = None
        self._last_diagnostic: dict[str, object] = {}
        self._call_requests: ContextVar[int | None] = ContextVar("youth_usborne_call_requests", default=None)
        self.client = client or httpx.Client(
            timeout=max(1.0, min(float(timeout_seconds), 30.0)),
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": "Cifereca-Youth-Usborne/0.1",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en",
            },
        )

    def activate(self, context):
        self.context = context
        return self

    def diagnostic_snapshot(self) -> dict[str, object]:
        """Last explicit browse outcome only; never a URL, page body or title."""
        return dict(self._last_diagnostic)

    def _publish_diagnostic(self, status: str) -> None:
        if self.context is not None:
            try:
                self.context.report_diagnostic(
                    "Usborne youth catalogue collection", status,
                    detail=self.diagnostic_snapshot(), level="normal",
                )
            except Exception:
                # Broker diagnostics must not change source behavior.
                pass

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    def source_info(self) -> Mapping[str, str]:
        return {
            "source_id": "usborne",
            "display_name": "Usborne",
            "homepage_url": BROWSE,
        }

    def _get(self, url: str, *, evidence: dict[str, object] | None = None) -> str:
        allowed = _allowed_url(url)
        if not allowed:
            raise ValueError("Usborne URL rejected")
        parts = urlsplit(allowed)
        path = parts.path.rstrip("/")
        base = "/us/books/browse-by-age"
        if path != base and not path.startswith(base + "/"):
            raise ValueError("only Usborne Browse by Age pages are allowed")
        self._last_diagnostic["request_count"] = int(self._last_diagnostic.get("request_count", 0)) + 1
        counter = self._call_requests.get()
        if counter is not None:
            self._call_requests.set(counter + 1)
        response = self.client.get(allowed)
        observed = {
            "http_status": response.status_code,
            "content_type_is_html": "html" in _fold(response.headers.get("content-type")),
            "html_bytes": len(response.content),
            "html_sha256_prefix": sha256(response.content).hexdigest()[:16],
        }
        self._last_diagnostic.update(observed)
        if evidence is not None:
            evidence.update(observed)
        if response.status_code >= 300:
            error = RuntimeError(f"Usborne HTTP {response.status_code}")
            error.http_status = response.status_code
            raise error
        content_type = _fold(response.headers.get("content-type"))
        if content_type and "html" not in content_type:
            raise RuntimeError("Usborne response is not HTML")
        if len(response.content) > 2_000_000:
            raise RuntimeError("Usborne response exceeds 2 MB")
        return response.text

    def browse(
        self,
        *,
        age: int | None = None,
        text_filter: str = "",
        cursor: str | None = None,
        limit: int = 40,
        themes: tuple[str, ...] = (),
    ) -> Mapping[str, object]:
        # Usborne's public age pages expose no attested theme query contract.
        # Themes are intentionally ignored rather than translated into invented URLs.
        del themes
        try:
            page = int(cursor or "1")
        except (TypeError, ValueError):
            raise ValueError("invalid Usborne page cursor")
        if not 1 <= page <= 100:
            raise ValueError("invalid Usborne page cursor")
        started = perf_counter()
        self._last_diagnostic = {
            "source": "usborne",
            "attempted_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "page": page,
            "requested_limit": max(1, min(int(limit), 40)),
            "age_filter_present": age is not None,
            "text_filter_present": bool(_text(text_filter)),
            "phase": "listing",
            "request_count": 0,
        }
        request_token = self._call_requests.set(0)
        pipeline: dict[str, object] = {
            "query_mode": "local", "effective_limit": max(1, min(int(limit), 40)),
        }
        try:
            url = _browse_url(age=age, page=page)
            http_started = perf_counter()
            try:
                html = self._get(url, evidence=pipeline)
            finally:
                pipeline["http_ms"] = max(0, int((perf_counter() - http_started) * 1000))
            self._last_diagnostic["phase"] = "parsing"
            parse_started = perf_counter()
            try:
                rows = parse_listing(
                    html, text_filter=_text(text_filter)[:_MAX_FILTER_CHARS],
                    age=age, limit=max(1, min(int(limit), 40)),
                    diagnostic=pipeline,
                )
            finally:
                pipeline["parse_ms"] = max(0, int((perf_counter() - parse_started) * 1000))
            self._last_diagnostic.update({
                key: value for key, value in pipeline.items()
                if key not in {"query_mode", "effective_limit", "rejection_reasons"}
            })
            # A diagnostic-only second parse of the bounded HTML: no network.
            tokens = _tokens(html)
            product_urls = {
                product_url
                for kind, _label, href in tokens if kind == "link"
                for product_url, _isbn in (_product_url(href),) if product_url
            }
            cursor_out = next_page_cursor(html, current_page=page)
            if cursor_out and not product_urls:
                raise InvalidCatalogueStructureError(
                    "Usborne paginated catalogue has no recognizable product links"
                )
            pipeline["total_ms"] = max(0, int((perf_counter() - started) * 1000))
            self._last_diagnostic.update({
                "anchor_count": sum(kind == "link" for kind, _label, _href in tokens),
                "product_url_count": len(product_urls),
                "returned_items": len(rows),
                "qualified_age_count": len(rows),
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
            exc.collection_pipeline = dict(pipeline)
            exc.collection_request_count = self._call_requests.get()
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

    def search(self, query: str, *, cursor: str | None = None, limit: int = 40) -> Mapping[str, object]:
        # No free-text remote endpoint is assumed. Filter the explicitly loaded
        # Browse by Age page locally, as with the BookTrust adapter.
        return self.browse(text_filter=_text(query)[:_MAX_FILTER_CHARS], cursor=cursor, limit=limit)


PLUGIN = UsborneYouthSource()
