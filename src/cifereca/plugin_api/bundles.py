from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

BUNDLE_MANIFEST_NAME = ".cifereca-bundle.json"
BUNDLE_MANIFEST_VERSION = 1


class BundleError(ValueError):
    """Invalid or incomplete autonomous bundle."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _format_for(path: Path) -> str:
    suffix = path.suffix.casefold().lstrip(".")
    if path.name.casefold().endswith(".m3u8"):
        return "m3u8"
    return suffix or "file"


@dataclass(frozen=True, slots=True)
class BundleMember:
    path: str
    sha256: str
    size: int
    format: str = ""
    title: str = ""
    author: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "path": self.path,
            "sha256": self.sha256,
            "size": int(self.size),
            "format": self.format,
        }
        if self.title:
            row["title"] = self.title
        if self.author:
            row["author"] = self.author
        if self.metadata:
            row["metadata"] = dict(self.metadata)
        return row


@dataclass(frozen=True, slots=True)
class BundleManifest:
    root: Path
    bundle_sha256: str
    media_format: str
    title: str = ""
    author: str = ""
    playlist: str = ""
    members: tuple[BundleMember, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return sum(int(member.size) for member in self.members)

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest": BUNDLE_MANIFEST_VERSION,
            "kind": "cifereca.bundle",
            "media_format": self.media_format,
            "title": self.title,
            "author": self.author,
            "playlist": self.playlist,
            "members": [member.as_dict() for member in self.members],
            "metadata": dict(self.metadata),
            "bundle_sha256": self.bundle_sha256,
        }


def _safe_relative_path(root: Path, value: str) -> Path:
    rel = Path(str(value or ""))
    if not value or rel.is_absolute() or ".." in rel.parts:
        raise BundleError(f"unsafe bundle member path: {value!r}")
    path = (root / rel).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise BundleError(f"bundle member escapes root: {value!r}") from exc
    return path


def _hash_payload(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()


def _manifest_payload(
    *,
    media_format: str,
    title: str,
    author: str,
    playlist: str,
    members: Iterable[BundleMember],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "manifest": BUNDLE_MANIFEST_VERSION,
        "kind": "cifereca.bundle",
        "media_format": str(media_format or "bundle"),
        "title": str(title or ""),
        "author": str(author or ""),
        "playlist": str(playlist or ""),
        "members": [member.as_dict() for member in members],
        "metadata": dict(metadata),
    }


def write_bundle_manifest(
    root: Path,
    *,
    media_format: str = "audiobook",
    title: str = "",
    author: str = "",
    playlist: str | None = None,
    member_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    metadata: Mapping[str, Any] | None = None,
    include: Iterable[str] | None = None,
) -> BundleManifest:
    """Create the autonomous technical manifest inside ``root``.

    The bundle remains portable: copying the directory preserves the playlist,
    every media member and the technical manifest required to reconstruct and
    validate the cifereca Artifact without SQLite.
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise BundleError(f"bundle root is not a directory: {root}")
    wanted = {str(value) for value in include} if include is not None else None
    rows: list[BundleMember] = []
    meta_by_path = {str(key): dict(value) for key, value in (member_metadata or {}).items()}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel == BUNDLE_MANIFEST_NAME or path.name.endswith(".part"):
            continue
        if wanted is not None and rel not in wanted:
            continue
        details = meta_by_path.get(rel, {})
        rows.append(BundleMember(
            path=rel,
            sha256=_sha256_file(path),
            size=path.stat().st_size,
            format=str(details.get("format") or _format_for(path)),
            title=str(details.get("title") or ""),
            author=str(details.get("author") or ""),
            metadata={k: v for k, v in details.items() if k not in {"format", "title", "author"}},
        ))
    if not rows:
        raise BundleError("bundle has no members")
    playlist_rel = str(playlist or "")
    if playlist_rel:
        playlist_path = _safe_relative_path(root, playlist_rel)
        if not playlist_path.is_file():
            raise BundleError(f"bundle playlist not found: {playlist_rel}")
        if playlist_rel not in {member.path for member in rows}:
            raise BundleError("bundle playlist must be included in members")
    payload = _manifest_payload(
        media_format=media_format, title=title, author=author, playlist=playlist_rel,
        members=rows, metadata=dict(metadata or {}),
    )
    digest = _hash_payload(payload)
    complete = {**payload, "bundle_sha256": digest}
    manifest_path = root / BUNDLE_MANIFEST_NAME
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp.write_text(json.dumps(complete, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(manifest_path)
    return BundleManifest(
        root=root, bundle_sha256=digest, media_format=str(media_format or "bundle"),
        title=str(title or ""), author=str(author or ""), playlist=playlist_rel,
        members=tuple(rows), metadata=dict(metadata or {}),
    )


def load_bundle_manifest(root: Path, *, verify: bool = True) -> BundleManifest:
    root = Path(root).expanduser().resolve()
    manifest_path = root / BUNDLE_MANIFEST_NAME
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError(f"cannot read bundle manifest: {manifest_path}") from exc
    if not isinstance(raw, dict) or int(raw.get("manifest") or 0) != BUNDLE_MANIFEST_VERSION:
        raise BundleError("unsupported bundle manifest")
    if raw.get("kind") != "cifereca.bundle":
        raise BundleError("invalid bundle kind")
    member_rows = raw.get("members")
    if not isinstance(member_rows, list) or not member_rows:
        raise BundleError("bundle has no members")
    members: list[BundleMember] = []
    for row in member_rows:
        if not isinstance(row, dict):
            raise BundleError("invalid bundle member")
        rel = str(row.get("path") or "")
        path = _safe_relative_path(root, rel)
        if not path.is_file():
            raise BundleError(f"bundle member missing: {rel}")
        expected_sha = str(row.get("sha256") or "").casefold()
        expected_size = int(row.get("size") or 0)
        if verify:
            if path.stat().st_size != expected_size:
                raise BundleError(f"bundle member size mismatch: {rel}")
            if _sha256_file(path).casefold() != expected_sha:
                raise BundleError(f"bundle member hash mismatch: {rel}")
        members.append(BundleMember(
            path=rel,
            sha256=expected_sha,
            size=expected_size,
            format=str(row.get("format") or _format_for(path)),
            title=str(row.get("title") or ""),
            author=str(row.get("author") or ""),
            metadata=dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), dict) else {},
        ))
    playlist = str(raw.get("playlist") or "")
    if playlist and playlist not in {member.path for member in members}:
        raise BundleError("bundle playlist is not a member")
    metadata = dict(raw.get("metadata") or {}) if isinstance(raw.get("metadata"), dict) else {}
    payload = _manifest_payload(
        media_format=str(raw.get("media_format") or "bundle"),
        title=str(raw.get("title") or ""),
        author=str(raw.get("author") or ""),
        playlist=playlist,
        members=members,
        metadata=metadata,
    )
    computed = _hash_payload(payload)
    declared = str(raw.get("bundle_sha256") or "").casefold()
    if declared and computed != declared:
        raise BundleError("bundle manifest hash mismatch")
    return BundleManifest(
        root=root,
        bundle_sha256=computed,
        media_format=str(raw.get("media_format") or "bundle"),
        title=str(raw.get("title") or ""),
        author=str(raw.get("author") or ""),
        playlist=playlist,
        members=tuple(members),
        metadata=metadata,
    )


def bundle_search_values(manifest: BundleManifest) -> list[str]:
    """Flatten searchable parent/member metadata while retaining one parent result."""
    values = [manifest.title, manifest.author]
    for member in manifest.members:
        values.extend((member.title, member.author, member.path))
        for key in ("subjects", "series", "collection", "publisher", "languages"):
            value = member.metadata.get(key)
            if isinstance(value, (list, tuple, set)):
                values.extend(str(item) for item in value)
            elif value is not None:
                values.append(str(value))
    return [str(value) for value in values if str(value or "").strip()]


__all__ = [
    "BUNDLE_MANIFEST_NAME", "BUNDLE_MANIFEST_VERSION", "BundleError", "BundleMember",
    "BundleManifest", "write_bundle_manifest", "load_bundle_manifest", "bundle_search_values",
]
