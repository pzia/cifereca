from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import re
from threading import Lock
from time import monotonic, sleep
from typing import Mapping
from urllib.parse import urlsplit

import httpx

BASE = "https://openlibrary.org"
SEARCH_URL = BASE + "/search.json"
_FIELDS = "key,title,author_name,first_publish_year,isbn,language,subject,publisher,edition_key,cover_i"
_ISBN_CLEAN = re.compile(r"[^0-9Xx]")
_OLID = re.compile(r"^OL[0-9]+[WM]$", re.I)
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}$", re.I)
_MAX_QUERY_CHARS = 300


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _values(value: object, *, limit: int = 30) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        rows = (value,)
    elif isinstance(value, Mapping):
        rows = value.values()
    else:
        try:
            rows = tuple(value)  # type: ignore[arg-type]
        except TypeError:
            rows = (value,)
    out: list[str] = []
    for row in rows:
        item = _text(row)
        if item and item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return tuple(out)


def normalize_isbn(value: object) -> str:
    raw = _ISBN_CLEAN.sub("", _text(value)).upper()
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


def normalize_olid(value: object) -> str:
    raw = _text(value).upper().split("/")[-1]
    return raw if _OLID.fullmatch(raw) else ""


def normalize_language(value: object) -> str:
    raw = _text(value).casefold()
    return raw if _LANGUAGE.fullmatch(raw) else ""


def normalize_query(value: object) -> str:
    return _text(value)[:_MAX_QUERY_CHARS]


def _work_url(key: object) -> str:
    raw = _text(key)
    if not raw:
        return ""
    if raw.startswith("/"):
        parts = urlsplit(raw)
        if parts.scheme or parts.netloc or parts.query or parts.fragment:
            return ""
        match = re.fullmatch(r"/(works|books)/(OL[0-9]+[WM])/?", parts.path, re.I)
        if not match:
            return ""
        kind = match.group(1).casefold()
        olid = normalize_olid(match.group(2))
        if not olid:
            return ""
        if (kind == "works" and not olid.endswith("W")) or (kind == "books" and not olid.endswith("M")):
            return ""
        return f"{BASE}/{kind}/{olid}"
    if "/" in raw or "://" in raw:
        return ""
    olid = normalize_olid(raw)
    if not olid:
        return ""
    prefix = "/works/" if olid.endswith("W") else "/books/"
    return BASE + prefix + olid


def _year(value: object) -> int | None:
    match = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", _text(value))
    return int(match.group(1)) if match else None


def parse_direct_record(row: object, *, olid: str) -> "BibliographyRecord | None":
    if not isinstance(row, Mapping):
        return None
    normalized = normalize_olid(olid)
    title = _text(row.get("title"))
    if not normalized or not title:
        return None
    isbns: list[str] = []
    for field in ("isbn_13", "isbn_10"):
        for raw in _values(row.get(field), limit=20):
            isbn = normalize_isbn(raw)
            if isbn and isbn not in isbns:
                isbns.append(isbn)
    languages = []
    raw_languages = row.get("languages")
    if isinstance(raw_languages, (list, tuple)):
        for value in raw_languages[:12]:
            key = _text(value.get("key")) if isinstance(value, Mapping) else _text(value)
            code = key.rstrip("/").split("/")[-1]
            if code and code not in languages:
                languages.append(code)
    authors = []
    for value in row.get("authors", ()) if isinstance(row.get("authors"), (list, tuple)) else ():
        if isinstance(value, Mapping):
            author = value.get("author") if isinstance(value.get("author"), Mapping) else value
            key = _text(author.get("key")) if isinstance(author, Mapping) else ""
            if key:
                authors.append(key.rstrip("/").split("/")[-1])
    covers = _values(row.get("covers"), limit=1)
    cover_url = ""
    if covers:
        try:
            cover_id = int(covers[0])
            if cover_id > 0:
                cover_url = f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg"
        except (TypeError, ValueError):
            pass
    editions = (normalized,) if normalized.endswith("M") else ()
    return BibliographyRecord(
        source_id="openlibrary",
        record_id=normalized,
        title=title,
        authors=tuple(dict.fromkeys(authors)),
        isbns=tuple(isbns),
        languages=tuple(languages),
        subjects=_values(row.get("subjects"), limit=30),
        publishers=_values(row.get("publishers"), limit=12),
        edition_ids=editions,
        first_publish_year=_year(row.get("first_publish_date") or row.get("publish_date")),
        notice_url=_work_url(normalized),
        cover_url=cover_url,
        age_ranges=(),
        metadata={"bibliographic_only": True, "age_evidence": "none", "source": "openlibrary.object"},
    )


@dataclass(frozen=True, slots=True)
class BibliographyRecord:
    source_id: str
    record_id: str
    title: str
    authors: tuple[str, ...] = ()
    isbns: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    publishers: tuple[str, ...] = ()
    edition_ids: tuple[str, ...] = ()
    first_publish_year: int | None = None
    notice_url: str = ""
    cover_url: str = ""
    age_ranges: tuple[object, ...] = ()
    metadata: Mapping[str, object] | None = None


def parse_record(row: object) -> BibliographyRecord | None:
    if not isinstance(row, Mapping):
        return None
    key = _text(row.get("key"))
    title = _text(row.get("title"))
    if not key or not title:
        return None
    notice = _work_url(key)
    if not notice:
        return None
    isbns: list[str] = []
    for raw in _values(row.get("isbn"), limit=20):
        isbn = normalize_isbn(raw)
        if isbn and isbn not in isbns:
            isbns.append(isbn)
    editions = tuple(filter(None, (normalize_olid(value) for value in _values(row.get("edition_key"), limit=10))))
    try:
        year = int(row.get("first_publish_year")) if row.get("first_publish_year") not in (None, "") else None
    except (TypeError, ValueError):
        year = None
    cover = ""
    try:
        cover_id = int(row.get("cover_i"))
        if cover_id > 0:
            cover = f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg"
    except (TypeError, ValueError):
        pass
    record_id = normalize_olid(key) or key.strip("/").split("/")[-1]
    return BibliographyRecord(
        source_id="openlibrary",
        record_id=record_id,
        title=title,
        authors=_values(row.get("author_name"), limit=12),
        isbns=tuple(isbns),
        languages=_values(row.get("language"), limit=12),
        subjects=_values(row.get("subject"), limit=30),
        publishers=_values(row.get("publisher"), limit=12),
        edition_ids=editions,
        first_publish_year=year,
        notice_url=notice,
        cover_url=cover,
        age_ranges=(),
        metadata={"bibliographic_only": True, "age_evidence": "none", "source": "openlibrary.search"},
    )


class OpenLibraryBibliography:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        contact_email: str = "",
        timeout_seconds: float = 12.0,
        cache_size: int = 128,
        clock=monotonic,
        sleeper=sleep,
    ) -> None:
        self.contact_email = _text(contact_email)
        self.identified = bool(_EMAIL.fullmatch(self.contact_email))
        user_agent = "Cifereca-Youth-OpenLibrary/0.1"
        if self.identified:
            user_agent += f" ({self.contact_email})"
        self._own_client = client is None
        self.client = client or httpx.Client(
            timeout=max(1.0, min(float(timeout_seconds), 30.0)),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )
        self.cache_size = max(1, min(int(cache_size), 512))
        self._cache: OrderedDict[tuple, tuple[BibliographyRecord, ...]] = OrderedDict()
        self._clock, self._sleep = clock, sleeper
        self._last_request = -1e9
        self._lock = Lock()

    def source_info(self) -> Mapping[str, str]:
        return {
            "source_id": "openlibrary",
            "display_name": "Open Library",
            "homepage_url": BASE + "/",
            "age_evidence": "none",
        }

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    def _throttle(self) -> None:
        minimum = 1.0 / 3.0 if self.identified else 1.0
        with self._lock:
            now = self._clock()
            wait = minimum - (now - self._last_request)
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
            self._last_request = now

    def _get_json(self, url: str) -> object:
        self._throttle()
        response = self.client.get(url)
        if response.status_code == 404:
            return {}
        if response.status_code >= 300:
            raise RuntimeError(f"Open Library HTTP {response.status_code}")
        content_type = _text(response.headers.get("content-type")).casefold()
        if content_type and "json" not in content_type:
            raise RuntimeError("Open Library response is not JSON")
        if len(response.content) > 2_000_000:
            raise RuntimeError("Open Library response exceeds 2 MB")
        try:
            return response.json()
        except Exception as exc:
            raise RuntimeError("Open Library invalid JSON") from exc

    def _request(self, *, q: str, limit: int, page: int = 1, lang: str = "") -> tuple[BibliographyRecord, ...]:
        clean_q = normalize_query(q)
        if not clean_q:
            return ()
        bounded_limit = max(1, min(int(limit), 20))
        bounded_page = max(1, min(int(page), 100))
        language = normalize_language(lang)
        cache_key = (clean_q, bounded_limit, bounded_page, language)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            return cached
        self._throttle()
        params = {"q": clean_q, "fields": _FIELDS, "limit": str(bounded_limit), "page": str(bounded_page)}
        if language:
            params["lang"] = language
        response = self.client.get(SEARCH_URL, params=params)
        if response.status_code >= 300:
            raise RuntimeError(f"Open Library HTTP {response.status_code}")
        content_type = _text(response.headers.get("content-type")).casefold()
        if content_type and "json" not in content_type:
            raise RuntimeError("Open Library response is not JSON")
        if len(response.content) > 2_000_000:
            raise RuntimeError("Open Library response exceeds 2 MB")
        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError("Open Library invalid JSON") from exc
        docs = payload.get("docs", ()) if isinstance(payload, Mapping) else ()
        if not isinstance(docs, (list, tuple)):
            docs = ()
        records = tuple(record for row in docs[:bounded_limit] if (record := parse_record(row)) is not None)
        self._cache[cache_key] = records
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return records

    def lookup_isbn(self, isbn: object) -> tuple[BibliographyRecord, ...]:
        normalized = normalize_isbn(isbn)
        if not normalized:
            return ()
        rows = self._request(q=f"isbn:{normalized}", limit=5)
        return tuple(row for row in rows if normalized in row.isbns)

    def lookup_olid(self, olid: object) -> tuple[BibliographyRecord, ...]:
        normalized = normalize_olid(olid)
        if not normalized:
            return ()
        cache_key = ("olid", normalized)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            return cached
        prefix = "/works/" if normalized.endswith("W") else "/books/"
        payload = self._get_json(BASE + prefix + normalized + ".json")
        record = parse_direct_record(payload, olid=normalized)
        records = (record,) if record is not None else ()
        self._cache[cache_key] = records
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return records

    def search(self, query: str, *, limit: int = 10, page: int = 1, language: str = "") -> tuple[BibliographyRecord, ...]:
        return self._request(q=normalize_query(query), limit=limit, page=page, lang=language)


PLUGIN = OpenLibraryBibliography()
