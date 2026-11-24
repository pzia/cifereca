"""#4030: one explicitly authorised anonymous catalogue comparison.

Core operator primitive. No private source, provider connection,
write to C, search UI projection, download, association, or right.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from unicodedata import category
from threading import Lock
from time import monotonic
import re
import httpx

from ..plugin_api import AuthenticationRequired, Cancelled, TemporaryUnavailable

from ..plugin_api import ProviderSearchResponse, PublicSearchNotice, ProviderSearchWithFacets
from .library_sqlite_freshness import sqlite_source_stamp
from .local_origin_suggestion_operator import LocalOriginSuggestionRequest
from .local_origin_suggestion_preview import _PublicComparisonEvidence, _verified_candidate
from .local_origin_epub_evidence import read_verified_epub_details, _isbn13
from .local_origin_documentary_suggestions import _fold
from .public_search_projection import project_public_search_results
from .search_orchestration import (
    _inert_search_source_state, _is_public_web_evidence, _public_catalog_url,
    _session_search_capabilities, _terminal_anonymous_empty,
)

_MAX_RESULTS = 50


@dataclass(frozen=True, slots=True)
class PublicCatalogueCandidate:
    provider_id: str
    record_id: str
    docbase: str
    medium: str
    title: str
    author: str | None
    media_format: str | None
    isbns: tuple[str, ...]
    comparison: str


def _source(service, request, provider):
    if not request.current() or not service._provider_enabled_for_identity(provider, request.identity_id):
        return None
    # The direct confirmed comparison must obey the same 256-session cap as
    # discovery.  A stale or oversized registry is no permission to search.
    registry = service._sessions
    if type(registry) is not dict or len(registry) > 256:
        return None
    sessions = [s for s in registry.values()
                if _inert_search_source_state(s) == (provider, "__public__", True)]
    if len(sessions) != 1:
        return None
    session = sessions[0]
    if (type(getattr(session, "credentials", None)) is not dict or session.credentials
            or type(getattr(session, "_accounts", None)) is not list or session._accounts
            or getattr(session, "_closed", False) is not False
            or getattr(session.capabilities, "search", None) is not True
            or not isinstance(session, ProviderSearchWithFacets)):
        return None
    caps = _session_search_capabilities(session)
    field = next((f for f in ("title", "all") if f in caps.fields), None)
    scope = next((s for s in ("catalog", "digital", "all") if s in caps.scopes), None)
    return (session, field, scope) if field and scope else None


@dataclass(frozen=True, slots=True, repr=False)
class PublicCatalogueChoice:
    request: LocalOriginSuggestionRequest
    provider_id: str
    session: object
    field: str
    scope: str
    evidence: _PublicComparisonEvidence


class _TerminalEmptyReviewCache:
    """One exact anonymous empty observation, never a negative rights proof.

    Activation-local, bounded to one entry, expires by monotonic time.
    A different scope, file, session, SQLite image or query is a miss.
    No stored provider payload, no persistence, no provider capability.
    """
    TTL_SECONDS = 30

    def __init__(self):
        self._lock = Lock()
        self._generation = 0
        self._entry = None

    def clear(self):
        with self._lock:
            self._generation += 1
            self._entry = None

    def probe(self, key, session):
        with self._lock:
            entry = self._entry
            now = monotonic()
            if entry is not None and not 0 <= now - entry[2] < self.TTL_SECONDS:
                self._entry = entry = None
            return (self._generation,
                    entry is not None and entry[0] == key and entry[1] is session)

    def remember(self, generation, key, session, observed):
        with self._lock:
            if generation == self._generation and 0 <= monotonic() - observed < self.TTL_SECONDS:
                self._entry = (key, session, observed)


def available_local_public_catalogues(service, request, *, evidence=None):
    """Bounded inert discovery after explicit known-C review; no provider call."""
    try:
        if (type(request) is not LocalOriginSuggestionRequest
                or request.service is not service or request.page != 0
                or type(evidence) is not _PublicComparisonEvidence
                or not evidence.matches_scope(request)
                or not request.current()):
            return ()
        registry = service._sessions
        if type(registry) is not dict or len(registry) > 256:
            return ()
        sessions = tuple(registry.values())
        ids = set()
        for session in sessions:
            state = _inert_search_source_state(session)
            if (state is not None and state[1:] == ("__public__", True)
                    and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", state[0])):
                ids.add(state[0])
        choices = []
        for provider in sorted(ids):
            selected = _source(service, request, provider)
            if selected is not None:
                choices.append(PublicCatalogueChoice(request, provider, *selected, evidence))
            if len(choices) >= 8:
                break
        return tuple(choices) if request.current() else ()
    except Exception:
        return ()


def _displayable_coordinate(value, *, optional=False):
    """Untrusted public coordinates are shown as origin hints in local UI."""
    if value is None:
        return optional
    return (type(value) is str and (optional or bool(value))
            and len(value) <= 512
            and not any(category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp")
                        for ch in value))


def _isbns(metadata):
    values = []
    for field in ("isbn", "isbns"):
        value = metadata.get(field)
        if type(value) is str:
            values.append(value)
        elif type(value) is list:
            if len(values) + len(value) > 32:
                return ()
            values.extend(value)
    if len(values) > 32:
        return ()
    return tuple(sorted({n for value in values if (n := _isbn13(value)) is not None}))


def _comparison(title, authors, local, row, remote):
    title_match = _fold(row.title) == _fold(title)
    author_match = len(authors) == 1 and bool(row.author) and _fold(row.author) == _fold(authors[0])
    if local and remote and set(local) & set(remote):
        if len(local) == len(remote) == 1 and title_match and author_match:
            return "isbn_title_author_concordant"
        return "isbn_claim_ambiguous"
    if local and remote:
        return "isbn_claim_divergent"
    if title_match and author_match:
        return "title_author_concordant"
    return "title_related" if not title_match else "title_concordant_author_unverified"


def _public_failure_status(exc):
    """Fixed transport outcomes, not supplier message forwarding or absence."""
    if isinstance(exc, Cancelled):
        return "cancelled"
    if isinstance(exc, AuthenticationRequired):
        return "auth_required"
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return {401: "auth_required", 403: "access_denied", 429: "rate_limited"}.get(
            exc.response.status_code, "unavailable")
    if type(exc) is TemporaryUnavailable and len(exc.args) == 1 and type(exc.args[0]) is str:
        return {
            "transport.http: auth_required": "auth_required",
            "transport.http: access_denied": "access_denied",
            "transport.http: rate_limited": "rate_limited",
        }.get(exc.args[0], "unavailable")
    return "unavailable"


def search_local_public_catalogue(service, request, *, provider_id, confirmed=False,
                                  choice=None, still_current=lambda: True, empty_cache=None):
    """One synchronous typed call; send title only, never path/bytes/ISBN.

    Each invocation requires explicit approval of the selected public source.
    Catalogue response is transient comparison evidence, not persisted C.
    Never claim complete coverage or network freshness (plugin cache possible).
    """
    called = False

    def report(status, candidates=(), *, window_status="unverified"):
        return {"status": status, "candidates": candidates,
                "window_status": window_status,
                "provider_access_performed": called, "coverage_complete": False,
                "fresh_network_verified": False, "association_authorized": False,
                "may_write_origin_or_right": False}

    if (confirmed is not True or type(request) is not LocalOriginSuggestionRequest
            or request.service is not service or request.page != 0
            or request.continuation is not None or type(provider_id) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", provider_id) is None):
        return report("excluded")
    try:
        if still_current() is not True:
            return report("excluded")
        selected = _source(service, request, provider_id)
        if selected is None:
            return report("excluded")
        session, field, scope = selected
        if choice is not None and (
                type(choice) is not PublicCatalogueChoice
                or choice.request is not request or choice.provider_id != provider_id
                or choice.session is not session
                or type(choice.evidence) is not _PublicComparisonEvidence
                or not choice.evidence.matches_scope(request)
                or (choice.field, choice.scope) != (field, scope)):
            return report("scope_changed")

        def live():
            current = _source(service, request, provider_id)
            return (still_current() is True and current is not None and current[0] is session
                    and current[1:] == (field, scope))

        with _verified_candidate(request.storage, library_root=request.root,
                                 identity_id=request.identity_id,
                                 placement_id=request.placement_id,
                                 catalogue=True) as (local, path, signature):
            # The user's consent is for the exact file previously reviewed,
            # not another valid EPUB now occupying the same placement.
            if choice is not None and (choice.evidence.evidence != local
                                       or choice.evidence.signature != signature):
                return report("scope_changed")
            if local[6] not in ("epub", "kepub"):
                return report("excluded")
            details = read_verified_epub_details(path, signature, include_source_claims=True)
            if details is None or not live():
                return report("excluded")
            title, authors, isbns, _, source_urls = details
            cache_key = cache_generation = None
            if type(empty_cache) is _TerminalEmptyReviewCache and choice is not None:
                stamp = sqlite_source_stamp(request.database)
                if stamp is not None:
                    cache_key = (request.identity_id, request.placement_id, request.root,
                                 request.source_epoch, request.navigation_epoch,
                                 id(request.storage), id(request.owner), id(request.config),
                                 provider_id, field, scope, title, local, signature, stamp)
                    cache_generation, hit = empty_cache.probe(cache_key, session)
                    if hit and live():
                        return report("partial", window_status="cached_declared_complete")
            observed = monotonic()
            called = True
            try:
                response = session.search_with_response_facets(
                    title, scope, field=field, content_types=(),
                )
            except Exception as exc:
                return report(_public_failure_status(exc) if live() else "scope_changed")
            if not live():
                return report("scope_changed")
            if (type(response) is not ProviderSearchResponse
                    or type(response.partial) is not bool
                    or type(response.catalogue_complete) not in (bool, type(None))
                    or type(response.results) is not list or len(response.results) > _MAX_RESULTS
                    or type(response.public_notices) is not tuple
                    or len(response.public_notices) > _MAX_RESULTS
                    or len({id(row) for row in response.results}) != len(response.results)):
                return report("response_unverified")
            projections = project_public_search_results(response)
            if projections is None:
                return report("response_unverified")
            claims = [id(notice.result) for notice in response.public_notices
                      if type(notice) is PublicSearchNotice]
            candidates = []
            source_matches = []
            for row in response.results:
                projected = projections.get(id(row))
                if (projected is None or claims.count(id(row)) != 1
                        or projected.source_provider != provider_id
                        or projected.source_credential != "__public__"
                        or projected.source_collection != "public_catalog"
                        or row.owned is not False or row.downloadable is not False
                        or row.source_account_id is not None or row.download_url is not None
                        or type(projected.title) is not str
                        or (projected.author is not None
                            and type(projected.author) is not str)
                        # The remote bibliographic result is advisory only.
                        # Reject C1/bidi/invisible characters that could forge
                        # an origin label in a local consent/comparison view.
                        or any(category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp")
                               for value in (projected.title, projected.author)
                               if value is not None for ch in value)
                        # These source coordinates are also rendered by the
                        # local comparison. General public C projection is
                        # deliberately unchanged: only this view rejects
                        # spoofed local provenance labels.
                        or not all(_displayable_coordinate(value) for value in (
                            projected.docbase, projected.record_id, projected.medium))
                        or not _displayable_coordinate(projected.format, optional=True)):
                    continue
                remote = _isbns(projected.metadata)
                comparison = _comparison(title, authors, isbns, projected, remote)
                # Only a literal URL admitted for this exact public parser
                # row can corroborate an OPF claim. The URL never becomes a
                # lookup argument or a link exposed by this review.
                if (len(source_urls) == 1 and type(row.friendly_url) is str
                        and row.friendly_url in source_urls):
                    evidence = row.metadata.get("public_url_evidence")
                    if (_is_public_web_evidence(evidence, provider_id)
                            and _public_catalog_url(
                                row.friendly_url, evidence=evidence, provider_id=provider_id,
                                record_id=projected.record_id,
                            ) == row.friendly_url):
                        source_matches.append(len(candidates))
                candidates.append(PublicCatalogueCandidate(
                    projected.source_provider, projected.record_id, projected.docbase,
                    projected.medium, projected.title, projected.author,
                    projected.format, remote, comparison,
                ))
            # A matching ISBN does not identify one edition when the same
            # public response claims it for multiple records or formats.
            # Keep every candidate and downgrade only the otherwise exact
            # descriptive claim; do not merge editions or select a winner.
            # Repeated source coordinates do not prove that two independently
            # returned public objects designate the same edition. The local
            # view intentionally omits unverified source revision/row IDs.
            # Counting individual occurrences (not distinct projected keys)
            # therefore refuses an exact-edition claim for *either* twin.
            by_isbn = defaultdict(int)
            # An excluded or undeclared row can still make a returned ISBN
            # claim ambiguous. Use these values only as a veto, never as
            # display evidence or authority for that inadmissible row.
            for raw in response.results:
                if type(raw.metadata) is dict:
                    for isbn in _isbns(raw.metadata):
                        by_isbn[isbn] += 1
            candidates = tuple(
                replace(candidate, comparison="isbn_claim_ambiguous")
                if (candidate.comparison == "isbn_title_author_concordant"
                    and any(by_isbn[isbn] > 1 for isbn in candidate.isbns))
                else candidate for candidate in candidates)
            # Keep a shared URL ambiguous across editions. A URL alone also
            # cannot override contradictory bibliographic evidence.
            # A second returned row claiming the same literal URL is
            # ambiguous even if its marker is missing or it was excluded
            # from the display projection. Do not upgrade the surviving
            # row by silently discarding contradictory response evidence.
            literal_occurrences = sum(
                1 for row in response.results
                if type(row.friendly_url) is str and row.friendly_url in source_urls
            )
            if len(source_matches) == 1 and literal_occurrences == 1:
                index = source_matches[0]
                candidate = candidates[index]
                if candidate.comparison in (
                    "isbn_title_author_concordant", "title_author_concordant",
                ):
                    candidates = (
                        *candidates[:index],
                        replace(candidate, comparison="opf_source_url_concordant"),
                        *candidates[index + 1:],
                    )
            # Explicit evidence order only within this one public window.
            # Keep supplier order for ties and all ambiguous editions; no
            # ranking here proves source, acquisition, licence or freshness.
            evidence_rank = {
                "opf_source_url_concordant": -1,
                "isbn_title_author_concordant": 0,
                "title_author_concordant": 1,
                "isbn_claim_ambiguous": 2,
                "title_concordant_author_unverified": 3,
                "title_related": 4,
                "isbn_claim_divergent": 5,
            }
            candidates = tuple(sorted(
                candidates, key=lambda item: evidence_rank.get(item.comparison, 6)))
            # The producer can qualify its one response, never the whole
            # catalogue. An omitted or inadmissible row prevents a complete
            # verdict even when the producer declared one.
            if (response.partial or response.catalogue_complete is False
                    or len(candidates) != len(response.results)
                    or len(claims) != len(response.results)):
                window_status = "incomplete"
            elif response.catalogue_complete is True:
                window_status = "declared_complete"
            else:
                window_status = "unverified"
            result = report("partial", candidates, window_status=window_status)
        if not live():
            return report("scope_changed")
        if (cache_key is not None and _terminal_anonymous_empty(response, session)
                and result["window_status"] == "declared_complete"
                and sqlite_source_stamp(request.database) == cache_key[-1]):
            empty_cache.remember(cache_generation, cache_key, session, observed)
        return result if live() else report("scope_changed")
    except Exception:
        # Never expose supplier exceptions, account details or private path.
        return report("unavailable" if called else "excluded")
