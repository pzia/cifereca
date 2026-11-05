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


def _open_owned_transfer_file(path: Path, owner_root: Path) -> int:
    """Open a regular source through no-follow directory descriptors.

    O_NOFOLLOW on the final file alone does not protect intermediate parents:
    a profile subdirectory could be replaced by a symlink after the initial
    owner-path check, and a legacy same-SHA transfer would then copy the
    *other* profile's bytes. Each component is resolved relative to an
    already-pinned directory descriptor instead of by its mutable pathname.
    """
    import stat

    relative = path.relative_to(owner_root)
    if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError("invalid_owned_transfer_path")
    dir_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent_fd = os.open(owner_root, dir_flags)
    try:
        for part in relative.parts[:-1]:
            child_fd = os.open(part, dir_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = child_fd
        file_fd = os.open(
            relative.parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise ValueError("invalid_owned_transfer_file")
            return file_fd
        except BaseException:
            os.close(file_fd)
            raise
    finally:
        os.close(parent_fd)


def _transfer_file_identity(info) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev, info.st_ino, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    )


def _owned_transfer_digest(path: Path, owner_root: Path):
    """SHA of the pinned file, never a lookup through a substituted parent."""
    import hashlib

    file_fd = _open_owned_transfer_file(path, owner_root)
    with os.fdopen(file_fd, "rb") as stream:
        initial = os.fstat(stream.fileno())
        sha = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
        final = os.fstat(stream.fileno())
        if _transfer_file_identity(initial) != _transfer_file_identity(final):
            raise ValueError("owned_transfer_file_changed")
        return sha.hexdigest(), initial


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
    source_identity = self.identity_by_id(owner) if owner else None
    if source_identity is None:
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
    # The Placement owner, not merely the library root, must own the
    # physical source. Otherwise a stale/foreign SQLite row can send another
    # profile's bytes through the legacy noncanonical transfer path.
    owner_directory = safe_library_component(
        source_identity.directory_name or owner, "default",
    )
    owner_root = root / owner_directory
    if (src == owner_root or not src.is_relative_to(owner_root)
            or any(
                other.identity_id != owner
                and safe_library_component(
                    other.directory_name or other.identity_id, "default",
                ) == owner_directory
                for other in self.identities
            )):
        raise ProviderConfigurationError("Emplacement source hors du profil propriétaire")
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
        # Pin the *whole* owner-relative path while hashing. The initial
        # path.resolve() check above cannot authorize subsequent opens after
        # a hostile parent-directory substitution.
        try:
            source_digest, source_stat = _owned_transfer_digest(src, owner_root)
        except (OSError, ValueError) as exc:
            raise ProviderConfigurationError(
                "Emplacement source hors du profil propriétaire"
            ) from exc

    from .transfer_storage_policy import (
        TRANSFER_STORAGE_CLASS, directory_for_storage_class,
        transfer_readable_filename,
    )

    # Q025: the destination *storage class* is independent of source
    # provider provenance. Keep the original provider on the new placement
    # and catalogue projection; never mint a recipient supplier right.
    provider = str(source.get("provider_id") or "shared")
    destination_folder = directory_for_storage_class(TRANSFER_STORAGE_CLASS)
    identity_part = safe_library_component(
        target_identity.directory_name or target_identity_id, "default",
    )
    storage_part = safe_library_component(destination_folder, "Acquisitions")
    legacy_provider_part = safe_library_component(provider, "local")
    identity_dir = root / identity_part
    expected_dir = identity_dir / storage_part
    legacy_dir = identity_dir / legacy_provider_part
    historical_shared_dir = identity_dir / "shared"
    # Do this before the service creates any directories: a symlink to an
    # external directory must not be followed by mkdir or by a later copy.
    if (identity_dir.is_symlink() or expected_dir.is_symlink()
            or identity_dir.resolve() != identity_dir
            or expected_dir.resolve() != expected_dir):
        raise ProviderConfigurationError("Répertoire cible symbolique interdit")
    dest_dir = self.library_identity_provider_dir(target_identity_id, destination_folder)
    if (dest_dir.is_symlink() or dest_dir.resolve() != expected_dir
            or not dest_dir.resolve().is_relative_to(root)):
        raise ProviderConfigurationError("Répertoire cible hors bibliothèque")

    artifact_id = str(source.get("artifact_id") or "").strip()
    default_owner = self.identities[0].identity_id if self.identities else None
    # The catalogue is descriptive, not proof of the Placement's ownership.
    # Imported/poisoned SQLite can associate a foreign profile's private
    # title/author/cover with the SAME placement_id. Never project it into
    # the recipient's catalogue just because this is an explicit local send.
    # Preserve v0.7 NULL-owner metadata for the default profile only.
    source_entries = [
        row for row in self.storage.catalog_entries()
        if str(row.get("placement_id") or "") == str(placement_id)
        and (
            row.get("identity_id") == owner
            or (row.get("identity_id") in (None, "")
                and owner == default_owner)
        )
        and (
            not row.get("artifact_id")
            or row.get("artifact_id") == artifact_id
        )
    ]
    if len(source_entries) > 1:
        raise ProviderConfigurationError("Notices source ambiguës")
    source_entry = source_entries[0] if source_entries else {}
    item_id = str(source.get("source_item_id") or source_entry.get("item_id") or "").strip() or None

    # A canonical Artifact is immutable final SHA evidence. If its source
    # bytes were altered, never copy them under that old Artifact ID. Legacy
    # placeholder rows without an actual Artifact are not upgraded by guess.
    checkpoint = None
    if not is_bundle and artifact_id.startswith("sha256:"):
        registered = self.storage.artifact_get(artifact_id)
        if registered is not None and (
            registered.get("sha256") != source_digest
            or registered.get("size") != source_stat.st_size
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
                or source_artifact.get("size") != source_stat.st_size):
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

    def verified_target(path: Path, *, historical: bool = False) -> bool:
        # Existing A→B receipts under provider/ and shared/ remain valid
        # in place. New copies always go to B/Acquisitions. Historical reuse
        # still requires the exact
        # donor/placement metadata gate below, not a matching name or SHA.
        accepted = path.parent == dest_dir or (
            historical and path.parent in {legacy_dir, historical_shared_dir}
        )
        if path.is_symlink() or path.resolve() != path or not accepted:
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
                and (row.get("provider_id") in (None, provider)
                     # A previously received copy may have been projected
                     # as 'shared' when the donor's provider was unknown.
                     # Exact transfer-origin evidence below still decides
                     # reuse; the folder name alone never does.
                     or (row.get("provider_id") == "shared"
                         and Path(str(row.get("path") or "")).parent in {
                         dest_dir, historical_shared_dir
                     }))
                and (row.get("source_item_id") is None
                     or str(row.get("source_item_id") or "") == str(item_id or ""))
                and row.get("artifact_id") == artifact_id
                and row.get("state") == "present"
                and type(meta) is dict
                and meta.get("materialized_from") == placement_id):
            path = Path(str(row.get("path") or ""))
            if (path.parent in {
                    dest_dir, legacy_dir, historical_shared_dir
                } and path != src
                    and verified_target(path, historical=True)):
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
        temp = Path(temporary)
        journaled = False
        created = None
        try:
            # Keep mkstemp's exclusive inode open: reopening the pathname
            # would follow a symlink substituted after mkstemp and could
            # overwrite unrelated bytes before the later SHA verification.
            with os.fdopen(fd, "wb") as out:
                created = os.fstat(out.fileno())
                try:
                    opened = _open_owned_transfer_file(src, owner_root)
                except (OSError, ValueError) as exc:
                    raise ProviderConfigurationError(
                        "Emplacement source hors du profil propriétaire"
                    ) from exc
                with os.fdopen(opened, "rb") as input_stream:
                    if (_transfer_file_identity(os.fstat(input_stream.fileno()))
                            != _transfer_file_identity(source_stat)):
                        raise ProviderConfigurationError(
                            "Source modifiée pendant la copie"
                        )
                    shutil.copyfileobj(input_stream, out)
                    out.flush()
                    os.fsync(out.fileno())
            # Even an equal-SHA replacement is not this newly created copy.
            # Never link/replace a foreign inode or symlink as the recipient.
            try:
                staged = os.stat(temp, follow_symlinks=False)
            except OSError as exc:
                raise ProviderConfigurationError("Fichier temporaire modifié") from exc
            if (not stat.S_ISREG(staged.st_mode)
                    or (staged.st_dev, staged.st_ino) !=
                       (created.st_dev, created.st_ino)):
                raise ProviderConfigurationError("Fichier temporaire modifié")
            try:
                current_digest, current_stat = _owned_transfer_digest(src, owner_root)
            except (OSError, ValueError) as exc:
                raise ProviderConfigurationError(
                    "Source modifiée pendant la copie"
                ) from exc
            if (sha256_file(temp) != source_digest
                    or current_digest != source_digest
                    or _transfer_file_identity(current_stat) !=
                       _transfer_file_identity(source_stat)):
                raise ProviderConfigurationError("Source modifiée pendant la copie")
            if replace:
                if not verified_target(dest, historical=True):
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
            if not journaled and created is not None:
                # A different process may have replaced this pathname
                # with another regular file. Do not unlink its inode merely
                # because it inherited our temporary filename. Keep the
                # foreign path for explicit inspection instead.
                try:
                    remaining = os.stat(temp, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if ((remaining.st_dev, remaining.st_ino)
                            == (created.st_dev, created.st_ino)):
                        temp.unlink(missing_ok=True)

    recovered = checkpoint.recover() if checkpoint is not None and owned_copy is None else None
    # An *already verified* historical B copy can carry the old generic
    # 'shared' provider projection. Do not reassign that existing Placement
    # to the donor's now-enriched provider just to repair a catalogue row.
    recipient_provider = (
        owned_copy[1].get("provider_id") or provider
        if owned_copy is not None else provider
    )
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
                    "target_identity_id": target_identity_id, "provider_id": recipient_provider,
                }
            # Canonical publication survived, but its descriptive projection
            # did not. Repair it below using the SAME verified copy.
            status = "unchanged"
    elif recovered is not None:
        dest, status = recovered, "complete"
    else:
        dest = dest_dir / transfer_readable_filename(
            src, title=source_entry.get("title"), author=source_entry.get("author"),
        )
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
            # The source manifest was verified before catalogue lookups and
            # mkdir. A newly substituted member/parent symlink must be COPIED
            # as a symlink, never followed into another identity's bytes.
            # verified_target() rejects every resulting symlink before the
            # recipient gains a Placement.
            shutil.copytree(src, dest, symlinks=True)
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
            "storage_class": TRANSFER_STORAGE_CLASS,
            "file_origin_identity_id": owner,
        }
        if is_bundle:
            placement_meta["manifest_size"] = int(size_stat.st_size)
        else:
            placement_meta["file_size"] = int(size_stat.st_size)
        new_placement_id = self.storage.placement_put({
            "artifact_id": artifact_id, "path": str(dest),
            "identity_id": target_identity_id, "provider_id": recipient_provider,
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
            "storage_class": TRANSFER_STORAGE_CLASS,
        })
        self.storage.catalog_entry_put({
            "artifact_id": artifact_id, "placement_id": new_placement_id,
            "title": source_entry.get("title") or src.stem,
            "author": source_entry.get("author"),
            "media_format": source_entry.get("media_format") or src.suffix.lstrip(".").lower(),
            "identity_id": target_identity_id, "provider_id": recipient_provider, "item_id": item_id,
            "local_state": "local", "metadata": metadata,
        })
    else:
        indexed = self.catalog_index_path(
            dest, title=str(source_entry.get("title") or src.stem), item_id=item_id,
            identity_id=target_identity_id, provider_id=recipient_provider,
        )
        new_placement_id = indexed.get("placement_id") if type(indexed) is dict else None
    return {
        "status": status, "path": str(dest),
        "placement_id": new_placement_id,
        "target_identity_id": target_identity_id, "provider_id": recipient_provider,
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
