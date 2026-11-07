"""Confirmed, offline restoration of an already attested Kobo file link (#1345).

No rights are created or modified. An owned entitlement AND the old backup SHA,
size, revision and account must agree. Files are only hashed, never moved,
deleted, parsed or decrypted. The existing read-only audit/checklist is not an
execution plan: this command needs explicit private targets and a fresh token.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
from types import SimpleNamespace
import uuid

from ..library_files import safe_library_component
from ..locking import InterProcessLock, LockBusyError
from ..paths import acquisition_lock_file, config_file, database_file
from ..identities import Identity
from ..storage import Storage, CURRENT_SCHEMA_VERSION
from .kobo_backup import _kobo_preview_paths
from .kobo_local_audit import _read_hash_within

_JOURNAL = "core.kobo.local-reconciliation."
# A separate append-only file observation is deliberately NOT a right or a
# completed Core acquisition receipt. Its historical stat cannot prove current
# file presence after undo, a rename, account rebind, or fresh provider Sync.
_FILE_EVIDENCE_JOURNAL = "core.kobo.local-file-evidence."
_FILE_EVIDENCE_SCHEMA = "kobo.offline-file-observation/1"
_SCHEMA = "kobo.local.reconciliation.receipt/1"
_KEYS = {
    "backup_items": ("provider_id", "credential_id", "item_id"),
    "acquisition_ledger": ("acquisition_id",),
    "placements": ("placement_id",),
    "catalog_entries": ("catalog_entry_id",),
}
_CHANGES = {
    "backup_items": {"local_path", "status", "error", "attention_code", "updated_at"},
    "acquisition_ledger": {"artifact_id", "status", "error_code", "error_message", "attention_code", "updated_at"},
    "placements": {"artifact_id", "provider_id", "source_item_id", "state", "last_seen_at", "updated_at"},
    "catalog_entries": {"artifact_id", "placement_id", "provider_id", "item_id", "local_state", "metadata_json", "updated_at"},
}


class ReconciliationRejected(ValueError):
    """A fixed public reason, never a provider exception or private target."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _require(condition, code):
    if not condition:
        raise ReconciliationRejected(code)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _token(value):
    return hashlib.sha256(("kobo-confirmed-link/1\0" + _json(value)).encode()).hexdigest()


def _rows(db, table, where="1", args=()):
    # All table/where fragments are internal constants; values are parameters.
    cursor = db.execute(f"SELECT * FROM {table} WHERE {where}", args)
    columns = [column[0] for column in cursor.description]
    values = cursor.fetchmany(4097)
    _require(len(values) <= 4096, "evidence_budget_exhausted")
    return [dict(zip(columns, row)) for row in values]


@contextmanager
def _transaction(storage, write, completion, on_committed=None):
    with storage._lock:
        db = storage._connect()
        _require(not db.in_transaction, "storage_busy")
        previous = db.execute("PRAGMA synchronous").fetchone()[0]
        if write:
            db.execute("PRAGMA synchronous=FULL")
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                _require(Storage._read_schema_version(db) == CURRENT_SCHEMA_VERSION, "schema_upgrade_required")
                pending = db.execute("SELECT value FROM metadata WHERE key='identity-directory-migration-v1'").fetchone()
                _require(not pending or not pending[0], "directory_migration_pending")
                yield db
                db.commit()
                completion["committed"] = True
                # Allow CoreUI to advance the private snapshot guard only
                # after a successful write, while still under Storage's lock.
                if write and callable(on_committed):
                    try:
                        on_committed()
                    except Exception:
                        # A committed write must never become a false failure.
                        pass
            except BaseException:
                db.rollback()
                raise
        finally:
            if write:
                try:
                    db.execute(f"PRAGMA synchronous={int(previous)}")
                except sqlite3.Error:
                    # Connection cleanup must never turn a durable commit
                    # into a false "no changes" error receipt.
                    pass


def _roots(service, identity):
    _require(type(identity) is str and 0 < len(identity) <= 1024
             and not any(char in identity for char in ("\0", "\n", "\r")), "unknown_identity")
    profile = service.identity_by_id(identity)
    _require(profile is not None, "unknown_identity")
    library, target = _kobo_preview_paths(service, identity)
    folder = safe_library_component(profile.directory_name or identity, "default")
    _require(not any(other.identity_id != identity
                     and safe_library_component(other.directory_name or other.identity_id, "default") == folder
                     for other in getattr(service, "identities", ())), "ambiguous_identity_directory")
    return (target, library / folder)


def _within(path, roots):
    if type(path) is not str or not path or "\0" in path:
        return False
    value = Path(path)
    if not value.is_absolute() or ".." in value.parts:
        return False
    value = Path(os.path.abspath(value))
    return any(value != root and value.is_relative_to(root) for root in roots)


def _file(path, roots, sha, size):
    _require(hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"), "platform_not_supported")
    _require(_within(path, roots), "file_outside_identity")
    result = _read_hash_within(path, roots)
    _require(result is not None, "file_unverified")
    _require(result[:2] == (sha, size), "file_hash_mismatch")
    info = os.lstat(path)
    _require(stat.S_ISREG(info.st_mode) and info.st_size == size, "file_changed")
    return (result, info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)


def _unique_catalogue_fields(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ReconciliationRejected("ambiguous_catalogue_metadata")
        value[key] = item
    return value


def _metadata(row):
    raw = row.get("metadata_json", "{}")
    _require(type(raw) is str and len(raw) <= 65536, "invalid_catalogue_metadata")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_catalogue_fields)
    except (ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, ReconciliationRejected):
            raise
        raise ReconciliationRejected("invalid_catalogue_metadata") from None
    _require(type(value) is dict, "invalid_catalogue_metadata")
    return value


def _snapshot(db, storage, credential, item, path, sha):
    backup = _rows(db, "backup_items", "provider_id='kobo' AND credential_id=? AND item_id=?", (credential, item))
    _require(len(backup) == 1, "backup_evidence_missing")
    backup = backup[0]
    # #3196 W4: this offline 0.7 repair must not become a second writer
    # for a SourceReference already admitted by the canonical Core. The
    # legacy key omits the borrower and branch, so even an account-mismatched
    # typed source is a veto, never a row to relink by SHA.
    if db.execute(
        "SELECT 1 FROM source_references WHERE provider_id='kobo' "
        "AND credential_id=? AND edition_id=? LIMIT 1",
        (credential, item),
    ).fetchone():
        raise ReconciliationRejected("canonical_source_requires_core_recovery")
    placement_id = storage._stable_id("placement", path)
    placements = _rows(db, "placements", "path=? OR placement_id=?", (path, placement_id))
    _require(len(placements) <= 1, "ambiguous_placement")
    placement = placements[0] if placements else None
    # OriginLinks are scoped to Artifact + identity, not to a placement ID.
    # This path may already support a different canonical edition even when
    # the legacy credential/item has no typed SourceReference of its own.
    if placement is not None and db.execute(
        "SELECT 1 FROM origin_links WHERE artifact_id=? AND identity_id=? LIMIT 1",
        (placement["artifact_id"], placement["identity_id"]),
    ).fetchone():
        raise ReconciliationRejected("canonical_placement_requires_core_recovery")
    placement_id = placement["placement_id"] if placement else placement_id
    catalogue = _rows(db, "catalog_entries", "1 LIMIT 4097")
    _require(len(catalogue) <= 4096, "catalogue_budget_exhausted")
    matches = []
    for row in catalogue:
        raw = row.get("metadata_json")
        # Every catalogue row is a potential historical owner of this
        # path. Missing, malformed or oversized JSON can conceal such an
        # association: treating it as {} would permit a duplicate relink.
        _require(type(raw) is str and len(raw) <= 65536,
                 "invalid_catalogue_metadata")
        try:
            metadata = json.loads(
                raw, object_pairs_hook=_unique_catalogue_fields,
            )
        except ReconciliationRejected:
            # Duplicate keys, even nested, cannot be interpreted last-wins.
            raise
        except (TypeError, ValueError, RecursionError):
            raise ReconciliationRejected("invalid_catalogue_metadata") from None
        _require(type(metadata) is dict, "invalid_catalogue_metadata")
        candidate = metadata.get("path")
        same_path = (type(candidate) is str and "\0" not in candidate
                     and os.path.isabs(candidate) and os.path.abspath(candidate) == path)
        if row.get("placement_id") == placement_id or same_path:
            matches.append(row)
    _require(len(matches) <= 1, "ambiguous_catalogue_links")
    catalogue_id = storage._stable_id("catalog", placement_id)
    if not matches:
        _require(not any(row["catalog_entry_id"] == catalogue_id for row in catalogue), "catalogue_key_conflict")
    acquisitions = _rows(db, "acquisition_ledger", "provider_id='kobo' AND credential_id=? AND item_id=?", (credential, item))
    _require(len(acquisitions) == 1, "acquisition_evidence_missing")
    # A canonical intake checkpoint can precede SourceReference publication.
    # Never overwrite its status/artifact as a *legacy* confirmed repair:
    # only the canonical recovery engine may replay that operation. Decode
    # the JSON so escaped schema markers cannot evade this guard.
    raw_stage = acquisitions[0].get("metadata_json")
    if type(raw_stage) is str and len(raw_stage) <= 65536:
        def unique_fields(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ReconciliationRejected("canonical_stage_ambiguous")
                result[key] = value
            return result
        try:
            stage = json.loads(raw_stage, object_pairs_hook=unique_fields)
        except (ValueError, TypeError, RecursionError):
            raise ReconciliationRejected("canonical_stage_ambiguous") from None
        if (type(stage) is dict
                and stage.get("schema") == "canonical.completed-delivery.v1"):
            raise ReconciliationRejected("canonical_stage_requires_core_recovery")
        # This reversible 0.7 W4 repair is permitted to update only the
        # *synthetic legacy* acquisition row. A canonical/independently
        # produced generic ledger lacking this marker must go through the
        # Core's own recovery, even before SourceReference publication.
        # legacy_status identifies projection provenance, NOT a right.
        _require(
            type(stage) is dict and set(stage) == {"legacy_status"}
            and type(stage["legacy_status"]) is str,
            "legacy_acquisition_provenance_missing",
        )
    else:
        raise ReconciliationRejected("legacy_acquisition_provenance_missing")
    artifacts = _rows(db, "artifacts", "artifact_id=?", ("sha256:" + sha,))
    _require(len(artifacts) == 1, "artifact_evidence_missing")
    return {
        "rows": {"backup_items": backup, "acquisition_ledger": acquisitions[0],
                 "placements": placement, "catalog_entries": matches[0] if matches else None},
        "watch": {
            "connections": _rows(db, "provider_connections", "provider_id='kobo' AND credential_id=?", (credential,)),
            "entitlements": _rows(db, "entitlements", "provider_id='kobo' AND credential_id=? AND item_id=? ORDER BY entitlement_id", (credential, item)),
            "artifact": artifacts[0],
        },
        "placement_id": placement_id,
        "catalogue_id": matches[0]["catalog_entry_id"] if matches else catalogue_id,
    }


def _validate(service, snapshot, intent):
    rows, watch = snapshot["rows"], snapshot["watch"]
    connections = watch["connections"]
    _require(len(connections) == 1 and connections[0]["identity_id"] == intent["identity"], "ambiguous_source")
    backup, acquisition = rows["backup_items"], rows["acquisition_ledger"]
    for row in (backup, acquisition):
        _require(row.get("identity_id") == intent["identity"]
                 and row.get("account_id") == intent["account"], "source_scope_mismatch")
        _require(row.get("status") not in {"acquiring", "downloading"}, "acquisition_in_progress")
    _require(backup.get("entitlement_type") == "owned" and not backup.get("loan_id")
             and not backup.get("expires_at"), "owned_right_required")
    _require(type(backup.get("revision_id")) is str and bool(backup["revision_id"]), "revision_unproven")
    _require(type(backup.get("sha256")) is str and backup["sha256"].lower() == intent["sha"] and type(backup.get("size")) is int
             and backup["size"] > 0, "hash_unattested")
    rights = [row for row in watch["entitlements"] if row.get("state") == "active"]
    _require(len(rights) == 1, "active_right_ambiguous_or_missing")
    right = rights[0]
    _require(right.get("identity_id") == intent["identity"]
             and right.get("account_id") == intent["account"]
             and right.get("entitlement_type") == "owned"
             and right.get("revision_id") == backup["revision_id"]
             and not right.get("loan_id") and not right.get("expires_at"), "right_evidence_mismatch")
    artifact_id = "sha256:" + intent["sha"]
    _require(acquisition.get("entitlement_id") == right["entitlement_id"]
             and acquisition.get("artifact_id") in {None, artifact_id}, "acquisition_evidence_mismatch")
    artifact = watch["artifact"]
    _require(artifact.get("sha256") == intent["sha"] and artifact.get("size") == backup["size"]
             and type(artifact.get("media_format")) is str
             and artifact["media_format"].lower() in {"epub", "kepub", "pdf"}, "artifact_evidence_mismatch")
    extension = "epub" if artifact["media_format"].lower() == "kepub" else artifact["media_format"].lower()
    _require(Path(intent["path"]).suffix.lower().lstrip(".") == extension, "format_mismatch")
    roots = _roots(service, intent["identity"])
    _require(list(map(str, roots)) == intent["roots"], "source_configuration_changed")
    _require(not backup.get("local_path") or _within(backup["local_path"], roots), "old_path_unproven")
    for row in (rows["placements"], rows["catalog_entries"]):
        if row is not None:
            _require(row.get("identity_id") == intent["identity"]
                     and row.get("artifact_id") in {None, artifact_id}
                     and row.get("provider_id") in {None, "local", "kobo"}, "local_link_conflict")
            if row.get("provider_id") == "kobo":
                _require(row.get("source_item_id", row.get("item_id")) in {None, intent["item"]}, "product_link_conflict")
    if rows["placements"]:
        _require(rows["placements"]["path"] == intent["path"], "placement_key_conflict")
    if rows["catalog_entries"]:
        _require(rows["catalog_entries"].get("placement_id") in {None, snapshot["placement_id"]}, "local_link_conflict")
        metadata = _metadata(rows["catalog_entries"])
        _require(metadata.get("path") is None or metadata["path"] == intent["path"], "local_link_conflict")
    return _file(intent["path"], roots, intent["sha"], backup["size"])


def _proposed(snapshot, intent, now):
    before = snapshot["rows"]
    artifact = "sha256:" + intent["sha"]
    common = {"identity_id": intent["identity"], "provider_id": "kobo", "artifact_id": artifact}
    placement = dict(before["placements"] or {
        **common, "placement_id": snapshot["placement_id"], "path": intent["path"],
        "metadata_json": "{}", "created_at": now,
    })
    placement.update(artifact_id=artifact, provider_id="kobo", source_item_id=intent["item"],
                     state="present", last_seen_at=now, updated_at=now)
    catalogue = dict(before["catalog_entries"] or {
        **common, "catalog_entry_id": snapshot["catalogue_id"],
        "title": before["backup_items"]["title"], "author": before["backup_items"].get("author"),
        "media_format": snapshot["watch"]["artifact"]["media_format"],
        "metadata_json": "{}", "created_at": now,
    })
    metadata = _metadata(catalogue)
    metadata["path"] = intent["path"]
    catalogue.update(artifact_id=artifact, placement_id=placement["placement_id"], provider_id="kobo",
                     item_id=intent["item"], local_state="local", metadata_json=_json(metadata), updated_at=now)
    return {
        "backup_items": {**before["backup_items"], "local_path": intent["path"], "status": "complete",
                         "error": None, "attention_code": None, "updated_at": now},
        "acquisition_ledger": {**before["acquisition_ledger"], "artifact_id": artifact, "status": "complete",
                               "error_code": None, "error_message": None, "attention_code": None, "updated_at": now},
        "placements": placement, "catalog_entries": catalogue,
    }


def _write_row(db, table, row, previous):
    keys = _KEYS[table]
    if row is None:
        db.execute(f"DELETE FROM {table} WHERE " + " AND ".join(key + "=?" for key in keys), tuple(previous[key] for key in keys))
    else:
        columns = list(row)
        quoted = lambda column: '"' + column.replace('"', '""') + '"'
        db.execute(f"INSERT INTO {table}(" + ",".join(map(quoted, columns)) + ") VALUES(" + ",".join("?" for _ in columns)
                   + ") ON CONFLICT(" + ",".join(keys) + ") DO UPDATE SET "
                   + ",".join(quoted(column) + "=excluded." + quoted(column) for column in columns if column not in keys), tuple(row.values()))


def _operation_id(record):
    # Content address + random nonce: changing any historical row invalidates
    # the opaque ID the user retained, without introducing a new secret file.
    immutable = {key: value for key, value in record.items() if key != "state"}
    return hashlib.sha256(("kobo-reconciliation-operation/1\0" + _json(immutable)).encode()).hexdigest()


def _journal(db, operation):
    value = db.execute("SELECT value FROM metadata WHERE key=?", (_JOURNAL + operation,)).fetchone()
    _require(value is not None and type(value[0]) is str and len(value[0]) <= 262144, "journal_unavailable")
    result = json.loads(value[0])
    _require(isinstance(result, dict) and set(result) == {"version", "state", "nonce", "intent", "before", "after", "watch"}
             and result.get("version") == 1 and result.get("state") in {"applied", "undone"}
             and type(result.get("nonce")) is str and re.fullmatch("[0-9a-f]{32}", result["nonce"])
             and _operation_id(result) == operation, "invalid_journal")
    return result


def _validate_undo(db, record, snapshot, intent):
    _require(record.get("intent") == intent, "journal_scope_mismatch")
    _require(record.get("after") == snapshot["rows"] and record.get("watch") == snapshot["watch"], "state_changed")
    before, after = record.get("before"), record["after"]
    _require(isinstance(before, dict) and set(before) == set(_KEYS), "invalid_journal")
    for table, keys in _KEYS.items():
        old, current = before[table], after[table]
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        _require(isinstance(current, dict) and set(current) == columns, "invalid_journal")
        if old is None:
            _require(table in {"placements", "catalog_entries"}, "invalid_journal")
        else:
            _require(isinstance(old, dict) and set(old) == columns
                     and all(old[key] == current[key] for key in keys)
                     and {key for key in columns if old[key] != current[key]} <= _CHANGES[table], "invalid_journal")
            _require(old.get("identity_id") == intent["identity"], "invalid_journal")
    roots = tuple(Path(value) for value in intent["roots"])
    old_path = before["backup_items"].get("local_path")
    _require(not old_path or _within(old_path, roots), "invalid_journal")
    # Validate historical local provenance as strictly as the new binding.
    historical = {**snapshot, "rows": before, "after": after}
    _validate_historical_links(historical, intent)
    return before


def _validate_historical_links(snapshot, intent):
    for table in ("placements", "catalog_entries"):
        row = snapshot["rows"][table]
        if row is None:
            continue
        _require(row.get("provider_id") in {None, "local", "kobo"}
                 and row.get("artifact_id") in {None, "sha256:" + intent["sha"]}, "invalid_journal")
        if row.get("provider_id") == "kobo":
            _require(row.get("source_item_id", row.get("item_id")) in {None, intent["item"]}, "invalid_journal")
        if table == "catalog_entries":
            old = _metadata(row)
            current = _metadata(snapshot["after"][table])
            _require(row.get("placement_id") in {None, snapshot["placement_id"]}
                     and (old.get("path") is None or old["path"] == intent["path"])
                     and {key: value for key, value in old.items() if key != "path"}
                     == {key: value for key, value in current.items() if key != "path"}, "invalid_journal")


def _record_offline_file_evidence(db, operation, intent, backup, evidence):
    """Keep a durable *file-only* observation for later exact-source review.

    This W4 evidence comes from independently hashed local bytes and a
    manual confirmation. Neither legacy receipt nor content SHA proves Kobo
    purchase, revocation, delivery, or OriginLink. It is append-only; undo
    changes the W4 journal's state but preserves the historical observation.
    """
    observed, device, inode, modified_ns, changed_ns = evidence
    _require(
        observed[:2] == (intent["sha"], backup["size"])
        and observed[2] == intent["path"],
        "file_evidence_changed",
    )
    entry = {
        "schema": _FILE_EVIDENCE_SCHEMA,
        "operation_id": operation,
        "source": {
            "identity_id": intent["identity"], "provider_id": "kobo",
            "credential_id": intent["credential"], "account_id": intent["account"],
            "edition_id": intent["item"], "legacy_revision_id": backup["revision_id"],
        },
        "file": {
            "path": intent["path"], "sha256": observed[0], "size": observed[1],
            "device": device, "inode": inode, "mtime_ns": modified_ns,
            "ctime_ns": changed_ns,
        },
        "observed_at": int(time.time()),
        "authority": "historical_local_file_only",
        "rights_attested": False,
        "canonical_delivery_attested": False,
    }
    # An already existing key means a collision/corrupt journal; do not
    # overwrite another file observation even with a matching SHA.
    db.execute(
        "INSERT INTO metadata(key,value) VALUES(?,?)",
        (_FILE_EVIDENCE_JOURNAL + operation, _json(entry)),
    )


def _receipt(status, *, token=None, operation=None, changed=0):
    return {"schema": _SCHEMA, "ok": True, "status": status, "offline": True,
            "dry_run": status == "preview", "changed": changed,
            "plan_token": token, "operation_id": operation,
            "requires_explicit_confirmation": status == "preview",
            "file_mutations": 0, "rights_mutations": 0, "originals_preserved": True}



# #4859: this independent, append-only route is the W4 replacement for
# historical files WITHOUT independently attested supplier A/B. It never
# invokes the legacy positive _proposed/_write_row path.
_FILE_ONLY_SCHEMA = "core.kobo.local-file-only/1"
_FILE_ONLY_JOURNAL = "core.kobo.local-file-only."


def observe_kobo_local_file_only(
    service, *, identity_id, credential_id, account_id, item_id,
    expected_sha256, expected_size, path, confirmation=None,
    cancelled=lambda: False, on_committed=None,
):
    """Record verified *bytes only*, never an acquisition or ownership claim.

    A caller can preview and explicitly confirm physical evidence for an
    existing, exactly bound Kobo account. Neither the old backup nor a
    synthetic entitlement is read, updated or required. An immutable local
    journal is suitable for a later, independent provider-attested A->B
    review; it is NOT a CompletedDelivery, SourceReference or OriginLink.

    The public receipt deliberately contains no private path, SHA, identity,
    item, account or credential. A changed inode/content, disabled/rebound
    account, old confirmation or conflicting journal fails closed.
    """
    for value in (identity_id, credential_id, item_id):
        _require(type(value) is str and 0 < len(value) <= 1024
                 and all(32 <= ord(char) < 127 or ord(char) >= 160
                         for char in value), "invalid_request")
    # The upstream primary borrower has the exact account ID "". Reject
    # missing/unknown IDs, but do not conflate this valid ID with None or
    # another account: the SQLite connection/borrower join below is exact.
    _require(type(account_id) is str and len(account_id) <= 1024
             and all(32 <= ord(char) < 127 or ord(char) >= 160
                     for char in account_id), "invalid_request")
    _require(type(expected_sha256) is str
             and re.fullmatch("[0-9a-f]{64}", expected_sha256) is not None,
             "invalid_sha256")
    _require(type(expected_size) is int and expected_size > 0,
             "invalid_size")
    _require(type(path) is str and 0 < len(path) <= 4096
             and "\0" not in path and Path(path).is_absolute()
             and ".." not in Path(path).parts, "invalid_path")
    _require(confirmation is None or (
        type(confirmation) is str
        and re.fullmatch("[0-9a-f]{64}", confirmation) is not None
    ), "invalid_confirmation")
    _require(callable(cancelled), "invalid_request")
    _require(not cancelled(), "reconciliation_cancelled")

    write = confirmation is not None
    completion = {}
    receipt = None
    try:
        guard = InterProcessLock(acquisition_lock_file()) if write else nullcontext()
        with guard, _transaction(service.storage, write, completion, on_committed) as db:
            _require(not cancelled(), "reconciliation_cancelled")
            # A live exact connection/borrower is required even for this
            # historical note: never attribute another account's file.
            scope = db.execute(
                "SELECT connection_id,identity_id,enabled FROM provider_connections "
                "WHERE provider_id='kobo' AND credential_id=? LIMIT 2",
                (credential_id,),
            ).fetchall()
            _require(
                len(scope) == 1 and scope[0][1:] == (identity_id, 1),
                "source_scope_unavailable",
            )
            borrowers = db.execute(
                "SELECT provider_account_id,updated_at FROM provider_accounts "
                "WHERE connection_id=? AND identity_id=? AND provider_id='kobo' "
                "AND credential_id=? AND account_id=? LIMIT 2",
                (scope[0][0], identity_id, credential_id, account_id),
            ).fetchall()
            _require(len(borrowers) == 1, "borrower_scope_unavailable")
            roots = _roots(service, identity_id)
            target = os.path.abspath(path)
            evidence = _file(target, roots, expected_sha256, expected_size)
            # The journal key reserves the exact claimed coordinates + path.
            # Reobserving changed inode/stat with the same SHA is NOT a
            # re-authorization: a conflicting evidence record is refused.
            claimed = {
                "identity_id": identity_id, "provider_id": "kobo",
                "credential_id": credential_id, "account_id": account_id,
                "edition_id": item_id, "path": target,
            }
            key = _FILE_ONLY_JOURNAL + hashlib.sha256(
                _json(claimed).encode("utf-8"),
            ).hexdigest()
            record = {
                "schema": _FILE_ONLY_SCHEMA,
                "claimed_scope": claimed,
                "connection_id": scope[0][0],
                "borrower_record_id": borrowers[0][0],
                "borrower_record_updated_at": borrowers[0][1],
                "file": {
                    "sha256": expected_sha256, "size": expected_size,
                    "device": evidence[1], "inode": evidence[2],
                    "mtime_ns": evidence[3], "ctime_ns": evidence[4],
                },
                "authority": "historical_local_bytes_only",
                "rights_attested": False,
                "canonical_delivery_attested": False,
            }
            serialized = _json(record)
            _require(len(serialized) <= 65536, "evidence_budget_exhausted")
            existing = db.execute(
                "SELECT value FROM metadata WHERE key=?", (key,),
            ).fetchone()
            _require(existing is None or existing[0] == serialized,
                     "file_evidence_conflict")
            token = hashlib.sha256(
                ("kobo-local-file-only-confirm/1\0" + serialized).encode("utf-8"),
            ).hexdigest()
            response = lambda status, plan=None: {
                "schema": _FILE_ONLY_SCHEMA,
                "status": status, "ok": True,
                "plan_token": plan, "changed": int(status == "observed"),
                "requires_provider_attestation": True,
                "rights_attested": False, "canonical_delivery_attested": False,
                "rights_mutations": 0, "file_mutations": 0,
                "legacy_backup_mutations": 0,
            }
            if existing:
                return response("already_observed")
            if not write:
                return response("preview", token)
            _require(confirmation == token, "preview_changed")
            _require(not cancelled(), "reconciliation_cancelled")
            # Exact file and borrower evidence are both rechecked at the
            # commit boundary. A stale preview cannot publish stale bytes.
            _require(evidence == _file(target, _roots(service, identity_id),
                                       expected_sha256, expected_size),
                     "file_changed")
            db.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?)",
                (key, serialized),
            )
            receipt = response("observed")
            return receipt
    except LockBusyError:
        raise ReconciliationRejected("acquisition_busy") from None
    except (OSError, sqlite3.Error):
        if completion.get("committed") and receipt is not None:
            return receipt
        raise ReconciliationRejected("local_storage_unavailable") from None
    except (TypeError, KeyError, ValueError) as exc:
        if isinstance(exc, ReconciliationRejected):
            raise
        raise ReconciliationRejected("invalid_local_evidence") from None


def reconcile_kobo_local(service, *, credential_id, account_id, item_id, expected_sha256,
                         path=None, confirmation=None, undo_operation=None, identity_id=None,
                         cancelled=lambda: False, on_committed=None):
    """Retired W4 entry: historical receipts never grant positive Core B."""
    # Public direct callers must not reach SQL, filesystem or the old writer
    # for preview/confirm/undo, even with a formerly valid plan token.
    raise ReconciliationRejected("legacy_reconciliation_retired")




def reconcile_existing_profile(*, config_path=None, database_path=None, **options):
    """CLI entry without Core/plugin startup, cache purge or migration recovery.

    Even Config.load() chmods existing files; read its persisted JSON directly.
    Require the current existing schema instead of migrating during a preview.
    Roots are reread from disk on validation, so another config save invalidates
    the plan if it changes the selected identity's directory or library root.
    """
    # P0 #4859 W4/D5: the historical default created positive ledger and
    # Placement rows from v0.7 evidence. All production callers are retired:
    # never open Config, SQLite or a file for legacy preview/confirm/undo.
    # Only two explicit, independently fenced safe operators remain.
    file_only = options.pop("file_only", False)
    canonical_relink = options.pop("canonical_relink", False)
    _require(type(file_only) is bool and type(canonical_relink) is bool
             and not (file_only and canonical_relink), "invalid_request")
    _require(file_only or canonical_relink, "legacy_reconciliation_retired")
    config_path = Path(config_path) if config_path is not None else config_file(create=False)
    database_path = Path(database_path) if database_path is not None else database_file(create=False)

    def raw_config():
        with config_path.open("r", encoding="utf-8") as stream:
            text = stream.read(4 * 1024 * 1024 + 1)
        _require(len(text) <= 4 * 1024 * 1024, "invalid_profile")
        data = json.loads(text)
        _require(isinstance(data, dict), "invalid_profile")
        return data

    def get(key, default=None):
        value = raw_config()
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def identity(identity_id):
        values = raw_config().get("identities")
        _require(isinstance(values, list) and len(values) <= 4096, "invalid_profile")
        matching = [row for row in values if isinstance(row, dict) and row.get("id") == identity_id]
        _require(len(matching) == 1, "unknown_identity")
        folder = matching[0].get("directory_name")
        _require(type(folder) is str and bool(folder) and folder not in {".", ".."}
                 and safe_library_component(folder, "default") == folder, "identity_directory_unproven")
        _require(all(isinstance(row, dict) and type(row.get("directory_name")) is str
                     and bool(row["directory_name"]) and row["directory_name"] not in {".", ".."}
                     and safe_library_component(row["directory_name"], "default") == row["directory_name"]
                     for row in values), "identity_directory_unproven")
        _require(sum(row["directory_name"] == folder for row in values) == 1, "ambiguous_identity_directory")
        return Identity(identity_id, str(matching[0].get("label") or identity_id), directory_name=folder)

    connection = None
    try:
        # mode=rw/ro never creates a missing database. No schema/init/repair.
        mode = "rw" if options.get("confirmation") is not None else "ro"
        connection = sqlite3.connect(database_path.absolute().as_uri() + "?mode=" + mode, uri=True, timeout=10)
        _require(Storage._read_schema_version(connection) == CURRENT_SCHEMA_VERSION, "schema_upgrade_required")
        storage = SimpleNamespace(path=database_path, _lock=threading.Lock(),
                                  _connect=lambda: connection, _stable_id=Storage._stable_id)
        service = SimpleNamespace(storage=storage, config=SimpleNamespace(get=get), identity_by_id=identity)
        if canonical_relink:
            from .kobo_canonical_relink import relink_verified_canonical_kobo
            # This offline path deliberately reads Config/SQLite, not any
            # connected provider. The CURRENT on-disk profile mapping must
            # match the exact identity's canonical SQLite directory.
            selected = identity(options.get("identity_id"))
            folder = connection.execute(
                "SELECT directory_name FROM identities WHERE identity_id=?",
                (selected.identity_id,),
            ).fetchall()
            _require(len(folder) == 1 and folder[0][0] == selected.directory_name,
                     "identity_directory_unproven")
            root = get("library.directory", "~/EBooks")
            _require(type(root) is str and bool(root.strip()),
                     "identity_directory_unproven")
            root = Path(os.path.expandvars(os.path.expanduser(root.strip())))
            if not root.is_absolute():
                root = Path.home() / root
            path = options.pop("path", None)
            _require(type(path) is str and bool(path), "invalid_path")

            def profile_current():
                # Raw Config reread only; no Storage call inside the writer's
                # BEGIN IMMEDIATE or nonrecursive storage._lock.
                try:
                    check = identity(options["identity_id"])
                    current_root = get("library.directory", "~/EBooks")
                    if type(current_root) is not str or not current_root.strip():
                        return False
                    current_root = Path(os.path.expandvars(
                        os.path.expanduser(current_root.strip()),
                    ))
                    if not current_root.is_absolute():
                        current_root = Path.home() / current_root
                    return (
                        check.directory_name == selected.directory_name
                        and current_root == root
                    )
                except Exception:
                    return False

            return relink_verified_canonical_kobo(
                storage, library_root=root, new_path=Path(path),
                context_current=profile_current, **options,
            )
        return (
            observe_kobo_local_file_only(service, **options)
            if file_only else reconcile_kobo_local(service, **options)
        )
    except (OSError, sqlite3.Error, json.JSONDecodeError, UnicodeError):
        raise ReconciliationRejected("local_profile_unavailable") from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass  # Preserve the already decided transaction receipt.
