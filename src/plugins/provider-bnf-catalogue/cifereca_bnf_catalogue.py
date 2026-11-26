"""Public BnF bibliographic catalogue, Provider Plugin API v1 only."""
from __future__ import annotations
import re
from unicodedata import category
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import httpx
from cifereca.plugin_api import (
    HTTPX_TRANSPORT, CatalogueXMLParseError, parse_bounded_catalogue_xml,
)

from cifereca.plugin_api import (
    description_text,
    description_element,
    Account, ItemDetail, SearchResult, ProviderCapabilities, ProviderSearchCapabilities,
    ProviderCapabilityError, ProviderDescriptor,
    ProviderError, ProviderPlugin, ProviderSession, normalize_language_tag,
    ProviderSearchResponse, PublicSearchNotice,
)

SRU = "http://www.loc.gov/zing/srw/"
DC = "http://purl.org/dc/elements/1.1/"
ENDPOINT = "https://catalogue.bnf.fr/api/SRU"
MAX_SRU_BYTES = 2 * 1024 * 1024
MAX_REQUESTED_PAGES = 101  # Zero-indexed, page 0..100; no unbounded SRU offsets.
# A bibliographic identifier is an exact bare ARK or a Catalogue général
# record URL, not an ARK-looking substring in arbitrary prose or a foreign URL.
ARK = re.compile(r"(?:https?://catalogue\.bnf\.fr/)?(ark:/12148/cb[0-9]{1,20}[a-z0-9])", re.I)
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
        rows, _partial, _complete = self._search_page(query, scope, page, field)
        return rows

    def _search_page(self, query: str, scope="all", page=0, field="all"):
        """Return rows and coverage from this call, never mutable diagnostics."""
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
            return [], bool(query), None
        relation = "adj" if field == "isbn" else "all"
        try:
            with self.client.stream("GET", self.sru_url, params={
                "operation": "searchRetrieve", "version": "1.2", "recordSchema": "dublincore",
                "query": f"{FIELDS[field]} {relation} {literal(query)}",
                "startRecord": str(page * self.page_size + 1),
                "maximumRecords": str(self.page_size),
            }, headers={"Accept-Encoding": "identity"},
                follow_redirects=self.sru_url != ENDPOINT) as response:
                if self.sru_url == ENDPOINT:
                    # A configured public endpoint is not proof of the final
                    # response's origin. Until a redirect route is explicitly
                    # attested, refuse before following it or parsing its body.
                    target = urlsplit(str(response.url))
                    if (300 <= response.status_code < 400 or response.history
                            or (target.scheme, target.netloc, target.path)
                            != ("https", "catalogue.bnf.fr", "/api/SRU")
                            or target.fragment):
                        raise ProviderError("BnF : unverified_sru_navigation")
                response.raise_for_status()
                encoding = response.headers.get("content-encoding", "").strip().casefold()
                declared = response.headers.get("content-length", "")
                if (encoding not in {"", "identity"}
                        or (declared.isascii() and declared.isdecimal()
                            and (len(declared) > 9 or int(declared) > MAX_SRU_BYTES))):
                    raise ProviderError("BnF : invalid_sru_response")
                if response.is_stream_consumed:
                    if not response.is_closed or len(response.content) > MAX_SRU_BYTES:
                        raise ProviderError("BnF : invalid_sru_response")
                    content = response.content
                else:
                    chunks = []
                    total = 0
                    for chunk in response.iter_raw(chunk_size=65536):
                        total += len(chunk)
                        if total > MAX_SRU_BYTES:
                            raise ProviderError("BnF : invalid_sru_response")
                        chunks.append(chunk)
                    content = b"".join(chunks)
            rows, total, record_count, structural, next_positions = self._parse_page(content)
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
        # Determine coverage before local filtering. Discarded/duplicate SRU
        # records, contradictory totals or truncated pages are execution
        # partials, not legitimate pagination. A declared next position may
        # authorize a shorter *window*, never an extra request.
        start = page * self.page_size + 1
        remaining = max(0, total - start + 1)
        expected = min(self.page_size, remaining)
        next_valid = (
            len(next_positions) == 1
            and type(next_positions[0]) is str
            and re.fullmatch(r"[0-9]{1,12}", next_positions[0]) is not None
            and int(next_positions[0]) == start + record_count <= total
            and record_count > 0
        )
        partial = (
            not structural or len(rows) != record_count
            or record_count > expected
            or (bool(next_positions) and not next_valid)
            or (record_count < expected and not next_valid)
        )
        local_filter = scope != "all" or field in {"series", "collection"}
        complete = None if partial else bool(
            page == 0 and not local_filter
            and total == record_count and not next_positions
        )
        if partial:
            self.last_search_warnings.append(
                "partial_results : réponse SRU incomplète ou incohérente"
            )
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
        return rows, partial, complete

    def search_with_response_facets(self, query, scope="all", *, field="all", content_types=()):
        """One same-call SRU page; no claim of exhaustive or private inventory."""
        if (type(content_types) not in (tuple, list, set, frozenset)
                or len(content_types) > 32
                or any(type(value) is not str or not 0 < len(value) <= 80
                       for value in content_types)):
            raise ProviderCapabilityError("BnF : unsupported_content_type")
        requested = {value.casefold() for value in content_types} - {"all"}
        supported = {"epub", "pdf", "ebook", "physical", "unknown"}
        scope = "all" if scope == "catalog" else scope
        rows, partial, complete = self._search_page(query, scope, field=field)
        if requested:
            # A bibliographic MIME is descriptive, never an available
            # file or an acquisition alternative. Do not infer "ebook"
            # from digital support alone (e.g. a sound recording).
            def matches(row):
                kind = row.format.casefold() if type(row.format) is str else None
                return (
                    (kind in {"epub", "pdf"} and
                     bool(requested & {kind, "ebook"}))
                    or (row.medium == "physical" and "physical" in requested)
                    or (row.medium == "unknown" and "unknown" in requested)
                )
            rows = [row for row in rows if matches(row)]
            # The SRU request itself did not apply the Format facet. A
            # filtered page never establishes exhaustive filtered coverage.
            if complete is True:
                complete = False
            if requested - supported:
                partial = True
                complete = None
                self.last_search_warnings.append(
                    "partial_results : formats BnF demandés non qualifiés"
                )
        # The legacy list API retains its page-local warning. The typed
        # envelope now carries that limitation in catalogue_complete, not
        # in last_search_warnings (which older Core treats as a failure).
        if not partial:
            self.last_search_warnings = []
        # Only the configured canonical public SRU endpoint and genuinely
        # anonymous session can qualify cross-profile evidence. A custom
        # endpoint (including local simulation) is not silently relabelled.
        public = (self.connected and self.credential_id == "__public__"
                  and self.sru_url == ENDPOINT
                  and type(self.credentials) is dict and not self.credentials
                  and type(self._accounts) is list and not self._accounts)
        return ProviderSearchResponse(
            results=rows, partial=partial, catalogue_complete=complete,
            public_notices=tuple(PublicSearchNotice(row) for row in rows) if public else (),
        )

    def parse_records(self, content: bytes):
        rows, *_coverage = self._parse_page(content)
        return rows

    def _parse_page(self, content: bytes):
        reason = None
        try:
            root = parse_bounded_catalogue_xml(content)
        except CatalogueXMLParseError as exc:
            reason = exc.code
        if reason is not None:
            # Keep the historic malformed/HTML outage classification.
            # A forbidden declaration or exceeded safety budget is a
            # refusal, not evidence permitting an offline fallback.
            if reason in {"malformed", "html"}:
                raise _bnf_unavailable_xml() from None
            raise ProviderError("BnF : invalid_sru_response") from None
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
        groups = root.findall(f"{{{SRU}}}records")
        records = root.findall(f"{{{SRU}}}records/{{{SRU}}}record")
        data_nodes = root.findall(f".//{{{SRU}}}recordData")
        structural = (
            len(groups) <= 1
            and len(records) == len(data_nodes)
            and all(len(record.findall(f"{{{SRU}}}recordData")) == 1 for record in records)
        )
        next_positions = tuple(
            element.text.strip() if element.text is not None and len(element) == 0 else None
            for element in root.findall(f"{{{SRU}}}nextRecordPosition")
        )
        # Count every recordData (including malformed/nested siblings), but
        # each ARK only once per data node. An invalid duplicate must not
        # make a valid sibling look uniquely attested by this SRU page.
        ark_occurrences: dict[str, int] = {}
        for data in data_nodes:
            observed = {
                match.group(1).lower()
                for node in data.iter(f"{{{DC}}}identifier")
                if (match := ARK.fullmatch(_text(node).strip())) is not None
            }
            for candidate in observed:
                ark_occurrences[candidate] = ark_occurrences.get(candidate, 0) + 1
        rows = {}
        # Only direct SRU record/recordData children attest an occurrence.
        # A nested/orphan recordData still makes coverage partial, but must
        # never mint a public record or poison a valid sibling's identity.
        for record in records:
            own_data = record.findall(f"{{{SRU}}}recordData")
            if len(own_data) != 1:
                continue
            data = own_data[0]
            values: dict[str, list[str]] = {}
            for node in data.iter():
                if node.tag.startswith("{" + DC + "}"):
                    value = _text(node)
                    if value:
                        values.setdefault(node.tag.split("}", 1)[1], []).append(value)
            identifiers = values.get("identifier", [])
            arks = {
                match.group(1).lower() for value in identifiers
                if (match := ARK.fullmatch(value.strip())) is not None
            }
            if len(arks) > 1:
                # Two competing exact Catalogue général records in one DC
                # record cannot attest which book the link belongs to.
                raise ProviderError("BnF : ambiguous_record_identifier")
            if not arks:
                continue
            ark = next(iter(arks))
            if ark_occurrences.get(ark) != 1:
                continue
            formats = values.get("format", [])
            kinds = values.get("type", [])
            links = list(dict.fromkeys(link for value in values.get("relation", []) + identifiers if (link := _gallica_link(value))))
            digital = any(f.lower().startswith(("application/", "audio/", "video/")) for f in formats) or any("électronique" in t.casefold() or "electronic" in t.casefold() for t in kinds)
            physical = any(re.search(r"\b(?:vol\.|p\.|cm\b)", f, re.I) for f in formats)
            support = "digital" if digital else "physical" if physical else "unknown"
            # Technical format is left unknown unless a MIME type says otherwise.
            fmt = next(({"application/epub+zip": "EPUB", "application/pdf": "PDF"}[f.lower().split(";", 1)[0].strip()] for f in formats if f.lower().split(";", 1)[0].strip() in {"application/epub+zip", "application/pdf"}), None)
            # Only literal identifiers of this record, never dc.relation,
            # nested markup or the search query. Keep the declared ISBN form.
            isbns = []
            # The DC record envelope may use the DC, OAI-DC or SRU-DC
            # namespace. Only its immediate fields belong to this record;
            # an identifier inside a relation or another embedded record
            # cannot attest the manifestation described here.
            dc_records = [node for node in data if node.tag in {
                f"{{{DC}}}dc", "{http://www.openarchives.org/OAI/2.0/oai_dc/}dc",
                "{http://www.loc.gov/zing/srw/dcschema/v1.0/}dc",
            }]
            literal_nodes = (dc_records[0].findall(f"{{{DC}}}identifier")
                             if len(dc_records) == 1 else [])
            # Require the same exact ARK in this envelope, not an ARK
            # found elsewhere by the legacy descriptive extraction.
            literal_nodes = [
                node for node in literal_nodes
                if not len(node) and not node.attrib and node.text
                and len(node.text) <= 128
            ]
            if not any(
                (match := ARK.fullmatch(node.text.strip())) is not None
                and match.group(1).lower() == ark for node in literal_nodes
            ):
                literal_nodes = []
            for node in literal_nodes:
                candidate = re.sub(r"^(?:urn:isbn:|isbn\s*:?)\s*", "", node.text.strip(), flags=re.I)
                candidate = re.sub(r"[\s-]", "", candidate).upper()
                if re.fullmatch(r"[0-9]{9}[0-9X]", candidate):
                    valid = sum((10 - i) * (10 if c == "X" else int(c))
                                for i, c in enumerate(candidate)) % 11 == 0
                elif re.fullmatch(r"97[89][0-9]{10}", candidate):
                    valid = sum(int(c) * (1 if i % 2 == 0 else 3)
                                for i, c in enumerate(candidate)) % 10 == 0
                else:
                    valid = False
                if valid and candidate not in isbns:
                    isbns.append(candidate)
            languages = list(dict.fromkeys(
                canonical for raw in values.get("language", [])
                if (canonical := normalize_language_tag(raw)) is not None
            ))
            # Retain the exact record's raw DC tags only when the entire
            # bounded declaration normalizes. Mixed invalid input must not
            # masquerade as a complete, attested set of languages.
            # Inspect every raw declaration: the display-oriented values map
            # drops empties and flattens nested XML, so it cannot prove that
            # the whole record's language set was understood.
            language_nodes = list(data.iter(f"{{{DC}}}language"))
            raw_tags = [(node.text or "").strip() for node in language_nodes]
            language_proof = {}
            if (languages and 1 <= len(raw_tags) <= 16
                    and all(not len(node) and not node.attrib
                            and re.fullmatch(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{1,8}){0,8}", tag)
                            and len(tag) <= 128 and normalize_language_tag(tag)
                            for node, tag in zip(language_nodes, raw_tags))):
                language_proof = {"bibliography_evidence": {"languages": {
                    "raw_tags": raw_tags, "source": self.provider_id + ".sru",
                    "source_field": "dc.language", "scope": "SRU-DUBLINCORE",
                    "confidence": "explicit_record_field", "freshness": "search_response",
                }}}
            if 1 <= len(isbns) <= 32:
                language_proof.setdefault("bibliography_evidence", {})["isbns"] = {
                    "source": self.provider_id + ".sru",
                    "source_field": "dc.identifier", "scope": "SRU-DUBLINCORE",
                    "record_id": ark, "confidence": "explicit_record_field",
                    "freshness": "search_response", "relationship": "record_identifier",
                }
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
                download_url=None, downloadable=False, owned=False,
                source_collection="public_catalog",
                metadata={"source": "BnF Catalogue général", "isbns": isbns,
                          "public_url_evidence": f"{self.provider_id}.public_search_record_identifier",
                          "subjects": values.get("subject", []), "languages": languages,
                          **language_proof,
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
        return (list(rows.values()), int(totals[0].text.strip()),
                len(records), structural, next_positions)

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
