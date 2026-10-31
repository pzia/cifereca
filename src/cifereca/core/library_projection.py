"""Internal library/history/cover projections over facade-owned state.

Existing catalogue, covers and file helpers are reused. No additional scan
or acquisition is introduced; the facade controls identity and explicit calls."""
from __future__ import annotations

import time
import hashlib
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


def recent_acquisitions(self, *, identity_id: str | None=None, limit: int=100) -> list[dict[str, Any]]:
    """Return recent local materializations, newest first.

    Covers come from the scoped catalogue, current loan inventory and
    canonical entitlement metadata; backup_items is historical only and
    must never be queried to decorate a current placement.
    """
    identity_id = identity_id or (self.current_identity.identity_id if self.current_identity else None)
    entries = {str(x.get("placement_id")): x for x in self.storage.catalog_entries() if x.get("placement_id")}
    loan_by_key: dict[tuple[str, str, str | None], Loan] = {}
    for loan in self.last_loans:
        try:
            loan_identity = loan.identity_id or self.identity_for_connection(loan.source_provider, loan.source_credential).identity_id
        except Exception:
            loan_identity = loan.identity_id
        loan_by_key[(loan.source_provider, str(loan.record_id or loan.id), loan_identity)] = loan
    entitlements_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entitlement in self.storage.entitlements():
        entitlements_by_key.setdefault(
            (str(entitlement.get("provider_id") or ""), str(entitlement.get("item_id") or "")), []
        ).append(entitlement)
    rows: list[dict[str, Any]] = []
    for placement in sorted(
        self.storage.placements(),
        key=lambda x: int(x.get("created_at") or x.get("updated_at") or 0),
        reverse=True,
    ):
        if placement.get("state") != "present":
            continue
        if identity_id and placement.get("identity_id") not in {identity_id, None, ""}:
            continue
        entry = entries.get(str(placement.get("placement_id"))) or {}
        metadata = dict(entry.get("metadata") or {})
        provider_id = str(placement.get("provider_id") or entry.get("provider_id") or "local")
        row_identity = placement.get("identity_id") or identity_id
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
        if item_id:
            loan_candidates = [
                loan_by_key.get((provider_id, item_id, row_identity)),
                loan_by_key.get((provider_id, item_id, None)),
            ]
            loan = next((value for value in loan_candidates if value is not None), None)
            if loan is None:
                loan = next((
                    value for (p, i, _identity), value in loan_by_key.items()
                    if p == provider_id and i == item_id and (not row_identity or _identity == row_identity)
                ), None)
            if loan is not None:
                value = cover_candidate(
                    loan.thumbnail_url, tier="origin", source="loan",
                    evidence_id=f"{provider_id}:{item_id}", evidence_strength=90,
                )
                if value:
                    cover_candidates.append(value)
            for entitlement in entitlements_by_key.get((provider_id, item_id), []):
                if row_identity and entitlement.get("identity_id") not in {row_identity, None, ""}:
                    continue
                emeta = dict(entitlement.get("metadata") or {})
                value = cover_candidate(
                    emeta.get("cover_url") or emeta.get("thumbnail_url"),
                    tier="origin", source="entitlement",
                    evidence_id=str(entitlement.get("entitlement_id") or f"{provider_id}:{item_id}"),
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
    path=Path(str(row.get('path') or '')).expanduser().resolve()
    if path.is_dir():
        try:manifest=load_bundle_manifest(path,verify=True)
        except BundleError as exc:raise ProviderConfigurationError('Bundle local invalide') from exc
        if not manifest.playlist:raise ProviderConfigurationError('Ce bundle ne possède pas de fichier à ouvrir')
        target=(path/manifest.playlist).resolve()
        if path not in target.parents:raise ProviderConfigurationError('Playlist locale invalide')
        path=target
    if not path.is_file():raise ProviderConfigurationError('Le fichier local a disparu')
    return path


def local_placement_for_source(self, *, provider_id, credential_id, item_id, identity_id):
    """Find only an acquisition-attested unique placement, never by title."""
    artifact_ids={str(row.get('artifact_id') or '') for row in self.storage.acquisitions()
                  if row.get('status')=='complete' and row.get('identity_id')==identity_id
                  and row.get('provider_id')==provider_id and row.get('credential_id')==credential_id
                  and str(row.get('item_id') or '')==str(item_id)}
    rows=[row for row in self.storage.placements() if row.get('state')=='present'
          and row.get('identity_id')==identity_id and row.get('artifact_id') in artifact_ids]
    if len(rows)!=1:return None
    try:self.local_book_path(rows[0]['placement_id'],identity_id=identity_id)
    except ProviderConfigurationError:return None
    return rows[0]


def local_placements_for_sources(self, identity_id):
    """One snapshot for search; only unique acquisition-attested placements."""
    artifacts = {}
    for row in self.storage.acquisitions(status='complete'):
        if row.get('identity_id') != identity_id or not row.get('artifact_id'): continue
        key = (row.get('provider_id'), row.get('credential_id'), str(row.get('item_id') or ''))
        artifacts.setdefault(key, set()).add(row['artifact_id'])
    placements = {}
    for row in self.storage.placements():
        if row.get('identity_id') == identity_id and row.get('state') == 'present':
            placements.setdefault(row.get('artifact_id'), []).append(row)
    linked = {}
    for key, ids in artifacts.items():
        rows = [row for artifact_id in ids for row in placements.get(artifact_id, [])]
        if len(rows) != 1: continue
        try: self.local_book_path(rows[0]['placement_id'], identity_id=identity_id)
        except ProviderConfigurationError: continue
        linked[key] = rows[0]
    return linked


def catalog_entries(self, *, state: str | None=None) -> list[dict[str, Any]]:
    return LocalCatalog(self.storage, self.library_dir()).entries(state=state)


def library_entries(self, *, identity_id: str | None=None, scan: bool=False, logger) -> list[dict[str, Any]]:
    """Canonical Library projection.

    Local rows are grouped by content identity (SHA-256/Artifact) rather than
    by provider/item. Remote-only rights remain distinct until reconciliation
    can bind them to a local Artifact.
    """
    _lib_started = time.monotonic()
    identity_id = identity_id or (self.current_identity.identity_id if self.current_identity else None)
    logger.info("TRACE library-entries begin identity=%s filesystem_scan=%s", identity_id, "requested" if scan else "skipped")
    if scan:
        self.catalog_scan()
    merged: dict[str, dict[str, Any]] = {}
    source_alias: dict[tuple[str, str], str] = {}
    local_keys = {}
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
        # The v0.7 backup mirror can create local_state='local' for an
        # unverified placeholder, even when its recorded path was deleted or
        # moved. It is history, not physical presence. Only an exact current
        # present Placement in this identity can make that row local in UI.
        # Keep the SQLite catalogue/backup unchanged for later reconciliation.
        metadata = dict(row.get("metadata") or {})
        if metadata.get("imported_from") == "backup_items":
            placement_id = str(row.get("placement_id") or "")
            if not placement_id or not any(
                str(p.get("placement_id") or "") == placement_id
                for p in placements_by_artifact.get(artifact_id, ())
            ):
                continue
        item_id = str(row.get("item_id") or artifact_id or row.get("catalog_entry_id"))
        artifact = artifacts.get(artifact_id, {})
        sha256 = str(artifact.get("sha256") or "")
        if not sha256 and artifact_id.startswith("sha256:"):
            sha256 = artifact_id.split(":", 1)[1]
        canonical = f"sha256:{sha256}" if sha256 else (artifact_id or f"source:{provider_id}:{item_id}")
        key = f"artifact:{canonical}" if not canonical.startswith("source:") else canonical
        source_alias[(provider_id, item_id)] = key
        local_keys[(row.get('placement_id'), artifact_id)] = key
        metadata = dict(row.get("metadata") or {})
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
                "local_path": metadata.get("path") or (current_placement or {}).get("path"),
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
        source_key=(provider_id,item_id); canonical_key=source_alias.get(source_key, f"source:{provider_id}:{item_id}")
        if provider_id == 'bnf-pret' and entitlement_type == 'loan':
            canonical_key = bnf_links.get((entitlement_identity, entitlement.get('credential_id'),
                                           entitlement.get('account_id'), entitlement.get('loan_id'), item_id), canonical_key)
        existing = merged.get(canonical_key, {})
        meta = dict(entitlement.get("metadata") or {})
        loan = loans_by_key.get((provider_id, str(entitlement.get("credential_id") or ""), entitlement.get("account_id"), item_id))
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
