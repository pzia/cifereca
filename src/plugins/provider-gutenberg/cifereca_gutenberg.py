from __future__ import annotations
from collections import OrderedDict
import html
import logging
from datetime import date, datetime
import re
import time
from unicodedata import category
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import httpx

from cifereca.plugin_api import (
    Account,
    AcquisitionOffer,
    ItemDetail,
    ProviderCapabilities,
    ProviderSearchCapabilities,
    ProviderCapabilityError,
    ProviderConfigurationError,
    ProviderDescriptor,
    ProviderError,
    ProviderPlugin,
    ProviderSession, normalize_language_tag, sanitize_filename_component, description_text, description_element,
    SearchResult,
)

ATOM = "http://www.w3.org/2005/Atom"
DCTERMS = "http://purl.org/dc/terms/"
LANGUAGE_SEARCH_NAMES = {
    "fr": "French", "french": "French", "francais": "French", "français": "French",
    "en": "English", "english": "English",
    "de": "German", "german": "German", "deutsch": "German",
    "es": "Spanish", "spanish": "Spanish", "espanol": "Spanish", "español": "Spanish",
    "it": "Italian", "italian": "Italian", "pt": "Portuguese", "portuguese": "Portuguese",
    "nl": "Dutch", "dutch": "Dutch",
}
LANGUAGE_SEARCH_CODES = {
    "french": "fr", "francais": "fr", "français": "fr", "english": "en",
    "german": "de", "deutsch": "de", "spanish": "es", "espanol": "es", "español": "es",
    "italian": "it", "portuguese": "pt", "dutch": "nl",
}
DEFAULT_OPDS_SEARCH_URL = "https://www.gutenberg.org/ebooks/search.opds/"
GUTENBERG_PAGE_SIZE = 25
MAX_REQUESTED_PAGES = 101
MAX_SEARCH_OPDS_BYTES = 2 * 1024 * 1024  # Bound parsed Atom and HTTP body.
_EPUB_REL = "http://opds-spec.org/acquisition"
_EBOOK_RE = re.compile(r"(?:urn:gutenberg:|/ebooks/)(\d+)", re.I)
_LOG = logging.getLogger("cifereca.plugin.provider.gutenberg")


def _attested_date(value: str | None) -> str | None:
    """Accept a precise OPDS date, without inferring an edition publication.

    Atom published/updated refer to the feed entry, never the print book.
    Dublin Core issued is the bibliographic publication date only if it
    contains a valid ISO year, year-month or full date.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) == 4 and text.isascii() and text.isdecimal():
        return text if 1 <= int(text) <= 9999 else None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}", text):
        year, month = (int(piece) for piece in text.split("-"))
        return text if 1 <= year <= 9999 and 1 <= month <= 12 else None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        try:
            date.fromisoformat(text)
        except ValueError:
            return None
        return text
    return None


def _attested_atom_timestamp(value: str | None) -> str | None:
    """Keep public Atom entry chronology as metadata, never as book issued date."""
    if not isinstance(value, str) or len(value) > 40:
        return None
    text = value.strip()
    if _attested_date(text):
        return text
    if "T" not in text or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})", text,
    ):
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Offset-aware dates only; malformed "2025-99" rejected.
    return text if parsed.tzinfo is not None else None


def _safe_url(value: str) -> str:
    """Return a log-safe URL without query/fragment."""
    try:
        parts = urlsplit(str(value or ""))
        if parts.scheme.casefold() == "data":
            return "data:<redacted>"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return "<invalid-url>"


def _unavailable_opds_xml():
    """Malformed Atom is a technical search outage, not a confirmed zero."""
    failure = ProviderError("Project Gutenberg OPDS : XML invalide (invalid_opds_feed)")
    failure.code = "search_temporarily_unavailable"
    failure.retryable = False  # No second equivalent OPDS GET.
    return failure


DESCRIPTOR = ProviderDescriptor(
    id="gutenberg",
    display_name="Project Gutenberg",
    description="Project Gutenberg : bibliothèque publique de livres du domaine public, via OPDS.",
    state="ready",
    capabilities=ProviderCapabilities(search=True, holdings=True, direct_download=True),
    search_capabilities=ProviderSearchCapabilities(
        fields=("all", "title", "author", "subject", "language"),
        scopes=("all", "digital"),
        pagination="next-link",
        page_size=GUTENBERG_PAGE_SIZE,
        max_pages=MAX_REQUESTED_PAGES,
        notes=(
            "title/author/subject/language are local post-filters on the fetched OPDS page, not server-side fields",
            "bookshelf remains available through the general query prefix bookshelf:...",
        ),
    ),
    homepage="https://www.gutenberg.org",
    notes=(
        "Catalogue public sans authentification.",
        "Project Gutenberg demande un User-Agent avec une adresse de contact et un rythme de requêtes comparable à celui d'un utilisateur.",
        "Les droits Project Gutenberg sont principalement déterminés selon le droit américain ; vérifier la situation locale avant redistribution.",
    ),
)


def _search_facet(query: str, field: str) -> tuple[str, str, str]:
    raw = " ".join(str(query or "").split())
    selected = str(field or "all").casefold()
    if selected in {"all", ""}:
        match = re.match(r"^(?:lang|language):\s*(.+)$", raw, re.I)
        if match:
            selected = "language"
            raw = match.group(1).strip()
        else:
            match = re.match(r"^(?:bookshelf|shelf):\s*(.+)$", raw, re.I)
            if match:
                selected = "bookshelf"
                raw = match.group(1).strip()
    if selected == "language":
        folded = raw.casefold()
        canonical = LANGUAGE_SEARCH_CODES.get(folded) or normalize_language_tag(folded)
        server = LANGUAGE_SEARCH_NAMES.get(folded) or LANGUAGE_SEARCH_NAMES.get(canonical or "") or raw
        return server, "language", canonical or folded
    if selected in {"bookshelf", "category"}:
        return raw, "bookshelf", raw
    return raw, selected, raw


class GutenbergSession(ProviderSession):
    """Project Gutenberg OPDS session.

    The plugin requests only the OPDS page explicitly asked by the caller.
    For page N it follows Atom rel=next links at most N times; it never prefetches
    beyond the requested page or crawls the catalog.
    """

    def __init__(self, *, config: Any, diagnostics: Any, transport: Any = None, **kwargs: Any) -> None:
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        self.diagnostics = diagnostics
        self.transport = transport
        timeout = float(
            self.options.get("timeout_seconds")
            or (
                config.request_timeout_seconds("gutenberg", self.credential_id)
                if hasattr(config, "request_timeout_seconds")
                else config.get("request_timeout_seconds", 20)
            )
        )
        self.opds_search_url = str(self.options.get("opds_search_url") or DEFAULT_OPDS_SEARCH_URL).strip()
        self.prefer_images = bool(self.options.get("prefer_images", True))
        self.max_download_bytes = int(self.options.get("max_download_bytes", 100 * 1024 * 1024))
        self.contact = self._normalize_contact(str(self.options.get("contact") or ""))
        user_agent = "cifereca-gutenberg/1.2.19"
        if self.contact:
            user_agent += f" (+{self.contact})"
        self.user_agent = user_agent
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            http2=False,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/atom+xml;profile=opds-catalog, application/atom+xml;q=0.9, application/xml;q=0.8, */*;q=0.2",
            },
        )
        self._page_urls = OrderedDict()
        self._detail_feed_cache = OrderedDict()
        self._page_cache_keys = 32
        self._detail_cache_entries = 64

    @staticmethod
    def _normalize_contact(value: str) -> str:
        value = " ".join(str(value or "").strip().split())
        if not value:
            return ""
        if "@" in value and "://" not in value and not value.lower().startswith("mailto:"):
            return "mailto:" + value
        return value

    def _require_contact(self) -> None:
        if not self.contact:
            raise ProviderConfigurationError(
                "Project Gutenberg : configurez une adresse e-mail ou URL de contact pour le User-Agent OPDS."
            )
        if "\r" in self.contact or "\n" in self.contact:
            raise ProviderConfigurationError("Project Gutenberg : contact User-Agent invalide")

    def _diagnostic(self, *args: Any, **kwargs: Any) -> None:
        try:
            if self.diagnostics is not None:
                self.diagnostics.add(*args, **kwargs)
        except Exception:
            pass

    def connect(self) -> list[Account]:
        self.connected = True
        self._accounts = [
            Account(
                display_name="Catalogue public",
                barcode="",
                unique_id="",
                is_primary=True,
                provider_id=self.provider_id,
                credential_id=self.credential_id,
                provider_label=self.descriptor.display_name,
            )
        ]
        return self.accounts

    def close(self) -> None:
        self.client.close()
        self.connected = False

    @staticmethod
    def _text(node: ET.Element | None) -> str | None:
        if node is None:
            return None
        value = " ".join("".join(node.itertext()).split())
        return html.unescape(value) if value else None

    @staticmethod
    def _ebook_number(entry: ET.Element, *urls: str | None) -> str | None:
        values = [GutenbergSession._text(entry.find(f"{{{ATOM}}}id")), *urls]
        for value in values:
            if not value:
                continue
            match = _EBOOK_RE.search(str(value))
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _safe_name(value: str, fallback: str = "gutenberg") -> str:
        return sanitize_filename_component(value, fallback)

    def _parse_feed(self, content: bytes, base_url: str) -> list[SearchResult]:
        started = time.perf_counter()
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            # XML parser details (and its exception chain) are not safe
            # public diagnostics. Keep a closed reason and byte count.
            _LOG.warning(
                "TRACE gutenberg parse failed bytes=%s reason=invalid_opds_feed",
                len(content),
            )
            self._diagnostic(
                "GUTENBERG", "OPDS parse", "error",
                duration_ms=int((time.perf_counter() - started) * 1000),
                detail={"bytes": len(content), "reason": "invalid_opds_feed"},
                level="detailed",
            )
            raise _unavailable_opds_xml() from None

        # HTML/login pages and unrelated valid XML are not empty Atom feeds.
        # A 200 transport success never proves a catalogue zero on wrong XML.
        if root.tag != f"{{{ATOM}}}feed":
            raise _unavailable_opds_xml() from None

        entries = root.findall(f"{{{ATOM}}}entry")
        stats = {
            "entries": len(entries),
            "placeholder": 0,
            "without_epub": 0,
            "without_ebook_id": 0,
            "candidate_editions": 0,
            "detail_only": 0,
        }
        candidates: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            title = self._text(entry.find(f"{{{ATOM}}}title")) or "Sans titre"
            # Gutenberg returns a placeholder entry for no-result queries.
            if title.casefold() == "no records found.":
                stats["placeholder"] += 1
                continue
            authors = [self._text(a.find(f"{{{ATOM}}}name")) for a in entry.findall(f"{{{ATOM}}}author")]
            author = ", ".join(x for x in authors if x) or None
            description = description_element(entry.find(f"{{{ATOM}}}content")) or description_element(entry.find(f"{{{ATOM}}}summary"))
            # OPDS 1.2: atom:published is when the feed entry became
            # accessible, atom:updated is the entry's last revision. Neither
            # establishes the publication date of the described ebook.
            # Only an explicit dcterms:issued may populate published_at.
            issued = _attested_date(self._text(entry.find(f"{{{DCTERMS}}}issued")))
            entry_published = _attested_atom_timestamp(self._text(entry.find(f"{{{ATOM}}}published")))
            entry_updated = _attested_atom_timestamp(self._text(entry.find(f"{{{ATOM}}}updated")))
            language = normalize_language_tag(self._text(entry.find(f"{{{DCTERMS}}}language")))
            rights = self._text(entry.find(f"{{{ATOM}}}rights"))
            categories = [
                (html.unescape(str(category.attrib.get("term") or "").strip()), str(category.attrib.get("scheme") or ""))
                for category in entry.findall(f"{{{ATOM}}}category")
                if str(category.attrib.get("term") or "").strip()
            ]
            subjects = [term for term, scheme in categories if "LCSH" in scheme]
            bookshelves = [term for term, scheme in categories if "LCSH" not in scheme]

            thumbnail: str | None = None
            cover: str | None = None
            friendly: str | None = None
            detail_url: str | None = None
            epub_links: list[tuple[str, str]] = []
            link_shapes: list[tuple[str, str, str]] = []
            # OPDS is Atom-based, but be tolerant of servers that serialize link
            # elements with a different/no namespace.  The semantic attributes
            # (rel/type/href) are what matter here.
            links = [node for node in entry.iter() if str(node.tag).split("}")[-1] == "link"]
            for link in links:
                href = html.unescape(str(link.attrib.get("href") or "").strip())
                if not href:
                    continue
                href = urljoin(base_url, href)
                rel = str(link.attrib.get("rel") or "").strip()
                typ = str(link.attrib.get("type") or "").split(";", 1)[0].strip().lower()
                link_title = str(link.attrib.get("title") or "")
                href_lower = href.lower()
                is_epub_mime = typ in {"application/epub+zip", "application/epub"}
                is_epub_href = bool(re.search(r"\.epub(?:3)?(?:[./?]|$)", href_lower))
                is_acquisition = "acquisition" in rel.casefold()
                if len(link_shapes) < 12:
                    link_shapes.append((rel or "<none>", typ or "<none>", _safe_url(href)))
                # MIME is authoritative.  If a server uses a generic MIME, accept
                # an EPUB-looking URL only when the relation is acquisition-like.
                if rel.casefold().startswith(_EPUB_REL) and (is_epub_mime or is_epub_href):
                    epub_links.append((href, link_title))
                elif "thumbnail" in rel.casefold():
                    if urlsplit(href).scheme in {"http", "https"}:
                        thumbnail = thumbnail or href
                elif rel.casefold() in {"http://opds-spec.org/image", "https://opds-spec.org/image", "http://opds-spec.org/cover", "https://opds-spec.org/cover"}:
                    if urlsplit(href).scheme in {"http", "https"}:
                        cover = cover or href
                elif rel == "alternate" and typ in {"text/html", "application/xhtml+xml", ""}:
                    friendly = friendly or href
                elif rel == "subsection" and re.search(r"/ebooks/\d+\.opds(?:[/?]|$)", href, re.I):
                    detail_url = detail_url or href

            ebook_no = self._ebook_number(entry, friendly, detail_url, *(href for href, _ in epub_links))
            if not epub_links and not detail_url:
                stats["without_epub"] += 1
                _LOG.debug(
                    "TRACE gutenberg entry without-epub title=%r links=%s",
                    title[:120], link_shapes,
                )
                continue
            if not ebook_no:
                stats["without_ebook_id"] += 1
                continue
            if epub_links:
                stats["candidate_editions"] += 1
            else:
                stats["detail_only"] += 1

            def epub_rank(item: tuple[str, str]) -> tuple[int, int]:
                href, label = item
                text = (href + " " + label).casefold()
                with_images = "images" in text and "noimages" not in text and "no images" not in text
                epub3 = ".epub3" in text or "epub3" in text
                if self.prefer_images:
                    return (2 if with_images else 0, 1 if epub3 else 0)
                return (2 if not with_images else 0, 1 if epub3 else 0)

            acquisition = None
            if epub_links:
                acquisition, _label = max(epub_links, key=epub_rank)
            candidates.setdefault(ebook_no, []).append({
                "title": title,
                "author": author,
                "description": description,
                "published": issued,
                "opds_entry_published": entry_published,
                "opds_entry_updated": entry_updated,
                "language": language,
                "rights": rights,
                "subjects": subjects,
                "bookshelves": bookshelves,
                "thumbnail": thumbnail or cover,
                "cover_origin": "opds_thumbnail" if thumbnail else ("opds_image" if cover else "unavailable"),
                "friendly": friendly or f"https://www.gutenberg.org/ebooks/{ebook_no}",
                "download": acquisition,
                "detail_url": detail_url,
            })

        rows: list[SearchResult] = []
        for ebook_no, editions in candidates.items():
            # The OPDS feed may expose image/no-image editions as separate entries.
            # Pick the edition whose URL matches the user's preference.
            editions.sort(
                key=lambda row: (
                    int("images" in str(row.get("download") or "").casefold() and "noimages" not in str(row.get("download") or "").casefold()),
                    int("epub3" in str(row.get("download") or "").casefold()),
                ),
                reverse=self.prefer_images,
            )
            row = dict(editions[0])
            if not row.get("thumbnail"):
                sibling = next((edition for edition in editions if edition.get("thumbnail")), None)
                if sibling:
                    row["thumbnail"] = sibling["thumbnail"]
                    row["cover_origin"] = "opds_other_edition"
            rows.append(SearchResult(
                id=f"gutenberg:{ebook_no}",
                record_id=ebook_no,
                docbase="GUTENBERG",
                medium="digital",
                title=str(row["title"]),
                author=row["author"],
                type="Livre numérique",
                format="EPUB",
                publisher="Project Gutenberg",
                published_at=row["published"],
                description=row["description"],
                thumbnail_url=row["thumbnail"],
                friendly_url=row["friendly"],
                scenario="OPDS",
                source_provider=self.provider_id,
                source_credential=self.credential_id,
                download_url=(str(row["download"]) if row.get("download") else None),
                content_type="ebook",
                source_collection="Project Gutenberg",
                # Public availability is not user ownership.  Gutenberg entries
                # stay directly downloadable, but must not be mirrored as remote_owned.
                owned=False,
                downloadable=True,
                metadata={
                    "language": row["language"],
                    "rights": row["rights"],
                    # Keep provenance distinct and never fabricate a print
                    # edition's year from the Atom feed update timestamp.
                    **({"publication_date_evidence": "opds.dcterms.issued"}
                       if row.get("published") else {}),
                    **({"opds_entry_published": row["opds_entry_published"]}
                       if row.get("opds_entry_published") else {}),
                    **({"opds_entry_updated": row["opds_entry_updated"]}
                       if row.get("opds_entry_updated") else {}),
                    "subjects": list(row["subjects"]),
                    "bookshelves": list(row.get("bookshelves") or []),
                    "opds_detail_url": row.get("detail_url"),
                    "cover_source": row["cover_origin"],
                    **({
                        "catalog_cover": {
                            "url": row["thumbnail"],
                            "source": f"gutenberg.{row['cover_origin']}",
                            "evidence_id": f"gutenberg:{ebook_no}",
                            "evidence_strength": (
                                100 if row["cover_origin"] == "opds_thumbnail"
                                else 90 if row["cover_origin"] == "opds_image"
                                else 80
                            ),
                        }
                    } if row.get("thumbnail") else {}),
                },
            ))
        duplicate_editions = max(0, stats["candidate_editions"] - len(candidates))
        duration_ms = int((time.perf_counter() - started) * 1000)
        detail = {
            **stats,
            "covers_available": sum(bool(row.thumbnail_url) for row in rows),
            "covers_unavailable": sum(not row.thumbnail_url for row in rows),
            "unique_books": len(candidates),
            "duplicate_editions": duplicate_editions,
            "rows": len(rows),
            "bytes": len(content),
            "base_url": _safe_url(base_url),
        }
        _LOG.info(
            "TRACE gutenberg parse end entries=%s candidate_editions=%s detail_only=%s unique_books=%s rows=%s without_epub=%s placeholder=%s duplicates=%s dt_ms=%s",
            stats["entries"], stats["candidate_editions"], stats["detail_only"], len(candidates), len(rows),
            stats["without_epub"], stats["placeholder"], duplicate_editions, duration_ms,
        )
        _LOG.debug(
            "TRACE gutenberg parse sample=%s",
            [(row.record_id, row.title, _safe_url(str(row.download_url or ""))) for row in rows[:5]],
        )
        self._diagnostic(
            "GUTENBERG", "OPDS parse", "ok",
            duration_ms=duration_ms, detail=detail, level="detailed",
        )
        return rows

    @staticmethod
    def _next_page_url(content: bytes, base_url: str) -> str | None:
        # A *missing* Atom next link means end-of-catalogue; an invalid
        # server-advertised next link means pagination is unavailable, NOT
        # a successful empty page. Never follow a changed origin/TLS scheme.
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            # Malformed Atom is an XML failure, not a forged/missing next URL.
            raise _unavailable_opds_xml() from None
        if root.tag != f"{{{ATOM}}}feed":
            raise _unavailable_opds_xml() from None
        try:
            base = urlsplit(base_url)
            base_port = base.port or (443 if base.scheme == "https" else 80)
        except ValueError:
            raise ProviderError("Project Gutenberg : opds_next_link_unverified") from None
        next_urls = set()
        for link in root.findall(f"{{{ATOM}}}link"):
            rel = str(link.attrib.get("rel") or "").strip().casefold()
            if rel != "next":
                continue
            href = html.unescape(str(link.attrib.get("href") or ""))
            if (not href or len(href) > 2048 or href != href.strip()
                    or "\\" in href or any(ord(ch) < 33 or ord(ch) == 127 for ch in href)):
                raise ProviderError("Project Gutenberg : opds_next_link_unverified")
            try:
                resolved = urljoin(base_url, href)
                parsed = urlsplit(resolved)
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                if (parsed.scheme != base.scheme
                        or parsed.scheme not in {"http", "https"}
                        or not parsed.hostname or not base.hostname
                        or parsed.hostname.casefold() != base.hostname.casefold()
                        or port != base_port or parsed.username or parsed.password
                        or parsed.fragment or len(resolved) > 4096):
                    raise ValueError("unverified")
            except ValueError:
                raise ProviderError("Project Gutenberg : opds_next_link_unverified") from None
            next_urls.add(resolved)
            if len(next_urls) > 1:
                raise ProviderError("Project Gutenberg : opds_next_link_ambiguous")
        return next(iter(next_urls)) if next_urls else None

    @staticmethod
    def _field_filter(rows: list[SearchResult], query: str, field: str) -> list[SearchResult]:
        field = str(field or "all").lower()
        if field in {"all", ""}:
            return rows
        if field == "isbn":
            # Gutenberg's OPDS catalog does not expose ISBN as a stable search field.
            return []
        terms = [part.casefold() for part in query.split() if part]
        filtered: list[SearchResult] = []
        for item in rows:
            if field == "title":
                haystack = item.title
            elif field == "author":
                haystack = item.author or ""
            elif field == "subject":
                haystack = " ".join(str(v) for v in item.metadata.get("subjects", []))
            elif field == "bookshelf":
                haystack = " ".join(str(v) for v in (item.metadata.get("bookshelves", []) or item.metadata.get("subjects", [])))
            elif field == "language":
                language = str(item.metadata.get("language") or "")
                if language and normalize_language_tag(language) == normalize_language_tag(query):
                    filtered.append(item)
                continue
            else:
                return rows
            folded = haystack.casefold()
            if all(term in folded for term in terms):
                filtered.append(item)
        return filtered

    def _page_cache(self, query: str) -> list[str | None]:
        key=(self.opds_search_url, query.casefold())
        rows=self._page_urls.get(key)
        if rows is None:
            rows=[]; self._page_urls[key]=rows
        else:
            self._page_urls.move_to_end(key)
        while len(self._page_urls)>self._page_cache_keys: self._page_urls.popitem(last=False)
        return rows

    @staticmethod
    def _remember_page(rows: list[str | None], index: int, url: str | None) -> None:
        while len(rows)<=index: rows.append(None)
        if url: rows[index]=url

    def _detail_feed(self, url: str) -> tuple[bytes,str]:
        cached=self._detail_feed_cache.get(url)
        if cached is not None:
            self._detail_feed_cache.move_to_end(url); return cached
        response=self.client.get(url); response.raise_for_status(); value=(response.content,str(response.url))
        self._detail_feed_cache[url]=value
        while len(self._detail_feed_cache)>self._detail_cache_entries: self._detail_feed_cache.popitem(last=False)
        return value

    @staticmethod
    def _detail_cover(content: bytes, base_url: str) -> str | None:
        try: root=ET.fromstring(content)
        except ET.ParseError: return None
        image=None
        for link in root.iter():
            if str(link.tag).split('}')[-1] != 'link': continue
            rel=str(link.attrib.get('rel') or '').casefold(); href=str(link.attrib.get('href') or '').strip()
            if not href: continue
            resolved=urljoin(base_url,href); parsed=urlsplit(resolved)
            if parsed.scheme not in {'http','https'}: continue
            if 'thumbnail' in rel: return resolved
            if rel in {'http://opds-spec.org/image','https://opds-spec.org/image','http://opds-spec.org/cover','https://opds-spec.org/cover'}: image=image or resolved
        return image

    def search(self, query: str, scope: str = "all", page: int = 0, field: str = "all") -> list[SearchResult]:
        self.last_search_warnings = []
        if type(field) is not str or field.strip().casefold() not in DESCRIPTOR.search_capabilities.fields:
            raise ProviderCapabilityError("Project Gutenberg : unsupported_field")
        if type(scope) is not str or scope.strip().casefold() not in DESCRIPTOR.search_capabilities.scopes:
            raise ProviderCapabilityError("Project Gutenberg : unsupported_scope")
        if type(page) is not int or not 0 <= page < MAX_REQUESTED_PAGES:
            # Validate even an otherwise empty/too-short query: no malformed
            # page may be misreported as a successful empty search.
            raise ProviderCapabilityError("Project Gutenberg : invalid_page")
        # Gutenberg's local field parser previously stringified arbitrary
        # objects before deciding that a short search had no results.
        if query is None:
            query = ""
        if (type(query) is not str or len(query) > 2048
                or any(category(ch) in {"Cc", "Cf", "Cs"} for ch in query)):
            raise ProviderCapabilityError("Project Gutenberg : invalid_query")
        query, field, filter_query = _search_facet(query, field)
        if len(query) < 2:
            _LOG.info("TRACE gutenberg search skipped reason=query-too-short query_len=%s", len(query)); return []
        self._require_contact(); started = time.perf_counter(); rows_cache=self._page_cache(query)
        cached_indexes=[i for i,u in enumerate(rows_cache) if u and i<=page]
        if cached_indexes:
            page_index=max(cached_indexes); current_url=rows_cache[page_index]; params=None
        else:
            page_index=0; current_url=self.opds_search_url; params={"query": query}
        response=None
        while page_index <= page:
            endpoint = _safe_url(current_url)
            _LOG.info("TRACE gutenberg search page begin endpoint=%s query_len=%s scope=%s field=%s requested_page=%s hop=%s",endpoint, len(query), scope, field, page, page_index)
            try:
                # The former Client.get() buffered the entire upstream
                # response before any XML safety check. Bound raw bytes while
                # the socket is open; refuse compression rather than letting
                # httpx inflate an unbounded chunk before our byte budget.
                with self.client.stream(
                    "GET", current_url, params=params,
                    headers={"Accept-Encoding": "identity"},
                ) as response:
                    response.raise_for_status()
                    encoding = response.headers.get("content-encoding", "").strip().casefold()
                    if encoding not in {"", "identity"}:
                        raise _unavailable_opds_xml() from None
                    declared = response.headers.get("content-length", "")
                    if (len(declared) <= 9 and declared.isascii() and declared.isdecimal()
                            and int(declared) > MAX_SEARCH_OPDS_BYTES):
                        raise _unavailable_opds_xml() from None
                    chunks = []
                    total = 0
                    for chunk in response.iter_raw(chunk_size=65536):
                        total += len(chunk)
                        if total > MAX_SEARCH_OPDS_BYTES:
                            raise _unavailable_opds_xml() from None
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    response_url = str(response.url)
            except httpx.HTTPError as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000); status = getattr(getattr(exc, "response", None), "status_code", None)
                self._diagnostic("GUTENBERG", "OPDS search", "error", duration_ms=elapsed_ms,detail={"endpoint": endpoint, "status": status, "error": type(exc).__name__, "requested_page": page})
                # Typed and stable public status: no upstream body, URI or
                # httpx request representation is allowed in this exception.
                code = (
                    "auth_required" if status == 401 else
                    "access_denied" if status == 403 else
                    "rate_limited" if status == 429 else
                    "service_unavailable"
                )
                failure = ProviderError(
                    f"Project Gutenberg : recherche OPDS indisponible ({code})"
                )
                # The public Core fallback accepts only a built-in, exact
                # machine code; it must never infer an outage from an HTTP
                # 401/403, generic 400, upstream text or exception repr.
                if status == 401:
                    failure.code = "search_authentication_required"
                elif status == 403:
                    failure.code = "search_access_denied"
                elif (status is None or type(status) is int
                      and (status in {408, 429} or status >= 500)):
                    failure.code = "search_temporarily_unavailable"
                    failure.retryable = False  # No second equivalent GET.
                raise failure from None
            self._remember_page(rows_cache,page_index,response_url)
            next_url=self._next_page_url(content,response_url)
            if next_url: self._remember_page(rows_cache,page_index+1,next_url)
            if page_index == page: break
            if not next_url:
                # This proves only that the *requested OPDS page* does not
                # exist. For a specialized field, earlier pages were never
                # exhaustively searched for a matching author/subject/etc.
                if field not in ("all", ""):
                    self.last_search_warnings.append(
                        "partial_results : filtre local limité à la page OPDS demandée"
                    )
                self._diagnostic("GUTENBERG", "OPDS pagination", "ok",detail={"requested_page": page, "last_available_page": page_index}, level="detailed")
                return []
            current_url=next_url; params=None; page_index+=1
        assert response is not None
        parsed = self._parse_feed(content, response_url); rows = self._field_filter(parsed, filter_query, field); duration_ms = int((time.perf_counter() - started) * 1000)
        if field not in ("all", ""):
            # Filters are applied *after* obtaining one requested OPDS page.
            # Zero rows here are not evidence that the entire corpus lacks
            # a matching author, subject, bookshelf or language.
            self.last_search_warnings.append(
                "partial_results : filtre local limité à la page OPDS demandée"
            )
        self._diagnostic("GUTENBERG", "OPDS search", "ok", duration_ms=duration_ms,detail={"results": len(rows), "parsed_results": len(parsed), "query_len": len(query), "field": field, "page": page},level="detailed")
        return rows

    def _epub_candidates(self, content: bytes, base_url: str) -> list[tuple[str, str]]:
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            raise ProviderError("Project Gutenberg OPDS détail : XML invalide (invalid_opds_feed)") from None
        rows: list[tuple[str, str]] = []
        for link in root.iter():
            if str(link.tag).split("}")[-1] != "link":
                continue
            href = html.unescape(str(link.attrib.get("href") or "").strip())
            if not href:
                continue
            href = urljoin(base_url, href)
            rel = str(link.attrib.get("rel") or "").strip().casefold()
            typ = str(link.attrib.get("type") or "").split(";", 1)[0].strip().lower()
            label = str(link.attrib.get("title") or "")
            href_lower = href.lower()
            is_epub_mime = typ in {"application/epub+zip", "application/epub"}
            is_epub_href = bool(re.search(r"\.epub(?:3)?(?:[./?]|$)", href_lower))
            if rel.startswith(_EPUB_REL) and (is_epub_mime or is_epub_href):
                rows.append((href, label))
        return rows

    def _resolve_epub_url(self, item: SearchResult) -> str:
        cached = str(item.metadata.get("resolved_epub_url") or "").strip()
        if cached:
            return cached
        direct = str(item.download_url or "").strip()
        if direct:
            return direct
        detail_url = str(item.metadata.get("opds_detail_url") or "").strip()
        if not detail_url:
            raise ProviderError("Project Gutenberg : feed OPDS détaillé absent")
        self._require_contact()
        started = time.perf_counter()
        _LOG.info(
            "TRACE gutenberg detail begin record_id=%s url=%s",
            item.record_id, _safe_url(detail_url),
        )
        try:
            content, response_url = self._detail_feed(detail_url)
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            code = (
                "auth_required" if status == 401 else
                "access_denied" if status == 403 else
                "rate_limited" if status == 429 else
                "service_unavailable"
            )
            _LOG.warning(
                "TRACE gutenberg detail failed status=%s reason=%s dt_ms=%s",
                status if type(status) is int else None, code,
                int((time.perf_counter() - started) * 1000),
            )
            raise ProviderError(f"Project Gutenberg : détail OPDS indisponible ({code})") from None
        candidates = self._epub_candidates(content, response_url)
        if not candidates:
            _LOG.warning(
                "TRACE gutenberg detail no-epub record_id=%s bytes=%s dt_ms=%s",
                item.record_id, len(content), int((time.perf_counter() - started) * 1000),
            )
            raise ProviderError("Project Gutenberg : aucun EPUB dans le feed détaillé")

        def rank(value: tuple[str, str]) -> tuple[int, int]:
            href, label = value
            text = (href + " " + label).casefold()
            with_images = "images" in text and "noimages" not in text and "no images" not in text
            epub3 = ".epub3" in text or "epub3" in text
            if self.prefer_images:
                return (2 if with_images else 0, 1 if epub3 else 0)
            return (2 if not with_images else 0, 1 if epub3 else 0)

        resolved, _label = max(candidates, key=rank)
        item.metadata["resolved_epub_url"] = resolved
        item.download_url = resolved
        _LOG.info(
            "TRACE gutenberg detail resolved record_id=%s candidates=%s url=%s dt_ms=%s",
            item.record_id, len(candidates), _safe_url(resolved), int((time.perf_counter() - started) * 1000),
        )
        return resolved

    def holdings(self, item: SearchResult, search_terms: str = "") -> ItemDetail:
        thumbnail=item.thumbnail_url
        detail_url=str(item.metadata.get("opds_detail_url") or "").strip()
        if not thumbnail and detail_url:
            try:
                content,base_url=self._detail_feed(detail_url); thumbnail=self._detail_cover(content,base_url)
                if thumbnail: item.thumbnail_url=thumbnail
            except httpx.HTTPError:
                pass
        return ItemDetail(
            record_id=item.record_id,
            docbase=item.docbase,
            title=item.title,
            author=item.author,
            type=item.type,
            year=item.published_at,
            thumbnail_url=thumbnail,
            availability_status="available",
            availability_label="Téléchargement libre",
            holdings=[],
            source_provider=self.provider_id,
            source_credential=self.credential_id,
        )

    def acquisition_offers(self, item: SearchResult) -> list[AcquisitionOffer]:
        url = self._resolve_epub_url(item)
        return [AcquisitionOffer(
            offer_id=f"gutenberg:{item.record_id}",
            media_type="ebook",
            format="epub",
            drm="none",
            source_kind="http",
            source={
                "url": url,
                "headers": {
                    "User-Agent": self.user_agent,
                    "Accept": "application/epub+zip, application/octet-stream;q=0.5, */*;q=0.2",
                },
                "max_bytes": self.max_download_bytes,
                "allowed_content_types": (
                    "application/epub+zip",
                    "application/octet-stream",
                    "application/zip",
                ),
                "expected_format": "epub",
                "overwrite": True,
            },
            priority=20,
            metadata={"rights": item.metadata.get("rights"), "provider": "gutenberg"},
        )]

    def download_direct(self, item: SearchResult, target_dir: Path) -> tuple[Path, str]:
        self._require_contact()
        if self.transport is None:
            raise ProviderConfigurationError("Project Gutenberg : transport.http indisponible")
        offer = self.acquisition_offers(item)[0]
        url = str(offer.source.get("url") or "")
        started = time.perf_counter()
        _LOG.info(
            "TRACE gutenberg download begin record_id=%s url=%s",
            item.record_id, _safe_url(url),
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self._safe_name(item.title)[:145]}_{self._safe_name(item.record_id)[:20]}.epub"
        path = target_dir / filename
        try:
            path = Path(self.transport.fetch(dict(offer.source), path))
        except Exception as exc:
            path.unlink(missing_ok=True)
            _LOG.warning(
                "TRACE gutenberg download failed record_id=%s exception=%s dt_ms=%s error=%s",
                item.record_id, type(exc).__name__, int((time.perf_counter() - started) * 1000), exc,
            )
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError(f"Project Gutenberg : téléchargement EPUB indisponible ({exc})") from exc
        duration_ms = int((time.perf_counter() - started) * 1000)
        size = path.stat().st_size
        _LOG.info(
            "TRACE gutenberg download end record_id=%s path=%s bytes=%s dt_ms=%s",
            item.record_id, path.name, size, duration_ms,
        )
        self._diagnostic(
            "GUTENBERG", "EPUB download", "ok", duration_ms=duration_ms,
            detail={"bytes": size, "record_id": item.record_id, "transport": "transport.http"},
            level="detailed",
        )
        return path, url


class GutenbergPlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def __init__(self) -> None:
        self._plugin_config: dict[str, Any] = {}
        self._transport = None

    def activate(self, context: Any) -> "GutenbergPlugin":
        self._plugin_config = dict(getattr(context, "config", {}) or {})
        self._transport = context.service("transport.http")
        if self._transport is None:
            raise ProviderConfigurationError("Project Gutenberg : transport.http requis")
        return self

    def create_session(
        self,
        *,
        credential_id: str,
        label: str,
        credentials: dict[str, Any],
        options: dict[str, Any],
        config: Any,
        storage: Any,
        diagnostics: Any,
    ) -> ProviderSession:
        merged_options = dict(self._plugin_config)
        merged_options.update(options or {})
        return GutenbergSession(
            credential_id=credential_id,
            label=label,
            credentials=credentials,
            options=merged_options,
            config=config,
            diagnostics=diagnostics,
            transport=self._transport,
        )


PLUGIN = GutenbergPlugin()
