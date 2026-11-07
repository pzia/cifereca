"""Retired W4 transaction, deliberately stored outside installable Core.

For regression fixtures only. Never import this archive from application or
plugin code. Bind the old function's symbol table to the tested Core module
so monkeypatch-based WAL/rollback fixtures continue testing identical
historical SQL and filesystem invariants, without shipping an executable
positive reconciliation entry in production.
"""
from __future__ import annotations

from types import FunctionType

from cifereca.core import kobo_local_reconciliation as _runtime


def _bind_to_runtime_globals(func):
    rebound = FunctionType(
        func.__code__, vars(_runtime), func.__name__, func.__defaults__,
        func.__closure__,
    )
    rebound.__kwdefaults__ = dict(func.__kwdefaults__ or {})
    return rebound


@_bind_to_runtime_globals
def historical_reconcile_kobo_local(service, *, credential_id, account_id, item_id, expected_sha256,
                                     path=None, confirmation=None, undo_operation=None, identity_id=None,
                                     cancelled=lambda: False, on_committed=None):
    """ARCHIVE TEST-ONLY: historical reversible file-link transaction.

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
            if record is None:
                # Same durable transaction as the reversible 0.7 relink:
                # no partial evidence record can survive failed SQL/FS checks.
                _record_offline_file_evidence(
                    db, operation, intent, before["backup_items"], evidence,
                )
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

