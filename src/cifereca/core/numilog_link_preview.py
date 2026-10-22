"""Locked, non-mutating Numilog right/file evidence preview (#2714).

A successful result is *not* a plan token or authorization to attach a file.
The caller must first establish the active identity and current configured
roots. This function checks live SQLite evidence and fresh file bytes, without
ever writing to the library or persisting a preview.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3

from ..storage import Storage, CURRENT_SCHEMA_VERSION
from .library_fresh_file_check import revalidate_observed_file
from .library_inventory import FileObservation


@dataclass(frozen=True, repr=False)
class NumilogLinkEvidencePreview:
    status: str
    reason: str
    read_only: bool = True
    attachment_authorized: bool = False
    requires_explicit_confirmation: bool = True


def _rows(db, query, values):
    # Storage's thread-local SQLite connection deliberately has tuple rows;
    # never change its shared row_factory while other Core paths use it.
    cursor = db.execute(query, values)
    fields = tuple(item[0] for item in cursor.description)
    return [dict(zip(fields, row)) for row in cursor.fetchmany(3)]


def _single(db, table, where, values):
    # Table/where literals only, parametrized values, bounded evidence.
    rows = _rows(db, f"SELECT * FROM {table} WHERE {where} LIMIT 3", values)
    return rows[0] if len(rows) == 1 else None, len(rows)


def preview_numilog_link(storage: Storage, observation: FileObservation,
                         *, identity_id: str, credential_id: str,
                         account_id: str, item_id: str, roots: tuple[Path, ...],
                         cancelled=lambda: False) -> NumilogLinkEvidencePreview:
    def stop(reason):
        return NumilogLinkEvidencePreview("blocked", reason)
    if (type(storage) is not Storage or type(observation) is not FileObservation
            or type(roots) is not tuple
            or any(type(value) is not str or not 0 < len(value) <= 1024
                   or any(ord(char) < 32 for char in value)
                   for value in (identity_id, credential_id, account_id, item_id))
            or observation.identity_id != identity_id
            or not callable(cancelled)):
        return stop("invalid_scope")
    if cancelled():
        return stop("cancelled")
    try:
        # The acquisition transaction will use the same storage lock and an
        # inter-process lock. This preview performs no write whatsoever.
        with storage._lock:
            db = storage._connect()
            if db.in_transaction:
                return stop("storage_busy")
            db.execute("BEGIN")
            try:
                if Storage._read_schema_version(db) != CURRENT_SCHEMA_VERSION:
                    return stop("schema_changed")
                pending = db.execute(
                    "SELECT value FROM metadata WHERE key=?",
                    ("identity-directory-migration-v1",),
                ).fetchone()
                if pending and pending[0]:
                    return stop("migration_pending")
                # Credential binding is a separate prerequisite from the
                # entitlement row. An unbound account is not purchase proof.
                source, n = _single(
                    db, "provider_connections",
                    "provider_id=? AND credential_id=? AND identity_id=? AND enabled=1",
                    ("numilog", credential_id, identity_id),
                )
                if n != 1 or source is None:
                    return stop("source_unavailable")
                rights = _rows(db,
                    "SELECT entitlement_id, identity_id, provider_id, credential_id, "
                    "account_id, item_id, state, entitlement_type, loan_id, "
                    "expires_at, metadata_json FROM entitlements "
                    "WHERE provider_id=? AND credential_id=? AND item_id=? LIMIT 3",
                    ("numilog", credential_id, item_id),
                )
                if len(rights) != 1:
                    return stop("right_ambiguous_or_missing")
                right = rights[0]
                if (right["identity_id"] != identity_id
                        or right["account_id"] != account_id
                        or right["state"] != "active"
                        or right["entitlement_type"] != "owned"
                        or right["loan_id"] or right["expires_at"]):
                    return stop("right_not_attested")
                raw = right["metadata_json"]
                if type(raw) is not str or len(raw) > 65536:
                    return stop("right_not_attested")
                metadata = json.loads(raw)
                if (type(metadata) is not dict
                        or metadata.get("source") != "numilog.authenticated_library"):
                    return stop("right_not_attested")
                acquisitions = _rows(db,
                    "SELECT entitlement_id, identity_id, provider_id, credential_id, "
                    "account_id, item_id, status, artifact_id "
                    "FROM acquisition_ledger "
                    "WHERE entitlement_id=? OR (provider_id=? AND credential_id=? AND item_id=?) "
                    "LIMIT 3",
                    (right["entitlement_id"], "numilog", credential_id, item_id),
                )
                if len(acquisitions) != 1:
                    return stop("acquisition_ambiguous_or_missing")
                receipt = acquisitions[0]
                if (receipt["entitlement_id"] != right["entitlement_id"]
                        or receipt["identity_id"] != identity_id
                        or receipt["provider_id"] != "numilog"
                        or receipt["credential_id"] != credential_id
                        or receipt["account_id"] != account_id
                        or receipt["item_id"] != item_id
                        or receipt["status"] != "complete"
                        or type(receipt["artifact_id"]) is not str):
                    return stop("acquisition_conflict")
                artifact, n = _single(
                    db, "artifacts", "artifact_id=?", (receipt["artifact_id"],),
                )
                if n != 1 or artifact is None:
                    return stop("artifact_unavailable")
                if (type(observation.sha256) is not str
                        or artifact["sha256"] != observation.sha256
                        or artifact["size"] != observation.size
                        or artifact["media_format"] != "epub"):
                    return stop("artifact_mismatch")
                path, owner = observation.path, identity_id
                placements = _rows(db,
                    "SELECT identity_id, provider_id, source_item_id, artifact_id "
                    "FROM placements WHERE path=? OR "
                    "(identity_id=? AND provider_id=? AND source_item_id=?) LIMIT 3",
                    (path, owner, "numilog", item_id),
                )
                # This step only considers *unlinked* files. Existing links,
                # even matching ones, require a separate idempotence review.
                if placements:
                    return stop("placement_conflict")
                checked = revalidate_observed_file(
                    observation, roots, identity_id=identity_id, cancelled=cancelled,
                )
                if not checked.verified:
                    return stop("file_" + checked.reason)
                if cancelled():
                    return stop("cancelled")
                return NumilogLinkEvidencePreview("reviewable", "fresh_proofs_require_confirmation")
            finally:
                # Never COMMIT a read-only preflight, even on success.
                db.rollback()
    except (OSError, sqlite3.Error, TypeError, ValueError, KeyError,
            RecursionError, UnicodeError):
        return stop("evidence_unavailable")
