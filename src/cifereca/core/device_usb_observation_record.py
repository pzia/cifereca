"""P1 #2854: supervised *observation* of an exact USB reader, never delivery.

Only an explicit worker invocation may call this recorder. The Core Linux
scanner independently validates USB block UUID, filesystem and all managed
file hashes. Recording a point-in-time presence/absence is not a right to
copy, a managed-write receipt or an executable removal authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import time

from .device_usb_managed_scan import scan_managed_usb
from .device_usb_preview import _fresh_scan, _relative, _sha


_MAX_INTENTS = 1000
_BINDING_SQL = (
    "SELECT device_id,identity_id,label,enabled,metadata_json,"
    "created_at,updated_at,target_directory FROM device_bindings "
    "WHERE device_id=?"
)
_INTENTS_SQL = (
    "SELECT device_placement_id,device_id,artifact_id,desired_state,"
    "observed_state,target_relative_path,last_observed_at,metadata_json,"
    "created_at,updated_at FROM device_placements "
    "WHERE device_id=? ORDER BY artifact_id LIMIT ?"
)


@dataclass(frozen=True, slots=True)
class UsbRecordedObservation:
    status: str
    reason: str
    updated: int = 0
    unchanged: int = 0
    unresolved: int = 0
    # Never exposes a write capability, even after recording "present".
    executable: bool = False


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate USB metadata field")
        result[key] = value
    return result


def _metadata(raw: object) -> dict:
    if type(raw) is not str or len(raw) > 65536:
        raise ValueError("invalid USB metadata")
    value = json.loads(raw, object_pairs_hook=_unique)
    if type(value) is not dict:
        raise ValueError("invalid USB metadata")
    return value


def _snapshot(db, identity_id: str, device_id: str):
    binding = db.execute(_BINDING_SQL, (device_id,)).fetchone()
    intents = db.execute(
        _INTENTS_SQL, (device_id, _MAX_INTENTS + 1),
    ).fetchall()
    if (binding is None or len(intents) > _MAX_INTENTS
            or not intents or binding[0:2] != (device_id, identity_id)
            or binding[3] != 1 or type(binding[7]) is not str
            or not _relative(binding[7])):
        raise ValueError("usb_scope_unverified")
    metadata = _metadata(binding[4])
    fingerprint = metadata.get("usb_fingerprint")
    if (type(fingerprint) is not str or not 8 <= len(fingerprint) <= 128
            or any(ch not in "abcdefghijklmnopqrstuvwxyz"
                   "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
                   for ch in fingerprint)):
        raise ValueError("usb_fingerprint_unverified")
    scope = {
        "device_id": device_id, "identity_id": identity_id,
        "enabled": True, "target_directory": binding[7],
        "metadata": metadata,
    }
    placements = []
    seen: set[str] = set()
    seen_targets: set[str] = set()
    for row in intents:
        placement_id, d_id, artifact_id, desired, observed, target, ts, raw_meta, *_ = row
        if (type(placement_id) is not str or not placement_id
                or d_id != device_id or type(artifact_id) is not str
                or not artifact_id or artifact_id in seen
                or desired not in ("keep", "remove")
                or observed not in ("present", "missing", "unknown")
                or (ts is not None and (type(ts) is not int or ts <= 0))
                or target is not None and (
                    not _relative(target)
                    or not target.startswith(binding[7] + "/"))):
            raise ValueError("usb_intent_unverified")
        seen.add(artifact_id)
        meta = _metadata(raw_meta)
        if meta.get("intent_identity_id") != identity_id:
            raise ValueError("usb_intent_foreign")
        if target is not None:
            if target.casefold() in seen_targets:
                raise ValueError("usb_target_collision")
            seen_targets.add(target.casefold())
        placements.append({
            "device_id": device_id, "artifact_id": artifact_id,
            "target_relative_path": target, "metadata": meta,
        })
    # Read artifact SHA evidence in the same SQLite snapshot. No filenames,
    # catalogue/rights metadata or identity-inferred provider is authority.
    artifact_sha = {}
    for row in intents:
        artifact_id = row[2]
        record = db.execute(
            "SELECT sha256 FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if record is not None and type(record[0]) is str and _sha(record[0]):
            if artifact_id == "sha256:" + record[0]:
                artifact_sha[artifact_id] = record[0]
    return binding, tuple(intents), scope, tuple(placements), artifact_sha


def record_verified_usb_observation(
    *, storage, identity_id: str, device_id: str,
    mountpoint: Path, now: int | None = None,
) -> UsbRecordedObservation:
    """Record only proven physical facts on *explicit request*.

    No GTK render, ordinary Sync or metadata getter invokes this operation.
    Optimistic SQLite data_version/total_changes + a transactionally reread
    raw scope reject stale snapshot/ABA while a potentially long USB scan
    runs. An observed target is never a deletion receipt.
    """
    if (type(identity_id) is not str or not identity_id
            or type(device_id) is not str or not device_id
            or not isinstance(mountpoint, Path) or not mountpoint.is_absolute()
            or ".." in mountpoint.parts
            or (now is not None and (type(now) is not int or now <= 0))):
        return UsbRecordedObservation("blocked", "invalid_request")
    try:
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            original = _snapshot(db, identity_id, device_id)
            version = db.execute("PRAGMA data_version").fetchone()[0]
            changes = db.total_changes
        binding, intents, scan_binding, placements, hashes = original
        snapshot = scan_managed_usb(
            identity_id=identity_id, binding=scan_binding,
            mountpoint=mountpoint, placements=placements,
        )
        checked_at = int(time.time()) if now is None else now
        fingerprint = scan_binding["metadata"]["usb_fingerprint"]
        if not _fresh_scan(
            snapshot, identity_id=identity_id,
            device_id=device_id, fingerprint=fingerprint, now=checked_at,
            max_age_seconds=300,
        ):
            return UsbRecordedObservation("blocked", "usb_scan_unverified")

        observed_by_path = {
            f.relative_path.casefold(): (key, f)
            for key, f in snapshot.files.items()
        }
        transitions: list[tuple[str, str, int]] = []
        unresolved = unchanged = 0
        for row in intents:
            placement_id, _, artifact_id, _, old, target, previous, *_ = row
            expected = hashes.get(artifact_id)
            if target is None or expected is None:
                unresolved += 1
                continue
            found = observed_by_path.get(target.casefold())
            if found is None and (
                artifact_id in snapshot.files
                or any(entry.sha256 == expected
                       for entry in snapshot.files.values())
            ):
                # The registered Artifact or identical bytes appear at a
                # different managed path. This is an unresolved relocation,
                # NOT authority to call this book absent or to recopy it.
                unresolved += 1
                continue
            if found is None:
                state = "missing"  # complete scoped managed-subtree scan
            elif (found[0] == artifact_id
                  and found[1].relative_path == target
                  and found[1].sha256 == expected
                  and found[1].regular_file is True):
                state = "present"  # attested bytes, NOT a copy right
            else:
                unresolved += 1  # foreign/corrupt occupied target
                continue
            if previous is not None and previous > snapshot.observed_at:
                unresolved += 1  # never roll back a newer historical fact
            elif old == state and previous == snapshot.observed_at:
                unchanged += 1
            else:
                transitions.append((state, placement_id, snapshot.observed_at))

        with storage._lock, storage._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if (db.execute("PRAGMA data_version").fetchone()[0] != version
                    or db.total_changes != changes
                    or _snapshot(db, identity_id, device_id) != original):
                return UsbRecordedObservation("blocked", "usb_scope_changed")
            for state, placement_id, observed_at in transitions:
                db.execute(
                    "UPDATE device_placements SET observed_state=?,"
                    "last_observed_at=?,updated_at=? "
                    "WHERE device_placement_id=? AND device_id=?",
                    (state, observed_at, checked_at, placement_id, device_id),
                )
        return UsbRecordedObservation(
            "recorded", "usb_complete_scan_observed_only",
            len(transitions), unchanged, unresolved,
        )
    except (sqlite3.Error, ValueError, TypeError, OSError,
            UnicodeError, RecursionError):
        return UsbRecordedObservation("blocked", "usb_observation_unavailable")
    except Exception:
        return UsbRecordedObservation("blocked", "usb_observation_unavailable")


__all__ = ["UsbRecordedObservation", "record_verified_usb_observation"]
