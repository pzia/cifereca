"""Explicit, offline and reversible-at-SQL-transaction-boundary Numilog placement (#2714).

Core-private API only. A title, ISBN, matching bytes, cached preflight, plugin
or arbitrary right ID is never sufficient authority. Do not expose this API
through a broker or GUI without a separate explicit-confirmation review.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

from ..locking import InterProcessLock, LockBusyError
from ..paths import acquisition_lock_file
from ..storage import Storage, CURRENT_SCHEMA_VERSION
from .library_audit_roots import configured_identity_roots
from .library_fresh_file_check import revalidate_observed_file
from .library_inventory import FileObservation

_SCHEMA = "numilog.local.attachment/1"


class ManualAttachmentRejected(ValueError):
    """Only a fixed code, never private receipt, path or exception details."""


def _require(condition, code):
    if not condition:
        raise ManualAttachmentRejected(code)


def _rows(db, table, where, parameters):
    # Call sites contain only literal table/column/WHERE expressions.
    cursor = db.execute("SELECT * FROM " + table + " WHERE " + where
                        + " LIMIT 3", parameters)
    columns = [entry[0] for entry in cursor.description]
    return [dict(zip(columns, entry)) for entry in cursor.fetchall()]


def _scope(service, *, identity_id, credential_id, account_id):
    _require(type(identity_id) is str and 0 < len(identity_id) <= 1024
             and type(credential_id) is str and 0 < len(credential_id) <= 1024
             and type(account_id) is str and 0 < len(account_id) <= 1024,
             "invalid_scope")
    _require(all(not any(c in value for c in ("\\0", "\\n", "\\r"))
                 for value in (identity_id, credential_id, account_id)), "invalid_scope")
    current = getattr(service, "current_identity", None)
    _require(current is not None and current.identity_id == identity_id,
             "identity_not_active")
    identities = getattr(service, "identities", None)
    accounts = getattr(service, "accounts", None)
    _require(type(accounts) is list and type(identities) is list, "scope_unavailable")
    exact = [row for row in accounts
             if getattr(row, "provider_id", None) == "numilog"
             and getattr(row, "credential_id", None) == credential_id]
    _require(len(exact) == 1 and exact[0].identity_id == identity_id
             and exact[0].unique_id == account_id, "account_scope_changed")
    config = service.config
    storage = service.storage
    roots = configured_identity_roots(config, identity_id)[identity_id]
    _require(roots, "invalid_roots")
    epoch = service._account_context_epoch
    data = config.data

    def valid():
        try:
            active = service.current_identity
            matches = [row for row in service.accounts
                       if getattr(row, "provider_id", None) == "numilog"
                       and getattr(row, "credential_id", None) == credential_id]
            return (service.config is config and config.data is data
                    and service.storage is storage
                    and service.accounts is accounts and service.identities is identities
                    and service._account_context_epoch == epoch
                    and active is current and active.identity_id == identity_id
                    and len(matches) == 1 and matches[0] is exact[0]
                    and matches[0].identity_id == identity_id
                    and matches[0].unique_id == account_id
                    and configured_identity_roots(config, identity_id)[identity_id] == roots)
        except Exception:
            return False

    return roots, valid


@contextmanager
def _db_transaction(storage, *, write):
    """Same writer exclusion/schema guard as existing Kobo local repair."""
    with storage._lock:
        db = storage._connect()
        _require(not db.in_transaction, "storage_busy")
        previous = db.execute("PRAGMA synchronous").fetchone()[0]
        if write:
            db.execute("PRAGMA synchronous=FULL")
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                _require(Storage._read_schema_version(db) == CURRENT_SCHEMA_VERSION,
                         "schema_upgrade_required")
                pending = db.execute(
                    "SELECT value FROM metadata WHERE key='identity-directory-migration-v1'"
                ).fetchone()
                _require(not pending or not pending[0], "directory_migration_pending")
                yield db
                if write:
                    db.commit()
                else:
                    db.rollback()
            except BaseException:
                db.rollback()
                raise
        finally:
            if write:
                try:
                    db.execute("PRAGMA synchronous=" + str(int(previous)))
                except sqlite3.Error:
                    pass


def _evidence(db, *, identity_id, credential_id, account_id, item_id,
              observed: FileObservation):
    source = _rows(db, "provider_connections",
                   "provider_id='numilog' AND credential_id=?", (credential_id,))
    _require(len(source) == 1 and source[0]["identity_id"] == identity_id
             and source[0].get("enabled") == 1, "source_not_attested")
    rights = _rows(db, "entitlements",
                   "provider_id='numilog' AND credential_id=? AND item_id=?",
                   (credential_id, item_id))
    _require(len(rights) == 1, "ambiguous_or_missing_right")
    right = rights[0]
    _require(all(right.get(key) == value for key, value in (
        ("provider_id", "numilog"), ("credential_id", credential_id),
        ("account_id", account_id), ("identity_id", identity_id),
        ("item_id", item_id), ("state", "active"), ("entitlement_type", "owned"),
    )) and not right.get("loan_id") and not right.get("expires_at"),
             "owned_right_not_attested")
    raw = right.get("metadata_json")
    try:
        metadata = json.loads(raw) if type(raw) is str and len(raw) <= 65536 else None
    except (ValueError, RecursionError):
        metadata = None
    _require(type(metadata) is dict
             and metadata.get("source") == "numilog.authenticated_library",
             "purchase_provenance_unattested")
    ledger = _rows(db, "acquisition_ledger",
                   "entitlement_id=? OR (provider_id='numilog' AND credential_id=? AND item_id=?)",
                   (right["entitlement_id"], credential_id, item_id))
    _require(len(ledger) == 1, "acquisition_ambiguous_or_missing")
    acquisition = ledger[0]
    _require(all(acquisition.get(key) == value for key, value in (
        ("entitlement_id", right["entitlement_id"]), ("provider_id", "numilog"),
        ("credential_id", credential_id), ("account_id", account_id),
        ("identity_id", identity_id), ("item_id", item_id),
        ("status", "complete"),
    )) and type(acquisition.get("artifact_id")) is str
        and bool(acquisition["artifact_id"]), "acquisition_unproven")
    artifacts = _rows(db, "artifacts", "artifact_id=?",
                      (acquisition["artifact_id"],))
    _require(len(artifacts) == 1
             and artifacts[0].get("sha256") == observed.sha256
             and type(artifacts[0].get("size")) is int
             and artifacts[0]["size"] == observed.size
             and artifacts[0].get("media_format") == "epub"
             and acquisition["artifact_id"] == "sha256:" + observed.sha256,
             "artifact_file_mismatch")
    target_id = Storage._stable_id("placement", observed.path)
    placements = _rows(
        db, "placements",
        "path=? OR placement_id=? OR "
        "(provider_id='numilog' AND identity_id=? AND source_item_id=?)",
        (observed.path, target_id, identity_id, item_id),
    )
    _require(len(placements) <= 1, "placement_ambiguous")
    current = placements[0] if placements else None
    if current is not None:
        _require(
            current.get("path") == observed.path
            and current.get("placement_id") == target_id
            and current.get("identity_id") == identity_id
            and current.get("provider_id") == "numilog"
            and current.get("source_item_id") == item_id
            and current.get("artifact_id") == acquisition["artifact_id"]
            and current.get("state") == "present", "placement_conflict",
        )
    # Never hijack an old catalog reference to the proposed deterministic ID,
    # even if the placement row has not yet been published.
    catalog = _rows(db, "catalog_entries", "placement_id=?", (target_id,))
    if not current:
        _require(not catalog, "catalogue_conflict")
    else:
        _require(all(
            row.get("identity_id") == identity_id
            and row.get("provider_id") == "numilog"
            and row.get("item_id") == item_id
            for row in catalog
        ), "catalogue_conflict")
    return {
        "source": source[0], "right": right, "acquisition": acquisition,
        "artifact": artifacts[0], "placement": current,
        "catalogue": catalog,
        "placement_id": target_id,
    }


def _token(scope, snapshot, observation):
    # Entire private evidence is HASHED, never returned in the public receipt.
    serialized = json.dumps({
        "schema": _SCHEMA, "scope": scope, "rows": snapshot,
        "file": {
            "path": observation.path, "size": observation.size,
            "sha256": observation.sha256,
            "fingerprint": observation.fingerprint,
        },
    }, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(
        ("numilog-explicit-local-link/1\\0" + serialized).encode("utf-8")
    ).hexdigest()


def _receipt(status, *, token=None, changed=0):
    return {
        "schema": _SCHEMA, "status": status, "ok": True,
        "offline": True, "read_only": status == "preview",
        "dry_run": status == "preview", "changed": changed,
        "plan_token": token if status == "preview" else None,
        "requires_explicit_confirmation": status == "preview",
        "file_mutations": 0, "rights_mutations": 0,
        "originals_preserved": True,
    }


def reconcile_numilog_manual(
    service, *, observation: FileObservation, identity_id: str,
    credential_id: str, account_id: str, item_id: str,
    confirmation: str | None = None, cancelled=lambda: False,
):
    """Preview or confirm ONE already-owned, precisely receipted Numilog EPUB.

    This internal Core entry point accepts NO ISBN/title or untrusted plugin
    candidate. Even a previously 'eligible' private UI preflight is ignored:
    every account, ledger row, root, pathname and digest is revalidated.
    """
    _require(type(item_id) is str and 0 < len(item_id) <= 1024
             and not any(c in item_id for c in ("\\0", "\\n", "\\r")),
             "invalid_item")
    _require(type(observation) is FileObservation, "invalid_file_observation")
    _require(type(confirmation) is type(None)
             or (type(confirmation) is str
                 and re.fullmatch(r"[0-9a-f]{64}", confirmation) is not None),
             "invalid_confirmation")
    _require(callable(cancelled), "invalid_cancellation")
    roots, valid = _scope(
        service, identity_id=identity_id,
        credential_id=credential_id, account_id=account_id,
    )
    _require(observation.identity_id == identity_id, "source_scope_changed")
    write = confirmation is not None
    # No user file or right is touched before exact active-account validation.
    scope = {
        "identity": identity_id, "credential": credential_id,
        "account": account_id, "item": item_id,
        "roots": list(map(str, roots)), "database": str(service.storage.path),
        "epoch": service._account_context_epoch,
    }
    try:
        lock = InterProcessLock(acquisition_lock_file()) if write else nullcontext()
        with lock, _db_transaction(service.storage, write=write) as db:
            _require(valid() and not cancelled(), "source_scope_changed")
            snapshot = _evidence(
                db, identity_id=identity_id, credential_id=credential_id,
                account_id=account_id, item_id=item_id, observed=observation,
            )
            fresh = revalidate_observed_file(
                observation, roots, identity_id=identity_id,
                cancelled=lambda: cancelled() or not valid(),
            )
            _require(fresh.verified, "file_revalidation_failed")
            _require(valid() and not cancelled(), "source_scope_changed")
            if snapshot["placement"] is not None:
                return _receipt("already_linked")
            token = _token(scope, snapshot, observation)
            if not write:
                return _receipt("preview", token=token)
            _require(confirmation == token, "preview_changed")
            now = int(time.time())
            db.execute(
                "INSERT INTO placements "
                "(placement_id,artifact_id,path,identity_id,provider_id,"
                "source_item_id,state,last_seen_at,metadata_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,'{}',?,?)",
                (snapshot["placement_id"], snapshot["acquisition"]["artifact_id"],
                 observation.path, identity_id, "numilog", item_id,
                 "present", now, now, now),
            )
            _require(_evidence(
                db, identity_id=identity_id, credential_id=credential_id,
                account_id=account_id, item_id=item_id, observed=observation,
            )["placement"] is not None, "post_write_evidence_changed")
            # Cooperative writer lock + SQLite BEGIN IMMEDIATE keep DB stable.
            # The file is still external: rehash and reopen nofollow at commit.
            fresh_again = revalidate_observed_file(
                observation, roots, identity_id=identity_id,
                cancelled=lambda: cancelled() or not valid(),
            )
            _require(fresh_again.verified and valid() and not cancelled(),
                     "file_changed_before_commit")
            return _receipt("applied", changed=1)
    except LockBusyError:
        raise ManualAttachmentRejected("acquisition_busy") from None
    except (OSError, sqlite3.Error):
        raise ManualAttachmentRejected("local_storage_unavailable") from None
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        if type(exc) is ManualAttachmentRejected:
            raise
        raise ManualAttachmentRejected("invalid_local_evidence") from None
