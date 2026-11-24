from __future__ import annotations
import html
from cifereca_gallica_audio import download_bundle
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from dataclasses import replace
from collections import Counter
import re
from unicodedata import category
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import httpx
from cifereca.plugin_api import (
    HTTPX_TRANSPORT, MAX_XML_BYTES, CatalogueXMLParseError,
    parse_atom_catalogue, parse_bounded_catalogue_xml,
    catalogue_url_origin, resolve_catalogue_link,
)

from cifereca.plugin_api import (
    Account, ItemDetail, SearchResult, AcquisitionOffer,
    ProviderSearchResponse, PublicSearchNotice,
    BundleAcquisitionRecipe,
)
from cifereca.plugin_api import (
    AccountOptionField,
    ProviderCapabilities,
    ProviderSearchCapabilities,
    ProviderCapabilityError,
    ProviderDescriptor,
    ProviderError, Cancelled,
    ProviderPlugin,
    ProviderSession, normalize_language_tag, sanitize_filename_component, description_text, description_element,
)

ATOM = "http://www.w3.org/2005/Atom"
OPENSEARCH = "http://a9.com/-/spec/opensearch/1.1/"
DCTERMS = "http://purl.org/dc/terms/"
DC = "http://purl.org/dc/elements/1.1/"
EPUB_SEARCH = "https://gallica.bnf.fr/services/engine/search/opds"
MAX_REQUESTED_PAGES = 101  # Zero-based, bound both SRU and OPDS offsets.
EBOOK_FORMAT_CQL = '((dc.formatspecific all "epub") or (dc.formatspecific all "pdf"))'


def _cql_literal(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _publication_date(value: str | None) -> str | None:
    """Recognize a bibliographic ISO date, not an Atom entry timestamp."""
    if type(value) is not str:
        return None
    if re.fullmatch(r"[0-9]{4}", value):
        return value if 1 <= int(value) <= 9999 else None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
        year, month = map(int, value.split("-"))
        return value if 1 <= year <= 9999 and 1 <= month <= 12 else None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            date.fromisoformat(value)
        except ValueError:
            return None
        return value
    return None


def _opds_publication(entry: ET.Element) -> tuple[str | None, str | None]:
    # issued is the explicit publication assertion. Generic DC dates
    # belong to an unspecified event: they do not establish issuance.
    # Multiple contradictory or malformed assertions stay unknown.
    nodes = entry.findall(f"{{{DCTERMS}}}issued")
    if not nodes:
        return None, None
    values = {_publication_date(GallicaSession._text(node)) for node in nodes}
    if None in values or len(values) != 1:
        return None, None
    return next(iter(values)), "opds.dcterms.issued"


def _opds_language_evidence(entry: ET.Element) -> dict:
    """Attest the complete selected-entry language declaration, or nothing."""
    terms = entry.findall(f"{{{DCTERMS}}}language")
    dc = entry.findall(f"{{{DC}}}language")
    nodes = terms + dc
    if not 1 <= len(nodes) <= 16:
        return {}
    raw = [(node.text or "").strip() for node in nodes]
    if any(len(node) or node.attrib or len(tag) > 128
           or re.fullmatch(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{1,8}){0,8}", tag) is None
           or normalize_language_tag(tag) is None
           for node, tag in zip(nodes, raw)):
        return {}
    field = ("dcterms.language_and_dc.language" if terms and dc else
             "dcterms.language" if terms else "dc.language")
    return {"bibliography_evidence": {"languages": {
        "raw_tags": raw, "source": "gallica.opds", "source_field": field,
        "scope": "OPDS", "confidence": "explicit_record_field",
        "freshness": "search_response",
    }}}


def _opds_explicit_subjects(entry: ET.Element) -> list[str]:
    """Only literal, direct Dublin Core subject assertions of this entry."""
    nodes = entry.findall(f"{{{DCTERMS}}}subject") + entry.findall(f"{{{DC}}}subject")
    if not 1 <= len(nodes) <= 32:
        return []
    labels = []
    for node in nodes:
        value = (node.text or "").strip()
        if (len(node) or node.attrib or not 0 < len(value) <= 240
                or not value.isprintable()
                or value.startswith(("/", "\\", "~/"))
                or re.search(
                    r"(?i)https?://|file:|[^\s@]+@[^\s@]+\.[^\s@]+|"
                    r"(?:token|auth|cookie|signature|password|secret)\s*=|"
                    r"(?:bearer|basic)\s+\S+", value,
                )):
            # Reject the unqualified field, not an independent title or ARK.
            return []
        if value not in labels:
            labels.append(value)
    return labels


def _public_url(value: str) -> str:
    """Upgrade historical Gallica HTTP links without rewriting other hosts."""
    parsed = urlsplit(value)
    if parsed.hostname == "gallica.bnf.fr" and parsed.scheme == "http":
        return urlunsplit(parsed._replace(scheme="https"))
    return value

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
    search_capabilities=ProviderSearchCapabilities(
        fields=("all", "title", "author", "subject", "isbn"),
        scopes=("all", "digital"),
        pagination="page",
        page_size=50,
        max_pages=MAX_REQUESTED_PAGES,
        notes=(
            "all utilise OpenSearch/OPDS pour les livres EPUB ; les champs title/author/subject/isbn utilisent les index SRU EPUB.",
            "Audio SRU indépendant, optionnel ; les défaillances de branche donnent des résultats partiels explicitement signalés.",
            "Les résultats sont paginés par branche, 50 documents par requête ; aucune recherche physique ni collection privée.",
            "Un filtre de format sans branche EPUB/audio activée ne constitue pas un zéro de catalogue ; avertissement explicite, aucun HTTP.",
        ),
    ),
    homepage="https://gallica.bnf.fr",
    account_options=(AccountOptionField("opds_url", "URL du catalogue OPDS",
                                       default="https://gallica.bnf.fr/opds", value_type="url", url_schemes=("http", "https")),),
    account_note=(
        "Accès public",
        "Aucun identifiant requis · EPUB + documents sonores publics Gallica",
    ),
    notes=(
        "Source publique sans authentification.",
        "Les EPUB utilisent OPDS ; les documents sonores utilisent le SRU Gallica puis OAIRecord pour les pistes.",
    ),
)


def _gallica_audio_recipe(item: SearchResult) -> BundleAcquisitionRecipe | None:
    ark = GallicaSession._extract_ark(item.record_id, item.friendly_url, item.download_url)
    if not ark:
        return None
    resolver_url = _public_url(str(item.friendly_url or f"https://gallica.bnf.fr/{ark}"))
    return BundleAcquisitionRecipe(
        recipe_id=f"gallica:{ark}:audio",
        resolver_url=resolver_url,
        tracks=(),
        metadata={"provider_id": "gallica", "ark": ark},
    ).validate()


class _UnsafeOPDS(ProviderError):
    """Rejected payload or navigation: never authorize a fallback GET."""


class _UnsafeSRU(ProviderError):
    """A rejected audio catalogue does not authorize an outage fallback."""


class _PartialOPDSRows(list):
    """Private branch marker: some entries did not qualify for projection."""


class _RecoveredAudioRows(list):
    """Private branch result marker: recovery is necessarily partial."""


class GallicaSession(ProviderSession):
    """Session anonyme Gallica, EPUB + documents sonores du domaine public."""

    def __init__(self, *, config: Any, diagnostics: Any, transport: Any = None, **kwargs: Any) -> None:
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        self.diagnostics = diagnostics
        self.transport = transport
        timeout = float(self.options.get("timeout_seconds") or self.options.get("request_timeout_seconds") or (config.request_timeout_seconds("gallica", self.credential_id) if hasattr(config, "request_timeout_seconds") else config.get("request_timeout_seconds", 25)))
        self.epub_search_url = str(self.options.get("epub_search_url") or EPUB_SEARCH)
        self.max_download_bytes = int(self.options.get("max_download_bytes") or 100 * 1024 * 1024)
        self.opds_url = str(self.options.get("opds_url") or "https://gallica.bnf.fr/opds").rstrip("/")
        self.sru_url = str(self.options.get("sru_url") or "https://gallica.bnf.fr/services/engine/search/sru")
        self.oai_url = str(self.options.get("oai_url") or "https://gallica.bnf.fr/services/OAIRecord")
        self.include_audio = bool(self.options.get("include_audio", True))
        self.max_audio_track_bytes = int(self.options.get('max_audio_track_bytes', 100 * 1024 * 1024))
        self.max_audio_bundle_bytes = int(self.options.get('max_audio_bundle_bytes', 512 * 1024 * 1024))
        self.client = HTTPX_TRANSPORT.client(producer="provider.gallica",
            timeout=timeout,
            follow_redirects=True,
            http2=False,
            headers={
                "Accept": "application/atom+xml, application/xml;q=0.9, application/epub+zip;q=0.8, audio/*;q=0.8, */*;q=0.5",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
                "Referer": "https://gallica.bnf.fr/",
            },
        )
        self._search_template: str | None = None
        self.last_search_warnings: list[str] = []

    def _diagnostic(self, *args: Any, **kwargs: Any) -> None:
        """Emit diagnostics without allowing the sink to alter provider behavior."""
        try:
            self.diagnostics.add(*args, **kwargs)
        except Exception:
            pass

    def connect(self) -> list[Account]:
        """An OPDS catalogue is not an authenticated reader account.

        Keep both the canonical singleton and explicit legacy connection
        sessions operational; their configured options still belong to the
        saved provider row. Neither connection has a borrower identity and
        neither may manufacture a primary Account as a side effect.
        """
        self.connected = True
        self._accounts = []
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
    def _document_ark(*values: str | None) -> str | None:
        """Only an exact bare ARK or Gallica document URL attests identity."""
        arks = set()
        for value in values:
            if not isinstance(value, str):
                continue
            match = re.fullmatch(
                r"(?:https?://gallica\.bnf\.fr/)?(ark:/12148/[A-Za-z0-9]+)",
                value, flags=re.ASCII | re.IGNORECASE,
            )
            if match:
                arks.add("ark:/12148/" + match.group(1).rsplit("/", 1)[-1])
        return next(iter(arks)) if len(arks) == 1 else None

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

    @staticmethod
    def _opds_atom(content):
        failure = None
        try:
            return parse_atom_catalogue(content)
        except CatalogueXMLParseError as exc:
            failure = exc.code
        if failure == "malformed":
            raise ProviderError("Gallica OPDS : XML invalide")
        if failure == "unexpected_root":
            raise _UnsafeOPDS("Gallica OPDS : flux Atom attendu")
        raise _UnsafeOPDS("Gallica OPDS : invalid_opds_feed")

    def _parse_feed(self, content: bytes, base_url: str, *, documentary=False) -> list[SearchResult]:
        feed = self._opds_atom(content)
        rows: list[SearchResult] = []
        occurrences = Counter()
        if documentary and catalogue_url_origin(base_url) != catalogue_url_origin(EPUB_SEARCH):
            raise _UnsafeOPDS("Gallica OPDS : unverified_navigation")
        for entry in feed.entries:
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

            if catalogue_url_origin(base_url) == catalogue_url_origin(EPUB_SEARCH):
                # An acquisition or image URL is not a document identifier.
                # Keep historical custom-catalogue behavior separate.
                exact_ids = [
                    self._text(node) for node in entry.findall(f"{{{ATOM}}}id")
                ]
                exact_links = [
                    self._attr_href(link, base_url)
                    for link in entry.findall(f"{{{ATOM}}}link")
                    if str(link.attrib.get("rel") or "alternate") == "alternate"
                    and str(link.attrib.get("type") or "") in {"text/html", "application/xhtml+xml", ""}
                ]
                ark = self._document_ark(*exact_ids, *exact_links)
                if ark:
                    friendly = f"https://gallica.bnf.fr/{ark}"
            else:
                ark = self._extract_ark(entry_id, friendly, acquisition, fallback_link)
            if documentary:
                # The occurrence needs its own single explicit Atom ID and
                # title, not an alternate, acquisition or generated label.
                ids = entry.findall(f"{{{ATOM}}}id")
                titles = entry.findall(f"{{{ATOM}}}title")
                own = (self._document_ark(self._text(ids[0]))
                       if len(ids) == 1 and not len(ids[0]) else None)
                if own:
                    occurrences[own] += 1
                if (not own or ark != own or len(titles) != 1
                        or len(titles[0]) or not self._text(titles[0])):
                    continue
                # Narrow witnessed formats only. Never infer another
                # manifestation from a foreign or alternate delivery URL.
                qualified = {
                    (href, mime) for href, mime in acquisitions
                    if (href, mime) in {
                        (f"https://gallica.bnf.fr/{ark}.epub", "application/epub+zip"),
                        (f"https://gallica.bnf.fr/{ark}.pdf", "application/pdf"),
                    }
                }
                if len(qualified) != 1 or qualified != set(acquisitions):
                    continue
                acquisition, acquisition_type = next(iter(qualified))
            if not ark or not acquisition:
                continue

            friendly = friendly or f"https://gallica.bnf.fr/{ark}"
            publisher = self._first_text(entry, (f"{{{DCTERMS}}}publisher", f"{{{DC}}}publisher"))
            published, date_evidence = _opds_publication(entry)
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
                        **({"publication_date_evidence": date_evidence} if date_evidence else {}),
                        "rights": self._first_text(entry, (f"{{{DCTERMS}}}rights", f"{{{DC}}}rights")),
                        "languages": self._normalize_languages([self._text(n) for n in entry.findall(f"{{{DCTERMS}}}language") + entry.findall(f"{{{DC}}}language") if self._text(n)]),
                        **_opds_language_evidence(entry),
                        "subjects": (_opds_explicit_subjects(entry) if documentary else
                                     [n.attrib.get("label") or n.attrib.get("term")
                                      for n in entry.findall(f"{{{ATOM}}}category")]),
                    },
                )
            )
        if documentary:
            output = []
            for row in rows:
                if occurrences[row.record_id] != 1:
                    continue
                # Keep bounded explicit language and subject declarations.
                # No inferred cover, delivery locator, rights or raw summary.
                metadata = {
                    "public_url_evidence": "gallica.public_search_record_identifier",
                }
                if row.metadata.get("bibliography_evidence"):
                    metadata["bibliography_evidence"] = row.metadata["bibliography_evidence"]
                    metadata["languages"] = row.metadata["languages"]
                if row.metadata.get("subjects"):
                    metadata["subjects"] = list(row.metadata["subjects"])
                output.append(replace(
                    row, source_collection="public_catalog", owned=False,
                    downloadable=False, download_url=None, thumbnail_url=None,
                    description=None, metadata=metadata,
                ))
            rows = output
        # A real empty Atom page is distinct from a nonempty page whose
        # entries did not satisfy the existing exact ID/acquisition guards.
        return _PartialOPDSRows(rows) if len(rows) != len(feed.entries) else rows

    @staticmethod
    def _opds_resolve(base, raw):
        origin = catalogue_url_origin(base)
        if origin is None:
            raise _UnsafeOPDS("Gallica OPDS : unverified_navigation")
        target = resolve_catalogue_link(
            base, html.unescape(raw.strip()),
            accept=lambda candidate: catalogue_url_origin(candidate) == origin,
        )
        if target is None:
            raise _UnsafeOPDS("Gallica OPDS : unverified_navigation")
        return target

    @staticmethod
    def _bounded_xml_body(response, failure):
        if response.headers.get("content-encoding", "").strip().casefold() not in {"", "identity"}:
            raise failure()
        length = response.headers.get("content-length", "")
        if length.isascii() and length.isdecimal() and (
            len(length) > 9 or int(length) > MAX_XML_BYTES
        ):
            raise failure()
        if response.is_stream_consumed:
            if not response.is_closed or len(response.content) > MAX_XML_BYTES:
                raise failure()
            return response.content
        chunks = []
        size = 0
        for chunk in response.iter_raw(chunk_size=65536):
            size += len(chunk)
            if size > MAX_XML_BYTES:
                raise failure()
            chunks.append(chunk)
        return b"".join(chunks)

    def _opds_get(self, url, *, params=None):
        # Only the two configured OPDS roots authorize origins. A supplied
        # feed/descriptor/redirect cannot extend them. Retain the configured
        # local HTTP simulation origin, but do not allow downgrade redirects.
        origin = catalogue_url_origin(url)
        if origin is None or origin not in {
            catalogue_url_origin(self.opds_url),
            catalogue_url_origin(self.epub_search_url),
        } or "{" in url or "}" in url:
            raise _UnsafeOPDS("Gallica OPDS : unverified_navigation")
        for _ in range(4):
            with self.client.stream(
                "GET", url, params=params, follow_redirects=False,
                headers={"Accept-Encoding": "identity"},
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    url = self._opds_resolve(str(response.url), response.headers.get("location", ""))
                    params = None
                    continue
                response.raise_for_status()
                return self._bounded_xml_body(
                    response, lambda: _UnsafeOPDS("Gallica OPDS : invalid_opds_feed"),
                ), str(response.url)
        raise _UnsafeOPDS("Gallica OPDS : unverified_navigation")

    def _discover_search_template(self) -> str:
        if self._search_template:
            return self._search_template
        body, base = self._opds_get(self.opds_url)
        feed = self._opds_atom(body)
        matches = [link for link in feed.links if link.rel == "search"]
        if not matches:
            self._search_template = self._fallback_search_template()
            return self._search_template
        # A declared but conflicting discovery target is not a missing link.
        targets = {(self._opds_resolve(base, link.href), link.media_type) for link in matches}
        if len(targets) != 1:
            raise _UnsafeOPDS("Gallica OPDS : unverified_navigation")
        href, typ = next(iter(targets))
        if "opensearchdescription" not in typ:
            self._search_template = href if "{searchTerms" in href else self._fallback_search_template()
            return self._search_template
        descriptor, descriptor_url = self._opds_get(href)
        failure = None
        try:
            os_root = parse_bounded_catalogue_xml(descriptor)
        except CatalogueXMLParseError as exc:
            failure = exc.code
        if failure is not None:
            if failure == "malformed":
                raise ProviderError("Gallica OpenSearch : XML invalide")
            raise _UnsafeOPDS("Gallica OpenSearch : invalid_opds_feed")
        if os_root.tag not in {"OpenSearchDescription", f"{{{OPENSEARCH}}}OpenSearchDescription"}:
            raise _UnsafeOPDS("Gallica OpenSearch : invalid_opds_feed")
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
        self._search_template = (self._opds_resolve(descriptor_url, chosen)
                                 if "{searchTerms" in chosen else self._fallback_search_template())
        return self._search_template

    def _fallback_search_template(self) -> str:
        # Documented SRU/OPDS endpoint; /opds?q=... is not a search API.
        return (self.epub_search_url + '?operation=searchRetrieve&version=1.2&query='
                + quote_plus('(gallica all "{searchTerms}") and ' + EBOOK_FORMAT_CQL)
                  .replace('%7BsearchTerms%7D', '{searchTerms}')
                + '&startRecord={startIndex}&maximumRecords=50')

    def _search_ebooks(self, query: str, page: int, field: str, *, documentary=False) -> list[SearchResult]:
        if field == "all":
            try:
                template = self._discover_search_template()
            except _UnsafeOPDS:
                raise
            except (httpx.HTTPError, ProviderError):
                template = self._fallback_search_template()
                self._search_template = template
            # Escape CQL quotes before percent encoding in a SRU template.
            rendered_query = query.replace('\\', '\\\\').replace('"', '\\"') if "query=" in template else query
            body, response_url = self._opds_get(self._render_search_url(template, rendered_query, page))
        else:
            index = {"title": "dc.title", "author": "dc.creator", "subject": "dc.subject", "isbn": "dc.identifier"}.get(field)
            if index is None:
                raise ProviderError(f"Gallica : champ de recherche non pris en charge : {field}")
            body, response_url = self._opds_get(self.epub_search_url, params={
                "operation": "searchRetrieve", "version": "1.2",
                "query": f'({index} all {_cql_literal(query)}) and {EBOOK_FORMAT_CQL}',
                "startRecord": str(max(0, page) * 50 + 1), "maximumRecords": "50",
            })
        return self._parse_feed(body, response_url, documentary=True) if documentary else self._parse_feed(body, response_url)

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
            if catalogue_url_origin(self.sru_url) == catalogue_url_origin(EPUB_SEARCH):
                ark = self._document_ark(*(
                    self._text(node) for node in record_data.iter(f"{{{DC}}}identifier")
                ))
            else:
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
                metadata={
                    "languages": self._normalize_languages(values.get("language", [])),
                    "subjects": list(values.get("subject", [])),
                    "identifiers": list(values.get("identifier", [])),
                    "bibliographic_types": list(values.get("type", [])),
                    "bibliographic_formats": list(values.get("format", [])),
                    "metadata_evidence": "gallica.sru.dublin_core",
                },
            ))
        return rows

    @staticmethod
    def _sru_record_nodes(root):
        ns = "{http://www.loc.gov/zing/srw/}"
        if root.tag != ns + "searchRetrieveResponse":
            raise _UnsafeSRU("Gallica SRU : invalid_audio_feed")
        if any(node.tag.rsplit("}", 1)[-1] == "diagnostic" for node in root.iter()):
            raise _UnsafeSRU("Gallica SRU : invalid_audio_feed")
        return root.findall(f"{ns}records/{ns}record/{ns}recordData")

    def _recover_sru_audio_records(self, content: bytes) -> tuple[list[SearchResult], int, int]:
        """Conservative recovery of malformed recordData, not new XML authority.

        Only UTF-8, small documents with a verifiable SRU envelope; never
        recover DTD, comments, CDATA or processing instructions. Retain the
        actual envelope/namespace declarations instead of inventing them.
        """
        if len(content) > 256 * 1024:
            raise _UnsafeSRU("Gallica SRU : invalid_audio_feed")
        text = None
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
        if text is None:
            raise _UnsafeSRU("Gallica SRU : invalid_audio_feed")
        # The declaration may be present, but no other markup declaration
        # or PI may hide a fake record or a DTD after the first parse error.
        body = re.sub(r"^<\?xml\s+[^<>]*\?>", "", text, count=1)
        if "<!" in body or "<?" in body or "\x00" in body:
            raise _UnsafeSRU("Gallica SRU : invalid_audio_feed")
        tag_pattern = re.compile(r"<(/?)((?:[A-Za-z_][\w.-]*:)?recordData)\s*>")
        spans = []
        opening = None
        for match in tag_pattern.finditer(body):
            if not match.group(1):
                if opening is not None:
                    raise _UnsafeSRU("Gallica SRU : invalid_audio_feed")
                opening = match
            else:
                if opening is None or opening.group(2) != match.group(2):
                    raise _UnsafeSRU("Gallica SRU : invalid_audio_feed")
                spans.append((opening.end(), match.start()))
                opening = None
                if len(spans) > 50:
                    raise _UnsafeSRU("Gallica SRU : invalid_audio_feed")
        if opening is not None or not spans:
            return [], len(spans), 0

        def document(keep):
            chunks, offset = [], 0
            for i, (start, stop) in enumerate(spans):
                chunks.append(body[offset:start])
                if i == keep:
                    chunks.append(body[start:stop])
                offset = stop
            chunks.append(body[offset:])
            return "".join(chunks).encode("utf-8")

        def parse(payload):
            failure = None
            try:
                root = parse_bounded_catalogue_xml(payload)
            except CatalogueXMLParseError as exc:
                failure = exc.code
            if failure == "malformed":
                return None
            if failure is not None:
                raise _UnsafeSRU("Gallica SRU : invalid_audio_feed")
            nodes = self._sru_record_nodes(root)
            if len(nodes) != len(spans):
                raise _UnsafeSRU("Gallica SRU : invalid_audio_feed")
            return nodes

        # The envelope must itself parse once ALL record contents are
        # removed. Each recovered record is parsed in its original context.
        if parse(document(None)) is None:
            return [], len(spans), len(spans)
        nodes = []
        broken = 0
        for i in range(len(spans)):
            parsed = parse(document(i))
            if parsed is None:
                broken += 1
            else:
                nodes.append(parsed[i])
        return self._audio_rows_from_nodes(nodes), len(spans), broken

    def _parse_sru_audio(self, content: bytes) -> list[SearchResult]:
        failure = None
        try:
            root = parse_bounded_catalogue_xml(content)
        except CatalogueXMLParseError as exc:
            failure = exc.code
        if failure is None:
            return self._audio_rows_from_nodes(self._sru_record_nodes(root))
        if failure != "malformed":
            raise _UnsafeSRU("Gallica SRU : invalid_audio_feed")
        rows, fragments, broken = self._recover_sru_audio_records(content)
        if rows:
            self._diagnostic(
                "GALLICA", "SRU audio XML recovery", "ok",
                detail={"records_found": fragments, "records_skipped": broken,
                        "records_recovered": len(rows)},
                level="detailed",
            )
            return _RecoveredAudioRows(rows)
        self._diagnostic(
            "GALLICA", "SRU audio XML parse", "error",
            detail={"records_found": fragments, "records_skipped": broken},
        )
        raise ProviderError("réponse audio temporairement inexploitable")

    def _search_audio(self, query: str, page: int, field: str = "all") -> list[SearchResult]:
        index = {"all": "gallica", "title": "dc.title", "author": "dc.creator", "subject": "dc.subject", "isbn": "dc.identifier"}.get(field)
        if index is None:
            raise ProviderError(f"Gallica : champ de recherche non pris en charge : {field}")
        cql = f'({index} all {_cql_literal(query)}) and (dc.type any "audio")'
        origin = catalogue_url_origin(self.sru_url)
        official = origin == catalogue_url_origin(EPUB_SEARCH)
        # An official SRU response may attest an ARK only for its own
        # configured endpoint. An HTTP redirect is not that observation.
        # Preserve historical behavior for explicitly configured custom sources.
        redirect_options = {"follow_redirects": False} if official else {}
        with self.client.stream("GET", self.sru_url, params={
            "operation": "searchRetrieve",
            "version": "1.2",
            "query": cql,
            "startRecord": str(max(1, page * 50 + 1)),
            "maximumRecords": "50",
        }, headers={"Accept-Encoding": "identity"}, **redirect_options) as response:
            if official and (
                300 <= response.status_code < 400
                or response.history
                or catalogue_url_origin(str(response.url)) != origin
                or urlsplit(str(response.url)).path != urlsplit(self.sru_url).path
            ):
                raise _UnsafeSRU("Gallica SRU : unverified_navigation")
            response.raise_for_status()
            content = self._bounded_xml_body(
                response, lambda: _UnsafeSRU("Gallica SRU : invalid_audio_feed"),
            )
            status = response.status_code
        try:
            return self._parse_sru_audio(content)
        except ProviderError:
            self._diagnostic("GALLICA", "audio response metadata", "error", detail={
                "http_status": status,
                "bytes": len(content),
            })
            raise

    def search_with_response_facets(
        self, query: str, scope: str = "all", *, field: str = "all",
        content_types: tuple[str, ...] = (),
    ) -> ProviderSearchResponse:
        """Anonymous OPDS facts only: no legacy acquisition or audio authority."""
        if not (self.connected and self.credential_id == "__public__"
                and type(self.credentials) is dict and not self.credentials
                and type(self._accounts) is list and not self._accounts
                and self.opds_url == "https://gallica.bnf.fr/opds"
                and self.epub_search_url == EPUB_SEARCH):
            return ProviderSearchResponse([], partial=True, catalogue_complete=False)
        rows = self.search(
            query, "all" if scope == "catalog" else scope, field=field,
            content_types=content_types, _documentary=True,
        )
        return ProviderSearchResponse(
            rows, partial=bool(self.last_search_warnings), catalogue_complete=False,
            public_notices=tuple(PublicSearchNotice(row) for row in rows),
        )

    def search(self, query: str, scope: str = "all", page: int = 0, field: str = "all", content_types: tuple[str, ...] = (), *, _documentary=False) -> list[SearchResult]:
        self.last_search_warnings = []
        # Validate before any network or short-query zero: unsupported field
        # and physical catalogue are not evidence of missing public books.
        if type(field) is not str or field not in DESCRIPTOR.search_capabilities.fields:
            raise ProviderCapabilityError("Gallica : unsupported_field")
        if type(scope) is not str or scope not in DESCRIPTOR.search_capabilities.scopes:
            raise ProviderCapabilityError("Gallica : unsupported_scope")
        if type(page) is not int or not 0 <= page < MAX_REQUESTED_PAGES:
            # A negative, boolean or non-integer page cannot be treated as
            # page zero or reach either independent Gallica branch.
            raise ProviderCapabilityError("Gallica : invalid_page")
        # Content-type capabilities are independent of query length: an
        # unavailable format must never become an apparently successful zero
        # merely because the submitted text happens to be too short.
        if (type(content_types) not in (tuple, list, set, frozenset)
                or any(type(value) is not str or not 0 < len(value) <= 80
                       for value in content_types)):
            raise ProviderCapabilityError("Gallica : unsupported_content_type")
        # Do not stringify objects from a cache or arbitrary public caller
        # into an OPDS/SRU query. None keeps historical empty-query behavior.
        if query is None:
            query = ""
        if (type(query) is not str or len(query) > 2048
                or any(category(char) in {"Cc", "Cf", "Cs"} for char in query)):
            raise ProviderCapabilityError("Gallica : invalid_query")
        query = query.strip()
        requested = {value.casefold() for value in content_types}
        # Core passes file formats; legacy media categories remain supported.
        audio_formats = {"audiobook", "mp3", "m4a", "ogg", "opus", "flac", "wav"}
        ebook_formats = {"ebook", "epub", "pdf"} if _documentary else {"ebook", "epub", "pdf", "kepub"}
        skipped_unknown = bool(requested - audio_formats - ebook_formats)
        audio_explicit = bool(requested & audio_formats)
        skipped_audio = audio_explicit and not self.include_audio
        ebooks = not requested or bool(requested & ebook_formats)
        audio = self.include_audio and (not requested or audio_explicit)
        documentary_audio_omitted = _documentary and audio
        if _documentary:
            audio = False
        branches = []
        if ebooks:
            fn = (lambda q, p, f: self._search_ebooks(q, p, f, documentary=True)) if _documentary else self._search_ebooks
            branches.append(("OPDS", "catalogue livres numériques temporairement indisponible", fn))
        if audio:
            branches.append(("audio", "recherche audio temporairement indisponible", self._search_audio))
        if not branches:
            # The user requested only media formats unsupported by this
            # configured provider. This is NOT a successful empty search:
            # no remote catalogue was queried and Core must see the limit.
            self.last_search_warnings = [
                "unsupported_content_type : aucune branche Gallica disponible pour les formats demandés"
            ]
            return []
        # Classify branch availability before the historical short-text
        # shortcut. A one-character query with only unsupported formats has
        # *not* searched a catalogue and cannot be presented as a true zero.
        if len(query) < 2:
            return []
        rows: list[SearchResult] = []
        errors: list[str] = []
        causes = []
        successful_branches = 0
        recovered_audio = False
        partial_opds = False
        # Independent services run concurrently; an audio timeout does not add
        # another complete timeout after the EPUB branch has finished.
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="gallica-search") as pool:
            tasks = [(label, message, pool.submit(fn, query, page, field)) for label, message, fn in branches]
            for label, message, task in tasks:
                try:
                    branch_rows = task.result()
                    # Even an empty successful branch proves the route was
                    # available. A failure in the other branch must not turn
                    # this valid zero into total service_unavailable.
                    successful_branches += 1
                    rows.extend(branch_rows)
                    recovered_audio |= isinstance(branch_rows, _RecoveredAudioRows)
                    partial_opds |= isinstance(branch_rows, _PartialOPDSRows)
                except Exception as exc:
                    errors.append(message)
                    causes.append(exc)
                    # Provider errors may embed signed request URLs or HTML
                    # fragments in __str__; diagnostics are user-exportable.
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    self._diagnostic("GALLICA", label + " search branch", "error",
                        detail={
                            "error": type(exc).__name__,
                            "http_status": status if type(status) is int and 100 <= status <= 599 else None,
                        })
        self.last_search_warnings = [
            ("partial_results : " if successful_branches else "") + "Gallica : " + message
            for message in errors
        ]
        if documentary_audio_omitted:
            self.last_search_warnings.append(
                "partial_results : Gallica : branche audio non qualifiée pour Notice C"
            )
        if partial_opds:
            self.last_search_warnings.append(
                "partial_results : Gallica : certaines entrées OPDS non qualifiées"
            )
        if recovered_audio:
            self.last_search_warnings.append(
                "partial_results : Gallica : récupération partielle du XML audio"
            )
        if skipped_audio and successful_branches:
            # Explicitly requested audio was not searched. An eligible EPUB
            # branch is not evidence that the combined query was exhaustive.
            # Default searches with include_audio=False are not warned:
            # no audio format was explicitly requested by the caller.
            self.last_search_warnings.append(
                "partial_results : Gallica : branche audio désactivée par configuration"
            )
        if skipped_unknown and successful_branches:
            # A recognized EPUB/MP3 format can be searched successfully even
            # while another requested format has no Gallica branch. Do not
            # advertise the combined request as exhaustively searched.
            self.last_search_warnings.append(
                "partial_results : Gallica : certains formats demandés ne sont pas pris en charge"
            )
        # An exact result ID can occur more than once in the selected
        # branches/page. Do not let arrival order select its title, support,
        # cover or acquisition offer when the parsed occurrences disagree.
        # Identical projected rows are harmless repeats. Once conflicting,
        # the ID remains refused for this response, not just for one pair.
        by_id = {}
        conflicts = set()
        for item in rows:
            if item.id in conflicts:
                continue
            if item.id not in by_id:
                by_id[item.id] = item
            elif by_id[item.id] != item:
                del by_id[item.id]
                conflicts.add(item.id)
        rows = list(by_id.values())
        if _documentary and requested and "ebook" not in requested:
            # Exact media-format filters operate only on this loaded page.
            supported = requested & {"epub", "pdf"}
            rows = [row for row in rows if row.format.casefold() in supported]
        if conflicts:
            self.last_search_warnings.append(
                "partial_results : Gallica : occurrences de notice contradictoires"
            )
        if errors and not successful_branches:
            # A 401/403 on either independent public branch is not a
            # transport outage and must never authorize Core's stale
            # catalogue fallback. Only inspect httpx status integers,
            # never plugin exception messages or user-supplied code objects.
            statuses = [
                exc.response.status_code for exc in causes
                if isinstance(exc, httpx.HTTPStatusError)
            ]
            if 403 in statuses:
                error = ProviderError("Gallica : accès au catalogue refusé (access_denied)")
                error.code = "search_access_denied"
            elif 401 in statuses:
                error = ProviderError("Gallica : authentification requise (auth_required)")
                error.code = "search_authentication_required"
            else:
                error = ProviderError("Gallica : " + " ; ".join(errors))
                # A semantic HTTP 4xx (400/404/422) is not a technical
                # outage; the Core must not turn it into public stale rows.
                semantic = any(
                    400 <= status < 500 and status not in {408, 429}
                    for status in statuses
                )
                rejected_payload = any(isinstance(exc, (_UnsafeOPDS, _UnsafeSRU)) for exc in causes)
                # A rejected payload/navigation must never become authority
                # for Core's stale fallback or an equivalent retry.
                if not semantic and not rejected_payload:
                    error.code = "search_temporarily_unavailable"
                    error.retryable = True
                elif rejected_payload:
                    error.retryable = False
            # Exceptions can contain signed request URLs, cookies and bodies.
            # Do not attach a raw branch error as public __cause__.
            raise error from None
        self._diagnostic("GALLICA", "federated public search", "ok", detail={
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
        return sanitize_filename_component(value, fallback)

    def _download_ebook(self, item: SearchResult, target_dir: Path) -> tuple[Path, str]:
        url = _public_url(str(item.download_url or "").strip())
        if not url:
            raise ProviderError("Gallica : aucun lien de téléchargement attesté")
        if self.transport is None:
            raise ProviderError("Gallica : transport.http indisponible")
        fmt = str(item.format or "").upper()
        if fmt not in {"EPUB", "PDF"}:
            raise ProviderError("Gallica : format numérique non pris en charge")
        ext = ".pdf" if fmt == "PDF" else ".epub"
        target_dir.mkdir(parents=True, exist_ok=True)
        stable = self._safe_name(item.record_id.split("/")[-1], "")
        base = target_dir / (self._safe_name(item.title)[:145] + (f"_{stable[:24]}" if stable else "") + ext)
        path = base
        serial = 1
        while path.exists():
            path = base.with_name(f"{base.stem}_{serial}{base.suffix}")
            serial += 1
        allowed = (
            ("application/pdf", "application/octet-stream")
            if fmt == "PDF"
            else ("application/epub+zip", "application/zip", "application/octet-stream")
        )
        source = {
            "url": url,
            "headers": {
                "Accept": "application/epub+zip, application/pdf;q=0.9, */*;q=0.2",
                "Referer": "https://gallica.bnf.fr/",
            },
            "max_bytes": self.max_download_bytes,
            "allowed_content_types": allowed,
            "expected_format": fmt.casefold(),
        }
        try:
            path = Path(self.transport.fetch(source, path))
        except Cancelled:
            # The caller explicitly aborted the transfer: preserve the
            # control-flow signal instead of reporting a failed provider.
            path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            path.unlink(missing_ok=True)
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError(f"Gallica : téléchargement numérique indisponible ({exc})") from exc
        self._diagnostic(
            "GALLICA", "ebook download", "ok",
            detail={"bytes": path.stat().st_size, "format": fmt, "transport": "transport.http"},
            level="detailed",
        )
        return path, url

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
        recipe = _gallica_audio_recipe(item)
        if recipe is None:
            raise ProviderError("Gallica : recette audio sans ARK")
        return download_bundle(self, item, target_dir, recipe=recipe)

    def acquisition_offers(self, item: SearchResult) -> list[AcquisitionOffer]:
        # An ARK page identifies the resolver for a bundle, never a media track.
        if item.content_type == "audiobook":
            recipe = _gallica_audio_recipe(item)
            return [] if recipe is None else [
                recipe.to_offer(offer_id=f"gallica:{item.record_id}:audio")
            ]
        if not item.download_url:
            return []
        fmt = str(item.format or "").lower()
        allowed = (
            ("application/pdf", "application/octet-stream")
            if fmt == "pdf"
            else ("application/epub+zip", "application/zip", "application/octet-stream")
        )
        return [AcquisitionOffer(
            offer_id=f"gallica:{item.record_id}",
            media_type=item.content_type or "ebook",
            format=fmt,
            drm="none",
            source_kind="http",
            source={
                "url": _public_url(item.download_url),
                "headers": {
                    "Accept": "application/epub+zip, application/pdf;q=0.9, */*;q=0.2",
                    "Referer": "https://gallica.bnf.fr/",
                },
                "max_bytes": self.max_download_bytes,
                "allowed_content_types": allowed,
                "expected_format": fmt,
            },
            priority=10,
        )]

    def download_direct(self, item: SearchResult, target_dir: Path) -> tuple[Path, str]:
        if item.content_type == "audiobook":
            return self._download_audio(item, target_dir)
        return self._download_ebook(item, target_dir)


class GallicaPlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def activate(self, context):
        self._configuration = dict(context.config)
        self._transport = context.service("transport.http")
        if self._transport is None:
            raise ProviderError("Gallica : transport.http requis")
        return self
    @staticmethod
    def parse_feed(content: bytes, base_url: str) -> list[dict[str, object]]:
        # Keep the historical dictionary projection, but share the exact
        # bounded Atom parser and refusal classification of the session.
        feed = GallicaSession._opds_atom(content)
        out: list[dict[str, object]] = []
        for entry in feed.entries:
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
            transport=self._transport,
        )


PLUGIN = GallicaPlugin()
