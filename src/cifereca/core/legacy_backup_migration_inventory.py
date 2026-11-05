"""P0 #3196: bounded read-only inventory for retiring the Kobo 0.7 writer.

The legacy projection is NOT a source of purchase rights, presence or
publication proofs. This audit gives operators an exact-scope, aggregate-only
inventory before a future independently admitted migration. It NEVER opens
files, writes SQLite or promotes legacy observations into canonical rights.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .canonical_publication import _stable

_MAX_ROWS = 1000
_MAX_CHECKPOINT = 65536
_SHA = set("0123456789abcdef")

_KEYS = (
    "scanned", "status_complete", "status_acquiring",
    "status_reconcile_required", "status_skipped_archived", "status_other",
    "revision_unrecorded", "item_id_unqualified",
    "account_unbound", "account_conflicting",
    "ledger_absent", "ledger_scope_conflicting", "ledger_ambiguous",
    "path_unrecorded", "digest_unrecorded", "path_shared",
    "receipt_shape_candidate", "receipt_unproven",
)


def _blank(status: str) -> dict[str, int | str]:
    return {"status": status, **{key: 0 for key in _KEYS}}


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous_checkpoint")
        result[key] = value
    return result


def _receipt_shape(row: tuple, ledger: tuple) -> bool:
    """A syntactic candidate ONLY, not validated provenance/authority.

    The next migration must additionally revalidate the exact staged SourceKey
    and published OriginLink, current account, all path collisions, file
    SHA/inode and profile directory under the common acquisition lock.
    """
    owner, credential, account, item, path, digest = row
    operation, stage_status, stage_owner, stage_account, artifact, raw = ledger
    if (stage_status != "complete" or stage_owner != owner
            or stage_account != account or artifact != "sha256:" + digest
            or type(raw) is not str or len(raw) > _MAX_CHECKPOINT):
        return False
    try:
        document = json.loads(raw, object_pairs_hook=_unique_fields)
    except (ValueError, TypeError, RecursionError):
        return False
    if (type(document) is not dict
            or set(document) != {
                "schema", "source", "offer_id", "path", "sha256",
                "receipt", "adept_receipt",
            }
            or document["schema"] != "canonical.completed-delivery.v1"
            or document["path"] != path or document["sha256"] != digest
            or type(document["source"]) is not list
            or len(document["source"]) != 8
            or document["source"][:4] != [owner, "kobo", credential, account]
            or document["source"][5] != item
            or any(type(x) is not str for x in document["source"])
            or any(type(document[k]) is not str or not document[k]
                   for k in ("offer_id", "receipt"))
            or type(document["adept_receipt"]) is not str
            or not 0 < len(document["offer_id"]) <= 1024
            or not 0 < len(document["receipt"]) <= 1024
            or len(document["adept_receipt"]) > 1024):
        return False
    # An unrelated completed journal operation must not impersonate the
    # exact stage of the receipt. Still NO proof of OriginLink or bytes.
    return operation == _stable("canonical-intake", *document["source"], document["offer_id"])


def inventory_legacy_kobo_backups(
    storage: Any, *, identity_id: str, max_rows: int = _MAX_ROWS,
) -> dict[str, int | str]:
    """Aggregate one profile's legacy Kobo rows without exposing private data.

    status='complete' means only that this *inventory* is exhaustive within
    its bounds, NEVER that any row may migrate, suppress a GET or be removed.
    A NULL/unscoped legacy owner anywhere makes the Kobo snapshot ambiguous.
    An exceeded budget or SQL failure suppresses all partial counts.
    """
    if (type(identity_id) is not str
            or not 0 < len(identity_id) <= 1024
            or any(ord(c) < 32 or ord(c) == 127 for c in identity_id)
            or type(max_rows) is not int or not 1 <= max_rows <= _MAX_ROWS):
        return _blank("invalid_scope")
    try:
        if storage.schema_version() < 14:
            return _blank("schema_unavailable")
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            if db.execute(
                "SELECT 1 FROM backup_items WHERE provider_id='kobo' "
                "AND (identity_id IS NULL OR identity_id='') LIMIT 1"
            ).fetchone():
                return _blank("scope_unverified")
            records = db.execute(
                "SELECT identity_id,credential_id,account_id,item_id,"
                "revision_id,status,local_path,sha256,size "
                "FROM backup_items WHERE provider_id='kobo' AND identity_id=? "
                "ORDER BY credential_id,item_id LIMIT ?",
                (identity_id, max_rows + 1),
            ).fetchall()
            if len(records) > max_rows:
                return _blank("budget_exceeded")

            report = _blank("complete")
            for (owner, credential, account, item, revision, status, path,
                 digest, size) in records:
                report["scanned"] += 1
                # Receipt shape is advisory; this SQL-only inventory NEVER
                # attests SourceKey/OriginLink or file bytes. Every row still
                # requires independent receipt/provenance verification.
                report["receipt_unproven"] += 1
                if status == "complete":
                    report["status_complete"] += 1
                elif status in ("acquiring", "pending_acquisition"):
                    report["status_acquiring"] += 1
                elif status == "reconcile_required":
                    report["status_reconcile_required"] += 1
                elif status == "skipped_archived":
                    report["status_skipped_archived"] += 1
                else:
                    report["status_other"] += 1

                if type(revision) is not str or not revision:
                    report["revision_unrecorded"] += 1
                # The v0.7 SQLite primary key accepts identifiers a typed
                # Kobo manifest cannot uniquely address. The newer legacy
                # prune preserves those rows for review; the migration
                # inventory must count them and never describe their ledger
                # as a candidate for canonical publication/recovery.
                item_id_qualified = (
                    type(item) is str and bool(item.strip())
                    and item == item.strip() and len(item) <= 1024
                    and not any(c in item for c in "\x00\r\n")
                )
                if not item_id_qualified:
                    report["item_id_unqualified"] += 1
                if type(path) is not str or not path:
                    report["path_unrecorded"] += 1
                if (type(digest) is not str or len(digest) != 64
                        or any(char not in _SHA for char in digest)
                        or type(size) is not int or size <= 0):
                    report["digest_unrecorded"] += 1

                valid_source = (type(owner) is str and owner == identity_id
                                and type(credential) is str and bool(credential)
                                and item_id_qualified
                                and type(account) is str)
                if not valid_source:
                    report["account_unbound"] += 1
                    continue
                # Only a uniquely enabled connection and the EXACT borrower
                # count as a current binding; borrower '' is valid if stored.
                connections = db.execute(
                    "SELECT connection_id,identity_id,enabled "
                    "FROM provider_connections "
                    "WHERE provider_id='kobo' AND credential_id=? LIMIT 3",
                    (credential,),
                ).fetchall()
                account_bound = False
                if (len(connections) != 1
                        or connections[0][1:] != (owner, 1)):
                    report["account_unbound"] += 1
                    if len(connections) > 1 or any(
                        binding[1] != owner for binding in connections
                    ):
                        report["account_conflicting"] += 1
                else:
                    borrowers = db.execute(
                        "SELECT 1 FROM provider_accounts WHERE "
                        "connection_id=? AND identity_id=? "
                        "AND provider_id='kobo' AND credential_id=? "
                        "AND account_id=? LIMIT 2",
                        (connections[0][0], owner, credential, account),
                    ).fetchall()
                    account_bound = len(borrowers) == 1
                    if not account_bound:
                        report["account_unbound"] += 1

                path_shared = False
                if path:
                    # The backup PK omits owner/account; a reused physical
                    # path anywhere is never a unique scope for migration.
                    collisions = db.execute(
                        "SELECT 1 FROM backup_items WHERE local_path=? "
                        "AND NOT (provider_id='kobo' AND credential_id=? "
                        "AND item_id=?) LIMIT 1",
                        (path, credential, item),
                    ).fetchone()
                    if collisions:
                        report["path_shared"] += 1
                        path_shared = True

                ledger = db.execute(
                    "SELECT acquisition_id,status,identity_id,account_id,"
                    "artifact_id,metadata_json "
                    "FROM acquisition_ledger WHERE provider_id='kobo' "
                    "AND credential_id=? AND item_id=? LIMIT ?",
                    (credential, item, max_rows + 1),
                ).fetchall()
                if len(ledger) > max_rows:
                    # Never interpret a truncated source history as absence.
                    return _blank("budget_exceeded")
                if not ledger:
                    report["ledger_absent"] += 1
                if any(identity != owner or acc != account
                       for _, _, identity, acc, _, _ in ledger):
                    report["ledger_scope_conflicting"] += 1
                if len(ledger) > 1:
                    report["ledger_ambiguous"] += 1

                # Even a matching receipt shape does NOT prove bytes,
                # OriginLink, stage ID, rights or permission to mutate.
                candidate = (
                    len(ledger) == 1
                    and account_bound and not path_shared
                    and type(path) is str and bool(path)
                    and type(digest) is str and len(digest) == 64
                    and all(char in _SHA for char in digest)
                    and type(size) is int and size > 0
                    and not any(identity != owner or acc != account
                                for _, _, identity, acc, _, _ in ledger)
                    and _receipt_shape(
                        (owner, credential, account, item, path, digest),
                        ledger[0],
                    )
                )
                if candidate:
                    report["receipt_shape_candidate"] += 1
            return report
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError,
            RecursionError):
        return _blank("unavailable")


__all__ = ["inventory_legacy_kobo_backups"]
