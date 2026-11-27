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
            rights=(None if data["rights"] is None else UsbCopyRightsEvidence(**data["rights"])),
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


_MAX_PREVIEW_OPERATIONS = 1000


def completed_usb_managed_receipts(
    *, storage: Any, identity_id: str, device_id: str, fingerprint: str,
) -> dict[str, UsbManagedReceipt]:
    """Read exact *past* USB writes for a fresh advisory removal preview.

    A completed journal record is historical provenance, NOT current presence
    or a delete capability. The independent physical scan must also observe
    the matching bytes/path before a removal candidate may be displayed.
    Pending or completed removals invalidate their original copy receipt.
    """
    from .device_usb_copy_supervisor import UsbCopyJournalEntry, _coordinates
    from .device_usb_preview import _sha

    if (any(type(value) is not str or not value or len(value) > 128
            for value in (identity_id, device_id, fingerprint))
            or storage is None):
        return {}
    try:
        # Do not initialize the journal or mutate SQLite during an advisory
        # projection. The table might legitimately not exist yet.
        with storage._lock, storage._connect() as db:
            rows = db.execute(
                "SELECT operation_id, payload, phase, target_path, fingerprint "
                "FROM device_usb_operations WHERE device_id=? "
                "ORDER BY operation_id LIMIT ?",
                (device_id, _MAX_PREVIEW_OPERATIONS + 1),
            ).fetchall()
        if len(rows) > _MAX_PREVIEW_OPERATIONS:
            return {}
        copies: dict[str, list[tuple[str, UsbManagedReceipt]]] = {}
        retired: set[str] = set()
        pending_targets: set[str] = set()
        for operation_id, payload, phase, target_path, stored_fingerprint in rows:
            if (type(operation_id) is not str or type(payload) is not str
                    or type(phase) is not str or type(target_path) is not str
                    or type(stored_fingerprint) is not str):
                return {}
            entry = _decode(payload)
            consent = entry.consent
            if (consent.operation_id != operation_id
                    or consent.device_id != device_id
                    or consent.usb_fingerprint != stored_fingerprint
                    or consent.target_relative_path.casefold() != target_path
                    or entry.phase != phase):
                return {}
            if stored_fingerprint != fingerprint:
                continue
            if phase != "completed":
                pending_targets.add(target_path)
            if type(entry) is UsbRemovalJournalEntry:
                if consent.identity_id == identity_id:
                    retired.add(consent.copy_operation_id)
                continue
            if type(entry) is not UsbCopyJournalEntry or phase != "completed":
                continue
            rights = entry.rights
            receipt = entry.receipt
            pin = entry.pin
            digest = consent.sha256
            if (consent.identity_id != identity_id
                    or type(consent.artifact_id) is not str
                    or not _sha(digest)
                    or consent.artifact_id != "sha256:" + digest
                    or (rights is not None and (
                        type(rights) is not UsbCopyRightsEvidence
                        or rights.personal_usb_copy_permitted is not True
                        or _coordinates(rights) != _coordinates(consent)))
                    or type(receipt) is not UsbManagedReceipt
                    or receipt != UsbManagedReceipt(
                        identity_id, device_id, fingerprint,
                        consent.target_relative_path, digest,
                    )
                    or type(pin) is not UsbCopyRecoveryPin
                    or (pin.identity_id, pin.device_id, pin.usb_fingerprint)
                       != (identity_id, device_id, fingerprint)
                    or (pin.target_relative_path, pin.sha256, pin.size)
                       != (consent.target_relative_path, digest, entry.size)
                    or pin.staged_name != ".cifereca-usb-" + operation_id + ".part"
                    or type(entry.size) is not int or entry.size <= 0
                    or type(pin.stage_version) is not tuple
                    or len(pin.stage_version) != 5
                    or type(entry.final_version) is not tuple
                    or len(entry.final_version) != 5
                    or entry.final_version[:-1] != pin.stage_version[:-1]):
                # A corrupted completed proof is not a reason to erase other
                # devices, but is never an admissible matching receipt.
                continue
            copies.setdefault(consent.artifact_id, []).append((operation_id, receipt))

        result: dict[str, UsbManagedReceipt] = {}
        for artifact_id, options in copies.items():
            valid = [
                receipt for operation_id, receipt in options
                if operation_id not in retired
                and receipt.relative_path.casefold() not in pending_targets
            ]
            # No arbitrary 'latest' winner across multiple completed copies.
            if len(valid) == 1:
                result[artifact_id] = valid[0]
        return result
    except (sqlite3.Error, OSError, ValueError, TypeError, AttributeError,
            KeyError, OverflowError):
        # Malformed SQLite state or a stale file never grants an operation.
        return {}



def completed_usb_managed_copy_operations(
    *, storage: Any, identity_id: str, device_id: str, fingerprint: str,
) -> dict[str, str]:
    """Return exact completed copy operation IDs, never execution permits.

    The shared Core receipt selector owns ambiguity, pending removal, and
    malformed-payload rejection. This read-only accessor adds the opaque
    operation ID needed to prepare a *separate* one-book removal confirmation.
    Nothing here proves the USB file still exists, is unchanged, or can be
    deleted; the physical preview and removal supervisor must reattest it.
    """
    from .device_usb_copy_supervisor import UsbCopyJournalEntry
    import re

    receipts = completed_usb_managed_receipts(
        storage=storage, identity_id=identity_id, device_id=device_id,
        fingerprint=fingerprint,
    )
    if not receipts:
        return {}
    try:
        with storage._lock, storage._connect() as db:
            records = db.execute(
                "SELECT operation_id, payload, phase FROM device_usb_operations "
                "WHERE device_id=? ORDER BY operation_id LIMIT ?",
                (device_id, _MAX_PREVIEW_OPERATIONS + 1),
            ).fetchall()
        if len(records) > _MAX_PREVIEW_OPERATIONS:
            return {}
        matched: dict[str, list[str]] = {}
        for operation_id, payload, phase in records:
            if (type(operation_id) is not str
                    or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None
                    or type(payload) is not str or type(phase) is not str):
                return {}
            entry = _decode(payload)
            if (type(entry) is not UsbCopyJournalEntry
                    or phase != "completed" or entry.phase != "completed"):
                continue
            consent = entry.consent
            if (consent.operation_id != operation_id
                    or consent.identity_id != identity_id
                    or consent.device_id != device_id
                    or consent.usb_fingerprint != fingerprint):
                continue
            receipt = receipts.get(consent.artifact_id)
            if (receipt is not None
                    and entry.receipt == receipt
                    and consent.target_relative_path == receipt.relative_path
                    and consent.sha256 == receipt.sha256):
                matched.setdefault(consent.artifact_id, []).append(operation_id)
        # Detect journal mutations while producing the advisory lookup.
        if completed_usb_managed_receipts(
            storage=storage, identity_id=identity_id,
            device_id=device_id, fingerprint=fingerprint,
        ) != receipts:
            return {}
        return {
            artifact_id: options[0]
            for artifact_id, options in matched.items()
            if len(options) == 1
        }
    except (sqlite3.Error, OSError, ValueError, TypeError, AttributeError,
            KeyError, OverflowError):
        return {}


__all__ = [
    "SqliteUsbOperationJournal", "completed_usb_managed_receipts",
    "completed_usb_managed_copy_operations",
]
