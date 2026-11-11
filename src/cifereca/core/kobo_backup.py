"""Kobo Vault backup coordination over existing artifact/runtime interfaces.

Manifest authority remains provider.kobo; acquisition execution remains the
negotiated kobo-adept contract. State is owned by the service facade."""
from __future__ import annotations

from .legacy_checkpoint_read import _kobo_checkpoint_key, _kobo_checkpoint_for
from .legacy_diagnostic_projection import (
    _safe_kobo_opening_decision,
    safe_kobo_backup_report,
    _KOBO_DIAGNOSTIC_METRICS,
    _KOBO_DIAGNOSTIC_STATUSES,
    _safe_kobo_backup_diagnostic_detail,
)

import time
import re
import json
from pathlib import Path
from typing import Any, Callable
from ..acquisition import Acquisition
from ..library_files import sha256_file
from ..paths import acquisition_lock_file
from ..models import SearchResult
from ..providers import ProviderConfigurationError


def _execute_kobo_acquisition(self, item: SearchResult, target_dir: Path) -> Acquisition:
    """Retired D3 direct Kobo executor: use the selected canonical delivery API."""
    raise ProviderConfigurationError("kobo_legacy_direct_get_retired_use_canonical_provider")


def kobo_backup_enabled(self) -> bool:
    # Fail closed for a manually edited/migrated malformed config:
    # bool("false") would otherwise trigger implicit local downloads.
    return self.config.get("kobo_backup.enabled", False) is True


def kobo_backup_interval_minutes(self) -> int:
    try:
        return max(15, int(self.config.get("kobo_backup.interval_minutes", 60)))
    except (TypeError, ValueError):
        return 60


_KOBO_CHECKPOINT_STATUSES = frozenset({
    "complete", "partial", "failed", "aborted", "cancelled",
    "attention_required", "running", "interrupted", "manifest_fetch",
    "skipped_fresh",
})


def _kobo_safe_checkpoint_status(value: Any) -> str:
    """Accept only internal checkpoint states, never free provider strings."""
    return value if type(value) is str and value in _KOBO_CHECKPOINT_STATUSES else "unknown"


def _kobo_checkpoint_number(value: Any, *, limit: int = 1_000_000) -> int:
    """Fail closed on malformed local SQLite metadata rather than raising."""
    return value if type(value) is int and 0 <= value <= limit else 0



def kobo_backup_diagnostic(self) -> dict[str, Any]:
    """Read-only, source-scoped diagnostic; archived W2 receipts are inert.

    Legacy backup_items remain on disk for the user but are never read as
    current progress, rights, errors or reconcilable acquisitions.
    """
    try:
        opening_raw = json.loads(
            self.storage.metadata_get("kobo_backup_opening_decision_v1") or "null"
        )
    except (TypeError, ValueError):
        opening_raw = None
    return {
        "items": 0,
        "items_blocked_by_scope": 0,
        "statuses": {},
        "opening_decision": _safe_kobo_opening_decision(opening_raw),
        "sources": _kobo_source_summaries(self),
        "error_summary": [],
        "error_examples": [],
        "reconcile_examples": [],
        "attention_examples": [],
        "storage_schema_version": self.storage.schema_version(),
    }


def _kobo_source_summaries(self) -> list[dict[str, Any]]:
    """Expose only safe checkpoint counters for the local diagnostic."""
    summaries = []
    # kobo-sync-errors must work after a cold restart without loading or
    # connecting provider plugins. Sessions are transient; the connection
    # registry and checkpoint keys are durable in the local SQLite database.
    scopes = {
        (str(row.get("identity_id") or ""), str(row.get("credential_id") or ""))
        for row in self.storage.provider_connections()
        if row.get("provider_id") == "kobo"
        and row.get("identity_id") and row.get("credential_id")
    }
    # Retain compatibility with tests/legacy in-memory sessions that have not
    # yet been inserted in provider_connections.
    for session in self._sessions.values():
        if session.provider_id == "kobo":
            identity = self.identity_for_connection("kobo", session.credential_id).identity_id
            scopes.add((identity, session.credential_id))
    for identity, credential_id in sorted(scopes):
        source_id = _kobo_checkpoint_key(identity, credential_id).rsplit(".", 1)[-1]
        row = _kobo_checkpoint_for(self, identity, credential_id)
        if row is None:
            continue
        counts = row.get("counts") if isinstance(row.get("counts"), dict) else {}
        safe_counts = {
            key: int(value) for key, value in counts.items()
            if key in {"scanned", "attempted", "downloaded", "unchanged",
                       "pending", "attention_required", "errors", "skipped",
                       "skipped_archived", "remaining", "reconcile_pending", "reconcile_skipped",
                       "items_blocked_by_scope", "malformed_manifest_items",
                       "duplicate_manifest_items",
                       "provider_rejected_manifest_items"}
            and type(value) is int and 0 <= value <= 1000000
        }
        raw_streak = row.get("failure_streak")
        failure_streak = (
            raw_streak if type(raw_streak) is int and 0 <= raw_streak <= 6 else 0
        )
        total = _kobo_checkpoint_number(row.get("total"))
        summaries.append({
            "source_id": source_id,
            "status": _kobo_safe_checkpoint_status(row.get("status")),
            "position": min(_kobo_checkpoint_number(row.get("processed")), total),
            "total": total,
            "updated_at": _kobo_checkpoint_number(
                row.get("updated_at"), limit=4_102_444_800,
            ),
            # Public diagnostics: safe numeric cooldown, no raw credential.
            "failure_streak": failure_streak,
            **safe_counts,
        })
    summaries.sort(key=lambda row: row["source_id"])
    return summaries


def sync_kobo_backup(self, progress: Callable[[dict[str, Any]], None] | None=None, *, credential_id: str | None=None, retry_scope_blocked: bool=False, cancelled: Callable[[], bool] | None=None, lock_factory, lock_path) -> dict[str, Any]:
    """Retired legacy W2 facade: never invoke a fallback or acquire its lock."""
    raise ProviderConfigurationError("kobo_legacy_w2_retired_use_canonical_provider")


def _attested_received_kobo_copy(
    row: dict[str, Any], path: Path, *,
    identity_id: str, placements: dict[str, dict[str, Any]],
) -> bool:
    """A copied local file is not an *orphan Kobo acquisition* of recipient B.

    Trust neither the catalogue label nor a filename alone: the recipient's
    own persisted placement must corroborate the exact Core transfer markers.
    This exception affects only the global unknown-books counter; a matching
    supplier product still needs per-item reconciliation, never an implicit
    Kobo entitlement/backup.
    """
    metadata = row.get("metadata")
    if (row.get("provider_id") != "kobo" or type(metadata) is not dict
            or type(identity_id) is not str or not identity_id
            or not path.is_absolute() or path.is_symlink()
            or path.resolve() != path):
        return False
    placement_id = row.get("placement_id")
    placement = placements.get(placement_id) if type(placement_id) is str else None
    if (type(placement) is not dict
            or placement.get("state") != "present"
            or placement.get("identity_id") != identity_id
            or placement.get("provider_id") != "kobo"
            or str(placement.get("source_item_id") or "") != str(row.get("item_id") or "")
            or str(placement.get("artifact_id") or "") != str(row.get("artifact_id") or "")
            or placement.get("path") != str(path)):
        return False
    placement_metadata = placement.get("metadata")
    if type(placement_metadata) is not dict:
        return False
    origin = metadata.get("file_origin_identity_id")
    from_placement = metadata.get("materialized_from")
    if (type(origin) is not str or not origin or origin == identity_id
            or type(from_placement) is not str or not from_placement):
        return False
    return all(
        metadata.get(field) == placement_metadata.get(field) == value
        for field, value in (
            ("transfer_kind", "local_copy"),
            ("file_origin_identity_id", origin),
            ("materialized_from", from_placement),
        )
    )


def _verified_canonical_kobo_unlinked_exemptions(
    service: Any, identity_id: str, candidate_paths: set[str],
) -> set[str]:
    """Exclude independently published canonical Kobo B from 'unlinked'.

    An old backup_items path was historically the only exclusion. Once an
    exact legacy row is retired after canonical delivery, the same file must
    not reappear as a foreign/orphan library book and block another source.
    Neither title, filename, SHA alone nor an arbitrary catalogue provider
    label is sufficient: require current active SourceReference, provider
    OriginLink, unique registered profile, enabled exact account, present
    Placement and freshly verified current bytes. This only refines a
    diagnostic count. It cannot authorize a GET, right or file adoption.
    """
    if (type(identity_id) is not str or not identity_id
            or not candidate_paths or len(candidate_paths) > 256):
        return set()
    from .convergence_verified_presence import _verified_copy
    import sqlite3

    try:
        root = Path(service.library_dir())
        with service.storage._lock, service.storage._connect() as db:
            db.execute("BEGIN")
            owner = db.execute(
                "SELECT directory_name FROM identities WHERE identity_id=?",
                (identity_id,),
            ).fetchone()
            if (owner is None or type(owner[0]) is not str
                    or not owner[0] or owner[0] in {".", ".."}
                    or "/" in owner[0] or "\\" in owner[0]
                    or db.execute(
                        "SELECT 1 FROM identities WHERE directory_name=? "
                        "AND identity_id<>? LIMIT 1",
                        (owner[0], identity_id),
                    ).fetchone() is not None):
                return set()
            # DISTINCT avoids one Artifact/Placement multiplying through
            # multiple provider rights. The cap fails closed instead of
            # claiming an exhaustive local provenance scan.
            rows = db.execute(
                "SELECT DISTINCT p.path,a.sha256,a.size FROM placements p "
                "JOIN artifacts a ON a.artifact_id=p.artifact_id "
                "JOIN origin_links o ON o.artifact_id=p.artifact_id "
                "AND o.identity_id=p.identity_id AND o.origin_kind='provider' "
                "JOIN source_references s ON s.source_reference_id=o.source_reference_id "
                "AND s.identity_id=p.identity_id "
                "WHERE p.identity_id=? AND p.state='present' "
                "AND s.provider_id='kobo' AND s.state='active' "
                "AND s.evidence_kind='provider_response' AND s.evidence_ref<>'' "
                "AND o.evidence_ref<>'' "
                "AND EXISTS (SELECT 1 FROM provider_connections c "
                "JOIN provider_accounts pa ON pa.connection_id=c.connection_id "
                "WHERE c.provider_id='kobo' AND c.credential_id=s.credential_id "
                "AND c.identity_id=s.identity_id AND c.enabled=1 "
                "AND pa.provider_id='kobo' AND pa.credential_id=s.credential_id "
                "AND pa.identity_id=s.identity_id AND pa.account_id=s.account_id) "
                "LIMIT 257",
                (identity_id,),
            ).fetchall()
            if len(rows) > 256:
                return set()
            admitted: set[str] = set()
            for path, sha, size in rows:
                if type(path) is not str:
                    continue
                normalized = str(Path(path).resolve(strict=False))
                if normalized not in candidate_paths:
                    continue
                if _verified_copy(
                    path, root=root, owner_directory=owner[0],
                    registered_directories=frozenset((owner[0],)),
                    size=size, sha256=sha,
                ):
                    admitted.add(normalized)
            return admitted
    except (sqlite3.Error, OSError, TypeError, ValueError, RuntimeError):
        return set()


def kobo_unlinked_local_count(self, identity_id: str) -> int:
    """Count actual local books without independently attested provenance.

    A historical 0.7 backup_items.local_path is only an archive/recovery
    locator, including when status='complete'. It cannot certify a file or
    suppress this count. A separately attested received copy or exact Core B
    SourceReference+OriginLink+currently hashed Placement remains exempt.
    _indexed_kobo_catalog_paths still gates per-edition reconciliation.
    """
    default_id = self.identities[0].identity_id if self.identities else None
    placements = {
        str(row.get("placement_id") or ""): row
        for row in self.storage.placements(state="present")
        if row.get("identity_id") == identity_id
    }
    paths = set()
    for row in self.storage.catalog_entries():
        owner = row.get("identity_id") or default_id
        raw = (row.get("metadata") or {}).get("path")
        if (owner == identity_id and row.get("local_state") == "local"
                and str(row.get("provider_id") or "local") in {"local", "kobo"} and raw):
            path = Path(raw)
            if path.is_file():
                if _attested_received_kobo_copy(
                    row, path, identity_id=identity_id, placements=placements,
                ):
                    continue
                paths.add(str(path.resolve()))
    # A fully independent Core B publication may outlive the 0.7 row.
    # Verify its current SourceReference/OriginLink + physical bytes instead
    # of resurrecting an orphan count (or trusting the old archive).
    return len(
        paths - _verified_canonical_kobo_unlinked_exemptions(self, identity_id, paths)
    )
