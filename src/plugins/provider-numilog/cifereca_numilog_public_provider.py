"""Numilog commercial catalogue: independent anonymous public provider.

The authenticated Numilog store (provider_id=numilog) remains exclusively in
cifereca_numilog. This provider never constructs that session, reads its
cookies, infers ownership, or implements artifact acquisition.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, replace
import threading
import time

from cifereca.plugin_api import (
    ProviderCapabilities, ProviderCapabilityError, ProviderDescriptor,
    ItemDetail, ProviderError, ProviderPlugin, ProviderSearchCapabilities, ProviderSession,
)
from cifereca_numilog_public_client import (
    fetch_public_search, fetch_public_search_url, public_search_url,
    _RetainedSearchPage, _parse_retained_search_input, _transport_digest,
)
from cifereca_numilog_public import (
    PublicSearchPage, PublicSearchParseError, _query,
    requested_public_formats, filter_public_formats,
)

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


@dataclass(frozen=True, slots=True, repr=False)
class _ReservedPublicRefresh:
    execute: object
    release: object
    hold_publication: object


class NumilogPublicSession(ProviderSession):
    def __init__(self, *, _retain_public_search_inputs=False, **kwargs):
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        from cifereca_numilog_public_cache import PublicCatalogueCache
        self._public_cache = PublicCatalogueCache()
        self._navigation_lock = threading.Lock()
        self._navigation_epoch = 0
        self._navigation_urls = OrderedDict()
        if self.credentials:
            raise ProviderCapabilityError("Numilog public : credentials_forbidden")
        if (type(_retain_public_search_inputs) is not bool
                or (_retain_public_search_inputs and self.credential_id != "__public__")):
            raise ProviderCapabilityError("Numilog public : replay_scope_forbidden")
        self._retain_public_search_inputs = _retain_public_search_inputs

    def _reset_navigation_locked(self):
        self._navigation_epoch += 1
        self._navigation_urls.clear()

    def _reset_navigation(self):
        with self._navigation_lock:
            self._reset_navigation_locked()

    def connect(self):
        # One order for session/cache generation and the publication guard:
        # navigation -> public cache. Neither lock surrounds a network call.
        with self._navigation_lock:
            self._public_cache.clear()
            self._reset_navigation_locked()
            self.connected = True
            self._accounts = []
        return []

    def close(self):
        with self._navigation_lock:
            self.connected = False
            self._public_cache.clear()
            self._reset_navigation_locked()
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

    purchase_enrichment_source_provider_id = "numilog"

    def purchase_enrichment_public_response(self, product_url, expected_title, *, check_authority, control):
        """Qualified anonymous response, requiring the late Core start fence."""
        if not callable(getattr(check_authority, "hold_transport_start", None)):
            raise ProviderCapabilityError("Numilog public : purchase_start_fence_required")
        return self._purchase_enrichment_response(
            product_url, expected_title, check_authority=check_authority, control=control,
        )

    def _guarded_purchase_detail(self, product_url, expected_title, *, check_authority):
        """Private composition port; shared anonymous session/cache and timestamp.

        Return raw public detail, actual load time, and a publication guard
        that pins THIS anonymous session generation. Never open a session,
        reread inventory, request a second GET, or grant purchase rights.
        """
        from contextlib import contextmanager
        from cifereca_numilog_public_client import fetch_guarded_purchase_detail
        generation = self._navigation_epoch_snapshot()

        def checked():
            if check_authority() is not True:
                return False
            self._assert_public_generation(generation)
            return (self.credential_id == "__public__"
                    and not self.credentials and not self._accounts)

        start = getattr(check_authority, "hold_transport_start", None)
        if callable(start):
            @contextmanager
            def hold_provider_start():
                with start() as allowed:
                    if allowed is not True or not self._navigation_lock.acquire(blocking=False):
                        raise ProviderCapabilityError("Numilog public : purchase_authority_revoked")
                    try:
                        if (not self.connected or self._navigation_epoch != generation
                                or self.credential_id != "__public__"
                                or self.credentials or self._accounts):
                            raise ProviderCapabilityError("Numilog public : purchase_authority_revoked")
                        yield True
                    finally:
                        self._navigation_lock.release()
            checked.hold_transport_start = hold_provider_start

        if not checked():
            raise ProviderCapabilityError("Numilog public : purchase_authority_revoked")
        detail, observed_at = fetch_guarded_purchase_detail(
            self._public_cache, product_url, expected_title,
            check_authority=checked, with_observation=True,
        )
        if not checked():
            raise ProviderCapabilityError("Numilog public : purchase_authority_revoked")

        @contextmanager
        def hold_publication():
            # Core already holds its policy/lease guard. Nonblocking to
            # avoid waiting for any ongoing provider operation at commit.
            if not self._navigation_lock.acquire(blocking=False):
                raise ProviderError("Numilog public : purchase_publication_revoked")
            try:
                if (not self.connected or self._navigation_epoch != generation
                        or self.credential_id != "__public__"
                        or self.credentials or self._accounts):
                    raise ProviderError("Numilog public : purchase_publication_revoked")
                yield
            finally:
                self._navigation_lock.release()

        return detail, observed_at, hold_publication

    def _purchase_enrichment_response(self, product_url, expected_title, *,
                                      check_authority, control):
        """Qualified producer port for Core's same-read enrichment sink.

        The public result is made exclusively from parsed public facts.
        The private comparison title is NOT passed into the projector.
        No discovery, fallback, second GET or private session is involved.
        """
        from cifereca_numilog_public_detail import purchase_public_detail_response

        control.check_cancelled()
        from cifereca.plugin_api import PurchaseEnrichmentDeferred
        from cifereca_numilog_public_client import PublicRateLimitError, PublicServiceUnavailableError
        try:
            detail, observed_at, hold_publication = self._guarded_purchase_detail(
                product_url, expected_title, check_authority=check_authority,
            )
        except ProviderError as exc:
            if type(exc) is PublicServiceUnavailableError:
                reason = "unavailable"
            elif type(exc) is PublicRateLimitError or str(exc) == "Numilog : public_catalog_rate_limited":
                reason = "rate_limited"
            elif str(exc) in ("Numilog : public_catalog_request_in_progress",
                               "Numilog : public_catalog_request_budget_exhausted"):
                reason = "budget_busy"
            elif str(exc) == "Numilog : public_detail_network_unavailable":
                reason = "unavailable"
            else:
                raise
            if check_authority() is not True:
                raise ProviderCapabilityError("Numilog public : purchase_authority_revoked") from None
            delay = self._public_cache.purchase_retry_delay()
            if reason == "unavailable":
                delay = max(60, delay)
            raise PurchaseEnrichmentDeferred(reason, delay) from None
        response = purchase_public_detail_response(detail, product_url=product_url)
        control.check_cancelled()
        if check_authority() is not True:
            raise ProviderCapabilityError("Numilog public : purchase_authority_revoked")
        return response, observed_at, hold_publication

    def enrich_loaded_page_product(self, page, record_id):
        """Explicitly enrich ONE public search card, retaining page provenance.

        This performs at most one budgeted anonymous product GET through the
        existing cache. It never eagerly loads other cards to answer an ISBN
        filter or changes the page's unfiltered public total/pagination.
        """
        # Capture before page validation: a disconnect/reconnect while
        # validating must not re-authorize an old selection under the new
        # generation, including a cached anonymous product response.
        epoch = self._navigation_epoch_snapshot()
        if not self.connected:
            raise ProviderError("Numilog public : catalogue non connecté")
        from copy import deepcopy
        from cifereca_numilog_public_local_filter import _validate_page
        _validate_page(page)
        self._assert_public_generation(epoch)
        if type(record_id) is not str:
            raise ProviderCapabilityError("Numilog public : unverified_product_selection")
        indices = [
            index for index, item in enumerate(page.items)
            if item.record_id == record_id
        ]
        if len(indices) != 1:
            raise ProviderCapabilityError("Numilog public : unverified_product_selection")
        # This operation intentionally does NOT loop over page.items.
        item = self.enrich_public_detail(page.items[indices[0]])
        with self._navigation_lock:
            if not self.connected or self._navigation_epoch != epoch:
                raise ProviderError("Numilog public : navigation_invalidated")
        updated = list(deepcopy(page.items))
        updated[indices[0]] = deepcopy(item)
        self._assert_public_generation(epoch)
        return replace(page, items=tuple(updated))

    def _validate_search(self, query, page, field, scope):
        if type(field) is not str or field != "all":
            raise ProviderCapabilityError("Numilog public : unsupported_field")
        if type(scope) is not str or scope not in {"all", "digital"}:
            raise ProviderCapabilityError("Numilog public : unsupported_scope")
        if not self.connected:
            raise ProviderError("Numilog public : catalogue non connecté")
        return public_search_url(query, page)  # Before cache, broker or HTTP.

    def _get_page_entry(self, query, page, field, url):
        def load():
            if self._retain_public_search_inputs:
                return fetch_public_search(query, page=page, field=field, _retain_input=True)
            return fetch_public_search(query, page=page, field=field)
        return self._public_cache.get(("search", url, field), load)

    def _get_page(self, query, page, field, url):
        value = self._get_page_entry(query, page, field, url)
        return value.page if type(value) is _RetainedSearchPage else value

    def _search_retained_page(self, query, scope="all", *, page=0, field="all"):
        """Trusted opt-in same-call rows + opaque input token (or None).

        This uses the ordinary anonymous search cache/admission path, not a
        second GET. It does not alter the public SearchResponse ABI or make
        the factory opt in. Only the explicit future Core producer should
        consume it, never GTK or a generic cache HIT of SearchResult DTOs.
        """
        if not self._retain_public_search_inputs:
            raise ProviderCapabilityError("Numilog public : replay_scope_forbidden")
        epoch = self._navigation_epoch_snapshot()
        url = self._validate_search(query, page, field, "all" if scope == "catalog" else scope)
        self._assert_public_generation(epoch)
        entry = self._get_page_entry(query, page, field, url)
        if type(entry) is not _RetainedSearchPage:
            raise ProviderError("Numilog : public_catalog_replay_invalid")
        rows = self._public_rows(entry.page.items)
        token = entry.raw_input.token if entry.raw_input is not None else None
        self._assert_public_generation(epoch)
        return rows, token, entry.page.complete

    def _search_with_retained_input(self, query, scope="all", *, page=0, field="all"):
        rows, token, _complete = self._search_retained_page(query, scope, page=page, field=field)
        return rows, token

    def _search_response_with_retained_input(self, query, scope="all", *, page=0, field="all"):
        """Experimental same-call envelope plus anonymous cache coordinate."""
        from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice

        rows, token, complete = self._search_retained_page(query, scope, page=page, field=field)
        return ProviderSearchResponse(
            results=list(rows), catalogue_complete=complete,
            public_notices=tuple(PublicSearchNotice(row) for row in rows),
        ), token

    def _read_retained_public_input(self, token):
        """Trusted worker byte receipt input; no fetch or lifetime extension.

        The returned TTL is a conservative, whole-second remaining bound.
        The caller must recheck the same token before publication and must
        not persist or log body/URL. Expiry is unknown, not a tombstone.
        """
        if (not self._retain_public_search_inputs
                or type(token) is not str or len(token) != 32
                or any(ch not in "0123456789abcdef" for ch in token)):
            raise ProviderCapabilityError("Numilog public : replay_scope_forbidden")
        epoch = self._navigation_epoch_snapshot()
        self._assert_public_generation(epoch)
        entry, ttl = self._public_cache._peek_retained_input(token)
        self._assert_public_generation(epoch)
        if entry is None or ttl < 1:
            return None, 0
        return entry.raw_input, ttl

    @contextmanager
    def _hold_retained_public_input(self, token):
        """Opt-in worker publication guard; no parser or HTTP under locks.

        This serializes cache eviction and the session lifecycle with an
        exact final publication. The caller must not enter with a Storage
        lock or call back into this session while holding the guard.
        Busy is a refusal, not permission to retry via network. The
        coordinator must release immediately after the final transaction.
        """
        if (not self._retain_public_search_inputs
                or type(token) is not str or len(token) != 32
                or any(ch not in "0123456789abcdef" for ch in token)):
            raise ProviderCapabilityError("Numilog public : replay_scope_forbidden")
        if not self._navigation_lock.acquire(blocking=False):
            raise ProviderError("Numilog : public_catalog_replay_busy")
        try:
            if (not self.connected or self.credential_id != "__public__"
                    or self.credentials or self._accounts):
                yield None
            else:
                with self._public_cache._hold_retained_input(token) as raw:
                    yield raw
        finally:
            self._navigation_lock.release()

    def _reserve_exact_public_refresh(self, input_token, record_id, transport_digest,
                                      *, hold_start_authority):
        """Private opt-in refresh of ONE previously attested Search page.

        Caller is the trusted Core composition, not GTK or generic Search.
        It supplies current consent/policy via a guard, never a URL.
        The original entry must still be live; this is not a discovery
        endpoint, background TTL refresh or a cache-miss recovery.
        """
        if (not self._retain_public_search_inputs
                or type(input_token) is not str or len(input_token) != 32
                or any(ch not in "0123456789abcdef" for ch in input_token)
                or type(record_id) is not str or not 1 <= len(record_id) <= 160
                or type(transport_digest) is not str or len(transport_digest) != 64
                or not callable(hold_start_authority)):
            raise ProviderCapabilityError("Numilog : public_catalog_refresh_scope_forbidden")

        cache = self._public_cache
        if not self._navigation_lock.acquire(blocking=False):
            raise ProviderError("Numilog : public_catalog_refresh_busy")
        try:
            if (not self.connected or self.credential_id != "__public__"
                    or self.credentials or self._accounts):
                raise ProviderCapabilityError("Numilog : public_catalog_refresh_scope_forbidden")
            generation = self._navigation_epoch
            if not cache._lock.acquire(blocking=False):
                raise ProviderError("Numilog : public_catalog_refresh_busy")
            try:
                slot, epoch, key, raw, previous = cache._reserve_retained_refresh_locked(
                    input_token, record_id, transport_digest,
                )
            finally:
                cache._lock.release()
        finally:
            self._navigation_lock.release()

        lock = threading.Lock()
        phase = "reserved"
        started = False
        succeeded = False

        def release():
            nonlocal phase
            with lock:
                if phase != "reserved":
                    return
                phase = "done"
            cache._release_reserved(slot)

        def before_get(control):
            nonlocal started
            # External policy guard must not acquire Storage. Actual transport
            # start is linearized here, after HTTP client construction. A
            # revoke AFTER this point cannot unsend an admitted request.
            control.check_cancelled()
            with hold_start_authority() as allowed:
                if allowed is not True:
                    raise ProviderError("Numilog : public_catalog_refresh_revoked")
                with self._navigation_lock, cache._lock:
                    entry, ttl = cache._peek_retained_input_locked(input_token)
                    if (started or not self.connected
                            or self._navigation_epoch != generation
                            or self.credential_id != "__public__"
                            or self.credentials or self._accounts
                            or cache._epoch != epoch or cache._inflight is not slot
                            or entry is None or entry.raw_input != raw or ttl < 1
                            or _transport_digest(raw._url) != transport_digest):
                        raise ProviderError("Numilog : public_catalog_refresh_revoked")
                    control.check_cancelled()
                    started = True

        def execute(control):
            nonlocal phase, succeeded
            with lock:
                if phase != "reserved":
                    raise ProviderError("Numilog : public_catalog_refresh_consumed")
                phase = "running"
            try:
                def load():
                    fresh = fetch_public_search_url(
                        raw._url, _retain_input=True,
                        _before_get=lambda: before_get(control),
                    )
                    if not started or type(fresh) is not _RetainedSearchPage:
                        raise ProviderError("Numilog : public_catalog_refresh_invalid")
                    matches = [row for row in fresh.page.items if row.record_id == record_id]
                    if (len(matches) != 1
                            or any(getattr(matches[0], attr) != getattr(previous, attr)
                                   for attr in ("docbase", "medium", "revision_id", "type"))):
                        raise ProviderError("Numilog : public_catalog_refresh_coordinate_changed")
                    control.check_cancelled()
                    return fresh

                fresh = cache._run_reserved(key, slot, epoch, load)
                row, = [item for item in fresh.page.items if item.record_id == record_id]
                row, = self._public_rows((row,))
                self._assert_public_generation(generation)
                control.check_cancelled()
                from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
                response = ProviderSearchResponse(
                    results=[row], public_notices=(PublicSearchNotice(row),),
                )
                observed_at = int(time.time())
                with lock:
                    succeeded = True
                return response, observed_at
            finally:
                cache._release_reserved(slot)
                with lock:
                    phase = "done"

        @contextmanager
        def hold_publication():
            # Pin the exact anonymous session through the Core transaction.
            # No storage/transport calls here. The caller already pins the
            # Core policy and grant, keeping order policy -> session -> DB.
            with lock:
                ready = succeeded and phase == "done"
            if not ready or not self._navigation_lock.acquire(blocking=False):
                raise ProviderError("Numilog : public_catalog_refresh_revoked")
            try:
                if (not self.connected or self._navigation_epoch != generation
                        or self.credential_id != "__public__"
                        or self.credentials or self._accounts):
                    raise ProviderError("Numilog : public_catalog_refresh_revoked")
                yield
            finally:
                self._navigation_lock.release()

        return _ReservedPublicRefresh(execute, release, hold_publication)

    def _reparse_retained_public_row(self, token, record_id):
        """Run the existing offline parser on the exact retained input.

        Only a previously observed unique record may be selected. A changed
        edition coordinate is a refusal, not a new row or implicit fetch.
        Recheck the cache entry and session generation AFTER parsing.
        """
        if type(record_id) is not str or not record_id:
            raise ProviderCapabilityError("Numilog public : replay_scope_forbidden")
        epoch = self._navigation_epoch_snapshot()
        raw, _ttl = self._read_retained_public_input(token)
        if raw is None:
            return None
        entry, _ = self._public_cache._peek_retained_input(token)
        if entry is None:
            return None
        previous = [row for row in entry.page.items if row.record_id == record_id]
        if len(previous) != 1:
            return None
        page = _parse_retained_search_input(raw)
        matches = [row for row in page.items if row.record_id == record_id]
        if (len(matches) != 1 or matches[0].docbase != previous[0].docbase
                or matches[0].medium != previous[0].medium
                or matches[0].revision_id != previous[0].revision_id
                or matches[0].type != previous[0].type):
            raise ProviderError("Numilog : public_catalog_replay_coordinate_changed")
        latest, _ttl = self._read_retained_public_input(token)
        self._assert_public_generation(epoch)
        if latest != raw:
            return None
        return self._public_rows(matches)[0]

    def public_search_replay_capability(self):
        """Offer the cache-only contract, not permission to execute work."""
        from cifereca.plugin_api import (
            PublicSearchReplayInput, PublicSearchReplayCapability, PublicSearchReplayBusy,
        )
        import cifereca_numilog_public_client as client
        family = client._PARSER_FAMILY_DIGEST
        implementation = client._PARSER_IMPLEMENTATION_DIGEST

        def implementation_current():
            return (client._PARSER_FAMILY_DIGEST == family
                    and client._PARSER_IMPLEMENTATION_DIGEST == implementation)
        if (not self._retain_public_search_inputs or not self.connected
                or self.credential_id != "__public__" or self.credentials or self._accounts):
            return None
        epoch = self._navigation_epoch_snapshot()

        def normalize(raw):
            if raw is None:
                return None
            return PublicSearchReplayInput(
                raw.token, raw.transport_digest, raw.parser_family_digest,
                raw.parser_implementation_digest, raw._body,
            )

        def read(token):
            if not implementation_current():
                return None, 0
            try:
                self._assert_public_generation(epoch)
                raw, ttl = self._read_retained_public_input(token)
                self._assert_public_generation(epoch)
                return (normalize(raw), ttl) if implementation_current() else (None, 0)
            except ProviderError:
                return None, 0

        @contextmanager
        def hold(token):
            # Map only acquisition failures; never swallow an exception
            # raised by Core inside the publication guard.
            from contextlib import ExitStack
            with ExitStack() as stack:
                try:
                    # The underlying guard acquires navigation nonblocking.
                    # Check the captured generation only AFTER acquisition;
                    # a preliminary blocking generation check would hide busy.
                    raw = stack.enter_context(self._hold_retained_public_input(token))
                except ProviderError as exc:
                    if str(exc) == "Numilog : public_catalog_replay_busy":
                        raise PublicSearchReplayBusy("public_replay_busy") from None
                    raw = None
                if self._navigation_epoch != epoch or not implementation_current():
                    raw = None
                yield normalize(raw)

        def reparse(token, record_id):
            if not implementation_current():
                return None
            try:
                self._assert_public_generation(epoch)
                response = self._reparse_retained_public_response(token, record_id)
                self._assert_public_generation(epoch)
                return response if implementation_current() else None
            except ProviderError:
                return None

        return PublicSearchReplayCapability(
            family, implementation,
            read, hold, reparse,
        )

    def _reparse_retained_public_response(self, token, record_id):
        """Explicit public parser output, still subject to Core admission."""
        from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice

        row = self._reparse_retained_public_row(token, record_id)
        if row is None:
            return None
        return ProviderSearchResponse(results=[row], public_notices=(PublicSearchNotice(row),))

    def _public_rows(self, rows):
        # A public result can never be passed into an owned download session.
        projected = []
        for row in rows:
            metadata = dict(row.metadata)
            # The shared parser owns this exact public product-card proof.
            # Bind it to the actual anonymous producer, not the historical
            # authenticated provider ID. No proof from an arbitrary URL.
            evidence = metadata.pop("public_url_evidence", None)
            if evidence == "numilog.public_search_product_card":
                metadata["public_url_evidence"] = (
                    f"{self.provider_id}.public_search_product_card"
                )
            projected.append(replace(
                row, source_provider=self.provider_id,
                source_credential="__public__", source_collection="public_catalog",
                owned=False, downloadable=False, download_url=None, metadata=metadata,
            ))
        return tuple(projected)

    def _search_public_page(self, query, scope="all", page=0, field="all"):
        # Capture BEFORE validation: close/reconnect during validation cannot
        # silently turn a request from the old generation into a new request.
        epoch = self._navigation_epoch_snapshot()
        url = self._validate_search(query, page, field, scope)
        self._assert_public_generation(epoch)
        parsed = self._get_page(query, page, field, url)
        rows = list(self._public_rows(parsed.items))
        # The generic Core search path must be protected just like browse()
        # and follow_public_link() when a session changes during a cache hit,
        # conversion or transport completion. No extra HTTP request.
        self._assert_public_generation(epoch)
        return rows, parsed.complete

    def search(self, query, scope="all", page=0, field="all"):
        rows, _complete = self._search_public_page(query, scope, page, field)
        return rows

    def search_with_response_facets(
        self, query, scope="all", *, field="all", content_types=(),
    ):
        """Supply one SAME-CALL anonymous public envelope to federated Core.

        Core requires ProviderSearchResponse.public_notices for a session
        whose credential has no private SQL owner. The ordinary anonymous
        search already parsed these exact rows; do not add another HTTP GET,
        copy a previous response, or re-label a private purchase/inventory.
        Facets remain unverified for this generic Search API.
        """
        from cifereca.plugin_api import (
            ProviderSearchResponse, PublicSearchNotice,
        )
        # Federated Core requests a foreign/ownerless session with the
        # conservative "catalog" scope. This session has NO private content:
        # its only supported upstream operation is an anonymous catalogue.
        requested = requested_public_formats(content_types)
        normalized_scope = "all" if scope == "catalog" else scope
        body = implementation = retained_token = None
        if self._retain_public_search_inputs:
            rows, token, complete = self._search_retained_page(
                query, normalized_scope, field=field,
            )
            if token is not None:
                # The token was returned alongside these exact parsed rows.
                # No lookup by query, second GET or previous-response field.
                try:
                    with self._hold_retained_public_input(token) as raw:
                        if raw is not None and raw.token == token:
                            body = raw._body
                            implementation = raw.parser_implementation_digest
                            retained_token = raw.token
                except ProviderError as exc:
                    if str(exc) != "Numilog : public_catalog_replay_busy":
                        raise
                    # Optional evidence must not turn a valid Search into a failure.
                    body = implementation = retained_token = None
        else:
            rows, complete = self._search_public_page(query, normalized_scope, field=field)
        rows, partial, complete = filter_public_formats(rows, requested, complete)
        return ProviderSearchResponse(
            results=list(rows), partial=partial,
            catalogue_complete=complete,
            public_notices=tuple(PublicSearchNotice(result=row) for row in rows),
            notice_parse_input=body,
            notice_parser_implementation=implementation,
            notice_parse_input_token=retained_token,
        )

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
        epoch = self._navigation_epoch_snapshot()
        if not self.connected:
            raise ProviderError("Numilog public : catalogue non connecté")
        from cifereca_numilog_public_local_filter import refine_loaded_public_page
        result = refine_loaded_public_page(page, **filters)
        self._assert_public_generation(epoch)
        return result

    def refine_loaded_pages(self, pages, **filters):
        """Refine up to eight already-fetched consecutive public pages.

        This is explicitly offline, and MUST NOT imply server-side field
        search or a filtered global total. Mixed query/facet/edition sets,
        gaps, duplicate items and changing public counters are rejected.
        """
        epoch = self._navigation_epoch_snapshot()
        if not self.connected:
            raise ProviderError("Numilog public : catalogue non connecté")
        from cifereca_numilog_public_multi_refinement import refine_loaded_public_pages
        result = refine_loaded_public_pages(pages, **filters)
        self._assert_public_generation(epoch)
        return result

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
        # The attested link may have been invalidated just as the lock was
        # released. Refuse a stale GET/cache lookup after close/reconnect.
        self._assert_public_generation(epoch)
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
            _retain_public_search_inputs=(credential_id == "__public__" and not credentials),
        )


PLUGIN = NumilogPublicPlugin()
