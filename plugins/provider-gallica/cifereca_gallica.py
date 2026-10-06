from __future__ import annotations
from cifereca_gallica_description import description_text, description_element

import html
from cifereca_gallica_audio import download_bundle
from concurrent.futures import ThreadPoolExecutor
import io
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import httpx

from cifereca.plugin_api import Account, ItemDetail, SearchResult, AcquisitionOffer
from cifereca.plugin_api import (
    ProviderCapabilities,
    ProviderDescriptor,
    ProviderError,
    ProviderPlugin,
    ProviderSession, normalize_language_tag,
)

ATOM = "http://www.w3.org/2005/Atom"
OPENSEARCH = "http://a9.com/-/spec/opensearch/1.1/"
DCTERMS = "http://purl.org/dc/terms/"
DC = "http://purl.org/dc/elements/1.1/"
EPUB_SEARCH = "https://gallica.bnf.fr/services/engine/search/opds"
EBOOK_FORMAT_CQL = '((dc.formatspecific all "epub") or (dc.formatspecific all "pdf"))'


def _cql_literal(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _public_url(value: str) -> str:
    """Upgrade historical Gallica HTTP links without rewriting other hosts."""
    parsed = urlsplit(value)
    if parsed.hostname == "gallica.bnf.fr" and parsed.scheme == "http":
        return urlunsplit(parsed._replace(scheme="https"))
    return value

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._()\- àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]+")
_ARK_RE = re.compile(r"(?:https?://gallica\.bnf\.fr/)?(ark:/12148/[^/?#\s]+)", re.I)
_AUDIO_MIME_EXT = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/flac": ".flac",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}

DESCRIPTOR = ProviderDescriptor(
    id="gallica",
    display_name="Gallica",
    description="Gallica (BnF) : livres EPUB via OPDS et documents sonores publics via SRU/OAI.",
    state="ready",
    capabilities=ProviderCapabilities(search=True, holdings=True, direct_download=True),
    homepage="https://gallica.bnf.fr",
    notes=(
        "Source publique sans authentification.",
        "Les EPUB utilisent OPDS ; les documents sonores utilisent le SRU Gallica puis OAIRecord pour les pistes.",
    ),
)


class GallicaSession(ProviderSession):
    """Session anonyme Gallica, EPUB + documents sonores du domaine public."""

    def __init__(self, *, config: Any, diagnostics: Any, **kwargs: Any) -> None:
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        self.diagnostics = diagnostics
        timeout = float(self.options.get("timeout_seconds") or self.options.get("request_timeout_seconds") or (config.request_timeout_seconds("gallica", self.credential_id) if hasattr(config, "request_timeout_seconds") else config.get("request_timeout_seconds", 25)))
        self.epub_search_url = str(self.options.get("epub_search_url") or EPUB_SEARCH)
        self.max_download_bytes = int(self.options.get("max_download_bytes") or 100 * 1024 * 1024)
        self.opds_url = str(self.options.get("opds_url") or "https://gallica.bnf.fr/opds").rstrip("/")
        self.sru_url = str(self.options.get("sru_url") or "https://gallica.bnf.fr/services/engine/search/sru")
        self.oai_url = str(self.options.get("oai_url") or "https://gallica.bnf.fr/services/OAIRecord")
        self.include_audio = bool(self.options.get("include_audio", True))
        self.max_audio_track_bytes = int(self.options.get('max_audio_track_bytes', 100 * 1024 * 1024))
        self.max_audio_bundle_bytes = int(self.options.get('max_audio_bundle_bytes', 512 * 1024 * 1024))
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            http2=False,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
                ),
                "Accept": "application/atom+xml, application/xml;q=0.9, application/epub+zip;q=0.8, audio/*;q=0.8, */*;q=0.5",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
                "Referer": "https://gallica.bnf.fr/",
            },
        )
        self._search_template: str | None = None
        self.last_search_warnings: list[str] = []

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
    def _normalize_languages(values):
        return list(dict.fromkeys(
            canonical for value in values
            if (canonical := normalize_language_tag(value)) is not None
        ))

    @staticmethod
    def _text(node: ET.Element | None) -> str | None:
        if node is None:
            return None
        value = "".join(node.itertext()).strip()
        return html.unescape(value) if value else None

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()

    @staticmethod
    def _attr_href(node: ET.Element | None, base_url: str) -> str | None:
        if node is None:
            return None
        href = html.unescape(str(node.attrib.get("href") or "").strip())
        return _public_url(urljoin(base_url, href)) if href else None

    @classmethod
    def _extract_ark(cls, *values: str | None) -> str | None:
        for value in values:
            if not value:
                continue
            m = _ARK_RE.search(html.unescape(str(value)))
            if m:
                return m.group(1).rstrip(".")
        return None

    @staticmethod
    def _first_text(entry: ET.Element, paths: tuple[str, ...]) -> str | None:
        for path in paths:
            node = entry.find(path)
            value = GallicaSession._text(node)
            if value:
                return value
        return None

    @staticmethod
    def _content_from_acquisition(mime: str, href: str) -> tuple[str, str]:
        mime = (mime or "").split(";", 1)[0].strip().lower()
        low = href.lower()
        if mime.startswith("audio/") or any(low.endswith(ext) for ext in (".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav")):
            ext = _AUDIO_MIME_EXT.get(mime) or Path(low).suffix.lstrip(".").upper()
            fmt = (ext.lstrip(".").upper() if isinstance(ext, str) else "AUDIO") or "AUDIO"
            return "audiobook", fmt
        if mime == "application/pdf" or low.endswith(".pdf"):
            return "ebook", "PDF"
        if mime == "application/epub+zip" or low.endswith(".epub"):
            return "ebook", "EPUB"
        return "ebook", mime or "Numérique"

    def _parse_feed(self, content: bytes, base_url: str) -> list[SearchResult]:
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise ProviderError(f"Gallica OPDS : XML invalide ({exc})") from exc

        if root.tag != f"{{{ATOM}}}feed":
            raise ProviderError("Gallica OPDS : flux Atom attendu")

        rows: list[SearchResult] = []
        for entry in root.findall(f"{{{ATOM}}}entry"):
            title = self._text(entry.find(f"{{{ATOM}}}title")) or "Sans titre"
            author_names = [self._text(author.find(f"{{{ATOM}}}name")) for author in entry.findall(f"{{{ATOM}}}author")]
            author = ", ".join(x for x in author_names if x) or None
            summary = description_element(entry.find(f"{{{ATOM}}}summary")) or description_element(entry.find(f"{{{ATOM}}}content"))
            entry_id = self._text(entry.find(f"{{{ATOM}}}id"))

            acquisitions: list[tuple[str, str]] = []
            thumbnail: str | None = None
            image: str | None = None
            friendly: str | None = None
            fallback_link: str | None = None
            for link in entry.findall(f"{{{ATOM}}}link"):
                rel = str(link.attrib.get("rel") or "alternate")
                typ = str(link.attrib.get("type") or "")
                href = self._attr_href(link, base_url)
                if not href:
                    continue
                fallback_link = fallback_link or href
                if rel in {"http://opds-spec.org/acquisition", "http://opds-spec.org/acquisition/open-access"} and urlsplit(href).scheme in {"https", "http"}:
                    acquisitions.append((href, typ))
                elif rel in {"http://opds-spec.org/image/thumbnail", "http://opds-spec.org/thumbnail"}:
                    thumbnail = href
                elif rel in {"http://opds-spec.org/image", "http://opds-spec.org/cover"}:
                    image = href
                elif rel in {"alternate", "related", "self"} and typ in {"text/html", "application/xhtml+xml", ""}:
                    friendly = friendly or href

            # Prefer EPUB for a textual OPDS entry, otherwise preserve the first
            # acquisition link (including audio MIME types if Gallica exposes one).
            acquisition: str | None = None
            acquisition_type = ""
            for href, typ in acquisitions:
                if typ == "application/epub+zip" or href.lower().endswith(".epub"):
                    acquisition, acquisition_type = href, typ
                    break
                if acquisition is None:
                    acquisition, acquisition_type = href, typ

            ark = self._extract_ark(entry_id, friendly, acquisition, fallback_link)
            if not ark or not acquisition:
                continue

            friendly = friendly or f"https://gallica.bnf.fr/{ark}"
            publisher = self._first_text(entry, (f"{{{DCTERMS}}}publisher", f"{{{DC}}}publisher"))
            published = self._first_text(entry, (f"{{{DCTERMS}}}issued", f"{{{DCTERMS}}}date", f"{{{DC}}}date", f"{{{ATOM}}}updated"))
            content_type, fmt = self._content_from_acquisition(acquisition_type, acquisition)
            stable_id = ark.split("/")[-1]
            rows.append(
                SearchResult(
                    id=f"gallica:{stable_id}:{content_type}",
                    record_id=ark,
                    docbase="GALLICA",
                    medium="digital",
                    title=title,
                    author=author,
                    type="Livre audio" if content_type == "audiobook" else "Livre numérique",
                    format=fmt,
                    publisher=publisher,
                    published_at=published,
                    description=summary,
                    thumbnail_url=thumbnail or image,
                    friendly_url=friendly,
                    scenario="OPDS",
                    source_provider=self.provider_id,
                    source_credential=self.credential_id,
                    download_url=acquisition,
                    content_type=content_type,
                    downloadable=True,
                    metadata={
                        "format_evidence": "opds-acquisition", "mime_type": acquisition_type,
                        "rights": self._first_text(entry, (f"{{{DCTERMS}}}rights", f"{{{DC}}}rights")),
                        "languages": self._normalize_languages([self._text(n) for n in entry.findall(f"{{{DCTERMS}}}language") + entry.findall(f"{{{DC}}}language") if self._text(n)]),
                        "subjects": [n.attrib.get("label") or n.attrib.get("term") for n in entry.findall(f"{{{ATOM}}}category")],
                    },
                )
            )
        return rows

    def _discover_search_template(self) -> str:
        if self._search_template:
            return self._search_template
        response = self.client.get(self.opds_url)
        response.raise_for_status()
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise ProviderError(f"Gallica OPDS : racine XML invalide ({exc})") from exc
        search_link = next((link for link in root.findall(f"{{{ATOM}}}link") if str(link.attrib.get("rel") or "") == "search"), None)
        if search_link is None:
            self._search_template = self._fallback_search_template()
            return self._search_template
        href = self._attr_href(search_link, str(response.url))
        if not href:
            self._search_template = self._fallback_search_template()
            return self._search_template
        typ = str(search_link.attrib.get("type") or "")
        if "opensearchdescription" not in typ:
            self._search_template = href if "{searchTerms" in href else self._fallback_search_template()
            return self._search_template
        descriptor = self.client.get(href)
        descriptor.raise_for_status()
        try:
            os_root = ET.fromstring(descriptor.content)
        except ET.ParseError as exc:
            raise ProviderError(f"Gallica OpenSearch : XML invalide ({exc})") from exc
        candidates = list(os_root.findall(f"{{{OPENSEARCH}}}Url")) or list(os_root.findall("Url"))
        chosen: str | None = None
        for node in candidates:
            template = html.unescape(str(node.attrib.get("template") or "").strip())
            typ = str(node.attrib.get("type") or "")
            if not template:
                continue
            if "atom" in typ or "opds" in typ:
                chosen = template
                break
            chosen = chosen or template
        if not chosen:
            raise ProviderError("Gallica OpenSearch : aucun template de recherche utilisable")
        self._search_template = urljoin(str(descriptor.url), chosen) if "{searchTerms" in chosen else self._fallback_search_template()
        return self._search_template

    def _fallback_search_template(self) -> str:
        # Documented SRU/OPDS endpoint; /opds?q=... is not a search API.
        return (self.epub_search_url + '?operation=searchRetrieve&version=1.2&query='
                + quote_plus('(gallica all "{searchTerms}") and ' + EBOOK_FORMAT_CQL)
                  .replace('%7BsearchTerms%7D', '{searchTerms}')
                + '&startRecord={startIndex}&maximumRecords=50')

    def _search_ebooks(self, query: str, page: int, field: str) -> list[SearchResult]:
        if field == "all":
            try:
                template = self._discover_search_template()
            except (httpx.HTTPError, ProviderError):
                template = self._fallback_search_template()
                self._search_template = template
            # Escape CQL quotes before percent encoding in a SRU template.
            rendered_query = query.replace('\\', '\\\\').replace('"', '\\"') if "query=" in template else query
            response = self.client.get(self._render_search_url(template, rendered_query, page))
        else:
            index = {"title": "dc.title", "author": "dc.creator", "subject": "dc.subject", "isbn": "dc.identifier"}.get(field)
            if index is None:
                raise ProviderError(f"Gallica : champ de recherche non pris en charge : {field}")
            response = self.client.get(self.epub_search_url, params={
                "operation": "searchRetrieve", "version": "1.2",
                "query": f'({index} all {_cql_literal(query)}) and {EBOOK_FORMAT_CQL}',
                "startRecord": str(max(0, page) * 50 + 1), "maximumRecords": "50",
            })
        response.raise_for_status()
        return self._parse_feed(response.content, str(response.url))

    @staticmethod
    def _render_search_url(template: str, query: str, page: int) -> str:
        replacements = {
            "searchTerms": quote_plus(query), "searchTerms?": quote_plus(query),
            "count": "50", "count?": "50",
            "startPage": str(max(1, page + 1)), "startPage?": str(max(1, page + 1)),
            "startIndex": str(max(1, page * 50 + 1)), "startIndex?": str(max(1, page * 50 + 1)),
        }
        out = template
        for key, value in replacements.items():
            out = out.replace("{" + key + "}", value)
        return re.sub(r"\{[^{}]+\?\}", "", out)

    def _audio_rows_from_nodes(self, record_nodes: list[ET.Element]) -> list[SearchResult]:
        rows: list[SearchResult] = []
        for record_data in record_nodes:
            values: dict[str, list[str]] = {}
            for node in record_data.iter():
                key = self._local_name(node.tag)
                text = self._text(node)
                if text and key in {"title", "creator", "date", "identifier", "description", "publisher", "type", "format", "subject", "language"}:
                    values.setdefault(key, []).append(text)
            ark = self._extract_ark(*(values.get("identifier") or []))
            if not ark:
                continue
            types = " ".join(values.get("type") or []).casefold()
            formats = " ".join(values.get("format") or []).casefold()
            if not any(x in (types + " " + formats) for x in ("sound", "audio", "sonore", "enregistrement")):
                # Query already asks for dc.type=audio, but keep a defensive gate.
                continue
            stable = ark.split("/")[-1]
            title = (values.get("title") or ["Sans titre"])[0]
            author = ", ".join(values.get("creator") or []) or None
            rows.append(SearchResult(
                id=f"gallica:{stable}:audiobook",
                record_id=ark,
                docbase="GALLICA",
                medium="digital",
                title=title,
                author=author,
                type="Livre audio / document sonore",
                format=None,
                publisher=(values.get("publisher") or [None])[0],
                published_at=(values.get("date") or [None])[0],
                description=description_text((values.get("description") or [None])[0]),
                thumbnail_url=f"https://gallica.bnf.fr/{ark}/thumbnail",
                friendly_url=f"https://gallica.bnf.fr/{ark}",
                scenario="SRU-AUDIO",
                source_provider=self.provider_id,
                source_credential=self.credential_id,
                # For an external override we deliberately pass the stable
                # Gallica document URL. Built-in audio download resolves tracks
                # through OAIRecord.
                download_url=f"https://gallica.bnf.fr/{ark}",
                content_type="audiobook",
                metadata={"languages": self._normalize_languages(values.get("language", []))},
            ))
        return rows

    def _recover_sru_audio_records(self, content: bytes) -> tuple[list[SearchResult], int, int]:
        """Recover valid SRU records when one malformed record breaks the XML feed.

        Gallica occasionally returns a globally invalid SRU document because a
        single metadata record contains malformed embedded markup.  ElementTree
        is intentionally strict, so parse each ``recordData`` fragment in
        isolation and discard only the broken records instead of losing the
        complete audio result set.
        """
        text = content.decode("utf-8", errors="replace")
        fragments = re.findall(
            r"<(?:[A-Za-z_][\w.-]*:)?recordData\b[^>]*>(.*?)</(?:[A-Za-z_][\w.-]*:)?recordData\s*>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        nodes: list[ET.Element] = []
        broken = 0
        wrapper_start = (
            '<root xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" '
            'xmlns:srw="http://www.loc.gov/zing/srw/">'
        )
        for fragment in fragments:
            try:
                nodes.append(ET.fromstring((wrapper_start + fragment + "</root>").encode("utf-8")))
            except ET.ParseError:
                broken += 1
        return self._audio_rows_from_nodes(nodes), len(fragments), broken

    def _parse_sru_audio(self, content: bytes) -> list[SearchResult]:
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            rows, fragments, broken = self._recover_sru_audio_records(content)
            if rows:
                self.diagnostics.add(
                    "GALLICA", "SRU audio XML recovery", "ok",
                    detail={
                        "records_found": fragments,
                        "records_skipped": broken,
                        "records_recovered": len(rows),
                        "parse_error": str(exc)[:180],
                    },
                    level="detailed",
                )
                return rows
            self.diagnostics.add(
                "GALLICA", "SRU audio XML parse", "error",
                detail={"parse_error": str(exc)[:240], "records_found": fragments, "records_skipped": broken},
            )
            raise ProviderError("réponse audio temporairement inexploitable") from exc
        record_nodes = [node for node in root.iter() if self._local_name(node.tag) == "recorddata"]
        return self._audio_rows_from_nodes(record_nodes)

    def _search_audio(self, query: str, page: int, field: str = "all") -> list[SearchResult]:
        index = {"all": "gallica", "title": "dc.title", "author": "dc.creator", "subject": "dc.subject", "isbn": "dc.identifier"}.get(field)
        if index is None:
            raise ProviderError(f"Gallica : champ de recherche non pris en charge : {field}")
        cql = f'({index} all {_cql_literal(query)}) and (dc.type any "audio")'
        response = self.client.get(self.sru_url, params={
            "operation": "searchRetrieve",
            "version": "1.2",
            "query": cql,
            "startRecord": str(max(1, page * 50 + 1)),
            "maximumRecords": "50",
        })
        response.raise_for_status()
        try:
            return self._parse_sru_audio(response.content)
        except ProviderError:
            self.diagnostics.add("GALLICA", "audio response metadata", "error", detail={
                "http_status": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "bytes": len(response.content),
            })
            raise

    def search(self, query: str, scope: str = "all", page: int = 0, field: str = "all", content_types: tuple[str, ...] = ()) -> list[SearchResult]:
        self.last_search_warnings = []
        if scope == "physical":
            return []
        query = str(query or "").strip()
        if len(query) < 2:
            return []
        requested = {str(value).lower() for value in content_types}
        # Core passes file formats; legacy media categories remain supported.
        ebooks = not requested or bool(requested & {"ebook", "epub", "pdf", "kepub"})
        audio = self.include_audio and (not requested or bool(requested & {"audiobook", "mp3", "m4a", "ogg", "opus", "flac", "wav"}))
        branches = []
        if ebooks:
            branches.append(("OPDS", "catalogue livres numériques temporairement indisponible", self._search_ebooks))
        if audio:
            branches.append(("audio", "recherche audio temporairement indisponible", self._search_audio))
        rows: list[SearchResult] = []
        errors: list[str] = []
        causes = []
        # Independent services run concurrently; an audio timeout does not add
        # another complete timeout after the EPUB branch has finished.
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="gallica-search") as pool:
            tasks = [(label, message, pool.submit(fn, query, page, field)) for label, message, fn in branches]
            for label, message, task in tasks:
                try:
                    rows.extend(task.result())
                except Exception as exc:
                    errors.append(message)
                    causes.append(exc)
                    self.diagnostics.add("GALLICA", label + " search branch", "error",
                        detail={"error": type(exc).__name__, "message": str(exc)[:240]})
        self.last_search_warnings = ["Gallica : " + message for message in errors]
        rows = list({item.id: item for item in rows}.values())
        if not rows and errors:
            error = ProviderError("Gallica : " + " ; ".join(errors))
            error.code = "search_temporarily_unavailable"
            error.retryable = True
            raise error from causes[0]
        self.diagnostics.add("GALLICA", "federated public search", "ok", detail={
            "results": len(rows), "ebooks": sum(x.content_type == "ebook" for x in rows),
            "audiobooks": sum(x.content_type == "audiobook" for x in rows),
            "partial_errors": len(errors), "q_len": len(query), "page": page,
        }, level="detailed")
        return rows

    def holdings(self, item: SearchResult, search_terms: str = "") -> ItemDetail:
        return ItemDetail(
            record_id=item.record_id, docbase=item.docbase, title=item.title,
            author=item.author, type=item.type, year=item.published_at,
            thumbnail_url=item.thumbnail_url, availability_status="available",
            availability_label="Téléchargement libre", holdings=[],
            source_provider=self.provider_id, source_credential=self.credential_id,
        )

    @staticmethod
    def _safe_name(value: str, fallback: str = "gallica") -> str:
        return _SAFE_FILENAME.sub("_", value or fallback).strip(" ._") or fallback

    def _download_ebook(self, item: SearchResult, target_dir: Path) -> tuple[Path, str]:
        url = str(item.download_url or "").strip()
        if not url:
            raise ProviderError("Gallica : aucun lien de téléchargement attesté")
        with self.client.stream("GET", _public_url(url), headers={"Accept": "application/epub+zip, application/pdf;q=0.9, */*;q=0.2"}) as response:
            response.raise_for_status()
            content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].lower()
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > self.max_download_bytes:
                    raise ProviderError("Gallica : téléchargement trop volumineux")
                chunks.append(chunk)
            content = b"".join(chunks)
            final_url = str(response.url)
        fmt = str(item.format or "").upper()
        if fmt == "EPUB":
            self._validate_epub(content)
        elif fmt == "PDF":
            if not content.startswith(b"%PDF-"):
                raise ProviderError("Gallica : PDF invalide")
        else:
            raise ProviderError("Gallica : format numérique non pris en charge")
        if not content or "text/html" in content_type:
            raise ProviderError(f"Gallica : téléchargement numérique invalide (content-type={content_type or 'inconnu'})")
        ext = ".pdf" if fmt == "PDF" else ".epub"
        target_dir.mkdir(parents=True, exist_ok=True)
        stable = self._safe_name(item.record_id.split("/")[-1], "")
        path = target_dir / (self._safe_name(item.title)[:145] + (f"_{stable[:24]}" if stable else "") + ext)
        # Validate first; publish without overwriting a pre-existing artifact.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target_dir, suffix=".part", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content)
            candidate = path
            serial = 1
            while candidate.exists():
                if candidate.read_bytes() == content:
                    return candidate, final_url
                candidate = path.with_name(f"{path.stem}_{serial}{path.suffix}")
                serial += 1
            # Hard link is atomic and refuses a racing collision.
            candidate.hardlink_to(temporary)
            path = candidate
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.diagnostics.add("GALLICA", "ebook download", "ok", detail={"bytes": len(content), "format": fmt or ext[1:].upper()}, level="detailed")
        return path, final_url

    @staticmethod
    def _validate_epub(content: bytes) -> None:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                infos = archive.infolist()
                if len(infos) > 10000 or sum(n.file_size for n in infos) > 256 * 1024 * 1024:
                    raise ValueError("archive excessive")
                if archive.read("mimetype") != b"application/epub+zip":
                    raise ValueError("mimetype")
                container = ET.fromstring(archive.read("META-INF/container.xml"))
                roots = container.findall(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
                if not roots:
                    raise ValueError("container")
                for root in roots:
                    package = ET.fromstring(archive.read(root.attrib["full-path"]))
                    if package.tag != "{http://www.idpf.org/2007/opf}package":
                        raise ValueError("package")
                if archive.testzip() is not None:
                    raise ValueError("CRC")
        except (ValueError, KeyError, zipfile.BadZipFile, ET.ParseError, RuntimeError, OSError, NotImplementedError) as exc:
            raise ProviderError("Gallica : EPUB invalide (archive, mimetype ou package)") from exc

    def _audio_tracks(self, ark: str) -> list[tuple[str, str]]:
        response = self.client.get(self.oai_url, params={"ark": ark})
        response.raise_for_status()
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise ProviderError(f"Gallica OAIRecord : XML invalide ({exc})") from exc
        tracks: list[tuple[str, str]] = []
        for media in (node for node in root.iter() if self._local_name(node.tag) == "media"):
            title = next((self._text(x) for x in media if self._local_name(x.tag) == "title" and self._text(x)), None) or "Plage"
            url = next((self._text(x) for x in media if self._local_name(x.tag) == "file" and self._text(x)), None)
            if url and url.startswith("http"):
                tracks.append((title, url))
        # Some payloads may omit <media>; accept standalone <file> as fallback.
        if not tracks:
            for node in root.iter():
                if self._local_name(node.tag) == "file":
                    url = self._text(node)
                    if url and url.startswith("http"):
                        tracks.append(("Plage", url))
        return tracks

    def _download_audio(self, item: SearchResult, target_dir: Path) -> tuple[Path, str]:
        return download_bundle(self, item, target_dir)

    def acquisition_offers(self, item: SearchResult) -> list[AcquisitionOffer]:
        # An ARK page identifies a bundle to resolve, not an HTTP media file.
        # API v1 has no bundle recipe; download_direct resolves OAI tracks.
        if item.content_type == 'audiobook':
            return []
        if not item.download_url:
            return []
        fmt = str(item.format or "").lower()
        return [AcquisitionOffer(offer_id=f"gallica:{item.record_id}", media_type=item.content_type or "ebook", format=fmt, drm="none", source_kind="http", source={"url": item.download_url}, priority=10)]

    def download_direct(self, item: SearchResult, target_dir: Path) -> tuple[Path, str]:
        if item.content_type == "audiobook":
            return self._download_audio(item, target_dir)
        return self._download_ebook(item, target_dir)


class GallicaPlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def activate(self, context):
        self._configuration = dict(context.config)
        return self
    @staticmethod
    def parse_feed(content: bytes, base_url: str) -> list[dict[str, object]]:
        root = ET.fromstring(content)
        out: list[dict[str, object]] = []
        for entry in root.findall(f"{{{ATOM}}}entry"):
            title = GallicaSession._text(entry.find(f"{{{ATOM}}}title")) or "Sans titre"
            authors = [GallicaSession._text(a.find(f"{{{ATOM}}}name")) or "" for a in entry.findall(f"{{{ATOM}}}author")]
            acquisition = None; acquisition_type = ""; thumbnail = None; friendly = None
            for link in entry.findall(f"{{{ATOM}}}link"):
                href = html.unescape(str(link.attrib.get("href") or "").strip())
                if not href: continue
                href = urljoin(base_url, href); rel = str(link.attrib.get("rel") or ""); typ = str(link.attrib.get("type") or "")
                href = _public_url(href)
                if rel in {"http://opds-spec.org/acquisition", "http://opds-spec.org/acquisition/open-access"} and urlsplit(href).scheme in {"https", "http"}:
                    if acquisition is None or typ == "application/epub+zip": acquisition, acquisition_type = href, typ
                elif "thumbnail" in rel: thumbnail = href
                elif rel in {"alternate", "related"} and not friendly: friendly = href
            if not acquisition: continue
            item_id = GallicaSession._text(entry.find(f"{{{ATOM}}}id")) or acquisition
            fmt = "EPUB" if acquisition_type == "application/epub+zip" or acquisition.lower().endswith(".epub") else acquisition_type
            out.append({"id":item_id,"title":title,"author":", ".join(x for x in authors if x),"format":fmt or "DIGITAL","download_url":acquisition,"thumbnail_url":thumbnail,"friendly_url":friendly,"provider_id":"gallica","drm":"none","source_kind":"http"})
        return out

    def create_session(
        self, *, credential_id: str, label: str, credentials: dict[str, Any],
        options: dict[str, Any], config: Any, storage: Any, diagnostics: Any,
    ) -> ProviderSession:
        return GallicaSession(
            credential_id=credential_id, label=label, credentials=credentials,
            options={**getattr(self, "_configuration", {}), **options}, config=config, diagnostics=diagnostics,
        )


PLUGIN = GallicaPlugin()
