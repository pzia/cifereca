"""Explicit library materialization, catalogue scans and loan ledger writes.

Separated from read projections; invoked only through existing facade entry
points. No new scheduler, implicit migration or network operation."""
from __future__ import annotations

import time
import os
import shutil
from pathlib import Path
from typing import Any
from ..acquisition_engine import Entitlement, SourceIdentity
from ..catalog import LocalCatalog
from ..library_files import sha256_file
from ..models import Loan
from ..providers import ProviderConfigurationError
from ..plugin_api import BundleError, load_bundle_manifest


def materialize_to_identity(self, placement_id: str, target_identity_id: str) -> dict[str, Any]:
    """Materialize an existing Artifact for another Identity without reacquisition.

    0.9.0.1 deliberately performs targeted catalog registration here. A full
    catalog scan after a one-file hardlink/copy made this visible user action
    proportional to the entire library size.
    """
    source = self.storage.placement_get(placement_id)
    if not source or source.get("state") != "present":
        raise ProviderConfigurationError("Placement source local introuvable")
    src = Path(str(source.get("path") or ""))
    if not src.is_file() and not src.is_dir():
        raise ProviderConfigurationError("Artifact source introuvable")
    is_bundle = src.is_dir()
    source_bundle = None
    if is_bundle:
        try:
            source_bundle = load_bundle_manifest(src, verify=True)
        except BundleError as exc:
            raise ProviderConfigurationError(f"Bundle source invalide: {exc}") from exc
    target_identity = self.identity_by_id(target_identity_id)
    if target_identity is None:
        raise ProviderConfigurationError(f"Identité cible inconnue: {target_identity_id}")
    provider = str(source.get("provider_id") or "shared")
    source_entries = [
        row for row in self.storage.catalog_entries()
        if str(row.get("placement_id") or "") == str(placement_id)
    ]
    source_entry = source_entries[0] if source_entries else {}
    dest_dir = self.library_identity_provider_dir(target_identity_id, provider)
    dest = dest_dir / src.name
    unchanged = False
    if dest.exists():
        if is_bundle and dest.is_dir():
            try:
                unchanged = load_bundle_manifest(dest, verify=True).bundle_sha256 == source_bundle.bundle_sha256
            except BundleError:
                unchanged = False
        elif not is_bundle and dest.is_file():
            unchanged = sha256_file(dest) == sha256_file(src)
        if not unchanged:
            stem, suffix = dest.stem, dest.suffix; n = 2
            while dest.exists():
                dest = dest_dir / f"{stem} ({n}){suffix}"; n += 1
    if not unchanged:
        if is_bundle:
            shutil.copytree(src, dest)
        else:
            try:
                os.link(src, dest)
            except OSError:
                shutil.copy2(src, dest)

    artifact_id = str(source.get("artifact_id") or "").strip()
    item_id = str(source.get("source_item_id") or source_entry.get("item_id") or "").strip() or None
    if artifact_id:
        stat_target = (dest / ".cifereca-bundle.json") if is_bundle else dest
        stat = stat_target.stat()
        try:
            relative = str(dest.resolve().relative_to(self.library_dir().resolve()))
        except ValueError:
            relative = dest.name
        placement_meta = {
            "relative_path": relative, "mtime_ns": int(stat.st_mtime_ns),
            "materialized_from": placement_id, "artifact_kind": "bundle" if is_bundle else "file",
        }
        if is_bundle:
            placement_meta["manifest_size"] = int(stat.st_size)
        else:
            placement_meta["file_size"] = int(stat.st_size)
        new_placement_id = self.storage.placement_put({
            "artifact_id": artifact_id, "path": str(dest), "identity_id": target_identity_id,
            "provider_id": provider, "source_item_id": item_id, "state": "present",
            "last_seen_at": int(time.time()), "metadata": placement_meta,
        })
        metadata = dict(source_entry.get("metadata") or {})
        metadata["path"] = str(dest.resolve())
        metadata["materialized_from"] = placement_id
        self.storage.catalog_entry_put({
            "artifact_id": artifact_id, "placement_id": new_placement_id,
            "title": source_entry.get("title") or src.stem, "author": source_entry.get("author"),
            "media_format": source_entry.get("media_format") or src.suffix.lstrip(".").lower(),
            "identity_id": target_identity_id, "provider_id": provider, "item_id": item_id,
            "local_state": "local", "metadata": metadata,
        })
    else:
        self.catalog_index_path(
            dest, title=str(source_entry.get("title") or src.stem), item_id=item_id,
            identity_id=target_identity_id, provider_id=provider,
        )
    return {
        "status": "unchanged" if unchanged else "complete", "path": str(dest),
        "target_identity_id": target_identity_id, "provider_id": provider,
    }


def catalog_scan(self, *, repair_misnamed: bool=False, force_covers: bool=False) -> dict[str, Any]:
    """Reconcile the local-first catalog from the Paris-owned library tree."""
    result = LocalCatalog(self.storage, self.library_dir()).scan(repair_misnamed=repair_misnamed, force_covers=force_covers)
    self.diagnostics.add(
        "CATALOG", "local scan", "ok" if not result.invalid else "warning",
        detail=result.as_dict(),
    )
    return {**result.as_dict(), "storage_schema_version": self.storage.schema_version()}


def catalog_index_path(self, path: Path, *, title: str | None=None, item_id: str | None=None, identity_id: str | None=None, provider_id: str | None=None) -> dict[str, Any]:
    return LocalCatalog(self.storage, self.library_dir()).index_path(
        path, title=title, item_id=item_id, identity_id=identity_id, provider_id=provider_id,
    )


def _positive_loan_identity(loan: Loan) -> tuple[tuple, tuple]:
    """One upstream loan key and the identity-sensitive book/expiry projection.

    A duplicated upstream loan ID that points to two editions, lifetimes or
    branches is ambiguous, not two separate owned books or a last-row-wins
    update. URLs, ADEPT profiles and untrusted raw JSON are NOT identity keys.
    """
    key = (loan.source_provider, loan.source_credential,
           loan.source_account_id, loan.id)
    signature = (str(loan.record_id or loan.id), loan.provider, loan.medium,
                 loan.due_at, loan.document_type, loan.title)
    return key, signature


def _persist_active_loans(self, identity_id: str, loans: list[Loan], *, reconcile_missing: bool=False,
                          complete_scopes: set[tuple[str, str, str]] | frozenset[tuple[str, str, str]] | None = None,
                          realize_expired: bool = True) -> dict[str, int]:
    """Persist positive loan rows; no missing-loan inference without scoped proof."""
    # Build and validate the complete positive batch before touching SQLite.
    # A conflict in a later loan must not commit earlier positive rows.
    pending: list[Entitlement] = []
    seen: dict[tuple, tuple] = {}
    for loan in loans:
        if loan.medium != "digital":
            continue
        # A loan's originating provider/borrower stays the upstream site,
        # independent of the ADEPT profile that may decrypt or return it.
        # The caller's local identity must agree with the source observation:
        # never relabel a foreign imported/historical loan as our own.
        # Check every row before the atomic positive batch reaches SQLite.
        if (type(identity_id) is not str or not identity_id
                or type(loan) is not Loan
                or type(loan.identity_id) is not str
                or loan.identity_id != identity_id):
            raise ValueError("loan_identity_scope_mismatch")
        if type(loan.id) is not str or not loan.id:
            raise ValueError("invalid_positive_loan")
        key, signature = _positive_loan_identity(loan)
        if key in seen:
            if seen[key] != signature:
                raise ValueError("loan_duplicate_ambiguous")
            # A provider can repeat the SAME observation in one page or
            # across pages: keep one right and one acquisition decision.
            continue
        seen[key] = signature
        source = SourceIdentity(
            provider_id=loan.source_provider,
            credential_id=loan.source_credential,
            account_id=loan.source_account_id,
            identity_id=identity_id,
        )
        entitlement = Entitlement(
            source=source,
            item_id=str(loan.record_id or loan.id),
            entitlement_type="loan",
            state="active",
            loan_id=loan.id,
            expires_at=loan.due_at,
            metadata={
                "title": loan.title,
                "author": loan.author or (dict((loan.raw or {}).get("_cifereca") or {}).get("author") if isinstance((loan.raw or {}).get("_cifereca"), dict) else None),
                "media_format": loan.media_format or (dict((loan.raw or {}).get("_cifereca") or {}).get("format") if isinstance((loan.raw or {}).get("_cifereca"), dict) else None),
                "cover_url": loan.thumbnail_url,
                "ebook_url": loan.ebook_url,
                "document_type": loan.document_type,
            },
        )
        pending.append(entitlement)
    # This is a single SQLite transaction, even when several accounts and
    # providers contributed positive rows to the opening sync.
    active_ids = set(self.storage.entitlement_put_positive_loans(tuple(pending)))
    persisted = len(pending)
    returned = (self.storage.entitlement_realize_missing_loans(
        active_ids, identity_id=identity_id, complete_scopes=complete_scopes,
    ) if reconcile_missing and complete_scopes else 0)
    # Explicit single-source inventory sync only persists *positive* rows.
    # Historical expirations across other providers/profiles belong to the
    # dedicated lifecycle reconciliation, not a manual refresh of one source.
    expired = self.storage.entitlement_realize_expired() if realize_expired else 0
    return {"persisted": persisted, "returned_realized": returned, "expired_realized": expired}
