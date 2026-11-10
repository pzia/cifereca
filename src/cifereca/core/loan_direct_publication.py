"""P1 #5225: no-clobber publication of a staged direct digital loan.

This is a narrow safety bridge for the historical loan-auto path, NOT the
full canonical-delivery transaction. The caller still owns the loan ledger,
Core indexing, and source/identity admission; never infer a provider right
from a physical path or from matching file bytes.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile


class DirectLoanPublicationRefused(ValueError):
    """Closed technical reason; do not include source paths or provider data."""


def _bounded_stem(path: Path) -> str:
    stem = path.stem.encode("utf-8")[:120].decode("utf-8", "ignore")
    stem = stem.strip(" .-_")
    return stem or "livre"


def publish_direct_loan_candidate(
    staged_path: Path, *, work_dir: Path, library_target_dir: Path,
) -> Path:
    """Copy a provider's staged file to a new library name without overwriting.

    A private, per-attempt temp is created on the *destination* filesystem.
    Hardlinking it to the final name provides an atomic exclusive commit,
    even across two independent loans with the same original filename.
    A prior file is never replaced, unlinked, or adopted as an entitlement.
    """
    if (not isinstance(staged_path, Path)
            or not isinstance(work_dir, Path)
            or not isinstance(library_target_dir, Path)
            or not staged_path.name or staged_path.name in (".", "..")
            or not staged_path.suffix or len(staged_path.name.encode("utf-8")) > 240):
        raise DirectLoanPublicationRefused("loan_direct_candidate_invalid")
    try:
        if (staged_path.is_symlink() or work_dir.is_symlink()
                or library_target_dir.is_symlink()
                or library_target_dir.parent.is_symlink()
                or staged_path.parent.resolve(strict=True)
                   != work_dir.resolve(strict=True)):
            raise DirectLoanPublicationRefused("loan_direct_stage_scope_invalid")
        work_stat = staged_path.stat(follow_symlinks=False)
        if (not stat.S_ISREG(work_stat.st_mode)
                or work_stat.st_size <= 0 or work_stat.st_nlink != 1):
            raise DirectLoanPublicationRefused("loan_direct_stage_invalid")
    except OSError:
        raise DirectLoanPublicationRefused("loan_direct_stage_unavailable") from None

    # The exact identity/provider directory comes from LibraryService; do
    # not resolve a symlinked destination into a different profile.
    library_target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (library_target_dir.is_symlink()
            or library_target_dir.parent.is_symlink()):
        raise DirectLoanPublicationRefused("loan_direct_target_scope_invalid")

    temporary: Path | None = None
    try:
        src_fd = os.open(staged_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(src_fd, "rb") as src:
            current = os.fstat(src.fileno())
            if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                    or (current.st_dev, current.st_ino, current.st_size,
                        current.st_mtime_ns)
                       != (work_stat.st_dev, work_stat.st_ino,
                           work_stat.st_size, work_stat.st_mtime_ns)):
                raise DirectLoanPublicationRefused("loan_direct_stage_changed")
            fd, temp_name = tempfile.mkstemp(
                prefix=".loan-publish-", dir=library_target_dir,
            )
            temporary = Path(temp_name)
            digest = hashlib.sha256()
            with os.fdopen(fd, "wb") as out:
                while True:
                    part = src.read(1024 * 1024)
                    if not part:
                        break
                    digest.update(part)
                    out.write(part)
                out.flush()
                os.fsync(out.fileno())
                published_inode = os.fstat(out.fileno())
            after = os.fstat(src.fileno())
            if ((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    != (current.st_dev, current.st_ino, current.st_size,
                        current.st_mtime_ns)):
                raise DirectLoanPublicationRefused("loan_direct_stage_changed")
        try:
            stage_now = staged_path.stat(follow_symlinks=False)
        except OSError:
            raise DirectLoanPublicationRefused("loan_direct_stage_changed") from None
        if (not stat.S_ISREG(stage_now.st_mode)
                or (stage_now.st_dev, stage_now.st_ino, stage_now.st_size,
                    stage_now.st_mtime_ns)
                   != (work_stat.st_dev, work_stat.st_ino,
                       work_stat.st_size, work_stat.st_mtime_ns)):
            raise DirectLoanPublicationRefused("loan_direct_stage_changed")

        hashed = digest.hexdigest()
        stem, suffix = _bounded_stem(staged_path), staged_path.suffix
        choices = (
            staged_path.name,
            f"{stem} - {hashed[:16]}{suffix}",
            f"{stem} - {hashed}{suffix}",
        )
        # Every attempted link is exclusive. A same-name file is a collision,
        # not evidence of the same provider right or permission to overwrite.
        for index in range(35):
            name = (choices[index] if index < len(choices)
                    else f"{stem} - {hashed[:16]}-{index - 2}{suffix}")
            target = library_target_dir / name
            # A private temp pathname can itself be replaced after its fd is
            # closed. Verify the exact inode written above, not merely a
            # plausible file with identical size or content.
            temp_now = temporary.stat(follow_symlinks=False)
            if (not stat.S_ISREG(temp_now.st_mode)
                    or (temp_now.st_dev, temp_now.st_ino, temp_now.st_size)
                       != (published_inode.st_dev, published_inode.st_ino,
                           published_inode.st_size)):
                raise DirectLoanPublicationRefused("loan_direct_temp_changed")
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                continue
            else:
                # Re-check the exclusive link before returning a completed
                # path. A symlink swapped into the temp at link-time must
                # never become a published loan.
                published = target.stat(follow_symlinks=False)
                if (not stat.S_ISREG(published.st_mode)
                        or (published.st_dev, published.st_ino,
                            published.st_size)
                           != (published_inode.st_dev, published_inode.st_ino,
                               published_inode.st_size)):
                    # Only unlink a path that we just hardlinked, and never
                    # overwrite or adopt an unrelated preexisting artifact.
                    current_target = target.stat(follow_symlinks=False)
                    if ((current_target.st_dev, current_target.st_ino)
                            == (published.st_dev, published.st_ino)):
                        target.unlink()
                    raise DirectLoanPublicationRefused("loan_direct_temp_changed")
                try:
                    stage_now = staged_path.stat(follow_symlinks=False)
                except OSError:
                    stage_now = None
                if (stage_now is not None
                        and stat.S_ISREG(stage_now.st_mode)
                        and (stage_now.st_dev, stage_now.st_ino,
                             stage_now.st_size, stage_now.st_mtime_ns)
                            == (work_stat.st_dev, work_stat.st_ino,
                                work_stat.st_size, work_stat.st_mtime_ns)):
                    try:
                        staged_path.unlink()
                    except OSError:
                        # Post-publish staging cleanup is not a failed delivery.
                        pass
                return target
        raise DirectLoanPublicationRefused("loan_direct_destination_collisions")
    except DirectLoanPublicationRefused:
        raise
    except OSError:
        raise DirectLoanPublicationRefused("loan_direct_publication_failed") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


__all__ = ["DirectLoanPublicationRefused", "publish_direct_loan_candidate"]
