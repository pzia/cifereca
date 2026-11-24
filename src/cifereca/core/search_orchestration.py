"""Internal search orchestration over the facade-owned sessions and cache.

Existing search/ranking and provider-stream helpers remain the implementations.
Executor/logger are injected from the facade, preserving concurrency limits,
callback delivery and test patchpoints without importing LibraryService.
"""
from __future__ import annotations

import time
import hashlib
import json
import inspect
import logging
import re
import unicodedata
from concurrent.futures import FIRST_COMPLETED, wait
from dataclasses import asdict, dataclass, field, replace
from contextvars import ContextVar
from datetime import datetime, timezone
from queue import Queue, Empty, Full
from typing import Any, Callable
from ..models import ItemDetail, SearchResult
from ..providers import ProviderSearchCapabilities, ProviderSession
from ..plugin_api import (
    CatalogSearchFacet, CatalogSearchFacetValue, ProviderSearchEvent,
    ProviderSearchResponse, ProviderSearchStream, ProviderSearchWithFacets,
)
from ..core.provider_stream import consume_provider_stream, SearchStreamFailure, SearchStreamCancelled
from .source_context import source_context_epoch


@dataclass(frozen=True, slots=True, repr=False)
class SearchReadScope:
    """Core-local reader authority, distinct from the GTK search projection."""
    service: Any = field(repr=False)
    identity: Any = field(repr=False)
    identity_id: str
    source_epoch: int

    def current(self, service) -> bool:
        try:
            return (self.service is service
                    and service.identity_by_id(self.identity_id) is self.identity
                    and source_context_epoch(service) == self.source_epoch)
        except Exception:
            return False


def capture_search_read_scope(service, identity_id: str) -> SearchReadScope:
    if (type(identity_id) is not str or not identity_id
            or len(identity_id) > 1024 or any(ch in identity_id for ch in "\x00\r\n")):
        raise ValueError("search_identity_required")
    epoch = source_context_epoch(service)
    identity = service.identity_by_id(identity_id)
    if identity is None or identity.identity_id != identity_id:
        raise ValueError("search_identity_unknown")
    scope = SearchReadScope(service, identity, identity_id, epoch)
    if not scope.current(service):
        raise ValueError("search_source_changed")
    return scope


def _search_policy_enabled(service, provider_id, request_scope=None):
    if request_scope is not None:
        return service._provider_enabled_for_identity(provider_id, request_scope.identity_id)
    return service._provider_enabled_for_identity(provider_id)


def _log_observed_stream_failure(provider_id: str, phase: str, error: Exception) -> None:
    """Keep original stream causes in the raw journal, without extra I/O.

    A misbehaving provider exception may itself raise from __str__; a logger
    must not turn an already failed search into an unhandled callback crash.
    The fallback captures the original class/args and Python call stack,
    without changing the public closed error code or performing another GET.
    """
    logger = logging.getLogger("cifereca")
    try:
        # Python's traceback formatter silently replaces an exception whose
        # __str__ raises with '<exception str() failed>'. Always record the
        # ORIGINAL args separately, even when traceback formatting succeeds.
        # Rendering those args eagerly also prevents a hostile __repr__ from
        # failing later inside a logging handler.
        try:
            observed_args = repr(error.args)
        except Exception:
            observed_args = "<unrenderable>"
        logger.warning(
            "TRACE provider search stream failure provider_id=%s stage=%s "
            "exception_type=%s exception_args=%s",
            provider_id, phase, type(error).__name__, observed_args,
            exc_info=(type(error), error, error.__traceback__),
        )
    except Exception:
        # Diagnostics must never turn a provider failure into a second crash.
        logger.warning(
            "TRACE provider search stream failure provider_id=%s stage=%s "
            "exception_type=%s journal_fallback=true",
            provider_id, phase, type(error).__name__,
        )


def _search_query_variants(cls, query: str) -> list[str]:
    """Return a small set of progressively looser catalogue queries.

    Several upstream catalogues treat ``"Zola Germinal"`` as a phrase or
    an AND expression and may return nothing although title and author are
    indexed separately.  Keep the exact query first, then try meaningful
    halves/tokens.  The set is deliberately bounded to avoid turning one UI
    search into an unbounded burst of provider calls.
    """
    original = re.sub(r"\s+", " ", str(query or "")).strip()[:180]
    if not original:
        return []
    raw_tokens = re.findall(r"[0-9A-Za-zÀ-ÖØ-öø-ÿŒœ'-]+", original)
    stop = {"a", "au", "aux", "d", "de", "des", "du", "et", "l", "la", "le", "les", "un", "une"}
    tokens = [t for t in raw_tokens if len(cls._history_fold(t)) >= 2 and cls._history_fold(t) not in stop]
    if len(tokens) < 2:
        return [original]

    variants: list[str] = [original]
    if len(tokens) == 2:
        # Longest term first: for "Zola Germinal", "Germinal" is normally
        # the most discriminating fallback.
        variants.extend(sorted(tokens, key=lambda value: (-len(value), tokens.index(value))))
    else:
        midpoint = max(1, len(tokens) // 2)
        variants.append(" ".join(tokens[:midpoint]))
        variants.append(" ".join(tokens[midpoint:]))
        variants.extend(sorted(tokens, key=lambda value: (-len(value), tokens.index(value)))[:2])

    out: list[str] = []
    seen: set[str] = set()
    for value in variants:
        value = re.sub(r"\s+", " ", value).strip()[:180]
        key = cls._history_fold(value)
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out[:5]


def _search_relevance(cls, query: str, row: SearchResult) -> int:
    tokens = [x for x in cls._history_fold(query).split() if len(x) >= 2]
    if not tokens:
        return 0
    title = cls._history_fold(row.title)
    author = cls._history_fold(row.author)
    combined = f"{title} {author}".strip()
    score = 0
    matched = sum(1 for token in tokens if token in combined)
    score += matched * 20
    score += sum(8 for token in tokens if token in title)
    score += sum(6 for token in tokens if token in author)
    if matched == len(tokens):
        score += 60
    folded_query = cls._history_fold(query)
    if folded_query and folded_query in combined:
        score += 30
    return score


def _normalized_language_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        values: list[Any] = []
        for key in ("code", "id", "value", "name"):
            if key in value:
                values.append(value[key])
        if not values:
            values.extend(value.values())
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = [value]
    out: list[str] = []
    for raw in values:
        text = str(raw or "").strip().lower().replace("_", "-")
        if not text:
            continue
        # Keep ISO-ish codes stable while tolerating provider values such as
        # fr-FR. Human language names are left untouched and simply won't
        # match the configured short codes unless a provider normalizes them.
        code = text.split("-", 1)[0]
        if code not in out:
            out.append(code)
    return out


def _search_preference_score(
    row: SearchResult, *, reading_languages: tuple[str, ...] = (),
    prefer_original_language: bool = False,
) -> int:
    """Score metadata against an admitted immutable preference snapshot.

    This headless, provider-neutral projection never reads mutable profile
    selection or UI state. The synchronous LibraryService facade captures the
    selected profile and preferences once at the presentation boundary.
    """
    metadata = row.metadata if isinstance(row.metadata, dict) else {}
    languages: list[str] = []
    for key in ("language", "languages", "lang", "dc_language", "dc:language"):
        if key in metadata:
            for code in _normalized_language_values(metadata.get(key)):
                if code not in languages:
                    languages.append(code)
    score = 0
    for index, preferred in enumerate(reading_languages):
        code = str(preferred or "").strip().lower().split("-", 1)[0]
        if code and code in languages:
            score += max(1, len(reading_languages) - index) * 10
            break
    if prefer_original_language and languages:
        originals: list[str] = []
        for key in ("original_language", "originalLanguage", "language_original"):
            if key in metadata:
                originals.extend(_normalized_language_values(metadata.get(key)))
        if originals and any(code in originals for code in languages):
            score += 5
    return score


def availability_display_label(self, item: SearchResult, detail: ItemDetail) -> str:
    """Translate availability without making public catalogue rows actionable."""
    metadata = item.metadata if type(item.metadata) is dict else {}
    if metadata.get("catalogue_access") == "shared_read_only":
        # This explicit read-only projection never establishes a lender,
        # account, fulfillment URL or any locally held entitlement.
        if metadata.get("search_provenance") == "known_catalog_stale":
            return "Notice publique connue hors ligne"
        return "Notice publique · disponibilité à vérifier auprès du fournisseur"
    if item.source_collection == "library":
        # A supplier inventory observation is not a current delivery right,
        # irrespective of vendor, owned/downloadable flags or upstream label.
        # The selected-offer and entitlement contracts remain authoritative.
        if metadata.get("search_provenance") == "known_catalog_stale":
            return "Notice d’inventaire connue hors ligne"
        return "Notice d’inventaire · disponibilité à vérifier auprès du fournisseur"
    if self.can_direct_download(item):
        return "Téléchargement direct"
    # An upstream availability status is a descriptive observation.
    # Do not rewrite "available" into a borrowing action for any supplier:
    # manual borrow requires the separate account, consent and policy gates.
    label = str(detail.availability_label or detail.availability_status or "—").strip()
    return label or "—"


def searchable_providers(self, *, request_scope=None) -> list[tuple[str, str]]:
    """Connected providers for this reader, or the legacy global search UI."""
    if request_scope is not None and not request_scope.current(self):
        return []
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for session in self._sessions.values():
        if not session.connected or not session.capabilities.search or session.provider_id in seen:
            continue
        if (not _search_policy_enabled(self, session.provider_id, request_scope)
                or not _search_credential_enabled(self, session)):
            continue
        seen.add(session.provider_id)
        out.append((session.provider_id, session.descriptor.display_name))
    return out


def _session_search_capabilities(session: ProviderSession) -> ProviderSearchCapabilities:
    """Resolve search capabilities while tolerating legacy/provider test doubles."""
    contract = getattr(session, "search_capabilities", None)
    if contract is None:
        contract = getattr(getattr(session, "descriptor", None), "search_capabilities", None)
    return contract if isinstance(contract, ProviderSearchCapabilities) else ProviderSearchCapabilities()


def provider_search_capabilities(self) -> dict[str, ProviderSearchCapabilities]:
    """Return declared search limits for connected providers, keyed by provider id."""
    result: dict[str, ProviderSearchCapabilities] = {}
    for session in self._sessions.values():
        if not session.connected or not session.capabilities.search:
            continue
        if (not self._provider_enabled_for_identity(session.provider_id)
                or not _search_credential_enabled(self, session)):
            continue
        result.setdefault(session.provider_id, self._session_search_capabilities(session))
    return result


def _search_contract_warning(session: ProviderSession, field: str, scope: str, *, facade_type) -> str:
    contract = facade_type._session_search_capabilities(session)
    if not contract.supports_field(field):
        return f"{session.descriptor.display_name}: champ de recherche non pris en charge ({field})"
    if not contract.supports_scope(scope):
        return f"{session.descriptor.display_name}: périmètre de recherche non pris en charge ({scope})"
    return ""


def _effective_search_field(session: ProviderSession, field: str, scope: str, *, facade_type) -> tuple[str | None, str]:
    """Conservatively degrade unsupported title/author fields to global search."""
    contract = facade_type._session_search_capabilities(session)
    name = session.descriptor.display_name
    if not contract.supports_scope(scope):
        return None, f"{name}: périmètre de recherche non pris en charge ({scope})"
    if contract.supports_field(field):
        return field, ""
    # Only these fields can be verified against normalized search results.
    # ISBN/subject must never be silently presented as exact matches.
    if field in {"title", "author"} and contract.supports_field("all"):
        return "all", f"{name}: recherche {field} approchée via recherche globale"
    return None, f"{name}: champ de recherche non pris en charge ({field})"


def _field_match(cls, field: str, query: str, row: SearchResult) -> bool:
    field = str(field or "all").lower()
    if field == "all" or field in {"isbn", "subject"}:
        # ISBN/sujet are forwarded to providers that understand them. The
        # common model does not yet expose normalized values to post-filter.
        return True
    tokens = [x for x in cls._history_fold(query).split() if len(x) >= 2]
    if not tokens:
        return True
    target = cls._history_fold(row.title if field == "title" else row.author if field == "author" else "")
    return all(token in target for token in tokens)


def _without_redundant_merged(self, sessions: list[ProviderSession], *, field: str, scope: str, include_redundant_merged: bool=False) -> list[ProviderSession]:
    """Prefer original sources over synthetic.merged in a federated query.

    Suppression is scoped to one virtual collection parent: unrelated Core
    providers are never hidden. A merged provider remains selectable alone
    (or together via an explicit opt-in). Only origins able to satisfy the
    requested field/scope suppress their sibling aggregate.
    """
    if include_redundant_merged or len(sessions) < 2:
        return sessions
    original_parents: set[str] = set()
    for session in sessions:
        entry = self.registry.virtual_entry(session.provider_id)
        if entry is None or entry.kind == "synthetic.merged":
            continue
        parent = self.registry.virtual_parent(session.provider_id)
        if parent and not self._search_contract_warning(session, field, scope):
            original_parents.add(parent)
    if not original_parents:
        return sessions
    return [
        session for session in sessions
        if not (
            (entry := self.registry.virtual_entry(session.provider_id)) is not None
            and entry.kind == "synthetic.merged"
            and self.registry.virtual_parent(session.provider_id) in original_parents
        )
    ]


def _configured_search_preferences(
    self, identity: object, sessions: list[ProviderSession],
) -> dict[str, str]:
    """Read exact per-provider primary hints, without granting ownership."""
    identity_id = getattr(identity, "identity_id", None)
    if type(identity_id) is not str or not identity_id:
        return {}
    try:
        saved = self.config.identity(identity_id)
    except (AttributeError, LookupError, TypeError, ValueError):
        return {}
    if type(saved) is not dict:
        return {}
    hints: dict[str, str] = {}
    for session in sessions:
        provider = getattr(session, "provider_id", None)
        if (type(provider) is not str
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider) is None):
            continue
        primary = saved.get(f"primary_{provider}_connection_id")
        if (type(primary) is str and 0 < len(primary) <= 256
                and primary == primary.strip() and primary.isprintable()):
            hints[provider] = primary
    return hints


def _search_sessions_for_active_identity(
    self, sessions: list[ProviderSession], identity: object, *,
    preferred_credentials: dict[str, str] | None = None,
) -> list[ProviderSession]:
    """Select one session per provider, preferring the active owner."""
    from .search_session_selection import choose_search_sessions
    return choose_search_sessions(
        self, sessions, identity,
        preferred_credentials=preferred_credentials,
    )


_PRIVATE_CATALOGUE_SEGMENTS = frozenset({
    "account", "accounts", "profile", "profiles", "member", "members",
    "reader", "readers", "session", "sessions", "auth", "login", "oauth",
    "token", "tokens", "download", "downloads", "acquisition", "acquire",
    "loan", "loans", "borrow", "borrows", "private", "personal",
    "user", "users", "drm", "adept", "licence", "license",
})


def _private_catalogue_path(path: str) -> bool:
    """Reject account, session, borrowing and delivery routes for all sources."""
    return bool(
        {segment.casefold() for segment in path.split("/")}
        & _PRIVATE_CATALOGUE_SEGMENTS
    )


def _shared_catalogue_cover_url(value: Any) -> str | None:
    """Preserve an ordinary public catalogue image, never account/private URLs.

    Catalogue URLs are public by default unless marked/revealed otherwise.
    Sharing an image is NOT sharing a foreign credential, loan or entitlement.
    Use the existing conservative HTTPS host/query guard and exclude account,
    acquisition, or session path segments even on a public-looking hostname.
    """
    from urllib.parse import urlsplit

    public = _public_catalog_url(value, cover=True)
    if public is None:
        return None
    if _private_catalogue_path(urlsplit(public).path):
        return None
    return public


def _private_search_owner(self, session: ProviderSession) -> str | None:
    """Inert, provider-neutral SQLite owner for one private search session.

    This snapshot only determines when public-only projection is required.
    It never confers a right to read a different account's private catalogue.
    """
    provider_id = getattr(session, "provider_id", None)
    credential = getattr(session, "credential_id", None)
    if (type(provider_id) is not str or not provider_id
            or type(credential) is not str or not credential):
        return None
    try:
        owner = self.storage.identity_for_connection(provider_id, credential)
    except (AttributeError, LookupError, TypeError, ValueError):
        return None
    return owner if type(owner) is str and owner else None


# A per-search Core-only context keeps LibraryService's frozen mirror ABI.
# ContextVar isolates overlapping threads and callback-initiated nested searches;
# no plugin DTO or session can claim this admission scope.
_CORE_LIVE_PUBLIC_C_WRITER: ContextVar[bool] = ContextVar(
    "cifereca_core_live_public_c_writer", default=False,
)
_CORE_PROGRESSIVE_C_WRITTEN: ContextVar[bool] = ContextVar(
    "cifereca_core_progressive_c_written", default=False,
)


def _mirror_live_search_results(
    service, rows: list[SearchResult], *, identity_id: str,
    c_already_ingested: bool = False,
) -> int:
    """Invoke the legacy mirror ABI while requiring a same-call C witness."""
    if not c_already_ingested:
        from .notice_c_private_search_web import persist_authenticated_search_rows
        persist_authenticated_search_rows(service, rows, identity_id=identity_id)
        from .notice_c_private_series import persist_series_rows
        persist_series_rows(service, rows, identity_id=identity_id)
    token = _CORE_LIVE_PUBLIC_C_WRITER.set(True)
    ingested = _CORE_PROGRESSIVE_C_WRITTEN.set(c_already_ingested)
    try:
        return service._mirror_remote_results(rows, identity_id=identity_id)
    finally:
        _CORE_PROGRESSIVE_C_WRITTEN.reset(ingested)
        _CORE_LIVE_PUBLIC_C_WRITER.reset(token)


# In-process, nonserializable Core admission witness. Do not use a provider
# DTO flag/metadata field (or a cache hit) as permission to publish Notice C.
_CORE_PUBLIC_COVER_WITNESS = object()


def _public_cover_witness_coordinates(row: SearchResult) -> tuple:
    # Exact same-call source + edition + image; no title, ISBN or N3 SHA join.
    return (
        row.id, row.source_provider, row.source_credential,
        row.source_collection, row.source_account_id, row.record_id,
        row.docbase, row.medium, row.revision_id, row.scenario,
        row.thumbnail_url, row.owned, row.downloadable, row.download_url,
    )


def _verified_public_cover_witness(row: SearchResult) -> bool:
    witness = getattr(row, "_core_same_call_public_cover_witness", None)
    return (
        type(witness) is tuple and len(witness) == 2
        and witness[0] is _CORE_PUBLIC_COVER_WITNESS
        and witness[1] == _public_cover_witness_coordinates(row)
    )


_CORE_PUBLIC_WEB_WITNESS = object()


def _verified_public_web_reference(row: SearchResult) -> dict | None:
    """Read only the exact URL and marker sealed during same-call admission."""
    if type(row) is not SearchResult or not _verified_public_cover_witness(row):
        return None
    witness = getattr(row, "_core_same_call_public_web_witness", None)
    if type(row.metadata) is not dict or type(witness) is not tuple or len(witness) != 4:
        return None
    evidence = row.metadata.get("public_url_evidence")
    url = row.friendly_url
    if (witness[0] is not _CORE_PUBLIC_WEB_WITNESS
            or witness[1] is not row._core_same_call_public_cover_witness
            or witness[2] != url or witness[3] != evidence
            or not _is_public_web_evidence(evidence, row.source_provider)
            or _public_catalog_url(url, evidence=evidence, provider_id=row.source_provider,
                                   record_id=row.record_id) != url
            or type(url) is not str):
        return None
    return {"url": url, "evidence": evidence}


def _terminal_anonymous_empty(response, session):
    """Only the optional exact same-call empty receipt stops loose variants."""
    return (type(response) is ProviderSearchResponse
            and getattr(session, "credential_id", None) == "__public__"
            and response.terminal_empty is True
            and response.catalogue_complete is True and response.partial is False
            and type(response.results) is list and not response.results
            and type(response.public_notices) is tuple and not response.public_notices)


def _public_rows_from_response(
    response: ProviderSearchResponse, session: ProviderSession,
) -> list[SearchResult]:
    """Project only attested objects of this exact response, fail closed.

    A list-only legacy provider or a response without notices cannot authorize
    foreign-profile publication. Never turn private rows into public notices.
    """
    from .public_search_projection import project_public_search_results
    if (type(response) is not ProviderSearchResponse
            or type(response.results) is not list
            or len(response.results) > 10000
            or type(response.public_notices) is not tuple
            or len(response.public_notices) > 10000
            or len({id(row) for row in response.results}) != len(response.results)):
        # Repeated object identity is not an independently attested notice.
        return []
    from collections import Counter
    from ..plugin_api import PublicSearchNotice
    # Every declaration belongs to this one response. Validate and index
    # it once; each result then consumes an O(1) source/edition candidate.
    projections = project_public_search_results(response)
    if projections is None:
        return []
    exact_public_rows = Counter(
        id(claim.result)
        for claim in response.public_notices
        if type(claim) is PublicSearchNotice
    )
    from hashlib import sha256
    body = response.notice_parse_input
    implementation = response.notice_parser_implementation
    evidence = None
    if (type(body) is bytes and 0 < len(body) <= 2 * 1024 * 1024
            and type(implementation) is str
            and re.fullmatch(r"[0-9a-f]{64}", implementation)
            and response.partial is False):
        evidence = (sha256(body).hexdigest(), implementation)
    admitted: list[SearchResult] = []
    for row in response.results:
        projected = projections.get(id(row))
        if (
            projected is not None
            and row.source_provider == session.provider_id
            and row.source_credential in (
                getattr(session, "credential_id", None), "__public__",
            )
        ):
            # The generic foreign-profile public projection deliberately
            # drops URLs/editions from a PRIVATE session. An actually
            # anonymous public producer can retain its ordinary catalogue
            # image, but only when its exact same-call public notice points
            # to THIS source object, with no credential/account/rights.
            # A signed/private image still fails the Core catalogue guard.
            anonymous = (
                getattr(session, "credential_id", None) == "__public__"
                and type(getattr(session, "credentials", None)) is dict
                and not session.credentials
                and type(getattr(session, "_accounts", None)) is list
                and not session._accounts
                and row.source_credential == "__public__"
                and row.source_collection == "public_catalog"
                and row.source_account_id is None
                and row.owned is False and row.downloadable is False
                and row.download_url is None
                and exact_public_rows.get(id(row), 0) == 1
            )
            if anonymous:
                image = _shared_catalogue_cover_url(row.thumbnail_url)
                # An omitted image may attest a public edition, but an
                # unsafe REJECTED image cannot launder its revision into a
                # public result or become a Core same-call witness.
                valid_public_occurrence = (
                    image is not None or row.thumbnail_url is None
                )
                revision = (
                    row.revision_id
                    if valid_public_occurrence
                    and type(row.revision_id) is str
                    and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", row.revision_id)
                    else None
                )
                projected = replace(
                    projected, thumbnail_url=image, revision_id=revision,
                )
                if valid_public_occurrence:
                    # Retain a URL only from this exact anonymous public
                    # parser record/card, not a generic foreign/private claim.
                    evidence = row.metadata.get("public_url_evidence")
                    if _is_public_web_evidence(evidence, row.source_provider):
                        product = _public_catalog_url(
                            row.friendly_url, evidence=evidence,
                            provider_id=row.source_provider, record_id=row.record_id,
                        )
                        if product is not None:
                            projected = replace(
                                projected, friendly_url=product,
                                metadata={**projected.metadata,
                                          "public_url_evidence": evidence},
                            )
                    # Core-only same-call witness; not serialized into the
                    # shared Search cache or assigned by provider plugins.
                    from .notice_search_cover_producer import _CORE_PUBLIC_C_WITNESS
                    projected._core_notice_c_public_bibliography = _CORE_PUBLIC_C_WITNESS
                    # Independent proof for durable public cover admission.
                    # Keep the existing v2 bibliography witness intact.
                    projected._core_same_call_public_cover_witness = (
                        _CORE_PUBLIC_COVER_WITNESS,
                        _public_cover_witness_coordinates(projected),
                    )
                    from .notice_c_public_age import stamp_same_call_public_age
                    stamp_same_call_public_age(projected, row)
                    if projected.friendly_url is not None:
                        projected._core_same_call_public_web_witness = (
                            _CORE_PUBLIC_WEB_WITNESS,
                            projected._core_same_call_public_cover_witness,
                            projected.friendly_url,
                            projected.metadata.get("public_url_evidence"),
                        )
                    if evidence is not None:
                        from .notice_search_cover_producer import admit_public_search_catalogue
                        exact = admit_public_search_catalogue(
                            projected, identity_id="__public__", observed_at=1,
                            public_cover_url=projected.thumbnail_url,
                        )
                        if exact is not None:
                            projected._core_notice_parse_input = (
                                _CORE_PUBLIC_COVER_WITNESS, exact.key,
                                exact.raw_json, exact.parser_id, exact.parser_version,
                                *evidence,
                            )
            admitted.append(projected)
    return admitted


def _search_session_cache_scope(sessions: list[ProviderSession]) -> tuple[tuple[str, str], ...]:
    """Partition remote search cache by the active configured source account.

    The tuple is used exclusively inside a SHA-256 key preimage, never
    persisted, logged or exposed to GUI. Provider sessions without a
    credential_id retain an empty handle for compatibility.
    """
    return tuple(sorted(
        (str(session.provider_id), str(getattr(session, "credential_id", "")))
        for session in sessions
    ))


def _search_owner_cache_scope(
    owner_leases: tuple[tuple[ProviderSession, str | None], ...],
) -> tuple[tuple[str, str, str], ...]:
    """Bind remote cached rows to the exact inert SQL owner snapshot.

    A credential reassigned A -> B must not replay A's private cache to B,
    even when source selection, search terms and active profile are unchanged.
    The preimage stays local and is only persisted as a SHA-256 cache key.
    """
    return tuple(sorted(
        (
            str(session.provider_id),
            str(getattr(session, "credential_id", "")),
            owner if type(owner) is str else "",
        )
        for session, owner in owner_leases
    ))


def _search_presentation_cache_scope(
    identity: object,
    searchable: list[ProviderSession],
    owner_leases: tuple[tuple[ProviderSession, str | None], ...],
) -> str:
    """Cache by source, not by *viewer*, only for a truly anonymous producer.

    Never generalize this to private, ownerless-but-unknown or mixed sources:
    their response may reflect a different account. Search projections are
    already ranked using the selected reader's language preferences, so
    those preferences remain part of the key until raw source responses and
    reader ranking are separated. An anonymous source must still return a
    same-call PublicSearchNotice, independently enforced by the producer
    response gate; this cache key cannot confer public provenance.
    """
    if identity is None:
        return ""
    preferences = getattr(identity, "preferences", None)
    ranking = (
        f"{','.join(preferences.reading_languages)}:"
        f"{int(preferences.prefer_original_language)}"
    )
    if (len(searchable) == 1 and len(owner_leases) == 1
            and owner_leases[0][0] is searchable[0]
            and owner_leases[0][1] is None):
        session = searchable[0]
        if (getattr(session, "credential_id", None) == "__public__"
                and type(getattr(session, "credentials", None)) is dict
                and not session.credentials
                and type(getattr(session, "_accounts", None)) is list
                and not session._accounts
                and isinstance(session, ProviderSearchWithFacets)):
            # Source and owner scopes are independently in the cache key.
            # A NUL domain marker cannot occur in validated identity IDs;
            # it also prevents a private or malformed source cache collision.
            return "\0source-anonymous-public-v1:" + ranking
    return f"{identity.identity_id}:" + ranking


def _search_cooldown_clock() -> float:
    return time.monotonic()


def _search_cooldown_key(session: ProviderSession, identity: object) -> tuple:
    # Keys are strictly in-process; never persist identity/credential values.
    return (session.provider_id, getattr(session, "credential_id", None),
            getattr(identity, "identity_id", None))


def _source_in_retry_after(self, session: ProviderSession, identity: object) -> bool:
    """Check only the same configured source, session and identity."""
    lock = getattr(self, "_search_generation_lock", None)
    if lock is None:
        return False  # Compatible with lightweight search-only test hosts.
    with lock:
        entries = getattr(self, "_search_retry_after", None)
        if not entries:
            return False
        key = _search_cooldown_key(session, identity)
        row = entries.get(key)
        if row is None:
            return False
        bound_session, bound_identity, expires = row
        if _search_cooldown_clock() >= expires:
            entries.pop(key, None)
            return False
        # A new session with the same account key cannot inherit this
        # throttle; nor may it erase the old session's active cooldown.
        return bound_session is session and bound_identity is identity


def _record_source_retry_after(
    self, session: ProviderSession, identity: object, credential: object, error: BaseException,
    *, request_scope=None,
) -> None:
    """Remember a native HTTP 429 once; never sleep/retry in a worker."""
    from ..search_failures import search_retry_after_seconds
    delay = search_retry_after_seconds(error)
    if delay is None:
        return
    with self._search_generation_lock:
        # A completed request from an old identity/account/session must not
        # throttle a newly selected source or otherwise mutate its state.
        if ((not request_scope.current(self) if request_scope is not None
                else self.current_identity is not identity)
                or getattr(session, "credential_id", None) != credential):
            return
        entries = getattr(self, "_search_retry_after", None)
        if entries is None:
            entries = self._search_retry_after = {}
        now = _search_cooldown_clock()
        for key, (_, _, expires) in tuple(entries.items()):
            if expires <= now:
                del entries[key]
        if len(entries) >= 64:
            del entries[min(entries, key=lambda key: entries[key][2])]
        key = _search_cooldown_key(session, identity)
        current = entries.get(key)
        until = now + delay
        if current is not None and current[0] is session and current[1] is identity:
            until = max(until, current[2])
        entries[key] = (session, identity, until)


def _inert_search_source_state(session: object) -> tuple[object, object, object] | None:
    """Current scalar route for one source, without running provider getters.

    The source lease is only a revocation guard; it never grants search
    capability or access to another account.
    """
    try:
        state = object.__getattribute__(session, "__dict__")
    except (AttributeError, TypeError):
        return None
    if type(state) is not dict:
        return None
    # Established lightweight/catalogue sessions may declare *inert class
    # scalars* rather than setting instance fields in __init__. Static lookup
    # preserves those providers without executing an arbitrary property or
    # __getattribute__ callback (potential network I/O).
    def scalar(name: str, default: object = None) -> object:
        if name in state:
            return state[name]
        return inspect.getattr_static(session, name, default)

    provider = scalar("provider_id")
    # Catalogue-only virtual providers may have no credential at all.
    # Preserve their historical public search eligibility.
    credential = scalar("credential_id", "")
    if credential is None:
        credential = ""
    connected = scalar("connected")
    if type(provider) is not str or not provider or type(credential) is not str:
        return None
    return (provider, credential, connected)



def _search_credential_enabled(self, session: object) -> bool:
    """Check an exact account switch without consulting provider callbacks.

    A connected session may outlive an account toggle until the next broker
    reconnect. Neither the stored credential owner nor a public catalogue
    fallback is a right to continue using an explicitly disabled source.
    Absent registrations remain compatible with anonymous/legacy sources;
    a registered false switch always revokes the session's search lease.
    """
    state = _inert_search_source_state(session)
    if state is None or state[2] is not True:
        return False
    provider, credential, _ = state
    if not credential or credential == "__public__":
        return True
    try:
        configured = self.config.provider_accounts(provider)
        if any(
            type(row) is dict and row.get("id") == credential
            and row.get("enabled", True) is not True
            for row in configured
        ):
            return False
        stored = self.storage.provider_connection_enabled(provider, credential)
        return stored is not False
    except (AttributeError, OSError, LookupError, TypeError, ValueError):
        return False

def search(self, query: str, scope: str='all', force: bool=False, *, provider_id: str='all', field: str='all', content_types: tuple[str, ...] | list[str] | None=None, include_redundant_merged: bool=False, logger, facade_type) -> list[SearchResult]:
    # Capture before session discovery, which may run across an identity
    # transition. A query must not discover sources as A then run them as B.
    with self._search_generation_lock:
        self._search_generation += 1
        search_generation = self._search_generation
        identity = self.current_identity

    # The broker can replace/reconnect a provider without changing identity
    # or creating another search. Its in-flight old results are then revoked.
    source_leases: tuple[tuple[object, tuple[object, object, object] | None], ...] = ()
    owner_leases: tuple[tuple[ProviderSession, str | None], ...] = ()

    def sources_current() -> bool:
        if not source_leases:
            return True
        if (preference_sensitive
                and _configured_search_preferences(self, identity, available)
                != configured_search_preferences):
            return False
        try:
            registered = tuple(self._sessions.values())
        except RuntimeError:
            return False
        for session, expected in source_leases:
            if (expected is None or expected[2] is not True
                    or not any(candidate is session for candidate in registered)
                    or _inert_search_source_state(session) != expected
                    or not self._provider_enabled_for_identity(expected[0])
                    or not _search_credential_enabled(self, session)):
                return False
        if any(_private_search_owner(self, session) != owner
               for session, owner in owner_leases):
            return False
        return True

    def request_current() -> bool:
        with self._search_generation_lock:
            return (
                self._search_generation == search_generation
                and self.current_identity is identity
                and sources_current()
            )

    provider_scope = "all" if str(scope or "all").casefold() == "unknown" else scope
    # Search remains global by provider, but the selected reader's own
    # credential takes precedence when several sessions share that provider.
    # This also binds source_credential, holdings and actions to the reader.
    requested_provider = str(provider_id or "all").strip().lower()
    available = [
        session for session in self._sessions.values()
        if session.connected and session.capabilities.search
        and self._provider_enabled_for_identity(session.provider_id)
        and _search_credential_enabled(self, session)
        and (requested_provider == "all" or session.provider_id == requested_provider)
    ]
    configured_search_preferences = _configured_search_preferences(
        self, identity, available,
    )
    searchable = _search_sessions_for_active_identity(
        self, available, identity,
        preferred_credentials=configured_search_preferences,
    )
    searchable = self._without_redundant_merged(
        searchable, field=field, scope=provider_scope,
        include_redundant_merged=include_redundant_merged,
    )
    preference_sensitive = any(
        session.provider_id in configured_search_preferences
        for session in searchable
    )
    # A single exact SQL owner snapshot per selected session, regardless
    # of provider. All snapshots are rechecked before publishing results.
    # Foreign responses require exact same-call public notice provenance.
    owner_leases = tuple(
        (session, _private_search_owner(self, session))
        for session in searchable
    )
    active_id = getattr(identity, "identity_id", None)
    foreign_owner_sessions = {
        id(session) for session, owner in owner_leases
        if not (type(active_id) is str and active_id and owner == active_id)
    }
    source_leases = tuple(
        (session, _inert_search_source_state(session))
        for session in searchable
    )
    provider_key = ",".join(sorted(x.provider_id for x in searchable)) or requested_provider
    pref_key = _search_presentation_cache_scope(
        identity, searchable, owner_leases,
    )
    virtual_versions = ",".join(
        f"{entry.plugin_id}:{entry.source_revision}:{entry.catalog_generation}"
        for session in searchable
        if (entry := self.registry.virtual_entry(session.provider_id)) is not None
    )
    virtual_cache_tag = f"\0{virtual_versions}" if virtual_versions else ""
    cache_key = hashlib.sha256(
        f"search-v14\0{_search_session_cache_scope(searchable)!r}\0{_search_owner_cache_scope(owner_leases)!r}\0{provider_key}\0{scope}\0{field}\0{tuple(sorted(content_types or ()))!r}\0{pref_key}\0{query.casefold()}{virtual_cache_tag}".encode()
    ).hexdigest()
    ttl = int(self.config.get("cache.search_seconds", 21600))
    if not force:
        cached = self.storage.cache_get("search", cache_key)
        cached_contents = _search_cache_contents(cached, searchable)
        if cached_contents is not None:
            cached_rows, window_sources = cached_contents
            rows = [_provider_search_row(SearchResult(**x)) for x in cached_rows]
            _stamp_cached_public_notice_refs(
                rows, identity_id=identity.identity_id if identity else None,
                storage=self.storage,
            )
            with self._search_generation_lock:
                if (self._search_generation != search_generation
                        or self.current_identity is not identity
                        or not sources_current()):
                    return []
                self.last_search = rows
                # Never leak a warning from a previous, unrelated search.
                self.last_search_warnings = [
                    _catalogue_window_warning(session) for session in searchable
                    if session.provider_id in window_sources
                ]
            self.diagnostics.add("CACHE", "search", detail={"hit": True, "results": len(rows), "q_len": len(query), "providers": provider_key})
            return rows

    rows: list[SearchResult] = []
    warnings: list[str] = []
    window_sources: set[str] = set()
    variants = self._search_query_variants(query)
    for session in searchable:
        if not request_current():
            return []
        provider_rows: list[SearchResult] = []
        provider_seen: set[tuple[str, ...]] = set()
        effective_field, contract_warning = _effective_search_field(
            session, field, provider_scope, facade_type=facade_type,
        )
        foreign_owner = id(session) in foreign_owner_sessions
        # Never ask a foreign authenticated session for private library results.
        # Public notices are still mandatory; catalogue scope alone grants nothing.
        effective_search_scope = "catalog" if foreign_owner else provider_scope
        if contract_warning:
            warnings.append(contract_warning)
        if effective_field is None:
            continue
        primary_ok = False
        public_outage = _source_in_retry_after(self, session, identity)
        credential = getattr(session, "credential_id", None)
        if public_outage:
            warnings.append(f"{session.descriptor.display_name}: recherche en ligne indisponible")
        for attempt, variant in enumerate(() if public_outage else variants):
            # Stop network work after an identity switch or newer search.
            # A second loose-text GET cannot repair a revoked request.
            if not request_current():
                return []
            # Loose fallbacks are needed only when the exact query produced
            # no strong title/author match. This keeps normal searches fast.
            if attempt > 0 and primary_ok:
                break
            try:
                from .notice_c_private_search_web import (
                    capture_authenticated_search_call, stamp_authenticated_search_rows,
                )
                private_call = (None if foreign_owner else capture_authenticated_search_call(
                    self, session, getattr(identity, "identity_id", None),
                ))
                from .notice_c_private_series import capture_series_call, stamp_series_rows
                series_call = (None if foreign_owner else capture_series_call(
                    self, session, getattr(identity, "identity_id", None),
                ))
                response_partial = False
                terminal_empty = False
                if foreign_owner:
                    # Foreign sessions may only return an explicit, same-call
                    # public projection; no legacy list-only fallback.
                    if not isinstance(session, ProviderSearchWithFacets):
                        session_rows = []
                    else:
                        response = session.search_with_response_facets(
                            variant, effective_search_scope,
                            field=effective_field,
                            content_types=tuple(content_types or ()),
                        )
                        session_rows = _public_rows_from_response(response, session)
                        response_partial = response.partial is True
                        terminal_empty = _terminal_anonymous_empty(response, session)
                        if response.catalogue_complete is False:
                            window_sources.add(session.provider_id)
                else:
                    try:
                        import inspect
                        parameters = inspect.signature(session.search).parameters
                        kwargs = {"field": effective_field}
                        if "content_types" in parameters:
                            kwargs["content_types"] = tuple(content_types or ())
                        session_rows = session.search(variant, effective_search_scope, **kwargs)
                    except TypeError as exc:
                        if "field" not in str(exc) or "unexpected keyword" not in str(exc):
                            raise
                        session_rows = session.search(variant, effective_search_scope)
                stamp_authenticated_search_rows(self, private_call, session_rows)
                stamp_series_rows(self, series_call, session_rows)
                for raw_row in session_rows:
                    row = _provider_search_row(raw_row)
                    key = _search_result_scope_key(row)
                    if key not in provider_seen:
                        provider_seen.add(key)
                        provider_rows.append(row)
                concrete = getattr(session, "client", None)
                partial_failure = response_partial
                if response_partial:
                    warnings.append(
                        f"{session.descriptor.display_name}: résultats partiels"
                    )
                for source in (session, concrete):
                    for _warning in list(getattr(source, "last_search_warnings", []) or []):
                        # A provider warning is untrusted: it may carry signed
                        # URLs, credentials, account IDs or a private title.
                        # Mirror the progressive path: export only a constant.
                        warnings.append(f"{session.descriptor.display_name}: résultats partiels")
                        partial_failure = True
                if partial_failure:
                    break
                if terminal_empty:
                    break
                if attempt == 0 and provider_rows:
                    primary_ok = max(self._search_relevance(query, row) for row in provider_rows) >= 100
            except Exception as exc:
                from ..search_failures import search_failure_code
                logger.warning(
                    "TRACE provider search failure provider=%s query_variant=%s attempt=%s",
                    session.provider_id, variant, attempt + 1, exc_info=True,
                )
                code = search_failure_code(exc)
                if code == "SearchTemporarilyUnavailable":
                    _record_source_retry_after(self, session, identity, credential, exc)
                public_outage = public_outage or code == "SearchTemporarilyUnavailable"
                # Provider exceptions may carry signed URLs, query terms,
                # cookies or private account identities. Never interpolate
                # them into warnings or diagnostics.
                # A successful-but-empty first variant does not authorize
                # treating an auth refusal or transport failure of a later
                # variant as a verified empty catalogue. Keep a fixed warning
                # on every failed variant; it also prevents a fresh TTL cache.
                warnings.append(
                    f"{session.descriptor.display_name}: "
                    f"{_search_problem_label(code or 'ProviderSearchFailed')}"
                )
                self.diagnostics.add(
                    "PLUGIN", "search", "error",
                    detail={
                        "provider": session.provider_id,
                        "attempt": attempt + 1,
                        "q_len": len(variant),
                        "error": code or "ProviderSearchFailed",
                        "message": "search_provider_failure",
                    },
                )
                # No equivalent network retry for auth refusal or outage.
                if code is not None:
                    break
        if public_outage and not provider_rows and self.current_identity is identity:
            from .known_catalog_fallback import known_public_fallback_page
            offline = known_public_fallback_page(
                self.storage, provider_id=session.provider_id,
                identity_id=identity.identity_id if identity is not None else None,
                query=query, scope=scope, field=field,
                content_types=tuple(content_types or ()),
            )
            if offline.results:
                provider_rows = list(offline.results)
                warnings.append(
                    f"{session.descriptor.display_name}: résultats connus, recherche en ligne indisponible"
                )
                if offline.has_more:
                    warnings.append(
                        f"{session.descriptor.display_name}: "
                        + _STALE_PUBLIC_TRUNCATED_WARNING
                    )
            else:
                warnings.append(
                    f"{session.descriptor.display_name}: "
                    + _stale_public_fallback_warning(offline.status)
                )
        if field in {"title", "author"}:
            provider_rows = [row for row in provider_rows if self._field_match(field, query, row)]
        if str(scope or "all").casefold() == "unknown":
            from ..core.search import support_kind
            provider_rows = [row for row in provider_rows if support_kind(asdict(row)) == "unknown"]
        rows.extend(provider_rows)

    # Stable de-duplication across plugins; source provider remains visible.
    unique: dict[tuple[str, str, str, str, str], SearchResult] = {}
    for row in rows:
        unique[_search_result_scope_key(row)] = row
    rows = list(unique.values())
    preference_scores = {id(row): self._search_preference_score(row) for row in rows}
    if len(variants) > 1 or any(preference_scores.values()):
        # Relevance remains authoritative; identity preferences only rank
        # otherwise comparable results and never hide other languages.
        rows.sort(
            key=lambda row: (
                self._search_relevance(query, row) if len(variants) > 1 else 0,
                preference_scores.get(id(row), 0),
            ),
            reverse=True,
        )
    warnings = list(dict.fromkeys(warnings))
    # Provider I/O may outlive both a user switch and a newer search request.
    # Never write those stale notices under the now-selected identity.
    if not request_current():
        return []
    if identity is not None:
        try:
            _mirror_live_search_results(self, [
                row for row in rows
                if not isinstance(row.metadata, dict)
                or row.metadata.get("search_provenance") != "known_catalog_stale"
            ], identity_id=identity.identity_id)
        except Exception:
            # Mirror exceptions may be dynamically named by untrusted plugins.
            logger.warning("TRACE catalog mirror search code=mirror_failed")
    if not request_current():
        return []
    if not warnings:
        self.storage.cache_put("search", cache_key, _search_cache_payload(rows, window_sources), ttl)
    warnings.extend(_catalogue_window_warning(session) for session in searchable
                    if session.provider_id in window_sources)
    with self._search_generation_lock:
        if (self._search_generation != search_generation
                or self.current_identity is not identity
                or not sources_current()):
            return []
        self.last_search = rows
        self.last_search_warnings = warnings
    self.diagnostics.add(
        "SEARCH", "federated",
        detail={
            "providers": provider_key,
            "field": field,
            "results": len(rows),
            "variants": len(variants),
            "fallback": len(variants) > 1,
        },
        level="detailed",
    )
    return rows


def _search_problem_label(code: str) -> str:
    """Fixed user-facing search status; never include a provider exception.

    Authorization refusal is not evidence of a public-service outage. In
    particular, it must neither suggest stale fallback nor a network retry.
    """
    if code == "SearchAuthenticationRequired":
        return "authentification requise"
    if code == "SearchAccessDenied":
        return "accès refusé"
    if code == "SearchTemporarilyUnavailable":
        return "recherche en ligne indisponible"
    return "échec de recherche"


def _provider_search_row(row: SearchResult) -> SearchResult:
    """Keep Core-owned provenance out of untrusted supplier and cache rows.

    Only known_public_fallback may assign ``known_catalog_stale``. Never
    mutate the provider's original row/metadata (it can be shared).
    """
    metadata = getattr(row, "metadata", None)
    if isinstance(metadata, dict) and "search_provenance" in metadata:
        return replace(row, metadata={
            key: value for key, value in metadata.items()
            if key != "search_provenance"
        })
    return row



_STALE_PUBLIC_TRUNCATED_WARNING = (
    "known_catalog_page_truncated : première page de secours seulement ; "
    "d’autres résultats connus ne sont pas affichés"
)


def _stale_public_fallback_warning(status: str) -> str:
    """Closed, public diagnostic: no match is not a read-budget failure."""
    if status == "scope_budget_exceeded":
        return ("recherche en ligne indisponible "
                "(catalogue connu trop volumineux : secours local non vérifiable)")
    if status in {"unavailable", "invalid_page", "page_out_of_range"}:
        return ("recherche en ligne indisponible "
                "(inventaire local de secours indisponible)")
    return "recherche en ligne indisponible (aucun résultat connu)"


_CATALOGUE_WINDOW_MESSAGE = "fenêtre du catalogue non exhaustive (pagination)"


def _catalogue_window_warning(session) -> str:
    return f"{session.descriptor.display_name}: {_CATALOGUE_WINDOW_MESSAGE}"


def _search_cache_payload(rows, window_sources):
    serialized = [asdict(row) for row in rows]
    if not window_sources:
        return serialized
    return {"rows": serialized, "window_sources": sorted(window_sources)}


def _search_cache_contents(value, sessions):
    """The window verdict belongs to the cached query, never a book."""
    if isinstance(value, list):
        return value, set()
    if type(value) is not dict or set(value) != {"rows", "window_sources"}:
        return None
    rows, source_ids = value["rows"], value["window_sources"]
    if type(rows) is not list or type(source_ids) is not list:
        return None
    if not source_ids or any(type(source_id) is not str for source_id in source_ids):
        return None
    sources = set(source_ids)
    if len(sources) != len(source_ids) or not sources.issubset(
        {session.provider_id for session in sessions}
    ):
        return None
    return rows, sources


def _search_batch_status(
    rows: list[SearchResult] | tuple[SearchResult, ...],
    warnings: list[str] | tuple[str, ...],
    error_code: str,
    *,
    cached: bool = False,
    stale_attested: bool = False,
) -> str:
    """Compute a closed Core verdict; never use arbitrary provider error text.

    A stale notice is not a successful live result. Access refusal stays
    distinguishable by the *existing* machine error_code, never relabelled as
    a public transport outage or evidence of a purchase.
    """
    if stale_attested and error_code == "SearchTemporarilyUnavailable" and rows:
        return "known_catalog_stale"
    if error_code:
        return "partial" if rows else "unavailable"
    if warnings:
        return "partial"
    return "cached" if cached else "live"


def _search_result_scope_key(row: SearchResult) -> tuple[str, ...]:
    """Deduplicate a *source/edition*, not just its legacy catalogue record.

    A supplier can reuse its record/docbase/collection for multiple revision
    markers, accounts or physical/digital manifestations. Notice C, N2 and
    the GTK list already distinguish those; discarding them in federated
    search before Core ingestion silently loses genuine notices. This key is
    read-only and grants neither catalogue provenance nor a right.

    Mirror-v2 continues to use its historical storage key while that index is
    replaced by the universal Notice C catalogue; never promote a dedup key
    to an ownership/holding witness.
    """
    return (
        row.source_provider, row.source_credential, row.record_id,
        row.docbase, row.source_collection or "",
        row.source_account_id or "", row.medium, row.revision_id or "",
    )


def _valid_facet_text(value: object) -> bool:
    return (
        type(value) is str and 1 <= len(value) <= 160 and bool(value.strip())
        and not any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value)
    )


def _validated_response_facets(facets: object) -> tuple[CatalogSearchFacet, ...]:
    if type(facets) is not tuple or len(facets) > 64:
        return ()
    accepted = []
    seen: set[tuple[str, int, str]] = set()
    for facet in facets:
        if (type(facet) is not CatalogSearchFacet
                or not all(_valid_facet_text(text) for text in (
                    facet.scenario, facet.field, facet.label
                ))
                or type(facet.page) is not int or not 0 <= facet.page < 2**31):
            continue
        key = (facet.scenario, facet.page, facet.field.casefold())
        if key in seen:
            continue
        seen.add(key)
        values = facet.values
        if values is not None and (
            type(values) is not tuple or not 1 <= len(values) <= 64
            or any(
                type(value) is not CatalogSearchFacetValue
                or not _valid_facet_text(value.label)
                or type(value.count) is not int or not 0 <= value.count < 2**63
                or type(value.is_selected) is not bool
                or (value.display_label is not None
                    and not _valid_facet_text(value.display_label))
                for value in values
            )
        ):
            facet = replace(facet, values=None)
        accepted.append(facet)
    return tuple(accepted)


def _search_provider_session(
    self, session: ProviderSession, query: str, scope: str, *, field: str = "all",
    content_types: tuple[str, ...] = (), facade_type,
    response_facets: list[CatalogSearchFacet] | None = None,
    response_windows: list[bool] | None = None,
    public_only: bool = False, request_scope=None, request_cancelled=None,
) -> tuple[list[SearchResult], list[str], str, str]:
    """Search one provider without mutating shared result state.

    This is deliberately safe to run in parallel with *other* provider sessions;
    a given session is still called sequentially for its own query variants.
    """
    provider_scope = "all" if str(scope or "all").casefold() == "unknown" else scope
    provider_rows: list[SearchResult] = []
    provider_seen: set[tuple[str, ...]] = set()
    warnings: list[str] = []
    effective_field, contract_warning = _effective_search_field(
        session, field, provider_scope, facade_type=facade_type,
    )
    if contract_warning:
        warnings.append(contract_warning)
    if effective_field is None:
        return [], warnings, "", ""
    if public_only:
        # Never invoke foreign library/holdings search by broad "all" scope.
        # A legacy search() result cannot attest foreign public provenance.
        provider_scope = "catalog"
        response_facets = None
    identity = request_scope.identity if request_scope is not None else getattr(self, "current_identity", None)
    credential = getattr(session, "credential_id", None)
    if request_cancelled is not None and request_cancelled():
        return [], [], "StreamCancelled", "search cancelled"
    if _source_in_retry_after(self, session, identity):
        code = "SearchTemporarilyUnavailable"
        return [], [f"{session.descriptor.display_name}: {_search_problem_label(code)}"], code, _search_problem_label(code)
    variants = self._search_query_variants(query)
    primary_ok = False
    terminal_error_code = terminal_error_message = ""
    for attempt, variant in enumerate(variants):
        if request_cancelled is not None and request_cancelled():
            return [], [], "StreamCancelled", "search cancelled"
        if attempt > 0 and primary_ok:
            break
        try:
            from .notice_c_private_search_web import (
                capture_authenticated_search_call, stamp_authenticated_search_rows,
            )
            private_call = (None if public_only else capture_authenticated_search_call(
                self, session, getattr(identity, "identity_id", None),
            ))
            from .notice_c_private_series import capture_series_call, stamp_series_rows
            series_call = (None if public_only else capture_series_call(
                self, session, getattr(identity, "identity_id", None),
            ))
            terminal_empty = False
            if public_only:
                if not isinstance(session, ProviderSearchWithFacets):
                    session_rows = []
                    partial_failure = False
                else:
                    response = session.search_with_response_facets(
                        variant, provider_scope, field=effective_field,
                        content_types=tuple(content_types or ()),
                    )
                    if type(response) is not ProviderSearchResponse:
                        raise TypeError("Invalid provider search response")
                    session_rows = _public_rows_from_response(response, session)
                    partial_failure = response.partial is True
                    terminal_empty = _terminal_anonymous_empty(response, session)
                    if response_windows is not None and response.catalogue_complete is False:
                        response_windows.append(True)
            elif attempt == 0 and response_facets is not None:
                response = session.search_with_response_facets(
                    variant, provider_scope, field=effective_field,
                    content_types=tuple(content_types or ()),
                )
                if type(response) is not ProviderSearchResponse:
                    raise TypeError("Invalid provider search response")
                session_rows = response.results
                partial_failure = response.partial is True
                terminal_empty = _terminal_anonymous_empty(response, session)
                if response_windows is not None and response.catalogue_complete is False:
                    response_windows.append(True)
                response_facets.extend(_validated_response_facets(response.facets))
            else:
                try:
                    parameters = inspect.signature(session.search).parameters
                    kwargs = {"field": effective_field}
                    if "content_types" in parameters:
                        kwargs["content_types"] = tuple(content_types or ())
                    session_rows = session.search(variant, provider_scope, **kwargs)
                except TypeError as exc:
                    if "field" not in str(exc) or "unexpected keyword" not in str(exc):
                        raise
                    session_rows = session.search(variant, provider_scope)
                concrete = getattr(session, "client", None)
                partial_failure = any(
                    bool(getattr(source, "last_search_warnings", None))
                    for source in (session, concrete)
                )
            stamp_authenticated_search_rows(self, private_call, session_rows)
            stamp_series_rows(self, series_call, session_rows)
            for raw_row in session_rows:
                row = _provider_search_row(raw_row)
                key = _search_result_scope_key(row)
                if key not in provider_seen:
                    provider_seen.add(key); provider_rows.append(row)
            if partial_failure:
                warnings.append(f"{session.descriptor.display_name}: résultats partiels")
                break
            if terminal_empty:
                break
            if attempt == 0 and provider_rows:
                primary_ok = max(self._search_relevance(query, row) for row in provider_rows) >= 100
        except Exception as exc:
            from ..search_failures import search_failure_code
            # The public batch remains a closed verdict, but the local raw
            # journal must preserve the observed provider failure verbatim.
            # No additional request or provider data capture is performed.
            logging.getLogger("cifereca").warning(
                "TRACE provider search failure provider=%s query_variant=%s attempt=%s",
                session.provider_id, variant, attempt + 1, exc_info=True,
            )
            # Provider exceptions can include signed URLs, account IDs and
            # personal search terms. Neither the result batch nor diagnostics
            # may carry their raw message.
            failure_code = search_failure_code(exc) or "ProviderSearchFailed"
            if failure_code == "SearchTemporarilyUnavailable":
                _record_source_retry_after(self, session, identity, credential, exc, request_scope=request_scope)
            # An earlier variant returning a legitimate empty 200 does not
            # make a later transport/protocol failure disappear. Record the
            # terminal outage for the source, even when it is variant N>1,
            # so the caller can use the same bounded, read-only public
            # catalogue fallback as for a failure on the first attempt.
            terminal_error_code = failure_code
            terminal_error_message = _search_problem_label(failure_code)
            warnings.append(
                f"{session.descriptor.display_name}: {terminal_error_message}"
            )
            self.diagnostics.add(
                "PLUGIN", "search", "error",
                detail={"provider": session.provider_id, "attempt": attempt + 1, "q_len": len(variant),
                        "error": failure_code, "message": "search_provider_failure"},
            )
            from ..search_failures import should_stop_search_variants
            if should_stop_search_variants(exc):
                break
    if field in {"title", "author"}:
        provider_rows = [row for row in provider_rows if self._field_match(field, query, row)]
    if str(scope or "all").casefold() == "unknown":
        from ..core.search import support_kind
        provider_rows = [row for row in provider_rows if support_kind(asdict(row)) == "unknown"]
    return provider_rows, list(dict.fromkeys(warnings)), terminal_error_code, terminal_error_message


def _search_virtual_stream_session(self, session: ProviderSession, query: str, scope: str, *, field: str, content_types: tuple[str, ...], generation: int, publish: Callable[[ProviderSearchEvent], None], cancelled: Callable[[], bool], control: Any, request_scope=None) -> tuple[list[SearchResult], list[str], str, str]:
    """Worker-only optional stream. Gaps/errors do not poison the cache."""
    if request_scope is not None and cancelled():
        return [], [], "StreamCancelled", "search cancelled"
    identity = request_scope.identity if request_scope is not None else getattr(self, "current_identity", None)
    credential = getattr(session, "credential_id", None)
    if _source_in_retry_after(self, session, identity):
        code = "SearchTemporarilyUnavailable"
        message = _search_problem_label(code)
        return [], [message], code, message
    try:
        outcome = consume_provider_stream(
            session.search_stream(
                query, scope, field=field, content_types=content_types,
                control=control,
            ),
            provider_id=session.provider_id, catalog_generation=generation,
            publish=publish, cancelled=cancelled,
        )
        rows = [_provider_search_row(row) for row in outcome.rows]
        if field in {"title", "author"}:
            rows = [row for row in rows if self._field_match(field, query, row)]
        if str(scope or "all").casefold() == "unknown":
            from ..core.search import support_kind
            rows = [row for row in rows if support_kind(asdict(row)) == "unknown"]
        return rows, list(outcome.warnings), outcome.error_code, outcome.error_message
    except SearchStreamCancelled:
        return [], ["stream cancelled"], "StreamCancelled", "search superseded or cancelled"
    except SearchStreamFailure as exc:
        _log_observed_stream_failure(
            session.provider_id, "stream_validation", exc,
        )
        publish(ProviderSearchEvent(
            session.provider_id, generation, 0, "reset",
            message="resync after invalid/incomplete stream",
        ))
        # Rebuild from a complete catalogue query; do not reuse partial
        # stream results after a dropped message.
        if request_scope is not None:
            return _search_provider_session(
                self, session, query, scope, field=field, content_types=content_types,
                facade_type=type(self), request_scope=request_scope, request_cancelled=cancelled,
            )
        return self._search_provider_session(
            session, query, scope, field=field, content_types=content_types,
        )
    except Exception as exc:
        _log_observed_stream_failure(
            session.provider_id, "stream_delivery", exc,
        )
        # The transport can fail while opening or iterating the stream.
        # Reuse classic search semantics: only attested public outages allow
        # local fallback; authentication/access/proxy refusals remain distinct.
        from ..search_failures import search_failure_code
        code = search_failure_code(exc)
        if code is not None:
            if code == "SearchTemporarilyUnavailable":
                _record_source_retry_after(self, session, identity, credential, exc, request_scope=request_scope)
            message = _search_problem_label(code)
            return [], [message], code, message
        # Unknown stream failures retain their existing business result.
        return [], ["Recherche en flux indisponible"], "StreamProviderFailed", "Recherche en flux indisponible"


def search_progressive(self, query: str, scope: str='all', force: bool=False, *, provider_id: str='all', provider_ids: tuple[str, ...] | list[str] | None=None, field: str='all', content_types: tuple[str, ...] | list[str] | None=None, on_batch: Callable[[Any], None] | None=None, control: Any=None, on_event: Callable[[ProviderSearchEvent], None] | None=None, include_redundant_merged: bool=False, executor, logger, request_scope=None, return_rows: bool=False):
    """Parallel federated search with provider-scoped progressive batches.

    Providers keep their simple synchronous ``search`` contract.  Core owns
    concurrency, partial-failure isolation, cancellation checks, mirroring and
    final caching. Optional virtual providers emit validated stream events
    through ``on_event``; callbacks run on the search caller thread, never
    on provider threads. Existing ``on_batch`` remains compatible.
    By default synthetic.merged is skipped when searchable sibling origins
    are already selected, preventing duplicated remote work. Explicit
    ``include_redundant_merged=True`` opts in to both.
    """
    from datetime import datetime, timezone
    from ..plugin_api import CatalogSearchBatch, CatalogSearchSummary

    started_dt = datetime.now(timezone.utc)
    started_mono = time.monotonic()
    scoped = request_scope is not None
    if return_rows and not scoped:
        raise ValueError("search_read_scope_required")
    with self._search_generation_lock:
        if scoped:
            if type(request_scope) is not SearchReadScope or not request_scope.current(self):
                raise ValueError("search_source_changed")
            identity = request_scope.identity
            # Independent reader requests do not supersede GTK Search or
            # another reader. Their own source epoch and control own revocation.
            search_generation = None
        else:
            self._search_generation += 1
            search_generation = self._search_generation
            identity = self.current_identity
    requested = {str(x).strip().lower() for x in (provider_ids or ()) if str(x).strip() and str(x).strip().lower() != "local"}
    single = str(provider_id or "all").strip().lower()
    if single != "all": requested.add(single)
    available = [
        session for session in self._sessions.values()
        if session.connected and session.capabilities.search
        and _search_policy_enabled(self, session.provider_id, request_scope)
        and _search_credential_enabled(self, session)
        and (not requested or session.provider_id in requested)
    ]
    configured_search_preferences = _configured_search_preferences(
        self, identity, available,
    )
    searchable = _search_sessions_for_active_identity(
        self, available, identity,
        preferred_credentials=configured_search_preferences,
    )
    searchable = self._without_redundant_merged(
        searchable, field=field, scope=scope,
        include_redundant_merged=include_redundant_merged,
    )
    searchable.sort(key=lambda row: row.provider_id)
    preference_sensitive = any(
        session.provider_id in configured_search_preferences
        for session in searchable
    )
    # A single exact SQL owner snapshot per selected session, regardless
    # of provider. All snapshots are rechecked before publishing results.
    # Foreign responses require exact same-call public notice provenance.
    owner_leases = tuple(
        (session, _private_search_owner(self, session))
        for session in searchable
    )
    active_id = getattr(identity, "identity_id", None)
    foreign_owner_sessions = {
        id(session) for session, owner in owner_leases
        if not (type(active_id) is str and active_id and owner == active_id)
    }
    source_leases = tuple(
        (session, _inert_search_source_state(session))
        for session in searchable
    )
    provider_key = ",".join(sorted(x.provider_id for x in searchable)) or (",".join(sorted(requested)) or single)
    pref_key = _search_presentation_cache_scope(
        identity, searchable, owner_leases,
    )
    types = tuple(sorted(content_types or ()))
    virtual_versions = ",".join(
        f"{entry.plugin_id}:{entry.source_revision}:{entry.catalog_generation}"
        for session in searchable
        if (entry := self.registry.virtual_entry(session.provider_id)) is not None
    )
    cache_key = hashlib.sha256(f"search-v14-progressive\0{_search_session_cache_scope(searchable)!r}\0{_search_owner_cache_scope(owner_leases)!r}\0{provider_key}\0{scope}\0{field}\0{types!r}\0{pref_key}\0{query.casefold()}\0{virtual_versions}".encode()).hexdigest()
    ttl = int(self.config.get("cache.search_seconds", 21600))

    def request_current_locked() -> bool:
        if scoped:
            if not request_scope.current(self):
                return False
        elif (search_generation != self._search_generation
                or self.current_identity is not identity):
            return False
        # A reconnect or a changed Paris primary revokes old results.
        if (preference_sensitive
                and _configured_search_preferences(self, identity, available)
                != configured_search_preferences):
            return False
        try:
            registered = tuple(self._sessions.values())
        except RuntimeError:
            return False
        for session, expected in source_leases:
            if (expected is None or expected[2] is not True
                    or not any(item is session for item in registered)
                    or _inert_search_source_state(session) != expected
                    or not _search_policy_enabled(self, expected[0], request_scope)
                    or not _search_credential_enabled(self, session)):
                return False
        if any(_private_search_owner(self, session) != owner
               for session, owner in owner_leases):
            return False
        return True

    def request_cancelled() -> bool:
        with self._search_generation_lock:
            if not request_current_locked():
                return True
        return bool(control is not None and callable(getattr(control, "cancelled", None)) and control.cancelled())

    def emit(batch):
        if on_batch is None or request_cancelled(): return
        try: on_batch(batch)
        except Exception as exc:
            # A callback can fail with an exception containing UI text,
            # query terms, personal paths or a signed link. No raw text.
            self.diagnostics.add(
                "SEARCH", "progress callback", "error",
                detail={"error": "CallbackFailed", "message": "search_progress_callback_failed"},
            )

    if not force:
        cached = self.storage.cache_get("search", cache_key)
        cached_contents = _search_cache_contents(cached, searchable)
        if cached_contents is not None:
            cached_rows, window_sources = cached_contents
            rows = [_provider_search_row(SearchResult(**x)) for x in cached_rows]
            groups: dict[str, list[SearchResult]] = {}
            selected = {session.provider_id for session in searchable}
            ambiguous = False
            for row in rows:
                # A virtual aggregate can legitimately return books whose
                # source_provider names an origin, not its selected session.
                # With one selected session the producer is unambiguous.
                if row.source_provider in selected:
                    owner = row.source_provider
                elif len(searchable) == 1:
                    owner = searchable[0].provider_id
                else:
                    # The legacy list-only cache has lost which selected
                    # session produced this origin. Never silently drop it or
                    # assign its book to an unrelated source: redo the query.
                    ambiguous = True
                    break
                groups.setdefault(owner, []).append(row)
            if not ambiguous:
                # Cache rows are not new documentary observations: compute
                # only exact source coordinates. N2 must still find an already
                # persisted Notice C source before selecting any image.
                _stamp_cached_public_notice_refs(
                    rows, identity_id=identity.identity_id if identity else None,
                storage=self.storage,
                )
                # An empty 200 also earns a cached verdict; session ownership
                # remains observable for the same selected provider set.
                delivered = 0
                cached_warnings = []
                for session in searchable:
                    if request_cancelled():
                        break
                    batch_warnings = (
                        (_catalogue_window_warning(session),)
                        if session.provider_id in window_sources else ()
                    )
                    cached_warnings.extend(batch_warnings)
                    emit(CatalogSearchBatch(
                        session.provider_id, session.descriptor.display_name,
                        tuple(groups.get(session.provider_id, ())),
                        warnings=batch_warnings,
                        cached=True, result_status=_search_batch_status(
                            groups.get(session.provider_id, ()), batch_warnings, "", cached=True
                        ),
                    ))
                    delivered += 1
                cancelled = request_cancelled()
                if not cancelled:
                    with self._search_generation_lock:
                        if request_current_locked():
                            if not scoped:
                                self.last_search = rows; self.last_search_warnings = cached_warnings
                        else:
                            cancelled = True
                completed = datetime.now(timezone.utc).isoformat(timespec="seconds")
                summary = CatalogSearchSummary(query, len(rows), len(searchable), delivered, 0, cancelled, tuple(cached_warnings), started_dt.isoformat(timespec="seconds"), completed)
                return (summary, () if cancelled else tuple(rows)) if return_rows else summary

    aggregate: list[SearchResult] = []
    progressive_c_pairs = []
    early_c = on_batch is not None
    warnings: list[str] = []
    window_sources: set[str] = set()
    failed = completed_count = 0
    cancelled = False
    futures = {}
    streamed: dict[str, int] = {}
    event_queue: Queue[ProviderSearchEvent] = Queue(maxsize=512)

    def publish_from_provider(event: ProviderSearchEvent) -> None:
        # Streaming upserts must obey the same Core-owned provenance boundary
        # before either a UI event or a provider batch becomes observable.
        if event.kind == "upsert" and isinstance(event.result, SearchResult):
            event = replace(event, result=_provider_search_row(event.result))
        # Bounded queue enforces backpressure on a noisy provider.
        while not request_cancelled():
            try:
                event_queue.put(event, timeout=0.05)
                return
            except Full:
                continue

    def drain_stream_events() -> None:
        while True:
            try:
                event = event_queue.get_nowait()
            except Empty:
                return
            if on_event is None or request_cancelled():
                continue
            entry = self.registry.virtual_entry(event.provider_id)
            if entry is None or entry.catalog_generation != event.catalog_generation:
                continue
            try:
                on_event(event)
            except Exception:
                self.diagnostics.add(
                    "SEARCH", "stream callback", "error",
                    detail={"code": "stream_callback_failed"},
                )

    if searchable:
        for session in searchable:
            if request_cancelled():
                cancelled = True
                break
            entry = self.registry.virtual_entry(session.provider_id)
            response_facets: list[CatalogSearchFacet] = []
            response_windows: list[bool] = []
            if scoped:
                # Pass the exact reader and cancellation fence into the
                # worker. Never recover it from global navigation.
                foreign = id(session) in foreign_owner_sessions
                if (not foreign and entry is not None
                        and "catalog.search.stream" in entry.capabilities
                        and isinstance(session, ProviderSearchStream)
                        and self._session_search_capabilities(session).supports_field(field)):
                    streamed[session.provider_id] = entry.catalog_generation
                    future = executor.submit(
                        _search_virtual_stream_session, self, session, query, scope,
                        field=field, content_types=tuple(content_types or ()),
                        generation=entry.catalog_generation,
                        publish=publish_from_provider, cancelled=request_cancelled,
                        control=control, request_scope=request_scope,
                    )
                else:
                    future = executor.submit(
                        _search_provider_session, self, session, query, scope,
                        field=field, content_types=tuple(content_types or ()),
                        facade_type=type(self), public_only=foreign,
                        response_facets=(response_facets if not foreign and on_batch is not None
                                         and isinstance(session, ProviderSearchWithFacets) else None),
                        response_windows=response_windows,
                        request_scope=request_scope, request_cancelled=request_cancelled,
                    )
            elif id(session) in foreign_owner_sessions:
                # Foreign streams have no per-upsert public response provenance.
                # Force one response-bound worker and suppress stream events.
                future = executor.submit(
                    _search_provider_session, self, session, query, scope,
                    field=field, content_types=tuple(content_types or ()),
                    facade_type=type(self), public_only=True, response_windows=response_windows,
                )
            elif (
                entry is not None
                and "catalog.search.stream" in entry.capabilities
                and isinstance(session, ProviderSearchStream)
                and self._session_search_capabilities(session).supports_field(field)
            ):
                streamed[session.provider_id] = entry.catalog_generation
                future = executor.submit(
                    self._search_virtual_stream_session, session, query, scope,
                    field=field, content_types=tuple(content_types or ()),
                    generation=entry.catalog_generation,
                    publish=publish_from_provider, cancelled=request_cancelled,
                    control=control,
                )
            elif on_batch is not None and isinstance(session, ProviderSearchWithFacets):
                future = executor.submit(
                    _search_provider_session, self, session, query, scope,
                    field=field, content_types=tuple(content_types or ()), facade_type=type(self),
                    response_facets=response_facets, response_windows=response_windows,
                )
            else:
                future = executor.submit(
                    self._search_provider_session, session, query, scope,
                    field=field, content_types=tuple(content_types or ()),
                )
            futures[future] = (session, time.monotonic(), response_facets, response_windows)
        pending = set(futures)
        while pending:
            if request_cancelled():
                cancelled = True
                for future in pending:
                    future.cancel()
                break
            if control is not None:
                try:
                    check = getattr(control, "check_cancelled", None)
                    if callable(check):
                        check()
                except Exception:
                    cancelled = True
                    for future in pending:
                        future.cancel()
                    break
            drain_stream_events()
            done, pending = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                session, provider_started, response_facets, response_windows = futures[future]
                try:
                    provider_rows, provider_warnings, error_code, error_message = future.result()
                except Exception as exc:
                    from ..search_failures import search_failure_code
                    logger.warning(
                        "TRACE provider search worker failure provider=%s",
                        session.provider_id, exc_info=True,
                    )
                    # A failed future can bypass the worker's normal sanitized
                    # error boundary. Preserve only a fixed reason code.
                    error_code = search_failure_code(exc) or "ProviderSearchFailed"
                    error_message = _search_problem_label(error_code)
                    provider_rows, provider_warnings = [], [
                        f"{session.descriptor.display_name}: {error_message}"
                    ]
                if request_cancelled():
                    cancelled = True
                    break
                # An inventory revision during a search invalidates any
                # partial results from the former generation.
                expected = streamed.get(session.provider_id)
                if expected is not None:
                    entry = self.registry.virtual_entry(session.provider_id)
                    if entry is None or entry.catalog_generation != expected:
                        cancelled = True
                        provider_rows = []
                        provider_warnings = []
                        error_code = "StreamStale"
                        error_message = "source generation changed"
                # A transport/protocol outage cannot be repaired by another
                # equivalent request. Offer only identity-scoped *public*
                # records already mirrored in SQLite; they are never promoted
                # to fresh catalogue results or purchase rights.
                live_rows = provider_rows
                stale_attested = False
                if (
                    error_code == "SearchTemporarilyUnavailable"
                    and not live_rows and not request_cancelled()
                ):
                    from .known_catalog_fallback import known_public_fallback_page
                    offline = known_public_fallback_page(
                        self.storage, provider_id=session.provider_id,
                        identity_id=identity.identity_id if identity is not None else None,
                        query=query, scope=scope, field=field,
                        content_types=types,
                    )
                    if offline.results:
                        provider_rows = list(offline.results)
                        stale_attested = True
                        provider_warnings = [
                            f"{session.descriptor.display_name}: résultats connus, recherche en ligne indisponible"
                        ]
                        if offline.has_more:
                            provider_warnings.append(
                                f"{session.descriptor.display_name}: "
                                + _STALE_PUBLIC_TRUNCATED_WARNING
                            )
                    else:
                        provider_warnings = [
                            f"{session.descriptor.display_name}: "
                            + _stale_public_fallback_warning(offline.status)
                        ]
                if early_c and identity is not None and not request_cancelled():
                    # Public C observations have no account/rights; the private
                    # catalogue mirror remains a final, admitted aggregate.
                    # Never publish a batch before its documentary producer.
                    from .notice_c_private_search_web import persist_authenticated_search_rows
                    persist_authenticated_search_rows(
                        self, live_rows, identity_id=identity.identity_id,
                    )
                    from .notice_c_private_series import persist_series_rows
                    persist_series_rows(self, live_rows, identity_id=identity.identity_id)
                    _clear_public_c_stamps(live_rows)
                    progressive_c_pairs.extend(_ingest_public_search_c(
                        self, live_rows, identity_id=identity.identity_id,
                        now=int(time.time()), require_public_cover_witness=True,
                    ))
                    if request_cancelled():
                        cancelled = True
                        break
                aggregate.extend(provider_rows)
                warnings.extend(provider_warnings)
                if response_windows:
                    window_sources.add(session.provider_id)
                    provider_warnings = [*provider_warnings, _catalogue_window_warning(session)]
                completed_count += 1
                if error_code:
                    failed += 1
                if not cancelled:
                    emit(CatalogSearchBatch(
                        session.provider_id, session.descriptor.display_name, tuple(provider_rows),
                        tuple(provider_warnings), error_code, error_message, False,
                        int((time.monotonic() - provider_started) * 1000),
                        _search_batch_status(
                            provider_rows, provider_warnings, error_code,
                            stale_attested=stale_attested,
                        ),
                        response_facets=tuple(response_facets) if not error_code else (),
                    ))
                if control is not None and callable(getattr(control, "report", None)):
                    try:
                        control.report(current=completed_count, total=len(searchable),
                                       message=f"Recherche · {session.descriptor.display_name}")
                    except Exception:
                        pass
        if not cancelled:
            drain_stream_events()
    if request_cancelled():
        cancelled = True

    unique: dict[tuple[str, str, str, str, str], SearchResult] = {}
    for row in aggregate: unique[_search_result_scope_key(row)] = row
    rows = list(unique.values())
    variants = self._search_query_variants(query)
    if scoped:
        prefs = identity.preferences
        preference_scores = {
            id(row): _search_preference_score(
                row, reading_languages=tuple(prefs.reading_languages),
                prefer_original_language=prefs.prefer_original_language is True,
            ) for row in rows
        }
    else:
        preference_scores = {id(row): self._search_preference_score(row) for row in rows}
    if len(variants) > 1 or any(preference_scores.values()):
        rows.sort(key=lambda row: (self._search_relevance(query, row) if len(variants) > 1 else 0, preference_scores.get(id(row), 0)), reverse=True)
    warnings = list(dict.fromkeys(warnings))
    cancelled = cancelled or request_cancelled()
    if not cancelled and identity is not None:
        # Stale offline fallbacks must NEVER get a fresh mirrored_at timestamp.
        live_results = [
            row for row in rows
            if row.metadata.get("search_provenance") != "known_catalog_stale"
        ]
        try: _mirror_live_search_results(
            self, live_results, identity_id=identity.identity_id,
            c_already_ingested=early_c,
        )
        except Exception: logger.warning("TRACE catalog mirror progressive search code=mirror_failed")
        if early_c and not request_cancelled():
            # A later supplier can complete a strong edition relation. Reuse
            # the already persisted source snapshots; don't write observations
            # twice or resurrect a superseded duplicate result.
            keep = {id(row) for row in live_results}
            pairs = [pair for pair in progressive_c_pairs if id(pair[0]) in keep]
            if len({pair[0].source_provider for pair in pairs}) > 1:
                try:
                    recover = getattr(self, "_notice_work_recover_search", None)
                    committed = getattr(self, "_notice_work_search_committed", None)
                    failed_c = [] if callable(recover) and not callable(committed) else None
                    _reconcile_progressive_public_c(
                        self.storage, pairs, restore_own=True, failed_sources=failed_c,
                        read_only=callable(committed),
                    )
                    if failed_c:
                        recover(tuple(failed_c), now=int(time.time()))
                except Exception:
                    logger.warning("TRACE notice c progressive code=late_reconciliation_failed")
    cancelled = cancelled or request_cancelled()
    if not cancelled and not warnings and failed == 0:
        self.storage.cache_put("search", cache_key, _search_cache_payload(rows, window_sources), ttl)
    warnings.extend(_catalogue_window_warning(session) for session in searchable
                    if session.provider_id in window_sources)
    if not cancelled:
        with self._search_generation_lock:
            if request_current_locked():
                if not scoped:
                    self.last_search = rows; self.last_search_warnings = warnings
            else:
                cancelled = True
    self.diagnostics.add("SEARCH", "progressive federated", detail={"providers": provider_key, "field": field, "results": len(rows), "completed": completed_count, "failed": failed, "cancelled": cancelled, "dt_ms": int((time.monotonic()-started_mono)*1000)}, level="detailed")
    completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summary = CatalogSearchSummary(query, len(rows), len(searchable), completed_count, failed, cancelled, tuple(warnings), started_dt.isoformat(timespec="seconds"), completed_at)
    return (summary, () if cancelled else tuple(rows)) if return_rows else summary


def search_local_library(self, query: str, scope: str='all', *, field: str='all', identity_id: str, age: int | None=None, age_kind: str | None=None, logger) -> list[dict[str, Any]]:
    # Core workers must not read GTK navigation while resolving private
    # local placements. Identity is captured by the public facade or caller.
    if type(identity_id) is not str or not identity_id.strip():
        return []
    from ..core.search import LocalLibrarySearch

    resolved_identity = identity_id
    rows = LocalLibrarySearch(self.library_entries).search(
        query, identity_id=resolved_identity, scope=scope, field=field,
        age=age, age_kind=age_kind,
    )
    logger.info(
        "TRACE search local identity=%s query_len=%s scope=%s field=%s results=%s",
        resolved_identity, len(str(query or "")), scope, field, len(rows),
    )
    return rows


def search_known_catalog(self, query: str, scope: str='all', *, field: str='all', identity_id: str, provider_ids: list[str] | tuple[str, ...] | None=None, content_types: list[str] | tuple[str, ...] | None=None, age: int | None=None, age_kind: str | None=None, logger) -> list[dict[str, Any]]:
    """Read a known-catalogue snapshot only for a pre-admitted identity.

    SQLite notices, local placements and remotely observed rights may each
    disclose private titles. Their scope must not be chosen after enqueue
    from the mutable GTK current_identity.
    """
    if type(identity_id) is not str or not identity_id.strip():
        return []
    from ..core.search import KnownCatalogSearch

    resolved_identity = identity_id
    # The known Search task is ALREADY queued off the GTK main thread.
    # Read the durable public C cursor FIRST, before consulting per-profile
    # legacy mirrors/possessions, and before any separate online extension.
    # The current synchronous known-task callback still returns a combined
    # snapshot; progressive public C UI batches remain a subsequent gate.
    c_page = None
    if (type(query) is str and 2 <= len(query.strip()) <= 160
            and field in ("all", "title", "author", "publisher", "subject", "series", "language")
            and scope in ("all", "digital", "physical", "unknown")):
        from ..storage import Storage
        if isinstance(getattr(self, "storage", None), Storage):
            from .notice_c_public_search import search_public_notice_c
            from .notice_c_known_projection import merge_public_notice_c_known_rows
            from sqlite3 import Error as SQLiteError
            try:
                c_page = search_public_notice_c(
                    self.storage, query, field=field, scope=scope,
                    provider_ids=tuple(provider_ids or ()), group_by_document=True,
                    content_types=tuple(content_types or ()),
                    age=age, age_kind=age_kind,
                )
            except (SQLiteError, ValueError, TypeError, AttributeError):
                # Failed public metadata must never erase private possessions
                # or turn an unproven empty C page into a search-zero verdict.
                c_page = None
    rows = KnownCatalogSearch(self.catalog_entries, self.library_entries).search(
        query, identity_id=resolved_identity, scope=scope, field=field,
        providers=provider_ids, content_types=content_types,
        age=age, age_kind=age_kind,
    )
    if c_page is not None:
        # The C cursor already filters each source BEFORE grouping/page.
        # The mirror is suppressed only against a visible exact source.
        rows = merge_public_notice_c_known_rows(
            rows, c_page, identity_id=resolved_identity,
        )
    logger.info(
        "TRACE search known identity=%s query_len=%s scope=%s field=%s results=%s",
        resolved_identity, len(str(query or "")), scope, field, len(rows),
    )
    return rows


def filter_loaded_search_results(rows: list[dict[str, Any]], query: str='', *, field: str='all', scope: str='all', provider_ids: list[str] | tuple[str, ...] | None=None, content_types: list[str] | tuple[str, ...] | None=None, age: int | None=None, age_kind: str | None=None) -> list[dict[str, Any]]:
    """Apply current Search controls without network or cache mutation."""
    from ..core.search import filter_loaded_results
    return filter_loaded_results(
        rows, query, providers=provider_ids, content_types=content_types,
        scope=scope, field=field, age=age, age_kind=age_kind,
    )


def _valid_public_web_provider_id(provider_id):
    """Allow bounded registry IDs, including exact dotted tenant namespaces."""
    return (type(provider_id) is str and 0 < len(provider_id) <= 64
            and re.fullmatch(r"[a-z][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*",
                             provider_id) is not None)


def _is_public_web_evidence(evidence, provider_id):
    """Closed, provider-qualified public parser field types (not URL inference)."""
    return (_valid_public_web_provider_id(provider_id)
            and type(evidence) is str
            and evidence in (
                f"{provider_id}.public_search_product_card",
                f"{provider_id}.public_search_record_identifier",
                f"{provider_id}.public_search_record_query",
                f"{provider_id}.public_product_jsonld",
            ))


def _public_catalog_url(
    value: Any, *, cover: bool = False, evidence: str | None = None,
    provider_id: str | None = None, record_id: str | None = None,
) -> str | None:
    """Structurally validate public URLs without supplier-specific decisions.

    Product URLs require exact producer-qualified evidence; a private search
    result or an arbitrary friendly URL cannot grant public-link authority.
    Only the producing plugin can attest its public title/card parser.
    """
    from urllib.parse import urlsplit
    import ipaddress

    if type(value) is not str or not 0 < len(value) <= 512:
        return None
    if (value != value.strip() or "\\" in value
            # The cover path returns before catalogue_notice_url. Apply the
            # same invisible-control fence to both covers and book links.
            or any(c.isspace() or unicodedata.category(c) in {"Cc", "Cf", "Cs"}
                   or ord(c) < 33 or ord(c) == 127 for c in value)):
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").casefold()
        if (parsed.scheme != "https" or parsed.username is not None
                or parsed.password is not None or parsed.fragment
                or parsed.port not in (None, 443)
                or not parsed.path.startswith("/") or "%" in parsed.path
                or any(part in {".", ".."} for part in parsed.path.split("/"))
                or host.endswith(".") or len(host) > 253
                or host.endswith((".localhost", ".local", ".internal",
                                  ".home.arpa", ".invalid", ".test", ".example"))
                or host in {"localhost", "metadata.google.internal"}
                or len(host.split(".")) < 2
                or any(re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label,
                ) is None for label in host.split("."))):
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return None
        # A product page and an image may not point to a private account,
        # delivery, claim or authentication endpoint, even when the parser
        # supplied otherwise valid public evidence.
        if _private_catalogue_path(parsed.path):
            return None
        if cover:
            return value if not parsed.query else None
        # An acquisition artifact or fulfillment token is never a product
        # notice, regardless of which provider-qualified source produced
        # the original public URL marker. No supplier host/path grammar.
        if parsed.path.casefold().endswith((
            ".acsm", ".lcpl", ".epub", ".pdf", ".zip",
        )):
            return None
        # No evidence inferred from the URL, a known provider, an owned
        # flag, or a catalogue row. The marker must match the producer exactly.
        if (not _valid_public_web_provider_id(provider_id)
                or not (evidence == f"{provider_id}.library_title_link"
                        or _is_public_web_evidence(evidence, provider_id))):
            return None
        # The optional historical library reference belongs only to the
        # private-card marker, never an anonymous search card.
        allowed_queries = (
            ("", "ref=library") if evidence == f"{provider_id}.library_title_link"
            else ("",)
        )
        if evidence == f"{provider_id}.public_search_record_query":
            # Distinct ABI for a literal catalogue detail endpoint with
            # one public numeric record argument. The legacy stable record
            # coordinate is the digest of this exact observed URL.
            # Neither a free-form query nor a different record is allowed.
            from hashlib import sha256
            if (not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*details?\.(?:php|aspx)", parsed.path)
                    or not re.fullmatch(r"(?:book|record|id)=[1-9][0-9]{0,15}", parsed.query)
                    or type(record_id) is not str
                    or record_id != sha256(value.encode("utf-8")).hexdigest()[:24]):
                return None
        elif parsed.query not in allowed_queries:
            return None
        # A producer-qualified marker does not turn a generic search/feed
        # route or homepage into a book-specific HTML notice.
        from ..catalogue_link import catalogue_notice_url
        return catalogue_notice_url({"friendly_url": value})
    except (ValueError, AttributeError, TypeError):
        return None


_PUBLIC_SEARCH_TEXT_FIELDS = (
    "awards", "prizes", "literary_prizes", "keywords", "themes", "tags",
    "summary", "synopsis",
)


def _safe_public_search_text(key: str, value: Any) -> str | list[str] | None:
    """Persist only bounded bibliographic text, never arbitrary Kobo extras.

    This projection supplements the established metadata allowlist without
    importing raw URLs, account fields, cookie headers or nested dictionaries
    from an untrusted catalogue search result.
    """
    limit = 4000 if key in {"description", "summary", "synopsis"} else 240

    def clean(raw: Any) -> str | None:
        if type(raw) is not str or not 0 < len(raw) <= limit:
            return None
        if any(ord(char) < 32 and char not in "\n\r\t" for char in raw):
            return None
        if re.search(
            r"(?i)(?:https?://|(?:token|auth|cookie|signature|access_token|"
            r"refresh_token)\s*=|(?:bearer|basic)\s+\S+|"
            r"[^\s@]+@[^\s@]+\.[^\s@]+)",
            raw,
        ):
            return None
        normalized = " ".join(raw.split())
        return normalized if normalized else None

    if type(value) is str:
        return clean(value)
    if type(value) in (list, tuple) and len(value) <= 32:
        values = [clean(part) for part in value]
        return values if values and all(part is not None for part in values) else None
    return None



# SearchService Resource facets: retain only exact source-attested fields.
# Evidence is bibliographic, never entitlement or acquisition authority.
_RESOURCE_FACET_FIELDS = {
    "subjects": "Subj",
    "tags": "Tags",
    "paris_blog_categories": "BlogPostCategories",
    "paris_blog_tags": "BlogPostTags",
}


def _safe_resource_facets(
    metadata: dict[str, Any], scenario: str, *, provider_id: str,
) -> dict[str, Any]:
    """Persist bounded Resource facets only with exact source-bound evidence.

    The historical helper name remains for existing callers; admission is
    based on the producing provider identity, not a Core supplier dispatch.
    """
    if (scenario not in {"CATALOGUE", "EBOOKS"}
            or type(provider_id) is not str or not provider_id
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider_id) is None):
        return {}
    evidence = metadata.get("bibliography_evidence")
    if type(evidence) is not dict:
        return {}

    fields: dict[str, Any] = {}
    proofs: dict[str, dict[str, str]] = {}
    for name, source_field in _RESOURCE_FACET_FIELDS.items():
        raw_values = metadata.get(name)
        proof = evidence.get(name)
        if type(raw_values) is not list or not 1 <= len(raw_values) <= 12:
            continue
        expected = {
            "source": f"{provider_id}.syracuse.search",
            "source_field": "Resource." + source_field,
            "scope": scenario,
            "confidence": "upstream_field_present",
            "freshness": "search_response",
        }
        if type(proof) is not dict or proof != expected:
            continue
        cleaned = _safe_public_search_text(name, raw_values)
        if type(cleaned) is not list or len(cleaned) != len(raw_values):
            continue
        fields[name] = cleaned
        proofs[name] = expected
    if proofs:
        fields["bibliography_evidence"] = proofs
    return fields


def mirrored_catalogue_scoped_id(
    identity_id: str, provider_id: str, record_id: str, credential_id: str,
    docbase: str, source_collection: str | None,
) -> str | None:
    """Canonical Core v2 cache key for ONE exact known supplier notice.

    This is a pure local-key calculation, not a right, catalogue GET or proof
    that the notice's account is currently connected. Empty collection is
    an exact absent collection, never a wildcard over other editions.
    """
    parts = (
        identity_id, provider_id, record_id, credential_id,
        docbase, source_collection if source_collection is not None else "",
    )
    if (any(type(value) is not str or not value or len(value) > 512
            or "\x00" in value for value in parts[:5])
            or type(parts[5]) is not str or len(parts[5]) > 512
            or "\x00" in parts[5]):
        return None
    scope = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(("mirror-v2\0" + scope).encode()).hexdigest()


def mirrored_catalogue_revisioned_id(
    identity_id: str, row: SearchResult,
) -> str | None:
    """Read-only mirror-v3 locator for one unambiguously versioned edition.

    This is a legacy *presentation* cache, not the authority for Notice C or
    holdings. The source account participates in the opaque hash, not a
    person's display/profile ID. A malformed revision must not overwrite
    a valid mirror-v2 row or acquire a v3 source certification.
    """
    if (type(row) is not SearchResult
            or type(row.revision_id) is not str
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", row.revision_id,
            ) is None
            or row.medium not in ("digital", "physical", "unknown")
            or (row.source_account_id is not None
                and (type(row.source_account_id) is not str
                     or not row.source_account_id
                     or len(row.source_account_id) > 512
                     or "\0" in row.source_account_id))):
        return None
    if mirrored_catalogue_scoped_id(
        identity_id, row.source_provider, row.record_id,
        row.source_credential, row.docbase, row.source_collection,
    ) is None:
        return None
    coordinates = (
        identity_id, row.source_provider, row.record_id,
        row.source_credential, row.docbase, row.source_collection or "",
        row.source_account_id or "", row.medium, row.revision_id,
    )
    raw = json.dumps(coordinates, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(("mirror-v3\0" + raw).encode()).hexdigest()


def _mirrored_catalogue_key(identity_id: str, row: SearchResult) -> tuple[str, bool]:
    """Keep historical incomplete scopes read-only; never infer a source."""
    if row.revision_id not in (None, ""):
        revised = mirrored_catalogue_revisioned_id(identity_id, row)
        if revised is not None:
            return revised, True
        # An invalid revision must not overwrite a former valid v2 entry.
        # Keep its exact bad source isolated but NEVER mark it qualified.
        opaque = (
            identity_id, row.source_provider, row.record_id,
            row.source_credential, row.docbase, row.source_collection,
            row.source_account_id, row.medium, row.revision_id,
        )
        raw = json.dumps(opaque, ensure_ascii=True, default=str)
        return hashlib.sha256(
            ("mirror-unqualified-revision\0" + raw).encode(),
        ).hexdigest(), False
    scoped = mirrored_catalogue_scoped_id(
        identity_id, row.source_provider, row.record_id,
        row.source_credential, row.docbase, row.source_collection,
    )
    if scoped is not None:
        return scoped, True
    legacy = f"mirror\0{identity_id}\0{row.source_provider}\0{row.record_id}"
    return hashlib.sha256(legacy.encode()).hexdigest(), False


def _stamp_cached_public_notice_refs(
    rows: list[SearchResult], *, identity_id: str | None,
    storage: object | None = None,
) -> None:
    """Replay cached public Search coordinates from the durable C source.

    A cache HIT does NOT recreate the nonserializable Core live-producer
    witness and is never documentary evidence by itself. For bounded Core
    workers with storage, upgrade only after reading an already persisted
    canonical C contribution which exactly matches the public bibliographic
    source. That index survives the shorter provider-source TTL.

    Source-coordinate-only fallback preserves the old read-through contract;
    N2 must still independently verify source bytes before showing a cover.
    No HTTP, source writes, private data, GTK DB access or rights.
    """
    if type(identity_id) is not str or not identity_id:
        return
    from .notice_search_cover_producer import admit_public_search_cover
    from .notice_cover_projection import notice_cover_document_ref
    from .notice_c_exact_source_lookup import exact_public_source_c_many
    from .notice_c_cached_revision import stamp_cached_c_revision

    admitted = []

    for index, row in enumerate(rows):
        if type(row) is not SearchResult:
            continue
        for key in (
            "_core_notice_cover_document_ref",
            "_core_notice_cover_document_epoch",
            "_core_notice_canonical_cover_document_ref",
            "_core_notice_canonical_cover_document_epoch",
            "_core_persisted_c_revision_witness",
            "_core_cached_c_receipt",
            "_core_notice_cross_provider_proof",
        ):
            row.__dict__.pop(key, None)
        safe_url = _shared_catalogue_cover_url(row.thumbnail_url)
        if safe_url is None:
            continue
        # A candidate coordinate is not an upstream freshness timestamp.
        try:
            snapshot = admit_public_search_cover(
                row, identity_id=identity_id, observed_at=1,
                public_cover_url=safe_url,
            )
            if snapshot is None:
                continue
            exact = notice_cover_document_ref(snapshot.key)
            row._core_notice_cover_document_ref = exact
            # Bounded indexed Core read, never a scan of supplier/HTTP cache.
            # The UI's GTK bind may only consume the resulting exact ref.
            if storage is None or index >= 256:
                continue
            coordinates = (row.source_provider, row.record_id, row.docbase,
                           row.medium, row.revision_id)
            admitted.append((row, exact, snapshot, coordinates))
        except (ValueError, TypeError, UnicodeError, AttributeError):
            continue

    if not admitted:
        return
    resolved = exact_public_source_c_many(storage, [x[3] for x in admitted])
    for row, exact, snapshot, coordinates in admitted:
        try:
            persisted = resolved.get(coordinates)
            if persisted is None or not persisted.candidates:
                continue
            for source in persisted.sources:
                facts = source.public_bibliography
                if (source.source_ref == exact.source_key
                        and source.provider_id == snapshot.key.provider_id
                        and source.visibility == "public"
                        and source.state == "available"
                        and facts is not None
                        and facts.edition_id == snapshot.key.edition_id
                        and facts.record_id == row.record_id
                        and facts.docbase == row.docbase
                        and facts.medium == row.medium
                        and facts.source_revision == row.revision_id
                        and facts.title == row.title
                        and facts.author == row.author):
                    row._core_notice_canonical_cover_document_ref = (
                        persisted.document
                    )
                    row._core_notice_canonical_cover_document_epoch = (
                        persisted.epoch
                    )
                    stamp_cached_c_revision(row, persisted, identity_id=identity_id)
                    break
        except (ValueError, TypeError, UnicodeError, AttributeError):
            # Corrupt/stale canonical metadata is not an absent source or a
            # permission; keep the legacy exact-coordinate read-through.
            # Never log raw source strings, credentials or signed cover URLs.
            continue


def _mirror_remote_results(
    self, rows: list[SearchResult], *, identity_id: str | None = None,
    require_public_cover_witness: bool = False,
) -> int:
    """Persist remote notices only for an identity pinned at admission.

    A catalogue observation is not a right, and GTK navigation is not an
    authority for placing either private or public metadata in SQLite.
    """
    # In particular, an explicit blank/invalid identity must never be
    # replaced by the mutable selected profile while a provider worker runs.
    if type(identity_id) is not str or not identity_id.strip():
        return 0
    now = int(time.time())
    if not _CORE_PROGRESSIVE_C_WRITTEN.get():
        _clear_public_c_stamps(rows)
    # A catalogue mirror contains descriptive observations only. Neither a
    # historical entitlement nor a supplier-owned flag can select its state.
    # Private metadata remains scoped; an actual offer requires separate
    # exact source admission and live verification.
    keyed: list[tuple[str, SearchResult, str, bool]] = []
    for row in rows:
        state = (
            # This is descriptive only: a supplier price is not a purchase right.
            "available_to_buy" if type(row.price) is str and bool(row.price.strip()) else
            # A public catalogue notice is not a borrowing right.
            # Borrowability must be established by the independent lender
            # policy/holding evidence, never inferred from provider or medium.
            "remote_catalog"
        )
        key, qualified = _mirrored_catalogue_key(identity_id, row)
        keyed.append((key, row, state, qualified))
    existing_by_id = self.storage.catalog_entries_by_ids([key for key, _row, _state, _qualified in keyed])
    upserts: list[dict[str, Any]] = []
    for key, row, state, qualified in keyed:
        existing = existing_by_id.get(key)
        if existing and existing.get("local_state") == "local":
            continue
        raw_meta = dict(row.metadata or {})
        # "rights" is an untrusted provider claim (possibly a signed
        # token), never a public bibliographic field nor ownership evidence.
        # No legacy or provider right participates in the mirror state.
        # This rule is provider-neutral: unknown plugins must not leak it.
        raw_meta.pop("rights", None)
        allowed_meta = {
            name: raw_meta.get(name)
            for name in ("subjects", "subject", "series", "collection", "publisher", "languages", "isbn", "isbns", "original_language", "authors", "contributors", "identifiers", "edition", "edition_statement", "series_index", "collection_index", "genre", "genres", "categories", "audience", "target_age", "dates")
            if raw_meta.get(name) not in (None, "", [], {})
        }
        # Kobo-Vault and other providers may already expose attested awards,
        # subjects and summaries. Preserve *only* sanitized bibliographic
        # fields of the same exact source item, not its private extras.
        for name in _PUBLIC_SEARCH_TEXT_FIELDS:
            safe = _safe_public_search_text(name, raw_meta.get(name))
            if safe is not None:
                allowed_meta[name] = safe
        # Only persist bounded Resource facets whose exact, scenario-specific
        # upstream evidence matches the source of this same result. No
        # supplier identity grants additional metadata or a right.
        allowed_meta.update(_safe_resource_facets(
            raw_meta, row.scenario, provider_id=row.source_provider,
        ))
        # The generic youth-age contract is also edition-scoped and already
        # structured. Keep only its validated bounds/kind and harmless original
        # label; never persist a raw nested provider age object, provenance
        # URL, signed token, other edition's range, or inferred age.
        from .attested_age_filter import attested_age_ranges
        verified_ages = attested_age_ranges({
            "provider_id": row.source_provider, "item_id": row.record_id,
            "metadata": {"youth_age_ranges": raw_meta.get("youth_age_ranges")},
        })
        public_ages = []
        for proof in verified_ages:
            label = proof.source_label
            if label and _safe_public_search_text("age_label", label) is None:
                continue
            public_ages.append({
                "age_min": proof.age_min, "age_max": proof.age_max,
                "age_kind": proof.age_kind, "source_label": label,
            })
        if public_ages:
            allowed_meta["youth_age_ranges"] = public_ages
        # A cover is descriptive, never a session or delivery URL. Apply
        # the same conservative public-image boundary to every provider.
        cover_url = _shared_catalogue_cover_url(row.thumbnail_url)
        # Persist a product URL only when this exact producing provider
        # supplies an eligible public-link marker and the URL is non-private.
        product_url = _public_catalog_url(
            row.friendly_url, evidence=raw_meta.get("public_url_evidence"),
            provider_id=row.source_provider,
            record_id=row.record_id if _verified_public_web_reference(row) else None,
        )
        upserts.append({
            "catalog_entry_id": key, "title": row.title, "author": row.author, "media_format": row.format,
            "identity_id": identity_id, "provider_id": row.source_provider, "item_id": row.record_id, "local_state": state,
            "metadata": {
                **allowed_meta, "docbase": row.docbase, "scenario": row.scenario, "cover_url": cover_url, "product_url": product_url,
                "price": row.price, "currency": row.currency, "source_credential": row.source_credential,
                "source_collection": row.source_collection, "publisher": row.publisher or allowed_meta.get("publisher"),
                "published_at": row.published_at, "type": row.type, "medium": row.medium,
                "mirrored_at": now,
                # Descriptions are untrusted supplier text just like summaries:
                # never persist URLs, bearer tokens or personal identifiers.
                "description": (
                    _safe_public_search_text("description", row.description)
                    if type(row.description) is str else None
                ),
                # v3 is a read-only edition-sensitive mirror. Only the
                # actual Notice C index confers a durable document source;
                # neither revision nor mirrored availability grants rights.
                "catalogue_scope_version": (
                    3 if qualified and row.revision_id not in (None, "")
                    else 2 if qualified else None
                ),
                "source_revision": (
                    row.revision_id
                    if qualified and row.revision_id not in (None, "")
                    else None
                ),
                "source_account_id": (
                    row.source_account_id
                    if qualified and row.revision_id not in (None, "")
                    else None
                ),
            },
        })
    self.storage.catalog_entries_upsert_many(upserts)
    if not _CORE_PROGRESSIVE_C_WRITTEN.get():
        _ingest_public_search_c(
            self, rows, identity_id=identity_id, now=now,
            require_public_cover_witness=require_public_cover_witness,
        )
    return len(upserts)

def _clear_public_c_stamps(rows):
    # Revoke a former producer association before validating current rows.
    # A mutable supplier result may be reused by a later source batch.
    for row in rows:
        if type(row) is SearchResult:
            row.__dict__.pop("_core_notice_cover_document_ref", None)
            row.__dict__.pop("_core_notice_cover_document_epoch", None)
            row.__dict__.pop("_core_notice_canonical_cover_document_ref", None)
            row.__dict__.pop("_core_notice_canonical_cover_document_epoch", None)
            row.__dict__.pop("_core_persisted_c_revision_witness", None)
            row.__dict__.pop("_core_notice_cross_provider_proof", None)
            row.__dict__.pop("_core_cached_c_receipt", None)


def _ingest_public_search_c(self, rows, *, identity_id, now,
                            require_public_cover_witness=False):
    """Persist public observations before publishing their batch; no legacy mirror."""
    pairs = []
    # The same public-document writer serves sync and progressive batches.
    #
    # Production Storage owns the durable source index. Test/fake storage
    # backends without that index keep the existing mirror contract.
    from ..storage import Storage
    if isinstance(self.storage, Storage):
        import sqlite3
        from .notice_search_cover_producer import admit_public_search_catalogue
        from .notice_cover_repository import persist_notice_cover_many
        from .notice_c_cover_index import persist_exact_public_search_cover
        producing_rows = []
        observations = []
        for row in rows:
            if ((require_public_cover_witness or _CORE_LIVE_PUBLIC_C_WRITER.get())
                    and not _verified_public_cover_witness(row)):
                # Do not promote an arbitrary legacy/private SearchResult to C.
                continue
            public_url = _shared_catalogue_cover_url(row.thumbnail_url)
            # A genuine same-call anonymous PUBLIC notice without any
            # thumbnail still belongs in C. A rejected unsafe URL does not:
            # that is not a verified absent image and cannot be a C producer.
            if public_url is None and row.thumbnail_url is not None:
                continue
            try:
                observed = admit_public_search_catalogue(
                    row, identity_id=identity_id, observed_at=now,
                    public_cover_url=public_url,
                )
            except (ValueError, TypeError, UnicodeError):
                observed = None
            if observed is None:
                continue
            producing_rows.append(row)
            observations.append(observed)

        if observations:
            try:
                recover = getattr(self, "_notice_work_recover_search", None)
                committed = getattr(self, "_notice_work_search_committed", None)
                atomic_groups = callable(recover) and callable(committed)
                atomic_c = {}
                pending_positions = set()

                def publish_or_request(db, source, position):
                    # Discard in-memory hints from an uncommitted chunk
                    # attempt before recomputing this exact row.
                    atomic_c.pop(position, None)
                    pending_positions.discard(position)
                    # The opt-in runtime owns durable recovery. Publish C on
                    # the source transaction; on failure roll back only that
                    # publication, then record the repair before source commit.
                    if (source.observation.parser_version != "2"
                            or not _verified_public_cover_witness(producing_rows[position])):
                        return
                    from .notice_c_cover_index import _persist_exact_public_search_cover_txn
                    from .notice_c_work_requests import request_affected_public_recompute_txn
                    from .notice_c_initial_publication import request_initial_publication_txn
                    db.execute("SAVEPOINT notice_c_search_publication")
                    try:
                        canonical = _persist_exact_public_search_cover_txn(db, source)
                    except (ValueError, TypeError, OSError, sqlite3.Error):
                        db.execute("ROLLBACK TO SAVEPOINT notice_c_search_publication")
                        canonical = None
                    finally:
                        db.execute("RELEASE SAVEPOINT notice_c_search_publication")
                    if canonical is None:
                        affected = request_affected_public_recompute_txn(
                            db, source, now=now, _include_groups=not atomic_groups,
                        )
                        if affected.refused or affected.overflow:
                            raise ValueError("notice_work_search_repair_refused")
                        initial = request_initial_publication_txn(db, source, now=now)
                        if affected.refs or initial is not None:
                            pending_positions.add(position)
                    atomic_c[position] = canonical

                def publish_groups(db, accepted):
                    from .notice_c_group_batch_publication import publish_search_groups_txn
                    public = tuple((position, source) for position, source in accepted
                                   if position in atomic_c)
                    refused_refs = set()
                    _, requests = publish_search_groups_txn(
                        db, tuple(source for _, source in public), now=now,
                        _refused_refs=refused_refs,
                    )
                    if requests:
                        pending_positions.update(position for position, _ in public)
                    return tuple(position for position, source in public
                                 if source.document.source_key in refused_refs)

                capture_ids = set()

                def require_witness_evidence(db, observation, attribute, position):
                    from .notice_parse_witness import admitted_source_scope_sha256
                    from .notice_parse_witness_store import _PREFIX
                    from .notice_cover_repository import _checked_source_state, _opaque_key
                    from .notice_parse_witness_alert import review_publication_txn
                    scope = admitted_source_scope_sha256(
                        observation.key, parser_id=observation.parser_id,
                    )
                    if db.execute(
                        "SELECT 1 FROM metadata WHERE key=?", (_PREFIX + scope,),
                    ).fetchone() is None:
                        return True
                    evidence = getattr(producing_rows[position], "_core_notice_parse_input", None)
                    if (type(evidence) is not tuple or len(evidence) != 7
                            or evidence[:5] != (
                                _CORE_PUBLIC_COVER_WITNESS, observation.key,
                                observation.raw_json, observation.parser_id,
                                observation.parser_version)):
                        return False
                    _, current, _ = _checked_source_state(
                        db, attribute.document, _opaque_key(attribute.document),
                    )
                    if current is None or observation.observed_at < current.observation.observed_at:
                        return False
                    reject, diagnostic = review_publication_txn(
                        db, current=current.observation, candidate=observation,
                        input_digest=evidence[5], implementation_digest=evidence[6],
                    )
                    if reject and diagnostic is not None:
                        capture_ids.add(diagnostic)
                    return not reject

                options = {"_before_persist": require_witness_evidence}
                if callable(recover):
                    options["_after_persist"] = publish_or_request
                if atomic_groups:
                    options["_before_chunk_commit"] = publish_groups
                persisted_batch = persist_notice_cover_many(
                    self.storage, observations,
                    field_path=("search_result", "thumbnail_url"),
                    cover_inventory_complete=True,
                    **options,
                )
                # Export only durable intents, after SQLite commit. Raw bytes
                # are not export-authorized by a descriptive Search envelope.
                from .notice_regression_capture_intent import export_capture
                for diagnostic in sorted(capture_ids):
                    try:
                        state = export_capture(self.storage, diagnostic, members={})
                        if state is not None:
                            logging.getLogger("cifereca").warning(
                                "NOTICE parser_witness_rejected diagnostic_id=%s capture_state=%s",
                                diagnostic, state,
                            )
                    except (ValueError, OSError, sqlite3.Error):
                        pass
                for row in producing_rows:
                    row.__dict__.pop("_core_notice_parse_input", None)
                failed_c = []
                for position, (row, persisted) in enumerate(zip(producing_rows, persisted_batch)):
                    if persisted is None:
                        logging.getLogger("cifereca").warning(
                            "TRACE notice c public search code=source_persist_failed"
                        )
                        continue
                    # Core stamps only a successfully accepted source and
                    # its durable epoch. A GTK widget never writes a second
                    # N2 source or infers an association from an N3 HIT.
                    row._core_notice_cover_document_ref = persisted.document
                    row._core_notice_cover_document_epoch = persisted.epoch
                    # A public C source is exactly related to ITS OWN
                    # producer-attested edition. No title/ISBN/URL or cross-
                    # provider identity is inferred here. The canonical
                    # index retains its own N2 revision and source proofs.
                    if (persisted.observation.parser_version == "2"
                            and _verified_public_cover_witness(row)):
                        canonical = None
                        try:
                            if position in atomic_c:
                                canonical = atomic_c[position]
                                if canonical is None:
                                    raise ValueError("notice_work_search_repair_pending")
                            else:
                                canonical = persist_exact_public_search_cover(
                                    self.storage, persisted,
                                )
                        except (ValueError, TypeError, OSError, sqlite3.Error):
                            logging.getLogger("cifereca").warning(
                                "TRACE notice c public search code=canonical_source_failed"
                            )
                            if len(failed_c) < 32:
                                failed_c.append(persisted)
                        else:
                            row._core_notice_canonical_cover_document_ref = (
                                canonical.document
                            )
                            row._core_notice_canonical_cover_document_epoch = (
                                canonical.epoch
                            )
                            from .notice_c_live_revision import stamp_persisted_c_revision
                            stamp_persisted_c_revision(row, canonical)
                        pairs.append((row, persisted, canonical))
                _reconcile_progressive_public_c(
                    self.storage, pairs,
                    failed_sources=failed_c if callable(recover) and not atomic_groups else None,
                    read_only=atomic_groups,
                )
                if atomic_groups:
                    # A hint is only a wake, never a second producer.
                    # An interrupted producer leaves its durable request for
                    # the next heartbeat. No dispatch during stack unwinding.
                    if any(persisted_batch[position] is not None
                           for position in pending_positions):
                        committed()
                if failed_c and callable(recover) and not atomic_groups:
                    recover(tuple(failed_c), now=now)
            except (ValueError, TypeError, OSError, sqlite3.Error):
                # Catalogue mirror remains independent. A broken source
                # index cannot invent a cover or leak provider bytes/paths.
                logging.getLogger("cifereca").warning(
                    "TRACE notice c public search code=source_batch_failed"
                )
    return pairs


def _reconcile_progressive_public_c(storage, pairs, *, restore_own=False,
                                    failed_sources=None, read_only=False):
    """Restamp only currently verified persisted editions, never reobserve."""
    from .notice_c_origin_link_producer import (
        reconcile_origin_linked_public_batch, _CORE_ORIGIN_LINK_C_WITNESS,
    )
    from .notice_c_live_revision import stamp_persisted_c_revision

    current = [(row, source, own) for row, source, own in pairs
               if _verified_public_cover_witness(row)
               and getattr(row, "_core_notice_cover_document_ref", None) == source.document
               and getattr(row, "_core_notice_cover_document_epoch", None) == source.epoch]
    if restore_own:
        for row, source, own in current:
            row.__dict__.pop("_core_notice_cross_provider_proof", None)
            if own is None:
                row.__dict__.pop("_core_notice_canonical_cover_document_ref", None)
                row.__dict__.pop("_core_notice_canonical_cover_document_epoch", None)
                row.__dict__.pop("_core_persisted_c_revision_witness", None)
            else:
                row._core_notice_canonical_cover_document_ref = own.document
                row._core_notice_canonical_cover_document_epoch = own.epoch
                stamp_persisted_c_revision(row, own)
    # Only one writer; late reconciliation reuses accepted source snapshots.
    if read_only:
        from .notice_c_group_batch_publication import read_committed_search_groups
        linked = read_committed_search_groups(
            storage, tuple(source for _row, source, _own in current),
        )
    else:
        linked = reconcile_origin_linked_public_batch(
            storage, [source for _row, source, _own in current],
            **({"failed_sources": failed_sources} if failed_sources is not None else {}),
        ) if current else {}
    for row, source, _own in current:
        shared = linked.get(source.document.source_key)
        if shared is None or not any(
                member.source_ref == source.document.source_key
                and member.source_epoch == source.epoch
                for member in shared.sources):
            # The same opaque source may occur with several observation
            # epochs across progressive batches. Do not stamp a newer
            # group onto an earlier row by source key alone.
            continue
        row._core_notice_canonical_cover_document_ref = shared.document
        row._core_notice_canonical_cover_document_epoch = shared.epoch
        stamp_persisted_c_revision(row, shared)
        row._core_notice_cross_provider_proof = (
            _CORE_ORIGIN_LINK_C_WITNESS, source.document.source_key,
            shared.document.source_key, shared.epoch,
        )


