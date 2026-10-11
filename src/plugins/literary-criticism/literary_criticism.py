from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from itertools import islice
import re
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit


_WHITESPACE_RE = re.compile(r"\s+")
_ISBN_CLEAN_RE = re.compile(r"[^0-9Xx]")
_FEED_TYPES = {
    "application/rss+xml": "rss",
    "application/atom+xml": "atom",
    "application/feed+json": "jsonfeed",
}
# Discovery preview is not bulk scraping: never exhaust a third-party stream.
MAX_DISCOVERY_HTML_CHARS = 128_000
MAX_FEED_LINKS_PER_PAGE = 32
MAX_FEED_URLS_PER_ROW = 8
MAX_SEARCH_ROWS_PER_RESULT = 4


def _text(value: object) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "").strip())


def _fold(value: object) -> str:
    return _text(value).casefold()


def _iter_values(value: object) -> Iterable[object]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    if isinstance(value, Mapping):
        return tuple(value.values())
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError:
        return (value,)


def canonical_url(value: object, *, base_url: str = "") -> str:
    raw = _text(value)
    if not raw:
        return ""
    if base_url:
        raw = urljoin(base_url, raw)
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    scheme = parts.scheme.casefold()
    if scheme not in {"http", "https"} or not parts.hostname:
        return ""
    if parts.username or parts.password:
        return ""
    host = parts.hostname.casefold()
    try:
        port = parts.port
    except ValueError:
        return ""
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    host = f"[{host}]" if ":" in host else host
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _valid_isbn10(value: str) -> bool:
    if len(value) != 10 or not re.fullmatch(r"[0-9]{9}[0-9X]", value):
        return False
    total = sum((10 - index) * (10 if char == "X" else int(char)) for index, char in enumerate(value))
    return total % 11 == 0


def _valid_isbn13(value: str) -> bool:
    if len(value) != 13 or not value.isdigit():
        return False
    total = sum(int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(value[:12]))
    check = (10 - (total % 10)) % 10
    return check == int(value[-1])


def normalize_isbn(value: object) -> str:
    isbn = re.sub(r"[\s-]+", "", _text(value)).upper()
    if _valid_isbn10(isbn):
        prefix = "978" + isbn[:9]
        return prefix + str((-sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(prefix))) % 10)
    if _valid_isbn13(isbn):
        return isbn
    return ""


def _normalized_isbns(*values: object) -> tuple[str, ...]:
    found: list[str] = []
    for value in values:
        for item in _iter_values(value):
            isbn = normalize_isbn(item)
            if isbn and isbn not in found:
                found.append(isbn)
    return tuple(found)


def _normalized_authors(value: object) -> tuple[str, ...]:
    authors: list[str] = []
    for item in _iter_values(value):
        if isinstance(item, Mapping):
            item = item.get("name") or item.get("label") or ""
        author = _text(item)
        if author and author not in authors:
            authors.append(author)
    return tuple(authors)


def _normalized_urls(value: object, *, base_url: str = "") -> tuple[str, ...]:
    urls: list[str] = []
    for item in _iter_values(value):
        url = canonical_url(item, base_url=base_url)
        if url and url not in urls:
            urls.append(url)
    return tuple(urls)


@dataclass(frozen=True, slots=True)
class SourceInfo:
    source_id: str
    display_name: str
    homepage_url: str = ""


@dataclass(frozen=True, slots=True)
class FeedCandidate:
    url: str
    page_url: str = ""
    title: str = ""
    format: str = ""
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class CriticismArticle:
    article_id: str
    source_id: str
    title: str
    url: str
    critic_author: str = ""
    published_at: str = ""
    excerpt: str = ""
    language: str = ""
    book_title: str = ""
    book_authors: tuple[str, ...] = ()
    isbns: tuple[str, ...] = ()
    notice_urls: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CollectionReport:
    source_id: str
    accepted: int = 0
    duplicates: int = 0
    errors: tuple[str, ...] = ()
    next_cursor: str = ""
    fresh_until: str = ""


@dataclass(frozen=True, slots=True)
class CriticismMatch:
    article: CriticismArticle
    status: str
    score: int
    reasons: tuple[str, ...]


class CriticismSource(Protocol):
    """Structural contract for criticism source plugins.

    Source plugins intentionally do not import this protocol. They expose:
    - source_info() -> mapping/object with source_id, display_name, homepage_url;
    - discover(query, limit=20) -> preview candidates, with no activation side effect;
    - collect(cursor=None, limit=50) -> mapping with items and optional pagination/freshness.
    """

    def source_info(self) -> object: ...
    def discover(self, query: str, limit: int = 20) -> object: ...
    def collect(self, cursor: str | None = None, limit: int = 50) -> object: ...


def _field(value: object, name: str, default: object = "") -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def source_info(source: object) -> SourceInfo:
    method = getattr(source, "source_info", None)
    if not callable(method):
        raise TypeError("criticism source must expose source_info()")
    raw = method()
    source_id = _fold(_field(raw, "source_id") or _field(raw, "id"))
    display_name = _text(_field(raw, "display_name") or _field(raw, "name"))
    homepage_url = canonical_url(_field(raw, "homepage_url") or _field(raw, "url"))
    if not source_id or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,127}", source_id):
        raise ValueError("invalid criticism source_id")
    if not display_name:
        raise ValueError("criticism source display_name is required")
    return SourceInfo(source_id=source_id, display_name=display_name, homepage_url=homepage_url)


def validate_source_contract(source: object) -> SourceInfo:
    info = source_info(source)
    for method_name in ("discover", "collect"):
        if not callable(getattr(source, method_name, None)):
            raise TypeError(f"criticism source must expose {method_name}()")
    return info


class _FeedLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "link":
            return
        row = {str(key).casefold(): str(value or "") for key, value in attrs}
        rel = {item.casefold() for item in row.get("rel", "").split()}
        media_type = row.get("type", "").split(";", 1)[0].strip().casefold()
        href = row.get("href", "")
        if ("alternate" in rel and href and media_type in _FEED_TYPES
                and len(self.links) < MAX_FEED_LINKS_PER_PAGE):
            self.links.append((href, media_type, row.get("title", "")))


def discover_feed_links(page_url: str, html: str) -> tuple[FeedCandidate, ...]:
    base = canonical_url(page_url)
    if not base or not isinstance(html, str) or len(html) > MAX_DISCOVERY_HTML_CHARS:
        # Oversized or non-text payload is not evidence of zero feeds.
        # A preview must never parse a third-party multi-megabyte document.
        return ()
    parser = _FeedLinkParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return ()
    out: list[FeedCandidate] = []
    seen: set[str] = set()
    for href, media_type, title in parser.links:
        url = canonical_url(href, base_url=base)
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(FeedCandidate(
            url=url,
            page_url=base,
            title=_text(title),
            format=_FEED_TYPES[media_type],
            evidence="html-alternate",
        ))
    return tuple(out)


def _limited_feed_values(value: object) -> tuple[object, ...]:
    """Extract only bounded candidate URLs, including hostile generators."""
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    if isinstance(value, Mapping):
        value = value.values()
    try:
        return tuple(islice(iter(value), MAX_FEED_URLS_PER_ROW))
    except (TypeError, ValueError, RuntimeError):
        return (value,) if not hasattr(value, "__iter__") else ()


def discover_feeds(
    query: str,
    search: Callable[[str, int], Iterable[object]],
    *,
    limit: int = 20,
) -> tuple[FeedCandidate, ...]:
    """Discover feed candidates through a caller-supplied metasearch.

    Search rows may expose page "url", optional "html", and explicit
    "feed_url"/"feed_urls" values. Discovery never activates a source.
    """
    q = _text(query)
    if not q:
        return ()
    bounded = max(1, min(int(limit), 100))
    try:
        rows = search(q, bounded)
    except Exception:
        return ()
    candidates: list[FeedCandidate] = []
    seen: set[str] = set()
    # Search engines can return more rows than requested or a never-ending
    # iterator without valid feeds. Read <= 4 * requested rows at most.
    try:
        rows_iter = iter(rows or ())
    except (TypeError, ValueError, RuntimeError):
        return ()
    for _ in range(bounded * MAX_SEARCH_ROWS_PER_RESULT):
        try:
            row = next(rows_iter)
        except StopIteration:
            break
        except Exception:
            # Third-party iterator failures can carry tokens in exception
            # messages. Keep already discovered candidates; emit no raw error.
            break
        try:
            page_url = canonical_url(_field(row, "url"))
            title = _text(_field(row, "title"))
            for field_name in ("feed_url", "feed_urls"):
                for raw_url in _limited_feed_values(_field(row, field_name)):
                    url = canonical_url(raw_url, base_url=page_url)
                    if url and url not in seen:
                        seen.add(url)
                        candidates.append(FeedCandidate(
                            url=url,
                            page_url=page_url,
                            title=title,
                            evidence="metasearch-explicit-feed",
                        ))
                        if len(candidates) >= bounded:
                            return tuple(candidates)
            html = _field(row, "html", "")
            if page_url and isinstance(html, str) and html:
                for item in discover_feed_links(page_url, html):
                    if item.url in seen:
                        continue
                    seen.add(item.url)
                    candidates.append(replace(item, title=item.title or title))
                    if len(candidates) >= bounded:
                        return tuple(candidates)
        except Exception:
            # Invalid result rows are isolated; remaining rows still count
            # against the finite discovery work budget.
            continue
    return tuple(candidates[:bounded])


class _ExcerptParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_excerpt(value):
    parser = _ExcerptParser()
    parser.feed(str(value or "")[:20000])
    return _text(" ".join(parser.parts))[:600]


def normalize_article(raw: object, *, source_id: str) -> CriticismArticle:
    book = _field(raw, "book", {})
    article_url = canonical_url(_field(raw, "url") or _field(raw, "canonical_url"))
    if not article_url:
        raise ValueError("criticism article requires an HTTP(S) canonical URL")
    title = _text(_field(raw, "title"))
    if not title:
        raise ValueError("criticism article title is required")
    article_id = _text(_field(raw, "article_id") or _field(raw, "id") or article_url)
    book_title = _text(
        _field(book, "title")
        or _field(raw, "book_title")
        or _field(raw, "work_title")
    )
    book_authors = _normalized_authors(
        _field(book, "authors")
        or _field(book, "author")
        or _field(raw, "book_authors")
        or _field(raw, "book_author")
    )
    isbns = _normalized_isbns(
        _field(book, "isbns"),
        _field(book, "isbn"),
        _field(book, "isbn10"),
        _field(book, "isbn13"),
        _field(raw, "isbns"),
        _field(raw, "isbn"),
        _field(raw, "isbn10"),
        _field(raw, "isbn13"),
    )
    notice_urls = _normalized_urls(
        _field(book, "notice_urls")
        or _field(book, "notice_url")
        or _field(raw, "notice_urls")
        or _field(raw, "notice_url")
    )
    language = _fold(_field(raw, "language")).replace("_", "-")
    return CriticismArticle(
        article_id=article_id,
        source_id=source_id,
        title=title,
        url=article_url,
        critic_author=_text(_field(raw, "critic_author") or _field(raw, "author")),
        published_at=_text(_field(raw, "published_at") or _field(raw, "date")),
        excerpt=plain_excerpt(_field(raw, "excerpt") or _field(raw, "summary")),
        language=language,
        book_title=book_title,
        book_authors=book_authors,
        isbns=isbns,
        notice_urls=notice_urls,
        provenance=(source_id,),
    )


def _merge_duplicate(existing: CriticismArticle, incoming: CriticismArticle) -> CriticismArticle:
    provenance = tuple(dict.fromkeys(existing.provenance + incoming.provenance))
    isbns = tuple(dict.fromkeys(existing.isbns + incoming.isbns))
    notices = tuple(dict.fromkeys(existing.notice_urls + incoming.notice_urls))
    authors = tuple(dict.fromkeys(existing.book_authors + incoming.book_authors))
    return replace(
        existing,
        provenance=provenance,
        isbns=isbns,
        notice_urls=notices,
        book_authors=authors,
        excerpt=existing.excerpt or incoming.excerpt,
        critic_author=existing.critic_author or incoming.critic_author,
        published_at=existing.published_at or incoming.published_at,
        language=existing.language or incoming.language,
        book_title=existing.book_title or incoming.book_title,
    )


def _extract_collection(raw: object) -> tuple[Iterable[object], str, int]:
    if isinstance(raw, Mapping):
        # Missing items is legacy empty response; explicit null/string/object
        # is malformed, not evidence that an existing feed became empty.
        items = raw.get("items", ())
        cursor_raw = raw.get("next_cursor")
        if cursor_raw is None:
            cursor = ""
        elif (isinstance(cursor_raw, str) and len(cursor_raw) <= 512
              and all(ord(ch) >= 32 and ord(ch) != 127 for ch in cursor_raw)):
            cursor = cursor_raw.strip()
        else:
            raise ValueError("invalid cursor")
        try:
            fresh_for = int(raw.get("fresh_for_seconds") or 0)
        except (TypeError, ValueError):
            fresh_for = 0
        return items, cursor, max(0, fresh_for)
    return raw if raw is not None else (), "", 0


def _book_value(book: object, *names: str) -> object:
    for name in names:
        value = _field(book, name, None)
        if value not in (None, "", (), [], {}):
            return value
    return ""


def _book_isbns(book: object) -> tuple[str, ...]:
    identifiers = _book_value(book, "identifiers")
    extra: list[object] = []
    if isinstance(identifiers, Mapping):
        for key, value in identifiers.items():
            if "isbn" in str(key).casefold():
                extra.extend(_iter_values(value))
    return _normalized_isbns(
        _book_value(book, "isbns"),
        _book_value(book, "isbn"),
        _book_value(book, "isbn10"),
        _book_value(book, "isbn13"),
        extra,
    )


def _book_notice_urls(book: object) -> tuple[str, ...]:
    values: list[object] = []
    for name in ("notice_urls", "notice_url", "friendly_url", "canonical_url"):
        values.extend(_iter_values(_book_value(book, name)))
    links = _book_value(book, "document_links", "links")
    for link in _iter_values(links):
        if isinstance(link, Mapping):
            values.append(link.get("url") or link.get("uri") or "")
        else:
            values.append(getattr(link, "url", "") or getattr(link, "uri", ""))
    metadata = _field(book, "metadata", {})
    if isinstance(metadata, Mapping):
        for name in ("notice_urls", "notice_url", "friendly_url"):
            values.extend(_iter_values(metadata.get(name)))
    return _normalized_urls(values)


def match_article(article: CriticismArticle, book: object) -> CriticismMatch:
    book_isbns = set(_book_isbns(book))
    common_isbns = tuple(sorted(set(article.isbns) & book_isbns))
    if common_isbns:
        return CriticismMatch(article, "confirmed", 100, tuple(f"isbn:{value}" for value in common_isbns))

    book_notices = set(_book_notice_urls(book))
    common_notices = set(article.notice_urls) & book_notices
    if common_notices:
        return CriticismMatch(article, "confirmed", 90, ("notice-url",))

    article_title = _fold(article.book_title)
    book_title = _fold(_book_value(book, "title"))
    article_authors = {_fold(value) for value in article.book_authors if _fold(value)}
    book_authors = {_fold(value) for value in _normalized_authors(_book_value(book, "authors", "author"))}
    common_authors = article_authors & book_authors

    conflicting_edition = bool(article.isbns and book_isbns and not common_isbns)
    if conflicting_edition and article_title == book_title and common_authors:
        return CriticismMatch(article, "suggested", 45, ("title-exact", "author-exact", "different-edition"))
    if article_title and book_title and article_title == book_title and common_authors:
        return CriticismMatch(article, "confirmed", 75, ("title-exact", "author-exact"))
    if article_title and book_title and article_title == book_title:
        return CriticismMatch(article, "suggested", 45, ("title-exact",))
    if common_authors and article_title and book_title:
        article_words = set(article_title.split())
        book_words = set(book_title.split())
        overlap = len(article_words & book_words)
        base = max(len(article_words), len(book_words), 1)
        if overlap / base >= 0.6:
            return CriticismMatch(article, "suggested", 40, ("title-partial", "author-exact"))
    return CriticismMatch(article, "none", 0, ())


class CriticismIndex:
    def __init__(self) -> None:
        self._articles: dict[str, CriticismArticle] = {}
        self._source_keys: dict[str, set[str]] = {}
        self._fresh_until: dict[str, str] = {}

    def articles(self) -> tuple[CriticismArticle, ...]:
        return tuple(self._articles[key] for key in sorted(self._articles))

    def fresh_until(self, source_id: str) -> str:
        return self._fresh_until.get(str(source_id), "")

    def ingest(
        self,
        source_id: str,
        rows: Iterable[object],
        *,
        replace_source: bool = False,
        fresh_for_seconds: int = 0,
    ) -> CollectionReport:
        sid = _fold(source_id)
        if not sid:
            raise ValueError("source_id is required")
        previous_keys = set(self._source_keys.get(sid, set()))
        incoming_keys: set[str] = set()
        accepted = 0
        duplicates = 0
        errors: list[str] = []
        for index, raw in enumerate(rows or ()):
            try:
                article = normalize_article(raw, source_id=sid)
            except Exception:
                # External item repr/errors may embed URLs, auth parameters or
                # user data; emit a stable category instead of raw exceptions.
                errors.append(f"item[{index}]: invalid_article")
                continue
            key = article.url
            incoming_keys.add(key)
            current = self._articles.get(key)
            if current is None:
                self._articles[key] = article
                accepted += 1
            else:
                self._articles[key] = _merge_duplicate(current, article)
                duplicates += 1
        if replace_source and not incoming_keys and errors:
            # An all-invalid reply is not proof that a previously valid feed
            # became empty. Keep the last-good articles and freshness marker.
            return CollectionReport(
                source_id=sid, accepted=accepted, duplicates=duplicates,
                errors=tuple(errors), fresh_until=self._fresh_until.get(sid, ""),
            )
        if replace_source:
            stale = previous_keys - incoming_keys
            for key in stale:
                current = self._articles.get(key)
                if current is None:
                    continue
                remaining = tuple(value for value in current.provenance if value != sid)
                if remaining:
                    self._articles[key] = replace(current, provenance=remaining)
                else:
                    self._articles.pop(key, None)
            self._source_keys[sid] = incoming_keys
        else:
            self._source_keys.setdefault(sid, set()).update(incoming_keys)

        fresh_until = ""
        if fresh_for_seconds > 0:
            until = datetime.now(timezone.utc) + timedelta(seconds=int(fresh_for_seconds))
            fresh_until = until.replace(microsecond=0).isoformat()
            self._fresh_until[sid] = fresh_until
        return CollectionReport(
            source_id=sid,
            accepted=accepted,
            duplicates=duplicates,
            errors=tuple(errors),
            fresh_until=fresh_until,
        )

    def matches_for(self, book: object) -> tuple[CriticismMatch, ...]:
        ranked = [match_article(article, book) for article in self._articles.values()]
        ranked = [item for item in ranked if item.score > 0]
        ranked.sort(key=lambda item: (-item.score, item.article.published_at, item.article.url))
        return tuple(ranked)

    def ranked_books(self, article: CriticismArticle, books: Sequence[object]) -> tuple[tuple[object, CriticismMatch], ...]:
        ranked = [(book, match_article(article, book)) for book in books]
        ranked = [item for item in ranked if item[1].score > 0]
        ranked.sort(key=lambda item: -item[1].score)
        if len(ranked) > 1 and ranked[0][1].status == "confirmed" and ranked[0][1].score == ranked[1][1].score:
            top_score = ranked[0][1].score
            adjusted: list[tuple[object, CriticismMatch]] = []
            for book, match in ranked:
                if match.score == top_score:
                    match = replace(match, status="suggested", reasons=match.reasons + ("ambiguous",))
                adjusted.append((book, match))
            ranked = adjusted
        return tuple(ranked)


class LiteraryCriticismPlugin:
    """Standalone criticism service. UI integration is deliberately left to Core contracts."""

    def __init__(self) -> None:
        self.index = CriticismIndex()
        self.context: object | None = None

    def activate(self, context: object) -> None:
        self.context = context

    def deactivate(self) -> None:
        self.context = None

    def discover(self, query: str, search: Callable[[str, int], Iterable[object]], *, limit: int = 20) -> tuple[FeedCandidate, ...]:
        return discover_feeds(query, search, limit=limit)

    def collect_source(
        self,
        source: object,
        *,
        cursor: str | None = None,
        limit: int = 50,
        replace_source: bool = False,
    ) -> CollectionReport:
        try:
            info = validate_source_contract(source)
        except Exception:
            return CollectionReport(source_id="", errors=("source: incompatible",))
        bounded = max(1, min(int(limit), 500))
        try:
            raw = source.collect(cursor=cursor, limit=bounded)  # type: ignore[attr-defined]
            items, next_cursor, fresh_for = _extract_collection(raw)
            # A provider can ignore the requested page limit, yield forever,
            # or supply malformed pagination. Reject the *whole* page before
            # changing the cached index; never accidentally treat it as empty.
            if (not isinstance(next_cursor, str) or len(next_cursor) > 512
                    or any(ord(ch) < 32 or ord(ch) == 127 for ch in next_cursor)):
                return CollectionReport(
                    source_id=info.source_id, errors=("collect: invalid_cursor",),
                    fresh_until=self.index.fresh_until(info.source_id),
                )
            if items is None or isinstance(items, (str, bytes, Mapping)):
                return CollectionReport(
                    source_id=info.source_id, errors=("collect: invalid_items",),
                    fresh_until=self.index.fresh_until(info.source_id),
                )
            from itertools import islice
            page = tuple(islice(iter(items), bounded + 1))
            if len(page) > bounded:
                return CollectionReport(
                    source_id=info.source_id, errors=("collect: limit_exceeded",),
                    fresh_until=self.index.fresh_until(info.source_id),
                )
            report = self.index.ingest(
                info.source_id,
                page,
                replace_source=replace_source,
                # A third-party feed must not mint an effectively eternal
                # freshness proof or overflow datetime on the worker.
                fresh_for_seconds=min(fresh_for, 7 * 24 * 3600),
            )
            return replace(report, next_cursor=next_cursor)
        except Exception:
            # Never expose a third-party exception message. It may include a
            # signed feed URL, token, private book title or raw response body.
            return CollectionReport(source_id=info.source_id, errors=("collect: source_error",))

    def articles_for(self, book: object) -> tuple[CriticismMatch, ...]:
        return self.index.matches_for(book)


PLUGIN = LiteraryCriticismPlugin()

