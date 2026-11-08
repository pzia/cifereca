"""Internal library/history/cover projections over facade-owned state.

Existing catalogue, covers and file helpers are reused. No additional scan
or acquisition is introduced; the facade controls identity and explicit calls."""
from __future__ import annotations

import time
import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from dataclasses import asdict
from pathlib import Path
from typing import Any
from ..catalog import LocalCatalog
from ..epub_metadata import cached_cover_url
from ..core.covers import candidate as cover_candidate, resolve_cover
from ..models import HistoryItem, Loan, SearchResult
from ..providers import ProviderConfigurationError
from ..plugin_api import BundleError, load_bundle_manifest
from .local_placement_open_guard import scoped_existing_placement_path


def refresh_history(self, force: bool=False) -> list[HistoryItem]:
    account = self.current_account
    epoch = self._account_context_epoch
    key = self._key("history-v3")
    ttl = int(self.config.get("cache.history_seconds", 1800))
    if not force:
        cached = self.storage.cache_get("account", key)
        if isinstance(cached, list):
            rows = [HistoryItem(**x) for x in cached]
            if self._account_context_epoch == epoch and self.current_account is account:
                self.last_history = rows
            self.diagnostics.add("CACHE", "history", detail={"hit": True, "count": len(rows)})
            return rows
    session = self._session_for_account(account)
    if not session.capabilities.history:
        if self._account_context_epoch == epoch and self.current_account is account:
            self.last_history = []
        return []
    rows = session.history(account)
    direct = sum(1 for x in rows if x.thumbnail_url)
    default_only = sum(1 for x in rows if not x.thumbnail_url and x.default_thumbnail_url)
    missing = sum(1 for x in rows if not x.thumbnail_url and not x.default_thumbnail_url)
    self.diagnostics.add(
        "API", "history thumbnails", "ok",
        detail={
            "provider_plugin": account.provider_id,
            "account": "primary" if account.is_primary else "linked",
            "count": len(rows), "direct_thumbnail": direct,
            "default_only": default_only, "missing": missing,
        },
        level="detailed",
    )
    self.storage.cache_put("account", key, [asdict(x) for x in rows], ttl)
    if self._account_context_epoch == epoch and self.current_account is account:
        self.last_history = rows
    return rows


def _history_fold(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^0-9a-z]+", " ", text).strip()


def _history_cover_score(cls, item_title: str, item_author: str | None, item_year: str | None, record_id: str | None, candidate: SearchResult) -> float:
    title = cls._history_fold(item_title)
    cand_title = cls._history_fold(candidate.title)
    if not title or not cand_title:
        return 0.0
    ratio = SequenceMatcher(None, title, cand_title).ratio()
    left = set(title.split())
    right = set(cand_title.split())
    token_overlap = len(left & right) / max(1, len(left | right))
    score = ratio * 55.0 + token_overlap * 25.0
    if title == cand_title:
        score += 30.0
    elif title in cand_title or cand_title in title:
        score += 12.0

    if record_id and str(candidate.record_id) == str(record_id):
        # Si Syracuse finit par renvoyer le même identifiant via Search,
        # c'est un signal plus fiable que le GetListHoldings défaillant.
        score += 60.0

    author = cls._history_fold(item_author)
    cand_author = cls._history_fold(candidate.author)
    if author and cand_author:
        author_ratio = SequenceMatcher(None, author, cand_author).ratio()
        aleft = set(author.split())
        aright = set(cand_author.split())
        author_tokens = len(aleft & aright) / max(1, len(aleft | aright))
        score += max(author_ratio, author_tokens) * 18.0
        if author == cand_author:
            score += 5.0

    year = re.sub(r"\D", "", str(item_year or ""))[:4]
    published = str(candidate.published_at or "")
    if year and year in published:
        score += 9.0
    return score


def _history_search_queries(cls, title: str) -> list[str]:
    """Construit au plus trois requêtes, du plus précis au plus tolérant.

    Syracuse peut indexer un titre différemment de celui renvoyé par
    ``ListHandings`` (sous-titre, ponctuation, mention de volume). On ne
    journalise jamais le texte de ces requêtes dans les diagnostics.
    """
    original = re.sub(r"\s+", " ", str(title or "")).strip()[:180]
    if not original:
        return []
    variants = [original]

    # Titre principal avant un séparateur de sous-titre fréquent.
    main = re.split(r"\s(?:[:;/=])\s|\s[-–—]\s", original, maxsplit=1)[0].strip()
    if len(main) >= 4:
        variants.append(main[:180])

    # Variante sans ponctuation ni diacritiques : utile quand ListHandings
    # et le catalogue n'emploient pas exactement la même typographie.
    folded = cls._history_fold(original)
    if folded:
        words = folded.split()
        variants.append(" ".join(words[:8])[:180])

    out: list[str] = []
    seen: set[str] = set()
    for value in variants:
        key = value.casefold().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out[:3]


def resolve_history_thumbnail(self, provider: str, record_id: str | None, title: str, author: str | None=None, year: str | None=None, default_url: str | None=None) -> tuple[str | None, str]:
    """Résout une couverture manquante de l'historique via le catalogue.

    0.2.6 corrige deux défauts de 0.2.5 : les résultats négatifs ne sont
    plus figés pendant sept jours, et le matching ne jette plus les
    notices qui n'exposent pas directement une image dans le résultat de
    recherche. Une notice fiable sans image est enrichie via ``holdings``
    *avec l'identifiant issu de Search.svc*, qui est exploitable.
    """
    provider = (provider or "SYRACUSE").upper()
    title = str(title or "").strip()
    if not title:
        return default_url, "not_found"

    folded = "\0".join([
        provider,
        str(record_id or ""),
        self._history_fold(title),
        self._history_fold(author),
        str(year or ""),
    ])
    key = hashlib.sha256(folded.encode("utf-8")).hexdigest()
    # Nouveau namespace : invalide automatiquement les not_found de 0.2.5.
    namespace = "history-cover-search-v2"
    cached = self.storage.cache_get(namespace, key)
    if isinstance(cached, dict) and cached.get("status"):
        status = str(cached.get("status"))
        url = str(cached.get("url") or "").strip() or None
        self.diagnostics.add(
            "COVER", "history search", "ok",
            detail={"provider": provider, "result": status, "cache": True},
            level="detailed",
        )
        return url or default_url, status

    # Cover enrichment is provider-agnostic: use the current provider
    # session contract rather than any concrete Syracuse client.
    session = self._session_for_account()
    all_rows: list[SearchResult] = []
    seen: set[str] = set()
    attempts = 0
    # Only the fact of a failed provider request must survive. Neither a
    # traceback nor exception object belongs in diagnostic or cache state.
    search_error = False
    search_queries = self._history_search_queries(title)
    folded_queries = {self._history_fold(q) for q in search_queries if q}

    for query in search_queries:
        attempts += 1
        try:
            rows = session.search(query, scope="all", page=0, field="all")
        except Exception:
            search_error = True
            self.diagnostics.add(
                "COVER", "history search attempt", "error",
                detail={
                    "attempt": attempts,
                    "code": "history_cover_search_failed",
                },
                level="detailed",
            )
            continue

        for row in rows:
            if row.id not in seen:
                seen.add(row.id)
                all_rows.append(row)
        self.diagnostics.add(
            "COVER", "history search attempt", "ok",
            detail={
                "provider": provider,
                "attempt": attempts,
                "rows": len(rows),
                "rows_total": len(all_rows),
                "with_thumbnail": sum(1 for x in rows if x.thumbnail_url),
            },
            level="detailed",
        )

        # Une égalité forte permet d'éviter les requêtes plus larges.
        preview = sorted(
            ((self._history_cover_score(title, author, year, record_id, x), x) for x in all_rows),
            key=lambda pair: pair[0], reverse=True,
        )
        if preview:
            pscore, pbest = preview[0]
            if str(record_id or "") and str(pbest.record_id) == str(record_id):
                break
            if self._history_fold(pbest.title) == self._history_fold(title) and pscore >= 85.0:
                break

    if not all_rows and search_error:
        self.storage.cache_put(namespace, key, {"url": "", "status": "error"}, 5 * 60)
        self.diagnostics.add(
            "COVER", "history search", "error",
            detail={
                "result": "error",
                "cache": False,
                "attempts": attempts,
                "code": "history_cover_search_failed",
            },
            level="detailed",
        )
        return default_url, "error"

    scored = sorted(
        ((self._history_cover_score(title, author, year, record_id, x), x) for x in all_rows),
        key=lambda pair: pair[0], reverse=True,
    )
    status = "not_found"
    url: str | None = None
    best_score = 0.0
    gap = 0.0
    cover_source = "none"
    best: SearchResult | None = None

    if scored:
        best_score, best = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        gap = best_score - second_score
        best_title = self._history_fold(best.title)
        history_title = self._history_fold(title)
        exact_title = bool(history_title and best_title == history_title)
        exact_variant = bool(best_title and best_title in folded_queries)
        same_record = bool(record_id and str(best.record_id) == str(record_id))

        confident = False
        if same_record:
            confident = True
        elif (exact_title or exact_variant) and (len(scored) == 1 or gap >= 5.0):
            confident = True
        elif best_score >= 92.0 and gap >= 10.0:
            confident = True
        elif best_score >= 72.0:
            status = "ambiguous"

        if confident:
            if best.thumbnail_url:
                url = best.thumbnail_url
                status = "matched"
                cover_source = "search"
            else:
                # Les résultats physiques peuvent être dépourvus d'image
                # dans CompactResult/CustomResult. L'identifiant Search.svc
                # est en revanche valide pour la fiche détaillée.
                try:
                    detail = session.holdings(best, search_terms="")
                    if detail.thumbnail_url:
                        url = detail.thumbnail_url
                        status = "matched"
                        cover_source = "detail"
                    else:
                        status = "no_cover"
                except Exception:
                    status = "no_cover"
                    self.diagnostics.add(
                        "COVER", "history detail cover", "error",
                        detail={"code": "history_cover_detail_failed"},
                        level="detailed",
                    )

    # Match positif durable ; les négatifs restent courts pour ne plus
    # verrouiller une mauvaise première résolution pendant plusieurs jours.
    ttl = {
        "matched": 30 * 24 * 3600,
        "no_cover": 6 * 3600,
        "ambiguous": 30 * 60,
        "not_found": 15 * 60,
        "error": 5 * 60,
    }.get(status, 15 * 60)
    self.storage.cache_put(namespace, key, {"url": url or "", "status": status}, ttl)
    self.diagnostics.add(
        "COVER", "history search", "ok",
        detail={
            "provider": provider,
            "result": status,
            "cache": False,
            "attempts": attempts,
            "candidates": len(all_rows),
            "with_thumbnail": sum(1 for x in all_rows if x.thumbnail_url),
            "score": round(best_score, 1),
            "gap": round(gap, 1),
            "cover_source": cover_source,
        },
        level="detailed",
    )
    return url or default_url, status


def _openable_scoped_placement(self, placement: dict[str, Any], *,
                               identity_id: str) -> bool:
    """A recorded 'present' is not proof that this profile can open the file.

    Reuse the already loaded Placement, not an N+1 SQLite placement_get.
    Only a real local file or a validated local bundle is shown as usable;
    missing mounts or foreign/symlink paths remain stored for reconciliation.
    """
    placement_id = placement.get("placement_id")
    if type(placement_id) is not str or not placement_id:
        return False
    try:
        path = scoped_existing_placement_path(
            self, placement, identity_id=identity_id,
        )
        if path.is_dir():
            # A directory is not a book unless the existing Core bundle
            # opener validates its playlist and physical scope.
            self.local_book_path(placement_id, identity_id=identity_id)
            return True
        return path.is_file()
    except (ProviderConfigurationError, OSError, ValueError, RuntimeError):
        return False


def recent_acquisitions(self, *, identity_id: str | None=None, limit: int=100) -> list[dict[str, Any]]:
    """Return recent local materializations, newest first.

    Covers come from the scoped catalogue, current loan inventory and
    canonical entitlement metadata; backup_items is historical only and
    must never be queried to decorate a current placement.
    """
    identity_id = identity_id or (self.current_identity.identity_id if self.current_identity else None)
    # A recent-acquisitions view belongs to one profile, never a union.
    # Unscoped v0.7 local placements belong only to the default identity,
    # exactly as in library_entries(); SQLite ownership remains unchanged.
    if not identity_id:
        return []
    default_identity_id = self.identities[0].identity_id if self.identities else None
    # A catalogue row is descriptive metadata, not a physical ownership
    # proof. Keep every possible row until its Placement owner and Artifact
    # are known; dict-by-placement would silently select a foreign row.
    entries_by_placement: dict[str, list[dict[str, Any]]] = {}
    for candidate in self.storage.catalog_entries():
        if candidate.get("placement_id"):
            entries_by_placement.setdefault(
                str(candidate["placement_id"]), [],
            ).append(candidate)
    # Remote covers can identify a borrower's private book. A provider/item
    # pair (even within one profile) is not evidence that its loan or purchase
    # belongs to this local Artifact. Only an exact Core provider OriginLink
    # joins remote cover metadata to a present placement.
    origin_scopes: dict[tuple[str, str], set[tuple[str, str, str, str, str]]] = {}
    for artifact, owner, provider, credential, account, edition, kind in (
        _verified_provider_origin_coordinates(
            self.storage, identity_id=identity_id, with_kind=True,
        )
    ):
        origin_scopes.setdefault((owner, artifact), set()).add(
            (provider, credential, account, edition, kind)
        )
    loans_by_scope: dict[tuple[str, str, str, str, str], Loan] = {}
    for loan in self.last_loans:
        try:
            loan_identity = loan.identity_id or self.identity_for_connection(
                loan.source_provider, loan.source_credential,
            ).identity_id
        except Exception:
            loan_identity = loan.identity_id
        # None is an unknown borrower, not the empty primary-account ID.
        if type(loan.source_account_id) is not str or not loan_identity:
            continue
        loans_by_scope[(
            loan_identity, loan.source_provider, loan.source_credential,
            loan.source_account_id, str(loan.record_id or loan.id),
        )] = loan
    entitlements_by_scope: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for entitlement in self.storage.entitlements():
        key = (
            entitlement.get("identity_id"), entitlement.get("provider_id"),
            entitlement.get("credential_id"), entitlement.get("account_id"),
            entitlement.get("item_id"),
        )
        if all(type(value) is str for value in key):
            entitlements_by_scope.setdefault(key, []).append(entitlement)
    rows: list[dict[str, Any]] = []
    for placement in sorted(
        self.storage.placements(),
        key=lambda x: int(x.get("created_at") or x.get("updated_at") or 0),
        reverse=True,
    ):
        if placement.get("state") != "present":
            continue
        placement_identity = placement.get("identity_id") or default_identity_id
        if placement_identity != identity_id:
            continue
        # #2847: a stale SQLite 'present' is not an accessible local book.
        # This view is read-only; unavailable mounts stay in reconciliation.
        if not _openable_scoped_placement(
            self, placement, identity_id=identity_id,
        ):
            continue
        # A migrated or corrupt catalogue may point a different profile's
        # private title/cover at a valid local Placement. A matching ID alone
        # does not give the catalogue row authority over that Placement.
        # Unscoped v0.7 descriptive rows are visible to the default profile
        # only, matching the historical unowned Placement rule above.
        eligible = [
            candidate for candidate in entries_by_placement.get(
                str(placement.get("placement_id")), (),
            )
            if (
                candidate.get("identity_id") == placement_identity
                or (
                    candidate.get("identity_id") in (None, "")
                    and placement_identity == default_identity_id
                )
            )
            and (
                not candidate.get("artifact_id")
                or candidate.get("artifact_id") == placement.get("artifact_id")
            )
        ]
        # More than one owner-matching candidate remains ambiguous: never
        # choose whichever metadata row happened to be loaded last.
        entry = eligible[0] if len(eligible) == 1 else {}
        metadata = dict(entry.get("metadata") or {})
        # #2797: recent["path"] is the scoped Placement, but the attached
        # catalogue metadata could otherwise disclose a foreign/stale path.
        # Do not invent a new metadata key for older local catalogues.
        if "path" in metadata:
            metadata["path"] = str(placement.get("path") or "")
        provider_id = str(placement.get("provider_id") or entry.get("provider_id") or "local")
        row_identity = placement_identity
        item_id = str(entry.get("item_id") or placement.get("source_item_id") or "")
        cover_candidates = []
        embedded = cover_candidate(
            cached_cover_url(metadata), tier="embedded", source="epub",
            evidence_id=str(placement.get("artifact_id") or ""), evidence_strength=100,
        )
        if embedded:
            cover_candidates.append(embedded)
        legacy = cover_candidate(
            metadata.get("cover_url") or metadata.get("thumbnail_url"),
            tier="origin", source="catalog-entry",
            evidence_id=f"{provider_id}:{item_id}" if item_id else provider_id, evidence_strength=60,
        )
        if legacy:
            cover_candidates.append(legacy)
        catalog_cover = metadata.get("catalog_cover")
        if isinstance(catalog_cover, dict):
            public = cover_candidate(
                catalog_cover.get("url"), tier="catalog", source=str(catalog_cover.get("source") or "catalog"),
                evidence_id=str(catalog_cover.get("evidence_id") or ""), evidence_strength=int(catalog_cover.get("evidence_strength") or 0),
            )
            if public:
                cover_candidates.append(public)
        for source_provider, credential, borrower, edition, source_kind in origin_scopes.get(
            (row_identity, str(placement.get("artifact_id") or "")), ()
        ):
            scope_key = (row_identity, source_provider, credential, borrower, edition)
            # A real provider loan cannot decorate a purchase-only OriginLink
            # (and vice versa), even within the same account and edition.
            loan = loans_by_scope.get(scope_key) if source_kind == "loan" else None
            if loan is not None:
                value = cover_candidate(
                    loan.thumbnail_url, tier="origin", source="loan",
                    evidence_id=f"{source_provider}:{edition}", evidence_strength=90,
                )
                if value:
                    cover_candidates.append(value)
            for entitlement in entitlements_by_scope.get(scope_key, ()):
                entitlement_kind = entitlement.get("entitlement_type")
                if not ((source_kind == "purchase" and entitlement_kind == "owned")
                        or (source_kind == "loan" and entitlement_kind == "loan")):
                    continue
                emeta = dict(entitlement.get("metadata") or {})
                value = cover_candidate(
                    emeta.get("cover_url") or emeta.get("thumbnail_url"),
                    tier="origin", source="entitlement",
                    evidence_id=str(entitlement.get("entitlement_id") or f"{source_provider}:{edition}"),
                    evidence_strength=80,
                )
                if value:
                    cover_candidates.append(value)
        cover = resolve_cover(cover_candidates)
        thumbnail_url = cover.url or None
        metadata["cover_resolution"] = cover.metadata()
        rows.append({
            "placement_id": placement.get("placement_id"), "artifact_id": placement.get("artifact_id"),
            "title": entry.get("title") or Path(str(placement.get("path") or "")).stem, "author": entry.get("author"),
            "identity_id": row_identity, "provider_id": provider_id, "item_id": item_id or None,
            "path": placement.get("path"), "media_format": entry.get("media_format"), "thumbnail_url": thumbnail_url,
            "metadata": metadata,
            "acquired_at": placement.get("created_at") or placement.get("updated_at"),
            "updated_at": placement.get("updated_at"),
        })
        if len(rows) >= max(1, limit):
            break
    return rows


def local_book_path(self, placement_id: str, *, identity_id: str) -> Path:
    """Resolve an inventoried file in the explicit identity scope, no acquisition."""
    if not identity_id or not self.identity_by_id(identity_id):
        raise ProviderConfigurationError('Identité locale inconnue')
    row=self.storage.placement_get(str(placement_id))
    owner=(row or {}).get('identity_id')
    if not owner and self.identities:owner=self.identities[0].identity_id
    if not row or owner!=identity_id or row.get('state')!='present':
        raise ProviderConfigurationError('Fichier local absent ou hors de cette identité')
    from .local_placement_open_guard import scoped_existing_placement_path
    try:
        path=scoped_existing_placement_path(self, row, identity_id=identity_id)
    except (OSError, ValueError, RuntimeError) as exc:
        # Fail closed on foreign/symlinked/inaccessible files. Never return
        # the private stored path or upstream exception to a launcher.
        raise ProviderConfigurationError('Fichier local absent ou hors de cette identité') from exc
    if path.is_dir():
        try:manifest=load_bundle_manifest(path,verify=True)
        except BundleError as exc:raise ProviderConfigurationError('Bundle local invalide') from exc
        if not manifest.playlist:raise ProviderConfigurationError('Ce bundle ne possède pas de fichier à ouvrir')
        playlist=Path(manifest.playlist)
        if playlist.is_absolute() or '..' in playlist.parts:
            raise ProviderConfigurationError('Playlist locale invalide')
        current=path
        for part in playlist.parts:
            current=current/part
            if current.is_symlink():
                raise ProviderConfigurationError('Playlist locale invalide')
        target=current.resolve()
        if path not in target.parents:raise ProviderConfigurationError('Playlist locale invalide')
        path=target
    if not path.is_file():raise ProviderConfigurationError('Le fichier local a disparu')
    return path


def local_placement_for_source(self, *, provider_id, credential_id, item_id, identity_id,
                               account_id=None):
    """Find a unique complete acquisition, optionally by exact borrower.

    Legacy callers retain the broad lookup. Current owned-right admission
    must supply account_id: another borrower's receipt is not proof of local
    possession of the selected right.
    """
    artifact_ids={str(row.get('artifact_id') or '') for row in self.storage.acquisitions()
                  if row.get('status')=='complete' and row.get('identity_id')==identity_id
                  and row.get('provider_id')==provider_id and row.get('credential_id')==credential_id
                  and str(row.get('item_id') or '')==str(item_id)
                  and (account_id is None or row.get('account_id')==account_id)}
    if provider_id == 'kobo':
        # The single-item reader is also used to admit an exact-account
        # targeted action. Once an edition is typed, the legacy ledger
        # cannot provide either a public alias or positive local proof.
        with self.storage._lock, self.storage._connect() as db:
            typed = db.execute(
                "SELECT identity_id,account_id FROM source_references "
                "WHERE provider_id='kobo' AND credential_id=? "
                "AND edition_id=? LIMIT 1001",
                (credential_id, str(item_id)),
            ).fetchall()
        if typed:
            if len(typed) > 1000:
                return None
            matching = [
                row for row in typed
                if row[0] == identity_id and (
                    account_id is None or row[1] == account_id
                )
            ]
            if len(matching) != 1 or (account_id is None and len(typed) != 1):
                return None
            exact_account = matching[0][1]
            origins = {
                artifact for artifact, owner, provider, credential,
                account, edition, _kind in (
                    _verified_provider_origin_coordinates(
                        self.storage, identity_id=identity_id,
                        with_kind=True,
                    )
                )
                if (owner, provider, credential, account, edition) == (
                    identity_id, 'kobo', credential_id,
                    exact_account, str(item_id),
                )
            }
            if len(origins) != 1:
                return None
            artifact_ids = origins
    rows=[row for row in self.storage.placements() if row.get('state')=='present'
          and row.get('identity_id')==identity_id and row.get('artifact_id') in artifact_ids]
    if len(rows)!=1:return None
    try:self.local_book_path(rows[0]['placement_id'],identity_id=identity_id)
    except ProviderConfigurationError:return None
    if account_id is not None:
        # Only a physically attested copy may suppress a fresh, explicitly
        # admitted owned-right GET. A complete historical receipt and an
        # existing pathname alone are not proof: bytes may have been replaced,
        # the path symlinked or the artifact digest corrupted. Do this stricter
        # check solely for the exact-account internal admission; preserve the
        # historical four-coordinate LibraryService reader contract.
        from .convergence_verified_presence import _verified_copy
        try:
            artifact_id = rows[0].get('artifact_id')
            # Bounded, one-snapshot proof of the registered owner and Artifact.
            # Storage.identity_get() would enumerate every unrelated profile,
            # and the historical UNIQUE index may be absent in legacy files.
            with self.storage._lock, self.storage._connect() as db:
                db.execute('BEGIN')
                evidence = db.execute(
                    'SELECT a.sha256,a.size,i.directory_name FROM artifacts a '
                    'JOIN identities i ON i.identity_id=? '
                    'WHERE a.artifact_id=?',
                    (identity_id, artifact_id),
                ).fetchone()
                if evidence is None:
                    return None
                digest, size, directory = evidence
                if (type(directory) is not str or not directory or db.execute(
                    'SELECT 1 FROM identities WHERE directory_name=? '
                    'AND identity_id<>? LIMIT 1',
                    (directory, identity_id),
                ).fetchone() is not None):
                    return None
            if (type(artifact_id) is not str or type(digest) is not str
                    or len(digest) != 64 or artifact_id != 'sha256:' + digest
                    or type(size) is not int or size <= 0
                    or not _verified_copy(
                        rows[0]['path'], root=self.library_dir(),
                        owner_directory=directory,
                        registered_directories=frozenset((directory,)),
                        size=size, sha256=digest,
                    )):
                return None
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return None
    return rows[0]


def local_placements_for_sources(self, identity_id):
    """Read-only search aliases; typed Kobo sources require provider provenance.

    The old 0.7 acquisition ledger has no canonical right/borrower binding.
    It remains a compatibility read only for editions with no typed source.
    """
    # A typed Kobo edition is identified by credential + edition, even if
    # the SourceReference belongs to another borrower or identity. The
    # catalogue's account-free search key cannot choose between sources.
    with self.storage._lock, self.storage._connect() as db:
        typed_rows = db.execute(
            "SELECT identity_id,credential_id,account_id,edition_id "
            "FROM source_references WHERE provider_id='kobo' "
            "ORDER BY source_reference_id LIMIT 100001"
        ).fetchall()
    if len(typed_rows) > 100000:
        # A truncated typed-source set must never reactivate an old alias.
        return {}
    typed_scopes = {}
    for owner, credential, account, edition in typed_rows:
        key = ("kobo", credential, edition)
        # Keep all rows, including multiple rights of one borrower. A
        # three-coordinate public search key cannot pick a unique origin.
        typed_scopes.setdefault(key, []).append((owner, account))

    # Provider OriginLinks, not legacy acquisition rows, authorize the
    # physical association of an already-typed Kobo search key. The helper
    # checks Core origin/SourceReference evidence, artifact identity and
    # current owner; local_book_path below independently checks physical B.
    canonical_artifacts = {}
    # Keep the legacy-only hot path free of an additional global origin join.
    # The typed-source query above is bounded and cannot hide new B editions.
    current_origins = (
        _verified_provider_origin_coordinates(
            self.storage, identity_id=identity_id, with_kind=True,
        ) if typed_scopes else ()
    )
    for artifact, owner, provider, credential, account, edition, _kind in current_origins:
        key = (provider, credential, edition)
        if provider == "kobo" and key in typed_scopes:
            canonical_artifacts.setdefault(key, set()).add(
                (owner, account, artifact),
            )

    artifacts = {}
    borrower_scopes = {}
    for row in self.storage.acquisitions(status='complete'):
        if row.get('identity_id') != identity_id or not row.get('artifact_id'): continue
        key = (row.get('provider_id'), row.get('credential_id'), str(row.get('item_id') or ''))
        if key in typed_scopes:
            # A legacy receipt cannot supply a missing or contradictory B link.
            continue
        artifacts.setdefault(key, set()).add(row['artifact_id'])
        # Search's old three-coordinate key has no borrower/account field.
        # Two exact completed receipts for different accounts are NOT a
        # unique binding for an arbitrary public result, even if both
        # receipts happen to reference the same Artifact and one Placement.
        account = row.get('account_id')
        borrower_scopes.setdefault(key, set()).add(
            (type(account).__name__, account) if type(account) in (str, type(None))
            else ("invalid", None)
        )
    placements = {}
    for row in self.storage.placements():
        if row.get('identity_id') == identity_id and row.get('state') == 'present':
            placements.setdefault(row.get('artifact_id'), []).append(row)
    linked = {}
    for key, ids in artifacts.items():
        accounts = borrower_scopes.get(key, ())
        if len(accounts) != 1 or ("invalid", None) in accounts: continue
        rows = [row for artifact_id in ids for row in placements.get(artifact_id, [])]
        if len(rows) != 1: continue
        try: self.local_book_path(rows[0]['placement_id'], identity_id=identity_id)
        except ProviderConfigurationError: continue
        linked[key] = rows[0]
    for key, scopes in typed_scopes.items():
        # The source identity and borrower must be uniquely known; a
        # second typed source (even for another profile) is not an alias.
        if len(scopes) != 1 or next(iter(scopes))[0] != identity_id:
            continue
        owner, account = next(iter(scopes))
        matched = {
            artifact for origin_owner, origin_account, artifact
            in canonical_artifacts.get(key, ())
            if (origin_owner, origin_account) == (owner, account)
        }
        if len(matched) != 1:
            continue
        rows = [
            row for artifact in matched
            for row in placements.get(artifact, ())
        ]
        if len(rows) != 1:
            continue
        try:
            self.local_book_path(rows[0]['placement_id'], identity_id=identity_id)
        except ProviderConfigurationError:
            continue
        linked[key] = rows[0]
    return linked


def catalog_entries(self, *, state: str | None=None) -> list[dict[str, Any]]:
    return LocalCatalog(self.storage, self.library_dir()).entries(state=state)


def _verified_provider_origin_coordinates(storage, *, identity_id: str | None,
                                          with_kind: bool = False):
    """Read canonical provider provenance scoped to a *present* placement.

    A shared Artifact SHA is not proof that a different profile or provider
    owns a resource. Only a completed provider OriginLink with a nonempty
    Core delivery receipt and a provider-response SourceReference can add a
    source facet; this projection performs no file or rights mutation.
    """
    from sqlite3 import Error as SqliteError

    if not hasattr(storage, "_lock") or not callable(getattr(storage, "_connect", None)):
        return ()
    try:
        with storage._lock, storage._connect() as db:
            return _read_provider_origin_coordinates(
                db, identity_id=identity_id, with_kind=with_kind,
            ) or ()
    except (SqliteError, AttributeError):
        # Earlier DB schemas cannot attest canonical provenance.
        return ()


def _read_provider_origin_coordinates(db, *, identity_id: str | None,
                                      with_kind: bool = False):
    rows = db.execute(
        "SELECT DISTINCT p.artifact_id,p.identity_id,s.provider_id,"
        "s.credential_id,s.account_id,s.edition_id,s.reference_kind "
        "FROM placements p "
        # A historical OriginLink remains for audit even if imported
        # Artifact metadata are corrupt. It must not become a live
        # supplier facet or fold a remote possession onto B when the
        # Artifact's content identifier/size cannot attest B.
        "JOIN artifacts a ON a.artifact_id=p.artifact_id "
        "AND a.artifact_id='sha256:'||a.sha256 AND a.size>0 "
        "AND length(a.sha256)=64 "
        "AND a.sha256 NOT GLOB '*[^0-9a-f]*' "
        "JOIN origin_links o ON o.artifact_id=p.artifact_id "
        "AND o.identity_id=p.identity_id "
        "JOIN source_references s "
        "ON s.source_reference_id=o.source_reference_id "
        "AND s.identity_id=o.identity_id "
        "WHERE p.state='present' AND o.origin_kind='provider' "
        "AND s.evidence_kind='provider_response' "
        "AND o.evidence_ref IS NOT NULL AND o.evidence_ref<>'' "
        "AND s.evidence_ref IS NOT NULL AND s.evidence_ref<>'' "
        + ("AND p.identity_id=? " if identity_id else "")
        + "ORDER BY p.identity_id,p.artifact_id,s.provider_id,"
        "s.credential_id,s.account_id,s.edition_id,s.reference_kind LIMIT 100001",
        (identity_id,) if identity_id else (),
    ).fetchall()
    if len(rows) > 100_000:
        # A truncated join could silently turn an ambiguous multi-source
        # Artifact into a falsely unique (and thus attachable) right. Fail
        # closed for every facet instead of projecting a partial source set.
        return None
    return tuple(
        (artifact, owner, provider, credential, account, edition, kind)
        if with_kind else (artifact, owner, provider, credential, account, edition)
        for artifact, owner, provider, credential, account, edition, kind in rows
        if all(type(field) is str and field for field in (
            artifact, owner, provider, credential, edition,
        )) and type(account) is str and kind in ("purchase", "loan")
    )


def _verified_legacy_receipt_aliases(storage, *, identity_id: str | None):
    """Read-only transitional bridge: exact scoped receipt + local bytes.

    A descriptive provider/item catalogue row cannot license a possession.
    A previously completed acquisition can match its *own* entitlement only
    when exact identity, credential, borrower, item, artifact, catalogue and
    placement agree, and the bytes remain SHA-verified. This is a display
    bridge, not an OriginLink/permission writer or download authority.
    """
    from sqlite3 import Error as SqliteError

    try:
        with storage._lock, storage._connect() as db:
            rows = db.execute(
                "SELECT a.identity_id,a.provider_id,a.credential_id,"
                "a.account_id,a.item_id,a.entitlement_id,a.artifact_id,"
                "p.placement_id,p.path,a.metadata_json "
                "FROM acquisition_ledger a "
                "JOIN placements p ON p.artifact_id=a.artifact_id "
                "AND p.identity_id=a.identity_id AND p.state='present' "
                "JOIN catalog_entries c ON c.placement_id=p.placement_id "
                "AND c.artifact_id=a.artifact_id AND c.identity_id=a.identity_id "
                "AND c.provider_id=a.provider_id AND c.item_id=a.item_id "
                "AND c.local_state='local' "
                "WHERE a.status='complete' AND a.entitlement_id IS NOT NULL "
                + ("AND a.identity_id=? " if identity_id else "")
                + "LIMIT 1025",
                (identity_id,) if identity_id else (),
            ).fetchall()
    except (SqliteError, AttributeError):
        return ()
    if len(rows) > 1024:
        return ()  # No unbounded legacy projection or incomplete proof.
    proven = []
    checks = {}
    for owner, provider, credential, account, item, entitlement, artifact, placement, path, raw in rows:
        if (not all(type(x) is str and x for x in (
                owner, provider, credential, item, entitlement,
                artifact, placement, path))
                or type(account) is not str
                or not artifact.startswith("sha256:") or len(artifact) != 71):
            continue
        try:
            metadata = json.loads(raw)
            if (type(metadata) is not dict or metadata.get("path") != path
                    or metadata.get("sha256") != artifact[7:]):
                continue
            if path not in checks:
                target = Path(path)
                checks[path] = (
                    target.is_absolute() and not target.is_symlink()
                    and target.is_file() and target.resolve(strict=True) == target
                    and LocalCatalog.sha256(target) == artifact[7:]
                )
            if not checks[path]:
                continue
        except (ValueError, OSError, TypeError, RuntimeError):
            continue
        proven.append((
            (owner, provider, credential, account, item, entitlement),
            artifact, placement,
        ))
    return tuple(proven)


def _unlinked_remote_entitlement_key(entitlement: dict[str, Any],
                                     identity_id: str) -> str:
    """Stable opaque display key for ONE exact, still-unlinked source right.

    Two borrowers can own the same provider item_id with different metadata
    and rights. A shared `source:provider:item` key would merge them, leak
    another account's details and overwrite a loan/entitlement state. A
    canonical Artifact+OriginLink may still intentionally group same-byte
    holdings; this key is only for remote-only fallback cards.
    """
    fields = (
        identity_id, entitlement.get("provider_id"),
        entitlement.get("credential_id"), entitlement.get("account_id"),
        entitlement.get("item_id"), entitlement.get("entitlement_type"),
        entitlement.get("loan_id"), entitlement.get("entitlement_id"),
    )
    payload = json.dumps(fields, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return "source:scoped:" + hashlib.sha256(
        b"cifereca:remote-entitlement:v1\x00" + payload
    ).hexdigest()


def library_entries(self, *, identity_id: str | None=None, scan: bool=False, logger) -> list[dict[str, Any]]:
    """Canonical Library projection.

    Local rows are grouped by content identity (SHA-256/Artifact) rather than
    by provider/item. Remote-only rights remain distinct until reconciliation
    can bind them to a local Artifact.
    """
    _lib_started = time.monotonic()
    identity_id = identity_id or (self.current_identity.identity_id if self.current_identity else None)
    logger.info("TRACE library-entries begin identity=%s filesystem_scan=%s", identity_id, "requested" if scan else "skipped")
    # There is no authorized union-of-profiles Library view. Without an
    # explicit effective identity, the content-hash grouping below would
    # collapse two owners' same-SHA placements and remote entitlements into
    # one card, disclosing paths, account metadata and private possessions.
    # Fail closed before scanning or reading any cross-profile SQLite rows.
    if not identity_id:
        return []
    if scan:
        self.catalog_scan()
    merged: dict[str, dict[str, Any]] = {}
    local_keys = {}
    local_artifact_keys: dict[tuple[str, str], str] = {}
    default_identity_id = self.identities[0].identity_id if self.identities else None
    artifacts = {str(row.get("artifact_id") or ""): row for row in self.storage.artifacts()}
    placements_by_artifact: dict[str, list[dict[str, Any]]] = {}
    for placement in self.storage.placements(state="present"):
        # The artifact itself is shared by content hash, but placements
        # belong to identities. Do not project another identity's paths,
        # storage targets or placement counts into this library snapshot.
        # Legacy unscoped placements belong only to the default identity.
        placement_identity = placement.get("identity_id") or default_identity_id
        if identity_id and placement_identity != identity_id:
            continue
        # #2797/#2847: even a second Placement for an otherwise usable
        # Artifact can be a stale path or a corrupt cross-profile binding.
        # Do not expose its filename/storage target as a local library facet.
        if not _openable_scoped_placement(
            self, placement, identity_id=placement_identity,
        ):
            continue
        placements_by_artifact.setdefault(str(placement.get("artifact_id") or ""), []).append(placement)

    def placement_view(row: dict[str, Any]) -> dict[str, Any]:
        meta = dict(row.get("metadata") or {})
        return {
            "placement_id": str(row.get("placement_id") or ""),
            "identity_id": row.get("identity_id") or default_identity_id,
            "provider_id": str(row.get("provider_id") or "local"),
            "source_item_id": str(row.get("source_item_id") or ""),
            "kind": str(meta.get("placement_kind") or ("storage" if meta.get("storage_target_id") else "library")),
            "storage_target_id": meta.get("storage_target_id"),
            "storage_plugin_id": meta.get("storage_plugin_id"),
            "filename": Path(str(row.get("path") or "")).name,
            "state": str(row.get("state") or "present"),
        }

    def add_unique(values: list[Any], value: Any) -> None:
        if value not in {None, ""} and value not in values:
            values.append(value)

    for row in self.storage.catalog_entries():
        if row.get("local_state") != "local":
            continue
        row_identity = row.get("identity_id")
        if identity_id and row_identity != identity_id:
            if row_identity not in {None, ""} or identity_id != default_identity_id:
                continue
        provider_id = str(row.get("provider_id") or "local")
        artifact_id = str(row.get("artifact_id") or "")
        # Catalogue local_state='local' is a descriptive projection, not
        # physical proof. An explicitly bound Placement (not only a v0.7
        # backup placeholder) must still be present in this identity, match
        # this Artifact, and point to an openable file. A stale scan or
        # renamed/deleted physical file must not appear as a usable book.
        #
        # Unbound, non-legacy local declarations remain on their established
        # compatibility path; we cannot silently invent a Placement for them.
        metadata = dict(row.get("metadata") or {})
        placement_id = str(row.get("placement_id") or "")
        scoped_local_path = None
        if placement_id or metadata.get("imported_from") == "backup_items":
            if not placement_id or not any(
                str(p.get("placement_id") or "") == placement_id
                for p in placements_by_artifact.get(artifact_id, ())
            ):
                continue
            owner = row_identity or default_identity_id
            if not owner:
                continue
            try:
                scoped_local_path = self.local_book_path(
                    placement_id, identity_id=owner,
                )
            except ProviderConfigurationError:
                continue
            # The catalogue is descriptive, never a path ownership proof.
            # A stale/foreign metadata.path must not override a physically
            # checked Placement, nor leak into the returned metadata. Keep
            # this optional descriptive key absent when originally absent.
            if "path" in metadata:
                metadata["path"] = str(scoped_local_path)
        item_id = str(row.get("item_id") or artifact_id or row.get("catalog_entry_id"))
        artifact = artifacts.get(artifact_id, {})
        sha256 = str(artifact.get("sha256") or "")
        if not sha256 and artifact_id.startswith("sha256:"):
            sha256 = artifact_id.split(":", 1)[1]
        canonical = f"sha256:{sha256}" if sha256 else (artifact_id or f"source:{provider_id}:{item_id}")
        key = f"artifact:{canonical}" if not canonical.startswith("source:") else canonical
        local_keys[(row.get('placement_id'), artifact_id)] = key
        local_artifact_keys[(row_identity or default_identity_id, artifact_id)] = key
        # Keep the scoped metadata.path normalized above; re-reading the raw
        # catalogue here would reintroduce an arbitrary foreign path.
        all_placements = [placement_view(p) for p in placements_by_artifact.get(artifact_id, ())]
        current_placement = next((p for p in placements_by_artifact.get(artifact_id, ()) if str(p.get("placement_id") or "") == str(row.get("placement_id") or "")), None)
        existing = merged.get(key)
        source_ref = {"provider_id": provider_id, "item_id": item_id, "identity_id": row_identity or default_identity_id}
        base_cover_candidates = []
        embedded_cover = cover_candidate(
            cached_cover_url(metadata), tier="embedded", source="epub",
            evidence_id=artifact_id or sha256, evidence_strength=100,
        )
        if embedded_cover:
            base_cover_candidates.append(embedded_cover)
        legacy_cover = cover_candidate(
            metadata.get("cover_url") or metadata.get("thumbnail_url"),
            tier="origin", source="catalog-entry",
            evidence_id=f"{provider_id}:{item_id}" if item_id else provider_id, evidence_strength=60,
        )
        if legacy_cover:
            base_cover_candidates.append(legacy_cover)
        catalog_cover = metadata.get("catalog_cover")
        if isinstance(catalog_cover, dict):
            public_cover = cover_candidate(
                catalog_cover.get("url"), tier="catalog", source=str(catalog_cover.get("source") or "catalog"),
                evidence_id=str(catalog_cover.get("evidence_id") or ""), evidence_strength=int(catalog_cover.get("evidence_strength") or 0),
            )
            if public_cover:
                base_cover_candidates.append(public_cover)
        base_cover = resolve_cover(base_cover_candidates)
        metadata["cover_resolution"] = base_cover.metadata()
        if existing is None:
            existing = {
                "key": key, "book_id": item_id, "title": str(row.get("title") or "Sans titre"),
                "author": row.get("author"), "provider_id": provider_id, "providers": [provider_id],
                "identity_id": row_identity or default_identity_id, "identity_ids": [row_identity or default_identity_id] if (row_identity or default_identity_id) else [],
                "item_id": item_id, "source_refs": [source_ref], "state": "local", "local": True,
                "local_path": (
                    str(scoped_local_path) if scoped_local_path is not None
                    else metadata.get("path") or (current_placement or {}).get("path")
                ),
                "media_format": artifact.get("media_format") or row.get("media_format"),
                "artifact_kind": metadata.get("artifact_kind") or artifact.get("kind") or "file",
                "bundle_members": metadata.get("bundle_members") or [], "playlist": metadata.get("playlist"),
                "entitlement_type": None, "due_at": None, "source": "catalog",
                "thumbnail_url": base_cover.url or None,
                "artifact_id": artifact_id or None, "sha256": sha256 or None,
                "placement_id": row.get("placement_id"), "placements": all_placements, "possessions": [],
                "metadata": metadata,
            }
            merged[key] = existing
        else:
            add_unique(existing.setdefault("providers", []), provider_id)
            add_unique(existing.setdefault("identity_ids", []), row_identity or default_identity_id)
            if source_ref not in existing.setdefault("source_refs", []): existing["source_refs"].append(source_ref)
            known_ids={p.get("placement_id") for p in existing.setdefault("placements", [])}
            existing["placements"].extend(p for p in all_placements if p.get("placement_id") not in known_ids)
            if not existing.get("author") and row.get("author"): existing["author"] = row.get("author")
            if existing.get("title") in {None, "", "Sans titre"} and row.get("title"): existing["title"] = row.get("title")
            if not existing.get("thumbnail_url") and base_cover.url:
                existing["thumbnail_url"] = base_cover.url
            existing["metadata"] = {**metadata, **dict(existing.get("metadata") or {})}

    # Canonical OriginLinks enrich the *existing* local Artifact card.
    # Never use a title, mere SHA equality, or an unscoped provider/item ID to
    # attribute a purchase to a credential. Unresolved sources stay distinct.
    exact_aliases: dict[tuple[str, str, str, str, str, str], set[str]] = {}
    for artifact_id, owner, provider, credential, account, edition, kind in (
        _verified_provider_origin_coordinates(
            self.storage, identity_id=identity_id, with_kind=True,
        )
    ):
        local_key = local_artifact_keys.get((owner, artifact_id))
        if local_key is None:
            # A published Placement and exact OriginLink may outlive a missing
            # local catalogue index. Project the verified physical file, but
            # never invent a catalogue entry, entitlement or borrower scope.
            owner_placements = [
                p for p in placements_by_artifact.get(artifact_id, ())
                if p.get("identity_id") == owner
            ]
            verified = []
            for placement in owner_placements:
                placement_id = str(placement.get("placement_id") or "")
                try:
                    path = self.local_book_path(placement_id, identity_id=owner)
                except (ProviderConfigurationError, OSError, ValueError):
                    continue
                verified.append((placement, path))
            if not verified:
                continue
            verified.sort(key=lambda pair: str(pair[0].get("placement_id") or ""))
            first, path = verified[0]
            artifact = artifacts.get(artifact_id, {})
            sha256 = str(artifact.get("sha256") or "")
            if not sha256 and artifact_id.startswith("sha256:"):
                sha256 = artifact_id.split(":", 1)[1]
            canonical = "sha256:" + sha256 if sha256 else artifact_id
            local_key = "artifact:" + canonical
            if local_key in merged and merged[local_key].get("identity_id") != owner:
                # This API normally projects a single identity. Do not expose
                # another profile's placement on an unscoped legacy request.
                continue
            if local_key not in merged:
                merged[local_key] = {
                    "key": local_key, "book_id": artifact_id,
                    "title": path.stem or "Sans titre", "author": None,
                    "provider_id": "local", "providers": ["local"],
                    "identity_id": owner, "identity_ids": [owner],
                    "item_id": None, "source_refs": [],
                    "state": "local", "local": True, "local_path": str(path),
                    "media_format": artifact.get("media_format"),
                    "artifact_kind": artifact.get("kind") or "file",
                    "bundle_members": [], "playlist": None,
                    "entitlement_type": None, "due_at": None,
                    "source": "placement", "thumbnail_url": None,
                    "artifact_id": artifact_id, "sha256": sha256 or None,
                    "placement_id": first.get("placement_id"),
                    "placements": [placement_view(p) for p in owner_placements],
                    "possessions": [], "metadata": {},
                }
            local_artifact_keys[(owner, artifact_id)] = local_key
            for placement in owner_placements:
                local_keys[(placement.get("placement_id"), artifact_id)] = local_key
        entry = merged[local_key]
        add_unique(entry.setdefault("providers", []), provider)
        source_ref = {"provider_id": provider, "item_id": edition, "identity_id": owner}
        if source_ref not in entry.setdefault("source_refs", []):
            entry["source_refs"].append(source_ref)
        exact_aliases.setdefault(
            (owner, provider, credential, account, edition, kind), set(),
        ).add(local_key)

    # Multiple Artifacts for the same exact source cannot be disambiguated by
    # metadata alone. Only a *unique* canonical link can fold the remote card.
    exact_source_alias = {
        scope: next(iter(keys))
        for scope, keys in exact_aliases.items() if len(keys) == 1
    }
    # The old ACSM/Core 0.8 receipt path predates OriginLink v14. Retain
    # a narrow verified legacy display bridge until receipt migration, never
    # the former global (provider_id,item_id) catalogue alias.
    legacy_exact_aliases: dict[tuple[str, ...], set[str]] = {}
    for scope, artifact, placement in _verified_legacy_receipt_aliases(
        self.storage, identity_id=identity_id,
    ):
        local_key = local_keys.get((placement, artifact))
        if local_key is not None:
            legacy_exact_aliases.setdefault(scope, set()).add(local_key)

    # A v0.7 backup projection is not scoped by identity/borrower: even a
    # descriptive title/cover can leak a former owner's private metadata.
    # Present only catalogue, canonical entitlement and scoped loan facts.
    from .bnf_import_provenance import display_links
    bnf_links = display_links(self.storage,
                             [p for rows in placements_by_artifact.values() for p in rows], local_keys)
    loans_by_key: dict[tuple[str, str, str | None, str], Loan] = {}
    for loan in self.last_loans:
        loans_by_key[(loan.source_provider, loan.source_credential, loan.source_account_id, str(loan.record_id or loan.id))] = loan

    for entitlement in self.storage.entitlements():
        status = str(entitlement.get("state") or "")
        if status != "active" and not (
            status == "archived" and entitlement.get("provider_id") == "kobo"
            and entitlement.get("entitlement_type") == "owned"
        ):
            continue
        entitlement_type = str(entitlement.get("entitlement_type") or "unknown")
        if entitlement_type not in {"owned", "loan", "license"}: continue
        entitlement_identity = str(entitlement.get("identity_id") or "").strip() or default_identity_id
        if identity_id and entitlement_identity != identity_id: continue
        provider_id = str(entitlement.get("provider_id") or ""); item_id = str(entitlement.get("item_id") or "")
        if not provider_id or not item_id: continue
        exact_scope = (
            entitlement_identity, provider_id,
            str(entitlement.get("credential_id") or ""),
            str(entitlement.get("account_id") or ""), item_id,
        )
        # Match an exact provider SourceReference kind, not only borrower
        # and edition. Licences without a typed purchase/loan attestation stay
        # remote until an explicit mapping is available.
        canonical_kind = (
            "purchase" if entitlement_type == "owned"
            else "loan" if entitlement_type == "loan" else None
        )
        typed_scope = (*exact_scope, canonical_kind) if canonical_kind else None
        canonical_key = exact_source_alias.get(typed_scope)
        if canonical_key is None:
            # Display-only compatibility for a completed, exact-account
            # receipt with SHA-verified local bytes; no unverified catalogue
            # provider/item label can attach another borrower's possession.
            receipt_scope = (*exact_scope, str(entitlement.get("entitlement_id") or ""))
            proved = legacy_exact_aliases.get(receipt_scope, ())
            canonical_key = (
                next(iter(proved)) if len(proved) == 1
                else _unlinked_remote_entitlement_key(
                    entitlement, entitlement_identity,
                )
            )
        if (provider_id == 'bnf-pret' and entitlement_type == 'loan'
                and exact_source_alias.get(typed_scope) is None):
            # A legacy manual-import display hint must never override an
            # exact canonical provider OriginLink for this borrower/edition.
            # Different physical Artifacts remain separate; only the loan
            # possession follows its strongest actual provenance proof.
            canonical_key = bnf_links.get((entitlement_identity, entitlement.get('credential_id'),
                                           entitlement.get('account_id'), entitlement.get('loan_id'), item_id), canonical_key)
        existing = merged.get(canonical_key, {})
        meta = dict(entitlement.get("metadata") or {})
        # An active loan may share the same edition/account as an owned right;
        # its title, cover and format must not decorate a purchase possession.
        loan = (loans_by_key.get((
            provider_id, str(entitlement.get("credential_id") or ""),
            entitlement.get("account_id"), item_id,
        )) if entitlement_type == "loan" else None)
        loan_extra = dict((loan.raw or {}).get("_cifereca") or {}) if loan is not None and isinstance((loan.raw or {}).get("_cifereca"), dict) else {}
        title = str(meta.get("title") or (loan.title if loan else "") or existing.get("title") or item_id)
        author = meta.get("author") or (loan.author if loan else None) or loan_extra.get("author") or existing.get("author")
        from ..core.search import file_format
        source_media_format = meta.get("media_format") or (loan.media_format if loan else None) or loan_extra.get("format")
        local_format = file_format(existing.get("media_format")) if existing.get("local") else ""
        media_format = local_format or file_format(source_media_format) or file_format(existing.get("media_format"))
        meta["source_media_format"] = source_media_format
        cover_candidates = []
        existing_meta = dict(existing.get("metadata") or {})
        embedded_cover = cover_candidate(
            cached_cover_url(existing_meta), tier="embedded", source="epub",
            evidence_id=str(existing.get("artifact_id") or existing.get("sha256") or ""), evidence_strength=100,
        )
        if embedded_cover:
            cover_candidates.append(embedded_cover)
        for url, source, strength, evidence in (
            (existing_meta.get("cover_url") or existing_meta.get("thumbnail_url"), "catalog-entry", 60, f"{provider_id}:{item_id}"),
            (meta.get("cover_url") or meta.get("thumbnail_url"), "entitlement", 80, str(entitlement.get("entitlement_id") or f"{provider_id}:{item_id}")),
            ((loan.thumbnail_url if loan else None), "loan", 90, f"{provider_id}:{item_id}"),
        ):
            value = cover_candidate(url, tier="origin", source=source, evidence_id=evidence, evidence_strength=strength)
            if value:
                cover_candidates.append(value)
        for metadata_source in (existing_meta, meta):
            public_meta = metadata_source.get("catalog_cover")
            if isinstance(public_meta, dict):
                value = cover_candidate(
                    public_meta.get("url"), tier="catalog", source=str(public_meta.get("source") or "catalog"),
                    evidence_id=str(public_meta.get("evidence_id") or ""), evidence_strength=int(public_meta.get("evidence_strength") or 0),
                )
                if value:
                    cover_candidates.append(value)
        cover = resolve_cover(cover_candidates)
        thumbnail = cover.url or None
        combined_meta = {**existing_meta, **meta}
        combined_meta["cover_resolution"] = cover.metadata()
        from .loan_cover_fallback import attested_local_loan_cover_proof
        proof = (
            attested_local_loan_cover_proof(
                loan, entitlement, existing,
                identity_id=entitlement_identity,
                provider_id=provider_id, item_id=item_id,
            )
            if entitlement_type == "loan" else None
        )
        if proof is not None:
            proofs = existing.setdefault("_exact_loan_cover_proofs", [])
            if proof not in proofs:
                proofs.append(proof)
        remote_state = ("remote_archived" if status == "archived" else
                        "remote_owned" if entitlement_type == "owned" else
                        "remote_loan_active" if entitlement_type == "loan" else "remote_entitled")
        # If one Kobo account still owns the edition, another archived account
        # must not downgrade the shared visible row from active to archived.
        if existing.get("state") == "remote_owned" and remote_state == "remote_archived":
            remote_state = "remote_owned"
        possession={"identity_id": entitlement_identity, "provider_id": provider_id, "item_id": item_id, "kind": entitlement_type, "account_id": entitlement.get("account_id"), "entitlement_id": entitlement.get("entitlement_id"), "expires_at": entitlement.get("expires_at"), "state": entitlement.get("state") or "active"}
        if existing:
            add_unique(existing.setdefault("providers", []), provider_id)
            add_unique(existing.setdefault("identity_ids", []), entitlement_identity)
            if possession not in existing.setdefault("possessions", []): existing["possessions"].append(possession)
            source_ref={"provider_id":provider_id,"item_id":item_id,"identity_id":entitlement_identity}
            if source_ref not in existing.setdefault("source_refs", []): existing["source_refs"].append(source_ref)
            existing.update({"title":title,"author":author,"state":"local" if existing.get("local") else remote_state,"media_format":str(media_format or "").lower() or None,"entitlement_type":entitlement_type,"entitlement_id":entitlement.get("entitlement_id"),"due_at":entitlement.get("expires_at"),"loan_id":entitlement.get("loan_id"),"thumbnail_url":thumbnail,"metadata":combined_meta})
        else:
            merged[canonical_key]={"key":canonical_key,"book_id":item_id,"title":title,"author":author,"provider_id":provider_id,"providers":[provider_id],"identity_id":entitlement_identity,"identity_ids":[entitlement_identity] if entitlement_identity else [],"item_id":item_id,"source_refs":[{"provider_id":provider_id,"item_id":item_id,"identity_id":entitlement_identity}],"state":remote_state,"local":False,"local_path":None,"media_format":str(media_format or "").lower() or None,"artifact_kind":None,"bundle_members":[],"playlist":None,"entitlement_type":entitlement_type,"entitlement_id":entitlement.get("entitlement_id"),"due_at":entitlement.get("expires_at"),"source":"entitlement","loan_id":entitlement.get("loan_id"),"thumbnail_url":thumbnail,"artifact_id":None,"sha256":None,"placement_id":None,"placements":[],"possessions":[possession],"metadata":combined_meta}

    result=[]
    for row in merged.values():
        row["providers"] = sorted(dict.fromkeys(str(x) for x in row.get("providers", []) if x))
        row["identity_ids"] = list(dict.fromkeys(x for x in row.get("identity_ids", []) if x))
        row["placement_count"] = len(row.get("placements") or [])
        row["source_count"] = len(row.get("source_refs") or [])
        result.append(row)
    result.sort(key=lambda row: (str(row.get("title") or "").casefold(), str(row.get("provider_id") or "")))
    logger.info("TRACE library-entries end identity=%s count=%s dt=%.3fs", identity_id, len(result), time.monotonic()-_lib_started)
    return result
