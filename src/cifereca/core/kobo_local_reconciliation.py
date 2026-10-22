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


def _metadata(row):
    raw = row.get("metadata_json", "{}")
    _require(type(raw) is str and len(raw) <= 65536, "invalid_catalogue_metadata")
    value = json.loads(raw)
    _require(isinstance(value, dict), "invalid_catalogue_metadata")
    return value


def _snapshot(db, storage, credential, item, path, sha):
    backup = _rows(db, "backup_items", "provider_id='kobo' AND credential_id=? AND item_id=?", (credential, item))
    _require(len(backup) == 1, "backup_evidence_missing")
    backup = backup[0]
    placement_id = storage._stable_id("placement", path)
    placements = _rows(db, "placements", "path=? OR placement_id=?", (path, placement_id))
    _require(len(placements) <= 1, "ambiguous_placement")
    placement = placements[0] if placements else None
    placement_id = placement["placement_id"] if placement else placement_id
    catalogue = _rows(db, "catalog_entries", "1 LIMIT 4097")
    _require(len(catalogue) <= 4096, "catalogue_budget_exhausted")
    matches = []
    for row in catalogue:
        raw = row.get("metadata_json")
        try:
            metadata = json.loads(raw) if type(raw) is str and len(raw) <= 65536 else {}
        except (TypeError, ValueError):
            metadata = {}
        candidate = metadata.get("path") if isinstance(metadata, dict) else None
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


def _receipt(status, *, token=None, operation=None, changed=0):
    return {"schema": _SCHEMA, "ok": True, "status": status, "offline": True,
            "dry_run": status == "preview", "changed": changed,
            "plan_token": token, "operation_id": operation,
            "requires_explicit_confirmation": status == "preview",
            "file_mutations": 0, "rights_mutations": 0, "originals_preserved": True}


def reconcile_kobo_local(service, *, credential_id, account_id, item_id, expected_sha256,
                         path=None, confirmation=None, undo_operation=None, identity_id=None,
                         cancelled=lambda: False, on_committed=None):
    """Preview or explicitly confirm ONE reversible file-link transaction.

    A token binds exact DB evidence, file bytes/stat and configured roots.
    The common acquisition lock serializes writes with existing app writers;
    uncooperative external filesystem edits cannot be locked by SQLite.
    """
    for value in (credential_id, account_id, item_id):
        _require(type(value) is str and 0 < len(value) <= 1024
                 and not any(char in value for char in ("\0", "\n", "\r")), "invalid_request")
    _require(type(expected_sha256) is str and re.fullmatch("[0-9a-f]{64}", expected_sha256), "invalid_sha256")
    _require(confirmation is None or (type(confirmation) is str and re.fullmatch("[0-9a-f]{64}", confirmation)), "invalid_confirmation")
    _require(undo_operation is None or (type(undo_operation) is str and re.fullmatch("[0-9a-f]{64}", undo_operation)), "invalid_operation")
    _require(callable(cancelled), "invalid_request")
    _require(not cancelled(), "reconciliation_cancelled")
    write = confirmation is not None
    completion = {}
    committed_receipt = None
    try:
        lock = InterProcessLock(acquisition_lock_file()) if write else nullcontext()
        with lock, _transaction(service.storage, write, completion, on_committed) as db:
            _require(not cancelled(), "reconciliation_cancelled")
            record = _journal(db, undo_operation) if undo_operation else None
            if record:
                stored = record.get("intent")
                _require(isinstance(stored, dict) and stored.get("credential") == credential_id
                         and stored.get("account") == account_id and stored.get("item") == item_id
                         and stored.get("sha") == expected_sha256, "journal_scope_mismatch")
                _require(path is None or path == stored.get("path"), "journal_scope_mismatch")
                path = stored.get("path")
            _require(type(path) is str and len(path) <= 4096 and "\0" not in path
                     and os.path.isabs(path) and ".." not in Path(path).parts, "invalid_path")
            path = os.path.abspath(path)
            sources = _rows(db, "provider_connections", "provider_id='kobo' AND credential_id=?", (credential_id,))
            _require(len(sources) == 1, "ambiguous_source")
            owner = sources[0]["identity_id"]
            _require(identity_id is None or identity_id == owner, "source_scope_mismatch")
            intent = {"credential": credential_id, "account": account_id, "item": item_id,
                      "identity": owner, "sha": expected_sha256, "path": path,
                      "database": str(service.storage.path.absolute()),
                      "roots": list(map(str, _roots(service, owner)))}
            snapshot = _snapshot(db, service.storage, credential_id, item_id, path, expected_sha256)
            evidence = _validate(service, snapshot, intent)
            before = snapshot["rows"]
            if record and record["state"] == "undone":
                _require(record.get("intent") == intent, "journal_scope_mismatch")
                _require(record.get("before") == before and record.get("watch") == snapshot["watch"], "journal_state_conflict")
                return _receipt("already_undone", operation=undo_operation)
            if record:
                desired = _validate_undo(db, record, snapshot, intent)
            else:
                desired = _proposed(snapshot, intent, int(time.time()))
                # Timestamps and harmless JSON serialization alone are not a
                # new reconciliation. Repeating a confirmed action is a no-op.
                equivalent = all(before[table] is not None and all(
                    (key in {"updated_at", "last_seen_at"}) or
                    (json.loads(before[table][key]) == json.loads(value) if key == "metadata_json" else before[table].get(key) == value)
                    for key, value in desired[table].items()) for table in _KEYS)
                if equivalent:
                    return _receipt("already_linked")
            journal = ({**record, "state": "undone"} if record else {
                "version": 1, "state": "applied", "nonce": uuid.uuid4().hex, "intent": intent,
                "before": before, "after": desired, "watch": snapshot["watch"],
            })
            journal_text = _json(journal)
            _require(len(journal_text) <= 262144, "journal_budget_exhausted")
            token = _token({"intent": intent, "snapshot": snapshot, "file": evidence,
                            "undo": undo_operation, "journal": record})
            if not write:
                return _receipt("preview", token=token, operation=undo_operation)
            _require(confirmation == token, "preview_changed")
            for table in _KEYS:
                _write_row(db, table, desired[table], before[table])
            # SQL triggers must not leave half a repair.
            after = _snapshot(db, service.storage, credential_id, item_id, path, expected_sha256)
            _require(after["rows"] == desired and after["watch"] == snapshot["watch"], "state_changed")
            operation = undo_operation or _operation_id(journal)
            db.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (_JOURNAL + operation, journal_text))
            # Recheck bytes and roots last, immediately before SQLite commit.
            _require(intent["roots"] == list(map(str, _roots(service, owner))), "source_configuration_changed")
            _require(evidence == _file(path, _roots(service, owner), expected_sha256,
                                      snapshot["rows"]["backup_items"]["size"]), "file_changed")
            _require(not cancelled(), "reconciliation_cancelled")
            committed_receipt = _receipt("undone" if record else "applied", operation=operation, changed=1)
            return committed_receipt
    except LockBusyError:
        raise ReconciliationRejected("acquisition_busy") from None
    except (OSError, sqlite3.Error):
        if completion.get("committed") and committed_receipt is not None:
            return committed_receipt
        raise ReconciliationRejected("local_storage_unavailable") from None
    except (TypeError, KeyError, json.JSONDecodeError, RecursionError, UnicodeError):
        raise ReconciliationRejected("invalid_local_evidence") from None


def reconcile_existing_profile(*, config_path=None, database_path=None, **options):
    """CLI entry without Core/plugin startup, cache purge or migration recovery.

    Even Config.load() chmods existing files; read its persisted JSON directly.
    Require the current existing schema instead of migrating during a preview.
    Roots are reread from disk on validation, so another config save invalidates
    the plan if it changes the selected identity's directory or library root.
    """
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
        return reconcile_kobo_local(service, **options)
    except (OSError, sqlite3.Error, json.JSONDecodeError, UnicodeError):
        raise ReconciliationRejected("local_profile_unavailable") from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass  # Preserve the already decided transaction receipt.
