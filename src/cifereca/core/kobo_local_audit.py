"""Kobo P1 #1345: bounded, offline, read-only artifact-reconciliation evidence.

All public output is an allowlist of counters and ephemeral case indices.
No path, account, title, product ID, full SHA, API call, EPUB parsing or
filesystem mutation is permitted. Equality of bytes is not proof that an
entitlement may be reassigned or an original safely deleted.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from typing import Any

from ..library_files import safe_library_component
from .kobo_backup import _kobo_preview_paths

_MAX_RECORDS = 4096
# Covers a Kobo account with >522 recorded books without assuming that
# every notice is already backed by a distinct local file. Hard ceilings
# remain global (across identities): no open-ended filesystem traversal.
_MAX_FILES = 2048
_MAX_FILE_BYTES = 512 * 1024 * 1024
_MAX_TOTAL_HASH_BYTES = 64 * 1024 * 1024 * 1024
_MAX_CASES = 24
_MAX_DIRECTORY_ENTRIES = 4096


@dataclass
class _HashBudget:
    remaining: int


# Internal sentinel only: insufficient budget is different from an invalid
# local artifact. Neither case is evidence permitting another download.
_HASH_BUDGET_EXHAUSTED = object()


def _unindexed_identity_files(
    root: Path, indexed_paths: set[str], budget: int,
) -> tuple[int, int, bool]:
    """Count possible local files in ONE identity directory, never open them.

    Do not follow symlinked ancestors, directories or leaf files. This is
    intentionally nonrecursive: a file absent from our persisted index is
    merely a *candidate*, not proof of an orphan or Kobo ownership.
    """
    if budget <= 0 or not root.is_absolute() or not hasattr(os, "O_NOFOLLOW"):
        return 0, 0, True
    flags = (
        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = -1
    inspected = 0
    candidates = 0
    try:
        fd = os.open("/", flags)
        absolute = Path(os.path.abspath(root))
        for component in absolute.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        with os.scandir(fd) as entries:
            for entry in entries:
                if inspected >= budget:
                    return candidates, inspected, True
                inspected += 1
                if not entry.name.lower().endswith((".epub", ".pdf")):
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                absolute_entry = str(absolute / entry.name)
                if absolute_entry not in indexed_paths:
                    candidates += 1
        return candidates, inspected, False
    except FileNotFoundError:
        # A missing backup folder is normal. Nothing was scanned or created.
        return 0, inspected, False
    except (OSError, ValueError, TypeError):
        # Permission errors, symlinked ancestors and races are inconclusive.
        return candidates, inspected, True
    finally:
        if fd >= 0:
            os.close(fd)




def _read_hash_within(
    path_text: object, roots: tuple[Path, ...], *,
    budget: _HashBudget | None = None,
) -> tuple[str, int, str] | None | object:
    """Hash a regular file via no-follow directory descriptors below one root.

    Reject missing, legacy/out-of-identity, symlinked or non-regular paths.
    A directory swap cannot turn the checked child into an external path:
    every directory component is opened with O_NOFOLLOW relative to its parent.
    """
    if type(path_text) is not str or not path_text or "\x00" in path_text:
        return None
    try:
        path = Path(path_text)
        if not path.is_absolute() or path.suffix.lower() not in {".epub", ".pdf"}:
            return None
        absolute = Path(os.path.abspath(path))
        candidates = []
        for root in roots:
            parent = Path(os.path.abspath(root))
            try:
                relative = absolute.relative_to(parent)
            except ValueError:
                continue
            if relative.parts and all(part not in {".", "..", ""} for part in relative.parts):
                candidates.append((len(parent.parts), parent, relative.parts))
        if not candidates:
            return None
        _, parent, pieces = max(candidates)
        # O_NOFOLLOW only protects the last path component. Resolve the
        # entire configured root from '/' using no-follow directory FDs, or
        # an ancestor link library/alice -> bob can cross identity boundaries.
        flags_dir = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        fd = os.open("/", flags_dir)
        try:
            for directory in parent.parts[1:]:
                newfd = os.open(directory, flags_dir, dir_fd=fd)
                os.close(fd)
                fd = newfd
            for directory in pieces[:-1]:
                newfd = os.open(directory, flags_dir, dir_fd=fd)
                os.close(fd)
                fd = newfd
            flags_file = (
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            )
            file_fd = os.open(pieces[-1], flags_file, dir_fd=fd)
            try:
                with os.fdopen(file_fd, "rb") as stream:
                    file_fd = -1
                    before = os.fstat(stream.fileno())
                    if (
                        not stat.S_ISREG(before.st_mode)
                        or before.st_size < 1 or before.st_size > _MAX_FILE_BYTES
                    ):
                        return None
                    # No bytes are read if the *remaining global* hash budget
                    # cannot cover the complete file. File size is checked on
                    # the already-open, no-follow descriptor, not an unsafe
                    # path-level stat. Future records may still fit the budget.
                    if budget is not None and before.st_size > budget.remaining:
                        return _HASH_BUDGET_EXHAUSTED
                    digest = hashlib.sha256()
                    total = 0
                    while total < before.st_size:
                        chunk = min(1024 * 1024, before.st_size - total)
                        if budget is not None:
                            chunk = min(chunk, budget.remaining)
                            if chunk <= 0:
                                return _HASH_BUDGET_EXHAUSTED
                        data = stream.read(chunk)
                        if not data:
                            break
                        total += len(data)
                        if budget is not None:
                            budget.remaining -= len(data)
                        if total > _MAX_FILE_BYTES:
                            return None
                        digest.update(data)
                    after = os.fstat(stream.fileno())
                    if (
                        total != before.st_size or after.st_size != before.st_size
                        or after.st_mtime_ns != before.st_mtime_ns
                        or after.st_ctime_ns != before.st_ctime_ns
                        or after.st_dev != before.st_dev or after.st_ino != before.st_ino
                    ):
                        return None
                    return digest.hexdigest(), total, str(absolute)
            finally:
                if file_fd >= 0:
                    os.close(file_fd)
        finally:
            os.close(fd)
    except (OSError, ValueError, TypeError):
        return None


def kobo_local_reconciliation_audit(
    service: Any, *, credential_id: str | None = None,
) -> dict[str, Any]:
    """Produce a redacted dry-run for persisted Kobo accounts, with NO writes.

    Only indexed local paths explicitly tied to a Kobo product ID are checked;
    title/ISBN/file-name similarity never creates or repairs a relationship.
    """
    if credential_id is not None and (type(credential_id) is not str or not credential_id):
        raise ValueError("invalid_kobo_audit_scope")
    connections = [
        row for row in service.storage.provider_connections()
        if row.get("provider_id") == "kobo"
        and type(row.get("credential_id")) is str
        and type(row.get("identity_id")) is str
    ]
    selected = [row for row in connections if credential_id is None or row["credential_id"] == credential_id]
    if credential_id is not None and not selected:
        raise ValueError("unknown_kobo_audit_scope")
    # A credential may appear in stale/migrated connection rows for several
    # identities. The backup ledger is keyed by credential, so choosing its
    # "last" identity would wrongly ascribe the same SHA or entitlement to
    # another reader. Reject this *before* scanning files or backup records.
    # Exact duplicate connection rows are one logical scope, not two cases.
    owners: dict[str, str] = {}
    unique_scopes: list[dict[str, Any]] = []
    for row in selected:
        credential, identity = row["credential_id"], row["identity_id"]
        if not credential or not identity:
            raise ValueError("invalid_kobo_audit_scope")
        previous = owners.get(credential)
        if previous is not None and previous != identity:
            raise ValueError("ambiguous_kobo_audit_scope")
        if previous is None:
            owners[credential] = identity
            unique_scopes.append(row)
    # Scoped output has only ephemeral ordinal labels, NEVER credential/identity.
    selected = sorted(unique_scopes, key=lambda row: (row["identity_id"], row["credential_id"]))
    valid_owner = owners
    selected_identities = set(owners.values())
    # Scope the local evidence before budget accounting: a --credential audit
    # must neither enumerate nor expose other identities' report totals.
    records = [
        row for row in service.storage.backup_list("kobo")
        if row.get("credential_id") in valid_owner
    ]
    # Other providers' indexed local files are not Kobo orphan candidates.
    # The only scan roots remain confined to the selected Kobo identities.
    all_catalog = service.storage.catalog_entries()
    catalog = [
        row for row in all_catalog
        if row.get("provider_id") == "kobo"
        and row.get("identity_id") in selected_identities
    ]
    indexed_paths: dict[str, set[str]] = {
        identity: set() for identity in selected_identities
    }
    for row in all_catalog:
        identity = row.get("identity_id")
        metadata = row.get("metadata")
        path = metadata.get("path") if isinstance(metadata, dict) else None
        if identity in indexed_paths and type(path) is str and os.path.isabs(path):
            indexed_paths[identity].add(os.path.abspath(path))
    for row in records:
        identity = valid_owner[row["credential_id"]]
        path = row.get("local_path")
        if type(path) is str and os.path.isabs(path):
            indexed_paths[identity].add(os.path.abspath(path))
    entitlements = [
        row for row in service.storage.entitlements(
            provider_id="kobo", state="active",
        ) if row.get("credential_id") in valid_owner
    ]
    scopes = []
    for index, row in enumerate(selected, start=1):
        identity = row["identity_id"]
        cred = row["credential_id"]
        library_root, target = _kobo_preview_paths(service, identity)
        ident = service.identity_by_id(identity)
        folder = safe_library_component(
            (ident.directory_name if ident and ident.directory_name else identity),
            "default",
        )
        # Historical files directly below the global library root are reported
        # as ambiguous, not opened as another identity's potential content.
        roots = (target, library_root / folder)
        scopes.append((f"account-{index}", cred, identity, roots))

    by_catalog: dict[tuple[str, str], list[object]] = defaultdict(list)
    unlinked_catalog = 0
    for row in catalog[:_MAX_RECORDS]:
        if row.get("provider_id") != "kobo" or row.get("local_state") != "local":
            continue
        identity, product = row.get("identity_id"), row.get("item_id")
        if identity not in selected_identities:
            continue
        if type(product) is not str or not product:
            unlinked_catalog += 1
            continue
        metadata = row.get("metadata")
        raw_path = metadata.get("path") if isinstance(metadata, dict) else None
        if raw_path and type(raw_path) is str:
            by_catalog[(identity, product)].append(raw_path)

    limited = (
        len(records) > _MAX_RECORDS or len(catalog) > _MAX_RECORDS
        or len(entitlements) > _MAX_RECORDS
    )
    checks_left = _MAX_FILES
    hash_budget = _HashBudget(_MAX_TOTAL_HASH_BYTES)
    account_results = []
    # (identity, digest, byte count) -> distinct (item, path) assertions.
    identical: dict[tuple[str, str, int], set[tuple[str, str]]] = defaultdict(set)
    cases: list[dict[str, Any]] = []
    totals = {
        "backup_rows": 0, "linked_files_verified": 0,
        "unverified_placements": 0, "missing_or_unlinked": 0,
        "stored_hash_mismatch": 0, "unproven_catalogue_links": 0,
        "same_product_different_bytes": 0, "binary_match_groups": 0,
        "active_entitlements_unmaterialized": 0,
        "unindexed_regular_file_candidates": 0,
        "identity_flat_file_candidates": 0,
    }

    def case(category: str, artifacts: int) -> None:
        if len(cases) < _MAX_CASES:
            cases.append({
                "case": f"case-{len(cases) + 1:03d}",
                "category": category, "artifacts": artifacts,
                "decision": "manual_review_only",
            })

    directory_entries_left = _MAX_DIRECTORY_ENTRIES
    for label, cred, identity, roots in scopes:
        counts = {key: 0 for key in totals if key != "binary_match_groups"}
        scoped_rows = [
            rec for rec in records[:_MAX_RECORDS]
            if rec.get("credential_id") == cred
        ]
        verified_products: set[str] = set()
        for rec in scoped_rows:
            counts["backup_rows"] += 1
            # Corrupted or legacy ownership cannot authorize a file read.
            claimed = rec.get("identity_id")
            if claimed and claimed != valid_owner[cred]:
                counts["unverified_placements"] += 1
                continue
            product = rec.get("item_id")
            if type(product) is not str or not product:
                counts["unverified_placements"] += 1
                continue
            observed: dict[str, tuple[str, int, str]] = {}
            attempted_paths: set[str] = set()
            stored = rec.get("local_path")
            proposals = [stored] if stored else []
            catalogue_paths = by_catalog.get((identity, product), ())
            if len(catalogue_paths) > 8:
                limited = True
            proposals.extend(catalogue_paths[:8])
            # Known catalogue links are suggestions, not authorization.
            if not stored:
                counts["missing_or_unlinked"] += 1
            for idx, proposed in enumerate(proposals[:9]):
                if not proposed or type(proposed) is not str:
                    continue
                # Repeated paths are not new probes, even when the global
                # hashing budget has already been exhausted.
                if proposed in attempted_paths:
                    continue
                attempted_paths.add(proposed)
                if checks_left == 0:
                    limited = True
                    counts["unverified_placements"] += 1
                    continue
                checks_left -= 1
                result = _read_hash_within(proposed, roots, budget=hash_budget)
                if result is _HASH_BUDGET_EXHAUSTED:
                    # A full pass is impossible under the cumulative read
                    # ceiling. Continue examining other, smaller candidates
                    # but never call this report complete.
                    limited = True
                    counts["unverified_placements"] += 1
                    continue
                if result is None:
                    counts["unverified_placements"] += 1
                    continue
                observed[proposed] = result
                counts["linked_files_verified"] += 1
                if idx > 0 or not stored:
                    counts["unproven_catalogue_links"] += 1
                sha, size, path_key = result
                identical[(identity, sha, size)].add((product, path_key))
                if idx == 0 and stored:
                    expected_sha = rec.get("sha256")
                    expected_size = rec.get("size")
                    hash_attested = (
                        type(expected_sha) is str
                        and len(expected_sha) == 64
                        and expected_sha.lower() == sha
                    )
                    size_attested = (
                        type(expected_size) is int and expected_size == size
                    )
                    if hash_attested and size_attested:
                        verified_products.add(product)
                    elif (
                        type(expected_sha) is str and len(expected_sha) == 64
                        and expected_sha.lower() != sha
                    ) or (
                        type(expected_size) is int and expected_size != size
                    ):
                        counts["stored_hash_mismatch"] += 1
            distinct = {value[:2] for value in observed.values()}
            if len(distinct) > 1:
                counts["same_product_different_bytes"] += 1
                case("same_product_different_bytes", len(distinct))
        # Local evidence only: an active persisted entitlement without an
        # observed artifact is a manual-inspection candidate, never proof of
        # a missing download right or permission to reacquire automatically.
        entitlement_products = {
            row.get("item_id")
            for row in entitlements[:_MAX_RECORDS]
            if row.get("credential_id") == cred
            and row.get("identity_id") in {identity, None}
            and type(row.get("item_id")) is str and row.get("item_id")
        }
        counts["active_entitlements_unmaterialized"] = len(
            entitlement_products - verified_products
        )
        if counts["active_entitlements_unmaterialized"]:
            case("active_entitlement_without_verified_artifact",
                 counts["active_entitlements_unmaterialized"])
        # Read *names and stat metadata only*, never file bytes or EPUBs.
        # roots[0] is the account's Kobo directory; roots[1] is its
        # identity-specific historical flat directory. Never traverse the
        # shared library root, other identities or arbitrary subdirectories.
        for index, directory in enumerate(roots):
            count, inspected, incomplete = _unindexed_identity_files(
                directory, indexed_paths[identity], directory_entries_left,
            )
            directory_entries_left -= inspected
            counts["unindexed_regular_file_candidates"] += count
            if index == 1:
                counts["identity_flat_file_candidates"] += count
            if incomplete:
                limited = True
        if counts["unindexed_regular_file_candidates"]:
            case("unindexed_identity_file_candidate",
                 counts["unindexed_regular_file_candidates"])
        account_results.append({"account": label, "counts": counts})
        for key, count in counts.items():
            totals[key] += count

    for (identity, sha, size), group in identical.items():
        if len({path for _, path in group}) < 2:
            continue
        totals["binary_match_groups"] += 1
        case("confirmed_binary_match", len({path for _, path in group}))
    if totals["stored_hash_mismatch"]:
        case("stored_hash_mismatch", totals["stored_hash_mismatch"])

    return {
        "schema": "kobo.local.reconciliation.audit/1",
        "scope": "single_account" if credential_id is not None else "all_configured",
        "accounts": account_results,
        "counts": totals,
        "catalogue_entries_without_product_identity": unlinked_catalog,
        "truncated": limited,
        "files_hashed_limit": _MAX_FILES,
        "directory_entries_limit": _MAX_DIRECTORY_ENTRIES,
        "total_hash_bytes_limit": _MAX_TOTAL_HASH_BYTES,
        "total_hash_bytes_read": _MAX_TOTAL_HASH_BYTES - hash_budget.remaining,
        "offline": True, "read_only": True, "dry_run": True,
        "mutations_authorized": False, "downloads_authorized": False,
        "classification": "local_hash_evidence_not_entitlement_authority",
        "evidence_status": ("verified_local_bytes" if totals["linked_files_verified"]
                            else "no_verified_local_evidence"),
        "cases": cases,
        "limitations": [
            "Only persisted Kobo backup, active rights and explicit catalogue paths were considered.",
            "Legacy paths outside the owning identity directory are not read.",
            "Only immediate identity-owned folders are enumerated, up to the shared scan limit.",
            "The global SHA audit is bounded by file count, per-file size and cumulative bytes read.",
            "Unindexed file names are candidates, not proven Kobo ownership or orphans.",
            "Legacy paths in the shared global library root cannot be assigned to an identity.",
            "No remote rights refresh or EPUB/DRM inspection.",
            "Same SHA does not authorize deletion or automatic ownership transfer.",
        ],
    }


__all__ = ["kobo_local_reconciliation_audit"]
