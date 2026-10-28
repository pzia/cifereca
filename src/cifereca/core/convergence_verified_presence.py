"""#3171: read-only, Core-owned evidence bridge from canonical SQLite v14 to plan A→B.

No provider calls, writes, scan-derived right, or automatic download admission.
The caller supplies a *previously admitted* ProviderRightsSnapshot from the
single Sync. A plan is a diagnostic decision, never an execution permission.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

from .source_convergence import (
    ConvergencePlan, OriginEvidence, PlacementEvidence, SourceKey,
    plan_convergence,
)
from ..catalog import LocalCatalog
from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot


def _verified_copy(
    path_text: str, *, root: Path, owner_directory: str,
    registered_directories: frozenset[str], size: int, sha256: str,
) -> bool:
    """Check the *current* regular bytes; SQLite state alone is not proof."""
    if not (type(path_text) is str and path_text and type(sha256) is str
            and len(sha256) == 64 and type(size) is int and size >= 0):
        return False
    try:
        alias = Path(os.path.abspath(root.expanduser()))
        physical = Path(path_text)
        if not physical.is_absolute():
            return False
        resolved_root = alias.resolve(strict=True)
        for anchor in (alias, resolved_root):
            try:
                relative = physical.relative_to(anchor)
                break
            except ValueError:
                continue
        else:
            return False
        if not relative.parts:
            return False
        # Legacy flat paths can retain an exact existing OriginLink; another
        # registered profile's directory is never this identity's copy.
        if (relative.parts[0] in registered_directories
                and relative.parts[0] != owner_directory):
            return False
        current = alias
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return False
        resolved = physical.resolve(strict=True)
        if not resolved.is_relative_to(resolved_root):
            return False
        before = os.stat(resolved, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_size != size:
            return False
        digest = LocalCatalog.sha256(resolved)
        after = os.stat(resolved, follow_symlinks=False)
        return digest == sha256 and stat.S_ISREG(after.st_mode) and all(
            getattr(before, name) == getattr(after, name)
            for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        )
    except (OSError, ValueError, RuntimeError):
        return False


def plan_existing_verified_presence(
    storage,
    snapshot: ProviderRightsSnapshot,
    *,
    library_root: Path,
) -> ConvergencePlan:
    """Build a conservative decision for one exact remote account/branch.

    Only a historical *provider-origin* with exact source coordinates, proof,
    the same final Artifact SHA, and a freshly hashed file in the owning
    profile can satisfy the remote right. A partial scan/remote inventory is
    never interpreted as an absence. Acquisitions and consent are not inferred.
    This function deliberately does not use `backup_items`, catalogue titles,
    directory names as providers, user declarations, or any remote API.
    """
    if type(snapshot) is not ProviderRightsSnapshot or not isinstance(library_root, Path):
        raise ValueError("invalid_verified_presence_request")
    if storage.schema_version() < 14:
        raise ValueError("canonical_source_schema_required")

    # Failed and empty positive inventories never generate negative evidence.
    source_keys = {
        SourceKey(snapshot.scope, row.edition_id, row.right_id, row.kind, snapshot.branch)
        for row in snapshot.rights if row.provider_attested
    }
    if not source_keys:
        return plan_convergence((snapshot,))

    scope = snapshot.scope
    with storage._lock, storage._connect() as db:
        dirs = db.execute(
            "SELECT identity_id,directory_name FROM identities"
        ).fetchall()
        owner = [name for identity, name in dirs if identity == scope.identity_id]
        # A missing/ambiguous identity folder cannot attest this placement.
        owner_dir = owner[0] if len(owner) == 1 else ""
        reserved_dirs = frozenset(
            name for _, name in dirs if type(name) is str and name
        )
        binding = db.execute(
            "SELECT identity_id,enabled FROM provider_connections "
            "WHERE provider_id=? AND credential_id=?",
            (scope.provider_id, scope.credential_id),
        ).fetchone()
        account_bindings = db.execute(
            "SELECT account_id FROM provider_accounts "
            "WHERE identity_id=? AND provider_id=? AND credential_id=?",
            (scope.identity_id, scope.provider_id, scope.credential_id),
        ).fetchall()
        bound = (
            binding == (scope.identity_id, 1)
            and sum(a[0] == scope.account_id for a in account_bindings) == 1
        )
        origins_raw = db.execute(
            "SELECT o.origin_link_id, o.identity_id, o.evidence_ref,"
            "s.branch_id, s.edition_id, s.right_id, s.reference_kind,"
            "a.sha256, a.size "
            "FROM origin_links AS o "
            "JOIN source_references AS s "
            "ON s.source_reference_id=o.source_reference_id "
            "JOIN artifacts AS a ON a.artifact_id=o.artifact_id "
            "WHERE o.origin_kind='provider' AND o.identity_id=? "
            "AND s.identity_id=? AND s.provider_id=? AND s.credential_id=? "
            "AND s.account_id=? AND s.branch_id=? "
            "AND s.evidence_kind='provider_response'",
            (scope.identity_id, scope.identity_id, scope.provider_id,
             scope.credential_id, scope.account_id, snapshot.branch),
        ).fetchall()
        # Read B with the same SQLite snapshot. Do not use an unbounded scan
        # or an unverified placement to invent a provider link.
        placements_raw = db.execute(
            "SELECT p.placement_id,p.path,p.identity_id,p.state,"
            "a.sha256,a.size FROM placements AS p "
            "JOIN artifacts AS a ON a.artifact_id=p.artifact_id "
            "WHERE p.identity_id=? AND p.state='present'",
            (scope.identity_id,),
        ).fetchall()

    origins = []
    hashes: set[str] = set()
    # A stale or disabled connection is not an admitted current right.
    # Retain its historical SQL rows, but do not report a present entitlement.
    for link_id, identity_id, evidence_ref, branch, edition, right, kind, sha, size in (
        origins_raw if bound else ()
    ):
        try:
            key = SourceKey(scope, edition, right, kind, branch)
            if key not in source_keys or type(evidence_ref) is not str or not evidence_ref:
                continue
            origin = OriginEvidence(
                kind="provider", artifact_sha256=sha, source=key,
                attested=True, identity_id=identity_id, origin_ref=link_id,
                proof_ref=evidence_ref,
            )
            origins.append(origin)
            hashes.add(sha)
        except ValueError:
            # Malformed legacy rows are not elevated to provider evidence.
            continue

    placements = []
    if owner_dir and hashes:
        for placement_id, path, identity, state, sha, size in placements_raw:
            if sha not in hashes:
                continue
            valid = _verified_copy(
                path, root=library_root, owner_directory=owner_dir,
                registered_directories=reserved_dirs, size=size, sha256=sha,
            )
            placements.append(PlacementEvidence(
                identity_id=identity, placement_id=placement_id, sha256=sha,
                size=size, verified=valid,
                verification_ref="sha256:" + sha if valid else "",
            ))
    # Deliberately no completed ProfileScan: this checks possible positive
    # satisfaction only, not exhaustive local absence or download permission.
    return plan_convergence(
        (snapshot,), origins=tuple(origins), placements=tuple(placements),
    )
