"""Public catalogue of the La Rochelle BiblioAccess tenant (issue #3691).

Independent of the reader's SSO and loan inventory. Its public session never
uses, reads, or imports any private credential or CAS cookie.
"""
from __future__ import annotations

from cifereca.plugin_api import (
    ProviderPlugin, ProviderSession, ProviderDescriptor, ProviderCapabilities,
    ProviderSearchCapabilities, ProviderCapabilityError,
    ProviderError, SearchResult, ItemDetail,
)
from biblioaccess_catalogue_http import (
    BiblioAccessCatalogueHTTP, CatalogueHTTPError, MAX_PAGE_INDEX,
)

PROVIDER_ID = "biblioaccess-catalogue.la-rochelle"

DESCRIPTOR = ProviderDescriptor(
    id=PROVIDER_ID,
    display_name="La Rochelle — catalogue numérique",
    description="Recherche publique dans le catalogue numérique des médiathèques de La Rochelle.",
    state="ready",
    capabilities=ProviderCapabilities(search=True, holdings=True),
    search_capabilities=ProviderSearchCapabilities(
        fields=("all",),
        scopes=("all", "digital", "catalog"),
        pagination="page", max_pages=MAX_PAGE_INDEX + 1,
        notes=(
            "Recherche plein texte côté BiblioAccess, notices du tenant 1409 uniquement.",
            "Page 0 à 100 ; le catalogue utilise ses propres liens de page suivante.",
            "La fiche d'un résultat donne les métadonnées structurées disponibles.",
            "Une notice de catalogue ne constitue jamais un droit d'emprunt.",
        ),
    ),
    homepage="https://www.biblioaccess.com/1409/",
    notes=("Catalogue public, aucun compte requis.",
           "Les prêts personnels relèvent de la source compte La Rochelle.",),
)


class LaRochelleCatalogueSession(ProviderSession):
    def __init__(self, *, client_factory=None, **kwargs):
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        self._client_factory = client_factory or BiblioAccessCatalogueHTTP
        self._catalogue = None
        self.last_search_warnings: list[str] = []

    def connect(self):
        if not self.connected:
            self._catalogue = self._client_factory()
            self.connected = True
        self._accounts = []  # Never synthesize a public borrower.
        return []

    def close(self):
        self.connected = False
        client, self._catalogue = self._catalogue, None
        if client is not None:
            client.close()
        self._accounts = []
        self.last_search_warnings = []

    def search(self, query: str, scope: str = "all", page: int = 0,
               field: str = "all") -> list[SearchResult]:
        rows, _ = self._search_with_observation(query, scope, page, field)
        return rows

    def _search_with_observation(self, query, scope, page, field):
        self.last_search_warnings = []
        if not self.connected or self._catalogue is None:
            raise ProviderError("biblioaccess_catalogue_not_connected")
        if type(field) is not str or field != "all":
            raise ProviderCapabilityError("biblioaccess_catalogue_field_unsupported")
        if type(scope) is not str or scope not in ("all", "digital", "catalog"):
            raise ProviderCapabilityError("biblioaccess_catalogue_scope_unsupported")
        if type(page) is not int or not 0 <= page <= MAX_PAGE_INDEX:
            raise ProviderCapabilityError("biblioaccess_catalogue_page_invalid")
        if type(query) is not str or len(query) > 512:
            raise ProviderCapabilityError("biblioaccess_catalogue_query_invalid")
        if not query.strip():
            return [], None
        try:
            observation = self._catalogue.search_page(query, page=page)
        except CatalogueHTTPError as exc:
            # The catalogue transport already supplies a closed technical
            # code, never an upstream body, URL or HTTP exception.
            raise ProviderError(str(exc)) from None
        if observation.status == "summary_html_unverified":
            raise ProviderError("biblioaccess_catalogue_search_unavailable")
        if observation.status == "summary_zero_attested":
            return [], observation
        self.last_search_warnings = [
            "Résultats publics partiels : le serveur ne garantit pas un catalogue exhaustif."
        ]
        names = dict(observation.book_titles)
        links = dict(observation.book_urls)
        return [
            SearchResult(
                id="biblioaccess:1409:book:" + book_id,
                record_id=book_id, docbase="BIBLIOACCESS",
                medium="digital",
                title=names.get(book_id) or ("Notice " + book_id),
                author=None, type="Livre numérique", format=None,
                publisher=None, published_at=None, description=None,
                thumbnail_url=None, friendly_url=links.get(book_id),
                scenario="BIBLIOACCESS_PUBLIC",
                source_provider=self.provider_id, source_credential=self.credential_id,
                source_collection="public_catalog",
                owned=False, downloadable=False, download_url=None,
                metadata={"tenant": "1409", "rights": "unverified",
                          "bibliographic_evidence": (
                              "search_link_text" if book_id in names else "book_link")},
            )
            for book_id in observation.book_ids
        ], observation

    def search_with_response_facets(
        self, query: str, scope: str = "all", *, field: str = "all",
        content_types: tuple[str, ...] = (),
    ):
        """Same-call public declarations; a page window is not a global zero.

        The legacy list may display an ID-derived fallback when the anchor
        has no visible label. Such a row is not a positive bibliography
        observation and must never become a durable C title.
        """
        from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
        from biblioaccess_observations import book_id_from_url

        rows, observation = self._search_with_observation(query, scope, 0, field)
        anonymous = (
            self.credential_id == "__public__"
            and type(self.credentials) is dict and not self.credentials
            and type(self._accounts) is list and not self._accounts
        )
        declarations = []
        if anonymous:
            for row in rows:
                if (row.metadata.get("bibliographic_evidence") != "search_link_text"
                        or book_id_from_url(row.friendly_url) != row.record_id):
                    continue
                # The Core still independently validates the anonymous
                # coordinates, public text and exact link. No URL is built
                # from the record ID, and no private session is imported.
                row.metadata["public_url_evidence"] = (
                    self.provider_id + ".public_search_record_identifier"
                )
                declarations.append(PublicSearchNotice(row))
        # No recognized record is not proof of an empty catalogue page.
        # Explicit clearing of the query is not a search attempt.
        zero = (observation is not None
                and observation.status == "summary_zero_attested"
                and observation.completeness == "complete"
                and not rows)
        partial = len(declarations) != len(rows) or (bool(query.strip()) and not rows and not zero)
        # Keep the warning for list-only callers; the typed response expresses
        # a bounded page independently of a missing bibliographic observation.
        self.last_search_warnings = (
            ["Résultats publics partiels : aucune notice reconnue."]
            if partial and not rows else
            ["Résultats publics partiels : notices sans preuve bibliographique."]
            if partial else []
        )
        return ProviderSearchResponse(
            results=rows, public_notices=tuple(declarations),
            partial=partial, catalogue_complete=zero if query.strip() else None,
        )

    def holdings(self, item: SearchResult, search_terms: str = "") -> ItemDetail:
        if not self.connected or self._catalogue is None:
            raise ProviderError("biblioaccess_catalogue_not_connected")
        if (type(item) is not SearchResult
                or item.source_provider != self.provider_id
                or item.source_credential != self.credential_id
                or item.source_collection != "public_catalog"):
            raise ProviderCapabilityError("biblioaccess_catalogue_item_unbound")
        try:
            notice = self._catalogue.read_observed_notice(item.record_id)
        except CatalogueHTTPError as exc:
            raise ProviderError(str(exc)) from None
        return ItemDetail(
            record_id=item.record_id, docbase=item.docbase,
            title=notice.title if notice else item.title,
            author=notice.author if notice else item.author,
            type=item.type, year=notice.published_at if notice else item.published_at,
            thumbnail_url=notice.image_url if notice else item.thumbnail_url,
            availability_status="unknown",
            availability_label="Catalogue numérique — disponibilité et droits non vérifiés",
            holdings=[], source_provider=self.provider_id,
            source_credential=self.credential_id,
        )


class LaRochelleCataloguePlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def activate(self, context):
        return self

    def create_session(self, *, credential_id, label, credentials, options,
                       config, storage, diagnostics):
        return LaRochelleCatalogueSession(
            credential_id=credential_id, label=label,
            credentials={}, options={},
        )


PLUGIN = LaRochelleCataloguePlugin()
