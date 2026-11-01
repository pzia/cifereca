"""#3909: promote verified Core staging bytes before committing a final Placement.

The original completed-delivery checkpoint deliberately retains its private
staging path until SQLite publication succeeds. Promotion therefore COPIES,
never moves: a crash between copying and SQL can recover from the original
exact-source receipt without a second supplier GET.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat

from ..catalog import LocalCatalog
from .canonical_publication import _physical_target, _same_file
from .canonical_recovery import _path_verified, _stage_id
from .source_convergence import SourceKey


_ATTEMPT = re.compile(r"run-[0-9a-f]{32}")


def _stage_parts(physical: Path, profile: Path) -> tuple[str, ...] | None:
    try:
        parts = physical.relative_to(profile).parts
    except ValueError as exc:
        raise ValueError("canonical_promotion_profile_mismatch") from exc
    if not parts or parts[0] != ".canonical-delivery":
        return None
    if (len(parts) < 3 or not _ATTEMPT.fullmatch(parts[1])
            or parts[-1] in ("", ".", "..")):
        raise ValueError("canonical_promotion_invalid_staging_namespace")
    return parts


def _profile(storage, root: Path, source: SourceKey) -> tuple[Path, Path]:
    matches = [
        x.get("directory_name") for x in storage.identities()
        if x.get("identity_id") == source.scope.identity_id
    ]
    if (len(matches) != 1 or type(matches[0]) is not str
            or not matches[0] or matches[0] in (".", "..")
            or "/" in matches[0] or "\\" in matches[0]):
        raise ValueError("canonical_promotion_profile_unknown")
    canonical_root = Path(os.path.abspath(root.expanduser()))
    profile = canonical_root.resolve() / matches[0]
    if (canonical_root.is_symlink() or profile.is_symlink()
            or not profile.is_dir()):
        raise ValueError("canonical_promotion_profile_unsafe")
    return canonical_root, profile


def promote_completed_delivery(
    storage, *, source: SourceKey, completed: CompletedDelivery, library_root: Path,
) -> Path:
    """Return final, independently verified path for canonical SQLite B.

    Only the already-admitted executor calls this, with a persisted exact
    delivery receipt and acquisition lock. A manually provided finished path
    outside the private Core staging namespace is left intact for legacy
    compatibility. A file within Core staging MUST be promoted before B.
    """
    from .convergence_execution import CompletedDelivery

    if (type(source) is not SourceKey or type(completed) is not CompletedDelivery
            or completed.source != source
            or not isinstance(library_root, Path)):
        raise ValueError("canonical_promotion_admission_required")
    root, profile = _profile(storage, library_root, source)
    original = _path_verified(
        storage, root=root, source=source,
        path=completed.path, final_sha256=completed.final_sha256,
    )
    parts = _stage_parts(original, profile)
    if parts is None:
        return original

    destination_dir = profile / "Acquisitions"
    if destination_dir.is_symlink():
        raise ValueError("canonical_promotion_destination_symlink")
    destination_dir.mkdir(mode=0o700, exist_ok=True)
    if destination_dir.is_symlink() or not destination_dir.is_dir():
        raise ValueError("canonical_promotion_destination_unsafe")

    scope = source.scope
    coordinates = (
        scope.identity_id, scope.provider_id, scope.credential_id,
        scope.account_id, source.branch, source.edition_id, source.right_id,
        source.kind, completed.delivery_offer_id, completed.receipt_ref,
        completed.final_sha256,
    )
    name = hashlib.sha256(json.dumps(
        coordinates, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    suffix = ".pdf" if LocalCatalog._media_format(original) == "pdf" else ".epub"
    target = destination_dir / (name + suffix)
    # The exact receipt is a part of the opaque target name. An unrelated
    # same-SHA file cannot be adopted as this acquisition's Placement.
    if target.exists() or target.is_symlink():
        try:
            return _path_verified(
                storage, root=root, source=source,
                path=target, final_sha256=completed.final_sha256,
            )
        except (ValueError, OSError) as exc:
            raise ValueError("canonical_promotion_destination_conflict") from exc

    before = os.stat(original, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("canonical_promotion_source_invalid")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    dest_flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                  | getattr(os, "O_NOFOLLOW", 0))
    # Never expose a partially copied EPUB/PDF under its final name. A crash
    # during the copy leaves only a hidden .part; the exact, journaled source
    # staging receipt can safely resume without another supplier GET.
    temporary = ".canonical-promote-" + secrets.token_hex(16) + ".part"
    dir_fd = os.open(
        destination_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    made = None
    linked = False

    def directory_current() -> bool:
        try:
            opened = os.fstat(dir_fd)
            current = os.stat(destination_dir, follow_symlinks=False)
            return (
                stat.S_ISDIR(opened.st_mode)
                and stat.S_ISDIR(current.st_mode)
                and (opened.st_dev, opened.st_ino)
                == (current.st_dev, current.st_ino)
            )
        except OSError:
            return False

    def own_entry(name: str) -> bool:
        if made is None:
            return False
        try:
            current = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            return (stat.S_ISREG(current.st_mode)
                    and (current.st_dev, current.st_ino)
                    == (made.st_dev, made.st_ino))
        except OSError:
            return False

    try:
        if not directory_current():
            raise ValueError("canonical_promotion_destination_changed")
        with os.fdopen(os.open(original, flags), "rb") as reader:
            opened = os.fstat(reader.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino)
                    != (before.st_dev, before.st_ino)):
                raise ValueError("canonical_promotion_source_changed")
            output_fd = os.open(temporary, dest_flags, 0o600, dir_fd=dir_fd)
            with os.fdopen(output_fd, "wb") as writer:
                made = os.fstat(writer.fileno())
                digest = hashlib.sha256()
                while True:
                    block = reader.read(1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
                    writer.write(block)
                writer.flush()
                os.fsync(writer.fileno())
        if (digest.hexdigest() != completed.final_sha256
                or not _same_file(original, before)):
            raise ValueError("canonical_promotion_sha_mismatch")
        if not directory_current() or not own_entry(temporary):
            raise ValueError("canonical_promotion_destination_changed")
        # A no-replace hardlink is an atomic namespace promotion. No other
        # thread can ever open a partially written final file; a foreign
        # final target is never overwritten or adopted by filename alone.
        try:
            os.link(
                temporary, target.name, src_dir_fd=dir_fd,
                dst_dir_fd=dir_fd, follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise ValueError("canonical_promotion_destination_conflict") from exc
        linked = True
        os.fsync(dir_fd)
        verified = _path_verified(
            storage, root=root, source=source,
            path=target, final_sha256=completed.final_sha256,
        )
        if (not directory_current() or not own_entry(target.name)
                or not _same_file(original, before)):
            raise ValueError("canonical_promotion_destination_changed")
        # Staging remains untouched until the Core B transaction commits.
        return verified
    except Exception:
        # This method never commits B. Discard only our own new hardlink;
        # do not delete a different inode swapped in by another process.
        if linked and own_entry(target.name):
            os.unlink(target.name, dir_fd=dir_fd)
            os.fsync(dir_fd)
        raise
    finally:
        # Cleanup cannot turn a verified final copy into a false error.
        # If interrupted before this point, a hidden .part is non-canonical
        # and the original receipt remains available to retry.
        if own_entry(temporary):
            try:
                os.unlink(temporary, dir_fd=dir_fd)
                os.fsync(dir_fd)
            except OSError:
                pass
        os.close(dir_fd)


def cleanup_published_staging(
    storage, *, source: SourceKey, completed: CompletedDelivery, library_root: Path,
) -> bool:
    """Return True only when a post-commit private staging attempt was removed.

    False means no cleanup occurred (non-staging path, absent/unsafe attempt,
    or another SQLite reference). Callers must not report successful physical
    reconciliation when the defensive dependency check retains the bytes.
    """
    root, profile = _profile(storage, library_root, source)
    # A missing old receipt file is not a reason to revoke a committed B.
    try:
        original = _physical_target(completed.path, root, profile.name)
    except (ValueError, OSError):
        return False
    parts = _stage_parts(original, profile)
    if parts is None:
        return False
    attempt = profile / ".canonical-delivery" / parts[1]
    # Never delete if the namespace has been replaced or redirected.
    if attempt.is_symlink() or not attempt.is_dir():
        return False
    # A Core delivery attempt is private, but legacy import/index tools may
    # still have registered another physical or operational reference under
    # it. Never turn those rows into dangling paths as a cleanup side effect.
    #
    # BEGIN IMMEDIATE protects the read+unlink decision from concurrent SQLite
    # writers. The acquisition lock is already held by both Core call sites.
    # A conservative metadata hit defers deletion, never publication or GET.
    prefix = str(attempt) + os.sep
    marker = str(attempt)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for table, column in (
            ("placements", "path"),
            ("backup_items", "local_path"),
        ):
            if db.execute(
                f"SELECT 1 FROM {table} WHERE {column}=? "
                f"OR substr({column},1,?)=? LIMIT 1",
                (marker, len(prefix), prefix),
            ).fetchone():
                return False
        # Path references in catalogue or other operation checkpoints are
        # operational until reconciled. No JSON structure is trusted here:
        # searching the serialized text also catches nested path fields.
        if db.execute(
            "SELECT 1 FROM catalog_entries "
            "WHERE instr(metadata_json,?)>0 LIMIT 1",
            (marker,),
        ).fetchone():
            return False
        current_operation = _stage_id(source, completed.delivery_offer_id)
        if db.execute(
            "SELECT 1 FROM acquisition_ledger "
            "WHERE acquisition_id<>? AND instr(metadata_json,?)>0 LIMIT 1",
            (current_operation, marker),
        ).fetchone():
            return False
        # Preserve the original receipt until B is already committed (caller
        # contract), but only remove a private run with no other registered
        # consumer. Any error leaves the staging bytes in place.
        shutil.rmtree(attempt)
        return True


__all__ = ["promote_completed_delivery", "cleanup_published_staging"]
