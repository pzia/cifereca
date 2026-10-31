"""Public BnF bibliographic catalogue, Provider Plugin API v1 only."""
from __future__ import annotations
import re
from unicodedata import category
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT

from cifereca.plugin_api import (
    description_text,
    description_element,
    Account, ItemDetail, SearchResult, ProviderCapabilities, ProviderSearchCapabilities,
    ProviderCapabilityError, ProviderDescriptor,
    ProviderError, ProviderPlugin, ProviderSession, normalize_language_tag,
)

SRU = "http://www.loc.gov/zing/srw/"
DC = "http://purl.org/dc/elements/1.1/"
ENDPOINT = "https://catalogue.bnf.fr/api/SRU"
MAX_REQUESTED_PAGES = 101  # Zero-indexed, page 0..100; no unbounded SRU offsets.
ARK = re.compile(r"(?:https?://catalogue\.bnf\.fr/)?(ark:/12148/cb[0-9]+[a-z0-9])(?:[/?#\s]|$)", re.I)
FIELDS = {
    "all": "bib.anywhere", "title": "bib.title", "author": "bib.author",
    "isbn": "bib.isbn", "subject": "bib.subject", "series": "bib.anywhere", "collection": "bib.anywhere",
}

DESCRIPTOR = ProviderDescriptor(
    id="bnf-catalogue", display_name="BnF — Catalogue général",
    description="Notices bibliographiques publiques : titres, auteurs, ISBN, sujets, séries et collections. Pas de prêt ni de téléchargement.",
    state="ready", capabilities=ProviderCapabilities(search=True, holdings=True),
    search_capabilities=ProviderSearchCapabilities(
        fields=tuple(FIELDS),
        scopes=("all", "physical", "digital"),
        pagination="page",
        page_size=50,
        max_pages=MAX_REQUESTED_PAGES,
        notes=(
            "SRU BnF : all/title/author/isbn/subject passent par des champs serveur dédiés.",
            "series/collection : SRU bib.anywhere puis post-filtre local sur la page reçue ; résultats non exhaustifs.",
            "digital/physical : post-filtre du support attesté sur les notices de la page SRU ; 'all' conserve les supports inconnus.",
            "Taille de page par défaut 50, configurable de 1 à 100 selon la session.",
        ),
    ),
    homepage="https://catalogue.bnf.fr",
    notes=("API SRU publique, sans compte BnF.",
           "Une notice bibliographique n'est pas une offre d'acquisition.",),
)


def literal(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _text(node: ET.Element) -> str:
    return "".join(node.itertext()).strip()


def _gallica_link(value: str) -> str | None:
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme in {"https", "http"} and parsed.hostname == "gallica.bnf.fr" and parsed.path.startswith("/ark:/12148/"):
        return parsed._replace(scheme="https").geturl()
    return None


def _structured_group_values(values: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """Extract conservative series/collection labels from simple Dublin Core."""
    def clean(rows):
        out=[]
        for raw in rows:
            value=' '.join(str(raw or '').split())
            if not value or value.startswith(('http://','https://','ark:/','urn:')):
                continue
            if value not in out: out.append(value)
        return out
    collections=clean(values.get('source',[]))
    series=clean(values.get('relation',[]))
    return series,collections


def _bnf_unavailable_xml():
    """A syntactically invalid or non-SRU HTTP 200 is not a catalogue zero."""
    failure = ProviderError("BnF : invalid_sru_response")
    failure.code = "search_temporarily_unavailable"
    failure.retryable = False
    return failure


class BnfCatalogueSession(ProviderSession):
    def __init__(self, *, diagnostics: Any, **kwargs: Any):
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        self.diagnostics = diagnostics
        self.sru_url = str(self.options.get("sru_url") or ENDPOINT)
        self.page_size = int(self.options.get("page_size", 50))
        if not 1 <= self.page_size <= 100:
            raise ProviderError("BnF : taille de page hors limites (1–100)")
        self.client = HTTPX_TRANSPORT.client(producer="provider.bnf-catalogue",
            timeout=float(self.options.get("timeout_seconds", 25)), follow_redirects=True,
            headers={"Accept": "application/xml, text/xml"},
        )

    def connect(self):
        self.connected = True
        # Public session; no fictitious borrower or delegated reader identity.
        self._accounts = []
        return []

    def close(self):
        self.client.close()
        self.connected = False

    def _diagnostic(self, operation: str, status: str, *, detail: dict[str, Any] | None = None) -> None:
        try:
            self.diagnostics.add("BNF", operation, status, detail=detail or {})
        except Exception:
            pass

    def search(self, query: str, scope="all", page=0, field="all"):
        # A previous partial search must not pollute subsequent requests,
        # including rejected fields, too-short queries and network failures.
        self.last_search_warnings = []
        if type(field) is not str or field not in FIELDS:
            raise ProviderCapabilityError("BnF : unsupported_field")
        if type(scope) is not str or scope not in {"all", "physical", "digital"}:
            raise ProviderCapabilityError("BnF : unsupported_scope")
        if type(page) is not int or not 0 <= page < MAX_REQUESTED_PAGES:
            # Never silently transform a requested negative/boolean/fractional
            # page into page 0 and call that a valid search result.
            raise ProviderCapabilityError("BnF : invalid_page")
        # An arbitrary cached object must not be serialized into an SRU CQL
        # query or silently converted to a successful short-query zero.
        # None retains the existing empty-search behavior.
        if query is None:
            query = ""
        if (type(query) is not str or len(query) > 2048
                or any(category(char) in {"Cc", "Cf", "Cs"} for char in query)):
            raise ProviderCapabilityError("BnF : invalid_query")
        query = query.strip()
        if field == "isbn":
            # Validate ISBN syntax before the general short-query exit.
            # A malformed one-character/empty ISBN is not a legitimate
            # negative bibliographic search: SRU was never queried.
            query = re.sub(r"[\s-]", "", query)
            if not re.fullmatch(r"(?:\d{9}[\dXx]|\d{13})", query):
                raise ProviderError("BnF : ISBN attendu sur 10 ou 13 caractères")
        if len(query) < 2:
            # A nonempty one-letter query is skipped locally, not an SRU
            # response proving zero matching bibliographic records.
            if query:
                self.last_search_warnings.append(
                    'partial_results : requête trop courte ; BnF non interrogée'
                )
            return []
        relation = "adj" if field == "isbn" else "all"
        try:
            response = self.client.get(self.sru_url, params={
                "operation": "searchRetrieve", "version": "1.2", "recordSchema": "dublincore",
                "query": f"{FIELDS[field]} {relation} {literal(query)}",
                "startRecord": str(page * self.page_size + 1),
                "maximumRecords": str(self.page_size),
            })
            response.raise_for_status()
            rows = self.parse_records(response.content)
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            code = (
                "auth_required" if status == 401 else
                "access_denied" if status == 403 else
                "rate_limited" if status == 429 else
                "service_unavailable"
            )
            # Stable, redacted failure codes. Never echo URLs, credentials or
            # provider response text in user-exportable diagnostics.
            self._diagnostic("SRU search", "error", detail={
                "error": type(exc).__name__,
                "http_status": status if type(status) is int and 100 <= status <= 599 else None,
            })
            failure = ProviderError(f"BnF : catalogue temporairement indisponible ({code})")
            if status == 401:
                failure.code = "search_authentication_required"
            elif status == 403:
                failure.code = "search_access_denied"
            elif (status in {408, 429}
                  or type(status) is int and 500 <= status <= 599
                  or isinstance(exc, (httpx.TransportError, httpx.DecodingError))):
                failure.code = "search_temporarily_unavailable"
                failure.retryable = False  # No duplicate SRU GET.
            # Request errors can contain signed URLs or account information.
            raise failure from None
        # Unknown support is omitted from scoped searches. API v1 only allows
        # physical/digital; all-scope uses a documented physical fallback.
        if scope != "all":
            rows = [r for r in rows if r.metadata["support"] == scope]
        if field in {"series","collection"}:
            key="series" if field=="series" else "collections"
            needle=query.casefold()
            rows=[r for r in rows if any(needle in str(value).casefold() for value in r.metadata.get(key,[]))]
        if scope != "all" or field in {"series", "collection"}:
            # Scoped support and series/collection filtering use only the
            # retrieved SRU page; a local zero is not a catalogue-wide zero.
            self.last_search_warnings.append(
                "partial_results : post-filtre local limité à la page SRU demandée"
            )
        self._diagnostic("SRU search", "ok", detail={"results": len(rows), "page": page, "field": field})
        return rows

    def parse_records(self, content: bytes):
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            raise _bnf_unavailable_xml() from None
        if root.tag != f"{{{SRU}}}searchRetrieveResponse":
            raise _bnf_unavailable_xml() from None
        if any(n.tag.rsplit("}", 1)[-1] == "diagnostic" for n in root.iter()):
            raise ProviderError("BnF : requête refusée par le service SRU")
        # SRU 1.2 numberOfRecords is the server's evidence for a genuine
        # zero. A structurally empty or truncated 200 response is NOT a
        # successful search with no books: callers must see a typed failure.
        totals = root.findall(f"{{{SRU}}}numberOfRecords")
        if (len(totals) != 1 or totals[0].text is None
                or re.fullmatch(r"[0-9]{1,12}", totals[0].text.strip()) is None):
            raise _bnf_unavailable_xml() from None
        rows = {}
        for data in root.findall(f".//{{{SRU}}}recordData"):
            values: dict[str, list[str]] = {}
            for node in data.iter():
                if node.tag.startswith("{" + DC + "}"):
                    value = _text(node)
                    if value:
                        values.setdefault(node.tag.split("}", 1)[1], []).append(value)
            identifiers = values.get("identifier", [])
            ark = next((m.group(1) for value in identifiers if (m := ARK.fullmatch(value.strip()) or ARK.search(value.strip()))), None)
            if not ark:
                continue
            formats = values.get("format", [])
            kinds = values.get("type", [])
            links = list(dict.fromkeys(link for value in values.get("relation", []) + identifiers if (link := _gallica_link(value))))
            digital = any(f.lower().startswith(("application/", "audio/", "video/")) for f in formats) or any("électronique" in t.casefold() or "electronic" in t.casefold() for t in kinds)
            physical = any(re.search(r"\b(?:vol\.|p\.|cm\b)", f, re.I) for f in formats)
            support = "digital" if digital else "physical" if physical else "unknown"
            # Technical format is left unknown unless a MIME type says otherwise.
            fmt = next(({"application/epub+zip": "EPUB", "application/pdf": "PDF"}[f.lower().split(";", 1)[0].strip()] for f in formats if f.lower().split(";", 1)[0].strip() in {"application/epub+zip", "application/pdf"}), None)
            isbns = []
            for identifier in identifiers:
                candidate = re.sub(r"^(?:urn:isbn:|isbn\s*:?)\s*", "", identifier, flags=re.I)
                candidate = re.sub(r"[\s-]", "", candidate)
                if re.fullmatch(r"(?:\d{9}[\dXx]|\d{13})", candidate):
                    isbns.append(candidate.upper())
            languages = list(dict.fromkeys(
                canonical for raw in values.get("language", [])
                if (canonical := normalize_language_tag(raw)) is not None
            ))
            series, collections = _structured_group_values(values)
            first = lambda key: (values.get(key) or [None])[0]
            rows[ark] = SearchResult(
                id=f"bnf-catalogue:{ark.rsplit('/', 1)[-1]}", record_id=ark,
                docbase="BNF-CATALOGUE", medium=support,
                title=first("title") or "Sans titre", author=" ; ".join(values.get("creator", [])) or None,
                type="Notice bibliographique", format=fmt, publisher=first("publisher"),
                published_at=first("date"), description=description_text("\n".join(values.get("description", []))),
                thumbnail_url=None, friendly_url=f"https://catalogue.bnf.fr/{ark}", scenario="SRU-DUBLINCORE",
                source_provider=self.provider_id, source_credential=self.credential_id,
                download_url=None, downloadable=False,
                metadata={"source": "BnF Catalogue général", "isbns": isbns,
                          "subjects": values.get("subject", []), "languages": languages,
                          "series": series, "collections": collections,
                          "rights": values.get("rights", []), "bibliographic_formats": formats,
                          "bibliographic_types": kinds,
                          "document_links": [
                              {"relation": "gallica", "url": link}
                              for link in links
                          ],
                          "support": support,
                          "format_evidence": "dc.format-mime" if fmt else "unknown"},
            )
        return list(rows.values())

    def holdings(self, item: SearchResult, search_terms=""):
        return ItemDetail(
            record_id=item.record_id, docbase=item.docbase, title=item.title,
            author=item.author, type=item.type, year=item.published_at,
            thumbnail_url=None, availability_status="unknown",
            availability_label="Notice bibliographique — disponibilité non vérifiée",
            holdings=[], source_provider=self.provider_id, source_credential=self.credential_id,
        )

    def acquisition_offers(self, item):
        return []


class BnfCataloguePlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def activate(self, context):
        self._configuration = dict(context.config)
        return self

    def create_session(self, *, credential_id, label, credentials, options, config, storage, diagnostics):
        return BnfCatalogueSession(
            credential_id=credential_id, label=label, credentials=credentials,
            options={**getattr(self, "_configuration", {}), **options}, diagnostics=diagnostics,
        )


PLUGIN = BnfCataloguePlugin()
