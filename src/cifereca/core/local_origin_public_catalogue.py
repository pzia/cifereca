"""#4030: one explicitly authorised anonymous catalogue comparison.

Unwired Core operator primitive. No private source, provider connection,
write to C, search UI projection, download, association, or right.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from ..plugin_api import ProviderSearchResponse, PublicSearchNotice, ProviderSearchWithFacets
from .local_origin_suggestion_operator import LocalOriginSuggestionRequest
from .local_origin_suggestion_preview import _verified_candidate
from .local_origin_epub_evidence import read_verified_epub_bibliography, _isbn13
from .local_origin_documentary_suggestions import _fold
from .public_search_projection import project_public_search_results
from .search_orchestration import _inert_search_source_state, _session_search_capabilities

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
    sessions = [s for s in service._sessions.values()
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


def _isbns(metadata):
    values = []
    for field in ("isbn", "isbns"):
        value = metadata.get(field)
        if type(value) is str:
            values.append(value)
        elif type(value) is list:
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


def search_local_public_catalogue(service, request, *, provider_id, confirmed=False):
    """One synchronous typed call; send title only, never path/bytes/ISBN.

    Each invocation requires explicit approval of the selected public source.
    Catalogue response is transient comparison evidence, not persisted C.
    Never claim complete coverage or network freshness (plugin cache possible).
    """
    called = False

    def report(status, candidates=()):
        return {"status": status, "candidates": candidates,
                "provider_access_performed": called, "coverage_complete": False,
                "fresh_network_verified": False, "association_authorized": False,
                "may_write_origin_or_right": False}

    if (confirmed is not True or type(request) is not LocalOriginSuggestionRequest
            or request.service is not service or request.page != 0
            or request.continuation is not None or type(provider_id) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", provider_id) is None):
        return report("excluded")
    try:
        selected = _source(service, request, provider_id)
        if selected is None:
            return report("excluded")
        session, field, scope = selected

        def live():
            current = _source(service, request, provider_id)
            return (current is not None and current[0] is session
                    and current[1:] == (field, scope))

        with _verified_candidate(request.storage, library_root=request.root,
                                 identity_id=request.identity_id,
                                 placement_id=request.placement_id,
                                 catalogue=True) as (local, path, signature):
            if local[6] not in ("epub", "kepub"):
                return report("excluded")
            details = read_verified_epub_bibliography(path, signature)
            if details is None or not live():
                return report("excluded")
            title, authors, isbns = details
            called = True
            response = session.search_with_response_facets(
                title, scope, field=field, content_types=(),
            )
            if not live():
                return report("scope_changed")
            if (type(response) is not ProviderSearchResponse
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
            for row in response.results:
                projected = projections.get(id(row))
                if (projected is None or claims.count(id(row)) != 1
                        or projected.source_provider != provider_id
                        or projected.source_credential != "__public__"
                        or projected.source_collection != "public_catalog"
                        or row.owned is not False or row.downloadable is not False
                        or row.source_account_id is not None or row.download_url is not None):
                    continue
                remote = _isbns(projected.metadata)
                candidates.append(PublicCatalogueCandidate(
                    projected.source_provider, projected.record_id, projected.docbase,
                    projected.medium, projected.title, projected.author,
                    projected.format, remote, _comparison(title, authors, isbns, projected, remote),
                ))
            result = report("partial", tuple(candidates))
        return result if live() else report("scope_changed")
    except Exception:
        # Never expose supplier exceptions, account details or private path.
        return report("unavailable" if called else "excluded")
