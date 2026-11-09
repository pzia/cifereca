"""Durable Core-owned USB copy/removal journal in the existing Storage SQLite DB.

No pickle, no supplementary device database, no plugin writes. This is the
persistence backend required by the USB supervisors and their recovery paths.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3
from typing import Any

from .device_usb_copy_admission import UsbCopyConsent, UsbCopyRightsEvidence
from .device_usb_copy_recovery import UsbCopyRecoveryPin
from .device_usb_copy_supervisor import UsbCopyJournalEntry
from .device_usb_preview import UsbManagedReceipt
from .device_usb_removal_review import UsbRemovalConsent
from .device_usb_removal_execute import UsbRemovalJournalEntry


_SCHEMA = """
CREATE TABLE IF NOT EXISTS device_usb_operations (
 operation_id TEXT PRIMARY KEY,
 device_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL,
 target_path TEXT NOT NULL,
 phase TEXT NOT NULL,
 payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS device_usb_pending_target
 ON device_usb_operations(fingerprint, target_path)
 WHERE phase != 'completed';
"""


def _encode(entry: UsbCopyJournalEntry | UsbRemovalJournalEntry) -> str:
    if type(entry) is UsbCopyJournalEntry:
        kind = "copy"
    elif type(entry) is UsbRemovalJournalEntry:
        kind = "remove"
    else:
        raise ValueError("usb_journal_entry_invalid")
    payload = {"schema": 1, "kind": kind, "entry": asdict(entry)}
    result = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=True, allow_nan=False)
    if len(result) > 65536:
        raise ValueError("usb_journal_entry_too_large")
    return result


def _decode(payload: str) -> UsbCopyJournalEntry | UsbRemovalJournalEntry:
    if type(payload) is not str or len(payload) > 65536:
        raise ValueError("usb_journal_payload_invalid")
    try:
        document = json.loads(payload)
        if (type(document) is not dict or document.get("schema") != 1
                or type(document.get("entry")) is not dict):
            raise ValueError("usb_journal_payload_invalid")
        data = document["entry"]
        kind = document.get("kind")
        if kind == "remove":
            return UsbRemovalJournalEntry(
                consent=UsbRemovalConsent(**data["consent"]),
                phase=data["phase"],
            )
        if kind != "copy":
            raise ValueError("usb_journal_kind_invalid")
        pin = data.get("pin")
        if pin is not None:
            pin = dict(pin)
            pin["directory_inode"] = tuple(pin["directory_inode"])
            pin["stage_version"] = tuple(pin["stage_version"])
            pin = UsbCopyRecoveryPin(**pin)
        receipt = data.get("receipt")
        if receipt is not None:
            receipt = UsbManagedReceipt(**receipt)
        version = data.get("final_version")
        if version is not None:
            version = tuple(version)
        return UsbCopyJournalEntry(
            consent=UsbCopyConsent(**data["consent"]),
            rights=UsbCopyRightsEvidence(**data["rights"]),
            size=data["size"], phase=data["phase"],
            pin=pin, receipt=receipt, final_version=version,
        )
    except (TypeError, KeyError, ValueError, AttributeError):
        raise ValueError("usb_journal_payload_invalid") from None


class SqliteUsbOperationJournal:
    """Atomic insert/CAS in the canonical Storage database.

    A completed operation is immutable. The pending target uniqueness rule
    serializes copy/removal attempts even across independent worker processes.
    """

    def __init__(self, storage: Any) -> None:
        self.storage = storage

    def _initialize(self, db: sqlite3.Connection) -> None:
        db.executescript(_SCHEMA)

    def get(self, operation_id: str) -> UsbCopyJournalEntry | UsbRemovalJournalEntry | None:
        if type(operation_id) is not str or not operation_id:
            return None
        with self.storage._lock, self.storage._connect() as db:
            try:
                row = db.execute(
                    "SELECT payload FROM device_usb_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
            except sqlite3.OperationalError as error:
                if "no such table" in str(error).lower():
                    return None
                raise
            return _decode(row[0]) if row is not None else None

    def begin(self, entry: UsbCopyJournalEntry | UsbRemovalJournalEntry) -> bool:
        payload = _encode(entry)
        if entry.phase != "pending":
            return False
        consent = entry.consent
        with self.storage._lock, self.storage._connect() as db:
            self._initialize(db)
            db.execute("BEGIN IMMEDIATE")
            result = db.execute(
                "INSERT OR IGNORE INTO device_usb_operations "
                "(operation_id, device_id, fingerprint, target_path, phase, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (consent.operation_id, consent.device_id,
                 consent.usb_fingerprint, consent.target_relative_path.casefold(),
                 entry.phase, payload),
            )
            return result.rowcount == 1

    def compare_and_set(
        self, before: UsbCopyJournalEntry | UsbRemovalJournalEntry,
        after: UsbCopyJournalEntry | UsbRemovalJournalEntry,
    ) -> bool:
        if (type(before) is not type(after)
                or before.consent != after.consent
                or before.phase == "completed"):
            return False
        old, new = _encode(before), _encode(after)
        with self.storage._lock, self.storage._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute(
                "UPDATE device_usb_operations SET phase=?, payload=? "
                "WHERE operation_id=? AND phase=? AND payload=?",
                (after.phase, new, before.consent.operation_id,
                 before.phase, old),
            )
            return result.rowcount == 1


__all__ = ["SqliteUsbOperationJournal"]
