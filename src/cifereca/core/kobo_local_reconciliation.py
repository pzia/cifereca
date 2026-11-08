"""Guarded, file-only Kobo observation and already-canonical relink.

The former W4 positive reconciliation engine is retired from production.
Only historical tests retain that engine and its mutable SQLite helpers.
Local observations here create no rights, delivery or canonical Placement.
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

from ..library_files import safe_library_component
from ..locking import InterProcessLock, LockBusyError
from ..paths import acquisition_lock_file, config_file, database_file
from ..identities import Identity
from ..storage import Storage, CURRENT_SCHEMA_VERSION
from .kobo_backup import _kobo_preview_paths
from .kobo_local_audit import _read_hash_within


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
    # Reject malformed offline requests before touching Config, SQLite or
    # private file paths. These two safe entry points have different exact
    # contracts; silently forwarding extra/omitted kwargs to a downstream
    # Python function exposes TypeError rather than a stable refusal.
    # In particular, a caller cannot inject a context_current callback
    # or use the legacy undo operation on the file-only route. The canonical
    # path accepts only the separately validated canonical journal digest.
    provided = set(options)
    if file_only:
        required = {
            "identity_id", "credential_id", "account_id", "item_id",
            "expected_sha256", "expected_size", "path",
        }
        allowed = required | {"confirmation", "cancelled", "on_committed"}
    else:
        required = {
            "identity_id", "credential_id", "account_id", "edition_id",
            "right_id", "branch_id", "placement_id", "path",
        }
        allowed = required | {"confirmation", "reference_kind", "undo_operation"}
    _require(required <= provided <= allowed, "invalid_request")
    if canonical_relink and "undo_operation" in options:
        operation = options["undo_operation"]
        _require(
            type(operation) is str and len(operation) == 64
            and all(char in "0123456789abcdef" for char in operation),
            "invalid_request",
        )
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
