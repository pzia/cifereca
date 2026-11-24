"""Core-only inert package inventory from independently pinned archive bytes.

The expected digest comes from the trusted package owner, not the archive,
install metadata, provider or plugin. No signature verification or activation
is implied; the caller must establish that trust and current installed state.
"""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import re
import zipfile

from ..plugins.manifest import MANIFEST_FILENAME, parse_manifest
from ..plugins.packages import PackageInstaller, MAX_FILES, MAX_UNCOMPRESSED_BYTES
from .notice_consolidator_manifest import bind_verified_package_consolidators, _PATH
from .notice_consolidator_runner import MAX_SCRIPT_BYTES

_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_MANIFEST_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True, repr=False)
class DigestPinnedConsolidators:
    package_id: str
    package_version: str
    archive_sha256: str
    candidates: tuple


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("consolidator_archive_duplicate_manifest_key")
        result[key] = value
    return result


def qualify_consolidator_archive(payload, *, expected_sha256):
    """Verify exact immutable bytes, then decode only bounded inert members."""
    if (type(payload) is not bytes or not 0 < len(payload) <= _MAX_ARCHIVE_BYTES
            or type(expected_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or sha256(payload).hexdigest() != expected_sha256):
        raise ValueError("consolidator_archive_digest_mismatch")
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("consolidator_archive_budget")
            seen = set()
            members = {}
            manifest_bytes = None
            for info in infos:
                PackageInstaller._validate_member(info)
                name = info.filename
                # Reject extraction aliases and duplicate directory/file names.
                canonical = name.rstrip("/")
                if ("\\" in name or any(p in ("", ".", "..") for p in canonical.split("/"))
                        or canonical in seen):
                    raise ValueError("consolidator_archive_ambiguous_member")
                seen.add(canonical)
                if name == MANIFEST_FILENAME:
                    if not 0 < info.file_size <= _MAX_MANIFEST_BYTES:
                        raise ValueError("consolidator_archive_budget")
                    manifest_bytes = archive.read(info)
                elif _PATH.fullmatch(name):
                    if len(members) >= 128 or not 0 < info.file_size <= MAX_SCRIPT_BYTES:
                        raise ValueError("consolidator_archive_budget")
                    members[name] = archive.read(info)
            if manifest_bytes is None:
                raise ValueError("consolidator_archive_missing_manifest")
            manifest = parse_manifest(json.loads(
                manifest_bytes.decode("utf-8"), object_pairs_hook=_unique,
            ))
            candidates = bind_verified_package_consolidators(manifest, verified_members=members)
    except Exception as exc:
        # Closed diagnostic: no paths or hostile manifest values escape.
        raise ValueError("consolidator_archive_invalid") from None
    return DigestPinnedConsolidators(
        manifest.package.id, manifest.package.version, expected_sha256, candidates,
    )
