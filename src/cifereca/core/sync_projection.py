"""Persisted synchronization, action statistics and dashboard projections.

Read-only state projection over the facade; no widget, scan or authentication."""
from __future__ import annotations

import time
import json
import hashlib
from collections import Counter, defaultdict
from dataclasses import asdict
from sqlite3 import Error as SqliteError
from typing import Any

from .library_projection import _read_provider_origin_coordinates


def action_statistics(self, identity_id: str, *, window_seconds: int=2592000):
    from ..plugin_api import ActionStatistics
    window = max(60, int(window_seconds or 2592000))
    until = int(time.time()); since = until - window
    counts = {(row["action_type"], row["outcome"]): int(row["count"]) for row in self.storage.action_event_counts(identity_id=identity_id, since=since, until=until)}
    def count(action: str, outcome: str) -> int:
        return counts.get((action, outcome), 0)
    return ActionStatistics(
        window, since, until,
        count("acquisition", "success"), count("acquisition", "failed"),
        count("import", "success"), count("import", "failed"),
        count("synchronization", "success"), count("synchronization", "failed"),
        count("transformation", "success"), count("transformation", "failed"),
        count("transfer", "success"), count("transfer", "failed"),
    )


def _provider_origin_statistics(storage, identity_id: str) -> tuple[dict[str, int], int | None]:
    """Count persisted provider proofs and uncovered local artifacts in one snapshot."""
    if not identity_id:
        return {}, None
    try:
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            origins = _read_provider_origin_coordinates(db, identity_id=identity_id)
            if origins is None:
                return {}, None
            local_artifacts = {
                artifact_id for artifact_id, in db.execute(
                    "SELECT DISTINCT p.artifact_id FROM placements p "
                    "JOIN artifacts a ON a.artifact_id=p.artifact_id "
                    "WHERE p.identity_id=? AND p.state='present'", (identity_id,),
                )
            }
    except (SqliteError, AttributeError):
        return {}, None
    by_provider = defaultdict(set)
    for artifact_id, _, provider, *_ in origins:
        by_provider[provider].add(artifact_id)
        local_artifacts.discard(artifact_id)
    return {provider: len(ids) for provider, ids in by_provider.items()}, len(local_artifacts)


def identity_statistics(self, identity_id, *, window_seconds: int=2592000):
    """Persisted inventory facts only; no scan, authentication or download."""
    placements=[r for r in self.storage.placements(state='present') if r.get('identity_id')==identity_id]
    artifacts={r['artifact_id']:r for r in self.storage.artifacts()}
    ids={r['artifact_id'] for r in placements if r['artifact_id'] in artifacts}
    formats=Counter();covers=Counter()
    for aid in ids:
        artifact=artifacts[aid];formats[str(artifact.get('media_format') or 'unknown')]+=1
        meta=artifact.get('metadata') or {}
        if artifact.get('media_format') in {'epub','kepub'}:
            covers['extracted' if meta.get('epub_cover') else str(meta.get('epub_cover_status') or 'not_inspected')]+=1
    sources, without_provider_origin = _provider_origin_statistics(self.storage, identity_id)
    try:observations=json.loads(self.storage.metadata_get('loan_statistics:'+str(identity_id)) or '{}')
    except (ValueError,TypeError):observations={}
    if not isinstance(observations,dict):observations={}
    selected=[]
    for account in self.accounts_for_identity(identity_id):
        key=hashlib.sha256(f'{account.provider_id}:{account.credential_id}:{account.unique_id}'.encode()).hexdigest()
        if isinstance(observations.get(key),dict):selected.append(observations[key])
    loan_keys={key for row in selected for key in row.get('loan_keys',[])}
    owned={r['entitlement_id'] for r in self.storage.entitlements(state='active') if r.get('identity_id')==identity_id and r.get('entitlement_type')=='owned'}
    return {'identity_id':identity_id,'local_books':len(ids),'local_placements':len(placements),'local_files':sum((r.get('metadata') or {}).get('artifact_kind')!='bundle' for r in placements),
            'known_loans':len(loan_keys) if selected else None,'loan_sources_observed':len(selected),
            'loans_observed_at':min((r.get('observed_at') for r in selected),default=None) if all(isinstance(r.get('observed_at'),int) for r in selected) else None,
            'remote_owned_rights':len(owned),'formats':dict(formats),'sources':sources,'epub_covers':dict(covers),
            'local_books_without_provider_origin':without_provider_origin,
            'actions':asdict(self.action_statistics(identity_id, window_seconds=window_seconds)),
            'scope':'persisted_inventory_last_observed_provider_loans_and_action_window'}


def _configured_source_keys_for_identity(service, identity_id: str) -> set[tuple[str, str]] | None:
    """Read configured accounts as negative-capability proof, fail closed.

    Live upstream accounts may omit a configured lender after login failure.
    A connected store alone cannot prove absence of lending sources.
    """
    try:
        rows = service.config.get("providers.accounts", None)
    except Exception:
        return None
    if type(rows) is not list or len(rows) > 2048:
        return None
    keys: set[tuple[str, str]] = set()
    for row in rows:
        if type(row) is not dict:
            return None
        enabled = row.get("enabled", True)
        if enabled is False:
            continue
        if enabled is not True:
            return None
        owner = row.get("identity_id")
        # An unscoped legacy account might belong to this profile.
        if type(owner) is not str or not owner:
            return None
        if owner != identity_id:
            continue
        provider, credential = row.get("provider"), row.get("id")
        if (type(provider) is not str or not provider
                or type(credential) is not str or not credential):
            return None
        key = (provider, credential)
        if key in keys:
            return None
        keys.add(key)
    return keys


def dashboard_state(self) -> list[dict[str, Any]]:
    """Return dashboard cards without triggering network I/O."""
    cards: list[dict[str, Any]] = []
    for identity in self.identities:
        account_rows = tuple(self.accounts_for_identity(identity.identity_id))
        providers = sorted({a.provider_id for a in account_rows})
        historic = self.storage.latest_sync_run(identity.identity_id)
        # Only runs initiated since this service was constructed describe the
        # current launch. Keep the original in SQLite for diagnostics/history.
        started = historic.get("started_at") if isinstance(historic, dict) else None
        launch = getattr(self, "_dashboard_launch_at", None)
        baseline = getattr(self, "_dashboard_initial_run_ids", {})
        initial_id = baseline.get(identity.identity_id) if isinstance(baseline, dict) else None
        latest = (historic if type(started) is int and type(launch) is int
                  and started >= launch and historic.get("sync_run_id") != initial_id
                  else None)
        profile_id = self.storage.adept_profile_binding_get("identity", identity.identity_id)
        try:
            deep_last_at = int(self.storage.metadata_get("catalog_deep_last_at") or 0) or None
        except (TypeError, ValueError):
            deep_last_at = None
        labels = {"search": "Recherche", "loans": "Emprunts", "history": "Historique",
                  "borrow": "Emprunter", "digital_download": "Récupérer les prêts",
                  "direct_download": "Téléchargement direct", "personal_library": "Livres de mon compte fournisseur",
                  "return_loan": "Restituer un prêt", "renew_loan": "Prolonger un prêt"}
        capabilities = set(); sources = []
        credential_rows = [(account.provider_id, account.credential_id)
                           for account in account_rows]
        credentials = set(credential_rows)
        # Negative lending evidence is only available from a unique, live
        # session for EVERY configured private source. A missing/disconnected
        # or partially declared source is unknown, never "no loan source".
        configured_keys = _configured_source_keys_for_identity(self, identity.identity_id)
        non_lending_proven = (
            bool(account_rows) and len(credentials) == len(credential_rows)
            and configured_keys is not None and configured_keys == credentials
        )
        matched_private_sessions = Counter()
        for session in self._sessions.values():
            descriptor = session.descriptor
            public = getattr(descriptor, "instantiation", "multiple") == "singleton_public"
            if not self._provider_enabled_for_identity(session.provider_id, identity.identity_id):
                continue
            if not public and (session.provider_id, session.credential_id) not in credentials:
                continue
            if not public:
                matched_private_sessions[(session.provider_id, session.credential_id)] += 1
            loans = getattr(getattr(session, "capabilities", None), "loans", None)
            if (getattr(session, "connected", None) is not True
                    or type(loans) is not bool or loans):
                non_lending_proven = False
            sources.append({"provider_id": session.provider_id, "label": descriptor.display_name,
                            "connected": bool(session.connected),
                            # Presentation-only descriptor fact; never a grant
                            # to search, connect or access any private account.
                            "public_catalogue": public})
            if session.connected:
                capabilities.update(key for key in labels if bool(getattr(session.capabilities, key, False)))
        non_lending_proven = (
            non_lending_proven
            and all(matched_private_sessions[key] == 1 for key in credentials)
        )
        cards.append({
            # A closed, identity-scoped capability statement. This is not a
            # lender-right, loan expiry check or authorization for acquisition.
            "loan_sources_explicitly_absent": non_lending_proven,
            "sources": sources,
            "statistics": self.identity_statistics(identity.identity_id),
            "selected": bool(self.current_identity and self.current_identity.identity_id == identity.identity_id),
            "capabilities": [{"id": key, "label": labels[key]} for key in labels if key in capabilities],
            "identity_id": identity.identity_id, "label": identity.label, "directory_name": identity.directory_name,
            "providers": providers, "adept_profile_id": profile_id,
            "sync": latest, "deep_last_at": deep_last_at,
        })
    return cards
