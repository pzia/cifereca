"""Bounded, read-only N3 EPUB failure qualification for N1 fallback (#6168).

Do NOT infer a documentary failure from a physical-cache MISS or a failed
download/extraction. Only an exact immutable Artifact, matched by SHA-256
on a no-follow descriptor, can prove that its recorded cover ZIP member
is definitively unusable. Even then, caller must independently select an
already-admitted origin/catalogue URL under the SAME N2 document scope.

This worker-only qualifier does not mutate N1/N2, fetch a URL, publish N3,
scan catalogues or grant an account/loan access to another book's image.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from typing import Literal
import zipfile

from ..epub_metadata import (
    MAX_ENTRIES, MAX_IMAGE, _bounded_directory, _image_kind, _read,
    cover_cache_dir, valid_cached_cover_path,
)
from .epub_cover_materialization import (
    ExactEpubCoverResource, _MAX_EPUB_BYTES,
)


AssessmentState = Literal["available", "retryable", "definitive_unusable"]
AssessmentCode = Literal[
    "n3_verified", "source_unverified", "materialization_failed",
    "member_missing_or_ambiguous", "member_unusable", "image_digest_mismatch",
]


@dataclass(frozen=True, slots=True)
class EpubCoverPhysicalAssessment:
    """Document-scoped evidence about PHYSICAL bytes only.

    The original N1 choice and N2 CAS token are not modified by this value.
    A consumer must verify the exact source+revision and a still-current N2
    selection before qualifying an independently attested URL fallback.
    """

    resource: ExactEpubCoverResource
    state: AssessmentState
    code: AssessmentCode

    @property
    def permits_url_fallback(self) -> bool:
        """Only proof tied to immutable, byte-for-byte identical EPUB qualifies."""
        return self.state == "definitive_unusable"


def qualify_failed_epub_cover(
    resource: ExactEpubCoverResource, *, source_path: Path,
) -> EpubCoverPhysicalAssessment:
    """Diagnose one N3 failure off GTK main; never re-resolve a DocumentRef.

    Do not use this on the warm success path. For a cold miss, try N3 recovery
    first; only if it fails may this bounded verifier classify immutable
    content. A transient I/O error, changed EPUB, cache eviction, cooldown
    or worker saturation MUST NOT cause a URL fallback.
    """
    if type(resource) is not ExactEpubCoverResource:
        raise TypeError("expected exact EPUB materialization resource")
    if (not isinstance(source_path, Path) or not source_path.is_absolute()
            or source_path.suffix.lower() not in (".epub", ".kepub")):
        raise ValueError("expected already-authorized EPUB Placement path")

    def state(kind: AssessmentState, code: AssessmentCode) -> EpubCoverPhysicalAssessment:
        return EpubCoverPhysicalAssessment(resource, kind, code)

    target = cover_cache_dir() / resource.cache_name
    if valid_cached_cover_path(target):
        return state("available", "n3_verified")

    try:
        descriptor = os.open(
            source_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode)
                    or not 0 < info.st_size <= _MAX_EPUB_BYTES):
                return state("retryable", "source_unverified")
            def stamp(value):
                return (value.st_dev, value.st_ino, value.st_size,
                        value.st_mtime_ns, value.st_ctime_ns)

            def unchanged():
                # The bytes and the currently named Placement must still
                # be the same regular file, not a moved/replaced inode.
                return (stamp(os.fstat(handle.fileno())) == stamp(info)
                        and stamp(os.stat(source_path, follow_symlinks=False)) == stamp(info))

            digest = hashlib.sha256()
            remaining = info.st_size
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    return state("retryable", "source_unverified")
                remaining -= len(chunk)
                digest.update(chunk)
            if (handle.read(1) or not unchanged()
                    or digest.hexdigest() != resource.artifact_sha256):
                return state("retryable", "source_unverified")
            handle.seek(0)
            _bounded_directory(handle)
            with zipfile.ZipFile(handle) as archive:
                if len(archive.infolist()) > MAX_ENTRIES:
                    return state("retryable", "source_unverified")
                try:
                    cover_bytes = _read(archive, resource.member, MAX_IMAGE)
                except ValueError as exc:
                    # Only our exact bounded parser codes may certify an
                    # immutable source's missing/unsafe cover member.
                    if not unchanged():
                        return state("retryable", "source_unverified")
                    code = exc.args[0] if type(exc) is ValueError and len(exc.args) == 1 else None
                    if code == "ambiguous_or_missing_entry":
                        return state("definitive_unusable", "member_missing_or_ambiguous")
                    if code in ("oversized_or_symlink_entry", "oversized_entry"):
                        return state("definitive_unusable", "member_unusable")
                    return state("retryable", "source_unverified")

            if not unchanged():
                return state("retryable", "source_unverified")

        kind = _image_kind(cover_bytes)
        if kind is None:
            return state("definitive_unusable", "member_unusable")
        actual = hashlib.sha256(cover_bytes).hexdigest() + "." + kind
        if actual != resource.cache_name:
            return state("definitive_unusable", "image_digest_mismatch")
        # Exact EPUB and exact embedded image both remain valid. A failed
        # materialization here is a transient N3 concern, NOT reason to
        # promote a cached public URL over the EPUB.
        return state("retryable", "materialization_failed")
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile,
            EOFError, zipfile.LargeZipFile):
        # No private ZIP filename/path/error message reaches diagnostic N0.
        return state("retryable", "source_unverified")


__all__ = ["EpubCoverPhysicalAssessment", "qualify_failed_epub_cover"]
