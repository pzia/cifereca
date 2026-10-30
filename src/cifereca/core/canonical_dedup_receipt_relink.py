"""#3719: rebind ONLY a completed canonical recovery checkpoint's local path.

No receipt, ADEPT proof, account, origin or right is rewritten. This is a
transactional preparatory step; both Placements and physical copies survive.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

from ..plugin_api.provider_rights_observation import ProviderRightsScope
from .canonical_publication import _same_file, _stable
from .canonical_recovery import _lock_ok, _stage_id, _unique_checkpoint_fields
from .convergence_verified_presence import _verified_copy
from .duplicate_maintenance_guard import _mentions_exact_path
from .source_convergence import SourceKey

_SCHEMA = "canonical.completed-delivery.v1"


def relink_completed_delivery_checkpoints(
    storage, *, library_root: Path, candidate_placement_id: str,
    keeper_placement_id: str, shared_lock,
) -> int:
    """Migrate exact *completed* journal paths after both files re-hash.

    The acquisition lock must already be held. An old operation receipt can
    point to identical bytes in the same physical profile after relocation;
    that NEVER grants new rights. Old staged/incomplete records are vetoes.
    A later operation must still relocate all OTHER dependencies before unlink.
    """
    if (not isinstance(library_root, Path) or not _lock_ok(shared_lock)
            or type(candidate_placement_id) is not str
            or type(keeper_placement_id) is not str
            or not candidate_placement_id or not keeper_placement_id
            or candidate_placement_id == keeper_placement_id):
        raise ValueError("canonical_receipt_relink_invalid_request")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        query = ("SELECT path,identity_id,artifact_id,state,provider_id,"
                 "source_item_id FROM placements WHERE placement_id=?")
        candidate = db.execute(query, (candidate_placement_id,)).fetchone()
        keeper = db.execute(query, (keeper_placement_id,)).fetchone()
        if (candidate is None or keeper is None
                or candidate[1:4] != keeper[1:4]
                or candidate[3] != "present"
                or candidate[4:] != (None, None)
                or keeper[4:] != (None, None)
                or type(candidate[1]) is not str or not candidate[1]
                or type(candidate[2]) is not str):
            raise ValueError("canonical_receipt_relink_scope_changed")
        profile, artifact_id = candidate[1:3]
        artifact = db.execute(
            "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if (artifact is None or type(artifact[0]) is not str
                or len(artifact[0]) != 64
                or any(c not in "0123456789abcdef" for c in artifact[0])
                or artifact_id != "sha256:" + artifact[0]
                or type(artifact[1]) is not int or artifact[1] <= 0):
            raise ValueError("canonical_receipt_relink_artifact_invalid")
        profiles = db.execute(
            "SELECT identity_id,directory_name FROM identities",
        ).fetchall()
        owner_dir = [d for identity, d in profiles if identity == profile]
        if (len(owner_dir) != 1 or type(owner_dir[0]) is not str
                or not owner_dir[0]
                or sum(d == owner_dir[0] for _, d in profiles) != 1):
            raise ValueError("canonical_receipt_relink_profile_ambiguous")
        registered = frozenset(d for _, d in profiles if type(d) is str and d)
        stat_snapshots = []
        for raw_path in (candidate[0], keeper[0]):
            path = Path(raw_path)
            try:
                before = os.stat(path, follow_symlinks=False)
            except OSError as exc:
                raise ValueError("canonical_receipt_relink_bytes_changed") from exc
            if (not _verified_copy(
                raw_path, root=library_root, owner_directory=owner_dir[0],
                registered_directories=registered, size=artifact[1],
                sha256=artifact[0],
            ) or not _same_file(path, before)):
                raise ValueError("canonical_receipt_relink_bytes_changed")
            stat_snapshots.append((path, before))

        # Read every row: untrusted JSON may encode slashes as \\u002f or \\/.
        # Avoid a LIKE shortcut that could miss an active checkpoint.
        ledgers = db.execute(
            "SELECT acquisition_id,provider_id,credential_id,account_id,"
            "item_id,identity_id,status,artifact_id,metadata_json "
            "FROM acquisition_ledger LIMIT 100001",
        ).fetchall()
        if len(ledgers) > 100_000:
            raise ValueError("canonical_receipt_relink_ledger_budget")
        staged = []
        for (record_id, provider, credential, account, item, identity,
             status, stored_artifact, raw) in ledgers:
            if type(raw) is not str or len(raw) > 65536:
                raise ValueError("canonical_receipt_relink_ledger_unreadable")
            try:
                value = json.loads(
                    raw, object_pairs_hook=_unique_checkpoint_fields,
                )
            except (ValueError, TypeError, RecursionError) as exc:
                raise ValueError("canonical_receipt_relink_ledger_unreadable") from exc
            if type(value) is not dict:
                raise ValueError("canonical_receipt_relink_ledger_unreadable")
            if not _mentions_exact_path(value, candidate[0]):
                continue
            if (set(value) != {
                "schema", "source", "offer_id", "path", "sha256",
                "receipt", "adept_receipt",
            } or value["schema"] != _SCHEMA
                    or value["path"] != candidate[0]
                    or status != "complete"
                    or identity != profile or stored_artifact != artifact_id
                    or value["sha256"] != artifact[0]
                    or type(value["offer_id"]) is not str
                    or not value["offer_id"]
                    or len(value["offer_id"]) > 1024
                    or type(value["receipt"]) is not str
                    or not value["receipt"]
                    or len(value["receipt"]) > 1024
                    or type(value["adept_receipt"]) is not str
                    or len(value["adept_receipt"]) > 1024
                    or any(c in x for x in (value["offer_id"], value["receipt"],
                                           value["adept_receipt"]) for c in "\\x00\\r\\n")
                    or type(value["source"]) is not list
                    or len(value["source"]) != 8):
                raise ValueError("canonical_receipt_relink_checkpoint_conflict")
            if any(
                _mentions_exact_path(item, candidate[0])
                for key, item in value.items() if key != "path"
            ):
                raise ValueError("canonical_receipt_relink_nested_path")
            try:
                scope_values = value["source"][:4]
                scope = ProviderRightsScope(*scope_values)
                source = SourceKey(
                    scope, value["source"][5], value["source"][6],
                    value["source"][7], value["source"][4],
                )
            except (ValueError, TypeError, IndexError) as exc:
                raise ValueError("canonical_receipt_relink_source_invalid") from exc
            if (scope.identity_id, scope.provider_id, scope.credential_id,
                    scope.account_id, source.edition_id) != (
                    identity, provider, credential, account, item
            ) or record_id != _stage_id(source, value["offer_id"]):
                raise ValueError("canonical_receipt_relink_source_mismatch")
            source_id = _stable(
                "source", scope.identity_id, scope.provider_id,
                scope.credential_id, scope.account_id, source.branch,
                source.edition_id, source.right_id, source.kind,
            )
            for kind, proof in (
                ("provider", value["receipt"]),
                *((("adept", value["adept_receipt"]),)
                  if value["adept_receipt"] else ()),
            ):
                origin_id = _stable(
                    "origin", kind, profile, artifact_id, source_id, proof,
                )
                origin = db.execute(
                    "SELECT origin_kind,identity_id,artifact_id,"
                    "source_reference_id,evidence_ref "
                    "FROM origin_links WHERE origin_link_id=?",
                    (origin_id,),
                ).fetchone()
                if origin != (kind, profile, artifact_id, source_id, proof):
                    raise ValueError("canonical_receipt_relink_origin_not_published")
            value["path"] = keeper[0]
            staged.append((
                record_id, json.dumps(value, sort_keys=True,
                                      ensure_ascii=False, separators=(",", ":")),
            ))
        if any(not _same_file(path, before) for path, before in stat_snapshots):
            raise ValueError("canonical_receipt_relink_bytes_changed")
        now = int(time.time())
        for record_id, metadata in staged:
            result = db.execute(
                "UPDATE acquisition_ledger SET metadata_json=?,updated_at=? "
                "WHERE acquisition_id=? AND status='complete' AND artifact_id=?",
                (metadata, now, record_id, artifact_id),
            )
            if result.rowcount != 1:
                raise ValueError("canonical_receipt_relink_checkpoint_changed")
        if any(not _same_file(path, before) for path, before in stat_snapshots):
            raise ValueError("canonical_receipt_relink_bytes_changed")
    return len(staged)


__all__ = ["relink_completed_delivery_checkpoints"]
