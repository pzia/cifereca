"""#4520: read-only local review of historical derived variants (schema v14).

A recipe and matching SHA bytes are NOT proof that a legacy file was produced
by this recipe, nor proof of an owner/borrower or provider entitlement. This
review never manufactures OriginLink, Placement, permission or migration token.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Literal

_MAX_BYTES = 4 * 1024**3
_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class LegacyDerivedReview:
    status: Literal["blocked", "physical_pair_only", "already_attested"]
    reason_code: str
    identity_id: str
    variant_id: str
    physical_pair_verified: bool = False
    derived_origin_attested: bool = False

    @property
    def may_migrate(self) -> bool:
        # A physically matching pair does not establish historic causality.
        # Mutation needs a separate independently attested operation receipt.
        return False


def _digest(path: Path, expected_sha: str, expected_size: int) -> bool:
    """Verify a regular no-follow file and its unchanged physical inode."""
    if (not hasattr(os, "O_NOFOLLOW") or not 0 < expected_size <= _MAX_BYTES
            or type(expected_sha) is not str or len(expected_sha) != 64):
        return False
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = None
    try:
        fd = os.open(path, flags)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
            return False
        hasher = hashlib.sha256()
        remaining = expected_size
        while remaining:
            chunk = os.read(fd, min(_CHUNK, remaining))
            if not chunk:
                return False
            hasher.update(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            return False
        after = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False)
        attrs = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        return (
            all(getattr(before, attr) == getattr(after, attr)
                == getattr(named, attr) for attr in attrs)
            and hasher.hexdigest() == expected_sha
        )
    except (OSError, OverflowError, ValueError):
        return False
    finally:
        if fd is not None:
            os.close(fd)


def _within_profile(path_text: str, root: Path, folder: Path) -> Path | None:
    """No symlink, relative traversal or cross-profile physical path."""
    if type(path_text) is not str or not path_text:
        return None
    path = Path(path_text)
    try:
        if (not path.is_absolute() or ".." in path.parts
                or not path.is_relative_to(folder)
                or path == folder or root.is_symlink()
                or folder.is_symlink()
                or path.resolve(strict=True) != path):
            return None
        current = folder
        for part in path.relative_to(folder).parts[:-1]:
            current = current / part
            if current.is_symlink() or not current.is_dir():
                return None
    except (OSError, RuntimeError, ValueError):
        return None
    return path


def review_legacy_derived_variant(
    storage, *, identity_id: str, variant_id: str, library_root: Path,
) -> LegacyDerivedReview:
    """Classify one exact identity's legacy variant, never act on the result.

    A physical pair MAY be verified; without the original immutable derived
    operation receipt it remains un-attributed even if its SHA matches the
    globally recorded recipe. No automatic OriginLink migration follows.
    """
    def blocked(reason: str, *, verified: bool = False) -> LegacyDerivedReview:
        return LegacyDerivedReview(
            "blocked", reason, identity_id if type(identity_id) is str else "",
            variant_id if type(variant_id) is str else "", verified, False,
        )

    if (type(identity_id) is not str or not 0 < len(identity_id) <= 256
            or type(variant_id) is not str or not 0 < len(variant_id) <= 256
            or not isinstance(library_root, Path)
            or not library_root.is_absolute()
            or library_root.is_symlink()):
        return blocked("invalid_request")
    try:
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            variant = db.execute(
                "SELECT artifact_id,source_artifact_id,source_sha256,"
                "transformer_plugin_id,transformer_version,profile_plugin_id,"
                "profile_version,options_fingerprint "
                "FROM artifact_variants WHERE variant_id=?",
                (variant_id,),
            ).fetchone()
            if variant is None:
                return blocked("variant_missing")
            child, source, source_sha = variant[:3]
            if (any(type(x) is not str or not x
                    for x in (child, source, source_sha, variant[3]))
                    or child == source
                    or source != "sha256:" + source_sha
                    or len(source_sha) != 64
                    or any(c not in "0123456789abcdef" for c in source_sha)
                    or not child.startswith("sha256:")
                    or len(child) != 71):
                return blocked("variant_invalid")
            expected_child_sha = child[7:]
            if any(c not in "0123456789abcdef" for c in expected_child_sha):
                return blocked("variant_invalid")
            artifacts = db.execute(
                "SELECT artifact_id,sha256,size FROM artifacts "
                "WHERE artifact_id IN (?,?)",
                (source, child),
            ).fetchall()
            data = {record[0]: (record[1], record[2]) for record in artifacts}
            if (len(data) != 2
                    or data[source][0] != source_sha
                    or data[child][0] != expected_child_sha
                    or any(type(data[aid][1]) is not int
                           or not 0 < data[aid][1] <= _MAX_BYTES
                           for aid in (source, child))):
                return blocked("artifact_sha_unverified")
            profile = db.execute(
                "SELECT directory_name FROM identities WHERE identity_id=?",
                (identity_id,),
            ).fetchone()
            if profile is None or type(profile[0]) is not str:
                return blocked("profile_unverified")
            dirname = profile[0]
            if (not dirname or dirname in (".", "..")
                    or "/" in dirname or "\\" in dirname
                    or "\x00" in dirname
                    or db.execute(
                        "SELECT 1 FROM identities WHERE identity_id<>? "
                        "AND directory_name=? LIMIT 1",
                        (identity_id, dirname),
                    ).fetchone()):
                return blocked("profile_unverified")
            placements = db.execute(
                "SELECT artifact_id,placement_id,path FROM placements "
                "WHERE identity_id=? AND artifact_id IN (?,?) "
                "AND state='present' LIMIT 5",
                (identity_id, source, child),
            ).fetchall()
            originals = [p for p in placements if p[0] == source]
            targets = [p for p in placements if p[0] == child]
            if len(originals) != 1 or len(targets) != 1:
                return blocked("physical_scope_ambiguous")
            links = db.execute(
                "SELECT evidence_ref,metadata_json FROM origin_links "
                "WHERE artifact_id=? AND identity_id=? AND origin_kind='derived' "
                "AND parent_artifact_id=? AND source_reference_id IS NULL LIMIT 3",
                (child, identity_id, source),
            ).fetchall()
            # Copy SQLite facts while the read snapshot remains consistent.
            original, target = originals[0], targets[0]
            # Only a receipt from the exact later Core publication can be
            # called already attested. A matching pair alone is still legacy.
            expected_meta = {
                "source_placement_id": original[1],
                "transformer_plugin_id": variant[3],
                "transformer_version": variant[4] or "",
                "profile_plugin_id": variant[5] or "",
                "profile_version": variant[6] or "",
                "options_fingerprint": variant[7] or "",
                "source_sha256": source_sha,
                "result_sha256": expected_child_sha,
            }
            attested = 0
            for evidence_ref, raw in links:
                if evidence_ref != f"derived:{variant_id}:{target[1]}":
                    continue
                parsed = json.loads(raw)
                if (type(parsed) is dict
                        and all(parsed.get(k) == v
                                for k, v in expected_meta.items())):
                    attested += 1

        root = library_root
        folder = root / dirname
        if root.resolve(strict=True) != root or folder.is_symlink():
            return blocked("profile_unverified")
        original_path = _within_profile(original[2], root, folder)
        target_path = _within_profile(target[2], root, folder)
        if (original_path is None or target_path is None
                or original_path == target_path
                or original_path.parent != target_path.parent):
            return blocked("physical_path_unverified")
        # Both separately observed byte streams must still match the stored
        # artifact SHA. This is a maintenance review, not normal UI rendering.
        if (not _digest(original_path, source_sha, data[source][1])
                or not _digest(target_path, expected_child_sha, data[child][1])):
            return blocked("physical_sha_unverified")
        if attested != 1:
            if links:
                return blocked("derived_origin_conflict", verified=True)
            return LegacyDerivedReview(
                "physical_pair_only", "derived_origin_unattributed",
                identity_id, variant_id, True, False,
            )
        return LegacyDerivedReview(
            "already_attested", "derived_origin_exact",
            identity_id, variant_id, True, True,
        )
    except (sqlite3.DatabaseError, OSError, TypeError, ValueError,
            AttributeError, RecursionError, RuntimeError):
        return blocked("inspection_unavailable")


__all__ = ["LegacyDerivedReview", "review_legacy_derived_variant"]
