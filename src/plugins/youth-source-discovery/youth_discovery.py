from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
from html.parser import HTMLParser
import ipaddress
import re
from typing import Callable, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

_WS = re.compile(r"\s+")

_STRONG_PATTERNS = (
    ("interest", re.compile(r"\binterest\s+age\s*:?\s*(\d{1,2})(?:\s*(?:[-–—]|to)\s*(\d{1,2})|\s*\+)?", re.I)),
    ("reading", re.compile(r"\breading\s+age\s*:?\s*(\d{1,2})(?:\s*(?:[-–—]|to)\s*(\d{1,2})|\s*\+)?", re.I)),
    ("recommended", re.compile(r"\b(?:recommended|recommend(?:ed)?\s+for|for)\s+ages?\s*:?\s*(\d{1,2})(?:\s*(?:[-–—]|to)\s*(\d{1,2})|\s*\+)?", re.I)),
    ("recommended", re.compile(r"\bages?\s*:?\s*(\d{1,2})\s*(?:[-–—]|to)\s*(\d{1,2})\b", re.I)),
    ("recommended", re.compile(r"\b(?:age\s+range|target\s+age)\s*:?\s*(\d{1,2})(?:\s*(?:[-–—]|to)\s*(\d{1,2})|\s*\+)?", re.I)),
    ("recommended", re.compile(r"\b(?:à\s+partir\s+de|dès)\s*(\d{1,2})\s*ans?\b", re.I)),
    ("recommended", re.compile(r"\bjusqu[’']?à\s*(\d{1,2})\s*ans?\b", re.I)),
    ("recommended", re.compile(r"\b(\d{1,2})\s*(?:[-–—]|à)\s*(\d{1,2})\s*ans?\b", re.I)),
)

_GENERIC_AGE = re.compile(r"\b(\d{1,2})\s*(?:\+|ans?\b|years?\s+(?:old|and\s+up)\b)", re.I)
_MONTH_AGE = re.compile(
    r"\b(?:age\s*:?\s*|from\s+|à\s+partir\s+de\s+|dès\s+)?"
    r"(\d{1,3})\s*\+?\s*(?:months?|mois)\b",
    re.I,
)
_YOUTH_CONTEXT = re.compile(
    r"\b(?:book|books|reading|reader|children|child|kids?|youth|teen|teens|young\s+adult|"
    r"livre|livres|lecture|lecteur|lectrice|enfant|enfants|jeunesse|ado|ados|adolescent|adolescente)\b",
    re.I,
)


def _text(value: object) -> str:
    return _WS.sub(" ", str(value or "").strip())


def _fold(value: object) -> str:
    return _text(value).casefold()


def canonical_url(value: object) -> str:
    raw = _text(value)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return ""
    scheme = parts.scheme.casefold()
    if scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return ""
    host = parts.hostname.casefold()
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def source_domain(url: object) -> str:
    value = canonical_url(url)
    return urlsplit(value).hostname.casefold() if value else ""


def is_public_candidate_url(url: object) -> bool:
    value = canonical_url(url)
    if not value:
        return False
    host = source_domain(value)
    if not host or host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return True
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


@dataclass(frozen=True, slots=True)
class AgeEvidence:
    kind: str
    label: str
    age_min: int
    age_max: int | None
    strength: int
    origin: str
    unit: str = "years"
    age_min_months: int | None = None


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    domain: str
    homepage_url: str
    title: str
    score: int
    status: str
    evidence: tuple[AgeEvidence, ...]
    sample_urls: tuple[str, ...]
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    query: str
    candidates: tuple[SourceCandidate, ...]
    inspected_pages: int
    search_errors: tuple[str, ...] = ()
    fetch_errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QualificationCandidate:
    fingerprint: str
    domain: str
    title: str
    homepage_url: str
    score: int
    status: str
    evidence: tuple[AgeEvidence, ...]
    evidence_kinds: tuple[str, ...]
    evidence_origins: tuple[str, ...]
    sample_urls: tuple[str, ...]
    diagnostics: tuple[str, ...]
    manual_review_required: bool = True


@dataclass(frozen=True, slots=True)
class QualificationDossier:
    schema_version: int
    query: str
    candidates: tuple[QualificationCandidate, ...]
    search_errors: tuple[str, ...] = ()
    fetch_errors: tuple[str, ...] = ()


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        if tag.casefold() == "meta":
            data = {str(k).casefold(): str(v or "") for k, v in attrs}
            name = _fold(data.get("name") or data.get("property"))
            if name in {"description", "og:description", "twitter:description"}:
                content = _text(data.get("content"))
                if content:
                    self.parts.append(content)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        value = _text(data)
        if value:
            self.parts.append(value)


def html_visible_text(html: object, *, max_chars: int = 120_000) -> str:
    parser = _TextParser()
    try:
        parser.feed(str(html or "")[:max_chars])
        parser.close()
    except Exception:
        return ""
    return _text(" ".join(parser.parts))[:max_chars]


def _valid_age(lo: int, hi: int | None) -> bool:
    return 0 <= lo < 18 and (hi is None or lo <= hi < 18)


def detect_age_evidence(text: object, *, origin: str = "snippet") -> tuple[AgeEvidence, ...]:
    value = _text(text)
    if not value:
        return ()
    found: list[AgeEvidence] = []
    spans: list[tuple[int, int]] = []
    for kind, pattern in _STRONG_PATTERNS:
        for match in pattern.finditer(value):
            if any(match.start() < end and start < match.end() for start, end in spans):
                continue
            lo = int(match.group(1))
            hi = int(match.group(2)) if match.lastindex and match.lastindex >= 2 and match.group(2) else None
            if not _valid_age(lo, hi):
                continue
            label = _text(match.group(0))
            item = AgeEvidence(kind, label, lo, hi, 100 if kind in {"interest", "reading"} else 90, origin)
            if item not in found:
                found.append(item)
                spans.append(match.span())

    for match in _MONTH_AGE.finditer(value):
        amount = int(match.group(1))
        if amount < 0 or amount >= 18 * 12:
            continue
        projected = int(math.ceil(amount / 12)) if amount else 0
        if projected >= 18:
            continue
        label = _text(match.group(0))
        item = AgeEvidence(
            "publisher", label, projected, None, 90, origin,
            unit="months", age_min_months=amount,
        )
        if item not in found:
            found.append(item)
            spans.append(match.span())

    for match in _GENERIC_AGE.finditer(value):
        if any(start <= match.start() < end or start < match.end() <= end for start, end in spans):
            continue
        lo = int(match.group(1))
        if not _valid_age(lo, None):
            continue
        window = value[max(0, match.start() - 70): min(len(value), match.end() + 70)]
        if not _YOUTH_CONTEXT.search(window):
            continue
        label = _text(match.group(0))
        item = AgeEvidence("recommended", label, lo, None, 55, origin)
        if item not in found:
            found.append(item)
    return tuple(found)


def _field(row: object, name: str, default: object = "") -> object:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _search_rows(value: object) -> Iterable[object]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        rows = value.get("items") or value.get("results") or value.get("hits") or ()
        return rows if not isinstance(rows, (str, bytes, Mapping)) else ()
    if isinstance(value, (str, bytes)):
        return ()
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError:
        return ()


def _fetch_text(value: object) -> str:
    if isinstance(value, Mapping):
        content_type = _fold(value.get("content_type") or value.get("mime_type"))
        if content_type and "html" not in content_type and "text" not in content_type:
            return ""
        return str(value.get("text") or value.get("content") or "")
    return str(value or "")


def _candidate_score(evidence: Iterable[AgeEvidence], *, pages: int, distinct_urls: int) -> int:
    rows = tuple(evidence)
    if not rows:
        return 0
    strong = max(item.strength for item in rows)
    kinds = len({item.kind for item in rows})
    return min(100, strong + min(6, (kinds - 1) * 3) + min(4, max(0, distinct_urls - 1) * 2) + min(2, pages - 1))


def discover_sources(
    query: str,
    *,
    search: Callable[..., object],
    fetch: Callable[[str], object] | None = None,
    limit: int = 20,
    inspect_limit: int = 5,
) -> DiscoveryReport:
    """Discover candidate youth sources without activating or registering any source."""
    clean_query = _text(query)
    bounded_limit = max(1, min(int(limit), 100))
    bounded_inspect = max(0, min(int(inspect_limit), 10))
    search_errors: list[str] = []
    fetch_errors: list[str] = []
    rows: list[object] = []

    queries = (
        clean_query,
        _text(f'{clean_query} "reading age" OR "interest age"'),
        _text(f'{clean_query} jeunesse "ans" OR "for ages"'),
    )
    seen_query: set[str] = set()
    for search_query in queries:
        if not search_query or search_query in seen_query:
            continue
        seen_query.add(search_query)
        try:
            result = search(search_query, limit=bounded_limit)
            rows.extend(_search_rows(result))
        except TypeError:
            try:
                result = search(search_query)
                rows.extend(_search_rows(result))
            except Exception as exc:
                search_errors.append(f"{search_query}: {type(exc).__name__}: {exc}")
        except Exception as exc:
            search_errors.append(f"{search_query}: {type(exc).__name__}: {exc}")

    grouped: dict[str, dict[str, object]] = {}
    ordered_urls: list[str] = []
    for row in rows:
        url = canonical_url(_field(row, "url") or _field(row, "link") or _field(row, "href"))
        domain = source_domain(url)
        if not url or not domain or not is_public_candidate_url(url):
            continue
        title = _text(_field(row, "title"))
        snippet = _text(_field(row, "snippet") or _field(row, "description") or _field(row, "text"))
        evidence = detect_age_evidence(" ".join((title, snippet)), origin="search")
        group = grouped.setdefault(domain, {
            "title": title or domain,
            "urls": [],
            "evidence": [],
            "diagnostics": [],
            "pages": 0,
        })
        if title and (not group["title"] or group["title"] == domain):
            group["title"] = title
        urls = group["urls"]
        if isinstance(urls, list) and url not in urls:
            urls.append(url)
            ordered_urls.append(url)
        ev = group["evidence"]
        if isinstance(ev, list):
            for item in evidence:
                if item not in ev:
                    ev.append(item)

    inspected = 0
    if fetch is not None and bounded_inspect:
        for url in ordered_urls:
            if inspected >= bounded_inspect:
                break
            domain = source_domain(url)
            group = grouped.get(domain)
            if group is None:
                continue
            try:
                raw = fetch(url)
                inspected += 1
                group["pages"] = int(group["pages"]) + 1
                text = html_visible_text(_fetch_text(raw))
                evidence = detect_age_evidence(text, origin="page")
                ev = group["evidence"]
                if isinstance(ev, list):
                    for item in evidence:
                        if item not in ev:
                            ev.append(item)
            except Exception as exc:
                fetch_errors.append(f"{url}: {type(exc).__name__}: {exc}")
                diag = group["diagnostics"]
                if isinstance(diag, list):
                    diag.append("page inspection failed")

    candidates: list[SourceCandidate] = []
    for domain, group in grouped.items():
        evidence = tuple(group["evidence"]) if isinstance(group["evidence"], list) else ()
        if not evidence:
            continue
        urls = tuple(group["urls"]) if isinstance(group["urls"], list) else ()
        pages = int(group["pages"])
        score = _candidate_score(evidence, pages=pages, distinct_urls=len(urls))
        status = "strong" if score >= 90 else "candidate"
        candidates.append(SourceCandidate(
            domain=domain,
            homepage_url=f"https://{domain}/",
            title=_text(group["title"]) or domain,
            score=score,
            status=status,
            evidence=evidence,
            sample_urls=urls[:5],
            diagnostics=tuple(group["diagnostics"]) if isinstance(group["diagnostics"], list) else (),
        ))

    candidates.sort(key=lambda item: (-item.score, item.domain))
    return DiscoveryReport(clean_query, tuple(candidates), inspected, tuple(search_errors), tuple(fetch_errors))


class YouthSourceDiscoveryPlugin:
    def discover(
        self,
        query: str,
        *,
        search: Callable[..., object],
        fetch: Callable[[str], object] | None = None,
        limit: int = 20,
        inspect_limit: int = 5,
    ) -> DiscoveryReport:
        return discover_sources(query, search=search, fetch=fetch, limit=limit, inspect_limit=inspect_limit)


PLUGIN = YouthSourceDiscoveryPlugin()
