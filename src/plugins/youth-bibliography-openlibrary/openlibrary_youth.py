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
_OLID = re.compile(r"^OL[0-9]+[WMA]$", re.I)
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


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


def _work_url(key: object) -> str:
    raw = _text(key)
    if not raw:
        return ""
    if raw.startswith("/"):
        url = BASE + raw
    else:
        olid = normalize_olid(raw)
        if not olid:
            return ""
        prefix = "/works/" if olid.endswith("W") else "/books/" if olid.endswith("M") else "/authors/"
        url = BASE + prefix + olid
    parts = urlsplit(url)
    return url if parts.scheme == "https" and parts.hostname == "openlibrary.org" else ""


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
    age_ranges: tuple[()] = ()
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

    def _request(self, *, q: str, limit: int, page: int = 1, lang: str = "") -> tuple[BibliographyRecord, ...]:
        clean_q = _text(q)
        if not clean_q:
            return ()
        bounded_limit = max(1, min(int(limit), 20))
        bounded_page = max(1, min(int(page), 100))
        language = _text(lang).casefold()[:8]
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
        if response.status_code >= 400:
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
        return self._request(q=f"olid:{normalized}", limit=5)

    def search(self, query: str, *, limit: int = 10, page: int = 1, language: str = "") -> tuple[BibliographyRecord, ...]:
        return self._request(q=_text(query), limit=limit, page=page, lang=language)


PLUGIN = OpenLibraryBibliography()
