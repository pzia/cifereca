"""Persisted synchronization, action statistics and dashboard projections.

Read-only state projection over the facade; no widget, scan or authentication."""
from __future__ import annotations

import time
import json
import hashlib
from collections import Counter
from dataclasses import asdict
from typing import Any


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


def identity_statistics(self, identity_id, *, window_seconds: int=2592000):
    """Persisted inventory facts only; no scan, authentication or download."""
    placements=[r for r in self.storage.placements(state='present') if r.get('identity_id')==identity_id]
    artifacts={r['artifact_id']:r for r in self.storage.artifacts()}
    ids={r['artifact_id'] for r in placements if r['artifact_id'] in artifacts}
    formats=Counter();sources=Counter();covers=Counter()
    for aid in ids:
        artifact=artifacts[aid];formats[str(artifact.get('media_format') or 'unknown')]+=1
        meta=artifact.get('metadata') or {}
        if artifact.get('media_format') in {'epub','kepub'}:
            covers['extracted' if meta.get('epub_cover') else str(meta.get('epub_cover_status') or 'not_inspected')]+=1
    for provider in {r.get('provider_id') or 'local' for r in placements}:
        sources[provider]=len({r['artifact_id'] for r in placements if (r.get('provider_id') or 'local')==provider})
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
            'remote_owned_rights':len(owned),'formats':dict(formats),'sources':dict(sources),'epub_covers':dict(covers),
            'actions':asdict(self.action_statistics(identity_id, window_seconds=window_seconds)),
            'scope':'persisted_inventory_last_observed_provider_loans_and_action_window'}


def dashboard_state(self) -> list[dict[str, Any]]:
    """Return dashboard cards without triggering network I/O."""
    cards: list[dict[str, Any]] = []
    for identity in self.identities:
        providers = sorted({a.provider_id for a in self.accounts_for_identity(identity.identity_id)})
        latest = self.storage.latest_sync_run(identity.identity_id)
        profile_id = self.storage.adept_profile_binding_get("identity", identity.identity_id)
        try:
            deep_last_at = int(self.storage.metadata_get("catalog_deep_last_at") or 0) or None
        except (TypeError, ValueError):
            deep_last_at = None
        labels = {"search": "Recherche", "loans": "Emprunts", "history": "Historique",
                  "bookings": "Réservations", "borrow": "Emprunter", "digital_download": "Récupérer les prêts",
                  "direct_download": "Téléchargement direct", "personal_library": "Livres de mon compte fournisseur",
                  "return_loan": "Restituer un prêt", "renew_loan": "Prolonger un prêt"}
        capabilities = set(); sources = []
        credentials = {(account.provider_id, account.credential_id) for account in self.accounts_for_identity(identity.identity_id)}
        for session in self._sessions.values():
            descriptor = session.descriptor
            public = getattr(descriptor, "instantiation", "multiple") == "singleton_public"
            if not self._provider_enabled_for_identity(session.provider_id, identity.identity_id):
                continue
            if not public and (session.provider_id, session.credential_id) not in credentials:
                continue
            sources.append({"provider_id": session.provider_id, "label": descriptor.display_name,
                            "connected": bool(session.connected)})
            if session.connected:
                capabilities.update(key for key in labels if bool(getattr(session.capabilities, key, False)))
        cards.append({
            "sources": sources,
            "statistics": self.identity_statistics(identity.identity_id),
            "selected": bool(self.current_identity and self.current_identity.identity_id == identity.identity_id),
            "capabilities": [{"id": key, "label": labels[key]} for key in labels if key in capabilities],
            "identity_id": identity.identity_id, "label": identity.label, "directory_name": identity.directory_name,
            "providers": providers, "adept_profile_id": profile_id,
            "sync": latest, "deep_last_at": deep_last_at,
        })
    return cards
