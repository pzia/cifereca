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
    """Explicit A→B send, serialized with the canonical acquisition lock."""
    from ..locking import InterProcessLock
    from ..paths import acquisition_lock_file
    with InterProcessLock(acquisition_lock_file()) as shared_lock:
        return _materialize_to_identity_locked(
            self, placement_id, target_identity_id, shared_lock,
        )


def _materialize_to_identity_locked(
    self, placement_id: str, target_identity_id: str, shared_lock,
) -> dict[str, Any]:
    """Send an independent local copy, never an entitlement for the recipient.

    All existing target files retain their ownership. Only a previously
    recorded copy from this exact placement may be reused or repaired.
    """
    import stat
    import tempfile
    from ..library_files import safe_library_component

    source = self.storage.placement_get(placement_id)
    if not source or source.get("state") != "present":
        raise ProviderConfigurationError("Placement source local introuvable")
    owner = str(source.get("identity_id") or "")
    if not owner or self.identity_by_id(owner) is None:
        raise ProviderConfigurationError("Provenance de l'identité source non attestée")
    target_identity = self.identity_by_id(target_identity_id)
    if target_identity is None or target_identity_id == owner:
        raise ProviderConfigurationError("Identité cible absente ou identique à la source")

    root = self.library_dir().expanduser().resolve()
    src = Path(str(source.get("path") or "")).expanduser()
    # A stored relative path, a symlink or an escaped parent is never a
    # source of bytes authorized for cross-identity publication.
    if (not src.is_absolute() or ".." in src.parts or src.is_symlink()
            or not src.resolve().is_relative_to(root) or src.resolve() != src):
        raise ProviderConfigurationError("Emplacement source hors de la bibliothèque")
    if not src.is_file() and not src.is_dir():
        raise ProviderConfigurationError("Artifact source introuvable")
    is_bundle = src.is_dir()
    if is_bundle:
        # copytree follows symlinks by default: reject them, including links
        # hidden in a playlist/manifest subdirectory.
        if any(path.is_symlink() for path in src.rglob("*")):
            raise ProviderConfigurationError("Lien symbolique interdit dans le bundle")
        try:
            source_bundle = load_bundle_manifest(src, verify=True)
        except BundleError as exc:
            raise ProviderConfigurationError("Bundle source invalide") from exc
    else:
        if not stat.S_ISREG(src.stat().st_mode):
            raise ProviderConfigurationError("Artifact source non régulier")
        source_digest = sha256_file(src)

    provider = str(source.get("provider_id") or "shared")
    identity_part = safe_library_component(
        target_identity.directory_name or target_identity_id, "default",
    )
    provider_part = safe_library_component(provider, "local")
    identity_dir = root / identity_part
    expected_dir = identity_dir / provider_part
    # Do this before the service creates any directories: a symlink to an
    # external directory must not be followed by mkdir or by a later copy.
    if (identity_dir.is_symlink() or expected_dir.is_symlink()
            or identity_dir.resolve() != identity_dir
            or expected_dir.resolve() != expected_dir):
        raise ProviderConfigurationError("Répertoire cible symbolique interdit")
    dest_dir = self.library_identity_provider_dir(target_identity_id, provider)
    if (dest_dir.is_symlink() or dest_dir.resolve() != expected_dir
            or not dest_dir.resolve().is_relative_to(root)):
        raise ProviderConfigurationError("Répertoire cible hors bibliothèque")

    source_entries = [
        row for row in self.storage.catalog_entries()
        if str(row.get("placement_id") or "") == str(placement_id)
    ]
    if len(source_entries) > 1:
        raise ProviderConfigurationError("Notices source ambiguës")
    source_entry = source_entries[0] if source_entries else {}
    artifact_id = str(source.get("artifact_id") or "").strip()
    item_id = str(source.get("source_item_id") or source_entry.get("item_id") or "").strip() or None

    # A canonical Artifact is immutable final SHA evidence. If its source
    # bytes were altered, never copy them under that old Artifact ID. Legacy
    # placeholder rows without an actual Artifact are not upgraded by guess.
    checkpoint = None
    if not is_bundle and artifact_id.startswith("sha256:"):
        registered = self.storage.artifact_get(artifact_id)
        if registered is not None and (
            registered.get("sha256") != source_digest
            or registered.get("size") != src.stat().st_size
        ):
            raise ProviderConfigurationError(
                "Artifact source modifié depuis son indexation"
            )
        if artifact_id == "sha256:" + source_digest:
            from .local_transfer_recovery import LocalTransferCheckpoint
            possible_checkpoint = LocalTransferCheckpoint(
                self.storage, root=root, source=source, recipient=target_identity_id,
                source_path=src, destination_dir=dest_dir, artifact_id=artifact_id,
                sha256=source_digest, shared_lock=shared_lock,
            )
            if registered is not None or possible_checkpoint.exists():
                checkpoint = possible_checkpoint

    def publish_canonical_send(path: Path) -> None:
        """Publish the verified local transfer only when source B is canonical.

        An arbitrary legacy artifact ID, missing local SHA or unregistered
        profile path is NOT an origin receipt. Keep preexisting legacy send
        semantics in those cases until #3196 migrates that source, without
        fabricating a right or adopting another recipient's copy. This
        method is called only AFTER this explicit user-requested send has
        created or independently verified its own target bytes.
        """
        if is_bundle or not artifact_id.startswith("sha256:"):
            return
        source_artifact = self.storage.artifact_get(artifact_id)
        if (type(source_artifact) is not dict
                or source_artifact.get("sha256") != source_digest
                or artifact_id != "sha256:" + source_digest
                or source_artifact.get("size") != src.stat().st_size):
            if checkpoint is not None:
                raise ProviderConfigurationError("Artifact source modifié depuis son indexation")
            return
        from .canonical_transfer import publish_completed_local_transfer
        from .canonical_publication import _stable

        # A completed byte-for-byte copy, not a provider/ADEPT grant or a
        # claim inferred from a recipient-owned same-SHA candidate.
        completed_id = _stable(
            "send", str(placement_id), owner, target_identity_id,
            str(src), str(path), source_digest,
        )
        publish_completed_local_transfer(
            self.storage, library_root=root,
            from_identity_id=owner, to_identity_id=target_identity_id,
            from_path=src, to_path=path,
            transfer_id=completed_id,
            transfer_receipt_ref=_stable("completed-local-send", completed_id),
            explicitly_authorized=True, shared_lock=shared_lock,
        )
        if checkpoint is not None:
            checkpoint.finish(path)

    def verified_target(path: Path) -> bool:
        if (path.is_symlink() or path.resolve() != path
                or not path.is_relative_to(dest_dir)):
            return False
        if is_bundle:
            if not path.is_dir() or any(p.is_symlink() for p in path.rglob("*")):
                return False
            try:
                return (load_bundle_manifest(path, verify=True).bundle_sha256
                        == source_bundle.bundle_sha256)
            except BundleError:
                return False
        return (path.is_file() and sha256_file(path) == source_digest)

    # A same-name, same-SHA file owned by B is NOT a copy created by A:
    # neither its metadata nor its credential/rights may be overwritten.
    owned_copy = None
    for row in self.storage.placements():
        meta = row.get("metadata") or {}
        if (row.get("identity_id") == target_identity_id
                and row.get("provider_id") in (None, provider)
                and (row.get("source_item_id") is None
                     or str(row.get("source_item_id") or "") == str(item_id or ""))
                and row.get("artifact_id") == artifact_id
                and row.get("state") == "present"
                and type(meta) is dict
                and meta.get("materialized_from") == placement_id):
            path = Path(str(row.get("path") or ""))
            if path.parent == dest_dir and path != src and verified_target(path):
                exact_provenance = (
                    meta.get("transfer_kind") == "local_copy"
                    and meta.get("file_origin_identity_id") == owner
                )
                # Old A→B hardlinks can be repaired in place: the shared
                # inode establishes their physical origin independently of
                # incomplete legacy metadata. An unrelated independent B
                # file must not be adopted solely from materialized_from.
                legacy_hardlink = (
                    not is_bundle and os.path.samefile(src, path)
                )
                if exact_provenance or legacy_hardlink:
                    owned_copy = (path, row)
                    break

    def copy_regular(dest: Path, *, replace: bool = False) -> None:
        # Publish an independent inode: never os.link(source, destination).
        # Temporary file and exclusive link also refuse a dangling symlink
        # created concurrently at the final destination.
        fd, temporary = tempfile.mkstemp(prefix=".cifereca-send-", dir=dest_dir)
        os.close(fd)
        temp = Path(temporary)
        journaled = False
        try:
            opened = os.open(src, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(opened, "rb") as input_stream, temp.open("wb") as out:
                if not stat.S_ISREG(os.fstat(input_stream.fileno()).st_mode):
                    raise ProviderConfigurationError("Artifact source non régulier")
                shutil.copyfileobj(input_stream, out)
                out.flush()
                os.fsync(out.fileno())
            if sha256_file(temp) != source_digest or sha256_file(src) != source_digest:
                raise ProviderConfigurationError("Source modifiée pendant la copie")
            if replace:
                if not verified_target(dest):
                    raise ProviderConfigurationError("Destination modifiée pendant la copie")
                os.replace(temp, dest)
            else:
                if checkpoint is not None:
                    checkpoint.record_ready(temp, dest)
                    journaled = True
                os.link(temp, dest)  # Exclusive destination; never hardlink src.
                if checkpoint is not None:
                    from .local_transfer_recovery import sync_directory
                    sync_directory(dest_dir)
        finally:
            if not journaled:
                temp.unlink(missing_ok=True)

    recovered = checkpoint.recover() if checkpoint is not None and owned_copy is None else None
    if owned_copy:
        dest, existing = owned_copy
        if not is_bundle and os.path.samefile(src, dest):
            copy_regular(dest, replace=True)  # Repair a legacy hardlink.
            status = "complete"
        else:
            publish_canonical_send(dest)
            if any(row.get("placement_id") == existing["placement_id"]
                   for row in self.storage.catalog_entries()):
                return {
                    "status": "unchanged", "path": str(dest),
                    "placement_id": existing["placement_id"],
                    "target_identity_id": target_identity_id, "provider_id": provider,
                }
            # Canonical publication survived, but its descriptive projection
            # did not. Repair it below using the SAME verified copy.
            status = "unchanged"
    elif recovered is not None:
        dest, status = recovered, "complete"
    else:
        dest = dest_dir / src.name
        stem, suffix = dest.stem, dest.suffix
        n = 2
        # A symbolic-link destination is a hard error, even when dangling.
        if dest.is_symlink():
            raise ProviderConfigurationError("Lien symbolique cible interdit")
        while os.path.lexists(dest):
            dest = dest_dir / f"{stem} ({n}){suffix}"
            n += 1
            if dest.is_symlink():
                raise ProviderConfigurationError("Lien symbolique cible interdit")
        if is_bundle:
            shutil.copytree(src, dest)
            try:
                if not verified_target(dest):
                    raise ProviderConfigurationError("Bundle copié non vérifié")
            except Exception:
                shutil.rmtree(dest)
                raise
        else:
            copy_regular(dest)
        status = "complete"

    if not dest.resolve().is_relative_to(root) or dest.resolve() != dest:
        raise ProviderConfigurationError("Destination hors bibliothèque")

    if artifact_id:
        # Canonical B placement + SourceReference(internal) + both contextual
        # origins commit atomically before legacy catalog projections. A failed
        # canonical write never advertises success or awards supplier rights.
        publish_canonical_send(dest)
        stat_target = (dest / ".cifereca-bundle.json") if is_bundle else dest
        size_stat = stat_target.stat()
        placement_meta = {
            "relative_path": str(dest.relative_to(root)),
            "mtime_ns": int(size_stat.st_mtime_ns),
            "materialized_from": placement_id,
            "artifact_kind": "bundle" if is_bundle else "file",
            "transfer_kind": "local_copy",
            "file_origin_identity_id": owner,
        }
        if is_bundle:
            placement_meta["manifest_size"] = int(size_stat.st_size)
        else:
            placement_meta["file_size"] = int(size_stat.st_size)
        new_placement_id = self.storage.placement_put({
            "artifact_id": artifact_id, "path": str(dest),
            "identity_id": target_identity_id, "provider_id": provider,
            "source_item_id": item_id, "state": "present",
            "last_seen_at": int(time.time()), "metadata": placement_meta,
        })
        # Source account credentials, rights and entitlement flags belong to
        # A. The recipient gets a local file with origin, not an owned item.
        allowed = {
            "description", "summary", "subjects", "series", "collection",
            "authors", "publisher", "language", "languages", "isbn",
            "cover_url", "thumbnail_url", "media_format", "artifact_kind",
        }
        metadata = {
            k: v for k, v in dict(source_entry.get("metadata") or {}).items()
            if k in allowed
        }
        metadata.update({
            "path": str(dest), "materialized_from": placement_id,
            "file_origin_identity_id": owner, "transfer_kind": "local_copy",
        })
        self.storage.catalog_entry_put({
            "artifact_id": artifact_id, "placement_id": new_placement_id,
            "title": source_entry.get("title") or src.stem,
            "author": source_entry.get("author"),
            "media_format": source_entry.get("media_format") or src.suffix.lstrip(".").lower(),
            "identity_id": target_identity_id, "provider_id": provider, "item_id": item_id,
            "local_state": "local", "metadata": metadata,
        })
    else:
        indexed = self.catalog_index_path(
            dest, title=str(source_entry.get("title") or src.stem), item_id=item_id,
            identity_id=target_identity_id, provider_id=provider,
        )
        new_placement_id = indexed.get("placement_id") if type(indexed) is dict else None
    return {
        "status": status, "path": str(dest),
        "placement_id": new_placement_id,
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
