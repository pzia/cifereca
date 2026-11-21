"""#3171: read-only, Core-owned evidence bridge from canonical SQLite v14 to plan A→B.

No provider calls, writes, scan-derived right, or automatic download admission.
The caller supplies a *previously admitted* ProviderRightsSnapshot from the
single Sync. A plan is a diagnostic decision, never an execution permission.
"""
from __future__ import annotations

import os
from pathlib import Path

from .source_convergence import (
    ConvergencePlan, OriginEvidence, PlacementEvidence, SourceKey,
    plan_convergence,
)
from .canonical_recovery import _exact_borrower_bound_in_db
from .derived_variant_publication import _verified_variant_file
from .library_inventory import _open_root
from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot


# A SourceKey/Placement read must never allocate an unlimited SQLite result.
# Overflow blocks this plan before an executor can interpret missing evidence
# as permission to attempt an unnecessary download.
_MAX_EVIDENCE_ROWS = 100_000


def _verified_copy(
    path_text: str, *, root: Path, owner_directory: str,
    registered_directories: frozenset[str], size: int, sha256: str,
) -> bool:
    """Check the *current* regular bytes; SQLite state alone is not proof."""
    if not (type(path_text) is str and path_text and type(sha256) is str
            and len(sha256) == 64 and type(size) is int and size > 0):
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
        # #3171: a physically attested profile copy MUST be inside its
        # registered directory. A legacy flat/manual/other path is not
        # acceptable evidence for target B even if SQLite claims that
        # identity or happens to share the provider Artifact SHA. Such bytes
        # remain untouched; acquisition eligibility is a separate decision.
        if (len(relative.parts) < 2
                or relative.parts[0] != owner_directory
                or owner_directory not in registered_directories
                # A poisoned SQLite path such as A/../B/book.pdf has the
                # right first component but physically belongs to profile B.
                or any(part == ".." for part in relative.parts)):
            return False
        # Only the configured root may be an alias. Resolve it once,
        # then open every remaining component without following symlinks.
        # Hash the exact descriptor whose named inode is rechecked.
        root_fd = _open_root(resolved_root)
        try:
            root_info = os.fstat(root_fd)
            expected_root = (root_info.st_dev, root_info.st_ino)
            resolved = resolved_root.joinpath(*relative.parts)

            def root_current():
                if alias.resolve(strict=True) != resolved_root:
                    return False
                current_fd = _open_root(resolved_root)
                try:
                    info = os.fstat(current_fd)
                    return (info.st_dev, info.st_ino) == expected_root
                finally:
                    os.close(current_fd)

            if not root_current():
                return False
            with _verified_variant_file(resolved, sha256, expected_size=size) as recheck:
                signature = recheck()
                return root_current() and recheck() == signature
        finally:
            os.close(root_fd)
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
    # Storage.schema_version itself enters the worker-local connection
    # context, which would implicitly settle an outer SQLite transaction.
    if storage._connect().in_transaction:
        raise ValueError("canonical_presence_nested_transaction")
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

    def database_stamp(db):
        # total_changes detects writes on this worker connection; data_version
        # detects commits from other workers/processes. A change triggers
        # scoped revalidation, not an automatic failure for unrelated writes.
        return (db.total_changes, db.execute("PRAGMA data_version").fetchone()[0])

    def read_evidence(db):
        if db.in_transaction:
            raise ValueError("canonical_presence_nested_transaction")
        with db:
            # Owner, source receipts and physical placements must be observed in
            # one consistent SQLite read snapshot, even across other processes.
            db.execute("BEGIN")
            # Exact local B presence must belong to this identity's registered
            # physical directory. The full identity registry can be arbitrarily
            # large and is irrelevant to one provider/source plan. A duplicate
            # directory in imported/corrupt SQLite must fail closed even if an
            # OriginLink and SHA are otherwise valid.
            owner_row = db.execute(
                "SELECT directory_name FROM identities WHERE identity_id=?",
                (scope.identity_id,),
            ).fetchone()
            owner_dir = (
                owner_row[0]
                if owner_row is not None and type(owner_row[0]) is str and owner_row[0]
                else ""
            )
            if owner_dir and db.execute(
                "SELECT 1 FROM identities WHERE directory_name=? "
                "AND identity_id<>? LIMIT 1",
                (owner_dir, scope.identity_id),
            ).fetchone() is not None:
                owner_dir = ""
            # Positive local B evidence may suppress a fresh GET. It must
            # therefore be tied not only to the exact borrower coordinates but
            # also to the one currently enabled provider connection. A migrated
            # account row with a foreign connection_id cannot satisfy this A.
            # Reuse the bounded Core journal guard in this same SQLite snapshot.
            bound = _exact_borrower_bound_in_db(db, scope)
            origins_raw = db.execute(
                "SELECT o.origin_link_id, o.identity_id, o.evidence_ref,"
                "s.branch_id, s.edition_id, s.right_id, s.reference_kind,"
                "o.artifact_id, a.sha256, a.size "
                "FROM origin_links AS o "
                "JOIN source_references AS s "
                "ON s.source_reference_id=o.source_reference_id "
                "JOIN artifacts AS a ON a.artifact_id=o.artifact_id "
                "WHERE o.origin_kind='provider' AND o.identity_id=? "
                "AND s.identity_id=? AND s.provider_id=? AND s.credential_id=? "
                "AND s.account_id=? AND s.branch_id=? "
                "AND s.evidence_kind='provider_response' "
                "AND s.evidence_ref<>'' "
                "ORDER BY o.origin_link_id LIMIT ?",
                (scope.identity_id, scope.identity_id, scope.provider_id,
                 scope.credential_id, scope.account_id, snapshot.branch,
                 _MAX_EVIDENCE_ROWS + 1),
            ).fetchall()
            if len(origins_raw) > _MAX_EVIDENCE_ROWS:
                # Never turn truncated local proof into an apparent absence that
                # could send an otherwise satisfied source back into acquisition.
                raise ValueError("canonical_presence_evidence_over_budget")
            # Read B inside the same SQLite snapshot, but ONLY for an Artifact
            # linked to this exact source account and branch. EXISTS avoids the
            # OriginLink x Placement multiplication when many rights or physical
            # copies share one Artifact. Foreign-profile/local-only Placements
            # must not consume this source's finite verification budget.
            placements_raw = db.execute(
                "SELECT p.placement_id,p.path,p.identity_id,p.state,"
                "p.artifact_id,a.sha256,a.size FROM placements AS p "
                "JOIN artifacts AS a ON a.artifact_id=p.artifact_id "
                "WHERE p.identity_id=? AND p.state='present' AND EXISTS ("
                "SELECT 1 FROM origin_links AS o "
                "JOIN source_references AS s "
                "ON s.source_reference_id=o.source_reference_id "
                "WHERE o.artifact_id=p.artifact_id AND o.identity_id=p.identity_id "
                "AND o.origin_kind='provider' AND s.identity_id=? "
                "AND s.provider_id=? AND s.credential_id=? "
                "AND s.account_id=? AND s.branch_id=? "
                "AND s.evidence_kind='provider_response' "
                "AND s.evidence_ref<>'') "
                "ORDER BY p.placement_id LIMIT ?",
                (scope.identity_id, scope.identity_id, scope.provider_id,
                 scope.credential_id, scope.account_id, snapshot.branch,
                 _MAX_EVIDENCE_ROWS + 1),
            ).fetchall()
            if len(placements_raw) > _MAX_EVIDENCE_ROWS:
                raise ValueError("canonical_presence_evidence_over_budget")
            return owner_dir, bound, origins_raw, placements_raw

    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise ValueError("canonical_presence_nested_transaction")
        stamp = database_stamp(db)
        exact = read_evidence(db)
    owner_dir, bound, origins_raw, placements_raw = exact
    reserved_dirs = frozenset((owner_dir,)) if owner_dir else frozenset()

    origins = []
    hashes: set[str] = set()
    # A stale or disabled connection is not an admitted current right.
    # Retain its historical SQL rows, but do not report a present entitlement.
    for link_id, identity_id, evidence_ref, branch, edition, right, kind, artifact_id, sha, size in (
        origins_raw if bound else ()
    ):
        try:
            key = SourceKey(scope, edition, right, kind, branch)
            if (key not in source_keys or type(evidence_ref) is not str
                    or not evidence_ref or type(sha) is not str
                    or type(size) is not int or size <= 0
                    or artifact_id != "sha256:" + sha):
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
        for placement_id, path, identity, state, artifact_id, sha, size in placements_raw:
            if sha not in hashes or artifact_id != "sha256:" + sha:
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
    # Hashing happened outside the SQLite lock. Do not return a stale
    # positive OR a speculative negative plan when the evidence database
    # changed during that interval. Never settle a caller-owned transaction.
    with storage._lock:
        active = storage._connect()
        if active is not db or active.in_transaction:
            raise ValueError("canonical_presence_snapshot_changed")
        changed = database_stamp(active)
        if changed != stamp:
            # A journal/notice for another source must not prevent Sync.
            # Re-read only the same bounded account/branch proof; if it
            # changes again during the read, do not spin or infer absence.
            if read_evidence(active) != exact or database_stamp(active) != changed:
                raise ValueError("canonical_presence_snapshot_changed")
    # Deliberately no completed ProfileScan: this checks possible positive
    # satisfaction only, not exhaustive local absence or download permission.
    return plan_convergence(
        (snapshot,), origins=tuple(origins), placements=tuple(placements),
    )
