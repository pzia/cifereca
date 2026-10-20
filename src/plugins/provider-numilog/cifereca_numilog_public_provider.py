"""Numilog commercial catalogue: independent anonymous public provider.

The authenticated Numilog store (provider_id=numilog) remains exclusively in
cifereca_numilog. This provider never constructs that session, reads its
cookies, infers ownership, or implements artifact acquisition.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
import threading
import time

from cifereca.plugin_api import (
    ProviderCapabilities, ProviderCapabilityError, ProviderDescriptor,
    ItemDetail, ProviderError, ProviderPlugin, ProviderSearchCapabilities, ProviderSession,
)
from cifereca_numilog_public_client import (
    fetch_public_search, fetch_public_search_url, public_search_url,
)
from cifereca_numilog_public import PublicSearchPage, PublicSearchParseError, _query

# Navigation URLs are server-attested capability links: they are not arbitrary
# user-provided endpoints or unchecked faceted query parameters.
_NAVIGATION_TTL_SECONDS = 60.0
_MAX_NAVIGATION_LINKS = 512


DESCRIPTOR = ProviderDescriptor(
    id="numilog-catalog",
    display_name="Numilog — Catalogue public",
    description="Catalogue commercial Numilog anonyme, distinct des livres achetés.",
    state="ready",
    capabilities=ProviderCapabilities(search=True, holdings=True),
    search_capabilities=ProviderSearchCapabilities(
        fields=("all",),
        scopes=("all", "digital"),
        pagination="page",
        page_size=20,
        max_pages=101,
        notes=(
            "Recherche anonyme publique brSimple : seule la recherche textuelle générale est attestée.",
            "Pagination GET GRID_PAGE et facettes publiques observées ; navigation explicite sur liens attestés seulement.",
            "Les résultats sont des offres commerciales ; aucun achat, droit, fichier ni disponibilité de téléchargement n'est déduit.",
        ),
    ),
    homepage="https://www.numilog.com/",
    notes=(
        "Catalogue public séparé du compte Numilog et de Mes achats.",
        "Accès HTTP direct, conditions de robots et pages suivantes restent à qualifier.",
    ),
)


class NumilogPublicSession(ProviderSession):
    def __init__(self, **kwargs):
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        from cifereca_numilog_public_cache import PublicCatalogueCache
        self._public_cache = PublicCatalogueCache()
        self._navigation_lock = threading.Lock()
        self._navigation_epoch = 0
        self._navigation_urls = OrderedDict()
        if self.credentials:
            raise ProviderCapabilityError("Numilog public : credentials_forbidden")

    def _reset_navigation(self):
        with self._navigation_lock:
            self._navigation_epoch += 1
            self._navigation_urls.clear()

    def connect(self):
        # A public provider has no account and no network handshake.
        self._public_cache.clear()
        self._reset_navigation()
        self.connected = True
        self._accounts = []
        return []

    def close(self):
        self.connected = False
        self._public_cache.clear()
        self._reset_navigation()
        self._accounts = []

    def holdings(self, item, search_terms=""):
        """Display an already-retrieved public listing without ANY network I/O.

        Core may call holdings automatically when a search row is selected.
        Product enrichment remains a separately requested operation: no N+1,
        login, cookies, purchase lookup, fulfillment or implied ownership.
        """
        if not self.connected:
            raise ProviderError("Numilog public : catalogue non connecté")
        from cifereca_numilog_public_detail import validate_public_detail_item
        validate_public_detail_item(item)
        if (getattr(item, "scenario", None) != "NUMILOG_PUBLIC"
                or getattr(item, "docbase", None) != "NUMILOG"
                or type(getattr(item, "title", None)) is not str
                or not item.title.strip()):
            raise ProviderCapabilityError("Numilog public : detail_evidence_conflict")
        return ItemDetail(
            record_id=item.record_id,
            docbase="NUMILOG",
            title=item.title,
            author=item.author,
            type=item.type,
            year=item.published_at,
            thumbnail_url=item.thumbnail_url,
            availability_status="unknown",
            availability_label="Catalogue Numilog — achat non attesté",
            holdings=[],
            source_provider=self.provider_id,
            source_credential=self.credential_id,
        )

    def enrich_public_detail(self, item):
        """Explicit detail request only, not part of connect/search/sync."""
        # Pin the generation BEFORE validating the caller's row. A reconnect
        # during validation must not bless a stale request with the new epoch.
        epoch = self._navigation_epoch_snapshot()
        if not self.connected:
            raise ProviderError("Numilog public : catalogue non connecté")
        from cifereca_numilog_public_client import fetch_public_detail
        from cifereca_numilog_public_detail import (
            validate_public_detail_item, merge_public_detail,
        )
        url = validate_public_detail_item(item)  # No HTTP for foreign/private rows.
        self._assert_public_generation(epoch)
        detail = self._public_cache.get(("detail", url), lambda: fetch_public_detail(url))
        result = merge_public_detail(item, detail)
        self._assert_public_generation(epoch)
        return result

    def enrich_loaded_page_product(self, page, record_id):
        """Explicitly enrich ONE public search card, retaining page provenance.

        This performs at most one budgeted anonymous product GET through the
        existing cache. It never eagerly loads other cards to answer an ISBN
        filter or changes the page's unfiltered public total/pagination.
        """
        if not self.connected:
            raise ProviderError("Numilog public : catalogue non connecté")
        from copy import deepcopy
        from cifereca_numilog_public_local_filter import _validate_page
        _validate_page(page)
        if type(record_id) is not str:
            raise ProviderCapabilityError("Numilog public : unverified_product_selection")
        indices = [
            index for index, item in enumerate(page.items)
            if item.record_id == record_id
        ]
        if len(indices) != 1:
            raise ProviderCapabilityError("Numilog public : unverified_product_selection")
        epoch = self._navigation_epoch_snapshot()
        # This operation intentionally does NOT loop over page.items.
        item = self.enrich_public_detail(page.items[indices[0]])
        with self._navigation_lock:
            if not self.connected or self._navigation_epoch != epoch:
                raise ProviderError("Numilog public : navigation_invalidated")
        updated = list(deepcopy(page.items))
        updated[indices[0]] = deepcopy(item)
        return replace(page, items=tuple(updated))

    def _validate_search(self, query, page, field, scope):
        if type(field) is not str or field != "all":
            raise ProviderCapabilityError("Numilog public : unsupported_field")
        if type(scope) is not str or scope not in {"all", "digital"}:
            raise ProviderCapabilityError("Numilog public : unsupported_scope")
        if not self.connected:
            raise ProviderError("Numilog public : catalogue non connecté")
        return public_search_url(query, page)  # Before cache, broker or HTTP.

    def _get_page(self, query, page, field, url):
        return self._public_cache.get(
            ("search", url, field),
            lambda: fetch_public_search(query, page=page, field=field),
        )

    def _public_rows(self, rows):
        # A public result can never be passed into an owned download session.
        return tuple(
            replace(row, source_provider=self.provider_id,
                    source_credential="__public__", source_collection="public_catalog",
                    owned=False, downloadable=False, download_url=None)
            for row in rows
        )

    def search(self, query, scope="all", page=0, field="all"):
        # Capture BEFORE validation: close/reconnect during validation cannot
        # silently turn a request from the old generation into a new request.
        epoch = self._navigation_epoch_snapshot()
        url = self._validate_search(query, page, field, scope)
        self._assert_public_generation(epoch)
        rows = list(self._public_rows(self._get_page(query, page, field, url).items))
        # The generic Core search path must be protected just like browse()
        # and follow_public_link() when a session changes during a cache hit,
        # conversion or transport completion. No extra HTTP request.
        self._assert_public_generation(epoch)
        return rows

    def _assert_public_generation(self, expected):
        with self._navigation_lock:
            if not self.connected or self._navigation_epoch != expected:
                raise ProviderError("Numilog public : navigation_invalidated")

    def _navigation_epoch_snapshot(self):
        with self._navigation_lock:
            return self._navigation_epoch

    def _register_public_page(self, page, epoch):
        """Expose a verified page and short-lived server-issued facet/pager links."""
        if type(page) is not PublicSearchPage:
            raise ProviderError("Numilog public : unverified_page")
        links = ([page.next_url] if page.next_url else [])
        links.extend(p.get("url") for p in page.pagination_links)
        links.extend(f.get("url") for f in page.facets)
        if len(links) > _MAX_NAVIGATION_LINKS:
            raise ProviderError("Numilog public : navigation_too_large")
        # Parser-validated page links must still respect the LIVE request budget
        # (<= 100 pages, <= 200 chars query), regardless of parser's wider bounds.
        try:
            for url in links:
                parsed = _query(url)
                public_search_url(parsed["query"], int(parsed.get("GRID_PAGE", "0")))
        except (PublicSearchParseError, ProviderCapabilityError, ValueError, TypeError):
            raise ProviderError("Numilog public : unverified_navigation") from None
        result = replace(page, items=self._public_rows(page.items))
        with self._navigation_lock:
            if not self.connected or self._navigation_epoch != epoch:
                raise ProviderError("Numilog public : navigation_invalidated")
            now = time.monotonic()
            for expired in [k for k, deadline in self._navigation_urls.items()
                            if deadline <= now]:
                del self._navigation_urls[expired]
            for url in links:
                self._navigation_urls[url] = now + _NAVIGATION_TTL_SECONDS
                self._navigation_urls.move_to_end(url)
            while len(self._navigation_urls) > _MAX_NAVIGATION_LINKS:
                self._navigation_urls.popitem(last=False)
        return result

    def browse_public_search(self, query, *, page=0, field="all", scope="all"):
        """Explicit search with actual total/facets/pager, not a fake local filter.

        The generic Core search() still yields list[SearchResult]. This
        opt-in method gives a UI/plugin caller the source-attested navigation
        links without claiming server search-by-author/ISBN.
        """
        # Same pre-validation generation fence as generic search(): a
        # close/reconnect during validation must not authorize an old browse
        # under the new session, even when the result is already cached.
        epoch = self._navigation_epoch_snapshot()
        url = self._validate_search(query, page, field, scope)
        self._assert_public_generation(epoch)
        page_result = self._get_page(query, page, field, url)
        return self._register_public_page(page_result, epoch)

    def refine_loaded_page(self, page, **filters):
        """Explicit local refinement of a previously loaded public page.

        No server-side field filter, extra HTTP or global filtered total.
        Missing published evidence is counted as uncertain, never discarded
        as a proven negative. Page/filters remain separate from account rights.
        """
        if not self.connected:
            raise ProviderError("Numilog public : catalogue non connecté")
        from cifereca_numilog_public_local_filter import refine_loaded_public_page
        return refine_loaded_public_page(page, **filters)

    def refine_loaded_pages(self, pages, **filters):
        """Refine up to eight already-fetched consecutive public pages.

        This is explicitly offline, and MUST NOT imply server-side field
        search or a filtered global total. Mixed query/facet/edition sets,
        gaps, duplicate items and changing public counters are rejected.
        """
        if not self.connected:
            raise ProviderError("Numilog public : catalogue non connecté")
        from cifereca_numilog_public_multi_refinement import refine_loaded_public_pages
        return refine_loaded_public_pages(pages, **filters)

    def follow_public_link(self, url):
        """Follow ONLY an unexpired facet/pager link returned by browse/follow.

        This never accepts an arbitrary URL, a handcrafted filter value, a
        purchase link, an authenticated endpoint or a signed download link.
        Every request passes through the same anonymous cache and budget.
        """
        if not self.connected:
            raise ProviderError("Numilog public : catalogue non connecté")
        if type(url) is not str or len(url) > 4096:
            raise ProviderCapabilityError("Numilog public : unverified_navigation")
        with self._navigation_lock:
            epoch = self._navigation_epoch
            expires = self._navigation_urls.get(url)
            if expires is None or expires <= time.monotonic():
                raise ProviderCapabilityError("Numilog public : unverified_navigation")
        page_result = self._public_cache.get(
            ("search-url", url, "all"),
            lambda: fetch_public_search_url(url),
        )
        return self._register_public_page(page_result, epoch)


class NumilogPublicPlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def create_session(self, *, credential_id, label, credentials, options,
                       config, storage, diagnostics):
        return NumilogPublicSession(
            credential_id=credential_id, label=label,
            credentials=credentials, options=options,
        )


PLUGIN = NumilogPublicPlugin()
