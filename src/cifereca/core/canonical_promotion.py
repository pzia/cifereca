"""#3909: promote verified Core staging bytes before committing a final Placement.

The original completed-delivery checkpoint deliberately retains its private
staging path until SQLite publication succeeds. Promotion therefore COPIES,
never moves: a crash between copying and SQL can recover from the original
exact-source receipt without a second supplier GET.
"""
from __future__ import annotations

from collections import Counter, defaultdict
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
from .canonical_recovery import (
    _exact_borrower_bound_in_db, _path_verified, _stage_id,
)
from .source_convergence import SourceKey
from .transfer_storage_policy import (
    _filename_component, _utf8_prefix, transfer_readable_filename,
)


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


def _attested_epub_series(metadata: dict) -> tuple[str, str] | None:
    """Require one unambiguous embedded series and its own volume number."""
    original = metadata["original"]
    rows = [row for row in original if row["name"] == "meta"]
    calibre_names = [row["attributes"].get("content", "") for row in rows
                     if row["attributes"].get("name") == "calibre:series"]
    calibre_numbers = [row["attributes"].get("content", "") for row in rows
                       if row["attributes"].get("name") == "calibre:series_index"]
    pairs = set()
    if calibre_names or calibre_numbers:
        if len(calibre_names) != 1 or len(calibre_numbers) != 1:
            return None
        pairs.add((calibre_names[0].strip(), calibre_numbers[0].strip()))

    # The descriptive projection may fill a missing index from Calibre.
    # For a filename, require an explicit position on this exact collection.
    id_counts = Counter(row["attributes"].get("id") for row in original)
    by_ref = defaultdict(list)
    for row in rows:
        by_ref[row["attributes"].get("refines")].append(row)
    collections = [row for row in rows
                   if row["attributes"].get("property") == "belongs-to-collection"]
    for row in collections:
        ref = row["attributes"].get("id")
        if not ref:
            continue
        refinements = by_ref[f"#{ref}"]
        types = [meta["value"] for meta in refinements
                 if meta["attributes"].get("property") == "collection-type"]
        if "series" not in types:
            continue
        numbers = [meta["value"] for meta in refinements
                   if meta["attributes"].get("property") == "group-position"]
        if types != ["series"] or len(numbers) != 1 or id_counts[ref] != 1:
            return None
        pairs.add((row["value"], numbers[0]))

    if len(pairs) != 1:
        return None
    name, number = pairs.pop()
    if not _filename_component(name) or not re.fullmatch(r"[0-9]{1,6}(?:\.[0-9]{1,4})?", number):
        return None
    return name, number


def _readable_epub_target_names(stream, *, opaque_name: str) -> tuple[str, ...]:
    """Recognized names in recovery order, with the publication name last.

    This helper runs only after a complete, fsynced, SHA-checked copy is in
    Core's private directory. A damaged/encrypted EPUB keeps its opaque name.
    Bibliographic metadata is descriptive, never a supplier authorization.
    """
    fallback = (opaque_name + ".epub",)
    try:
        from ..epub_metadata import inspect_epub

        inspected = inspect_epub(stream, metadata_only=True)
        if inspected.get("epub_status") != "ok":
            return fallback
        metadata = inspected.get("epub_metadata")
        if type(metadata) is not dict:
            return fallback
        title = metadata.get("title")
        authors = metadata.get("authors")
        author = authors[0] if type(authors) is list and authors else None
        if type(title) is not str or not title.strip():
            return fallback
        # Include the entire original 256-bit receipt discriminator: two
        # same-title acquisitions must never alias one another's path.
        readable = transfer_readable_filename(
            Path("received.epub"), title=title, author=author,
        )
        prefix = _utf8_prefix(Path(readable).stem, 120)
        if not prefix:
            return fallback
        ending = " - " + opaque_name + ".epub"
        legacy_name = prefix + ending
        series = _attested_epub_series(metadata)
        title_text = _filename_component(title)
        if series is None or not title_text:
            return (legacy_name,)
        series_name, number = series
        lead = " - ".join(part for part in (
            _utf8_prefix(_filename_component(author), 32),
            _utf8_prefix(_filename_component(series_name), 36), number,
        ) if part) + " - "
        title_text = _utf8_prefix(title_text, 120 - len(lead.encode("utf-8")))
        return legacy_name, lead + title_text + ending
    except (OSError, ValueError, TypeError, RuntimeError, KeyError):
        return fallback


def _profile(storage, root: Path, source: SourceKey) -> tuple[Path, Path]:
    registered = storage.identities()
    matches = [
        x.get("directory_name") for x in registered
        if x.get("identity_id") == source.scope.identity_id
    ]
    if (len(matches) != 1 or type(matches[0]) is not str
            or not matches[0] or matches[0] in (".", "..")
            or "/" in matches[0] or "\\" in matches[0]):
        raise ValueError("canonical_promotion_profile_unknown")
    # Both pre-commit promotion and post-commit cleanup may touch physical
    # bytes. An imported registry with two owners for one directory cannot
    # authorize either action, regardless of matching SHA/account receipts.
    if sum(
        row.get("directory_name") == matches[0] for row in registered
    ) != 1:
        raise ValueError("canonical_promotion_profile_ambiguous")
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

    def physical_owner_current() -> bool:
        # _profile() proved uniqueness before an expensive read/copy. An
        # unrelated SQLite writer may rebind that directory while bytes are
        # streamed. Repeat the exact physical ownership check before
        # advertising a final path; never rely only on its old SHA.
        try:
            checked_root, checked_profile = _profile(
                storage, library_root, source,
            )
            return checked_root == root and checked_profile == profile
        except (ValueError, OSError):
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
        if not physical_owner_current():
            raise ValueError("canonical_promotion_profile_rebound")
        if suffix == ".epub":
            # Name from the exact *copied* bytes, never an unrelated provider
            # catalogue title or an unverified URL. The receipt discriminator
            # keeps crash recovery deterministic and prevents same-title
            # collisions. Legacy opaque final names still win above.
            staged_fd = os.open(
                temporary, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=dir_fd,
            )
            with os.fdopen(staged_fd, "rb") as staged:
                opened_copy = os.fstat(staged.fileno())
                if (opened_copy.st_dev, opened_copy.st_ino) != (made.st_dev, made.st_ino):
                    raise ValueError("canonical_promotion_destination_changed")
                names = _readable_epub_target_names(
                    staged, opaque_name=name,
                )
            for candidate in names:
                target = destination_dir / candidate
                if target.exists() or target.is_symlink():
                    try:
                        return _path_verified(
                            storage, root=root, source=source, path=target,
                            final_sha256=completed.final_sha256,
                        )
                    except (ValueError, OSError) as exc:
                        raise ValueError(
                            "canonical_promotion_destination_conflict"
                        ) from exc
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
        if not physical_owner_current():
            raise ValueError("canonical_promotion_profile_rebound")
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


def _metadata_staging_dependency(db, *, table: str, marker: str,
                                 current_operation: str = "") -> bool:
    """Protect staging references even when JSON escapes Unicode or separators.

    SQLite instr(raw, absolute_path) misses normal ensure_ascii JSON on a
    non-ASCII library path. Read only rows mentioning a staging namespace
    (plain or escaped dot), decode JSON with duplicate keys preserved, and
    compare decoded strings. Invalid/oversized metadata fails closed.
    """
    if table not in ("catalog_entries", "acquisition_ledger"):
        raise ValueError("canonical_cleanup_invalid_dependency_table")
    exclusion = " AND acquisition_id<>?" if table == "acquisition_ledger" else ""
    cursor = db.execute(
        f"SELECT metadata_json FROM {table} WHERE ("
        "instr(metadata_json,?)>0 OR instr(metadata_json,?)>0 "
        "OR instr(metadata_json,?)>0 OR instr(metadata_json,?)>0)"
        + exclusion,
        (marker, ".canonical-delivery", "\\u002e", "\\u002E")
        + ((current_operation,) if exclusion else ()),
    )
    for index, (raw,) in enumerate(cursor):
        # The scanner is a safety guard, not a reason to delete on an
        # incomplete inventory. Cap parsed input and total candidate rows.
        if index >= 100_000 or type(raw) is not str or len(raw) > 65536:
            return True
        if marker in raw:
            return True
        try:
            # A dict constructor discards duplicate keys, possibly including
            # the only path dependency. Retain every field and nested value.
            decoded = json.loads(raw, object_pairs_hook=lambda pairs: pairs)
        except (ValueError, TypeError, RecursionError):
            return True
        pending = [decoded]
        scanned = 0
        while pending:
            node = pending.pop()
            scanned += 1
            if scanned > 65536:
                return True
            if type(node) is str:
                if marker in node:
                    return True
            elif type(node) in (list, tuple):
                pending.extend(node)
    return False


def cleanup_published_staging(
    storage, *, source: SourceKey, completed: CompletedDelivery, library_root: Path,
) -> bool:
    """Return True only when a post-commit private staging attempt was removed.

    False means no cleanup occurred (non-staging path, absent/unsafe attempt,
    or another SQLite reference). Callers must not report successful physical
    reconciliation when the defensive dependency check retains the bytes.
    """
    # A completed receipt belongs to one exact SourceKey, not merely to the
    # same physical profile/account or Artifact hash. Do not erase the private
    # staging of a different right even if the caller is otherwise bound.
    if type(source) is not SourceKey or getattr(completed, "source", None) != source:
        return False
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
    # Capture the selected physical directory BEFORE the bounded SQLite
    # dependency scan. A regular directory or symlink can replace its name
    # while SQLite is locked; the old absolute-path rmtree could then erase
    # another profile's run with the same random-looking basename.
    base = profile / ".canonical-delivery"
    try:
        base_initial = os.stat(base, follow_symlinks=False)
        run_initial = os.stat(attempt, follow_symlinks=False)
        if (not stat.S_ISDIR(base_initial.st_mode)
                or not stat.S_ISDIR(run_initial.st_mode)):
            return False
    except OSError:
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
        # The registry can change after _profile() resolved the folder.
        # A shared or rebound directory must never be recursively deleted,
        # even when no legacy SQL row references the staging attempt.
        scope = source.scope
        registered = db.execute(
            "SELECT directory_name FROM identities WHERE identity_id=?",
            (scope.identity_id,),
        ).fetchone()
        aliased = db.execute(
            "SELECT 1 FROM identities WHERE directory_name=? "
            "AND identity_id<>? LIMIT 1",
            (profile.name, scope.identity_id),
        ).fetchone()
        if registered != (profile.name,) or aliased is not None:
            return False
        # This is the final SQLite read/physical-delete boundary, including
        # direct cleanup callers. A former buyer with the same receipt/SHA
        # must not erase staging after its connection was disabled or rebound.
        if not _exact_borrower_bound_in_db(db, scope):
            return False
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
        # JSON text can escape non-ASCII path components or separators.
        # Decode only staging-looking records and preserve duplicate fields:
        # an escaped operational dependency must prevent unlink as well.
        if _metadata_staging_dependency(
            db, table="catalog_entries", marker=marker,
        ):
            return False
        current_operation = _stage_id(source, completed.delivery_offer_id)
        if _metadata_staging_dependency(
            db, table="acquisition_ledger", marker=marker,
            current_operation=current_operation,
        ):
            return False
        # Pin the actual owner namespace, not the absolute pathname:
        # a symlink or same-path directory substituted while dependencies
        # were read cannot redirect a recursive removal across profiles.
        # shutil.rmtree's fd-relative implementation refuses a symlink run.
        flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                 | getattr(os, "O_NOFOLLOW", 0))
        try:
            parent_fd = os.open(base, flags)
        except OSError:
            return False
        try:
            parent_now = os.fstat(parent_fd)
            parent_visible = os.stat(base, follow_symlinks=False)
            run_now = os.stat(
                parts[1], dir_fd=parent_fd, follow_symlinks=False,
            )
            for before, after in (
                (base_initial, parent_now), (base_initial, parent_visible),
                (run_initial, run_now),
            ):
                if (not stat.S_ISDIR(after.st_mode)
                        or (before.st_dev, before.st_ino)
                        != (after.st_dev, after.st_ino)):
                    return False
            # No absolute path reaches the recursive deleter. After this
            # check a parent rename still leaves parent_fd on OUR namespace.
            shutil.rmtree(parts[1], dir_fd=parent_fd)
            return True
        except (OSError, ValueError):
            return False
        finally:
            os.close(parent_fd)


__all__ = ["promote_completed_delivery", "cleanup_published_staging"]
