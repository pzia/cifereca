"""#3185: read-only dependency migration plan for one verified canonical pair.

No right can be inferred from common SHA. The plan is an *inventory*, never
permission to relocate a reference or unlink an ebook. Only the confirmed
Core supervisor may recheck the bytes and execute transactional relinks.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Literal

from .canonical_dedup_preview import preview_canonical_duplicate
from .duplicate_maintenance_guard import _mentions_exact_path

_MAX_CATALOGUE = 128
_MAX_BACKUP = 128
_MAX_LEDGER = 100000
_MAX_JSON = 65536
_COMPLETED = "canonical.completed-delivery.v1"


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("canonical_reference_plan_duplicate_field")
        result[key] = value
    return result


def _read(raw):
    if type(raw) is not str or len(raw) > _MAX_JSON:
        raise ValueError("canonical_reference_plan_invalid_json")
    value = json.loads(raw, object_pairs_hook=_unique_pairs)
    if type(value) is not dict:
        raise ValueError("canonical_reference_plan_invalid_json")
    return value


@dataclass(frozen=True, slots=True)
class CanonicalReferenceMigrationPlan:
    status: Literal["review_required", "blocked"]
    reason: str
    candidate_placement_id: str
    keeper_placement_id: str
    catalogue_candidates: int = 0
    backup_candidates: int = 0
    completed_receipt_candidates: int = 0
    unverified_references: int = 0
    artifact_origin_links: int = 0
    linked_source_references: int = 0
    # Proof of an inspected snapshot, NOT consent, a right, or unlink authority.
    physical_pair_verified_at_preview: bool = False

    @property
    def needs_separate_confirmation(self) -> bool:
        return True


def plan_verified_canonical_reference_migration(
    storage, *, library_root: Path, candidate_placement_id: str,
    keeper_placement_id: str,
) -> CanonicalReferenceMigrationPlan:
    """Classify *candidate* references without exposing private source details.

    The candidate must have passed the canonical pair's actual SHA/identity
    verification. Only precisely scoped references with already published
    SourceReferences enter the backup candidate count; all other references
    stay unverified. 'review_required' is never a grant to commit.
    """
    def plan(status, reason, *, catalogue=0, backup=0, receipts=0,
             unverified=0, preview=None):
        return CanonicalReferenceMigrationPlan(
            status, reason, candidate_placement_id, keeper_placement_id,
            catalogue, backup, receipts, unverified,
            preview.artifact_origin_links if preview else 0,
            preview.linked_source_references if preview else 0,
            bool(preview and preview.provenance_counted),
        )

    if (not isinstance(library_root, Path)
            or type(candidate_placement_id) is not str
            or type(keeper_placement_id) is not str
            or not 0 < len(candidate_placement_id) <= 256
            or not 0 < len(keeper_placement_id) <= 256
            or candidate_placement_id == keeper_placement_id):
        return plan("blocked", "invalid_pair")

    preview = preview_canonical_duplicate(
        storage, library_root=library_root,
        candidate_placement_id=candidate_placement_id,
        keeper_placement_id=keeper_placement_id,
    )
    if not preview.provenance_counted or preview.reason not in (
        "active_references", "verified_no_active_references",
    ):
        return plan("blocked", "pair_unverified")

    try:
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            sql = (
                "SELECT path,identity_id,artifact_id,state,provider_id,"
                "source_item_id FROM placements WHERE placement_id=?"
            )
            candidate = db.execute(sql, (candidate_placement_id,)).fetchone()
            keeper = db.execute(sql, (keeper_placement_id,)).fetchone()
            if (candidate is None or keeper is None
                    or candidate[1:3] != keeper[1:3]
                    or candidate[0] == keeper[0]
                    or candidate[3:] != ("present", None, None)
                    or keeper[3:] != ("present", None, None)
                    or type(candidate[0]) is not str
                    or type(candidate[1]) is not str
                    or type(candidate[2]) is not str):
                return plan("blocked", "pair_changed")
            path, owner, artifact = candidate[:3]
            sha = artifact[7:] if artifact.startswith("sha256:") else ""
            size_row = db.execute(
                "SELECT size FROM artifacts WHERE artifact_id=?", (artifact,),
            ).fetchone()
            if size_row is None or type(size_row[0]) is not int:
                return plan("blocked", "artifact_changed")
            size = size_row[0]
            unverified = 0
            catalogue = backup = receipts = 0

            catalogue_rows = db.execute(
                "SELECT placement_id,identity_id,artifact_id,metadata_json "
                "FROM catalog_entries LIMIT 100001",
            ).fetchall()
            if len(catalogue_rows) > 100000:
                return plan("blocked", "dependency_budget")
            for placement, identity, linked_artifact, raw in catalogue_rows:
                try:
                    metadata = _read(raw)
                except (ValueError, TypeError, RecursionError):
                    return plan("blocked", "dependency_unreadable")
                names_path = _mentions_exact_path(metadata, path)
                if placement != candidate_placement_id and not names_path:
                    continue
                if (placement == candidate_placement_id
                        and identity == owner and linked_artifact == artifact
                        and metadata.get("path") in (None, "", path)
                        and not any(_mentions_exact_path(value, path)
                                    for key, value in metadata.items()
                                    if key != "path")):
                    catalogue += 1
                else:
                    unverified += 1
            if catalogue > _MAX_CATALOGUE:
                return plan("blocked", "dependency_budget")

            backup_rows = db.execute(
                "SELECT provider_id,credential_id,item_id,identity_id,"
                "account_id,status,sha256,size FROM backup_items "
                "WHERE local_path=? LIMIT ?",
                (path, _MAX_BACKUP + 1),
            ).fetchall()
            if len(backup_rows) > _MAX_BACKUP:
                return plan("blocked", "dependency_budget")
            for provider, cred, item, identity, account, status, digest, count in backup_rows:
                if (identity != owner or type(provider) is not str or not provider
                        or type(cred) is not str or not cred
                        or type(item) is not str or not item
                        or type(account) is not str or status != "complete"
                        or digest != sha or count != size):
                    unverified += 1
                    continue
                connection = db.execute(
                    "SELECT identity_id,enabled FROM provider_connections "
                    "WHERE provider_id=? AND credential_id=?",
                    (provider, cred),
                ).fetchone()
                accounts = db.execute(
                    "SELECT account_id FROM provider_accounts "
                    "WHERE identity_id=? AND provider_id=? AND credential_id=?",
                    (owner, provider, cred),
                ).fetchall()
                evidence = db.execute(
                    "SELECT COUNT(*) FROM source_references s "
                    "JOIN origin_links o ON "
                    "o.source_reference_id=s.source_reference_id "
                    "WHERE s.identity_id=? AND s.provider_id=? "
                    "AND s.credential_id=? AND s.account_id=? "
                    "AND s.edition_id=? AND o.origin_kind='provider' "
                    "AND o.identity_id=? AND o.artifact_id=? "
                    "AND o.evidence_ref IS NOT NULL AND o.evidence_ref!=''",
                    (owner, provider, cred, account, item, owner, artifact),
                ).fetchone()[0]
                if (connection == (owner, 1)
                        and sum(row[0] == account for row in accounts) == 1
                        and evidence > 0):
                    backup += 1
                else:
                    unverified += 1

            ledger_rows = db.execute(
                "SELECT identity_id,artifact_id,status,metadata_json "
                "FROM acquisition_ledger LIMIT ?",
                (_MAX_LEDGER + 1,),
            ).fetchall()
            if len(ledger_rows) > _MAX_LEDGER:
                return plan("blocked", "dependency_budget")
            for identity, linked_artifact, status, raw in ledger_rows:
                try:
                    payload = _read(raw)
                except (ValueError, TypeError, RecursionError):
                    return plan("blocked", "dependency_unreadable")
                if not _mentions_exact_path(payload, path):
                    continue
                if (identity == owner and linked_artifact == artifact
                        and status == "complete"
                        and payload.get("schema") == _COMPLETED
                        and payload.get("path") == path
                        and payload.get("sha256") == sha
                        and not any(_mentions_exact_path(value, path)
                                    for key, value in payload.items()
                                    if key != "path")):
                    # Still subject to canonical receipt/SourceKey validation
                    # in the destructive supervisor; counting isn't proof.
                    receipts += 1
                else:
                    unverified += 1

            if unverified:
                return plan(
                    "blocked", "unverified_references",
                    catalogue=catalogue, backup=backup,
                    receipts=receipts, unverified=unverified,
                    preview=preview,
                )
            return plan(
                "review_required", "fresh_supervisor_validation_required",
                catalogue=catalogue, backup=backup, receipts=receipts,
                preview=preview,
            )
    except (sqlite3.Error, OSError, TypeError, ValueError, RecursionError):
        return plan("blocked", "inspection_unavailable")
    except Exception:
        return plan("blocked", "inspection_unavailable")


__all__ = [
    "CanonicalReferenceMigrationPlan",
    "plan_verified_canonical_reference_migration",
]
