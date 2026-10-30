"""#3719/#3185: non-destructive rebind of canonical completed-delivery paths.

A receipt is never a permission to discard a file. This narrow migration only
changes operational recovery paths after independently verifying the retained
copy and every affected source's existing provider OriginLink. It does not
retire placements, delete files or rewrite provenance.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

from .canonical_publication import _same_file, _stable
from .canonical_recovery import _lock_ok
from .convergence_verified_presence import _verified_copy
from .duplicate_maintenance_guard import _mentions_exact_path

_SCHEMA = "canonical.completed-delivery.v1"
_KEYS = frozenset({
    "schema", "source", "offer_id", "path", "sha256", "receipt",
    "adept_receipt",
})
_MAX_LEDGER_ROWS = 100_000


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("canonical_dedup_duplicate_ledger_field")
        result[key] = value
    return result


def _bound_to_existing_origin(db, *, source: list[str], receipt: str,
                              adept_receipt: str, artifact_id: str) -> bool:
    """A historical exact source/receipt is required, not just matching SHA."""
    links = db.execute(
        "SELECT o.origin_kind,o.evidence_ref FROM source_references AS s "
        "JOIN origin_links AS o ON o.source_reference_id=s.source_reference_id "
        "WHERE s.identity_id=? AND s.provider_id=? AND s.credential_id=? "
        "AND s.account_id=? AND s.branch_id=? AND s.edition_id=? "
        "AND s.right_id=? AND s.reference_kind=? "
        "AND o.identity_id=? AND o.artifact_id=?",
        (*source, source[0], artifact_id),
    ).fetchall()
    return (
        ("provider", receipt) in links
        and (not adept_receipt or ("adept", adept_receipt) in links)
    )


def relink_canonical_completed_delivery_paths(
    storage, *, library_root: Path, candidate_placement_id: str,
    keeper_placement_id: str, shared_lock,
) -> int:
    """Repoint only verified, completed Core recovery receipts to keeper.

    Requires the already-held acquisition interprocess lock. A staged or
    unrecognised journal mentioning the candidate blocks the entire change;
    it must not lose its own resumable bytes. The transaction is idempotent
    and deliberately leaves BOTH placements and physical files untouched.
    """
    if (not isinstance(library_root, Path) or not _lock_ok(shared_lock)
            or type(candidate_placement_id) is not str
            or type(keeper_placement_id) is not str
            or not 0 < len(candidate_placement_id) <= 256
            or not 0 < len(keeper_placement_id) <= 256
            or candidate_placement_id == keeper_placement_id):
        raise ValueError("canonical_dedup_ledger_invalid_request")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        columns = (
            "SELECT path,identity_id,artifact_id,state,provider_id,source_item_id "
            "FROM placements WHERE placement_id=?"
        )
        candidate = db.execute(columns, (candidate_placement_id,)).fetchone()
        keeper = db.execute(columns, (keeper_placement_id,)).fetchone()
        if (candidate is None or keeper is None
                or candidate[0] == keeper[0]
                or candidate[1] != keeper[1] or candidate[2] != keeper[2]
                or candidate[3] != "present" or keeper[3] != "present"
                or candidate[4:] != (None, None)
                or keeper[4:] != (None, None)
                or type(candidate[1]) is not str or not candidate[1]
                or type(candidate[2]) is not str
                or not candidate[2].startswith("sha256:")):
            raise ValueError("canonical_dedup_ledger_scope_changed")
        identity_id, artifact_id = candidate[1], candidate[2]
        artifact = db.execute(
            "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if (artifact is None or type(artifact[0]) is not str
                or len(artifact[0]) != 64
                or any(ch not in "0123456789abcdef" for ch in artifact[0])
                or artifact_id != "sha256:" + artifact[0]
                or type(artifact[1]) is not int or artifact[1] <= 0):
            raise ValueError("canonical_dedup_ledger_artifact_changed")

        directories = db.execute(
            "SELECT identity_id,directory_name FROM identities"
        ).fetchall()
        owner_names = [
            name for owner, name in directories if owner == identity_id
        ]
        if (len(owner_names) != 1 or type(owner_names[0]) is not str
                or not owner_names[0]
                or sum(name == owner_names[0] for _, name in directories) != 1):
            raise ValueError("canonical_dedup_ledger_profile_ambiguous")
        reserved = frozenset(
            name for _, name in directories if type(name) is str and name
        )
        physical = []
        for raw_path in (candidate[0], keeper[0]):
            if type(raw_path) is not str or not raw_path:
                raise ValueError("canonical_dedup_ledger_physical_evidence_changed")
            path = Path(raw_path)
            try:
                original_stat = os.stat(path, follow_symlinks=False)
            except OSError as exc:
                raise ValueError(
                    "canonical_dedup_ledger_physical_evidence_changed"
                ) from exc
            if (not _verified_copy(
                raw_path, root=library_root,
                owner_directory=owner_names[0],
                registered_directories=reserved, size=artifact[1],
                sha256=artifact[0],
            ) or not _same_file(path, original_stat)):
                raise ValueError(
                    "canonical_dedup_ledger_physical_evidence_changed"
                )
            physical.append((path, original_stat))

        staged = []
        ledger = db.execute(
            "SELECT acquisition_id,provider_id,credential_id,account_id,"
            "item_id,identity_id,status,artifact_id,metadata_json "
            "FROM acquisition_ledger LIMIT ?", (_MAX_LEDGER_ROWS + 1,),
        ).fetchall()
        if len(ledger) > _MAX_LEDGER_ROWS:
            raise ValueError("canonical_dedup_ledger_budget_exceeded")
        for record_id, provider, credential, account, edition, profile, status, a_id, raw in ledger:
            if type(raw) is not str or len(raw) > 1024 * 1024:
                raise ValueError("canonical_dedup_ledger_unreadable")
            try:
                metadata = json.loads(raw, object_pairs_hook=_unique_keys)
            except (ValueError, TypeError, RecursionError) as exc:
                raise ValueError("canonical_dedup_ledger_unreadable") from exc
            if type(metadata) is not dict:
                raise ValueError("canonical_dedup_ledger_unreadable")
            if not _mentions_exact_path(metadata, candidate[0]):
                continue
            source = metadata.get("source")
            offer = metadata.get("offer_id")
            receipt = metadata.get("receipt")
            adept = metadata.get("adept_receipt")
            if (set(metadata) != _KEYS
                    or metadata.get("schema") != _SCHEMA
                    or metadata.get("path") != candidate[0]
                    or metadata.get("sha256") != artifact[0]
                    or status != "complete" or a_id != artifact_id
                    or profile != identity_id
                    or type(source) is not list or len(source) != 8
                    or any(type(part) is not str for part in source)
                    or not all(source[i] for i in (0, 1, 2, 4, 5, 6, 7))
                    or source[:4] != [profile, provider, credential, account]
                    or source[5] != edition
                    or type(offer) is not str or not 0 < len(offer) <= 1024
                    or type(receipt) is not str or not 0 < len(receipt) <= 1024
                    or type(adept) is not str or len(adept) > 1024
                    or any("\x00" in text or "\r" in text or "\n" in text
                           for text in (offer, receipt, adept))
                    or record_id != _stable("canonical-intake", *source, offer)
                    or any(_mentions_exact_path(value, candidate[0])
                           for key, value in metadata.items() if key != "path")
                    or not _bound_to_existing_origin(
                        db, source=source, receipt=receipt,
                        adept_receipt=adept, artifact_id=artifact_id,
                    )):
                raise ValueError("canonical_dedup_ledger_dependency_ambiguous")
            metadata["path"] = keeper[0]
            staged.append((
                record_id, raw,
                json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")),
            ))
        if any(not _same_file(path, before) for path, before in physical):
            raise ValueError("canonical_dedup_ledger_physical_evidence_changed")

        now = int(time.time())
        for record_id, original, replacement in staged:
            count = db.execute(
                "UPDATE acquisition_ledger SET metadata_json=?,updated_at=? "
                "WHERE acquisition_id=? AND metadata_json=? AND status='complete' "
                "AND artifact_id=?",
                (replacement, now, record_id, original, artifact_id),
            ).rowcount
            if count != 1:
                raise ValueError("canonical_dedup_ledger_changed")
        if any(not _same_file(path, before) for path, before in physical):
            raise ValueError("canonical_dedup_ledger_physical_evidence_changed")
    return len(staged)


__all__ = ["relink_canonical_completed_delivery_paths"]
