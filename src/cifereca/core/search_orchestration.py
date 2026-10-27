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
import re
from concurrent.futures import FIRST_COMPLETED, wait
from dataclasses import asdict, replace
from datetime import datetime, timezone
from queue import Queue, Empty, Full
from typing import Any, Callable
from ..models import ItemDetail, SearchResult
from ..providers import ProviderSearchCapabilities, ProviderSession
from ..plugin_api import ProviderSearchEvent, ProviderSearchStream
from ..core.provider_stream import consume_provider_stream, SearchStreamFailure, SearchStreamCancelled


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


def _search_preference_score(self, row: SearchResult) -> int:
    identity = self.current_identity
    if identity is None:
        return 0
    metadata = row.metadata if isinstance(row.metadata, dict) else {}
    languages: list[str] = []
    for key in ("language", "languages", "lang", "dc_language", "dc:language"):
        if key in metadata:
            for code in self._normalized_language_values(metadata.get(key)):
                if code not in languages:
                    languages.append(code)
    prefs = identity.preferences
    score = 0
    for index, preferred in enumerate(prefs.reading_languages):
        code = str(preferred or "").strip().lower().split("-", 1)[0]
        if code and code in languages:
            score += max(1, len(prefs.reading_languages) - index) * 10
            break
    if prefs.prefer_original_language and languages:
        originals: list[str] = []
        for key in ("original_language", "originalLanguage", "language_original"):
            if key in metadata:
                originals.extend(self._normalized_language_values(metadata.get(key)))
        if originals and any(code in originals for code in languages):
            score += 5
    return score


def availability_display_label(self, item: SearchResult, detail: ItemDetail) -> str:
    """Translate provider availability into a user-actionable label."""
    if item.source_provider == "kobo":
        # Raw search result owned=True can originate in a public catalogue.
        # Only Core's exact identity-scoped manifest ledger proves purchase.
        identity_id = getattr(getattr(self, "current_identity", None), "identity_id", None)
        rights = {}
        if type(identity_id) is str and identity_id:
            try:
                reader = getattr(self, "kobo_personal_right_states", None)
                candidate = reader(identity_id) if callable(reader) else {}
                if type(candidate) is dict:
                    rights = candidate
            except Exception:
                pass  # Unknown cannot be promoted to a purchase.
        right_state = rights.get((item.source_credential, item.record_id))
        if right_state == "active":
            return ("Mes achats Kobo · sauvegarde possible"
                    if self.can_direct_download(item)
                    else "Mes achats Kobo · fichier local non présumé")
        if right_state == "archived":
            return "Achat Kobo archivé · sauvegarde non disponible"
        if item.price:
            return f"Catalogue Kobo · aucun achat attesté · {item.price} {item.currency or ''}".strip()
        return "Catalogue Kobo · aucun achat attesté"
    if self.can_direct_download(item):
        return "Téléchargement direct"
    label = str(detail.availability_label or detail.availability_status or "—").strip()
    status = self._history_fold(detail.availability_status)
    folded_label = self._history_fold(label)
    available = status in {"available", "disponible"} or (
        folded_label.startswith("disponible") and not folded_label.startswith("indisponible")
    )
    if item.source_provider == "paris" and item.medium == "digital" and available:
        return "Disponible à l’emprunt"
    return label or "—"


def searchable_providers(self) -> list[tuple[str, str]]:
    """Connected provider ids/display names usable by the global search UI."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for session in self._sessions.values():
        if not session.connected or not session.capabilities.search or session.provider_id in seen:
            continue
        if not self._provider_enabled_for_identity(session.provider_id):
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
        if not self._provider_enabled_for_identity(session.provider_id):
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
) -> None:
    """Remember a native HTTP 429 once; never sleep/retry in a worker."""
    from ..search_failures import search_retry_after_seconds
    delay = search_retry_after_seconds(error)
    if delay is None:
        return
    with self._search_generation_lock:
        # A completed request from an old identity/account/session must not
        # throttle a newly selected source or otherwise mutate its state.
        if (self.current_identity is not identity
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

    def sources_current() -> bool:
        if not source_leases:
            return True
        try:
            registered = tuple(self._sessions.values())
        except RuntimeError:
            return False
        for session, expected in source_leases:
            if (expected is None or expected[2] is not True
                    or not any(candidate is session for candidate in registered)
                    or _inert_search_source_state(session) != expected):
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
    # Search is global: one connected session per provider is queried,
    # independently of the reader account currently selected in the GUI.
    searchable: list[ProviderSession] = []
    seen_provider: set[str] = set()
    requested_provider = str(provider_id or "all").strip().lower()
    for session in self._sessions.values():
        if not session.connected or not session.capabilities.search:
            continue
        if not self._provider_enabled_for_identity(session.provider_id):
            continue
        if requested_provider != "all" and session.provider_id != requested_provider:
            continue
        if session.provider_id in seen_provider:
            continue
        seen_provider.add(session.provider_id)
        searchable.append(session)
    searchable = self._without_redundant_merged(
        searchable, field=field, scope=provider_scope,
        include_redundant_merged=include_redundant_merged,
    )
    source_leases = tuple(
        (session, _inert_search_source_state(session))
        for session in searchable
    )
    provider_key = ",".join(sorted(x.provider_id for x in searchable)) or requested_provider
    pref_key = ""
    if identity is not None:
        pref_key = f"{identity.identity_id}:{','.join(identity.preferences.reading_languages)}:{int(identity.preferences.prefer_original_language)}"
    virtual_versions = ",".join(
        f"{entry.plugin_id}:{entry.source_revision}:{entry.catalog_generation}"
        for session in searchable
        if (entry := self.registry.virtual_entry(session.provider_id)) is not None
    )
    virtual_cache_tag = f"\0{virtual_versions}" if virtual_versions else ""
    cache_key = hashlib.sha256(
        f"search-v8\0{_search_session_cache_scope(searchable)!r}\0{provider_key}\0{scope}\0{field}\0{tuple(sorted(content_types or ()))!r}\0{pref_key}\0{query.casefold()}{virtual_cache_tag}".encode()
    ).hexdigest()
    ttl = int(self.config.get("cache.search_seconds", 21600))
    if not force:
        cached = self.storage.cache_get("search", cache_key)
        if isinstance(cached, list):
            rows = [_provider_search_row(SearchResult(**x)) for x in cached]
            with self._search_generation_lock:
                if (self._search_generation != search_generation
                        or self.current_identity is not identity
                        or not sources_current()):
                    return []
                self.last_search = rows
                # Never leak a warning from a previous, unrelated search.
                self.last_search_warnings = []
            self.diagnostics.add("CACHE", "search", detail={"hit": True, "results": len(rows), "q_len": len(query), "providers": provider_key})
            return rows

    rows: list[SearchResult] = []
    warnings: list[str] = []
    variants = self._search_query_variants(query)
    for session in searchable:
        if not request_current():
            return []
        provider_rows: list[SearchResult] = []
        provider_seen: set[tuple[str, str, str]] = set()
        effective_field, contract_warning = _effective_search_field(
            session, field, provider_scope, facade_type=facade_type,
        )
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
                try:
                    import inspect
                    parameters = inspect.signature(session.search).parameters
                    kwargs = {"field": effective_field}
                    if "content_types" in parameters:
                        kwargs["content_types"] = tuple(content_types or ())
                    session_rows = session.search(variant, provider_scope, **kwargs)
                except TypeError as exc:
                    # 0.4 keeps source compatibility with small third-party
                    # provider prototypes that still implement the 0.3 signature.
                    if "field" not in str(exc) or "unexpected keyword" not in str(exc):
                        raise
                    session_rows = session.search(variant, provider_scope)
                for raw_row in session_rows:
                    row = _provider_search_row(raw_row)
                    key = (row.source_provider, row.docbase, row.record_id)
                    if key not in provider_seen:
                        provider_seen.add(key)
                        provider_rows.append(row)
                concrete = getattr(session, "client", None)
                partial_failure = False
                for source in (session, concrete):
                    for _warning in list(getattr(source, "last_search_warnings", []) or []):
                        # A provider warning is untrusted: it may carry signed
                        # URLs, credentials, account IDs or a private title.
                        # Mirror the progressive path: export only a constant.
                        warnings.append(f"{session.descriptor.display_name}: résultats partiels")
                        partial_failure = True
                if partial_failure:
                    break
                if attempt == 0 and provider_rows:
                    primary_ok = max(self._search_relevance(query, row) for row in provider_rows) >= 100
            except Exception as exc:
                from ..search_failures import search_failure_code
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
    unique: dict[tuple[str, str, str], SearchResult] = {}
    for row in rows:
        unique[(row.source_provider, row.docbase, row.record_id)] = row
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
            self._mirror_remote_results([
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
        self.storage.cache_put("search", cache_key, [asdict(x) for x in rows], ttl)
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


def _search_provider_session(self, session: ProviderSession, query: str, scope: str, *, field: str='all', content_types: tuple[str, ...]=(), facade_type) -> tuple[list[SearchResult], list[str], str, str]:
    """Search one provider without mutating shared result state.

    This is deliberately safe to run in parallel with *other* provider sessions;
    a given session is still called sequentially for its own query variants.
    """
    provider_scope = "all" if str(scope or "all").casefold() == "unknown" else scope
    provider_rows: list[SearchResult] = []
    provider_seen: set[tuple[str, str, str]] = set()
    warnings: list[str] = []
    effective_field, contract_warning = _effective_search_field(
        session, field, provider_scope, facade_type=facade_type,
    )
    if contract_warning:
        warnings.append(contract_warning)
    if effective_field is None:
        return [], warnings, "", ""
    identity = getattr(self, "current_identity", None)
    credential = getattr(session, "credential_id", None)
    if _source_in_retry_after(self, session, identity):
        code = "SearchTemporarilyUnavailable"
        return [], [f"{session.descriptor.display_name}: {_search_problem_label(code)}"], code, _search_problem_label(code)
    variants = self._search_query_variants(query)
    primary_ok = False
    terminal_error_code = terminal_error_message = ""
    for attempt, variant in enumerate(variants):
        if attempt > 0 and primary_ok:
            break
        try:
            try:
                import inspect
                parameters = inspect.signature(session.search).parameters
                kwargs = {"field": effective_field}
                if "content_types" in parameters:
                    kwargs["content_types"] = tuple(content_types or ())
                session_rows = session.search(variant, provider_scope, **kwargs)
            except TypeError as exc:
                if "field" not in str(exc) or "unexpected keyword" not in str(exc):
                    raise
                session_rows = session.search(variant, provider_scope)
            for raw_row in session_rows:
                row = _provider_search_row(raw_row)
                key = (row.source_provider, row.docbase, row.record_id)
                if key not in provider_seen:
                    provider_seen.add(key); provider_rows.append(row)
            concrete = getattr(session, "client", None)
            partial_failure = False
            for source in (session, concrete):
                for warning in list(getattr(source, "last_search_warnings", []) or []):
                    # Provider-owned strings may contain signed URLs,
                    # cookies or a private title; the public batch only
                    # exposes a fixed partial-result reason.
                    warnings.append(f"{session.descriptor.display_name}: résultats partiels")
                    partial_failure = True
            if partial_failure:
                break
            if attempt == 0 and provider_rows:
                primary_ok = max(self._search_relevance(query, row) for row in provider_rows) >= 100
        except Exception as exc:
            from ..search_failures import search_failure_code
            # Provider exceptions can include signed URLs, account IDs and
            # personal search terms. Neither the result batch nor diagnostics
            # may carry their raw message.
            failure_code = search_failure_code(exc) or "ProviderSearchFailed"
            if failure_code == "SearchTemporarilyUnavailable":
                _record_source_retry_after(self, session, identity, credential, exc)
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


def _search_virtual_stream_session(self, session: ProviderSession, query: str, scope: str, *, field: str, content_types: tuple[str, ...], generation: int, publish: Callable[[ProviderSearchEvent], None], cancelled: Callable[[], bool], control: Any) -> tuple[list[SearchResult], list[str], str, str]:
    """Worker-only optional stream. Gaps/errors do not poison the cache."""
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
        publish(ProviderSearchEvent(
            session.provider_id, generation, 0, "reset",
            message="resync after invalid/incomplete stream",
        ))
        # Rebuild from a complete catalogue query; do not reuse partial
        # stream results after a dropped message.
        return self._search_provider_session(
            session, query, scope, field=field, content_types=content_types,
        )
    except Exception:
        # Stream faults must never echo plugin class names or descriptor labels.
        # Preserve a typed terminal failure for the caller without its payload.
        return [], ["Recherche en flux indisponible"], "StreamProviderFailed", "Recherche en flux indisponible"


def search_progressive(self, query: str, scope: str='all', force: bool=False, *, provider_id: str='all', provider_ids: tuple[str, ...] | list[str] | None=None, field: str='all', content_types: tuple[str, ...] | list[str] | None=None, on_batch: Callable[[Any], None] | None=None, control: Any=None, on_event: Callable[[ProviderSearchEvent], None] | None=None, include_redundant_merged: bool=False, executor, logger):
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
    with self._search_generation_lock:
        self._search_generation += 1
        search_generation = self._search_generation
        identity = self.current_identity
    requested = {str(x).strip().lower() for x in (provider_ids or ()) if str(x).strip() and str(x).strip().lower() != "local"}
    single = str(provider_id or "all").strip().lower()
    if single != "all": requested.add(single)
    searchable: list[ProviderSession] = []
    seen_provider: set[str] = set()
    for session in self._sessions.values():
        if not session.connected or not session.capabilities.search:
            continue
        if not self._provider_enabled_for_identity(session.provider_id):
            continue
        if requested and session.provider_id not in requested:
            continue
        if session.provider_id in seen_provider:
            continue
        seen_provider.add(session.provider_id); searchable.append(session)
    searchable = self._without_redundant_merged(
        searchable, field=field, scope=scope,
        include_redundant_merged=include_redundant_merged,
    )
    searchable.sort(key=lambda row: row.provider_id)
    source_leases = tuple(
        (session, _inert_search_source_state(session))
        for session in searchable
    )
    provider_key = ",".join(sorted(x.provider_id for x in searchable)) or (",".join(sorted(requested)) or single)
    pref_key = "" if identity is None else f"{identity.identity_id}:{','.join(identity.preferences.reading_languages)}:{int(identity.preferences.prefer_original_language)}"
    types = tuple(sorted(content_types or ()))
    virtual_versions = ",".join(
        f"{entry.plugin_id}:{entry.source_revision}:{entry.catalog_generation}"
        for session in searchable
        if (entry := self.registry.virtual_entry(session.provider_id)) is not None
    )
    cache_key = hashlib.sha256(f"search-v9-progressive\0{_search_session_cache_scope(searchable)!r}\0{provider_key}\0{scope}\0{field}\0{types!r}\0{pref_key}\0{query.casefold()}\0{virtual_versions}".encode()).hexdigest()
    ttl = int(self.config.get("cache.search_seconds", 21600))

    def request_current_locked() -> bool:
        if (search_generation != self._search_generation
                or self.current_identity is not identity):
            return False
        # A provider can be reconnected without a profile transition. Its
        # old in-flight worker is then no longer authorized to publish a
        # batch, a stream event, a mirror or an unscoped cache result.
        try:
            registered = tuple(self._sessions.values())
        except RuntimeError:
            return False
        for session, expected in source_leases:
            if (expected is None or expected[2] is not True
                    or not any(item is session for item in registered)
                    or _inert_search_source_state(session) != expected):
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
        if isinstance(cached, list):
            rows = [_provider_search_row(SearchResult(**x)) for x in cached]
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
                # An empty 200 also earns a cached verdict; session ownership
                # remains observable for the same selected provider set.
                delivered = 0
                for session in searchable:
                    if request_cancelled():
                        break
                    emit(CatalogSearchBatch(
                        session.provider_id, session.descriptor.display_name,
                        tuple(groups.get(session.provider_id, ())),
                        cached=True, result_status=_search_batch_status(
                            groups.get(session.provider_id, ()), (), "", cached=True
                        ),
                    ))
                    delivered += 1
                cancelled = request_cancelled()
                if not cancelled:
                    with self._search_generation_lock:
                        if request_current_locked():
                            self.last_search = rows; self.last_search_warnings = []
                        else:
                            cancelled = True
                completed = datetime.now(timezone.utc).isoformat(timespec="seconds")
                return CatalogSearchSummary(query, len(rows), len(searchable), delivered, 0, cancelled, (), started_dt.isoformat(timespec="seconds"), completed)

    aggregate: list[SearchResult] = []
    warnings: list[str] = []
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
            if (
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
            else:
                future = executor.submit(
                    self._search_provider_session, session, query, scope,
                    field=field, content_types=tuple(content_types or ()),
                )
            futures[future] = (session, time.monotonic())
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
                session, provider_started = futures[future]
                try:
                    provider_rows, provider_warnings, error_code, error_message = future.result()
                except Exception as exc:
                    from ..search_failures import search_failure_code
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
                aggregate.extend(provider_rows)
                warnings.extend(provider_warnings)
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

    unique: dict[tuple[str, str, str], SearchResult] = {}
    for row in aggregate: unique[(row.source_provider, row.docbase, row.record_id)] = row
    rows = list(unique.values())
    variants = self._search_query_variants(query)
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
        try: self._mirror_remote_results(live_results, identity_id=identity.identity_id)
        except Exception: logger.warning("TRACE catalog mirror progressive search code=mirror_failed")
    cancelled = cancelled or request_cancelled()
    if not cancelled and not warnings and failed == 0:
        self.storage.cache_put("search", cache_key, [asdict(x) for x in rows], ttl)
    if not cancelled:
        with self._search_generation_lock:
            if request_current_locked():
                self.last_search = rows; self.last_search_warnings = warnings
            else:
                cancelled = True
    self.diagnostics.add("SEARCH", "progressive federated", detail={"providers": provider_key, "field": field, "results": len(rows), "completed": completed_count, "failed": failed, "cancelled": cancelled, "dt_ms": int((time.monotonic()-started_mono)*1000)}, level="detailed")
    completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return CatalogSearchSummary(query, len(rows), len(searchable), completed_count, failed, cancelled, tuple(warnings), started_dt.isoformat(timespec="seconds"), completed_at)


def search_local_library(self, query: str, scope: str='all', *, field: str='all', identity_id: str | None=None, age: int | None=None, age_kind: str | None=None, logger) -> list[dict[str, Any]]:
    from ..core.search import LocalLibrarySearch

    resolved_identity = identity_id or (self.current_identity.identity_id if self.current_identity else None)
    rows = LocalLibrarySearch(self.library_entries).search(
        query, identity_id=resolved_identity, scope=scope, field=field,
        age=age, age_kind=age_kind,
    )
    logger.info(
        "TRACE search local identity=%s query_len=%s scope=%s field=%s results=%s",
        resolved_identity, len(str(query or "")), scope, field, len(rows),
    )
    return rows


def search_known_catalog(self, query: str, scope: str='all', *, field: str='all', identity_id: str | None=None, provider_ids: list[str] | tuple[str, ...] | None=None, content_types: list[str] | tuple[str, ...] | None=None, age: int | None=None, age_kind: str | None=None, logger) -> list[dict[str, Any]]:
    """Search the already-known SQLite universe without provider I/O.

    This is the first stage of the Search screen: local artifacts, current
    rights and progressively mirrored catalogue facts. ``search()`` remains
    the explicit online extension operation.
    """
    from ..core.search import KnownCatalogSearch

    resolved_identity = identity_id or (self.current_identity.identity_id if self.current_identity else None)
    rows = KnownCatalogSearch(self.catalog_entries, self.library_entries).search(
        query, identity_id=resolved_identity, scope=scope, field=field,
        providers=provider_ids, content_types=content_types,
        age=age, age_kind=age_kind,
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


def _numilog_public_catalog_url(value: Any, *, cover: bool = False) -> str | None:
    """Never persist a store delivery, signed query, or private account URL.

    This is defense in depth after the provider's own public-book validation:
    no remote request is made, and unknown notices/covers are omitted rather
    than converting private account pages into persisted public links.
    """
    from urllib.parse import urlsplit
    if type(value) is not str or not 0 < len(value) <= 512:
        return None
    if value != value.strip() or any(c.isspace() or ord(c) < 32 for c in value):
        return None
    if "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.fragment or parsed.port not in (None, 443)
                or any(x in {".", ".."} for x in parsed.path.split("/"))):
            return None
        if cover:
            if parsed.hostname not in {
                "www.numilog.com", "services.numilog.com",
                "www.edenlivres.fr", "storage.googleapis.com",
                "couverture.numilog.com",
            } or parsed.query:
                return None
        else:
            if parsed.hostname != "www.numilog.com":
                return None
            official = re.fullmatch(
                r"/[0-9]{1,12}/[A-Za-z0-9][A-Za-z0-9._-]{0,150}\\.ebook",
                parsed.path, flags=re.I,
            )
            legacy = re.fullmatch(
                r"/(?:livres|ebooks)/[A-Za-z0-9][A-Za-z0-9._-]{0,100}",
                parsed.path, flags=re.I,
            )
            if not (official or legacy) or parsed.query not in ("", "ref=library"):
                return None
        return value
    except (ValueError, AttributeError):
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


def _mirrored_catalogue_key(identity_id: str, row: SearchResult) -> tuple[str, bool]:
    """Distinguish identical supplier IDs across accounts and editions.

    Legacy incomplete coordinates keep their historic key but cannot gain a
    version-2 attestation or an actionable SearchResult from the cache.
    """
    parts = (
        identity_id, row.source_provider, row.record_id, row.source_credential,
        row.docbase, row.source_collection or "",
    )
    qualified = (
        all(type(value) is str and 0 < len(value) <= 512
            and "\\x00" not in value for value in parts[:5])
        and type(parts[5]) is str and len(parts[5]) <= 512
        and "\\x00" not in parts[5]
        and (row.source_collection is None or type(row.source_collection) is str)
    )
    if not qualified:
        legacy = f"mirror\\0{identity_id}\\0{row.source_provider}\\0{row.record_id}"
        return hashlib.sha256(legacy.encode()).hexdigest(), False
    scope = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(("mirror-v2\\0" + scope).encode()).hexdigest(), True


def _mirror_remote_results(self, rows: list[SearchResult], *, identity_id: str | None=None) -> int:
    """Persist progressively discovered remote catalog metadata (0.8.3)."""
    identity_id = identity_id or (self.current_identity.identity_id if self.current_identity else None)
    if not identity_id:
        return 0
    now = int(time.time())
    # Neither Kobo nor Numilog public search flags are purchase evidence.
    # A positive right from an authenticated personal library must already
    # exist for this identity, credential and exact edition/variant (#1905).
    verified_owned: set[tuple[str, str, str, str]] = set()
    for provider_id in ("kobo", "numilog"):
        if not any(row.source_provider == provider_id and row.owned is True for row in rows):
            continue
        verified_owned.update({
            (provider_id, str(right.get("identity_id") or ""),
             str(right.get("credential_id") or ""),
             str(right.get("item_id") or ""))
            for right in self.storage.entitlements(provider_id=provider_id, state="active")
            if right.get("entitlement_type") == "owned"
        })
    keyed: list[tuple[str, SearchResult, str, bool]] = []
    for row in rows:
        owned = row.owned is True and (
            row.source_provider not in {"kobo", "numilog"} or
            (row.source_provider, identity_id, row.source_credential, row.record_id)
            in verified_owned
        )
        state = (
            "remote_owned" if owned else
            "available_to_buy" if row.source_provider in {"kobo", "numilog"} and row.price else
            "available_to_borrow" if row.source_provider == "paris" and row.medium == "digital" else
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
        if row.source_provider == "numilog":
            # Signed ACSM "rights" must never be copied into the public
            # catalogue. Ownership is the separate attested entitlement.
            raw_meta.pop("rights", None)
        allowed_meta = {
            name: raw_meta.get(name)
            for name in ("subjects", "subject", "series", "collection", "publisher", "languages", "isbn", "isbns", "original_language", "authors", "contributors", "identifiers", "edition", "edition_statement", "series_index", "collection_index", "genre", "genres", "categories", "audience", "target_age", "rights", "dates")
            if raw_meta.get(name) not in (None, "", [], {})
        }
        # Kobo-Vault and other providers may already expose attested awards,
        # subjects and summaries. Preserve *only* sanitized bibliographic
        # fields of the same exact source item, not its private extras.
        for name in _PUBLIC_SEARCH_TEXT_FIELDS:
            safe = _safe_public_search_text(name, raw_meta.get(name))
            if safe is not None:
                allowed_meta[name] = safe
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
        cover_url = (
            _numilog_public_catalog_url(row.thumbnail_url, cover=True)
            if row.source_provider == "numilog" else row.thumbnail_url
        )
        product_url = (
            _numilog_public_catalog_url(row.friendly_url)
            if row.source_provider == "numilog" else row.friendly_url
        )
        upserts.append({
            "catalog_entry_id": key, "title": row.title, "author": row.author, "media_format": row.format,
            "identity_id": identity_id, "provider_id": row.source_provider, "item_id": row.record_id, "local_state": state,
            "metadata": {
                **allowed_meta, "docbase": row.docbase, "scenario": row.scenario, "cover_url": cover_url, "product_url": product_url,
                "price": row.price, "currency": row.currency, "source_credential": row.source_credential,
                "source_collection": row.source_collection, "publisher": row.publisher or allowed_meta.get("publisher"),
                "published_at": row.published_at, "type": row.type, "medium": row.medium,
                "mirrored_at": now, "description": row.description,
                "catalogue_scope_version": 2 if qualified else None,
            },
        })
    self.storage.catalog_entries_upsert_many(upserts)
    return len(upserts)
