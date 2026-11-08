"""Offline canonical Kobo relink facade, with historical W4 paths retired.

No 0.7 receipt or local-file observation is an acquisition or a right.
Retired SQL and filesystem repair helpers live in test-only archives.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

from ..library_files import safe_library_component
from ..paths import config_file, database_file
from ..identities import Identity
from ..storage import Storage, CURRENT_SCHEMA_VERSION


class ReconciliationRejected(ValueError):
    """A fixed public reason, never a provider exception or private target."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _require(condition, code):
    if not condition:
        raise ReconciliationRejected(code)


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
    # The W4 file-only journal does not establish canonical provider A or B.
    # It is retired before any profile, SQLite or file access.
    _require(not file_only, "legacy_file_observation_retired")
    _require(canonical_relink, "legacy_reconciliation_retired")
    # Only an independently attested canonical Placement may be relinked.
    provided = set(options)
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
    except (OSError, sqlite3.Error, json.JSONDecodeError, UnicodeError):
        raise ReconciliationRejected("local_profile_unavailable") from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass  # Preserve the already decided transaction receipt.
