"""#3909: private, read-only evidence for Core-staged, unplaced receipts.

A completed operation journal can retain final EPUB/PDF bytes without any
Artifact/Placement/OriginLink if publication failed. This is diagnostic only:
it never authorizes GET, recovery, ownership attribution or a filesystem move.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..plugin_api.provider_rights_observation import ProviderRightsScope
from .canonical_promotion import _stage_parts
from .canonical_publication import _stable
from .canonical_recovery import _scope, _stage_id, _unique_checkpoint_fields
from .source_convergence import SourceKey
from .acquisition_staging_evidence import _verify_staged

_MAX_JSON = 65536


def core_staging_receipt_observations(
    db, *, coords: tuple[str, str, str, str],
    library_root: Path | None, verify_files: bool,
    reference, limit: int,
) -> tuple[list[tuple[str, dict[str, object]]], bool]:
    """Inspect the exact account's pending Core receipts, with bounded rows.

    Files are hashed only under the registered profile's isolated Core
    staging namespace; a status alone is not proof of physical availability.
    Every output identifier is pseudonymized by the caller.
    """
    if type(limit) is not int or limit < 0 or type(verify_files) is not bool:
        raise ValueError("canonical_staging_audit_invalid_request")
    if limit == 0:
        return [], bool(db.execute(
            "SELECT 1 FROM acquisition_ledger WHERE identity_id=? "
            "AND provider_id=? AND credential_id=? AND account_id=? "
            "AND status='canonical_receipt_staged' AND artifact_id IS NULL "
            "LIMIT 1", coords,
        ).fetchone())
    found = db.execute(
        "SELECT acquisition_id,artifact_id,metadata_json FROM acquisition_ledger "
        "WHERE identity_id=? AND provider_id=? AND credential_id=? "
        "AND account_id=? AND status='canonical_receipt_staged' "
        "AND artifact_id IS NULL ORDER BY acquisition_id LIMIT ?",
        (*coords, limit + 1),
    ).fetchall()
    if len(found) > limit:
        return [], True

    profile = None
    if isinstance(library_root, Path):
        matches = db.execute(
            "SELECT directory_name FROM identities WHERE identity_id=?",
            (coords[0],),
        ).fetchall()
        if len(matches) == 1:
            directory = matches[0][0]
            if (type(directory) is str and directory not in ("", ".", "..")
                    and "/" not in directory and "\\" not in directory
                    and "\x00" not in directory):
                root = Path(os.path.abspath(library_root.expanduser()))
                profile = root / directory
                # No hash within an aliased/shared or symlinked profile.
                other = db.execute(
                    "SELECT 1 FROM identities WHERE identity_id<>? "
                    "AND directory_name=? LIMIT 1",
                    (coords[0], directory),
                ).fetchone()
                if other or root.is_symlink() or profile.is_symlink():
                    profile = None

    observations: list[tuple[str, dict[str, object]]] = []
    for operation_id, artifact_id, raw in found:
        entry: dict[str, object] = {
            "receipt_ref": reference(operation_id),
            "staging_state": "core_staged_untrusted",
            "staged_visible_in_library": False,
            "staging_sha_verified": False,
            "origin_proven_by_receipt": False,
        }
        # The raw operation ID is internal to the caller's dict key only;
        # no real identifier may appear in a returned audit observation.
        observations.append((operation_id, entry))
        if type(raw) is not str or len(raw) > _MAX_JSON or artifact_id is not None:
            continue
        try:
            details = json.loads(raw, object_pairs_hook=_unique_checkpoint_fields)
            if (type(details) is not dict or set(details) != {
                "schema", "source", "offer_id", "path", "sha256",
                "receipt", "adept_receipt",
            } or details["schema"] != "canonical.completed-delivery.v1"
                or type(details["source"]) is not list
                or len(details["source"]) != 8
                or type(details["offer_id"]) is not str
                or not 0 < len(details["offer_id"]) <= 1024
                or type(details["path"]) is not str
                or type(details["sha256"]) is not str
                or len(details["sha256"]) != 64
                or any(ch not in "0123456789abcdef" for ch in details["sha256"])
                or type(details["receipt"]) is not str
                or not details["receipt"].startswith("core-delivery:")
                or len(details["receipt"]) != len("core-delivery:") + 64
                or any(ch not in "0123456789abcdef"
                       for ch in details["receipt"][len("core-delivery:"):])
                or type(details["adept_receipt"]) is not str
                or len(details["adept_receipt"]) > 1024
                or any(ch in v for v in (
                    details["receipt"], details["adept_receipt"],
                    details["offer_id"]) for ch in "\x00\r\n")):
                continue
            source = SourceKey(
                ProviderRightsScope(*details["source"][:4]),
                details["source"][5], details["source"][6],
                details["source"][7], details["source"][4],
            )
            if (details["source"] != _scope(source)
                    or tuple(details["source"][:4]) != coords
                    or operation_id != _stage_id(source, details["offer_id"])):
                continue
        except (TypeError, ValueError, IndexError, KeyError, RecursionError):
            continue

        # A published OriginLink for this exact source+artifact, even with
        # another completed receipt, makes an "unpublished" label unsafe.
        # No final Placement is inferred without a separate FS check.
        source_id = _stable(
            "source", *details["source"],
        )
        origin = db.execute(
            "SELECT 1 FROM origin_links o JOIN source_references s "
            "ON o.source_reference_id=s.source_reference_id "
            "WHERE o.origin_kind='provider' AND o.identity_id=? "
            "AND o.artifact_id=? AND o.source_reference_id=? "
            "AND s.identity_id=? "
            "AND s.provider_id=? AND s.credential_id=? AND s.account_id=? "
            "AND s.branch_id=? AND s.edition_id=? "
            "AND s.right_id=? AND s.reference_kind=? LIMIT 1",
            (coords[0], "sha256:" + details["sha256"], source_id,
             *details["source"]),
        ).fetchone()
        entry["staging_state"] = "core_staged_unverified"
        if not verify_files:
            continue
        if profile is None:
            entry["staging_state"] = "core_staged_untrusted"
            continue
        path = Path(details["path"])
        try:
            if (not path.is_absolute()
                    or _stage_parts(path, profile) is None):
                entry["staging_state"] = "core_staged_untrusted"
                continue
            root = profile / ".canonical-delivery"
            # The verified bytes, not a recorded size, determine whether a
            # journaled candidate still exists in this exact profile.
            verified = _verify_staged(
                str(path), root, details["sha256"], None,
            )
        except (ValueError, TypeError, OSError):
            verified = "unavailable"
        if verified == "verified_sha256":
            entry["staging_sha_verified"] = True
            entry["staging_state"] = (
                "core_staged_origin_recorded" if origin
                else "core_staged_present_unpublished"
            )
        elif verified == "missing":
            entry["staging_state"] = "core_staged_missing"
        else:
            entry["staging_state"] = "core_staged_untrusted"
    return observations, False


__all__ = ["core_staging_receipt_observations"]
