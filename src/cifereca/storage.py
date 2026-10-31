from __future__ import annotations

import hashlib
import logging
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .paths import database_file

_TRACE_LOG = logging.getLogger("cifereca")


# Schema history is deliberately explicit from 0.7.10 onward.  Databases created
# before that release had no schema_version metadata; they are treated as v0 and
# migrated forward transactionally.
CURRENT_SCHEMA_VERSION = 14

BASE_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS cache (
  namespace TEXT NOT NULL,
  cache_key TEXT NOT NULL,
  payload TEXT NOT NULL,
  stored_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  PRIMARY KEY(namespace, cache_key)
);
CREATE TABLE IF NOT EXISTS metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backup_items (
  provider_id TEXT NOT NULL,
  credential_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  revision_id TEXT,
  title TEXT NOT NULL,
  author TEXT,
  format TEXT,
  local_path TEXT,
  status TEXT NOT NULL,
  size INTEGER,
  sha256 TEXT,
  updated_at INTEGER NOT NULL,
  error TEXT,
  PRIMARY KEY(provider_id, credential_id, item_id)
);
"""


class Storage:
    def __init__(self, path: Path | None = None):
        self.path = path or database_file()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connections_lock = threading.Lock()
        self._connections: set[sqlite3.Connection] = set()
        self._thread_local = threading.local()
        started = time.monotonic()
        _TRACE_LOG.info("TRACE storage init path=%s", self.path)
        with self._connect() as db:
            _TRACE_LOG.info("TRACE storage base-schema begin")
            db.executescript(BASE_SCHEMA)
            _TRACE_LOG.info("TRACE storage base-schema end dt=%.3fs", time.monotonic()-started)
            self._migrate(db)
            _TRACE_LOG.info("TRACE storage migrate end schema=%s dt=%.3fs", self._read_schema_version(db), time.monotonic()-started)

    @staticmethod
    def _column_names(db: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()}

    def metadata_get(self, key: str, default: str | None = None) -> str | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key=?", (str(key),)).fetchone()
        return str(row[0]) if row is not None else default

    def metadata_json_put_item(self, key: str, item: str, value: Any, *, only_if_missing: bool = False) -> None:
        """Atomic dictionary update, retaining observations from other workers."""
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT value FROM metadata WHERE key=?",(key,)).fetchone()
            try:values=json.loads(row[0]) if row else {}
            except (ValueError,TypeError):values={}
            if not isinstance(values,dict):values={}
            if only_if_missing and item in values:return
            values[item]=value
            db.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,json.dumps(values)))

    @staticmethod
    def _loan_renewal_checkpoint_key_valid(key: object) -> bool:
        # Key is a digest, never a borrower, title, token or signed URL.
        return (
            type(key) is str and key.startswith("loan.renew.v1:")
            and len(key) == len("loan.renew.v1:") + 64
            and all(character in "0123456789abcdef"
                    for character in key[len("loan.renew.v1:"):])
        )

    def loan_renewal_state(self, key: str) -> dict[str, Any] | None:
        """Read a bounded durable at-most-once renewal receipt (never a right)."""
        if not self._loan_renewal_checkpoint_key_valid(key):
            return None
        raw = self.metadata_get(key)
        if type(raw) is not str or not 0 < len(raw) <= 4096:
            return None
        try:
            row = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if (type(row) is not dict or row.get("schema") != 1
                or row.get("operation") != "loan.renew"
                or type(row.get("status")) is not str
                or row.get("status") not in {
                    "request_sent", "submitted", "verification_unavailable",
                    "blocked",
                }
                or type(row.get("operation_id")) is not str
                or not row["operation_id"]):
            return None
        return row

    def loan_renewal_claim_once(
        self, key: str, operation_id: str,
    ) -> tuple[bool, dict[str, Any]]:
        """Reserve a renewal *before* the POST, atomically across processes.

        An existing key is never automatically reopened, even if the first
        network operation timed out or its metadata became malformed.
        Another renewal needs a separately attested upstream cycle; a
        repeated click on the same loan cannot replay the mutation.
        """
        from datetime import datetime, timezone
        if (not self._loan_renewal_checkpoint_key_valid(key)
                or type(operation_id) is not str or not operation_id
                or len(operation_id) > 128):
            raise ValueError("loan_renewal_claim_invalid")
        row = {
            "schema": 1, "operation": "loan.renew",
            "status": "request_sent", "operation_id": operation_id,
            "request_accepted": False, "verification": "not_checked",
            "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO NOTHING",
                (key, json.dumps(row, sort_keys=True, separators=(",", ":"))),
            )
            if cursor.rowcount == 1:
                return True, row
            # Never retry over corrupt checkpoint bytes or a prior dispatch.
            return False, {"status": "verification_unavailable"}

    def loan_renewal_finish(
        self, key: str, operation_id: str, *,
        status: str, accepted: bool = False,
    ) -> dict[str, Any] | None:
        """CAS after the POST; stale workers cannot alter later operations."""
        if (not self._loan_renewal_checkpoint_key_valid(key)
                or type(operation_id) is not str
                or type(status) is not str
                or status not in {
                    "submitted", "verification_unavailable", "blocked",
                }
                or type(accepted) is not bool):
            raise ValueError("loan_renewal_transition_invalid")
        from datetime import datetime, timezone
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM metadata WHERE key=?", (key,),
            ).fetchone()
            if row is None or type(row[0]) is not str or len(row[0]) > 4096:
                return None
            try:
                previous = json.loads(row[0])
            except (TypeError, ValueError):
                return None
            if (type(previous) is not dict
                    or previous.get("schema") != 1
                    or previous.get("operation") != "loan.renew"
                    or previous.get("operation_id") != operation_id
                    or previous.get("status") != "request_sent"):
                return None
            previous["status"] = status
            previous["request_accepted"] = accepted
            previous["verification"] = "not_checked" if accepted else "unavailable"
            previous["checked_at"] = datetime.now(timezone.utc).isoformat(
                timespec="seconds",
            )
            db.execute(
                "UPDATE metadata SET value=? WHERE key=?",
                (json.dumps(previous, sort_keys=True, separators=(",", ":")), key),
            )
            return previous

    def loan_return_state(self, key: str) -> dict[str, Any] | None:
        """Read a bounded operation checkpoint; invalid bytes never authorize POST."""
        raw = self.metadata_get(key)
        if not isinstance(raw, str) or not raw or len(raw) > 4096:
            return None
        try:
            row = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return row if isinstance(row, dict) and row.get("schema") == 1 else None

    def loan_return_claim(self, key: str, operation_id: str, *, reopen: bool = False) -> tuple[bool, dict[str, Any]]:
        """Atomically reserve the POST even across app instances/processes.

        Reopen is allowed only after explicit verification found the same loan
        still active. A timed-out POST is NOT safe to replay automatically.
        """
        import json as _json
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            raw = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            try:
                previous = _json.loads(raw[0]) if raw else None
            except (TypeError, ValueError):
                previous = {"schema": 1, "status": "verification_unavailable"}
            if previous is not None and (
                not reopen or not isinstance(previous, dict)
                or previous.get("status") != "still_active"
            ):
                return False, previous if isinstance(previous, dict) else {}
            row = {
                "schema": 1, "status": "request_sent",
                "operation_id": operation_id, "requested_at": now,
                "request_accepted": False, "verification": "not_checked",
                "checked_at": None,
            }
            db.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, _json.dumps(row, sort_keys=True, separators=(",", ":"))),
            )
            return True, row

    def loan_return_transition(self, key: str, operation_id: str, *,
                               status: str, verification: str,
                               accepted: bool | None = None,
                               error_type: str | None = None,
                               adobe_verified: bool | None = None) -> dict[str, Any] | None:
        """CAS update: an obsolete worker may not overwrite a newer attempt."""
        import json as _json
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            raw = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            if not raw:
                return None
            try:
                row = _json.loads(raw[0])
            except (TypeError, ValueError):
                return None
            if not isinstance(row, dict) or row.get("operation_id") != operation_id:
                return None
            row.update(status=status, verification=verification, checked_at=now)
            if accepted is not None:
                row["request_accepted"] = bool(accepted)
            if error_type is not None:
                row["error_type"] = error_type[:90]
            if adobe_verified is not None:
                row["adobe_verified"] = bool(adobe_verified)
            db.execute(
                "UPDATE metadata SET value=? WHERE key=?",
                (_json.dumps(row, sort_keys=True, separators=(",", ":")), key),
            )
            return row

    def metadata_set(self, key: str, value: Any) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(key), str(value)),
            )

    def metadata_set_with_revision(self, key: str, value: str, revision_key: str) -> None:
        """Atomically replace a source acquisition toggle and its CAS epoch.

        Every ON/OFF transition advances the epoch, including ON->OFF->ON.
        Publishing the two fields in one SQLite transaction avoids exposing
        a new generation with an old value to concurrent Core readers.
        """
        from secrets import token_hex

        if (type(key) is not str or type(revision_key) is not str
                or type(value) is not str or not key or not revision_key
                or key == revision_key or value not in ("0", "1")):
            raise ValueError("invalid_acquisition_policy_revision")
        generation = token_hex(16)
        with self._lock, self._connect() as db:
            for field, state in ((revision_key, generation), (key, value)):
                db.execute(
                    "INSERT INTO metadata(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (field, state),
                )

    @staticmethod
    def _read_schema_version(db: sqlite3.Connection) -> int:
        row = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        if row is None:
            return 0
        try:
            return max(0, int(row[0]))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _write_schema_version(db: sqlite3.Connection, version: int) -> None:
        db.execute(
            "INSERT INTO metadata(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(int(version)),),
        )

    @classmethod
    def _migrate(cls, db: sqlite3.Connection) -> None:
        """Forward-only, transaction-safe local schema migrations.

        v1: historical Kobo ``job_id`` compatibility column.
        v2: provenance/lifecycle columns for the historical Kobo compatibility table.
        v3: generic entitlements, acquisition ledger, immutable artifacts,
            placements and local catalog. ``backup_items`` remains a compatibility
            projection for the 0.7 Kobo sync API.
        v4: first-class identities, provider connections/accounts, ADE profile
            bindings and device bindings; generic provenance gains identity_id.
        v5: strict Kobo/ADE cardinalities and cover_url projection.
        v6: stable human filesystem directory per identity, decoupled from identity_id.
        v7: opening synchronization runs and durable per-identity status summaries.
        v8: normalize the Paris primary account key and merge duplicate loan/ledger rows.
        v9: device target directories and desired/observed artifact placements.
        v10: persisted artifact variants and preferred variants.
        v11: structured action events for dashboard statistics.
        v12: performance indexes for catalogue/library projections.
        v13: explicit, pinned Paris borrower-to-ADEPT identity/profile grants.
        v14: empty, additive canonical SourceReference and OriginLink storage.
            Legacy entitlements/backup_items remain untouched; their migration
            needs exact source/branch proofs and independent guarded writers.
        """
        version = cls._read_schema_version(db)
        _TRACE_LOG.info("TRACE storage migrate begin from=%s target=%s", version, CURRENT_SCHEMA_VERSION)
        if version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"Base cifereca trop récente (schéma {version}, support {CURRENT_SCHEMA_VERSION})"
            )

        if version < 1:
            cols = cls._column_names(db, "backup_items")
            if "job_id" not in cols:
                db.execute("ALTER TABLE backup_items ADD COLUMN job_id TEXT")
            cls._write_schema_version(db, 1)
            version = 1

        if version < 2:
            cols = cls._column_names(db, "backup_items")
            additions = {
                "account_id": "TEXT",
                "entitlement_type": "TEXT NOT NULL DEFAULT 'owned'",
                "loan_id": "TEXT",
                "expires_at": "TEXT",
                "acquisition_strategy": "TEXT",
                "executor": "TEXT",
                "adept_profile": "TEXT",
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "last_attempt_at": "INTEGER",
                "attention_code": "TEXT",
            }
            for name, ddl in additions.items():
                if name not in cols:
                    db.execute(f"ALTER TABLE backup_items ADD COLUMN {name} {ddl}")
            cls._write_schema_version(db, 2)
            version = 2

        if version < 3:
            # One transaction/savepoint creates the complete generic 0.8 model.
            # No filesystem access occurs during migration: paths from 0.7 are
            # imported as unverified placements and reconciled by a later scan.
            db.execute("SAVEPOINT migration_v3")
            try:
                schema_sql = """
                    CREATE TABLE IF NOT EXISTS entitlements (
                      entitlement_id TEXT PRIMARY KEY,
                      provider_id TEXT NOT NULL,
                      credential_id TEXT NOT NULL,
                      account_id TEXT,
                      item_id TEXT NOT NULL,
                      revision_id TEXT,
                      entitlement_type TEXT NOT NULL,
                      state TEXT NOT NULL,
                      loan_id TEXT,
                      expires_at TEXT,
                      metadata_json TEXT NOT NULL DEFAULT '{}',
                      discovered_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL,
                      identity_id TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_entitlements_source
                      ON entitlements(provider_id, credential_id, item_id);

                    CREATE TABLE IF NOT EXISTS artifacts (
                      artifact_id TEXT PRIMARY KEY,
                      sha256 TEXT NOT NULL UNIQUE,
                      size INTEGER NOT NULL,
                      media_format TEXT NOT NULL,
                      mime_type TEXT,
                      metadata_json TEXT NOT NULL DEFAULT '{}',
                      created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS placements (
                      placement_id TEXT PRIMARY KEY,
                      artifact_id TEXT,
                      path TEXT NOT NULL UNIQUE,
                      identity_id TEXT,
                      provider_id TEXT,
                      source_item_id TEXT,
                      state TEXT NOT NULL,
                      last_seen_at INTEGER,
                      metadata_json TEXT NOT NULL DEFAULT '{}',
                      created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_placements_artifact ON placements(artifact_id);
                    CREATE INDEX IF NOT EXISTS idx_placements_state ON placements(state);

                    CREATE TABLE IF NOT EXISTS acquisition_ledger (
                      acquisition_id TEXT PRIMARY KEY,
                      entitlement_id TEXT,
                      provider_id TEXT NOT NULL,
                      credential_id TEXT NOT NULL,
                      account_id TEXT,
                      item_id TEXT NOT NULL,
                      title TEXT,
                      status TEXT NOT NULL,
                      strategy TEXT,
                      executor TEXT,
                      candidate_id TEXT,
                      artifact_id TEXT,
                      attempt_count INTEGER NOT NULL DEFAULT 0,
                      last_attempt_at INTEGER,
                      error_code TEXT,
                      error_message TEXT,
                      attention_code TEXT,
                      metadata_json TEXT NOT NULL DEFAULT '{}',
                      created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL,
                      identity_id TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_acquisition_status ON acquisition_ledger(status);
                    CREATE INDEX IF NOT EXISTS idx_acquisition_source
                      ON acquisition_ledger(provider_id, credential_id, item_id);

                    CREATE TABLE IF NOT EXISTS catalog_entries (
                      catalog_entry_id TEXT PRIMARY KEY,
                      artifact_id TEXT,
                      placement_id TEXT,
                      title TEXT NOT NULL,
                      author TEXT,
                      media_format TEXT,
                      identity_id TEXT,
                      provider_id TEXT,
                      item_id TEXT,
                      local_state TEXT NOT NULL,
                      metadata_json TEXT NOT NULL DEFAULT '{}',
                      created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_catalog_local_state ON catalog_entries(local_state);
                    CREATE INDEX IF NOT EXISTS idx_catalog_artifact ON catalog_entries(artifact_id);
                    """
                for statement in schema_sql.split(";"):
                    statement = statement.strip()
                    if statement:
                        db.execute(statement)
                now = int(time.time())
                keys = [
                    "provider_id", "credential_id", "item_id", "revision_id", "title", "author",
                    "format", "local_path", "status", "size", "sha256", "updated_at", "error",
                    "account_id", "entitlement_type", "loan_id", "expires_at", "acquisition_strategy",
                    "executor", "attempt_count", "last_attempt_at", "attention_code",
                ]
                rows = db.execute("SELECT " + ",".join(keys) + " FROM backup_items").fetchall()
                for values in rows:
                    row = dict(zip(keys, values))
                    cls._mirror_backup_row(db, row, now=now, migration=True)
                cls._write_schema_version(db, 3)
                db.execute("RELEASE SAVEPOINT migration_v3")
            except Exception:
                db.execute("ROLLBACK TO SAVEPOINT migration_v3")
                db.execute("RELEASE SAVEPOINT migration_v3")
                raise
            version = 3

        if version < 4:
            db.execute("SAVEPOINT migration_v4")
            try:
                # Provenance becomes identity-aware without moving any user file.
                for table in ("backup_items", "entitlements", "acquisition_ledger"):
                    cols = cls._column_names(db, table)
                    if "identity_id" not in cols:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN identity_id TEXT")

                schema_sql = """
                    CREATE TABLE IF NOT EXISTS identities (
                      identity_id TEXT PRIMARY KEY,
                      label TEXT NOT NULL,
                      prefer_original_language INTEGER NOT NULL DEFAULT 0,
                      reading_languages_json TEXT NOT NULL DEFAULT '[]',
                      metadata_json TEXT NOT NULL DEFAULT '{}',
                      created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS provider_connections (
                      connection_id TEXT PRIMARY KEY,
                      identity_id TEXT NOT NULL,
                      provider_id TEXT NOT NULL,
                      credential_id TEXT NOT NULL,
                      label TEXT NOT NULL,
                      enabled INTEGER NOT NULL DEFAULT 1,
                      metadata_json TEXT NOT NULL DEFAULT '{}',
                      created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL,
                      UNIQUE(provider_id, credential_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_provider_connections_identity
                      ON provider_connections(identity_id, provider_id);
                    CREATE TABLE IF NOT EXISTS provider_accounts (
                      provider_account_id TEXT PRIMARY KEY,
                      connection_id TEXT NOT NULL,
                      identity_id TEXT NOT NULL,
                      provider_id TEXT NOT NULL,
                      credential_id TEXT NOT NULL,
                      account_id TEXT NOT NULL,
                      label TEXT NOT NULL,
                      is_primary INTEGER NOT NULL DEFAULT 0,
                      metadata_json TEXT NOT NULL DEFAULT '{}',
                      created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL,
                      UNIQUE(connection_id, account_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_provider_accounts_identity
                      ON provider_accounts(identity_id, provider_id);
                    CREATE TABLE IF NOT EXISTS adept_profile_bindings (
                      scope_type TEXT NOT NULL,
                      scope_id TEXT NOT NULL,
                      profile_id TEXT NOT NULL,
                      created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL,
                      PRIMARY KEY(scope_type, scope_id)
                    );
                    CREATE TABLE IF NOT EXISTS device_bindings (
                      device_id TEXT PRIMARY KEY,
                      identity_id TEXT NOT NULL,
                      label TEXT,
                      enabled INTEGER NOT NULL DEFAULT 1,
                      metadata_json TEXT NOT NULL DEFAULT '{}',
                      created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_device_bindings_identity
                      ON device_bindings(identity_id);
                    CREATE INDEX IF NOT EXISTS idx_entitlements_identity
                      ON entitlements(identity_id, provider_id);
                    CREATE INDEX IF NOT EXISTS idx_acquisition_identity
                      ON acquisition_ledger(identity_id, provider_id);
                """
                for statement in schema_sql.split(";"):
                    statement = statement.strip()
                    if statement:
                        db.execute(statement)
                cls._write_schema_version(db, 4)
                db.execute("RELEASE SAVEPOINT migration_v4")
            except Exception:
                db.execute("ROLLBACK TO SAVEPOINT migration_v4")
                db.execute("RELEASE SAVEPOINT migration_v4")
                raise

        if version < 5:
            db.execute("SAVEPOINT migration_v5")
            try:
                # 0.8.1 cardinality fix: one Kobo Vault connection/account and
                # one ADE identity/profile per Paris identity. Account-scoped ADE
                # bindings from the first 0.8.1 build are obsolete.
                if "cover_url" not in cls._column_names(db, "backup_items"):
                    db.execute("ALTER TABLE backup_items ADD COLUMN cover_url TEXT")
                db.execute("DELETE FROM adept_profile_bindings WHERE scope_type='account'")

                # Early 0.8.1 builds attached every legacy Kobo connection to the
                # first Paris identity.  Preserve every Vault connection by splitting
                # extras into deterministic derived identities instead of aborting the
                # application startup.  Provenance rows that carry credential_id move
                # with the connection; local catalog/placements remain untouched.
                duplicated_identities = db.execute(
                    "SELECT identity_id FROM provider_connections "
                    "WHERE provider_id='kobo' GROUP BY identity_id HAVING COUNT(*)>1"
                ).fetchall()
                for (identity_id,) in duplicated_identities:
                    connections = db.execute(
                        "SELECT connection_id,credential_id,label,created_at FROM provider_connections "
                        "WHERE provider_id='kobo' AND identity_id=? "
                        "ORDER BY created_at,connection_id",
                        (identity_id,),
                    ).fetchall()
                    for connection_id, credential_id, connection_label, _created_at in connections[1:]:
                        suffix = hashlib.sha256(str(credential_id).encode("utf-8", "surrogatepass")).hexdigest()[:8]
                        derived_id = f"{identity_id}-kobo-{suffix}"
                        # Avoid collision with an unrelated pre-existing identity.
                        candidate = derived_id
                        serial = 2
                        while db.execute(
                            "SELECT 1 FROM identities WHERE identity_id=?", (candidate,)
                        ).fetchone():
                            candidate = f"{derived_id}-{serial}"
                            serial += 1
                        derived_id = candidate

                        source = db.execute(
                            "SELECT label,prefer_original_language,reading_languages_json,metadata_json,created_at,updated_at "
                            "FROM identities WHERE identity_id=?",
                            (identity_id,),
                        ).fetchone()
                        now = int(time.time())
                        if source:
                            base_label, prefer_vo, langs_json, metadata_json, created_at, updated_at = source
                        else:
                            base_label, prefer_vo, langs_json, metadata_json, created_at, updated_at = (
                                str(identity_id), 0, '["fr"]', '{}', now, now
                            )
                        label = str(connection_label or f"{base_label} Kobo").strip() or f"{base_label} Kobo"
                        db.execute(
                            "INSERT INTO identities(identity_id,label,prefer_original_language,reading_languages_json,metadata_json,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?)",
                            (derived_id, label, prefer_vo, langs_json, metadata_json, created_at, updated_at),
                        )
                        db.execute(
                            "UPDATE provider_connections SET identity_id=? WHERE connection_id=?",
                            (derived_id, connection_id),
                        )
                        db.execute(
                            "UPDATE provider_accounts SET identity_id=? WHERE connection_id=?",
                            (derived_id, connection_id),
                        )
                        for table in ("backup_items", "entitlements", "acquisition_ledger"):
                            if "identity_id" in cls._column_names(db, table):
                                db.execute(
                                    f"UPDATE {table} SET identity_id=? WHERE provider_id='kobo' AND credential_id=? "
                                    "AND (identity_id=? OR identity_id IS NULL OR identity_id='')",
                                    (derived_id, credential_id, identity_id),
                                )

                # A Vault connection may have projected more than one Kobo account in
                # an earlier build.  Provider-account rows are a cache/projection, so
                # retain the primary row (then oldest as a deterministic fallback).
                duplicated_accounts = db.execute(
                    "SELECT identity_id FROM provider_accounts "
                    "WHERE provider_id='kobo' GROUP BY identity_id HAVING COUNT(*)>1"
                ).fetchall()
                for (identity_id,) in duplicated_accounts:
                    rows = db.execute(
                        "SELECT provider_account_id FROM provider_accounts "
                        "WHERE provider_id='kobo' AND identity_id=? "
                        "ORDER BY is_primary DESC,created_at,provider_account_id",
                        (identity_id,),
                    ).fetchall()
                    for (provider_account_id,) in rows[1:]:
                        db.execute(
                            "DELETE FROM provider_accounts WHERE provider_account_id=?",
                            (provider_account_id,),
                        )

                # A legacy global ADE profile could have been projected onto several
                # identities by an early 0.8.1 build.  Keep the oldest deterministic
                # binding and leave the other identities unbound; no ADE secret is
                # stored or guessed by Paris.
                duplicated_profiles = db.execute(
                    "SELECT profile_id FROM adept_profile_bindings "
                    "WHERE scope_type='identity' GROUP BY profile_id HAVING COUNT(*)>1"
                ).fetchall()
                for (profile_id,) in duplicated_profiles:
                    rows = db.execute(
                        "SELECT scope_id FROM adept_profile_bindings "
                        "WHERE scope_type='identity' AND profile_id=? "
                        "ORDER BY created_at,scope_id",
                        (profile_id,),
                    ).fetchall()
                    for (scope_id,) in rows[1:]:
                        db.execute(
                            "DELETE FROM adept_profile_bindings WHERE scope_type='identity' AND scope_id=?",
                            (scope_id,),
                        )

                db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_kobo_connection_identity "
                    "ON provider_connections(identity_id) WHERE provider_id='kobo'"
                )
                db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_kobo_account_identity "
                    "ON provider_accounts(identity_id) WHERE provider_id='kobo'"
                )
                db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_ade_profile_identity "
                    "ON adept_profile_bindings(profile_id) WHERE scope_type='identity'"
                )
                cls._write_schema_version(db, 5)
                db.execute("RELEASE SAVEPOINT migration_v5")
            except Exception:
                db.execute("ROLLBACK TO SAVEPOINT migration_v5")
                db.execute("RELEASE SAVEPOINT migration_v5")
                raise

        if version < 6:
            db.execute("SAVEPOINT migration_v6")
            try:
                if "directory_name" not in cls._column_names(db, "identities"):
                    db.execute("ALTER TABLE identities ADD COLUMN directory_name TEXT")
                # Bootstrap only. LibraryService will project the configured
                # human directory name (derived from label for legacy config).
                db.execute("UPDATE identities SET directory_name=identity_id WHERE directory_name IS NULL OR TRIM(directory_name)='' ")
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_directory_name ON identities(directory_name)")
                cls._write_schema_version(db, 6)
                db.execute("RELEASE SAVEPOINT migration_v6")
            except Exception:
                db.execute("ROLLBACK TO SAVEPOINT migration_v6")
                db.execute("RELEASE SAVEPOINT migration_v6")
                raise
            version = 6

        if version < 7:
            db.execute("SAVEPOINT migration_v7")
            try:
                schema_sql = """
                    CREATE TABLE IF NOT EXISTS sync_runs (
                      sync_run_id TEXT PRIMARY KEY,
                      identity_id TEXT,
                      scope TEXT NOT NULL,
                      status TEXT NOT NULL,
                      started_at INTEGER NOT NULL,
                      finished_at INTEGER,
                      new_acquisitions INTEGER NOT NULL DEFAULT 0,
                      attention_required INTEGER NOT NULL DEFAULT 0,
                      errors INTEGER NOT NULL DEFAULT 0,
                      detail_json TEXT NOT NULL DEFAULT '{}',
                      created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_sync_runs_identity
                      ON sync_runs(identity_id, started_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_sync_runs_scope
                      ON sync_runs(scope, started_at DESC);
                    """
                for statement in schema_sql.split(";"):
                    statement = statement.strip()
                    if statement:
                        db.execute(statement)
                cls._write_schema_version(db, 7)
                db.execute("RELEASE SAVEPOINT migration_v7")
            except Exception:
                db.execute("ROLLBACK TO SAVEPOINT migration_v7")
                db.execute("RELEASE SAVEPOINT migration_v7")
                raise
            version = 7

        if version < 8:
            db.execute("SAVEPOINT migration_v8")
            try:
                # Paris 0.8.3 made the logical primary-account key explicit as
                # ``primary``. Older rows used NULL/empty, which changed stable
                # entitlement/acquisition ids and could create duplicate ledger
                # rows for the very same loan. Normalize and merge them here.
                db.row_factory = sqlite3.Row
                entitlement_rows = db.execute(
                    "SELECT * FROM entitlements WHERE provider_id='paris' "
                    "AND (account_id IS NULL OR TRIM(account_id)='' OR account_id='primary') "
                    "ORDER BY updated_at DESC, discovered_at DESC"
                ).fetchall()
                groups = {}
                for raw in entitlement_rows:
                    row = dict(raw)
                    row['account_id'] = 'primary'
                    key = (row.get('credential_id') or '', row.get('item_id') or '',
                           row.get('entitlement_type') or '', row.get('loan_id') or '',
                           row.get('identity_id') or '')
                    groups.setdefault(key, []).append(row)
                for rows in groups.values():
                    base = rows[0]
                    canonical_id = cls._entitlement_id_for(base)
                    survivor = next((r for r in rows if r.get('entitlement_id') == canonical_id), rows[0])
                    survivor_id = survivor['entitlement_id']
                    if survivor_id != canonical_id:
                        existing = db.execute("SELECT 1 FROM entitlements WHERE entitlement_id=?", (canonical_id,)).fetchone()
                        if not existing:
                            db.execute("UPDATE entitlements SET entitlement_id=? WHERE entitlement_id=?", (canonical_id, survivor_id))
                            db.execute("UPDATE acquisition_ledger SET entitlement_id=? WHERE entitlement_id=?", (canonical_id, survivor_id))
                            survivor_id = canonical_id
                    db.execute("UPDATE entitlements SET account_id='primary' WHERE entitlement_id=?", (survivor_id,))
                    for dup in rows:
                        dup_id = dup['entitlement_id']
                        if dup_id == survivor_id or dup_id == canonical_id:
                            continue
                        db.execute("UPDATE acquisition_ledger SET entitlement_id=? WHERE entitlement_id=?", (survivor_id, dup_id))
                        db.execute("DELETE FROM entitlements WHERE entitlement_id=?", (dup_id,))

                ledger_rows = db.execute(
                    "SELECT * FROM acquisition_ledger WHERE provider_id='paris' AND strategy='loan_auto' "
                    "AND (account_id IS NULL OR TRIM(account_id)='' OR account_id='primary') "
                    "ORDER BY updated_at DESC, created_at DESC"
                ).fetchall()
                groups = {}
                for raw in ledger_rows:
                    row = dict(raw)
                    row['account_id'] = 'primary'
                    key = (row.get('credential_id') or '', row.get('item_id') or '',
                           row.get('entitlement_id') or '', row.get('identity_id') or '')
                    groups.setdefault(key, []).append(row)
                status_rank = {'complete': 5, 'acquiring': 4, 'retryable_error': 3, 'attention_required': 2, 'pending': 1, 'discovered': 0}
                for rows in groups.values():
                    # Prefer a completed row; otherwise the most recently updated one.
                    rows.sort(key=lambda r: (status_rank.get(str(r.get('status') or ''), -1), int(r.get('updated_at') or 0)), reverse=True)
                    survivor = rows[0]
                    canonical_id = cls._acquisition_id_for(survivor)
                    survivor_id = survivor['acquisition_id']
                    max_attempts = max(int(r.get('attempt_count') or 0) for r in rows)
                    if survivor_id != canonical_id:
                        existing = db.execute("SELECT 1 FROM acquisition_ledger WHERE acquisition_id=?", (canonical_id,)).fetchone()
                        if not existing:
                            db.execute("UPDATE acquisition_ledger SET acquisition_id=? WHERE acquisition_id=?", (canonical_id, survivor_id))
                            survivor_id = canonical_id
                    db.execute(
                        "UPDATE acquisition_ledger SET account_id='primary',attempt_count=? WHERE acquisition_id=?",
                        (max_attempts, survivor_id),
                    )
                    for dup in rows[1:]:
                        dup_id = dup['acquisition_id']
                        if dup_id == survivor_id or dup_id == canonical_id:
                            continue
                        db.execute("DELETE FROM acquisition_ledger WHERE acquisition_id=?", (dup_id,))

                cls._write_schema_version(db, 8)
                db.execute("RELEASE SAVEPOINT migration_v8")
            except Exception:
                db.execute("ROLLBACK TO SAVEPOINT migration_v8")
                db.execute("RELEASE SAVEPOINT migration_v8")
                raise
            finally:
                db.row_factory = None
            version = 8

        if version < 9:
            db.execute("SAVEPOINT migration_v9")
            try:
                if "target_directory" not in cls._column_names(db, "device_bindings"):
                    db.execute("ALTER TABLE device_bindings ADD COLUMN target_directory TEXT NOT NULL DEFAULT 'Books/Paris'")
                schema_sql = """
                    CREATE TABLE IF NOT EXISTS device_placements (
                      device_placement_id TEXT PRIMARY KEY,
                      device_id TEXT NOT NULL,
                      artifact_id TEXT NOT NULL,
                      desired_state TEXT NOT NULL DEFAULT 'keep',
                      observed_state TEXT NOT NULL DEFAULT 'unknown',
                      target_relative_path TEXT,
                      last_observed_at INTEGER,
                      metadata_json TEXT NOT NULL DEFAULT '{}',
                      created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL,
                      UNIQUE(device_id, artifact_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_device_placements_device
                      ON device_placements(device_id, desired_state, observed_state);
                    CREATE INDEX IF NOT EXISTS idx_device_placements_artifact
                      ON device_placements(artifact_id);
                """
                for statement in schema_sql.split(";"):
                    statement = statement.strip()
                    if statement:
                        db.execute(statement)
                cls._write_schema_version(db, 9)
                db.execute("RELEASE SAVEPOINT migration_v9")
            except Exception:
                db.execute("ROLLBACK TO SAVEPOINT migration_v9")
                db.execute("RELEASE SAVEPOINT migration_v9")
                raise
            version = 9

        if version < 10:
            db.execute("SAVEPOINT migration_v10")
            try:
                schema_sql = """
                    CREATE TABLE IF NOT EXISTS artifact_variants (
                      variant_id TEXT PRIMARY KEY,
                      artifact_id TEXT NOT NULL,
                      source_artifact_id TEXT NOT NULL,
                      source_sha256 TEXT NOT NULL,
                      variant_kind TEXT NOT NULL,
                      transformer_plugin_id TEXT NOT NULL,
                      transformer_version TEXT NOT NULL DEFAULT '',
                      profile_plugin_id TEXT,
                      profile_version TEXT,
                      options_fingerprint TEXT NOT NULL DEFAULT '',
                      metadata_json TEXT NOT NULL DEFAULT '{}',
                      created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL,
                      UNIQUE(source_sha256, transformer_plugin_id, profile_plugin_id, options_fingerprint),
                      FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id),
                      FOREIGN KEY(source_artifact_id) REFERENCES artifacts(artifact_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_artifact_variants_source ON artifact_variants(source_artifact_id);
                    CREATE INDEX IF NOT EXISTS idx_artifact_variants_artifact ON artifact_variants(artifact_id);
                """
                for statement in schema_sql.split(";"):
                    statement = statement.strip()
                    if statement:
                        db.execute(statement)
                cls._write_schema_version(db, 10)
                db.execute("RELEASE SAVEPOINT migration_v10")
            except Exception:
                db.execute("ROLLBACK TO SAVEPOINT migration_v10")
                db.execute("RELEASE SAVEPOINT migration_v10")
                raise
            version = 10

        if version < 11:
            db.execute("SAVEPOINT migration_v11")
            try:
                schema_sql = """
                    CREATE TABLE IF NOT EXISTS action_events (
                      event_id TEXT PRIMARY KEY,
                      identity_id TEXT,
                      action_type TEXT NOT NULL,
                      outcome TEXT NOT NULL,
                      provider_id TEXT,
                      plugin_id TEXT,
                      facts_json TEXT NOT NULL DEFAULT '{}',
                      occurred_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_action_events_identity_time
                      ON action_events(identity_id, occurred_at);
                    CREATE INDEX IF NOT EXISTS idx_action_events_type_outcome
                      ON action_events(action_type, outcome, occurred_at);
                """
                for statement in schema_sql.split(";"):
                    statement = statement.strip()
                    if statement:
                        db.execute(statement)
                cls._write_schema_version(db, 11)
                db.execute("RELEASE SAVEPOINT migration_v11")
            except Exception:
                db.execute("ROLLBACK TO SAVEPOINT migration_v11")
                db.execute("RELEASE SAVEPOINT migration_v11")
                raise
            version = 11

        if version < 12:
            db.execute("SAVEPOINT migration_v12")
            try:
                schema_sql = """
                    CREATE INDEX IF NOT EXISTS idx_catalog_identity_state
                      ON catalog_entries(identity_id, local_state);
                    CREATE INDEX IF NOT EXISTS idx_catalog_source
                      ON catalog_entries(identity_id, provider_id, item_id);
                    CREATE INDEX IF NOT EXISTS idx_catalog_placement
                      ON catalog_entries(placement_id);
                    CREATE INDEX IF NOT EXISTS idx_placements_identity_state
                      ON placements(identity_id, state);
                    CREATE INDEX IF NOT EXISTS idx_placements_source
                      ON placements(provider_id, source_item_id, state);
                """
                for statement in schema_sql.split(";"):
                    statement = statement.strip()
                    if statement:
                        db.execute(statement)
                cls._write_schema_version(db, 12)
                db.execute("RELEASE SAVEPOINT migration_v12")
            except Exception:
                db.execute("ROLLBACK TO SAVEPOINT migration_v12")
                db.execute("RELEASE SAVEPOINT migration_v12")
                raise
            version = 12

        if version < 13:
            db.execute("SAVEPOINT migration_v13")
            try:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS paris_borrower_adept_bindings (
                        credential_id TEXT NOT NULL,
                        account_id TEXT NOT NULL,
                        connection_identity_id TEXT NOT NULL,
                        borrower_identity_id TEXT NOT NULL,
                        profile_id TEXT NOT NULL,
                        confirmed_by TEXT NOT NULL,
                        confirmed_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY (credential_id, account_id)
                    )
                """)
                cls._write_schema_version(db, 13)
                db.execute("RELEASE SAVEPOINT migration_v13")
            except Exception:
                db.execute("ROLLBACK TO SAVEPOINT migration_v13")
                db.execute("RELEASE SAVEPOINT migration_v13")
                raise
            version = 13

        if version < 14:
            # Foundations only: NO copying or mirroring from backup_items,
            # entitlements, path labels or ledger history. Old rows lack the
            # exact verified account/branch/origin needed to assert authority.
            # In particular, this migration performs zero file/network I/O.
            db.execute("SAVEPOINT migration_v14")
            try:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS source_references (
                        source_reference_id TEXT PRIMARY KEY,
                        identity_id TEXT NOT NULL,
                        provider_id TEXT NOT NULL,
                        credential_id TEXT NOT NULL,
                        account_id TEXT NOT NULL,
                        branch_id TEXT NOT NULL,
                        edition_id TEXT NOT NULL,
                        right_id TEXT NOT NULL,
                        reference_kind TEXT NOT NULL CHECK (
                            reference_kind IN ('purchase','loan','notice','internal')
                        ),
                        state TEXT NOT NULL DEFAULT 'unknown' CHECK (
                            state IN ('active','inactive','unknown')
                        ),
                        synchronizable INTEGER NOT NULL DEFAULT 0 CHECK (
                            synchronizable IN (0,1)
                        ),
                        evidence_kind TEXT NOT NULL DEFAULT 'unverified',
                        evidence_ref TEXT NOT NULL DEFAULT '',
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        observed_at TEXT,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        UNIQUE(identity_id,provider_id,credential_id,account_id,
                               branch_id,edition_id,right_id,reference_kind)
                    )
                """)
                db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_source_references_scope
                    ON source_references(identity_id,provider_id,credential_id,
                                         account_id,branch_id,reference_kind)
                """)
                db.execute("""
                    CREATE TABLE IF NOT EXISTS origin_links (
                        origin_link_id TEXT PRIMARY KEY,
                        origin_kind TEXT NOT NULL CHECK (
                            origin_kind IN ('provider','manual','transfer',
                                            'cifereca','adept','derived')
                        ),
                        identity_id TEXT NOT NULL,
                        artifact_id TEXT NOT NULL,
                        source_reference_id TEXT,
                        evidence_ref TEXT NOT NULL DEFAULT '',
                        parent_artifact_id TEXT,
                        from_identity_id TEXT,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        CHECK (
                            origin_kind != 'provider'
                            OR (source_reference_id IS NOT NULL
                                AND evidence_ref <> '')
                        ),
                        CHECK (
                            origin_kind != 'cifereca'
                            OR source_reference_id IS NOT NULL
                        ),
                        CHECK (
                            origin_kind != 'transfer'
                            OR (from_identity_id IS NOT NULL
                                AND from_identity_id <> identity_id)
                        ),
                        CHECK (
                            origin_kind != 'derived'
                            OR (parent_artifact_id IS NOT NULL
                                AND parent_artifact_id <> artifact_id)
                        ),
                        CHECK (
                            origin_kind = 'derived'
                            OR parent_artifact_id IS NULL
                        ),
                        FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id),
                        FOREIGN KEY(source_reference_id)
                            REFERENCES source_references(source_reference_id),
                        FOREIGN KEY(parent_artifact_id)
                            REFERENCES artifacts(artifact_id)
                    )
                """)
                db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_origin_links_artifact
                    ON origin_links(artifact_id,identity_id,origin_kind)
                """)
                db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_origin_links_source
                    ON origin_links(source_reference_id,origin_kind)
                """)
                # The same SHA can belong to another identity's independent
                # purchase, but a 'provider' origin may not borrow that right.
                # Enforce even for direct SQL writers; a future broker MUST
                # still revalidate file bytes and exact account/session.
                db.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_provider_origin_scope_insert
                    BEFORE INSERT ON origin_links
                    WHEN NEW.origin_kind='provider' AND NOT EXISTS (
                        SELECT 1 FROM source_references AS s
                        WHERE s.source_reference_id=NEW.source_reference_id
                        AND s.identity_id=NEW.identity_id
                        AND s.reference_kind IN ('purchase','loan')
                    )
                    BEGIN
                        SELECT RAISE(ABORT,'provider_origin_scope_mismatch');
                    END
                """)
                db.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_internal_origin_scope_insert
                    BEFORE INSERT ON origin_links
                    WHEN NEW.origin_kind='cifereca' AND NOT EXISTS (
                        SELECT 1 FROM source_references AS s
                        WHERE s.source_reference_id=NEW.source_reference_id
                        AND s.identity_id=NEW.identity_id
                        AND s.reference_kind='internal'
                    )
                    BEGIN
                        SELECT RAISE(ABORT,'internal_origin_scope_mismatch');
                    END
                """)
                db.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_artifact_linked_sha_update
                    BEFORE UPDATE OF sha256 ON artifacts
                    WHEN NEW.sha256<>OLD.sha256 AND EXISTS (
                        SELECT 1 FROM origin_links AS o
                        WHERE o.artifact_id=OLD.artifact_id
                           OR o.parent_artifact_id=OLD.artifact_id
                    )
                    BEGIN
                        SELECT RAISE(ABORT,'origin_artifact_sha_immutable');
                    END
                """)
                # SQLite connections do not all enable PRAGMA foreign_keys;
                # protect the physical Artifact and both source references
                # directly so an old maintenance writer cannot erase an
                # OriginLink's historical coordinates by deleting a parent.
                db.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_origin_artifact_insert
                    BEFORE INSERT ON origin_links
                    WHEN NOT EXISTS (
                        SELECT 1 FROM artifacts
                        WHERE artifact_id=NEW.artifact_id
                    ) OR (NEW.parent_artifact_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM artifacts
                        WHERE artifact_id=NEW.parent_artifact_id
                    ))
                    BEGIN
                        SELECT RAISE(ABORT,'origin_artifact_missing');
                    END
                """)
                db.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_origin_artifact_parent_delete
                    BEFORE DELETE ON artifacts
                    WHEN EXISTS (
                        SELECT 1 FROM origin_links
                        WHERE artifact_id=OLD.artifact_id
                           OR parent_artifact_id=OLD.artifact_id
                    )
                    BEGIN
                        SELECT RAISE(ABORT,'origin_artifact_still_referenced');
                    END
                """)
                db.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_origin_source_delete
                    BEFORE DELETE ON source_references
                    WHEN EXISTS (
                        SELECT 1 FROM origin_links
                        WHERE source_reference_id=OLD.source_reference_id
                    )
                    BEGIN
                        SELECT RAISE(ABORT,'origin_source_still_referenced');
                    END
                """)
                # Origin records are persistent provenance, not mutable
                # snapshots. Changing a linked SourceReference's account,
                # provider, branch, edition or right would silently grant a
                # different owner the same historical SHA. Only mutable
                # observation state/timestamps may evolve; exact coordinates
                # and the OriginLink's evidence are append-only.
                db.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_linked_source_coords_immutable
                    BEFORE UPDATE OF source_reference_id,identity_id,provider_id,
                        credential_id,account_id,branch_id,edition_id,
                        right_id,reference_kind ON source_references
                    WHEN EXISTS (
                        SELECT 1 FROM origin_links AS o
                        WHERE o.source_reference_id=OLD.source_reference_id
                    ) AND (
                        NEW.source_reference_id IS NOT OLD.source_reference_id
                        OR NEW.identity_id IS NOT OLD.identity_id
                        OR NEW.provider_id IS NOT OLD.provider_id
                        OR NEW.credential_id IS NOT OLD.credential_id
                        OR NEW.account_id IS NOT OLD.account_id
                        OR NEW.branch_id IS NOT OLD.branch_id
                        OR NEW.edition_id IS NOT OLD.edition_id
                        OR NEW.right_id IS NOT OLD.right_id
                        OR NEW.reference_kind IS NOT OLD.reference_kind
                    )
                    BEGIN
                        SELECT RAISE(ABORT,'origin_source_coords_immutable');
                    END
                """)
                db.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_origin_coords_immutable
                    BEFORE UPDATE OF origin_link_id,origin_kind,identity_id,
                        artifact_id,source_reference_id,evidence_ref,
                        parent_artifact_id,from_identity_id ON origin_links
                    WHEN (
                        NEW.origin_link_id IS NOT OLD.origin_link_id
                        OR NEW.origin_kind IS NOT OLD.origin_kind
                        OR NEW.identity_id IS NOT OLD.identity_id
                        OR NEW.artifact_id IS NOT OLD.artifact_id
                        OR NEW.source_reference_id IS NOT OLD.source_reference_id
                        OR NEW.evidence_ref IS NOT OLD.evidence_ref
                        OR NEW.parent_artifact_id IS NOT OLD.parent_artifact_id
                        OR NEW.from_identity_id IS NOT OLD.from_identity_id
                    )
                    BEGIN
                        SELECT RAISE(ABORT,'origin_link_coords_immutable');
                    END
                """)
                cls._write_schema_version(db, 14)
                db.execute("RELEASE SAVEPOINT migration_v14")
            except Exception:
                db.execute("ROLLBACK TO SAVEPOINT migration_v14")
                db.execute("RELEASE SAVEPOINT migration_v14")
                raise
            version = 14

    @staticmethod
    def _stable_id(prefix: str, *parts: Any) -> str:
        raw = "\x1f".join(str(part or "") for part in parts).encode("utf-8", "surrogatepass")
        return f"{prefix}:{hashlib.sha256(raw).hexdigest()[:32]}"

    @staticmethod
    def _json_dump(value: Any) -> str:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _json_load(value: Any) -> dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(str(value))
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    @classmethod
    def _entitlement_id_for(cls, row: dict[str, Any]) -> str:
        return cls._stable_id(
            "entitlement", row.get("provider_id"), row.get("credential_id"), row.get("account_id"),
            row.get("item_id"), row.get("entitlement_type") or "owned", row.get("loan_id"),
        )

    @classmethod
    def _acquisition_id_for(cls, row: dict[str, Any]) -> str:
        return cls._stable_id("acquisition", cls._entitlement_id_for(row))

    @classmethod
    def _generic_status(cls, value: Any) -> str:
        status = str(value or "").strip().lower()
        if status == "complete":
            return "complete"
        if status == "acquiring":
            return "acquiring"
        if status == "attention_required":
            return "attention_required"
        if status in {"error", "failed", "retryable_error"}:
            return "retryable_error"
        if status in {"pending", "pending_acquisition", "queued"}:
            return "pending"
        return "discovered"

    @classmethod
    def _mirror_backup_row(
        cls, db: sqlite3.Connection, row: dict[str, Any], *, now: int | None = None, migration: bool = False
    ) -> None:
        timestamp = int(row.get("updated_at") or now or time.time())
        entitlement_id = cls._entitlement_id_for(row)
        entitlement_type = str(row.get("entitlement_type") or "owned")
        # A v0.7 projection must not overwrite a newer, provider-attested
        # canonical right (state, revision, expires_at, metadata or timestamp).
        # Preserve the canonical row while still mirroring the historical
        # receipt. This SELECT is inside the same SQLite write transaction
        # held by backup_put() / the migration; another writer cannot rebind
        # the entitlement between the scope check and the receipt handoff.
        existing_right = db.execute(
            "SELECT provider_id,credential_id,account_id,item_id,"
            "entitlement_type,loan_id,identity_id,metadata_json "
            "FROM entitlements WHERE entitlement_id=?",
            (entitlement_id,),
        ).fetchone()
        if existing_right is not None:
            expected = (
                str(row.get("provider_id") or ""),
                str(row.get("credential_id") or ""),
                row.get("account_id"), str(row.get("item_id") or ""),
                entitlement_type, row.get("loan_id"),
            )
            old_owner, offered_owner = existing_right[6], row.get("identity_id")
            if (existing_right[:6] != expected
                    or (old_owner is not None and offered_owner is not None
                        and old_owner != offered_owner)):
                raise ValueError("backup_entitlement_owner_conflict")
            previous_metadata = cls._json_load(existing_right[7])
            projection_owned = (
                previous_metadata.get("compatibility_projection") == "backup_items"
                or previous_metadata.get("imported_from") == "backup_items"
            )
        else:
            projection_owned = True
        if projection_owned:
            entitlement_cursor = db.execute(
                "INSERT INTO entitlements(entitlement_id,provider_id,credential_id,account_id,item_id,revision_id,"
                "entitlement_type,state,loan_id,expires_at,metadata_json,discovered_at,updated_at,identity_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(entitlement_id) DO UPDATE SET revision_id=excluded.revision_id,"
                "state=excluded.state,expires_at=COALESCE(excluded.expires_at,entitlements.expires_at),"
                "metadata_json=excluded.metadata_json,"
                "identity_id=COALESCE(excluded.identity_id,entitlements.identity_id),updated_at=excluded.updated_at "
                "WHERE entitlements.provider_id=excluded.provider_id "
                "AND entitlements.credential_id=excluded.credential_id "
                "AND entitlements.account_id IS excluded.account_id "
                "AND entitlements.item_id=excluded.item_id "
                "AND entitlements.entitlement_type=excluded.entitlement_type "
                "AND entitlements.loan_id IS excluded.loan_id "
                "AND (entitlements.identity_id IS NULL OR excluded.identity_id IS NULL "
                "OR entitlements.identity_id=excluded.identity_id)",
                (
                    entitlement_id, str(row.get("provider_id") or ""), str(row.get("credential_id") or ""),
                    row.get("account_id"), str(row.get("item_id") or ""), row.get("revision_id"), entitlement_type,
                    "active", row.get("loan_id"), row.get("expires_at"),
                    cls._json_dump({
                        **({"imported_from": "backup_items"} if migration else {"compatibility_projection": "backup_items"}),
                        "title": row.get("title"), "author": row.get("author"),
                        "media_format": str(row.get("format") or "").lower() or None,
                        "cover_url": row.get("cover_url"),
                    }),
                    timestamp, timestamp, row.get("identity_id"),
                ),
            )
            if entitlement_cursor.rowcount != 1:
                raise ValueError("backup_entitlement_owner_conflict")
        artifact_id = None
        sha = str(row.get("sha256") or "").strip().lower()
        if sha:
            artifact_id = f"sha256:{sha}"
            try:
                size = max(0, int(row.get("size") or 0))
            except (TypeError, ValueError):
                size = 0
            # A legacy claim of a SHA/size is not a validation of its bytes.
            # Preserve existing canonical content facts, even on same-SHA replay.
            db.execute(
                "INSERT INTO artifacts(artifact_id,sha256,size,media_format,mime_type,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(artifact_id) DO NOTHING",
                (artifact_id, sha, size, str(row.get("format") or "unknown").lower(), None,
                 cls._json_dump({"imported_from": "backup_items"}), timestamp, timestamp),
            )
        local_path = str(row.get("local_path") or "").strip()
        placement_id = None
        if local_path:
            placement_id = cls._stable_id("placement", str(Path(local_path).expanduser().resolve()))
            # A physical pathname is unique across canonical placements.
            # Only an untouched, unverified legacy placeholder may be refreshed.
            # A canonical scan/publication (including missing/invalid history)
            # has higher authority than this writer and must not be relabelled.
            # Another right/identity cannot silently take it over merely
            # because its legacy Kobo projection names the same local file.
            placement_cursor = db.execute(
                "INSERT INTO placements(placement_id,artifact_id,path,identity_id,provider_id,source_item_id,state,last_seen_at,"
                "metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET artifact_id=COALESCE(excluded.artifact_id,placements.artifact_id),"
                "provider_id=COALESCE(excluded.provider_id,placements.provider_id),source_item_id=COALESCE(excluded.source_item_id,placements.source_item_id),"
                # A passive v0.7 replay is not a physical stat/hash. Keep the
                # original placeholder timestamp when its physical mapping
                # (SHA claim, supplier, edition) is unchanged; a changed claim
                # still updates the placeholder, never its verification state.
                "updated_at=CASE WHEN "
                "placements.artifact_id IS COALESCE(excluded.artifact_id,placements.artifact_id) "
                "AND placements.provider_id IS COALESCE(excluded.provider_id,placements.provider_id) "
                "AND placements.source_item_id IS COALESCE(excluded.source_item_id,placements.source_item_id) "
                "THEN placements.updated_at ELSE excluded.updated_at END "
                "WHERE placements.state='unverified' AND placements.metadata_json=? "
                "AND (placements.identity_id IS NULL OR excluded.identity_id IS NULL "
                "OR placements.identity_id=excluded.identity_id) "
                "AND (placements.provider_id IS NULL OR excluded.provider_id IS NULL "
                "OR placements.provider_id=excluded.provider_id) "
                "AND (placements.source_item_id IS NULL OR excluded.source_item_id IS NULL "
                "OR placements.source_item_id=excluded.source_item_id) "
                # A v0.7 placeholder can coexist with a catalogue already
                # promoted by an independent scan. Never let the old writer
                # change its physical claim after the catalogue became
                # authoritative, even if the placement itself stayed unverified.
                "AND NOT EXISTS (SELECT 1 FROM catalog_entries AS catalog_guard "
                "WHERE catalog_guard.placement_id=placements.placement_id AND ("
                "catalog_guard.metadata_json IS NOT ? OR catalog_guard.local_state<>'local' "
                "OR catalog_guard.identity_id IS NOT ? OR catalog_guard.provider_id IS NOT ? "
                "OR catalog_guard.item_id IS NOT ?))",
                (placement_id, artifact_id, str(Path(local_path).expanduser().resolve()), row.get("identity_id"), row.get("provider_id"),
                 row.get("item_id"), "unverified", None, cls._json_dump({"imported_from": "backup_items"}), timestamp, timestamp,
                 cls._json_dump({"imported_from": "backup_items"}),
                  cls._json_dump({"imported_from": "backup_items"}),
                  row.get("identity_id"), row.get("provider_id"), row.get("item_id")),
            )
            if placement_cursor.rowcount != 1:
                # The historical backup projection can contain multiple
                # item references to the same pathname (including diagnoses
                # of broken mappings). Preserve each legacy/source receipt,
                # but never create another canonical LOCAL catalogue claim
                # or link this item's acquisition to the incumbent artifact.
                placement_id = None
                artifact_id = None
            else:
                catalog_id = cls._stable_id("catalog", placement_id)
                db.execute(
                    "INSERT INTO catalog_entries(catalog_entry_id,artifact_id,placement_id,title,author,media_format,identity_id,"
                    "provider_id,item_id,local_state,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(catalog_entry_id) DO UPDATE SET artifact_id=COALESCE(excluded.artifact_id,catalog_entries.artifact_id),"
                    "title=excluded.title,author=COALESCE(excluded.author,catalog_entries.author),media_format=COALESCE(excluded.media_format,catalog_entries.media_format),"
                    "provider_id=COALESCE(excluded.provider_id,catalog_entries.provider_id),item_id=COALESCE(excluded.item_id,catalog_entries.item_id),"
                    "updated_at=excluded.updated_at "
                    # Refresh only a still-owned 0.7 descriptive placeholder.
                    # Do not overwrite a local catalogue entry promoted by a
                    # scan, an explicit declaration, or a different account.
                    "WHERE catalog_entries.placement_id IS excluded.placement_id "
                    "AND catalog_entries.metadata_json IS ? AND catalog_entries.local_state='local' "
                    "AND catalog_entries.identity_id IS excluded.identity_id "
                    "AND catalog_entries.provider_id IS excluded.provider_id "
                    "AND catalog_entries.item_id IS excluded.item_id",
                    (catalog_id, artifact_id, placement_id, str(row.get("title") or row.get("item_id") or "Sans titre"),
                     row.get("author"), str(row.get("format") or "").lower() or None, row.get("identity_id"), row.get("provider_id"), row.get("item_id"),
                     "local", cls._json_dump({"imported_from": "backup_items"}), timestamp, timestamp,
                     cls._json_dump({"imported_from": "backup_items"})),
                )
        acquisition_id = cls._acquisition_id_for(row)
        acquisition_cursor = db.execute(
            "INSERT INTO acquisition_ledger(acquisition_id,entitlement_id,provider_id,credential_id,account_id,item_id,title,status,"
            "strategy,executor,candidate_id,artifact_id,attempt_count,last_attempt_at,error_code,error_message,attention_code,"
            "metadata_json,created_at,updated_at,identity_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(acquisition_id) DO UPDATE SET "
            # Passive old discovery/preflight must never downgrade a completed
            # canonical acquisition. Active attempts (acquiring/errors) remain
            # possible so an intentional new delivery is not blocked.
            "status=CASE WHEN acquisition_ledger.status='complete' "
            "AND excluded.status IN ('pending','discovered') "
            "THEN acquisition_ledger.status ELSE excluded.status END,"
            "strategy=CASE WHEN acquisition_ledger.status='complete' "
            "AND excluded.status IN ('pending','discovered') "
            "THEN acquisition_ledger.strategy "
            "ELSE COALESCE(excluded.strategy,acquisition_ledger.strategy) END,"
            "executor=CASE WHEN acquisition_ledger.status='complete' "
            "AND excluded.status IN ('pending','discovered') "
            "THEN acquisition_ledger.executor "
            "ELSE COALESCE(excluded.executor,acquisition_ledger.executor) END,"
            "artifact_id=CASE WHEN acquisition_ledger.status='complete' "
            "AND excluded.status IN ('pending','discovered') "
            "THEN acquisition_ledger.artifact_id "
            "ELSE COALESCE(excluded.artifact_id,acquisition_ledger.artifact_id) END,"
            "attempt_count=CASE WHEN acquisition_ledger.status='complete' "
            "AND excluded.status IN ('pending','discovered') "
            "THEN acquisition_ledger.attempt_count "
            "ELSE MAX(acquisition_ledger.attempt_count,excluded.attempt_count) END,"
            "last_attempt_at=CASE WHEN acquisition_ledger.status='complete' "
            "AND excluded.status IN ('pending','discovered') "
            "THEN acquisition_ledger.last_attempt_at "
            "ELSE COALESCE(excluded.last_attempt_at,acquisition_ledger.last_attempt_at) END,"
            "error_message=CASE WHEN acquisition_ledger.status='complete' "
            "AND excluded.status IN ('pending','discovered') "
            "THEN acquisition_ledger.error_message ELSE excluded.error_message END,"
            "attention_code=CASE WHEN acquisition_ledger.status='complete' "
            "AND excluded.status IN ('pending','discovered') "
            "THEN acquisition_ledger.attention_code ELSE excluded.attention_code END,"
            # A passive v0.7 replay cannot assert a fresher owner or
            # timestamp for an already completed canonical receipt. Active
            # acquisitions keep their existing forward transitions.
            "identity_id=CASE WHEN acquisition_ledger.status='complete' "
            "AND excluded.status IN ('pending','discovered') "
            "THEN acquisition_ledger.identity_id "
            "ELSE COALESCE(excluded.identity_id,acquisition_ledger.identity_id) END,"
            "updated_at=CASE WHEN acquisition_ledger.status='complete' "
            "AND excluded.status IN ('pending','discovered') "
            "THEN acquisition_ledger.updated_at ELSE excluded.updated_at END "
            "WHERE acquisition_ledger.entitlement_id IS excluded.entitlement_id "
            "AND acquisition_ledger.provider_id=excluded.provider_id "
            "AND acquisition_ledger.credential_id=excluded.credential_id "
            "AND acquisition_ledger.account_id IS excluded.account_id "
            "AND acquisition_ledger.item_id=excluded.item_id "
            "AND (acquisition_ledger.identity_id IS NULL OR excluded.identity_id IS NULL "
            "OR acquisition_ledger.identity_id=excluded.identity_id)",
            (
                acquisition_id, entitlement_id, str(row.get("provider_id") or ""), str(row.get("credential_id") or ""),
                row.get("account_id"), str(row.get("item_id") or ""), row.get("title"), cls._generic_status(row.get("status")),
                row.get("acquisition_strategy"), row.get("executor"), None, artifact_id, int(row.get("attempt_count") or 0),
                row.get("last_attempt_at"), None, row.get("error"), row.get("attention_code"),
                cls._json_dump({"legacy_status": row.get("status")}), timestamp, timestamp, row.get("identity_id"),
            ),
        )

        if acquisition_cursor.rowcount != 1:
            raise ValueError("backup_acquisition_owner_conflict")

    def schema_version(self) -> int:
        with self._lock, self._connect() as db:
            return self._read_schema_version(db)

    def _connect(self):
        """Return one reusable SQLite connection per worker thread.

        Most Storage methods use the sqlite connection as a context manager.
        ``sqlite3.Connection.__exit__`` commits/rolls back but deliberately does
        not close the connection, which makes a thread-local connection a safe
        drop-in optimisation for the historical call sites.  This removes the
        open/close + PRAGMA setup cost from the hot paths while preserving the
        existing transaction boundaries.
        """
        connection = getattr(self._thread_local, "connection", None)
        if connection is not None:
            return connection
        connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA synchronous=NORMAL")
        self._thread_local.connection = connection
        with self._connections_lock:
            self._connections.add(connection)
        return connection

    def close(self) -> None:
        """Close cached connections owned by this Storage instance."""
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error:
                pass
        try:
            del self._thread_local.connection
        except AttributeError:
            pass

    def cache_get(self, namespace: str, key: str, default: Any = None) -> Any:
        now = int(time.time())
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT payload, expires_at FROM cache WHERE namespace=? AND cache_key=?",
                (namespace, key),
            ).fetchone()
            if not row:
                return default
            payload, expires_at = row
            if expires_at < now:
                db.execute("DELETE FROM cache WHERE namespace=? AND cache_key=?", (namespace, key))
                return default
            try:
                return json.loads(payload)
            except Exception:
                return default

    def cache_put(self, namespace: str, key: str, value: Any, ttl_seconds: int) -> None:
        now = int(time.time())
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO cache(namespace,cache_key,payload,stored_at,expires_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(namespace,cache_key) DO UPDATE SET payload=excluded.payload, stored_at=excluded.stored_at, expires_at=excluded.expires_at",
                (namespace, key, json.dumps(value, ensure_ascii=False), now, now + max(1, ttl_seconds)),
            )

    def cache_delete(self, namespace: str, key: str) -> bool:
        with self._lock, self._connect() as db:
            cur = db.execute(
                "DELETE FROM cache WHERE namespace=? AND cache_key=?",
                (str(namespace), str(key)),
            )
            return bool(cur.rowcount)

    def cache_delete_namespace(self, namespace: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM cache WHERE namespace=?", (namespace,))

    def purge_expired(self) -> int:
        now = int(time.time())
        with self._lock, self._connect() as db:
            cur = db.execute("DELETE FROM cache WHERE expires_at < ?", (now,))
            return cur.rowcount


    def cache_namespace_stats(self) -> list[dict[str, Any]]:
        now = int(time.time())
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT namespace,COUNT(*),COALESCE(SUM(LENGTH(payload)),0),"
                "SUM(CASE WHEN expires_at < ? THEN 1 ELSE 0 END) FROM cache GROUP BY namespace ORDER BY namespace",
                (now,),
            ).fetchall()
        return [
            {"namespace": str(row[0]), "entries": int(row[1] or 0), "size_bytes": int(row[2] or 0), "expired_entries": int(row[3] or 0)}
            for row in rows
        ]

    def cache_delete_namespace_counted(self, namespace: str, *, expired_only: bool = False) -> tuple[int, int]:
        now = int(time.time())
        with self._lock, self._connect() as db:
            where = "namespace=? AND expires_at < ?" if expired_only else "namespace=?"
            params = (str(namespace), now) if expired_only else (str(namespace),)
            row = db.execute(f"SELECT COUNT(*),COALESCE(SUM(LENGTH(payload)),0) FROM cache WHERE {where}", params).fetchone()
            db.execute(f"DELETE FROM cache WHERE {where}", params)
        return int((row or (0,0))[0] or 0), int((row or (0,0))[1] or 0)

    def action_event_put(self, *, identity_id: str | None, action_type: str, outcome: str, provider_id: str = "", plugin_id: str = "", facts: Mapping[str, Any] | None = None, occurred_at: int | None = None) -> str:
        timestamp = int(occurred_at or time.time())
        raw = "\0".join((str(identity_id or ""), str(action_type), str(outcome), str(provider_id), str(plugin_id), str(timestamp), str(time.time_ns()), json.dumps(dict(facts or {}), sort_keys=True, default=str)))
        import hashlib
        event_id = "event:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO action_events(event_id,identity_id,action_type,outcome,provider_id,plugin_id,facts_json,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
                (event_id, identity_id, str(action_type), str(outcome), str(provider_id or ""), str(plugin_id or ""), self._json_dump(dict(facts or {})), timestamp),
            )
        return event_id

    def action_event_counts(self, *, identity_id: str | None, since: int, until: int | None = None) -> list[dict[str, Any]]:
        until = int(until or time.time())
        clauses = ["occurred_at>=?", "occurred_at<=?"]; params: list[Any] = [int(since), until]
        if identity_id is not None:
            clauses.append("identity_id=?"); params.append(str(identity_id))
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT action_type,outcome,COUNT(*) FROM action_events WHERE " + " AND ".join(clauses) + " GROUP BY action_type,outcome ORDER BY action_type,outcome",
                tuple(params),
            ).fetchall()
        return [{"action_type": str(r[0]), "outcome": str(r[1]), "count": int(r[2])} for r in rows]

    @staticmethod
    def _backup_keys() -> list[str]:
        return [
            "revision_id", "title", "author", "format", "local_path", "status", "size", "sha256",
            "updated_at", "error", "job_id", "account_id", "entitlement_type", "loan_id", "expires_at",
            "acquisition_strategy", "executor", "adept_profile", "attempt_count", "last_attempt_at", "attention_code", "identity_id", "cover_url",
        ]

    def backup_get(self, provider_id: str, credential_id: str, item_id: str) -> dict[str, Any] | None:
        keys = self._backup_keys()
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT " + ",".join(keys) + " FROM backup_items "
                "WHERE provider_id=? AND credential_id=? AND item_id=?",
                (provider_id, credential_id, item_id),
            ).fetchone()
        if not row:
            return None
        out = dict(zip(keys, row))
        out.update({"provider_id": provider_id, "credential_id": credential_id, "item_id": item_id})
        return out

    def backup_put(self, item: dict[str, Any]) -> None:
        now = int(item.get("updated_at") or time.time())
        attempt_count = item.get("attempt_count")
        try:
            attempt_count_value = max(0, int(attempt_count)) if attempt_count is not None else 0
        except (TypeError, ValueError):
            attempt_count_value = 0
        with self._lock, self._connect() as db:
            # The v0.7 writer is still in service until #3196 migrates its
            # consumers. In particular an active Kobo delivery legitimately
            # advances the canonical receipt before refreshing backup_items.
            # Do not reject that transition merely because the two states
            # temporarily differ: protect only passive replay downgrades in
            # the canonical UPSERT below, leaving both writers operational.
            # A legacy projection PK omits identity/account. Refuse an
            # explicit conflicting owner atomically: neither the 0.7 row nor
            # the canonical right/receipt may change on an ambiguous rebind.
            backup_cursor = db.execute(
                "INSERT INTO backup_items("
                "provider_id,credential_id,item_id,revision_id,title,author,format,local_path,status,size,sha256,updated_at,error,job_id,"
                "account_id,entitlement_type,loan_id,expires_at,acquisition_strategy,executor,adept_profile,attempt_count,last_attempt_at,attention_code,identity_id,cover_url"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(provider_id,credential_id,item_id) DO UPDATE SET "
                "revision_id=excluded.revision_id,title=excluded.title,author=excluded.author,format=excluded.format,"
                "local_path=excluded.local_path,status=excluded.status,size=excluded.size,sha256=excluded.sha256,"
                "updated_at=excluded.updated_at,error=excluded.error,job_id=excluded.job_id,"
                "account_id=COALESCE(excluded.account_id,backup_items.account_id),"
                "entitlement_type=COALESCE(NULLIF(excluded.entitlement_type,''),backup_items.entitlement_type),"
                "loan_id=COALESCE(excluded.loan_id,backup_items.loan_id),expires_at=COALESCE(excluded.expires_at,backup_items.expires_at),"
                "acquisition_strategy=COALESCE(excluded.acquisition_strategy,backup_items.acquisition_strategy),"
                "executor=COALESCE(excluded.executor,backup_items.executor),"
                "adept_profile=COALESCE(excluded.adept_profile,backup_items.adept_profile),"
                "attempt_count=MAX(backup_items.attempt_count,excluded.attempt_count),"
                "last_attempt_at=COALESCE(excluded.last_attempt_at,backup_items.last_attempt_at),"
                "attention_code=excluded.attention_code,identity_id=COALESCE(excluded.identity_id,backup_items.identity_id),"
                "cover_url=COALESCE(excluded.cover_url,backup_items.cover_url) "
                "WHERE (backup_items.identity_id IS NULL OR excluded.identity_id IS NULL "
                "OR backup_items.identity_id=excluded.identity_id) "
                "AND (backup_items.account_id IS NULL OR excluded.account_id IS NULL "
                "OR backup_items.account_id=excluded.account_id) "
                "AND backup_items.entitlement_type=excluded.entitlement_type "
                "AND (backup_items.loan_id IS NULL OR excluded.loan_id IS NULL "
                "OR backup_items.loan_id=excluded.loan_id)",
                (
                    str(item.get("provider_id") or ""), str(item.get("credential_id") or ""), str(item.get("item_id") or ""),
                    item.get("revision_id"), str(item.get("title") or item.get("item_id") or "Sans titre"), item.get("author"),
                    item.get("format"), item.get("local_path"), str(item.get("status") or "pending"), item.get("size"),
                    item.get("sha256"), now, item.get("error"), item.get("job_id"), item.get("account_id"),
                    str(item.get("entitlement_type") or "owned"), item.get("loan_id"), item.get("expires_at"),
                    item.get("acquisition_strategy"), item.get("executor"), item.get("adept_profile"), attempt_count_value,
                    item.get("last_attempt_at"), item.get("attention_code"), item.get("identity_id"), item.get("cover_url"),
                ),
            )
            if backup_cursor.rowcount != 1:
                raise ValueError("backup_projection_owner_conflict")
            # Keep the 0.7 compatibility projection and the generic 0.8 ledger
            # in the same SQLite transaction. This makes Kobo immediately visible
            # to the provider-neutral acquisition model without duplicating sync I/O.
            # The UPSERT may have retained an already known account/identity,
            # loan, strategy or attempt counter from a partial legacy patch.
            # Hand off THAT merged row under the same write transaction. The
            # raw input would manufacture a second NULL-account entitlement
            # and could evade the canonical owner's collision guards.
            keys = ["provider_id", "credential_id", "item_id", *self._backup_keys()]
            persisted = db.execute(
                "SELECT " + ",".join(keys) + " FROM backup_items "
                "WHERE provider_id=? AND credential_id=? AND item_id=?",
                (str(item.get("provider_id") or ""),
                 str(item.get("credential_id") or ""),
                 str(item.get("item_id") or "")),
            ).fetchone()
            if persisted is None:
                raise ValueError("backup_projection_handoff_missing")
            self._mirror_backup_row(db, dict(zip(keys, persisted)), now=now, migration=False)

    def backup_prune_missing(
        self, provider_id: str, credential_id: str, seen_item_ids: set[str] | list[str] | tuple[str, ...]
    ) -> dict[str, int]:
        """Prune only the v0.7 compatibility projection after a complete manifest.

        A provider manifest admitted by the Kobo compatibility synchronizer
        may retire obsolete backup_items rows; it cannot attest an exact
        SourceReference right, an entitlement revocation or removal of bytes.
        Entitlements/OriginLinks/Placements/acquisition history are never
        mutated by this obsolete projection. Negative canonical rights require
        a separately scoped, typed provider contract under #3171/#3197.
        """
        provider = str(provider_id or "").strip()
        credential = str(credential_id or "").strip()
        # A malformed/partial caller payload must never become an empty
        # manifest. Preserve the existing all-or-nothing admission boundary.
        if type(seen_item_ids) not in (set, list, tuple) or len(seen_item_ids) > 100_000:
            raise ValueError("invalid_authoritative_manifest_item_ids")
        seen: set[str] = set()
        for raw_item in seen_item_ids:
            if (type(raw_item) is not str or not raw_item.strip()
                    or len(raw_item) > 1024
                    or any(c in raw_item for c in "\x00\r\n")):
                raise ValueError("invalid_authoritative_manifest_item_ids")
            seen.add(raw_item.strip())
        if not provider or not credential:
            return {"pruned": 0, "local_preserved": 0, "entitlements_inactivated": 0}

        with self._lock, self._connect() as db:
            # A single SQLite transaction protects the manifest projection
            # against concurrent backup_put and ensures all stale rows retire
            # together. Nothing in the canonical source/receipt tables is
            # in this write set.
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT item_id,local_path FROM backup_items "
                "WHERE provider_id=? AND credential_id=?",
                (provider, credential),
            ).fetchall()
            stale = [
                (item_id, path) for item_id, path in rows
                if str(item_id or "") not in seen
            ]
            local_preserved = 0
            for item_id, path in stale:
                local_path = str(path or "").strip()
                if local_path and Path(local_path).expanduser().is_file():
                    local_preserved += 1
                db.execute(
                    "DELETE FROM backup_items WHERE provider_id=? "
                    "AND credential_id=? AND item_id=?",
                    (provider, credential, str(item_id or "")),
                )
            # Retain the old summary field for GUI/caller compatibility.
            # A legacy manifest can never inactivate any canonical right.
            return {
                "pruned": len(stale),
                "local_preserved": local_preserved,
                "entitlements_inactivated": 0,
            }

    def backup_list(self, provider_id: str | None = None, credential_id: str | None = None) -> list[dict[str, Any]]:
        item_keys = self._backup_keys()
        keys = ["provider_id", "credential_id", "item_id", *item_keys]
        sql = "SELECT " + ",".join(keys) + " FROM backup_items"
        args: list[Any] = []
        where: list[str] = []
        if provider_id is not None:
            where.append("provider_id=?"); args.append(provider_id)
        if credential_id is not None:
            where.append("credential_id=?"); args.append(credential_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC, title COLLATE NOCASE"
        with self._lock, self._connect() as db:
            rows = db.execute(sql, tuple(args)).fetchall()
        return [dict(zip(keys, row)) for row in rows]

    # ---- Identity / provenance model (schema v4 / 0.8.1) -----------------------

    def identity_put(self, identity: Any) -> str:
        if isinstance(identity, dict):
            row = dict(identity)
        else:
            prefs = getattr(identity, "preferences", None)
            row = {
                "identity_id": getattr(identity, "identity_id"),
                "label": getattr(identity, "label", getattr(identity, "identity_id", "Identité")),
                "directory_name": getattr(identity, "directory_name", None),
                "prefer_original_language": getattr(prefs, "prefer_original_language", False),
                "reading_languages": list(getattr(prefs, "reading_languages", ()) or ()),
                "metadata": getattr(identity, "metadata", {}),
            }
        identity_id = str(row.get("identity_id") or "").strip()
        if not identity_id:
            raise ValueError("identity_id required")
        label = str(row.get("label") or identity_id).strip() or identity_id
        directory_name = str(row.get("directory_name") or identity_id).strip() or identity_id
        languages = row.get("reading_languages")
        if not isinstance(languages, (list, tuple)):
            languages = []
        languages = [str(x).strip().lower() for x in languages if str(x).strip()]
        now = int(time.time())
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO identities(identity_id,label,directory_name,prefer_original_language,reading_languages_json,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(identity_id) DO UPDATE SET label=excluded.label,directory_name=excluded.directory_name,"
                "prefer_original_language=excluded.prefer_original_language,reading_languages_json=excluded.reading_languages_json,"
                "metadata_json=excluded.metadata_json,updated_at=excluded.updated_at",
                (identity_id, label, directory_name, int(bool(row.get("prefer_original_language", False))),
                 json.dumps(languages, ensure_ascii=False), self._json_dump(row.get("metadata")), now, now),
            )
        return identity_id

    def identities(self) -> list[dict[str, Any]]:
        columns = ["identity_id", "label", "directory_name", "prefer_original_language", "reading_languages_json", "metadata_json", "created_at", "updated_at"]
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT " + ",".join(columns) + " FROM identities ORDER BY label COLLATE NOCASE, identity_id").fetchall()
        out: list[dict[str, Any]] = []
        for values in rows:
            row = dict(zip(columns, values))
            try:
                langs = json.loads(str(row.pop("reading_languages_json") or "[]"))
            except Exception:
                langs = []
            row["reading_languages"] = [str(x) for x in langs] if isinstance(langs, list) else []
            row["prefer_original_language"] = bool(row["prefer_original_language"])
            row["metadata"] = self._json_load(row.pop("metadata_json"))
            out.append(row)
        return out

    def identity_get(self, identity_id: str) -> dict[str, Any] | None:
        return next((row for row in self.identities() if row["identity_id"] == identity_id), None)

    def relocate_local_path(self, old_path: Path, new_path: Path, *, identity_id: str, provider_id: str) -> None:
        old_text = str(old_path.expanduser().resolve())
        new_text = str(new_path.expanduser().resolve())
        now = int(time.time())
        with self._lock, self._connect() as db:
            db.execute("SAVEPOINT relocate_local_path")
            try:
                # A user declaration still names the OLD file and contains
                # a stat fingerprint. Merely rewriting placements/backups
                # would strand that association after unlink. Until a
                # transaction can also refresh its fingerprint and consent,
                # refuse this rename BEFORE touching any persistent row.
                # The caller's staged hardlink is recoverable on exception.
                if old_text != new_text:
                    from .core.library_declared_links import (
                        DeclarationRejected, _records,
                    )
                    try:
                        declarations = _records(db)
                    except DeclarationRejected:
                        raise ValueError("relocate_declared_context_unavailable") from None
                    if any(
                        declaration["state"] == "active"
                        and declaration["file"]["path"] == old_text
                        for declaration in declarations
                    ):
                        raise ValueError("relocate_active_declaration_requires_relink")
                # Reconciliation may *not* rename away an acquisition's
                # checkpoint path and then claim that the download is absent.
                # Historical and active metadata cannot safely be distinguished
                # here: refuse rather than corrupt an index_pending recovery,
                # an ADEPT receipt or an independent account's provenance.
                # This is intentionally read-only and fail-closed until #3185
                # provides an explicit transactional checkpoint migration.
                if old_text != new_text:
                    import json as _json
                    from .core.duplicate_maintenance_guard import _mentions_exact_path
                    for (raw,) in db.execute(
                        "SELECT metadata_json FROM acquisition_ledger"
                    ):
                        if type(raw) is not str or len(raw) > 1024 * 1024:
                            raise ValueError("relocate_acquisition_context_unavailable")
                        try:
                            value = _json.loads(raw)
                        except (ValueError, TypeError, RecursionError):
                            raise ValueError("relocate_acquisition_context_unavailable") from None
                        if type(value) is not dict:
                            raise ValueError("relocate_acquisition_context_unavailable")
                        if _mentions_exact_path(value, old_text):
                            raise ValueError("relocate_acquisition_reference_requires_relink")
                    # Variant metadata may still identify this exact
                    # physical file as a source or output. Until a separate,
                    # scoped variant-path migration exists, refuse the rename
                    # inside the same transaction rather than strand it.
                    for index, (raw,) in enumerate(db.execute(
                        "SELECT metadata_json FROM artifact_variants"
                    )):
                        if index >= 100_000 or type(raw) is not str or len(raw) > 1024 * 1024:
                            raise ValueError("relocate_variant_context_unavailable")
                        try:
                            value = _json.loads(raw)
                        except (ValueError, TypeError, RecursionError):
                            raise ValueError("relocate_variant_context_unavailable") from None
                        if type(value) is not dict:
                            raise ValueError("relocate_variant_context_unavailable")
                        if _mentions_exact_path(value, old_text):
                            raise ValueError("relocate_variant_reference_requires_relink")
                    # Other canonical rights/bytes/placement records may
                    # carry this pathname independently of the row being moved.
                    # Historical vs active JSON paths are not yet typed: block
                    # rather than silently orphan a surviving reference.
                    from .core.duplicate_maintenance_guard import (
                        canonical_metadata_mentions_path,
                    )
                    extra_reference = canonical_metadata_mentions_path(
                        db, old_text,
                    )
                    if extra_reference is None:
                        raise ValueError("relocate_canonical_metadata_unavailable")
                    if extra_reference:
                        raise ValueError("relocate_canonical_metadata_requires_relink")
                    # A shared local_path belonging to another provider or
                    # identity must not remain dangling after this move.
                    if db.execute(
                        "SELECT 1 FROM backup_items WHERE local_path=? AND "
                        "(provider_id<>? OR identity_id IS NULL "
                        "OR identity_id<>?) LIMIT 1",
                        (old_text, provider_id, identity_id),
                    ).fetchone():
                        raise ValueError("relocate_scope_conflict")
                # A matching path or SHA is not evidence that a placement
                # belongs to the same identity, provider and logical source.
                # Validate *before* changing any backup or catalog references.
                placement_columns = (
                    "SELECT placement_id,metadata_json,identity_id,provider_id,"
                    "artifact_id,source_item_id FROM placements WHERE path=?"
                )
                old_row = db.execute(placement_columns, (old_text,)).fetchone()
                target_row = db.execute(placement_columns, (new_text,)).fetchone()
                if old_row and (
                    not identity_id or not provider_id
                    or old_row[2] != identity_id or old_row[3] != provider_id
                ):
                    raise ValueError("relocate_scope_conflict")
                if target_row and (
                    not identity_id or not provider_id
                    or target_row[2] != identity_id
                    or target_row[3] != provider_id
                ):
                    raise ValueError("relocate_scope_conflict")
                if old_row and target_row and old_row[0] != target_row[0]:
                    if (
                        not old_row[4] or old_row[4] != target_row[4]
                        or not old_row[5] or old_row[5] != target_row[5]
                    ):
                        raise ValueError("relocate_scope_conflict")
                placement_metadata = None
                # A different catalogue projection may still point at the
                # source path even when it uses another placement ID. Do not
                # strand or silently retarget that independent reference.
                # Only the direct metadata.path of the affected placement(s)
                # can be rewritten by the transaction below.
                if old_text != new_text:
                    from .core.duplicate_maintenance_guard import _mentions_exact_path
                    owned_catalog_ids = {
                        row[0] for row in (old_row, target_row) if row is not None
                    }
                    for index, (catalog_placement, raw) in enumerate(db.execute(
                        "SELECT placement_id,metadata_json FROM catalog_entries"
                    )):
                        if index >= 100_000 or type(raw) is not str or len(raw) > 1024 * 1024:
                            raise ValueError("relocate_catalog_context_unavailable")
                        try:
                            catalogue = json.loads(raw)
                        except (ValueError, TypeError, RecursionError):
                            raise ValueError("relocate_catalog_context_unavailable") from None
                        if type(catalogue) is not dict:
                            raise ValueError("relocate_catalog_context_unavailable")
                        if not _mentions_exact_path(catalogue, old_text):
                            continue
                        if catalog_placement not in owned_catalog_ids or any(
                            _mentions_exact_path(value, old_text)
                            for key, value in catalogue.items() if key != "path"
                        ):
                            raise ValueError("relocate_catalog_reference_requires_relink")
                    # Keep a scan-generated relative_path in sync with the
                    # renamed file. Its directory is unchanged for the
                    # normalize_filename operation; directory migration has
                    # a separate transaction and must not use this shortcut.
                    if old_row and (
                        target_row is None or target_row[0] == old_row[0]
                    ):
                        raw = old_row[1]
                        if type(raw) is not str or len(raw) > 1024 * 1024:
                            raise ValueError("relocate_placement_context_unavailable")
                        try:
                            placement_metadata = json.loads(raw)
                        except (ValueError, TypeError, RecursionError):
                            raise ValueError("relocate_placement_context_unavailable") from None
                        if type(placement_metadata) is not dict:
                            raise ValueError("relocate_placement_context_unavailable")
                        if _mentions_exact_path(placement_metadata, old_text):
                            raise ValueError("relocate_placement_reference_requires_relink")
                        relative = placement_metadata.get("relative_path")
                        if relative not in (None, ""):
                            old_name, new_name = Path(old_text), Path(new_text)
                            if (type(relative) is not str or len(relative) > 4096
                                    or Path(relative).is_absolute()
                                    or ".." in Path(relative).parts
                                    or Path(relative).name != old_name.name
                                    or old_name.parent != new_name.parent):
                                raise ValueError("relocate_relative_path_requires_relink")
                            placement_metadata["relative_path"] = str(
                                Path(relative).with_name(new_name.name)
                            )
                    else:
                        placement_metadata = None
                # A catalog entry bound to a placement can carry an
                # independent, explicit identity/source/edition/artifact.
                # The relocation below rewrites its identity/provider (and
                # possibly merges its placement ID). That is only safe when
                # none of its recorded coordinates contradicts the owning
                # placement: a row pointing to an old path must not become
                # evidence of another account's or edition's ownership.
                # Check both source and destination before any SQL mutation.
                if old_row:
                    owners = {old_row[0]: old_row}
                    if target_row is not None:
                        owners[target_row[0]] = target_row
                    for placement_key, catalog_identity, catalog_provider, catalog_item, catalog_artifact in db.execute(
                        "SELECT placement_id,identity_id,provider_id,item_id,artifact_id "
                        "FROM catalog_entries WHERE placement_id IN (?,?)",
                        (old_row[0], target_row[0] if target_row else old_row[0]),
                    ):
                        owner = owners.get(placement_key)
                        if owner is None:
                            raise ValueError("relocate_catalog_scope_conflict")
                        for recorded, expected in (
                            (catalog_identity, owner[2]),
                            (catalog_provider, owner[3]),
                            (catalog_item, owner[5]),
                            (catalog_artifact, owner[4]),
                        ):
                            if (recorded not in (None, "") and (
                                type(recorded) is not str
                                or type(expected) is not str
                                or not expected or recorded != expected
                            )):
                                raise ValueError("relocate_catalog_scope_conflict")
                if any(
                    row[0] not in (None, "", identity_id)
                    for row in db.execute(
                        "SELECT identity_id FROM backup_items "
                        "WHERE local_path=? AND provider_id=?",
                        (old_text, provider_id),
                    )
                ):
                    raise ValueError("relocate_scope_conflict")
                # Relocation is never an ownership attestation. In particular,
                # a no-op rename must not fill a historical NULL identity;
                # actual moves already refuse ambiguous legacy owners above.
                if old_text != new_text:
                    db.execute(
                        "UPDATE backup_items SET local_path=? "
                        "WHERE local_path=? AND provider_id=? AND identity_id=?",
                        (new_text, old_text, provider_id, identity_id),
                    )
                if old_row:
                    old_placement_id = str(old_row[0])
                    placement_id = old_placement_id
                    if target_row and str(target_row[0]) != old_placement_id:
                        placement_id = str(target_row[0])
                        db.execute(
                            "UPDATE placements SET identity_id=?,provider_id=?,state='present',updated_at=? WHERE placement_id=?",
                            (identity_id, provider_id, now, placement_id),
                        )
                        # The destination may have been discovered by a scan after a
                        # crash between copy and DB rewrite. Merge references safely.
                        db.execute(
                            "UPDATE catalog_entries SET placement_id=?,identity_id=?,provider_id=?,updated_at=? WHERE placement_id=?",
                            (placement_id, identity_id, provider_id, now, old_placement_id),
                        )
                        db.execute("DELETE FROM placements WHERE placement_id=?", (old_placement_id,))
                    else:
                        db.execute(
                            "UPDATE placements SET path=?,identity_id=?,provider_id=?,"
                            "metadata_json=?,updated_at=? WHERE placement_id=?",
                            (new_text, identity_id, provider_id,
                             self._json_dump(placement_metadata)
                             if placement_metadata is not None else old_row[1],
                             now, old_placement_id),
                        )
                    rows = db.execute(
                        "SELECT catalog_entry_id,metadata_json FROM catalog_entries WHERE placement_id=?", (placement_id,)
                    ).fetchall()
                    for catalog_entry_id, catalog_metadata_json in rows:
                        metadata = self._json_load(catalog_metadata_json)
                        if metadata.get("path") in {old_text, None, ""}:
                            metadata["path"] = new_text
                        db.execute(
                            "UPDATE catalog_entries SET identity_id=?,provider_id=?,metadata_json=?,updated_at=? WHERE catalog_entry_id=?",
                            (identity_id, provider_id, self._json_dump(metadata), now, catalog_entry_id),
                        )
                db.execute("RELEASE SAVEPOINT relocate_local_path")
            except Exception:
                db.execute("ROLLBACK TO SAVEPOINT relocate_local_path")
                db.execute("RELEASE SAVEPOINT relocate_local_path")
                raise

    def placement_for_path(self, path: Path) -> dict[str, Any] | None:
        text = str(path.expanduser().resolve())
        with self._lock, self._connect() as db:
            row = db.execute("SELECT placement_id FROM placements WHERE path=?", (text,)).fetchone()
        return self.placement_get(str(row[0])) if row else None

    def commit_directory_migration(self, plan: dict[str, Any]) -> None:
        """Commit all local references and journal state in one SQLite transaction."""
        from .identities.directory_migration import JOURNAL_KEY, rewrite_path
        mappings = [(e["old"], e["new"]) for e in plan["entries"]]
        mappings += [(c["old"], c["new"]) for c in plan["changes"]]
        root = Path(plan["root"])
        now = int(time.time())
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # A pending canonical duplicate cleanup owns the ORIGINAL inode
            # and both Placement paths until durable unlink + tombstone
            # finalization. An explicit identity-directory migration must not
            # silently move or delete this evidence, including by collapsing
            # two equal-content paths into a single Placement.
            pending = db.execute(
                "SELECT path,identity_id,metadata_json FROM placements "
                "WHERE state='dedup_cleanup_pending' LIMIT 1001"
            ).fetchall()
            if len(pending) > 1000:
                raise ValueError("directory_migration_pending_dedup_budget")
            changed_owners = {str(row["identity_id"]) for row in plan["changes"]}
            def unique_keys(items):
                value = {}
                for key, item in items:
                    if key in value:
                        raise ValueError("directory_migration_pending_dedup_evidence")
                    value[key] = item
                return value
            for path, identity_id, raw in pending:
                if (type(path) is not str or type(identity_id) is not str
                        or identity_id in changed_owners
                        or rewrite_path(path, mappings, root) != path):
                    raise ValueError("directory_migration_pending_dedup_scope")
                try:
                    evidence = json.loads(raw, object_pairs_hook=unique_keys)
                    marker = evidence["core.canonical-dedup-retirement.v1"]
                    keeper_id = marker["keeper_placement_id"]
                except (ValueError, TypeError, KeyError, RecursionError):
                    raise ValueError("directory_migration_pending_dedup_evidence") from None
                if type(keeper_id) is not str or not keeper_id:
                    raise ValueError("directory_migration_pending_dedup_evidence")
                keeper = db.execute(
                    "SELECT path,identity_id FROM placements WHERE placement_id=?",
                    (keeper_id,),
                ).fetchone()
                if (keeper is None or keeper[1] != identity_id
                        or type(keeper[0]) is not str
                        or rewrite_path(keeper[0], mappings, root) != keeper[0]):
                    raise ValueError("directory_migration_pending_dedup_keeper")
            # Repoint old references to an already indexed identical destination.
            for pid, path in db.execute("SELECT placement_id,path FROM placements").fetchall():
                new = rewrite_path(path, mappings, root)
                if new == path:
                    continue
                target = db.execute("SELECT placement_id FROM placements WHERE path=?", (new,)).fetchone()
                if target and target[0] != pid:
                    db.execute("UPDATE catalog_entries SET placement_id=? WHERE placement_id=?", (target[0], pid))
                    db.execute("DELETE FROM placements WHERE placement_id=?", (pid,))
                else:
                    db.execute("UPDATE placements SET path=?,updated_at=? WHERE placement_id=?", (new, now, pid))
            # Folder names once served as synthetic owner IDs for legacy
            # unconfigured trees. Only an EXPLICIT, journaled directory
            # migration may adopt those inferred owners; an ordinary scan
            # must never do so. Requalify them in this same SQLite commit,
            # after relocating paths and before the post-migration scan.
            for change in plan["changes"]:
                identity = str(change["identity_id"])
                destination = str(change["new"])
                for legacy in {
                    Path(str(change["old"])).name,
                    Path(destination).name,
                }:
                    if legacy == identity or not legacy:
                        continue
                    if any(
                        db.execute(
                            f"SELECT 1 FROM {table} WHERE identity_id=? LIMIT 1",
                            (legacy,),
                        ).fetchone()
                        for table in (
                            "identities", "entitlements", "acquisition_ledger",
                            "provider_connections",
                        )
                    ):
                        # A registered/attested owner is not a folder alias.
                        continue
                    prefix = destination + "/"
                    inferred = db.execute(
                        "SELECT placement_id FROM placements "
                        "WHERE identity_id=? AND (path=? OR "
                        "substr(path,1,length(?))=?)",
                        (legacy, destination, prefix, prefix),
                    ).fetchall()
                    for (placement_id,) in inferred:
                        db.execute(
                            "UPDATE placements SET identity_id=?,updated_at=? "
                            "WHERE placement_id=? AND identity_id=?",
                            (identity, now, placement_id, legacy),
                        )
                        db.execute(
                            "UPDATE catalog_entries SET identity_id=?,updated_at=? "
                            "WHERE placement_id=? AND "
                            "(identity_id IS NULL OR identity_id=?)",
                            (identity, now, placement_id, legacy),
                        )
            for provider, credential, item, path in db.execute("SELECT provider_id,credential_id,item_id,local_path FROM backup_items").fetchall():
                new = rewrite_path(path, mappings, root)
                if new != path:
                    db.execute("UPDATE backup_items SET local_path=?,updated_at=? WHERE provider_id=? AND credential_id=? AND item_id=?", (new, now, provider, credential, item))
            # These JSON containers hold local paths, including nested ledger
            # materialization paths. Provider URLs and text remain untouched.
            for table, key in [("placements","placement_id"),("catalog_entries","catalog_entry_id"),
                               ("acquisition_ledger","acquisition_id"),("artifacts","artifact_id"),("entitlements","entitlement_id")]:
                for iid, value in db.execute(f"SELECT {key},metadata_json FROM {table}").fetchall():
                    before = self._json_load(value)
                    after = rewrite_path(before, mappings, root)
                    if after != before:
                        db.execute(f"UPDATE {table} SET metadata_json=?,updated_at=? WHERE {key}=?", (self._json_dump(after), now, iid))
            for change in plan["changes"]:
                db.execute("UPDATE identities SET directory_name=?,updated_at=? WHERE identity_id=?", (Path(change["new"]).name, now, change["identity_id"]))
            committed = {**plan, "state": "committed"}
            db.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (JOURNAL_KEY, json.dumps(committed)))
            db.execute("DELETE FROM cache WHERE namespace='search'")

    def provider_connection_put(self, item: dict[str, Any]) -> str:
        row = dict(item)
        provider_id = str(row.get("provider_id") or "").strip().lower()
        credential_id = str(row.get("credential_id") or "").strip()
        identity_id = str(row.get("identity_id") or "").strip()
        if not provider_id or not credential_id or not identity_id:
            raise ValueError("provider connection requires identity_id/provider_id/credential_id")
        connection_id = str(row.get("connection_id") or self._stable_id("connection", provider_id, credential_id))
        now = int(time.time())
        with self._lock, self._connect() as db:
            if provider_id == "kobo":
                existing = db.execute(
                    "SELECT credential_id FROM provider_connections WHERE identity_id=? AND provider_id='kobo' AND credential_id<>?",
                    (identity_id, credential_id),
                ).fetchone()
                if existing:
                    raise ValueError("une identité Paris ne peut avoir qu'un seul compte Kobo Vault")
            db.execute(
                "INSERT INTO provider_connections(connection_id,identity_id,provider_id,credential_id,label,enabled,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(provider_id,credential_id) DO UPDATE SET identity_id=excluded.identity_id,"
                "label=excluded.label,enabled=excluded.enabled,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at",
                (connection_id, identity_id, provider_id, credential_id, str(row.get("label") or provider_id),
                 int(bool(row.get("enabled", True))), self._json_dump(row.get("metadata")), now, now),
            )
            actual = db.execute(
                "SELECT connection_id FROM provider_connections WHERE provider_id=? AND credential_id=?",
                (provider_id, credential_id),
            ).fetchone()
        return str(actual[0] if actual else connection_id)

    def provider_connections(self, *, identity_id: str | None = None) -> list[dict[str, Any]]:
        columns = ["connection_id", "identity_id", "provider_id", "credential_id", "label", "enabled", "metadata_json", "created_at", "updated_at"]
        sql = "SELECT " + ",".join(columns) + " FROM provider_connections"
        args: tuple[Any, ...] = ()
        if identity_id is not None:
            sql += " WHERE identity_id=?"; args = (identity_id,)
        sql += " ORDER BY identity_id, provider_id, label COLLATE NOCASE"
        with self._lock, self._connect() as db:
            values = db.execute(sql, args).fetchall()
        out=[]
        for record in values:
            row=dict(zip(columns,record)); row["enabled"]=bool(row["enabled"]); row["metadata"]=self._json_load(row.pop("metadata_json")); out.append(row)
        return out

    def identity_for_connection(self, provider_id: str, credential_id: str) -> str | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT identity_id FROM provider_connections WHERE provider_id=? AND credential_id=?",
                (str(provider_id).lower(), str(credential_id)),
            ).fetchone()
        return str(row[0]) if row else None

    def provider_account_put(self, item: dict[str, Any]) -> str:
        row = dict(item)
        connection_id = str(row.get("connection_id") or "").strip()
        identity_id = str(row.get("identity_id") or "").strip()
        provider_id = str(row.get("provider_id") or "").strip().lower()
        credential_id = str(row.get("credential_id") or "").strip()
        account_id = str(row.get("account_id") if row.get("account_id") is not None else "")
        if not connection_id or not identity_id or not provider_id or not credential_id:
            raise ValueError("provider account requires connection and provenance")
        provider_account_id = str(row.get("provider_account_id") or self._stable_id("provider-account", connection_id, account_id))
        now = int(time.time())
        with self._lock, self._connect() as db:
            if provider_id == "kobo":
                existing = db.execute(
                    "SELECT provider_account_id FROM provider_accounts WHERE identity_id=? AND provider_id='kobo' AND provider_account_id<>?",
                    (identity_id, provider_account_id),
                ).fetchone()
                if existing:
                    raise ValueError("une identité Paris ne peut avoir qu'un seul compte Kobo")
            db.execute(
                "INSERT INTO provider_accounts(provider_account_id,connection_id,identity_id,provider_id,credential_id,account_id,label,is_primary,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(connection_id,account_id) DO UPDATE SET identity_id=excluded.identity_id,"
                "label=excluded.label,is_primary=excluded.is_primary,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at",
                (provider_account_id, connection_id, identity_id, provider_id, credential_id, account_id,
                 str(row.get("label") or account_id or "Compte principal"), int(bool(row.get("is_primary", False))),
                 self._json_dump(row.get("metadata")), now, now),
            )
            actual = db.execute(
                "SELECT provider_account_id FROM provider_accounts WHERE connection_id=? AND account_id=?",
                (connection_id, account_id),
            ).fetchone()
        return str(actual[0] if actual else provider_account_id)

    def provider_accounts(self, *, identity_id: str | None = None) -> list[dict[str, Any]]:
        columns = ["provider_account_id", "connection_id", "identity_id", "provider_id", "credential_id", "account_id", "label", "is_primary", "metadata_json", "created_at", "updated_at"]
        sql = "SELECT " + ",".join(columns) + " FROM provider_accounts"
        args: tuple[Any, ...] = ()
        if identity_id is not None:
            sql += " WHERE identity_id=?"; args = (identity_id,)
        sql += " ORDER BY identity_id, provider_id, is_primary DESC, label COLLATE NOCASE"
        with self._lock, self._connect() as db:
            values = db.execute(sql, args).fetchall()
        out=[]
        for record in values:
            row=dict(zip(columns,record)); row["is_primary"]=bool(row["is_primary"]); row["metadata"]=self._json_load(row.pop("metadata_json")); out.append(row)
        return out

    def adept_profile_binding_set(self, scope_type: str, scope_id: str, profile_id: str | None) -> None:
        scope_type = str(scope_type).strip().lower()
        scope_id = str(scope_id).strip()
        if scope_type != "identity" or not scope_id:
            raise ValueError("ADE profiles are bound to Paris identities only")
        now = int(time.time())
        with self._lock, self._connect() as db:
            if not str(profile_id or "").strip():
                db.execute("DELETE FROM adept_profile_bindings WHERE scope_type=? AND scope_id=?", (scope_type, scope_id))
            else:
                profile = str(profile_id).strip()
                existing = db.execute(
                    "SELECT scope_id FROM adept_profile_bindings WHERE scope_type='identity' AND profile_id=? AND scope_id<>?",
                    (profile, scope_id),
                ).fetchone()
                if existing:
                    raise ValueError("un profil Kobo-Adept ne peut être lié qu'à une seule identité Paris")
                db.execute(
                    "INSERT INTO adept_profile_bindings(scope_type,scope_id,profile_id,created_at,updated_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(scope_type,scope_id) DO UPDATE SET profile_id=excluded.profile_id,updated_at=excluded.updated_at",
                    (scope_type, scope_id, str(profile_id).strip(), now, now),
                )

    def adept_profile_binding_get(self, scope_type: str, scope_id: str) -> str | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT profile_id FROM adept_profile_bindings WHERE scope_type=? AND scope_id=?",
                (str(scope_type).strip().lower(), str(scope_id).strip()),
            ).fetchone()
        return str(row[0]) if row else None

    def paris_borrower_adept_binding_put(
        self, *, credential_id: str, account_id: str, borrower_identity_id: str,
        profile_id: str, confirmed: bool = False,
    ) -> None:
        """Persist an explicit borrower grant; never infer Adobe ownership.

        The grant is scoped to one Paris connection and borrower account, and
        pins the identity's stable ADEPT profile reference. A later profile
        change invalidates the grant until an explicit reconfirmation.
        """
        credential = str(credential_id or "").strip()
        borrower = str(account_id or "").strip()
        owner = str(borrower_identity_id or "").strip()
        profile = str(profile_id or "").strip()
        if not confirmed:
            raise ValueError("explicit borrower/ADEPT confirmation required")
        if not all((credential, borrower, owner, profile)):
            raise ValueError("Paris borrower and ADEPT identity/profile required")
        now = int(time.time())
        with self._lock, self._connect() as db:
            connection = db.execute(
                "SELECT identity_id FROM provider_connections WHERE provider_id='paris' AND credential_id=?",
                (credential,),
            ).fetchone()
            binding = db.execute(
                "SELECT profile_id FROM adept_profile_bindings WHERE scope_type='identity' AND scope_id=?",
                (owner,),
            ).fetchone()
            identity = db.execute(
                "SELECT 1 FROM identities WHERE identity_id=?", (owner,)
            ).fetchone()
            if not connection or not identity or not binding or str(binding[0]) != profile:
                raise ValueError("Paris connection or identity ADEPT profile is not proven")
            db.execute(
                "INSERT INTO paris_borrower_adept_bindings "
                "(credential_id,account_id,connection_identity_id,borrower_identity_id,profile_id,confirmed_by,confirmed_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(credential_id,account_id) DO UPDATE SET "
                "connection_identity_id=excluded.connection_identity_id,"
                "borrower_identity_id=excluded.borrower_identity_id,"
                "profile_id=excluded.profile_id,confirmed_by=excluded.confirmed_by,"
                "confirmed_at=excluded.confirmed_at,updated_at=excluded.updated_at",
                (credential, borrower, str(connection[0]), owner, profile, "explicit", now, now),
            )

    def paris_borrower_adept_binding_get(
        self, *, credential_id: str, account_id: str,
    ) -> dict[str, Any] | None:
        credential = str(credential_id or "").strip()
        borrower = str(account_id or "").strip()
        if not credential or not borrower:
            return None
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT connection_identity_id,borrower_identity_id,profile_id,confirmed_by,confirmed_at,updated_at "
                "FROM paris_borrower_adept_bindings WHERE credential_id=? AND account_id=?",
                (credential, borrower),
            ).fetchone()
        if row is None:
            return None
        return dict(zip(
            ("connection_identity_id", "borrower_identity_id", "profile_id", "confirmed_by", "confirmed_at", "updated_at"),
            row,
        ))

    def paris_borrower_adept_bindings(
        self, *, credential_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List locally confirmed bindings only, without contacting Paris/Adobe."""
        query = (
            "SELECT credential_id,account_id,connection_identity_id,"
            "borrower_identity_id,profile_id,confirmed_by,confirmed_at,updated_at "
            "FROM paris_borrower_adept_bindings"
        )
        params: tuple[str, ...] = ()
        if credential_id is not None:
            query += " WHERE credential_id=?"
            params = (str(credential_id).strip(),)
        query += " ORDER BY credential_id,account_id"
        with self._lock, self._connect() as db:
            rows = db.execute(query, params).fetchall()
        fields = (
            "credential_id", "account_id", "connection_identity_id",
            "borrower_identity_id", "profile_id", "confirmed_by",
            "confirmed_at", "updated_at",
        )
        return [dict(zip(fields, row)) for row in rows]

    def paris_borrower_adept_binding_delete(
        self, *, credential_id: str, account_id: str,
    ) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "DELETE FROM paris_borrower_adept_bindings WHERE credential_id=? AND account_id=?",
                (str(credential_id).strip(), str(account_id).strip()),
            )

    def device_binding_put(self, item: dict[str, Any]) -> str:
        row = dict(item); device_id = str(row.get("device_id") or "").strip(); identity_id = str(row.get("identity_id") or "").strip()
        if not device_id or not identity_id:
            raise ValueError("device binding requires device_id and identity_id")
        target_directory = str(row.get("target_directory") or "Books/Paris").strip() or "Books/Paris"
        now = int(time.time())
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO device_bindings(device_id,identity_id,label,enabled,metadata_json,created_at,updated_at,target_directory) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(device_id) DO UPDATE SET identity_id=excluded.identity_id,label=excluded.label,enabled=excluded.enabled,"
                "metadata_json=excluded.metadata_json,target_directory=excluded.target_directory,updated_at=excluded.updated_at",
                (device_id, identity_id, row.get("label"), int(bool(row.get("enabled", True))), self._json_dump(row.get("metadata")), now, now, target_directory),
            )
        return device_id

    def device_binding_get(self, device_id: str) -> dict[str, Any] | None:
        rows = [row for row in self.device_bindings() if row.get("device_id") == str(device_id)]
        return rows[0] if rows else None

    def device_binding_delete(self, device_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM device_placements WHERE device_id=?", (str(device_id),))
            db.execute("DELETE FROM device_bindings WHERE device_id=?", (str(device_id),))

    def device_bindings(self, *, identity_id: str | None = None) -> list[dict[str, Any]]:
        columns=["device_id","identity_id","label","enabled","target_directory","metadata_json","created_at","updated_at"]
        sql="SELECT "+",".join(columns)+" FROM device_bindings"; args: tuple[Any,...]=()
        if identity_id is not None:
            sql += " WHERE identity_id=?"; args=(identity_id,)
        sql += " ORDER BY label COLLATE NOCASE, device_id"
        with self._lock, self._connect() as db:
            values=db.execute(sql,args).fetchall()
        out=[]
        for record in values:
            row=dict(zip(columns,record)); row["enabled"]=bool(row["enabled"]); row["metadata"]=self._json_load(row.pop("metadata_json")); out.append(row)
        return out

    def device_placement_put(self, item: dict[str, Any]) -> str:
        row = dict(item)
        device_id = str(row.get("device_id") or "").strip()
        artifact_id = str(row.get("artifact_id") or "").strip()
        if not device_id or not artifact_id:
            raise ValueError("device placement requires device_id and artifact_id")
        desired = str(row.get("desired_state") or "keep").strip().lower()
        observed = str(row.get("observed_state") or "unknown").strip().lower()
        if desired not in {"keep", "remove"}:
            raise ValueError("invalid desired device placement state")
        if observed not in {"present", "missing", "unknown"}:
            raise ValueError("invalid observed device placement state")
        placement_id = str(row.get("device_placement_id") or self._stable_id("device-placement", device_id, artifact_id))
        now = int(time.time())
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO device_placements(device_placement_id,device_id,artifact_id,desired_state,observed_state,target_relative_path,last_observed_at,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(device_id,artifact_id) DO UPDATE SET "
                "desired_state=excluded.desired_state,observed_state=excluded.observed_state,"
                "target_relative_path=COALESCE(excluded.target_relative_path,device_placements.target_relative_path),"
                "last_observed_at=COALESCE(excluded.last_observed_at,device_placements.last_observed_at),"
                "metadata_json=excluded.metadata_json,updated_at=excluded.updated_at",
                (placement_id, device_id, artifact_id, desired, observed, row.get("target_relative_path"),
                 row.get("last_observed_at"), self._json_dump(row.get("metadata")), now, now),
            )
        return placement_id

    def device_placement_get(self, device_id: str, artifact_id: str) -> dict[str, Any] | None:
        rows = self.device_placements(device_id=device_id, artifact_id=artifact_id)
        return rows[0] if rows else None

    def device_placements(self, *, device_id: str | None = None, artifact_id: str | None = None) -> list[dict[str, Any]]:
        columns = [
            "device_placement_id", "device_id", "artifact_id", "desired_state", "observed_state",
            "target_relative_path", "last_observed_at", "metadata_json", "created_at", "updated_at",
        ]
        clauses: list[str] = []
        args: list[Any] = []
        if device_id is not None:
            clauses.append("device_id=?"); args.append(str(device_id))
        if artifact_id is not None:
            clauses.append("artifact_id=?"); args.append(str(artifact_id))
        sql = "SELECT " + ",".join(columns) + " FROM device_placements"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY device_id, artifact_id"
        with self._lock, self._connect() as db:
            values = db.execute(sql, tuple(args)).fetchall()
        out: list[dict[str, Any]] = []
        for record in values:
            row = dict(zip(columns, record)); row["metadata"] = self._json_load(row.pop("metadata_json")); out.append(row)
        return out

    def device_placement_delete(self, device_id: str, artifact_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM device_placements WHERE device_id=? AND artifact_id=?", (str(device_id), str(artifact_id)))

    # ---- Generic 0.8 acquisition/catalog model ---------------------------------

    def backup_find_by_local_path(self, path: str) -> dict[str, Any] | None:
        target = str(Path(path).expanduser().resolve())
        item_keys = self._backup_keys()
        keys = ["provider_id", "credential_id", "item_id", *item_keys]
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT " + ",".join(keys) + " FROM backup_items WHERE local_path IS NOT NULL"
            ).fetchall()
        match = None
        for row in rows:
            item = dict(zip(keys, row))
            raw = str(item.get("local_path") or "").strip()
            if not raw:
                continue
            try:
                if str(Path(raw).expanduser().resolve()) != target:
                    continue
            except OSError:
                continue
            # A shared path is not a supplier receipt. Two buyers, accounts,
            # editions or providers may legitimately refer to identical
            # bytes. Choosing the first SQLite row was nondeterministic and
            # could reassign catalogue provenance during a local rescan.
            # Treat multiple rows as unproven rather than promoting one.
            if match is not None:
                return None
            match = item
        return match

    def entitlement_realize_missing_loans(
        self, active_entitlement_ids: set[str], *, identity_id: str | None = None,
        complete_scopes: set[tuple[str, str, str]] | frozenset[tuple[str, str, str]] | None = None,
        now: int | None = None,
    ) -> int:
        """Mark absent loans realized only for complete, exact borrower scopes.

        An identity-level union, a successful sync task and a partial supplier
        response are not negative evidence. No file, placement, artifact or
        acquisition record is removed. Expiration is a separate operation.
        """
        if type(identity_id) is not str or not identity_id:
            return 0
        if type(complete_scopes) not in (set, frozenset) or not 0 < len(complete_scopes) <= 1000:
            return 0
        for scope in complete_scopes:
            if (type(scope) is not tuple or len(scope) != 3
                    or any(type(part) is not str for part in scope)
                    or not scope[0] or not scope[1]):
                # A malformed scope invalidates the entire absence decision.
                return 0
        if type(active_entitlement_ids) is not set or any(
            type(value) is not str for value in active_entitlement_ids
        ):
            return 0
        timestamp = int(now if now is not None else time.time())
        changed = 0
        with self._lock, self._connect() as db:
            for provider, credential, account in sorted(complete_scopes):
                rows = db.execute(
                    "SELECT entitlement_id FROM entitlements "
                    "WHERE entitlement_type='loan' AND state='active' "
                    "AND identity_id=? AND provider_id=? AND credential_id=? "
                    "AND account_id=?",
                    (identity_id, provider, credential, account),
                ).fetchall()
                for (entitlement_id,) in rows:
                    if entitlement_id in active_entitlement_ids:
                        continue
                    cur = db.execute(
                        "UPDATE entitlements SET state='realized',updated_at=? "
                        "WHERE entitlement_id=? AND identity_id=? AND provider_id=? "
                        "AND credential_id=? AND account_id=? "
                        "AND entitlement_type='loan' AND state='active'",
                        (timestamp, entitlement_id, identity_id, provider, credential, account),
                    )
                    changed += cur.rowcount
        return changed

    def sync_run_put(self, row: dict[str, Any]) -> str:
        now = int(row.get("updated_at") or time.time())
        started = int(row.get("started_at") or now)
        sync_run_id = str(row.get("sync_run_id") or self._stable_id("sync", row.get("scope") or "opening", row.get("identity_id"), started, now))
        detail = self._json_dump(row.get("detail") or {})
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO sync_runs(sync_run_id,identity_id,scope,status,started_at,finished_at,new_acquisitions,attention_required,errors,detail_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(sync_run_id) DO UPDATE SET status=excluded.status,finished_at=excluded.finished_at,"
                "new_acquisitions=excluded.new_acquisitions,attention_required=excluded.attention_required,errors=excluded.errors,"
                "detail_json=excluded.detail_json,updated_at=excluded.updated_at",
                (sync_run_id, row.get("identity_id"), str(row.get("scope") or "opening"), str(row.get("status") or "running"),
                 started, row.get("finished_at"), int(row.get("new_acquisitions") or 0), int(row.get("attention_required") or 0),
                 int(row.get("errors") or 0), detail, int(row.get("created_at") or now), now),
            )
        return sync_run_id

    def sync_runs(self, *, identity_id: str | None = None, scope: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        cols = ["sync_run_id","identity_id","scope","status","started_at","finished_at","new_acquisitions","attention_required","errors","detail_json","created_at","updated_at"]
        where: list[str] = []
        args: list[Any] = []
        if identity_id is not None:
            where.append("identity_id=?"); args.append(identity_id)
        if scope is not None:
            where.append("scope=?"); args.append(scope)
        sql = "SELECT " + ",".join(cols) + " FROM sync_runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY started_at DESC LIMIT ?"
        args.append(max(1, int(limit)))
        with self._lock, self._connect() as db:
            rows = db.execute(sql, tuple(args)).fetchall()
        out: list[dict[str, Any]] = []
        for values in rows:
            item = dict(zip(cols, values))
            item["detail"] = self._json_load(item.pop("detail_json"))
            out.append(item)
        return out

    def latest_sync_run(self, identity_id: str, *, scope: str = "opening") -> dict[str, Any] | None:
        rows = self.sync_runs(identity_id=identity_id, scope=scope, limit=1)
        return rows[0] if rows else None

    def entitlement_put(self, entitlement: Any) -> str:
        source = getattr(entitlement, "source", None)
        if source is not None:
            row = {
                "entitlement_id": getattr(entitlement, "entitlement_id"),
                "provider_id": getattr(source, "provider_id"),
                "credential_id": getattr(source, "credential_id"),
                "account_id": getattr(source, "account_id", None),
                "identity_id": getattr(source, "identity_id", None),
                "item_id": getattr(entitlement, "item_id"),
                "revision_id": getattr(entitlement, "revision_id", None),
                "entitlement_type": getattr(entitlement, "entitlement_type", "owned"),
                "state": getattr(entitlement, "state", "active"),
                "loan_id": getattr(entitlement, "loan_id", None),
                "expires_at": getattr(entitlement, "expires_at", None),
                "metadata": getattr(entitlement, "metadata", {}),
            }
        else:
            row = dict(entitlement)
            row.setdefault("entitlement_id", self._entitlement_id_for(row))
        now = int(time.time())
        with self._lock, self._connect() as db:
            # Entitlement IDs historically omit identity_id. The same
            # provider/credential/borrower/item/loan can therefore be
            # presented while its connection is assigned to another identity.
            # Never let an ordinary sync "move" the old right or replace
            # its provenance/metadata through ON CONFLICT. A real ownership
            # migration needs an explicit, separately reviewed operation.
            # WHERE executes in the SAME atomic UPSERT as the write, so
            # concurrent SQLite writers cannot race a preliminary SELECT.
            cursor = db.execute(
                "INSERT INTO entitlements(entitlement_id,provider_id,credential_id,account_id,item_id,revision_id,"
                "entitlement_type,state,loan_id,expires_at,metadata_json,discovered_at,updated_at,identity_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(entitlement_id) DO UPDATE SET revision_id=excluded.revision_id,state=excluded.state,"
                "loan_id=COALESCE(excluded.loan_id,entitlements.loan_id),expires_at=COALESCE(excluded.expires_at,entitlements.expires_at),"
                "metadata_json=excluded.metadata_json,identity_id=COALESCE(excluded.identity_id,entitlements.identity_id),updated_at=excluded.updated_at "
                "WHERE entitlements.provider_id=excluded.provider_id "
                "AND entitlements.credential_id=excluded.credential_id "
                "AND entitlements.account_id IS excluded.account_id "
                "AND entitlements.item_id=excluded.item_id "
                "AND entitlements.entitlement_type=excluded.entitlement_type "
                "AND entitlements.loan_id IS excluded.loan_id "
                "AND (entitlements.identity_id IS NULL "
                "OR entitlements.identity_id=excluded.identity_id)",
                (
                    row["entitlement_id"], str(row.get("provider_id") or ""), str(row.get("credential_id") or ""),
                    row.get("account_id"), str(row.get("item_id") or ""), row.get("revision_id"),
                    str(row.get("entitlement_type") or "owned"), str(row.get("state") or "active"),
                    row.get("loan_id"), row.get("expires_at"), self._json_dump(row.get("metadata")), now, now, row.get("identity_id"),
                ),
            )
            if cursor.rowcount != 1:
                # Closed error only: account, item, title, loan ID, database
                # path and provider-supplied text must not leave the Core.
                raise ValueError("entitlement_source_scope_conflict")
        return str(row["entitlement_id"])

    def entitlement_put_attested_owned(self, row: dict[str, Any]) -> bool:
        """Upsert a personal-manifest PURCHASE only for its exact owner.

        The canonical entitlement PK excludes the Cifereca identity, and
        mirror preflight reservations are only snapshots. A competing writer
        may claim that PK after discovery. The WHERE clause is therefore the
        *atomic* authority for preserving identity/account/provider/edition,
        including a legacy NULL owner, even across different processes.

        False means conflict: zero changes. This intentionally does not
        implement loan renewal, provenance repair, receipts or acquisitions.
        """
        expected = {
            "kobo": "kobo.personal_manifest",
            "numilog": "numilog.authenticated_library",
        }
        provider = row.get("provider_id") if type(row) is dict else None
        if type(provider) is not str or provider not in expected:
            raise ValueError("invalid_attested_owned_right")
        fields = (
            "entitlement_id", "provider_id", "credential_id",
            "identity_id", "item_id",
        )
        if (any(type(row.get(k)) is not str or not row[k]
                or len(row[k]) > 1024 or any(c in row[k] for c in "\0\r\n")
                for k in fields)
                or (row.get("account_id") is not None
                    and (type(row["account_id"]) is not str
                         or len(row["account_id"]) > 1024
                         or any(c in row["account_id"] for c in "\0\r\n")))
                or row.get("entitlement_type") != "owned"
                or row.get("state") not in (
                    {"active", "archived"} if provider == "kobo" else {"active"}
                )
                or row.get("loan_id") is not None
                or row.get("expires_at") is not None
                or type(row.get("metadata")) is not dict
                or row["metadata"].get("source") != expected[provider]):
            raise ValueError("invalid_attested_owned_right")
        encoded = self._json_dump(row["metadata"])
        if len(encoded) > 65536:
            raise ValueError("invalid_attested_owned_right")
        now = int(time.time())
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "INSERT INTO entitlements("
                "entitlement_id,provider_id,credential_id,account_id,item_id,"
                "revision_id,entitlement_type,state,loan_id,expires_at,"
                "metadata_json,discovered_at,updated_at,identity_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(entitlement_id) DO UPDATE SET "
                "revision_id=excluded.revision_id,"
                "state=excluded.state,"
                "metadata_json=excluded.metadata_json,"
                "updated_at=excluded.updated_at "
                "WHERE entitlements.identity_id IS excluded.identity_id "
                "AND entitlements.provider_id=excluded.provider_id "
                "AND entitlements.credential_id=excluded.credential_id "
                "AND entitlements.account_id IS excluded.account_id "
                "AND entitlements.item_id=excluded.item_id "
                "AND entitlements.entitlement_type='owned' "
                "AND entitlements.loan_id IS NULL "
                "AND entitlements.expires_at IS NULL",
                (
                    row["entitlement_id"], provider, row["credential_id"],
                    row.get("account_id"), row["item_id"], row.get("revision_id"),
                    "owned", row["state"], None, None, encoded, now, now,
                    row["identity_id"],
                ),
            )
            return cursor.rowcount == 1

    def entitlement_put_positive_loans(self, entitlements: tuple[Any, ...]) -> tuple[str, ...]:
        """Persist one complete positive-loan batch, or leave it unchanged.

        The stable entitlement PK excludes the local identity. Even if the
        first observed loan is valid, a later conflicting loan must NOT leave
        a half-refreshed batch in SQLite. Validate all fields before the first
        INSERT, then perform the guarded per-owner upserts in one transaction.
        An exception (including a stale/foreign owner) rolls back every row.
        No negative inventory proof, ledger or placement is written here.
        """
        from .acquisition_engine import Entitlement

        if type(entitlements) is not tuple or len(entitlements) > 100_000:
            raise ValueError("invalid_positive_loan")
        now = int(time.time())
        rows = []
        seen_source_loans: dict[tuple[str, str, str, str], tuple] = {}
        for entitlement in entitlements:
            if type(entitlement) is not Entitlement or entitlement.entitlement_type != "loan":
                raise ValueError("invalid_positive_loan")
            source = entitlement.source
            fields = (
                source.provider_id, source.credential_id, source.account_id,
                source.identity_id, entitlement.item_id, entitlement.loan_id,
            )
            if (any(type(value) is not str for value in fields)
                    or any(not value for value in (
                        source.provider_id, source.credential_id,
                        source.identity_id, entitlement.item_id, entitlement.loan_id,
                    ))
                    or any(len(value) > 1024 or any(c in value for c in "\0\r\n")
                           for value in fields)
                    or entitlement.state != "active"):
                raise ValueError("invalid_positive_loan")
            row = (
                entitlement.entitlement_id, source.provider_id,
                source.credential_id, source.account_id, entitlement.item_id,
                entitlement.revision_id, "loan", "active",
                entitlement.loan_id, entitlement.expires_at,
                self._json_dump(entitlement.metadata), now, now,
                source.identity_id,
            )
            # The upstream loan coordinate does NOT contain an edition ID.
            # Two editions for the same account/loan must not create two
            # concurrent rights, even if their hashed entitlement IDs differ.
            source_key = (source.provider_id, source.credential_id,
                          source.account_id, entitlement.loan_id)
            previous = seen_source_loans.get(source_key)
            if previous is not None:
                if previous != row:
                    raise ValueError("loan_duplicate_ambiguous")
                continue
            seen_source_loans[source_key] = row
            rows.append(row)
        # Empty/physical-only source inventories are read-only: in particular
        # they must not accidentally commit an unrelated cached transaction.
        if not rows:
            return ()
        statement = (
            "INSERT INTO entitlements("
            "entitlement_id,provider_id,credential_id,account_id,item_id,"
            "revision_id,entitlement_type,state,loan_id,expires_at,"
            "metadata_json,discovered_at,updated_at,identity_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(entitlement_id) DO UPDATE SET "
            "revision_id=excluded.revision_id,state=excluded.state,"
            "loan_id=excluded.loan_id,"
            # This is a fresh POSITIVE supplier observation, not a patch
            # of an unrelated acquisition record. If the current supplier
            # omits the expiry, the *old* deadline is no longer attested.
            # Retaining it could immediately re-realize the newly seen loan.
            "expires_at=excluded.expires_at,"
            "metadata_json=excluded.metadata_json,updated_at=excluded.updated_at "
            "WHERE entitlements.identity_id IS excluded.identity_id "
            "AND entitlements.provider_id=excluded.provider_id "
            "AND entitlements.credential_id=excluded.credential_id "
            "AND entitlements.account_id IS excluded.account_id "
            "AND entitlements.item_id=excluded.item_id "
            "AND entitlements.entitlement_type='loan' "
            "AND entitlements.loan_id IS excluded.loan_id"
        )
        with self._lock, self._connect() as db:
            # A SELECT does not acquire SQLite's write reservation. Without
            # BEGIN IMMEDIATE two *different* Core processes can each see
            # "no loan" before either inserts a conflicting edition. The
            # writer lock must precede the first source/loan lookup.
            if db.in_transaction:
                raise ValueError("storage_busy")
            db.execute("BEGIN IMMEDIATE")
            for row in rows:
                # The lookup and UPSERT share ONE writer transaction. A
                # previous refresh may already have assigned this provider
                # loan ID to another edition (or another local profile).
                # Never insert another entitlement merely because the new
                # edition changes the entitlement PK and avoids ON CONFLICT.
                existing = db.execute(
                    "SELECT entitlement_id FROM entitlements "
                    "WHERE provider_id=? AND credential_id=? AND account_id=? "
                    "AND entitlement_type='loan' AND loan_id=? LIMIT 2",
                    (row[1], row[2], row[3], row[8]),
                ).fetchall()
                if any(other_id != row[0] for (other_id,) in existing):
                    raise ValueError("loan_duplicate_ambiguous")
                if db.execute(statement, row).rowcount != 1:
                    # sqlite3.Connection.__exit__ rolls back the WHOLE batch
                    # before any observer can see the partial positive state.
                    raise ValueError("loan_entitlement_scope_conflict")
        return tuple(row[0] for row in rows)

    def entitlement_put_positive_loan(self, entitlement: Any) -> str:
        """Compatibility single-loan entrypoint with the same atomic guard."""
        return self.entitlement_put_positive_loans((entitlement,))[0]

    def numilog_entitlement_reservations(
        self, *, identity_id: str, credential_id: str, account_id: str,
        candidate_ids: tuple[str, ...],
    ) -> list[dict[str, str]]:
        """Identifiers needed for one authenticated Numilog purchase mirror.

        Fetch the current account's historical owned keys, plus *only*
        conflicting global IDs that its candidate writes might overwrite.
        Do not load other accounts' book metadata or enumerate their rights.
        Keep all lookups in one SQLite read snapshot.
        """
        scope = (identity_id, credential_id, account_id)
        if (not all(type(value) is str and 0 < len(value) <= 1024
                    and not any(ch in value for ch in "\0\r\n")
                    for value in scope)
                or type(candidate_ids) is not tuple
                or len(candidate_ids) > 200_000
                or any(type(value) is not str or not 1 <= len(value) <= 128
                       for value in candidate_ids)):
            raise ValueError("invalid_numilog_entitlement_scope")
        selected = (
            "entitlement_id,identity_id,credential_id,account_id,item_id,entitlement_type"
        )
        rows: dict[str, dict[str, str]] = {}
        with self._lock, self._connect() as db:
            db.execute("SAVEPOINT numilog_rights_index")
            try:
                own = db.execute(
                    f"SELECT {selected} FROM entitlements WHERE provider_id='numilog' "
                    "AND identity_id=? AND credential_id=? AND account_id=? "
                    "AND entitlement_type='owned' LIMIT 100001", scope,
                ).fetchall()
                if len(own) > 100_000:
                    raise ValueError("numilog_rights_budget_exhausted")
                # IN is bounded to 300 PK lookups (safe on older SQLite).
                # The candidate identifiers contain no book metadata.
                ids = tuple(dict.fromkeys(candidate_ids))
                for start in range(0, len(ids), 300):
                    batch = ids[start:start + 300]
                    placeholders = ",".join("?" for _ in batch)
                    for row in db.execute(
                        f"SELECT {selected} FROM entitlements "
                        f"WHERE provider_id='numilog' AND entitlement_id IN ({placeholders})",
                        batch,
                    ):
                        rows[row[0]] = dict(zip(selected.split(","), row))
                for row in own:
                    rows[row[0]] = dict(zip(selected.split(","), row))
            finally:
                db.execute("RELEASE SAVEPOINT numilog_rights_index")
        return list(rows.values())

    def numilog_possession_rights(
        self, *, identity_id: str, item_id: str, entitlement_id: str,
    ) -> list[dict[str, Any]]:
        """Only the exact owner/edition/right for an offline index-resume action.

        Never enumerate or decode other accounts' entitlements in the library
        UI. This passive read is NOT acquisition consent or a download grant.
        The receipt itself is checked separately under the acquisition lock.
        """
        from .core.numilog_manifest_rights import _scoped_record_id
        if (not all(type(v) is str and 0 < len(v) <= 1024
                    and not any(c in v for c in "\0\r\n")
                    for v in (identity_id, item_id, entitlement_id))
                or len(item_id) > 512 or len(entitlement_id) > 128
                or "://" in identity_id
                or any(ch.isspace() for ch in identity_id)):
            raise ValueError("invalid_numilog_possession_scope")
        columns = (
            "entitlement_id", "identity_id", "credential_id", "account_id",
            "item_id", "entitlement_type", "state", "loan_id", "expires_at",
            "metadata_json",
        )
        with self._lock, self._connect() as db:
            values = db.execute(
                "SELECT " + ",".join(columns) + " FROM entitlements "
                "WHERE provider_id='numilog' AND identity_id=? AND item_id=? "
                "AND entitlement_id=? AND entitlement_type='owned' "
                "AND state='active' LIMIT 2",
                (identity_id, item_id, entitlement_id),
            ).fetchall()
        result = []
        for value in values:
            row = dict(zip(columns, value))
            credential = row.get("credential_id")
            if (type(credential) is not str
                    or _scoped_record_id(item_id, credential) is None):
                continue
            row["metadata"] = self._json_load(row.pop("metadata_json"))
            result.append(row)
        return result

    def entitlements(self, *, provider_id: str | None = None, state: str | None = None) -> list[dict[str, Any]]:
        columns = [
            "entitlement_id", "provider_id", "credential_id", "account_id", "item_id", "revision_id", "identity_id",
            "entitlement_type", "state", "loan_id", "expires_at", "metadata_json", "discovered_at", "updated_at",
        ]
        where: list[str] = []
        args: list[Any] = []
        if provider_id is not None:
            where.append("provider_id=?"); args.append(provider_id)
        if state is not None:
            where.append("state=?"); args.append(state)
        sql = "SELECT " + ",".join(columns) + " FROM entitlements"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC"
        with self._lock, self._connect() as db:
            rows = db.execute(sql, tuple(args)).fetchall()
        out: list[dict[str, Any]] = []
        for values in rows:
            row = dict(zip(columns, values)); row["metadata"] = self._json_load(row.pop("metadata_json")); out.append(row)
        return out

    def entitlement_realize_expired(self, *, now: int | None = None) -> int:
        # ISO-8601 UTC timestamps sort lexically when consistently formatted, but
        # provider inputs are not guaranteed to be. SQLite datetime() normalizes
        # the common formats and ignores rows it cannot parse.
        ts = int(now or time.time())
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
        with self._lock, self._connect() as db:
            cur = db.execute(
                "UPDATE entitlements SET state='realized',updated_at=? "
                "WHERE entitlement_type='loan' AND state='active' AND expires_at IS NOT NULL "
                "AND datetime(expires_at) <= datetime(?)",
                (ts, iso),
            )
            return cur.rowcount

    def acquisition_put(self, item: dict[str, Any], *, insert_only: bool = False) -> str:
        row = dict(item)
        now = int(row.get("updated_at") or time.time())
        acquisition_id = str(row.get("acquisition_id") or self._stable_id(
            "acquisition", row.get("entitlement_id"), row.get("provider_id"), row.get("credential_id"), row.get("item_id")
        ))
        # Discovery must be insert-only: a repeated observation must not
        # erase a completed artifact or an in-flight/error state. Existing
        # callers retain the historical upsert behaviour by default.
        conflict_sql = (
            "ON CONFLICT(acquisition_id) DO NOTHING" if insert_only else
            "ON CONFLICT(acquisition_id) DO UPDATE SET entitlement_id=COALESCE(excluded.entitlement_id,acquisition_ledger.entitlement_id),"
            "title=COALESCE(excluded.title,acquisition_ledger.title),status=excluded.status,strategy=COALESCE(excluded.strategy,acquisition_ledger.strategy),"
            "executor=COALESCE(excluded.executor,acquisition_ledger.executor),candidate_id=COALESCE(excluded.candidate_id,acquisition_ledger.candidate_id),"
            "artifact_id=COALESCE(excluded.artifact_id,acquisition_ledger.artifact_id),attempt_count=MAX(acquisition_ledger.attempt_count,excluded.attempt_count),"
            "last_attempt_at=COALESCE(excluded.last_attempt_at,acquisition_ledger.last_attempt_at),error_code=excluded.error_code,"
            "error_message=excluded.error_message,attention_code=excluded.attention_code,metadata_json=excluded.metadata_json,"
            "identity_id=COALESCE(excluded.identity_id,acquisition_ledger.identity_id),updated_at=excluded.updated_at "
            # A stable acquisition ID may predate a complete account scope.
            # A collision must not update another borrower or provider's
            # state, artifact, identity or entitlement.
            "WHERE acquisition_ledger.provider_id=excluded.provider_id "
            "AND acquisition_ledger.credential_id=excluded.credential_id "
            "AND acquisition_ledger.account_id IS excluded.account_id "
            "AND acquisition_ledger.item_id=excluded.item_id "
            "AND (acquisition_ledger.identity_id IS NULL "
            "OR excluded.identity_id IS NULL "
            "OR acquisition_ledger.identity_id=excluded.identity_id) "
            "AND (acquisition_ledger.entitlement_id IS NULL "
            "OR excluded.entitlement_id IS NULL "
            "OR acquisition_ledger.entitlement_id=excluded.entitlement_id)"
        )
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "INSERT INTO acquisition_ledger(acquisition_id,entitlement_id,provider_id,credential_id,account_id,item_id,title,status,"
                "strategy,executor,candidate_id,artifact_id,attempt_count,last_attempt_at,error_code,error_message,attention_code,"
                "metadata_json,created_at,updated_at,identity_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                + conflict_sql,
                (
                    acquisition_id, row.get("entitlement_id"), str(row.get("provider_id") or ""), str(row.get("credential_id") or ""),
                    row.get("account_id"), str(row.get("item_id") or ""), row.get("title"), str(row.get("status") or "discovered"),
                    row.get("strategy"), row.get("executor"), row.get("candidate_id"), row.get("artifact_id"), int(row.get("attempt_count") or 0),
                    row.get("last_attempt_at"), row.get("error_code"), row.get("error_message"), row.get("attention_code"),
                    self._json_dump(row.get("metadata")), now, now, row.get("identity_id"),
                ),
            )
            if cursor.rowcount != 1:
                if not insert_only:
                    raise ValueError("acquisition_source_scope_conflict")
                # DO NOTHING means *some* receipt already owns this ID.
                # Before returning its ID to discovery (whose next action may
                # update its state), prove it belongs to the exact source,
                # borrower, library identity and entitlement. A legacy NULL
                # is unknown rather than permission to silently claim it.
                # The preceding INSERT holds the SQLite write transaction,
                # so no other worker can swap the row between these checks.
                existing = db.execute(
                    "SELECT 1 FROM acquisition_ledger WHERE acquisition_id=? "
                    "AND provider_id=? AND credential_id=? "
                    "AND account_id IS ? AND item_id=? "
                    "AND identity_id IS ? AND entitlement_id IS ?",
                    (
                        acquisition_id,
                        str(row.get("provider_id") or ""),
                        str(row.get("credential_id") or ""),
                        row.get("account_id"), str(row.get("item_id") or ""),
                        row.get("identity_id"), row.get("entitlement_id"),
                    ),
                ).fetchone()
                if existing is None:
                    raise ValueError("acquisition_source_scope_conflict")
        return acquisition_id

    def acquisition_get(self, acquisition_id: str) -> dict[str, Any] | None:
        rows = self.acquisitions(acquisition_id=acquisition_id)
        return rows[0] if rows else None

    def numilog_purchase_has_local_conflict(
        self, *, credential_id: str, item_id: str,
    ) -> bool:
        """Conservatively block duplicate/partial acquisitions for one edition.

        Exact stable account-scoped item ID only. No global acquisition scan,
        raw ledger payload, path or title leaves SQLite.
        """
        from .core.numilog_manifest_rights import _scoped_record_id
        if (_scoped_record_id(item_id, credential_id) is None):
            raise ValueError("invalid_numilog_purchase")
        with self._lock, self._connect() as db:
            # A consistent SQLite snapshot prevents a concurrent writer
            # from making the two evidence tables disagree mid-preflight.
            db.execute("SAVEPOINT numilog_manual_acquire")
            try:
                ledger = db.execute(
                    "SELECT 1 FROM acquisition_ledger WHERE provider_id='numilog' "
                    "AND credential_id=? AND item_id=? LIMIT 1",
                    (credential_id, item_id),
                ).fetchone()
                placement = db.execute(
                    "SELECT 1 FROM placements WHERE provider_id='numilog' "
                    "AND source_item_id=? LIMIT 1",
                    (item_id,),
                ).fetchone()
                return ledger is not None or placement is not None
            finally:
                db.execute("RELEASE SAVEPOINT numilog_manual_acquire")

    def numilog_purchase_preflight_evidence(
        self, *, identity_id: str, credential_id: str, account_id: str,
        item_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Read one exact purchase and conflicting ledger owners, atomically.

        This is a local *preflight*, never a download authorization. Other
        identities' entitlements/metadata are not read. Conflicting ledger
        rows for the same credential + variant must remain visible even if
        owned by a different account: never mistake them for a missing book.
        """
        from .core.numilog_local_preview import _item_id
        scope = (identity_id, credential_id, account_id)
        if (not all(type(v) is str and 0 < len(v) <= 1024
                    and not any(c in v for c in "\0\r\n") for v in scope)
                or not _item_id(item_id, credential_id)):
            raise ValueError("invalid_purchase_scope")
        with self._lock, self._connect() as db:
            db.execute("SAVEPOINT numilog_purchase_preflight")
            try:
                rights = db.execute(
                    "SELECT entitlement_id,identity_id,credential_id,account_id,"
                    "item_id,entitlement_type,state,loan_id,expires_at,metadata_json "
                    "FROM entitlements WHERE provider_id='numilog' "
                    "AND identity_id=? AND credential_id=? AND account_id=? "
                    "AND item_id=? LIMIT 3", (*scope, item_id),
                ).fetchall()
                ledgers = db.execute(
                    "SELECT acquisition_id,entitlement_id,identity_id,account_id,"
                    "item_id,status FROM acquisition_ledger "
                    "WHERE provider_id='numilog' AND credential_id=? AND item_id=? "
                    "LIMIT 3", (credential_id, item_id),
                ).fetchall()
                # The locked acquisition path ALSO refuses a placement for
                # this exact scoped purchase, even when no ledger exists.
                # Include the same conservative guard in the *same* SQLite
                # snapshot so the batch does not claim an acquirable item
                # that is already locally placed. Never expose path/owner.
                placement_conflict = db.execute(
                    "SELECT 1 FROM placements WHERE provider_id='numilog' "
                    "AND source_item_id=? LIMIT 1", (item_id,),
                ).fetchone() is not None
            finally:
                db.execute("RELEASE SAVEPOINT numilog_purchase_preflight")
        right_keys = ("entitlement_id", "identity_id", "credential_id",
                      "account_id", "item_id", "entitlement_type", "state",
                      "loan_id", "expires_at", "metadata_json")
        ledger_keys = ("acquisition_id", "entitlement_id", "identity_id",
                       "account_id", "item_id", "status")
        result = []
        for values in rights:
            row = dict(zip(right_keys, values))
            row["metadata"] = self._json_load(row.pop("metadata_json"))
            result.append(row)
        return {
            "entitlements": result,
            "acquisitions": [dict(zip(ledger_keys, values)) for values in ledgers],
            "placement_conflict": placement_conflict,
        }

    def numilog_materialization_consent_revision(
        self, source_id: str,
    ) -> str | None:
        """Observe the opaque consent generation before making a preview."""
        import re
        if type(source_id) is not str or re.fullmatch(
            r"sync:[0-9a-f]{24}", source_id,
        ) is None:
            raise ValueError("invalid_numilog_source")
        key = "sync.source.numilog.materialization.epoch.v1." + source_id
        value = self.metadata_get(key)
        if value is not None and re.fullmatch(r"[0-9a-f]{32}", value) is None:
            raise ValueError("invalid_numilog_consent_revision")
        return value

    def numilog_materialization_change_consent(
        self, source_id: str, *, activate: bool,
        expected_revision: str | None = None,
    ) -> bool:
        """Cross-process CAS: revocation invalidates every in-flight token.

        Consent and epoch are written in the same BEGIN IMMEDIATE transaction.
        Never re-enable after another process has revoked/disabled the source.
        Missing epoch is valid only on a fresh installation, never after a
        revocation. A corrupt revision blocks activation but can be revoked.
        """
        import re
        import secrets
        from .core.numilog_materialization_consent import (
            _ACTIVE, _MANUAL, policy_key,
        )
        if (type(source_id) is not str
                or re.fullmatch(r"sync:[0-9a-f]{24}", source_id) is None
                or type(activate) is not bool
                or (expected_revision is not None and (
                    type(expected_revision) is not str
                    or re.fullmatch(r"[0-9a-f]{32}", expected_revision) is None
                ))):
            raise ValueError("invalid_numilog_consent_update")
        epoch = "sync.source.numilog.materialization.epoch.v1." + source_id
        key = policy_key(source_id)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT value FROM metadata WHERE key=?", (epoch,),
            ).fetchone()
            prior = current[0] if current is not None else None
            state = db.execute(
                "SELECT value FROM metadata WHERE key=?", (key,),
            ).fetchone()
            if activate:
                if prior != expected_revision or (
                    state is not None and state[0] != _MANUAL
                ):
                    return False
            generation = secrets.token_hex(16)
            db.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (epoch, generation),
            )
            db.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, _ACTIVE if activate else _MANUAL),
            )
            return True

    def numilog_materialization_disable_source(self, source_id: str) -> None:
        """Atomically disable a Numilog source and revoke its backup grant.

        A two-transaction disable could leave acquisition consent enabled if
        the second write failed. A later inventory re-enable would silently
        resurrect that grant. Commit all three changes or none, with a fresh
        consent epoch that invalidates pending confirmations across processes.
        """
        import re
        import secrets
        from .core.numilog_materialization_consent import _MANUAL, policy_key
        if (type(source_id) is not str
                or re.fullmatch(r"sync:[0-9a-f]{24}", source_id) is None):
            raise ValueError("invalid_numilog_source")
        keys = (
            ("sync.source.enabled." + source_id, "0"),
            ("sync.source.numilog.materialization.epoch.v1." + source_id,
             secrets.token_hex(16)),
            (policy_key(source_id), _MANUAL),
        )
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for key, value in keys:
                db.execute(
                    "INSERT INTO metadata(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )

    def numilog_materialization_enable_source(self, source_id: str) -> None:
        """Re-enable inventory without resurrecting any previously disabled grant.

        Older two-step disables could leave source.enabled='0' and acquisition
        consent='on_start:v1'. A subsequent toggle must not turn that stale
        consent into new network permission. All changes share one SQLite
        transaction and rotate the epoch on disabled/corrupt -> enabled.
        """
        import re
        import secrets
        from .core.numilog_materialization_consent import _MANUAL, policy_key
        if (type(source_id) is not str
                or re.fullmatch(r"sync:[0-9a-f]{24}", source_id) is None):
            raise ValueError("invalid_numilog_source")
        key = "sync.source.enabled." + source_id
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT value FROM metadata WHERE key=?", (key,),
            ).fetchone()
            if previous is not None and previous[0] != "1":
                # A past explicit disable, including a malformed disabled
                # flag, cannot retain any backup acquisition permission.
                for pref, value in (
                    ("sync.source.numilog.materialization.epoch.v1." + source_id,
                     secrets.token_hex(16)),
                    (policy_key(source_id), _MANUAL),
                ):
                    db.execute(
                        "INSERT INTO metadata(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (pref, value),
                    )
            db.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, "1"),
            )

    def numilog_materialization_claim_startup(
        self, *, source_id: str, now: int, expected_revision: str,
    ) -> bool:
        """Cross-process one-shot claim, conditional on actual separate consent.

        Claims even when downstream I/O fails, so an interrupted fulfillment
        cannot be retried on every startup and consume a single-use ACSM URL.
        Never infer consent from generic sync, legacy Kobo flags or cache.
        A queued worker supplies its observed consent revision: compare it in
        the same transaction before consuming the new grant's clock/receipt.
        Every caller must name the grant it observed.
        """
        import re
        from .core.numilog_materialization_consent import (
            _ACTIVE, _MIN_SECONDS_BETWEEN_STARTS, policy_key,
        )
        if (type(source_id) is not str
                or re.fullmatch(r"sync:[0-9a-f]{24}", source_id) is None
                or type(now) is not int or not 1_000_000_000 <= now <= 9_999_999_999
                or type(expected_revision) is not str
                or re.fullmatch(r"[0-9a-f]{32}", expected_revision) is None):
            return False
        clock_key = "sync.source.numilog.materialization.last_start.v1." + source_id
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            consent = db.execute(
                "SELECT value FROM metadata WHERE key=?", (policy_key(source_id),),
            ).fetchone()
            if consent is None or consent[0] != _ACTIVE:
                return False
            epoch = db.execute(
                "SELECT value FROM metadata WHERE key=?",
                ("sync.source.numilog.materialization.epoch.v1." + source_id,),
            ).fetchone()
            if epoch is None or epoch[0] != expected_revision:
                return False
            clock = db.execute(
                "SELECT value FROM metadata WHERE key=?", (clock_key,),
            ).fetchone()
            if clock is not None:
                stamp = clock[0]
                if (type(stamp) is not str or not stamp.isdecimal()
                        or len(stamp) > 11):
                    return False
                previous = int(stamp)
                if previous > now or now - previous < _MIN_SECONDS_BETWEEN_STARTS:
                    return False
            db.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (clock_key, str(now)),
            )
            # A crash after claiming is *not* a successful backup. Keep a
            # durable "running" marker; no scheduler retries this claim.
            result_key = "sync.source.numilog.materialization.result.v1." + source_id
            pending = json.dumps({
                "version": 1, "started_at": now, "status": "running",
                "attempted": 0, "downloaded": 0, "skipped": 0, "failed": 0,
            }, sort_keys=True, separators=(",", ":"))
            db.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (result_key, pending),
            )
            return True

    def numilog_materialization_finish_startup(
        self, *, source_id: str, started_at: int,
        status: str, attempted: int, downloaded: int,
        skipped: int, failed: int,
        declared_local: int = 0, declared_pending: int = 0,
    ) -> bool:
        """Write bounded account-scoped counters with a stale-run SQL guard."""
        import re
        if (type(source_id) is not str
                or re.fullmatch(r"sync:[0-9a-f]{24}", source_id) is None
                or type(started_at) is not int
                or not 1_000_000_000 <= started_at <= 9_999_999_999
                or type(status) is not str
                or status not in {"complete", "partial", "failed", "blocked"}
                or any(type(value) is not int or not 0 <= value <= 8
                       for value in (attempted, downloaded, failed))
                or type(skipped) is not int or not 0 <= skipped <= 2048
                or type(declared_local) is not int or not 0 <= declared_local <= skipped
                or type(declared_pending) is not int or not 0 <= declared_pending <= 2048
                or (status == "complete" and declared_pending != 0)
                or downloaded > attempted
                or failed > attempted + 1
                or (status == "complete" and failed != 0)
                or (status == "partial" and downloaded == 0)
                or (status == "failed" and failed == 0)
                or (status == "blocked" and (downloaded != 0 or failed != 0))):
            raise ValueError("invalid_numilog_materialization_receipt")
        clock_key = "sync.source.numilog.materialization.last_start.v1." + source_id
        result_key = "sync.source.numilog.materialization.result.v1." + source_id
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            clock = db.execute(
                "SELECT value FROM metadata WHERE key=?", (clock_key,),
            ).fetchone()
            prior = db.execute(
                "SELECT value FROM metadata WHERE key=?", (result_key,),
            ).fetchone()
            if clock is None or clock[0] != str(started_at) or prior is None:
                return False
            try:
                current = json.loads(prior[0])
            except (ValueError, TypeError):
                return False
            if (type(current) is not dict
                    or current.get("version") != 1
                    or current.get("started_at") != started_at
                    or current.get("status") != "running"):
                return False
            completed = json.dumps({
                "version": 1, "started_at": started_at, "status": status,
                "attempted": attempted, "downloaded": downloaded,
                "skipped": skipped, "failed": failed,
                "already_local_declared": declared_local,
                "declared_needs_review": declared_pending,
            }, sort_keys=True, separators=(",", ":"))
            db.execute(
                "UPDATE metadata SET value=? WHERE key=?",
                (completed, result_key),
            )
            return True

    def numilog_materialization_run_status(self, source_id: str) -> dict[str, Any]:
        """Passive, account-scoped status for UI. No private book/URL data."""
        import re
        if type(source_id) is not str or re.fullmatch(
            r"sync:[0-9a-f]{24}", source_id,
        ) is None:
            raise ValueError("invalid_numilog_source")
        clock_key = "sync.source.numilog.materialization.last_start.v1." + source_id
        result_key = "sync.source.numilog.materialization.result.v1." + source_id
        with self._lock, self._connect() as db:
            db.execute("SAVEPOINT numilog_materialization_status")
            try:
                clock_row = db.execute(
                    "SELECT value FROM metadata WHERE key=?", (clock_key,),
                ).fetchone()
                result_row = db.execute(
                    "SELECT value FROM metadata WHERE key=?", (result_key,),
                ).fetchone()
            finally:
                db.execute("RELEASE SAVEPOINT numilog_materialization_status")
        if clock_row is None:
            return {"status": "never", "started_at": None,
                    "next_eligible_at": None,
                    "attempted": 0, "downloaded": 0, "skipped": 0, "failed": 0}
        clock = clock_row[0]
        empty = {"status": "unknown", "started_at": None,
                 "next_eligible_at": None,
                 "attempted": 0, "downloaded": 0, "skipped": 0, "failed": 0}
        if type(clock) is not str or not clock.isdecimal() or len(clock) > 11:
            return empty
        started = int(clock)
        if not 1_000_000_000 <= started <= 9_999_999_999:
            return empty
        if result_row is None:
            return empty  # legacy attempt clock, result was never recorded
        try:
            result = json.loads(result_row[0])
        except (ValueError, TypeError):
            return empty
        counts = ("attempted", "downloaded", "skipped", "failed")
        receipt_keys = {"version", "started_at", "status", *counts}
        declared_keys = {"already_local_declared", "declared_needs_review"}
        if (type(result) is not dict
                or set(result) not in (receipt_keys, receipt_keys | declared_keys)
                or type(result.get("version")) is not int
                or result["version"] != 1 or type(result.get("started_at")) is not int
                or result["started_at"] != started
                or type(result.get("status")) is not str
                or result["status"] not in {
                    "running", "complete", "partial", "failed", "blocked",
                } or any(type(result.get(key)) is not int
                         or not 0 <= result[key] <= (2048 if key == "skipped" else 8)
                         for key in counts)
                or result["downloaded"] > result["attempted"]
                or (result["status"] == "complete" and result["failed"] != 0)
                or (result["status"] == "partial" and result["downloaded"] == 0)
                or (result["status"] == "failed" and result["failed"] == 0)
                or (result["status"] == "blocked" and (
                    result["downloaded"] != 0 or result["failed"] != 0
                ))
                or (result["status"] == "running" and any(
                    result[key] != 0 for key in counts
                ))):
            return empty
        if declared_keys <= set(result):
            if (any(type(result.get(key)) is not int
                    or not 0 <= result[key] <= 2048 for key in declared_keys)
                    or result["already_local_declared"] > result["skipped"]
                    or (result["status"] in {"running", "complete"}
                        and result["declared_needs_review"] != 0)
                    or (result["status"] == "running"
                        and result["already_local_declared"] != 0)):
                return empty
        return {
            "status": result["status"], "started_at": started,
            "next_eligible_at": started + 3600,
            **{key: result[key] for key in counts},
            **{key: result[key] for key in declared_keys if key in result},
        }

    def source_materialization_evidence(
        self, *, provider_id: str, credential_id: str, account_id: str,
        identity_id: str, max_rows: int = 100_000,
    ) -> dict[str, list[dict[str, Any]]]:
        """Bounded read snapshot for one account's active owned purchases.

        This is evidence, never authorization to acquire. Keep conflicting
        ledger owners for the SAME credential/item: dropping them would turn
        ambiguous acquisitions into apparently missing books. Placements are
        restricted to the requested identity/provider/purchase/artifact, and
        only referenced artifacts are loaded. No global list or per-item SQL.
        """
        if (not all(type(value) is str and value and len(value) <= 1024
                    and not any(c in value for c in "\0\r\n")
                    for value in (provider_id, credential_id, account_id, identity_id))
                or type(max_rows) is not int or not 1 <= max_rows <= 100_000):
            raise ValueError("invalid_evidence_scope")
        cte = (
            "WITH rights AS (SELECT * FROM entitlements WHERE provider_id=? "
            "AND credential_id=? AND account_id=? AND identity_id=? "
            "AND entitlement_type='owned' AND state='active'), "
            "ledger AS (SELECT a.* FROM acquisition_ledger AS a WHERE "
            "a.provider_id=? AND a.credential_id=? AND EXISTS "
            "(SELECT 1 FROM rights AS r WHERE r.item_id=a.item_id)) "
        )
        args = (provider_id, credential_id, account_id, identity_id,
                provider_id, credential_id)
        queries = {
            "entitlements": (
                "SELECT entitlement_id,provider_id,credential_id,account_id,"
                "item_id,identity_id,entitlement_type,state,loan_id,expires_at,"
                "metadata_json FROM rights", (),
            ),
            "acquisitions": (
                "SELECT acquisition_id,entitlement_id,provider_id,credential_id,"
                "account_id,item_id,identity_id,status,artifact_id FROM ledger", (),
            ),
            "placements": (
                "SELECT DISTINCT p.placement_id,p.artifact_id,p.path,p.identity_id,"
                "p.provider_id,p.source_item_id,p.state FROM ledger AS a "
                "JOIN placements AS p ON p.artifact_id=a.artifact_id "
                "AND p.source_item_id=a.item_id "
                "WHERE p.identity_id=? AND p.provider_id=?", (identity_id, provider_id),
            ),
            "artifacts": (
                "SELECT DISTINCT f.artifact_id,f.sha256,f.size,f.media_format,"
                "f.metadata_json FROM ledger AS a JOIN artifacts AS f "
                "ON f.artifact_id=a.artifact_id", (),
            ),
        }
        result = {}
        with self._lock, self._connect() as db:
            # A Python lock alone does not protect against another process.
            # The read transaction keeps all four tables at one SQLite view.
            db.execute("SAVEPOINT materialization_evidence")
            try:
                for name, (query, extra) in queries.items():
                    cursor = db.execute(cte + query + " LIMIT ?", (*args, *extra, max_rows + 1))
                    columns = [column[0] for column in cursor.description]
                    values = cursor.fetchall()
                    if len(values) > max_rows:
                        raise ValueError("evidence_budget_exhausted")
                    rows = []
                    for record in values:
                        row = dict(zip(columns, record))
                        if "metadata_json" in row:
                            row["metadata"] = self._json_load(row.pop("metadata_json"))
                        if name == "artifacts":
                            row = self._artifact_row_with_kind(row)
                        rows.append(row)
                    result[name] = rows
            finally:
                db.execute("RELEASE SAVEPOINT materialization_evidence")
        return result

    def acquisitions(
        self, *, acquisition_id: str | None = None, provider_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        columns = [
            "acquisition_id", "entitlement_id", "provider_id", "credential_id", "account_id", "item_id", "title", "status",
            "strategy", "executor", "candidate_id", "artifact_id", "attempt_count", "last_attempt_at", "error_code", "error_message",
            "attention_code", "metadata_json", "created_at", "updated_at", "identity_id",
        ]
        where: list[str] = []
        args: list[Any] = []
        if acquisition_id is not None:
            where.append("acquisition_id=?"); args.append(acquisition_id)
        if provider_id is not None:
            where.append("provider_id=?"); args.append(provider_id)
        if status is not None:
            where.append("status=?"); args.append(status)
        sql = "SELECT " + ",".join(columns) + " FROM acquisition_ledger"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC, acquisition_id"
        with self._lock, self._connect() as db:
            values = db.execute(sql, tuple(args)).fetchall()
        out: list[dict[str, Any]] = []
        for record in values:
            row = dict(zip(columns, record)); row["metadata"] = self._json_load(row.pop("metadata_json")); out.append(row)
        return out

    def acquisition_bind_bnf_display_loan(self, acquisition_id: str, *, path: str,
                                          artifact_id: str, scope: dict[str, str]) -> bool:
        """Atomically retain one manual import's observed borrower, not a right."""
        from .core.bnf_import_provenance import scope_key
        key = scope_key(scope)
        if key is None:
            return False
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id=? "
                "AND provider_id='manual' AND strategy='manual_acsm' AND status='complete' "
                "AND identity_id=? AND artifact_id=? AND entitlement_id IS NULL",
                (acquisition_id, key[0], artifact_id),
            ).fetchone()
            if row is None:
                return False
            metadata = self._json_load(row[0])
            if metadata.get('path') != path or metadata.get('bnf_observed_loan', scope) != scope:
                return False
            placement = db.execute(
                "SELECT 1 FROM placements WHERE path=? AND artifact_id=? AND identity_id=? "
                "AND provider_id='bnf-pret' AND state='present'",
                (path, artifact_id, key[0]),
            ).fetchone()
            if placement is None:
                return False
            metadata['bnf_observed_loan'] = dict(scope)
            db.execute("UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
                       (self._json_dump(metadata), acquisition_id))
            return True

    def acquisition_update_state(
        self,
        acquisition_id: str,
        status: str,
        *,
        strategy: str | None = None,
        executor: str | None = None,
        candidate_id: str | None = None,
        artifact_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        attention_code: str | None = None,
        metadata: dict[str, Any] | None = None,
        increment_attempt: bool = False,
    ) -> None:
        now = int(time.time())
        with self._lock, self._connect() as db:
            current = db.execute(
                "SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id=?", (acquisition_id,)
            ).fetchone()
            if current is None:
                raise KeyError(acquisition_id)
            merged = self._json_load(current[0])
            if metadata:
                merged.update(metadata)
            db.execute(
                "UPDATE acquisition_ledger SET status=?,strategy=COALESCE(?,strategy),executor=COALESCE(?,executor),"
                "candidate_id=COALESCE(?,candidate_id),artifact_id=COALESCE(?,artifact_id),"
                "attempt_count=attempt_count+?,last_attempt_at=CASE WHEN ? THEN ? ELSE last_attempt_at END,"
                "error_code=?,error_message=?,attention_code=?,metadata_json=?,updated_at=? WHERE acquisition_id=?",
                (
                    status, strategy, executor, candidate_id, artifact_id, int(increment_attempt), int(increment_attempt), now,
                    error_code, error_message, attention_code, self._json_dump(merged), now, acquisition_id,
                ),
            )

    def numilog_checkpoint_adopt_acsm_placement(
        self, acquisition_id: str, *, expected_path: str,
        diagnose: bool = False,
    ) -> bool | str:
        """Bind a broker-indexed Numilog ACSM file to one proven owned receipt.

        The ADEPT import initially indexes the ACSM sha as its source item.
        Only the *later* exact-purchase receipt may replace that provenance,
        and only when both independent ledgers, the artifact, placement and
        catalogue all attest the same existing bytes and identity. A matching
        title, ACSM hash alone or legacy source label never permits adoption.
        No network call or filesystem mutation is performed here.
        """
        import hashlib
        import re
        from pathlib import Path

        # An optional internal diagnostic returns only a *fixed refusal code*
        # from the same SQLite transaction that makes the adoption decision.
        # The pre-existing bool API, on-disk data and safe-failure behavior
        # are unchanged. Never publish private row keys or paths.
        def refused(code: str) -> bool | str:
            return code if diagnose is True else False

        if (type(acquisition_id) is not str
                or re.fullmatch(r"numilog-materialization:[0-9a-f]{32}",
                                acquisition_id) is None
                or type(expected_path) is not str
                or not expected_path or "\0" in expected_path):
            return refused("invalid_target")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = db.execute(
                "SELECT identity_id,credential_id,account_id,item_id,status,"
                "metadata_json FROM acquisition_ledger WHERE acquisition_id=? "
                "AND provider_id='numilog'",
                (acquisition_id,),
            ).fetchone()
            if record is None:
                return refused("receipt_missing")
            identity, credential, account, item_id, status, receipt_raw = record
            from .core.numilog_manifest_rights import _scoped_record_id
            if (not all(type(v) is str and v for v in
                        (identity, credential, account, item_id))
                    or _scoped_record_id(item_id, credential) is None
                    or status not in {"acquiring", "retryable_error", "attention_required"}
                    or acquisition_id not in {
                        self._stable_id("numilog-materialization", identity, credential, account, item_id),
                        self._stable_id("numilog-materialization-recovery", identity, credential, account, item_id),
                    }):
                return refused("receipt_scope_invalid")
            receipt = self._json_load(receipt_raw)
            if (type(receipt) is not dict
                    or receipt.get("kind") != "numilog_delivered_artifact_v1"
                    or receipt.get("phase") != "index_pending"
                    or receipt.get("path") != expected_path
                    or type(receipt.get("sha256")) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", receipt["sha256"]) is None
                    or type(receipt.get("size")) is not int
                    or receipt["size"] <= 0):
                return refused("receipt_invalid")
            source_root = receipt.get("root")
            if type(source_root) is not str:
                return refused("receipt_root_invalid")
            path = Path(expected_path)
            parent = path.parent
            recovering = acquisition_id == self._stable_id(
                "numilog-materialization-recovery", identity, credential, account, item_id,
            )
            purchase_digest = hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:32]
            stage_name = ("recovery-" if recovering else "delivery-") + purchase_digest
            # Only one of eight bounded, exact-purchase Core staging slots.
            # A prior uncheckpointed attempt cannot cause an overwrite;
            # arbitrary nested paths remain invalid.
            approved_stage = (
                str(parent.parent.parent) == source_root
                and re.fullmatch(re.escape(stage_name) + r"(?:-[2-8])?",
                                 parent.parent.name) is not None
            )
            approved_parent = str(parent.parent) == source_root or approved_stage
            if (not path.is_absolute() or not approved_parent
                    or re.fullmatch(r"acsm-([0-9a-f]{64})", parent.name) is None):
                return refused("path_not_canonical")
            acsm_sha = parent.name[5:]
            manual_id = "manual-acsm:" + hashlib.sha256(
                (identity + ":" + acsm_sha).encode("utf-8"),
            ).hexdigest()[:32]
            manual_row = db.execute(
                "SELECT provider_id,credential_id,identity_id,item_id,status,"
                "artifact_id,metadata_json FROM acquisition_ledger "
                "WHERE acquisition_id=?",
                (manual_id,),
            ).fetchone()
            if manual_row is None:
                return refused("manual_receipt_missing")
            m_provider, m_credential, m_identity, m_item, m_status, m_artifact, m_raw = manual_row
            m_meta = self._json_load(m_raw)
            artifact_id = "sha256:" + receipt["sha256"]
            if (m_provider != "numilog" or m_credential != "manual"
                    or m_identity != identity or m_item != acsm_sha
                    or m_status != "complete" or m_artifact != artifact_id
                    or type(m_meta) is not dict
                    or m_meta.get("source_acsm_sha256") != acsm_sha
                    or m_meta.get("path") != expected_path):
                return refused("manual_receipt_mismatch")
            art = db.execute(
                "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            if art != (receipt["sha256"], receipt["size"]):
                return refused("artifact_mismatch")
            placements = db.execute(
                "SELECT placement_id,artifact_id,identity_id,provider_id,"
                "source_item_id,state FROM placements WHERE path=?",
                (expected_path,),
            ).fetchall()
            if len(placements) != 1:
                return refused("placement_missing")
            pid, pa, pi, pp, pitem, state = placements[0]
            if (pa != artifact_id or pi != identity or pp != "numilog"
                    or pitem != acsm_sha or state != "present"):
                return refused("placement_conflict")
            catalogs = db.execute(
                "SELECT catalog_entry_id,artifact_id,identity_id,provider_id,"
                "item_id,local_state FROM catalog_entries WHERE placement_id=? LIMIT 2",
                (pid,),
            ).fetchall()
            if (len(catalogs) != 1
                    or catalogs[0][1:] != (
                        artifact_id, identity, "numilog", acsm_sha, "local"
                    )):
                return refused("catalogue_conflict")
            # Content-addressed artifacts can legitimately back independent
            # copies in several profiles. Adoption changes only this verified
            # placement and its exact catalogue row, never the shared artifact
            # ownership or another profile's provenance. Still reject another
            # sourced placement of the same owner, or an unresolvable owner.
            # A fresh, same-owner, exact purchase delivery justifies an
            # independent OriginLink even if another source has these bytes.
            # Preserve both Placements; an unknown owner remains a refusal.
            ambiguous_owners = (
                "NOT EXISTS (SELECT 1 FROM identities AS i WHERE i.identity_id=p.identity_id)"
                if recovering else
                "(p.identity_id=? OR NOT EXISTS (SELECT 1 FROM identities AS i "
                "WHERE i.identity_id=p.identity_id))"
            )
            other = db.execute(
                "SELECT 1 FROM placements AS p WHERE p.artifact_id=? AND p.path<>? "
                "AND p.source_item_id IS NOT NULL AND " + ambiguous_owners + " LIMIT 1",
                (artifact_id, expected_path) if recovering else
                (artifact_id, expected_path, identity),
            ).fetchone()
            if other is not None:
                return refused("artifact_shared_elsewhere")
            competitors = db.execute(
                "SELECT acquisition_id,identity_id,account_id,status FROM acquisition_ledger WHERE "
                "provider_id='numilog' AND credential_id=? AND item_id=? LIMIT 3",
                (credential, item_id),
            ).fetchall()
            recovering = acquisition_id == self._stable_id(
                "numilog-materialization-recovery", identity, credential, account, item_id,
            )
            if (not competitors or len(competitors) > (2 if recovering else 1)
                    or sum(row[0] == acquisition_id for row in competitors) != 1
                    or any(row[1] != identity or row[2] != account
                           or (row[0] != acquisition_id
                               and (not recovering or row[3] != 'complete'))
                           for row in competitors)):
                return refused("competing_receipts")
            # Both provenance rows change together. A crash/SQL error cannot
            # leave a placement adopted without its matching catalog entry.
            stamp = int(time.time())
            updated = db.execute(
                "UPDATE placements SET source_item_id=?,updated_at=? "
                "WHERE placement_id=? AND source_item_id=? AND artifact_id=?",
                (item_id, stamp, pid, acsm_sha, artifact_id),
            )
            changed = db.execute(
                "UPDATE catalog_entries SET item_id=?,updated_at=? "
                "WHERE catalog_entry_id=? AND item_id=? AND artifact_id=?",
                (item_id, stamp, catalogs[0][0], acsm_sha, artifact_id),
            )
            if updated.rowcount != 1 or changed.rowcount != 1:
                raise ValueError("stale_numilog_acsm_provenance")
            return "adopted" if diagnose is True else True

    def numilog_checkpoint_complete(
        self, acquisition_id: str, *, identity_id: str, credential_id: str,
        account_id: str, item_id: str, path: str, sha256: str, size: int, artifact_id: str,
    ) -> bool:
        """Finalize only the exact delivered bytes and same-owner placement.

        Receipt, artifact, placement and competing ledger rows are compared
        within one SQLite write transaction. No URL/title/provider text enters.
        """
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT acquisition_id,identity_id,account_id,status,metadata_json FROM acquisition_ledger "
                "WHERE provider_id='numilog' AND credential_id=? AND item_id=? LIMIT 2",
                (credential_id, item_id),
            ).fetchall()
            # Recovery is a distinct scoped acquisition. The former complete
            # receipt remains historical evidence; a foreign or in-flight
            # receipt is never silently replaced or taken as our own.
            from .core.numilog_recovery import recovery_id
            recovering = acquisition_id == recovery_id(
                identity_id, credential_id, account_id, item_id,
            )
            if not rows or len(rows) > (2 if recovering else 1):
                return False
            selected = [r for r in rows if r[0] == acquisition_id]
            if len(selected) != 1 or any(
                r[1] != identity_id or r[2] != account_id or
                (r[0] != acquisition_id and (not recovering or r[3] != 'complete'))
                for r in rows
            ):
                return False
            aid, owner, account, status, raw = selected[0]
            meta = self._json_load(raw)
            if (aid != acquisition_id or owner != identity_id or account != account_id
                    or status not in {'acquiring', 'retryable_error', 'attention_required', 'complete'}
                    or meta.get('kind') != 'numilog_delivered_artifact_v1'
                    or meta.get('phase') not in {'index_pending', 'indexed'}
                    or meta.get('path') != path or meta.get('sha256') != sha256
                    or type(meta.get('size')) is not int or meta['size'] != size):
                return False
            artifact = db.execute("SELECT sha256,size FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
            placement = db.execute(
                "SELECT artifact_id,identity_id,provider_id,source_item_id,state FROM placements WHERE path=?",
                (path,),
            ).fetchone()
            if (artifact != (sha256, size)
                    or placement != (artifact_id, identity_id, 'numilog', item_id, 'present')):
                return False
            meta['phase'] = 'indexed'
            db.execute(
                "UPDATE acquisition_ledger SET status='complete',artifact_id=?,metadata_json=?,"
                "error_code=NULL,error_message=NULL,attention_code=NULL,updated_at=? WHERE acquisition_id=?",
                (artifact_id, self._json_dump(meta), int(time.time()), acquisition_id),
            )
            return True

    def acquisition_recover_interrupted(self) -> int:
        now = int(time.time())
        with self._lock, self._connect() as db:
            cur = db.execute(
                "UPDATE acquisition_ledger SET status='retryable_error',error_code='interrupted',"
                "error_message='Acquisition interrompue lors de la précédente exécution',updated_at=? WHERE status='acquiring'",
                (now,),
            )
            return cur.rowcount

    @staticmethod
    def _artifact_row_with_kind(row: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(row.get("metadata") or {})
        artifact_id = str(row.get("artifact_id") or "")
        kind = str(metadata.get("artifact_kind") or ("bundle" if artifact_id.startswith("bundle:") else "file"))
        row["metadata"] = metadata
        row["kind"] = kind if kind in {"file", "bundle"} else "file"
        return row

    def artifact_get(self, artifact_id: str) -> dict[str, Any] | None:
        columns = ["artifact_id", "sha256", "size", "media_format", "mime_type", "metadata_json", "created_at", "updated_at"]
        with self._lock, self._connect() as db:
            record = db.execute(
                "SELECT " + ",".join(columns) + " FROM artifacts WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
        if not record:
            return None
        row = dict(zip(columns, record)); row["metadata"] = self._json_load(row.pop("metadata_json"))
        return self._artifact_row_with_kind(row)

    def artifacts(self) -> list[dict[str, Any]]:
        columns = ["artifact_id", "sha256", "size", "media_format", "mime_type", "metadata_json", "created_at", "updated_at"]
        with self._lock, self._connect() as db:
            values = db.execute("SELECT " + ",".join(columns) + " FROM artifacts ORDER BY created_at,artifact_id").fetchall()
        out: list[dict[str, Any]] = []
        for record in values:
            row = dict(zip(columns, record)); row["metadata"] = self._json_load(row.pop("metadata_json")); out.append(self._artifact_row_with_kind(row))
        return out

    def artifact_put(self, artifact: Any) -> str:
        if isinstance(artifact, dict):
            row = dict(artifact)
        else:
            row = {
                "artifact_id": getattr(artifact, "artifact_id"), "sha256": getattr(artifact, "sha256"),
                "size": getattr(artifact, "size"), "media_format": getattr(artifact, "media_format"),
                "kind": getattr(artifact, "kind", "file"),
                "mime_type": getattr(artifact, "mime_type", None), "metadata": getattr(artifact, "metadata", {}),
            }
        artifact_id = str(row.get("artifact_id") or f"sha256:{row.get('sha256')}")
        metadata = dict(row.get("metadata") or {})
        kind = str(row.get("kind") or metadata.get("artifact_kind") or ("bundle" if artifact_id.startswith("bundle:") else "file"))
        metadata["artifact_kind"] = kind if kind in {"file", "bundle"} else "file"
        now = int(time.time())
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO artifacts(artifact_id,sha256,size,media_format,mime_type,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(artifact_id) DO UPDATE SET size=excluded.size,media_format=excluded.media_format,"
                "mime_type=COALESCE(excluded.mime_type,artifacts.mime_type),metadata_json=excluded.metadata_json,updated_at=excluded.updated_at",
                (artifact_id, str(row.get("sha256") or ""), int(row.get("size") or 0), str(row.get("media_format") or "unknown"),
                 row.get("mime_type"), self._json_dump(metadata), now, now),
            )
        return artifact_id

    def artifact_variant_put(self, item: dict[str, Any]) -> str:
        row = dict(item); now = int(time.time())
        source_sha256 = str(row.get("source_sha256") or "")
        transformer = str(row.get("transformer_plugin_id") or "")
        profile = str(row.get("profile_plugin_id") or "")
        fingerprint = str(row.get("options_fingerprint") or "")
        if not source_sha256 or not transformer or not row.get("artifact_id") or not row.get("source_artifact_id"):
            raise ValueError("artifact variant requires source/artifact/transformer")
        variant_id = str(row.get("variant_id") or self._stable_id("variant", source_sha256, transformer, profile, fingerprint))
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO artifact_variants(variant_id,artifact_id,source_artifact_id,source_sha256,variant_kind,transformer_plugin_id,transformer_version,profile_plugin_id,profile_version,options_fingerprint,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(variant_id) DO UPDATE SET artifact_id=excluded.artifact_id,variant_kind=excluded.variant_kind,"
                "transformer_version=excluded.transformer_version,profile_version=excluded.profile_version,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at",
                (variant_id, str(row["artifact_id"]), str(row["source_artifact_id"]), source_sha256, str(row.get("variant_kind") or "derived"), transformer,
                 str(row.get("transformer_version") or ""), row.get("profile_plugin_id"), row.get("profile_version"), fingerprint, self._json_dump(row.get("metadata")), now, now),
            )
        return variant_id

    def artifact_variants(self, *, source_artifact_id: str | None = None, artifact_id: str | None = None) -> list[dict[str, Any]]:
        columns = ["variant_id","artifact_id","source_artifact_id","source_sha256","variant_kind","transformer_plugin_id","transformer_version","profile_plugin_id","profile_version","options_fingerprint","metadata_json","created_at","updated_at"]
        where=[]; args=[]
        if source_artifact_id is not None: where.append("source_artifact_id=?"); args.append(source_artifact_id)
        if artifact_id is not None: where.append("artifact_id=?"); args.append(artifact_id)
        sql="SELECT "+",".join(columns)+" FROM artifact_variants"
        if where: sql += " WHERE "+" AND ".join(where)
        sql += " ORDER BY created_at,variant_id"
        with self._lock, self._connect() as db: values=db.execute(sql,tuple(args)).fetchall()
        out=[]
        for record in values:
            row=dict(zip(columns,record)); row["metadata"]=self._json_load(row.pop("metadata_json")); out.append(row)
        return out

    def artifact_variant_delete(self, variant_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM artifact_variants WHERE variant_id=?", (str(variant_id),))

    def artifact_variant_retire_atomic(
        self, *, variant_id: str, artifact_id: str,
        source_artifact_id: str, placement_id: str | None,
        placement_path: str | None,
    ) -> None:
        """Retire one checked derivative and its own placement in ONE commit.

        Caller attests the file bytes and loan/identity scope under its
        acquisition lock. SQLite coordinates are rechecked under BEGIN
        IMMEDIATE, including the complete independent-reference guard; if
        any step fails, both the placement and variant remain in SQLite.
        Physical unlink is intentionally done by the caller AFTER commit.
        """
        if (not all(type(v) is str and v for v in
                    (variant_id, artifact_id, source_artifact_id))
                or artifact_id == source_artifact_id
                or (placement_id is None) != (placement_path is None)
                or (placement_id is not None and (
                    type(placement_id) is not str or not placement_id
                    or type(placement_path) is not str or not placement_path))):
            raise ValueError("variant_retire_scope_invalid")
        from .core.duplicate_maintenance_guard import (
            candidate_has_active_dependencies_in_db,
        )
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            variant = db.execute(
                "SELECT variant_id,source_artifact_id FROM artifact_variants "
                "WHERE artifact_id=?", (artifact_id,),
            ).fetchall()
            if variant != [(variant_id, source_artifact_id)]:
                raise ValueError("variant_retire_changed")
            placements = db.execute(
                "SELECT placement_id,path,identity_id,provider_id,"
                "source_item_id,state,metadata_json FROM placements "
                "WHERE artifact_id=?", (artifact_id,),
            ).fetchall()
            if placement_id is None:
                if placements:
                    raise ValueError("variant_retire_placement_changed")
            else:
                if (len(placements) != 1
                        or placements[0][0:2] != (placement_id, placement_path)):
                    raise ValueError("variant_retire_placement_changed")
                _, path, identity, provider, edition, state, raw = placements[0]
                try:
                    meta = json.loads(raw)
                except (TypeError, ValueError, RecursionError):
                    raise ValueError("variant_retire_provenance_invalid") from None
                if (type(meta) is not dict or meta.get("variant") is not True
                        or meta.get("source_artifact_id") != source_artifact_id
                        or state != "present"
                        or any(type(v) is not str or not v
                               for v in (identity, provider, edition))
                        or candidate_has_active_dependencies_in_db(
                            db, placement_id=placement_id, path=path,
                            removable_variant={
                                "artifact_id": artifact_id,
                                "identity_id": identity,
                                "provider_id": provider,
                                "source_item_id": edition,
                                "source_artifact_id": source_artifact_id,
                            },
                        )):
                    raise ValueError("variant_retire_references_changed")
                db.execute(
                    "DELETE FROM catalog_entries WHERE placement_id=?",
                    (placement_id,),
                )
                if db.execute(
                    "DELETE FROM placements WHERE placement_id=? "
                    "AND path=? AND artifact_id=?",
                    (placement_id, path, artifact_id),
                ).rowcount != 1:
                    raise ValueError("variant_retire_placement_changed")
            if db.execute(
                "DELETE FROM artifact_variants WHERE variant_id=? "
                "AND artifact_id=? AND source_artifact_id=?",
                (variant_id, artifact_id, source_artifact_id),
            ).rowcount != 1:
                raise ValueError("variant_retire_changed")
            for purpose in ("read", "export", "transfer"):
                db.execute(
                    "UPDATE metadata SET value='' WHERE key=? AND value=?",
                    (f"artifact.variant.preferred.{source_artifact_id}.{purpose}",
                     artifact_id),
                )

    def artifact_delete_if_unreferenced(self, artifact_id: str) -> bool:
        # A placement is not the only live reference to content-addressed
        # bytes. An unplaced catalogue notice, acquisition receipt or variant
        # with a *different* source can still refer to this artifact. Deleting
        # the authority record would orphan those links in reconciliation.
        artifact_id = str(artifact_id)
        with self._lock, self._connect() as db:
            for table, column in (
                ("placements", "artifact_id"),
                ("catalog_entries", "artifact_id"),
                ("acquisition_ledger", "artifact_id"),
                ("artifact_variants", "source_artifact_id"),
                ("artifact_variants", "artifact_id"),
            ):
                if db.execute(
                    f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1",
                    (artifact_id,),
                ).fetchone():
                    return False
            db.execute("DELETE FROM artifacts WHERE artifact_id=?", (artifact_id,))
            return True

    def artifact_variant_find(self, *, source_sha256: str, transformer_plugin_id: str, profile_plugin_id: str | None = None, options_fingerprint: str = "") -> dict[str, Any] | None:
        profile = str(profile_plugin_id or "")
        for row in self.artifact_variants():
            if str(row.get("source_sha256") or "") == str(source_sha256) and str(row.get("transformer_plugin_id") or "") == str(transformer_plugin_id) and str(row.get("profile_plugin_id") or "") == profile and str(row.get("options_fingerprint") or "") == str(options_fingerprint or ""):
                return row
        return None

    def placement_retire_duplicate_atomic(
        self, *, placement_id: str, keep_placement_id: str,
        candidate_path: str, kept_path: str, artifact_id: str,
        identity_id: str, provider_id: str, source_item_id: str | None,
    ) -> None:
        """Remove one unreferenced duplicate placement in a single SQLite commit.

        The caller hashes both files before entering this method and performs
        the optional physical unlink only AFTER commit. All SQLite scope and
        dependency checks must be repeated under BEGIN IMMEDIATE: otherwise a
        newly attached catalogue/receipt may be cascaded away between the
        broker's precheck and placement deletion.
        """
        if (not all(type(v) is str and v for v in (
                    placement_id, keep_placement_id, candidate_path, kept_path,
                    artifact_id, identity_id, provider_id,
                ))
                or placement_id == keep_placement_id
                or candidate_path == kept_path
                # A missing edition cannot establish two placements'
                # common ownership even if no entitlement exists today.
                or type(source_item_id) is not str or not source_item_id):
            raise ValueError("duplicate_retire_scope_invalid")
        from .core.duplicate_maintenance_guard import (
            candidate_has_active_dependencies_in_db,
        )
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            coordinates = (
                "SELECT path,artifact_id,identity_id,provider_id,"
                "source_item_id,state FROM placements WHERE placement_id=?"
            )
            row = db.execute(coordinates, (placement_id,)).fetchone()
            kept = db.execute(coordinates, (keep_placement_id,)).fetchone()
            expected = (
                candidate_path, artifact_id, identity_id, provider_id,
                source_item_id, "present",
            )
            expected_keep = (
                kept_path, artifact_id, identity_id, provider_id,
                source_item_id, "present",
            )
            if row != expected or kept != expected_keep:
                raise ValueError("duplicate_retire_scope_changed")
            # The keeper/candidate rows omit the credential and borrower:
            # inspect rights for EVERY provider under the same write lock.
            # Older rights without an identity cannot prove the local owner.
            if db.execute(
                "SELECT 1 FROM entitlements WHERE provider_id=? "
                "AND item_id=? AND identity_id IS NULL LIMIT 1",
                (provider_id, source_item_id),
            ).fetchone():
                raise ValueError("duplicate_retire_unscoped_owner")
            owners = set(db.execute(
                "SELECT credential_id,account_id FROM entitlements "
                "WHERE provider_id=? AND identity_id=? AND item_id=?",
                (provider_id, identity_id, source_item_id),
            ))
            if len(owners) > 1:
                raise ValueError("duplicate_retire_ambiguous_owner")
            if candidate_has_active_dependencies_in_db(
                db, placement_id=placement_id, path=candidate_path,
            ):
                raise ValueError("duplicate_retire_references_changed")
            if db.execute(
                "DELETE FROM placements WHERE placement_id=? AND path=? "
                "AND artifact_id=?",
                (placement_id, candidate_path, artifact_id),
            ).rowcount != 1:
                raise ValueError("duplicate_retire_scope_changed")

    def placement_delete(self, placement_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("SAVEPOINT placement_delete")
            try:
                # Only the explicit canonical recovery transaction may remove
                # its pending tombstone after verifying the original inode.
                if db.execute(
                    "SELECT 1 FROM placements WHERE placement_id=? "
                    "AND state='dedup_cleanup_pending'",
                    (str(placement_id),),
                ).fetchone():
                    raise ValueError("dedup_cleanup_pending")
                db.execute("DELETE FROM catalog_entries WHERE placement_id=?", (str(placement_id),))
                db.execute("DELETE FROM placements WHERE placement_id=?", (str(placement_id),))
                db.execute("RELEASE SAVEPOINT placement_delete")
            except Exception:
                db.execute("ROLLBACK TO SAVEPOINT placement_delete"); db.execute("RELEASE SAVEPOINT placement_delete"); raise

    def catalog_entry_update_metadata(self, catalog_entry_id: str, metadata: dict[str, Any]) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE catalog_entries SET metadata_json=?,updated_at=? WHERE catalog_entry_id=?", (self._json_dump(metadata), int(time.time()), str(catalog_entry_id)))

    def placement_get(self, placement_id: str) -> dict[str, Any] | None:
        columns = ["placement_id", "artifact_id", "path", "identity_id", "provider_id", "source_item_id", "state", "last_seen_at", "metadata_json", "created_at", "updated_at"]
        with self._lock, self._connect() as db:
            record = db.execute(
                "SELECT " + ",".join(columns) + " FROM placements WHERE placement_id=?", (placement_id,)
            ).fetchone()
        if not record:
            return None
        row = dict(zip(columns, record)); row["metadata"] = self._json_load(row.pop("metadata_json")); return row

    def placement_put(self, item: dict[str, Any]) -> str:
        row = dict(item); now = int(time.time())
        path = str(Path(str(row.get("path") or "")).expanduser().resolve())
        placement_id = str(row.get("placement_id") or self._stable_id("placement", path))
        with self._lock, self._connect() as db:
            # Inserting an already known path must neither transfer its
            # account/provider/edition nor return a non-existent placement ID.
            # Lock the read + upsert against concurrent SQLite writers.
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT placement_id,identity_id,provider_id,source_item_id,state "
                "FROM placements WHERE path=?", (path,),
            ).fetchone()
            if current is not None:
                # A post-placement dedup tombstone is not a materialization.
                # Do not resurrect or overwrite its original inode evidence
                # while an explicit recovery is still pending.
                if current[4] == "dedup_cleanup_pending":
                    raise ValueError("dedup_cleanup_pending")
                for stored, proposed in zip(
                    current[1:4],
                    (row.get("identity_id"), row.get("provider_id"),
                     row.get("source_item_id")),
                ):
                    if stored and proposed and stored != proposed:
                        raise ValueError("placement_scope_conflict")
            db.execute(
                "INSERT INTO placements(placement_id,artifact_id,path,identity_id,provider_id,source_item_id,state,last_seen_at,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET artifact_id=excluded.artifact_id,identity_id=COALESCE(excluded.identity_id,placements.identity_id),"
                "provider_id=COALESCE(excluded.provider_id,placements.provider_id),source_item_id=COALESCE(excluded.source_item_id,placements.source_item_id),"
                "state=excluded.state,last_seen_at=excluded.last_seen_at,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at",
                (placement_id, row.get("artifact_id"), path, row.get("identity_id"), row.get("provider_id"), row.get("source_item_id"),
                 str(row.get("state") or "present"), row.get("last_seen_at"), self._json_dump(row.get("metadata")), now, now),
            )
            if str(row.get("state") or "present") == "invalid":
                # A corrupted/unreadable file must not remain presented as a
                # locally available catalogue entry after its placement was
                # invalidated. Keep this transition in the SAME SQLite commit,
                # including when ON CONFLICT(path) selected an existing ID.
                db.execute(
                    "UPDATE catalog_entries SET local_state='invalid',updated_at=? "
                    "WHERE placement_id=(SELECT placement_id FROM placements WHERE path=?)",
                    (now, path),
                )
            # ON CONFLICT(path) keeps the original primary key; callers must
            # receive that actual key rather than their proposed new one.
            canonical = db.execute(
                "SELECT placement_id FROM placements WHERE path=?", (path,),
            ).fetchone()
            return str(canonical[0])

    def placements(self, *, state: str | None = None) -> list[dict[str, Any]]:
        columns = ["placement_id", "artifact_id", "path", "identity_id", "provider_id", "source_item_id", "state", "last_seen_at", "metadata_json", "created_at", "updated_at"]
        sql = "SELECT " + ",".join(columns) + " FROM placements"
        args: tuple[Any, ...] = ()
        if state is not None:
            sql += " WHERE state=?"; args = (state,)
        sql += " ORDER BY path"
        with self._lock, self._connect() as db:
            values = db.execute(sql, args).fetchall()
        out=[]
        for record in values:
            row=dict(zip(columns,record)); row["metadata"]=self._json_load(row.pop("metadata_json")); out.append(row)
        return out

    def placements_mark_missing(
        self, root: Path, seen_paths: set[str], *, seen_at: int | None = None,
        candidate_placement_ids: set[str] | None = None,
    ) -> int:
        root_resolved = root.expanduser().resolve()
        now = int(seen_at or time.time())
        changed = 0
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT placement_id,path,state FROM placements").fetchall()
            for placement_id, path_text, state in rows:
                # A concurrent acquisition may have created or reactivated
                # this row after the scan started. It is not a valid negative
                # candidate for a snapshot that predates its presence.
                if (candidate_placement_ids is not None
                        and placement_id not in candidate_placement_ids):
                    continue
                try:
                    # Do not resolve the *stored* leaf or its parents here:
                    # a previously indexed book replaced by a symlink could
                    # now point outside the root and incorrectly retain its
                    # old "present" attestation. The scan's verified
                    # seen_paths are physical paths; only lexical membership
                    # of an already stored absolute path is needed.
                    path = Path(str(path_text))
                    if (not path.is_absolute() or ".." in path.parts
                            or path == root_resolved):
                        continue
                    path.relative_to(root_resolved)
                except (OSError, ValueError):
                    continue
                # A pending dedup tombstone is intentionally absent from the
                # indexed library. A scan must never turn its recovery
                # checkpoint into a generic missing placement.
                if (str(path) not in seen_paths
                        and state not in ("missing", "dedup_cleanup_pending")):
                    # The scan's SELECT may predate a concurrent SQLite
                    # tombstone commit. Check state AND path atomically when
                    # writing, and never rewrite a recovery tombstone.
                    updated = db.execute(
                        "UPDATE placements SET state='missing',updated_at=? "
                        "WHERE placement_id=? AND path=? AND state=? "
                        "AND state<>'dedup_cleanup_pending'",
                        (now, placement_id, path_text, state),
                    )
                    if updated.rowcount != 1:
                        continue
                    db.execute(
                        "UPDATE catalog_entries SET local_state='missing',updated_at=? WHERE placement_id=?", (now, placement_id)
                    )
                    changed += 1
        return changed

    def catalog_entry_put(self, item: dict[str, Any]) -> str:
        row = dict(item); now = int(time.time())
        entry_id = str(row.get("catalog_entry_id") or self._stable_id("catalog", row.get("placement_id"), row.get("artifact_id"), row.get("item_id")))
        with self._lock, self._connect() as db:
            # A local catalog entry's ID must not silently move its book to a
            # different physical placement, identity, provider or edition.
            # Explicit relocation/reconciliation use their own reviewed paths.
            # Remote-only entries (without a placement) retain their normal
            # provider-catalog refresh semantics.
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT placement_id,identity_id,provider_id,item_id "
                "FROM catalog_entries WHERE catalog_entry_id=?", (entry_id,),
            ).fetchone()
            if current is not None and current[0]:
                if row.get("placement_id") != current[0]:
                    raise ValueError("catalog_entry_scope_conflict")
                for stored, proposed in zip(
                    current[1:],
                    (row.get("identity_id"), row.get("provider_id"),
                     row.get("item_id")),
                ):
                    if stored and proposed and stored != proposed:
                        raise ValueError("catalog_entry_scope_conflict")
            db.execute(
                "INSERT INTO catalog_entries(catalog_entry_id,artifact_id,placement_id,title,author,media_format,identity_id,provider_id,item_id,local_state,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(catalog_entry_id) DO UPDATE SET artifact_id=excluded.artifact_id,placement_id=excluded.placement_id,"
                "title=excluded.title,author=COALESCE(excluded.author,catalog_entries.author),media_format=excluded.media_format,identity_id=COALESCE(excluded.identity_id,catalog_entries.identity_id),"
                "provider_id=COALESCE(excluded.provider_id,catalog_entries.provider_id),item_id=COALESCE(excluded.item_id,catalog_entries.item_id),"
                "local_state=excluded.local_state,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at",
                (entry_id, row.get("artifact_id"), row.get("placement_id"), str(row.get("title") or "Sans titre"), row.get("author"),
                 row.get("media_format"), row.get("identity_id"), row.get("provider_id"), row.get("item_id"),
                 str(row.get("local_state") or "local"), self._json_dump(row.get("metadata")), now, now),
            )
        return entry_id

    def catalog_entry_get(self, catalog_entry_id: str) -> dict[str, Any] | None:
        columns = ["catalog_entry_id", "artifact_id", "placement_id", "title", "author", "media_format", "identity_id", "provider_id", "item_id", "local_state", "metadata_json", "created_at", "updated_at"]
        with self._lock, self._connect() as db:
            record = db.execute(
                "SELECT " + ",".join(columns) + " FROM catalog_entries WHERE catalog_entry_id=?",
                (str(catalog_entry_id),),
            ).fetchone()
        if record is None:
            return None
        row = dict(zip(columns, record)); row["metadata"] = self._json_load(row.pop("metadata_json")); return row

    def catalog_entries_by_ids(self, catalog_entry_ids: list[str] | tuple[str, ...] | set[str]) -> dict[str, dict[str, Any]]:
        """Fetch a catalogue subset without scanning the complete table."""
        ids = list(dict.fromkeys(str(value) for value in catalog_entry_ids if str(value)))
        if not ids:
            return {}
        columns = ["catalog_entry_id", "artifact_id", "placement_id", "title", "author", "media_format", "identity_id", "provider_id", "item_id", "local_state", "metadata_json", "created_at", "updated_at"]
        records: list[tuple[Any, ...]] = []
        with self._lock, self._connect() as db:
            for offset in range(0, len(ids), 800):
                chunk = ids[offset:offset + 800]
                placeholders = ",".join("?" for _ in chunk)
                records.extend(db.execute(
                    "SELECT " + ",".join(columns) + f" FROM catalog_entries WHERE catalog_entry_id IN ({placeholders})",
                    tuple(chunk),
                ).fetchall())
        out: dict[str, dict[str, Any]] = {}
        for record in records:
            row = dict(zip(columns, record)); row["metadata"] = self._json_load(row.pop("metadata_json")); out[str(row["catalog_entry_id"])] = row
        return out

    def catalog_entries_upsert_many(self, items: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[str]:
        """Upsert a catalogue batch in one transaction.

        This mirrors ``catalog_entry_put`` semantics but avoids one SQLite
        transaction per provider result during progressive searches/sync.
        """
        if not items:
            return []
        now = int(time.time())
        rows: list[tuple[Any, ...]] = []
        ids: list[str] = []
        for item in items:
            row = dict(item)
            entry_id = str(row.get("catalog_entry_id") or self._stable_id("catalog", row.get("placement_id"), row.get("artifact_id"), row.get("item_id")))
            ids.append(entry_id)
            rows.append((
                entry_id, row.get("artifact_id"), row.get("placement_id"), str(row.get("title") or "Sans titre"), row.get("author"),
                row.get("media_format"), row.get("identity_id"), row.get("provider_id"), row.get("item_id"),
                str(row.get("local_state") or "local"), self._json_dump(row.get("metadata")), now, now,
            ))
        sql = (
            "INSERT INTO catalog_entries(catalog_entry_id,artifact_id,placement_id,title,author,media_format,identity_id,provider_id,item_id,local_state,metadata_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(catalog_entry_id) DO UPDATE SET artifact_id=excluded.artifact_id,placement_id=excluded.placement_id,"
            "title=excluded.title,author=COALESCE(excluded.author,catalog_entries.author),media_format=excluded.media_format,identity_id=COALESCE(excluded.identity_id,catalog_entries.identity_id),"
            "provider_id=COALESCE(excluded.provider_id,catalog_entries.provider_id),item_id=COALESCE(excluded.item_id,catalog_entries.item_id),"
            "local_state=excluded.local_state,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at"
        )
        with self._lock, self._connect() as db:
            # Batch writes have the same ownership contract as
            # catalog_entry_put. Validate the complete batch before SQL writes
            # so one conflicting local row cannot partially publish others.
            db.execute("BEGIN IMMEDIATE")
            staged: dict[str, tuple[Any, Any, Any, Any]] = {}
            unique_ids = list(dict.fromkeys(ids))
            for offset in range(0, len(unique_ids), 800):
                chunk = unique_ids[offset:offset + 800]
                placeholders = ",".join("?" for _ in chunk)
                for record in db.execute(
                    "SELECT catalog_entry_id,placement_id,identity_id,provider_id,item_id "
                    f"FROM catalog_entries WHERE catalog_entry_id IN ({placeholders}) "
                    "AND placement_id IS NOT NULL", tuple(chunk),
                ):
                    staged[str(record[0])] = tuple(record[1:])
            for raw in rows:
                entry_id = str(raw[0])
                current = staged.get(entry_id)
                proposed = (raw[2], raw[6], raw[7], raw[8])
                if current is not None and current[0]:
                    if proposed[0] != current[0] or any(
                        stored and new and stored != new
                        for stored, new in zip(current[1:], proposed[1:])
                    ):
                        raise ValueError("catalog_entry_scope_conflict")
                # Even repeated IDs within one batch must not transfer the
                # placement introduced by an earlier row of that same batch.
                staged[entry_id] = (
                    proposed[0],
                    proposed[1] or (current[1] if current else None),
                    proposed[2] or (current[2] if current else None),
                    proposed[3] or (current[3] if current else None),
                )
            db.executemany(sql, rows)
        return ids

    def catalog_entries(
        self, *, state: str | None = None, identity_id: str | None = None,
        provider_id: str | None = None, limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read optional exact scopes before loading/decoding catalogue rows."""
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 100_001):
            raise ValueError("invalid catalogue read limit")
        columns = ["catalog_entry_id", "artifact_id", "placement_id", "title", "author", "media_format", "identity_id", "provider_id", "item_id", "local_state", "metadata_json", "created_at", "updated_at"]
        sql = "SELECT " + ",".join(columns) + " FROM catalog_entries"
        conditions = []
        args: list[Any] = []
        for column, value in (
            ("local_state", state), ("identity_id", identity_id), ("provider_id", provider_id),
        ):
            if value is not None:
                if type(value) is not str:
                    raise ValueError("invalid catalogue scope")
                conditions.append(column + "=?")
                args.append(value)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY title COLLATE NOCASE, catalog_entry_id"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        with self._lock, self._connect() as db:
            values = db.execute(sql, args).fetchall()
        out=[]
        for record in values:
            row=dict(zip(columns,record)); row["metadata"]=self._json_load(row.pop("metadata_json")); out.append(row)
        return out
