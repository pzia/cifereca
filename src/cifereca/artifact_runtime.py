from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
import hashlib
import json
import os
import secrets
import stat
from pathlib import Path
import shutil
import time
from typing import Any

from .library_files import sha256_file
from .plugin_api import (
    ArtifactFormat, ArtifactInput, ArtifactPlacementRef, ArtifactPossessionRef, ArtifactRef,
    ArtifactRoutePlan, ArtifactRouteRequest, ArtifactRouteTarget, ArtifactTransferResult,
    ArtifactVariant, BookRef, Conflict, LibraryMutationPlan, LibraryMutationRequest,
    LibraryMutationResult, LogicalBookArtifacts, NotFound, TaskControl, TransformRequest,
    TransformResult, UnsupportedOperation,
)


class _LibraryFilenameConflict(Conflict):
    """Closed, catchable collision code; no path or identity in the error."""
    code = "library_filename_conflict"

class _DuplicateScopeConflict(Conflict):
    """A fixed UI-safe reason; never include private path or borrower text."""
    code = "placement_not_duplicate"


class _CanonicalDedupConflict(Conflict):
    """A typed refusal with an explicit stable code for the maintenance host."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code

class _VariantScopeConflict(Conflict):
    """Deleting a shared SHA is not authority to unlink another book."""
    code = "artifact_variant_scope_conflict"


class _VariantConflict(Conflict):
    """Typed refusal for transform safety without changing plugin_api errors."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class _VariantNotFound(NotFound):
    """Typed missing-source refusal within the existing NotFound contract."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code




def _enforce_ordinary_dedup_hold(request: LibraryMutationRequest) -> None:
    """P0 #3185: a stored plan, forced plugin or direct broker cannot unlink.

    No preference may override the suspended keeper-policy decision #4496.
    Non-mutating preview and recovery of an *existing* tombstone are separate
    operations and remain admitted to their normal Core validations.
    """
    operation = request.operation
    if operation not in ("canonical_dedup", "delete_duplicate"):
        return
    action = (_canonical_maintenance_action(request)
              if operation == "canonical_dedup" else "commit")
    if action != "commit":
        return
    from .core.canonical_dedup_supervisor import _ordinary_cleanup_enabled
    if not _ordinary_cleanup_enabled():
        raise _CanonicalDedupConflict(
            "Nettoyage de doublons suspendu — décision produit #4496",
            code="ordinary_cleanup_suspended",
        )

def _canonical_b_pair(row: dict[str, Any] | None, keeper: dict[str, Any] | None) -> bool:
    """The legacy source-scoped deleter cannot retire canonical B placements."""
    return (
        type(row) is dict and type(keeper) is dict
        and row.get("provider_id") is None and row.get("source_item_id") is None
        and keeper.get("provider_id") is None and keeper.get("source_item_id") is None
    )


def _canonical_maintenance_root(service: Any) -> Path:
    """Only the configured library root may authorize a physical maintenance."""
    config = getattr(service, "config", None)
    get = getattr(config, "get", None)
    if not callable(get):
        raise _CanonicalDedupConflict("Racine de bibliothèque indisponible", code="canonical_dedup_root_unavailable")
    raw = get("library.directory", None)
    if (type(raw) is not str or not 0 < len(raw) <= 4096
            or any(ord(c) < 32 for c in raw)):
        raise _CanonicalDedupConflict("Racine de bibliothèque indisponible", code="canonical_dedup_root_unavailable")
    try:
        root = Path(os.path.expandvars(raw)).expanduser()
        if (not root.is_absolute() or not root.is_dir()):
            raise ValueError("non_canonical_root")
        return root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise _CanonicalDedupConflict("Racine de bibliothèque indisponible", code="canonical_dedup_root_unavailable") from None


def _canonical_maintenance_action(request: LibraryMutationRequest) -> str:
    """Strictly opt-in: a malformed plan or option may never authorize unlink."""
    options = request.options
    if type(options) is not dict:
        raise _CanonicalDedupConflict("Option de dédoublonnage invalide", code="canonical_dedup_invalid_action")
    action = options.get("action", "preview")
    if type(action) is not str or action not in ("preview", "commit", "recover"):
        raise _CanonicalDedupConflict("Option de dédoublonnage invalide", code="canonical_dedup_invalid_action")
    return action





def _media_type(fmt: str, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    value = str(fmt or "").lower()
    return {
        "epub": "application/epub+zip",
        "pdf": "application/pdf",
        "cbz": "application/vnd.comicbook+zip",
        "kepub": "application/epub+zip",
        "txt": "text/plain",
    }.get(value, "application/octet-stream")


def _identity_placements(storage: Any, artifact_id: str, *, identity_id: str | None = None,
                         default_identity_id: str | None = None) -> list[dict[str, Any]]:
    """Return present placements for one identity, or all for legacy maintenance calls."""
    return [
        placement for placement in storage.placements(state="present")
        if str(placement.get("artifact_id") or "") == str(artifact_id)
        and (identity_id is None or
             str(placement.get("identity_id") or default_identity_id or "") == identity_id)
    ]


def _artifact_ref(storage: Any, artifact_id: str, *, book_id: str | None = None,
                  identity_id: str | None = None,
                  default_identity_id: str | None = None) -> ArtifactRef | None:
    row = storage.artifact_get(str(artifact_id))
    if row is None:
        return None
    placements = _identity_placements(
        storage, artifact_id, identity_id=identity_id, default_identity_id=default_identity_id,
    )
    if identity_id is not None and not placements:
        return None
    filename = Path(str(placements[0].get("path"))).name if placements else ""
    metadata = dict(row.get("metadata") or {})
    fmt = str(row.get("media_format") or "unknown")
    return ArtifactRef(
        artifact_id=str(row.get("artifact_id") or artifact_id),
        book_id=book_id,
        format=ArtifactFormat(_media_type(fmt, row.get("mime_type")), fmt, str(metadata.get("drm") or "none")),
        size_bytes=int(row.get("size") or 0),
        sha256=str(row.get("sha256") or ""),
        variant_kind=str(metadata.get("variant_kind") or "original"),
        filename=filename,
    )


def _placement_ref(row: dict[str, Any]) -> ArtifactPlacementRef:
    metadata = dict(row.get("metadata") or {})
    kind = str(metadata.get("placement_kind") or ("storage" if metadata.get("storage_target_id") else "library"))
    return ArtifactPlacementRef(
        placement_id=str(row.get("placement_id") or ""),
        artifact_id=str(row.get("artifact_id") or ""),
        identity_id=str(row.get("identity_id")) if row.get("identity_id") not in {None, ""} else None,
        provider_id=str(row.get("provider_id") or ""),
        source_item_id=str(row.get("source_item_id") or ""),
        state=str(row.get("state") or "present"),
        kind=kind,
        filename=Path(str(row.get("path") or "")).name,
        storage_target_id=str(metadata.get("storage_target_id")) if metadata.get("storage_target_id") else None,
        storage_plugin_id=str(metadata.get("storage_plugin_id")) if metadata.get("storage_plugin_id") else None,
        facts={k: v for k, v in metadata.items() if k not in {"path"}},
    )


def _variant_ref(storage: Any, row: dict[str, Any], *, book_id: str = "",
                 identity_id: str | None = None,
                 default_identity_id: str | None = None) -> ArtifactVariant | None:
    artifact = storage.artifact_get(str(row.get("artifact_id") or ""))
    if artifact is None:
        return None
    placements = _identity_placements(
        storage, str(row.get("artifact_id") or ""), identity_id=identity_id,
        default_identity_id=default_identity_id,
    )
    if identity_id is not None and not placements:
        return None
    filename = Path(str(placements[0].get("path"))).name if placements else ""
    metadata = dict(row.get("metadata") or {})
    fmt = str(artifact.get("media_format") or "unknown")
    return ArtifactVariant(
        artifact_id=str(artifact.get("artifact_id") or ""), book_id=str(book_id or metadata.get("book_id") or ""),
        source_artifact_id=str(row.get("source_artifact_id") or ""), variant_kind=str(row.get("variant_kind") or "derived"),
        format=ArtifactFormat(_media_type(fmt, artifact.get("mime_type")), fmt, str((artifact.get("metadata") or {}).get("drm") or "none")),
        size_bytes=int(artifact.get("size") or 0), sha256=str(artifact.get("sha256") or ""), filename=filename,
        transformer_plugin_id=str(row.get("transformer_plugin_id") or ""), transformer_version=str(row.get("transformer_version") or ""),
        profile_plugin_id=str(row.get("profile_plugin_id")) if row.get("profile_plugin_id") else None,
        profile_version=str(row.get("profile_version")) if row.get("profile_version") else None,
        options_fingerprint=str(row.get("options_fingerprint") or ""), source_sha256=str(row.get("source_sha256") or ""),
        created_at=str(row.get("created_at") or ""), stale=bool(metadata.get("stale", False)), facts=metadata,
    )


def _variant_file_stamp(path: Path) -> tuple[int, ...] | None:
    """No-follow immutable-file signature; a source pathname is not proof."""
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            return None
        return tuple(int(getattr(info, attr)) for attr in (
            "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
        ))
    except (OSError, ValueError):
        return None


def _variant_directory_stamp(path: Path) -> tuple[int, int] | None:
    try:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            return None
        return int(info.st_dev), int(info.st_ino)
    except (OSError, ValueError):
        return None


def _publish_variant_exclusive(staged: Path, source_parent: Path, *,
                               basename: str, sha256: str,
                               directory_stamp: tuple[int, int],
                               staging_directory_stamp: tuple[int, int],
                               staging_root_stamp: tuple[int, int],
                               claimed_paths: frozenset[str]) -> Path:
    """Hardlink a verified staging inode atomically, with no replace path.

    Both directory descriptors pin the original physical locations. Even a
    preexisting symlink, unrelated file or a racing writer cannot be clobbered
    by an exclusive os.link. The short-hash fallback is attempted only once.
    """
    suffix = Path(basename).suffix
    stem = Path(basename).stem
    candidates = (basename, f"{stem}-{sha256[:8]}{suffix}")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    if (_variant_directory_stamp(source_parent) != directory_stamp
            or _variant_directory_stamp(staged.parent) != staging_directory_stamp
            or _variant_directory_stamp(staged.parent.parent) != staging_root_stamp):
        raise _VariantConflict("Répertoire de variante modifié", code="artifact_variant_directory_changed")
    parent_fd = stage_fd = None
    try:
        parent_fd = os.open(source_parent, flags)
        stage_fd = os.open(staged.parent, flags)
        parent_info = os.fstat(parent_fd)
        stage_dir_info = os.fstat(stage_fd)
        if ((parent_info.st_dev, parent_info.st_ino) != directory_stamp
                or (stage_dir_info.st_dev, stage_dir_info.st_ino) != staging_directory_stamp
                or _variant_directory_stamp(staged.parent) != staging_directory_stamp
                or _variant_directory_stamp(staged.parent.parent) != staging_root_stamp
                or _variant_directory_stamp(source_parent) != directory_stamp):
            raise _VariantConflict("Répertoire de variante modifié", code="artifact_variant_directory_changed")
        stage_info = os.stat(staged.name, dir_fd=stage_fd, follow_symlinks=False)
        if not stat.S_ISREG(stage_info.st_mode):
            raise _VariantConflict("Fichier de variante non régulier", code="artifact_variant_output_unverified")
        for candidate in candidates:
            # A missing physical file can still have an authoritative
            # Placement/other-owner claim. Never rebind that SQL path.
            if str(source_parent / candidate) in claimed_paths:
                continue
            try:
                os.link(staged.name, candidate, src_dir_fd=stage_fd,
                        dst_dir_fd=parent_fd, follow_symlinks=False)
            except FileExistsError:
                continue
            except OSError as error:
                raise _VariantConflict("Publication de variante impossible",
                               code="artifact_variant_publish_failed") from error
            try:
                final_info = os.stat(candidate, dir_fd=parent_fd, follow_symlinks=False)
                current_stage = os.stat(staged.name, dir_fd=stage_fd,
                                        follow_symlinks=False)
                # Creating/removing a hardlink legitimately changes inode
                # ctime; compare the stable content-bearing fields instead.
                signature = lambda info: (
                    info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                )
                if (not stat.S_ISREG(final_info.st_mode)
                        or signature(final_info) != signature(stage_info)
                        or signature(current_stage) != signature(stage_info)
                        or sha256_file(source_parent / candidate) != sha256
                        or _variant_directory_stamp(source_parent) != directory_stamp):
                    raise _VariantConflict("Variante ou répertoire changé",
                                   code="artifact_variant_publish_race")
                os.unlink(staged.name, dir_fd=stage_fd)
                return source_parent / candidate
            except Exception:
                # The exclusive hardlink is our new entry; never touch a
                # preexisting path, and remove only this newly linked inode.
                now = os.stat(candidate, dir_fd=parent_fd, follow_symlinks=False)
                if (now.st_dev, now.st_ino) == (stage_info.st_dev, stage_info.st_ino):
                    os.unlink(candidate, dir_fd=parent_fd)
                raise
        raise _VariantConflict("Nom de variante déjà occupé",
                       code="artifact_variant_filename_conflict")
    except OSError as error:
        raise _VariantConflict("Publication de variante impossible",
                       code="artifact_variant_publish_failed") from error
    finally:
        if stage_fd is not None:
            os.close(stage_fd)
        if parent_fd is not None:
            os.close(parent_fd)


@dataclass(slots=True)
class _InputLease:
    _path: Path
    _artifact: ArtifactRef
    source_placement_id: str = ""
    source_identity_id: str = ""
    source_stamp: tuple[int, ...] | None = None
    source_directory_stamp: tuple[int, int] | None = None
    @property
    def path(self) -> Path: return self._path
    @property
    def artifact(self) -> ArtifactRef: return self._artifact


@dataclass(slots=True)
class _OutputLease:
    _path: Path
    source: ArtifactRef
    output_format: ArtifactFormat
    proposed_filename: str
    variant_kind: str
    source_path: Path | None = None
    source_placement_id: str = ""
    source_identity_id: str = ""
    source_stamp: tuple[int, ...] | None = None
    source_directory_stamp: tuple[int, int] | None = None
    staging_directory_stamp: tuple[int, int] | None = None
    staging_root_stamp: tuple[int, int] | None = None
    active: bool = True
    @property
    def path(self) -> Path: return self._path
    def suggested_suffix(self) -> str:
        fmt = str(self.output_format.format or "").strip().lower()
        return ("." + fmt) if fmt else self._path.suffix


@dataclass(slots=True)
class ArtifactExecutionBrokerImpl:
    service: Any

    def _source_placement(
        self, artifact: ArtifactRef, *, identity_id: str | None = None,
        placement_id: str | None = None,
    ) -> dict[str, Any]:
        rows = [row for row in self.service.storage.placements(state="present")
                if str(row.get("artifact_id") or "") == artifact.artifact_id]
        if not rows:
            raise _VariantNotFound("Artefact local introuvable", code="artifact_missing")
        if identity_id is not None or placement_id is not None:
            # Both coordinates are mandatory, exact, and immutable throughout
            # the lease. A bare identity or SHA cannot choose a first copy.
            if (type(identity_id) is not str or not identity_id
                    or type(placement_id) is not str or not placement_id):
                raise _VariantConflict("Placement source non précisé",
                                       code="artifact_source_scope_incomplete")
            rows = [row for row in rows
                    if row.get("identity_id") == identity_id
                    and row.get("placement_id") == placement_id]
            if not rows:
                raise _VariantConflict("Placement source exact introuvable",
                                       code="artifact_source_scope_unverified")
        # Legacy unscoped calls stay safe only with one physical copy globally.
        if len(rows) != 1:
            raise _VariantConflict("Placement source ambigu", code="artifact_source_ambiguous")
        row = rows[0]
        # A valid SHA owned only by profile A still is not available to a
        # user whose active profile is B. ArtifactRef has no identity, so the
        # host's current selection is the second mandatory authority.
        active_identity = getattr(getattr(self.service, "current_identity", None),
                                  "identity_id", None)
        if (type(active_identity) is not str or not active_identity
                or active_identity != row.get("identity_id")):
            raise _VariantConflict("Profil actif distinct du placement",
                           code="artifact_source_identity_mismatch")
        path = Path(str(row.get("path") or ""))
        if (type(row.get("identity_id")) is not str
                or not row["identity_id"] or not path.is_absolute()
                or _variant_file_stamp(path) is None
                or _variant_directory_stamp(path.parent) is None):
            raise _VariantConflict("Placement source non vérifié", code="artifact_source_unverified")
        # P0 #4519: a global SHA is never permission to read another
        # identity's physical copy. The registered profile subtree and the
        # Core-owned library root are the only acceptable path authority.
        config = getattr(self.service, "config", None)
        get = getattr(config, "get", None)
        raw = get("library.directory", None) if callable(get) else None
        profile = self.service.storage.identity_get(row["identity_id"])
        directory = profile.get("directory_name") if type(profile) is dict else None
        if (type(raw) is not str or not raw or type(directory) is not str
                or directory in ("", ".", "..")
                or "/" in directory or "\\" in directory):
            raise _VariantConflict("Profil de variante non vérifié", code="artifact_source_scope_unverified")
        try:
            root = Path(os.path.expandvars(raw)).expanduser()
            owner_folder = root / directory
            if (not root.is_absolute()
                    or _variant_directory_stamp(root) is None
                    or _variant_directory_stamp(owner_folder) is None
                    or path.resolve(strict=True) != path
                    or not path.is_relative_to(owner_folder)
                    or any(
                        (owner_folder.joinpath(*path.relative_to(owner_folder).parts[:n])).is_symlink()
                        for n in range(1, len(path.relative_to(owner_folder).parts) + 1)
                    )
                    or sum(
                        x.get("directory_name") == directory
                        for x in self.service.storage.identities()
                    ) != 1):
                raise ValueError("variant_profile_path_unverified")
        except (OSError, RuntimeError, ValueError, TypeError) as error:
            raise _VariantConflict("Chemin de variante hors profil",
                           code="artifact_source_scope_unverified") from error
        return row

    def input_lease(self, artifact: ArtifactRef, *,
                    identity_id: str | None = None,
                    placement_id: str | None = None) -> _InputLease:
        row = self._source_placement(
            artifact, identity_id=identity_id, placement_id=placement_id,
        )
        path = Path(str(row["path"]))
        stamp = _variant_file_stamp(path)
        folder = _variant_directory_stamp(path.parent)
        if (stamp is None or folder is None
                or not artifact.sha256 or sha256_file(path) != artifact.sha256
                or _variant_file_stamp(path) != stamp
                or _variant_directory_stamp(path.parent) != folder):
            raise _VariantConflict("Le contenu local ne correspond plus à l'artefact",
                           code="artifact_checksum_mismatch")
        return _InputLease(path, artifact, str(row.get("placement_id") or ""),
                           row["identity_id"], stamp, folder)

    def output_lease(self, source: ArtifactRef, *, output_format: ArtifactFormat,
                     proposed_filename: str = "", variant_kind: str = "",
                     identity_id: str | None = None,
                     placement_id: str | None = None) -> _OutputLease:
        row = self._source_placement(
            source, identity_id=identity_id, placement_id=placement_id,
        )
        source_path = Path(str(row["path"]))
        stamp = _variant_file_stamp(source_path)
        folder = _variant_directory_stamp(source_path.parent)
        if stamp is None or folder is None or sha256_file(source_path) != source.sha256:
            raise _VariantConflict("Source de variante modifiée", code="artifact_source_changed")
        staging_root = source_path.parent / ".cifereca-staging"
        staging = staging_root / "variants"
        if ((staging_root.exists() and _variant_directory_stamp(staging_root) is None)
                or (staging.exists() and _variant_directory_stamp(staging) is None)):
            raise _VariantConflict("Staging de variante non vérifié", code="artifact_variant_staging_unverified")
        staging.mkdir(parents=True, exist_ok=True)
        if (_variant_directory_stamp(source_path.parent) != folder
                or _variant_directory_stamp(staging_root) is None
                or _variant_directory_stamp(staging) is None
                or _variant_file_stamp(source_path) != stamp):
            raise _VariantConflict("Répertoire de source modifié", code="artifact_source_changed")
        suffix = "." + str(output_format.format or source_path.suffix.lstrip(".") or "bin").lower().lstrip(".")
        # Do not mkstemp(path) then unlink(path): a swapped staging
        # parent between those operations could redirect unlink to another
        # profile. Reserve and release the random filename under one pinned
        # O_NOFOLLOW directory descriptor.
        stage_pin = _variant_directory_stamp(staging)
        stage_root_pin = _variant_directory_stamp(staging_root)
        if stage_pin is None or stage_root_pin is None:
            raise _VariantConflict("Staging non vérifié", code="artifact_variant_staging_unverified")
        dir_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        stage_fd = None
        name = ""
        try:
            stage_fd = os.open(staging, dir_flags)
            info = os.fstat(stage_fd)
            if ((info.st_dev, info.st_ino) != stage_pin
                    or _variant_directory_stamp(staging) != stage_pin
                    or _variant_directory_stamp(staging_root) != stage_root_pin):
                raise _VariantConflict("Staging déplacé", code="artifact_variant_staging_changed")
            for _ in range(8):
                attempt = f"variant-{secrets.token_hex(12)}{suffix}"
                try:
                    fd = os.open(
                        attempt, os.O_CREAT | os.O_EXCL | os.O_RDWR
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600, dir_fd=stage_fd,
                    )
                except FileExistsError:
                    continue
                os.close(fd)
                os.unlink(attempt, dir_fd=stage_fd)
                name = attempt
                break
            if not name:
                raise _VariantConflict("Staging saturé", code="artifact_variant_staging_unavailable")
        except OSError as error:
            raise _VariantConflict("Staging inaccessible",
                           code="artifact_variant_staging_unavailable") from error
        finally:
            if stage_fd is not None:
                os.close(stage_fd)
        if (_variant_directory_stamp(staging) != stage_pin
                or _variant_directory_stamp(staging_root) != stage_root_pin
                or _variant_directory_stamp(source_path.parent) != folder):
            raise _VariantConflict("Staging déplacé", code="artifact_variant_staging_changed")
        path = staging / name
        return _OutputLease(
            path, source, output_format, str(proposed_filename or ""),
            str(variant_kind or "derived"),
            source_path=source_path,
            source_placement_id=str(row.get("placement_id") or ""),
            source_identity_id=row["identity_id"],
            source_stamp=stamp,
            source_directory_stamp=folder,
            staging_directory_stamp=stage_pin,
            staging_root_stamp=stage_root_pin,
        )

    @staticmethod
    def _assert_output_lease(destination: Any) -> _OutputLease:
        if not isinstance(destination, _OutputLease) or not destination.active:
            raise ValueError("invalid or inactive artifact output lease")
        return destination

    def _version_for_plugin(self, plugin_id: str | None) -> str:
        if not plugin_id:
            return ""
        manager = getattr(self.service, "plugin_manager", None)
        if manager is None: return ""
        try:
            status = next((s for s in manager.statuses() if s.descriptor.id == plugin_id), None)
            return str(status.descriptor.version) if status else ""
        except Exception:
            return ""

    def commit_variant(self, *, source: ArtifactRef, destination: Any, result: TransformResult, transformer_plugin_id: str, profile_plugin_id: str | None = None, options_fingerprint: str = "") -> ArtifactRef:
        lease = self._assert_output_lease(destination)
        if lease.source.artifact_id != source.artifact_id:
            raise _VariantConflict("Le lease de sortie ne correspond pas à la source", code="artifact_lease_source_mismatch")
        path = lease.path
        # A moved/replaced source directory may also make the staged output
        # disappear from its old path. Preserve the stronger source lease
        # refusal instead of misreporting a missing transformer output.
        if _variant_directory_stamp(lease.source_path.parent) != lease.source_directory_stamp:
            raise _VariantConflict("Répertoire de source modifié", code="artifact_source_changed")
        if _variant_file_stamp(path) is None:
            raise _VariantNotFound("Le transformer n'a produit aucun fichier régulier",
                           code="artifact_output_missing")
        stage_stamp = _variant_file_stamp(path)
        actual_sha = sha256_file(path)
        actual_size = path.stat().st_size
        if _variant_file_stamp(path) != stage_stamp:
            raise _VariantConflict("Staging de variante modifié", code="artifact_variant_output_changed")
        source_placement = self._source_placement(
            source, identity_id=lease.source_identity_id,
            placement_id=lease.source_placement_id,
        )
        source_path = Path(str(source_placement["path"]))
        if (lease.source_path != source_path
                or lease.source_placement_id != str(source_placement.get("placement_id") or "")
                or lease.source_identity_id != source_placement["identity_id"]
                or _variant_directory_stamp(source_path.parent) != lease.source_directory_stamp
                or _variant_file_stamp(source_path) != lease.source_stamp
                or sha256_file(source_path) != source.sha256
                or _variant_file_stamp(source_path) != lease.source_stamp):
            raise _VariantConflict("Source de variante remplacée", code="artifact_source_changed")
        if result.sha256 and actual_sha != result.sha256:
            raise _VariantConflict("Checksum de transformation invalide", code="artifact_output_checksum_mismatch")
        if result.size_bytes and actual_size != int(result.size_bytes):
            raise _VariantConflict("Taille de transformation invalide", code="artifact_output_size_mismatch")
        # An identity transform has exactly the original content-addressed
        # artifact_id. Persisting a self-variant would make delete_variant
        # select and physically unlink the ORIGINAL book placements. The
        # produced staging file is disposable; preserve the existing source
        # artifact and do not create a false independent derivative.
        if actual_sha == source.sha256:
            self.abort_output(lease)
            return source
        existing = self.service.storage.artifact_variant_find(
            source_sha256=source.sha256, transformer_plugin_id=transformer_plugin_id,
            profile_plugin_id=profile_plugin_id, options_fingerprint=options_fingerprint,
        )
        if existing:
            artifact = _artifact_ref(
                self.service.storage, str(existing.get("artifact_id") or ""),
                book_id=source.book_id,
                identity_id=lease.source_identity_id,
            )
            if artifact is not None:
                try:
                    self.input_lease(artifact)
                except (Conflict, NotFound, OSError, ValueError):
                    artifact = None
            if artifact is not None:
                self.abort_output(lease)
                return artifact
        proposed = Path(str(result.proposed_filename or lease.proposed_filename or "")).name
        if not proposed:
            suffix = "." + str(result.output_format.format or lease.output_format.format or source_path.suffix.lstrip(".")).lstrip(".")
            proposed = f"{source_path.stem} [{lease.variant_kind or 'variant'}]{suffix}"
        suffix = Path(proposed).suffix or ("." + str(result.output_format.format or lease.output_format.format or "bin").lstrip("."))
        stem = Path(proposed).stem or source_path.stem
        if (source_path.parent != path.parent.parent.parent
                or _variant_file_stamp(path) != stage_stamp
                or _variant_file_stamp(source_path) != lease.source_stamp
                or _variant_directory_stamp(source_path.parent) != lease.source_directory_stamp):
            raise _VariantConflict("Staging ou source déplacé", code="artifact_variant_scope_changed")
        claimed_paths = frozenset(
            str(row.get("path") or "") for row in self.service.storage.placements()
        )
        final = _publish_variant_exclusive(
            path, source_path.parent, basename=stem + suffix,
            sha256=actual_sha, directory_stamp=lease.source_directory_stamp,
            staging_directory_stamp=lease.staging_directory_stamp,
            staging_root_stamp=lease.staging_root_stamp,
            claimed_paths=claimed_paths,
        )
        lease.active = False
        # Revalidate the physical target is still our just-published inode;
        # never report a variant if an unrelated writer substituted the path.
        if (not final.is_file() or sha256_file(final) != actual_sha
                or _variant_directory_stamp(final.parent) != lease.source_directory_stamp):
            raise _VariantConflict("Variante publiée déplacée", code="artifact_variant_publish_race")
        # A source or another profile may have claimed the filename while
        # the exact physical hardlink was being installed. Stop before any
        # SQL writer can rebind that existing Placement.
        if any(str(row.get("path") or "") == str(final)
               for row in self.service.storage.placements()):
            raise _VariantConflict("Chemin de variante déjà réservé",
                           code="artifact_variant_filename_conflict")
        now = int(time.time())
        artifact_id = f"sha256:{actual_sha}"
        # The filesystem hardlink is already verified. Commit all SQLite
        # authorities together so an injected failure never exposes a partial
        # local derivative or falsely inherited supplier provenance (#4520).
        from .core.derived_variant_publication import (
            DerivedPublicationConflict, publish_derived_variant,
        )
        try:
            publish_derived_variant(
                self.service.storage,
                parent_artifact_id=source.artifact_id,
                parent_sha256=source.sha256,
                parent_placement_id=lease.source_placement_id,
                parent_path=source_path,
                identity_id=lease.source_identity_id,
                published_path=final,
                output_sha256=actual_sha,
                output_size=actual_size,
                media_format=result.output_format.format
                or lease.output_format.format or suffix.lstrip("."),
                mime_type=result.output_format.media_type
                or lease.output_format.media_type,
                variant_kind=lease.variant_kind or "derived",
                transformer_id=transformer_plugin_id,
                transformer_version=self._version_for_plugin(transformer_plugin_id),
                profile_id=profile_plugin_id,
                profile_version=self._version_for_plugin(profile_plugin_id),
                options_fingerprint=options_fingerprint,
                book_id=source.book_id,
                warnings=result.warnings,
                facts=dict(result.facts),
            )
        except DerivedPublicationConflict as exc:
            raise _VariantConflict(
                "Publication canonique de variante refusée", code=str(exc),
            ) from None
        ref = _artifact_ref(
            self.service.storage, artifact_id, book_id=source.book_id,
            identity_id=lease.source_identity_id,
        )
        if ref is None: raise RuntimeError("committed artifact disappeared")
        return ref

    def abort_output(self, destination: Any) -> None:
        lease = self._assert_output_lease(destination)
        # A malicious transformer may swap the whole staging directory for
        # a symlink into a foreign profile before raising. Never unlink
        # through such a pathname; use a no-follow pinned directory FD.
        lease.active = False
        expected = lease.staging_directory_stamp
        path = lease.path
        if (expected is None
                or _variant_directory_stamp(path.parent) != expected
                or _variant_directory_stamp(path.parent.parent) != lease.staging_root_stamp):
            raise _VariantConflict("Staging de variante déplacé", code="artifact_variant_staging_changed")
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path.parent, flags)
            try:
                directory = os.fstat(fd)
                if ((directory.st_dev, directory.st_ino) != expected
                        or _variant_directory_stamp(path.parent) != expected
                        or _variant_directory_stamp(path.parent.parent) != lease.staging_root_stamp):
                    raise _VariantConflict("Staging de variante déplacé",
                                   code="artifact_variant_staging_changed")
                try:
                    os.unlink(path.name, dir_fd=fd)
                except FileNotFoundError:
                    pass
            finally:
                os.close(fd)
        except OSError as error:
            raise _VariantConflict("Staging de variante inaccessible",
                           code="artifact_variant_staging_changed") from error

    def variants(self, source: ArtifactRef) -> tuple[ArtifactVariant, ...]:
        out=[]
        for row in self.service.storage.artifact_variants(source_artifact_id=source.artifact_id):
            variant = _variant_ref(self.service.storage, row, book_id=source.book_id or "")
            if variant is not None: out.append(variant)
        return tuple(out)

    def _select_transformer(self, request: TransformRequest, plugin_id: str = "") -> tuple[str, Any]:
        manager = getattr(self.service, "plugin_manager", None)
        if manager is None: raise RuntimeError("plugin manager unavailable")
        rows = list(manager.services("document.transformer"))
        if plugin_id:
            rows = [row for row in rows if row.plugin_id == plugin_id]
        rows = [row for row in rows if "artifact.transform" in set(row.capabilities)] or rows
        for row in rows:
            supports = getattr(row.service, "supports", None)
            if callable(supports):
                try:
                    if not supports(request): continue
                except Exception: continue
            return row.plugin_id, row.service
        raise _VariantNotFound("Aucun transformer compatible", code="artifact_transformer_unavailable")

    def execute_transform(self, request: TransformRequest, *, transformer_plugin_id: str = "", control: TaskControl | None = None) -> ArtifactRef:
        if control is None:
            control = _NullTaskControl()
        control.check_cancelled()
        plugin_id, transformer = self._select_transformer(request, transformer_plugin_id)
        # Validate the current source bytes before considering an existing
        # derivative. A stale local source must never reuse an older variant.
        source_lease = self.input_lease(request.source)
        fingerprint = hashlib.sha256(
            json.dumps(dict(request.options), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        identity_id = None
        try:
            placement = self._source_placement(request.source)
            identity_id = placement.get("identity_id")
        except Exception:
            pass

        existing = self.service.storage.artifact_variant_find(
            source_sha256=request.source.sha256,
            transformer_plugin_id=plugin_id,
            profile_plugin_id=request.profile_plugin_id,
            options_fingerprint=fingerprint,
        )
        if existing:
            metadata = dict(existing.get("metadata") or {})
            transformer_version = self._version_for_plugin(plugin_id)
            profile_version = self._version_for_plugin(request.profile_plugin_id)
            version_match = (
                str(existing.get("transformer_version") or "") == transformer_version
                and str(existing.get("profile_version") or "") == profile_version
                and not bool(metadata.get("stale", False))
            )
            if version_match:
                cached = _artifact_ref(
                    self.service.storage,
                    str(existing.get("artifact_id") or ""),
                    book_id=request.source.book_id,
                    identity_id=identity_id,
                )
                if cached is not None:
                    try:
                        # Also verifies that a present placement still matches
                        # the canonical checksum.
                        self.input_lease(cached)
                    except Exception:
                        cached = None
                if cached is not None:
                    control.check_cancelled()
                    control.report(current=1, total=1, message="Variante existante réutilisée")
                    recorder = getattr(self.service, "_record_action", None)
                    if callable(recorder):
                        recorder(
                            identity_id,
                            "transformation",
                            "success",
                            plugin_id=plugin_id,
                            facts={
                                "capability": request.capability_id,
                                "source_artifact_id": request.source.artifact_id,
                                "artifact_id": cached.artifact_id,
                                "cache_hit": True,
                            },
                        )
                    return cached

        plan = transformer.plan(request, source_lease, control)
        destination = self.output_lease(
            request.source,
            output_format=plan.output_format,
            proposed_filename=plan.proposed_filename,
            variant_kind=request.capability_id or "derived",
        )
        try:
            # The transformer may move or swap the original after plan.
            # The lease pins both path and current physical inode.
            if (destination.source_placement_id != source_lease.source_placement_id
                    or destination.source_identity_id != source_lease.source_identity_id
                    or destination.source_path != source_lease.path
                    or destination.source_stamp != source_lease.source_stamp
                    or destination.source_directory_stamp != source_lease.source_directory_stamp):
                raise _VariantConflict("Source changée pendant le plan", code="artifact_source_changed")
            result = transformer.transform(request, source_lease, destination, control)
            control.check_cancelled()
            committed = self.commit_variant(
                source=request.source,
                destination=destination,
                result=result,
                transformer_plugin_id=plugin_id,
                profile_plugin_id=request.profile_plugin_id,
                options_fingerprint=fingerprint,
            )
            recorder = getattr(self.service, "_record_action", None)
            if callable(recorder):
                recorder(
                    identity_id,
                    "transformation",
                    "success",
                    plugin_id=plugin_id,
                    facts={
                        "capability": request.capability_id,
                        "source_artifact_id": request.source.artifact_id,
                        "artifact_id": committed.artifact_id,
                        "cache_hit": False,
                    },
                )
            return committed
        except Exception as exc:
            if destination.active:
                try: self.abort_output(destination)
                except Exception: pass
            recorder = getattr(self.service, "_record_action", None)
            if callable(recorder): recorder(identity_id, "transformation", "failed", plugin_id=plugin_id, facts={"capability": request.capability_id, "error": type(exc).__name__})
            raise


class _NullTaskControl:
    def cancelled(self) -> bool: return False
    def check_cancelled(self) -> None: return None
    def report(self, *, current: int | None = None, total: int | None = None, message: str = "") -> None: return None


@dataclass(slots=True)
class LibraryReadBrokerImpl:
    service: Any

    def _default_identity(self) -> str | None:
        identities = getattr(self.service, "identities", ()) or ()
        return str(getattr(identities[0], "identity_id", "") or "") or None if identities else None

    def _visible(self, row: dict[str, Any], identity_id: str | None) -> bool:
        return identity_id is None or str(
            row.get("identity_id") or self._default_identity() or ""
        ) == identity_id

    def artifact(self, artifact_id: str, *, identity_id: str | None = None) -> ArtifactRef | None:
        return _artifact_ref(
            self.service.storage, str(artifact_id), identity_id=identity_id,
            default_identity_id=self._default_identity(),
        )

    def preferred_artifact(self, book_id: str, *, purpose: str = "read",
                           identity_id: str | None = None) -> ArtifactRef | None:
        view = self.book(book_id, identity_id=identity_id)
        if view is None or not view.artifacts:
            return None
        source_ids = {v.source_artifact_id for v in view.variants}
        originals = [a for a in view.artifacts if a.artifact_id not in {v.artifact_id for v in view.variants}]
        source = originals[0] if originals else view.artifacts[0]
        preferred = self.service.storage.metadata_get(f"artifact.variant.preferred.{source.artifact_id}.{str(purpose or 'read')}")
        if preferred:
            candidate = next((a for a in view.artifacts if a.artifact_id == preferred), None)
            if candidate is not None:
                return candidate
        return source

    def _view(self, entries: list[dict[str, Any]], book_id: str,
              *, identity_id: str | None = None) -> LogicalBookArtifacts | None:
        if not entries: return None
        title = str(next((e.get("title") for e in entries if e.get("title")), ""))
        author = str(next((e.get("author") for e in entries if e.get("author")), ""))
        provider = str(next((e.get("provider_id") for e in entries if e.get("provider_id")), ""))
        logical_id = str(book_id or next((e.get("item_id") or e.get("artifact_id") for e in entries), ""))
        book = BookRef(logical_id, title=title, authors=tuple(x.strip() for x in author.split(";") if x.strip()), identifiers={"provider": provider} if provider else {})
        artifact_ids=[]
        for e in entries:
            aid=str(e.get("artifact_id") or "")
            if aid and aid not in artifact_ids: artifact_ids.append(aid)
        # Pull derived variants into the same logical view.
        for aid in tuple(artifact_ids):
            for v in self.service.storage.artifact_variants(source_artifact_id=aid):
                vid=str(v.get("artifact_id") or "")
                if (vid and vid not in artifact_ids and (
                    identity_id is None or _identity_placements(
                        self.service.storage, vid, identity_id=identity_id,
                        default_identity_id=self._default_identity(),
                    )
                )):
                    artifact_ids.append(vid)
        artifacts=tuple(
            x for x in (
                _artifact_ref(
                    self.service.storage, aid, book_id=logical_id, identity_id=identity_id,
                    default_identity_id=self._default_identity(),
                ) for aid in artifact_ids
            ) if x is not None
        )
        variants=[]
        for aid in artifact_ids:
            for row in self.service.storage.artifact_variants(source_artifact_id=aid):
                v=_variant_ref(
                    self.service.storage, row, book_id=logical_id, identity_id=identity_id,
                    default_identity_id=self._default_identity(),
                )
                if v is not None: variants.append(v)
        placements=[]
        for row in self.service.storage.placements():
            if (str(row.get("artifact_id") or "") in artifact_ids
                    and row.get("state") == "present"
                    and self._visible(row, identity_id)):
                placements.append(_placement_ref(row))
        possessions=[]
        provider_items={(str(e.get("provider_id") or ""),str(e.get("item_id") or "")) for e in entries}
        for entitlement in self.service.storage.entitlements():
            if not self._visible(entitlement, identity_id): continue
            if (str(entitlement.get("provider_id") or ""),str(entitlement.get("item_id") or "")) not in provider_items: continue
            kind=str(entitlement.get("entitlement_type") or "owned")
            if kind not in {"owned","loan","license"}: kind="owned"
            possessions.append(ArtifactPossessionRef(
                identity_id=str(entitlement.get("identity_id")) if entitlement.get("identity_id") else None,
                provider_id=str(entitlement.get("provider_id") or ""), source_item_id=str(entitlement.get("item_id") or ""), kind=kind,
                state=str(entitlement.get("state") or "active"), account_id=str(entitlement.get("account_id")) if entitlement.get("account_id") else None,
                entitlement_id=str(entitlement.get("entitlement_id")) if entitlement.get("entitlement_id") else None,
                expires_at=str(entitlement.get("expires_at")) if entitlement.get("expires_at") else None,
                facts={"credential_id": str(entitlement.get("credential_id") or "")},
            ))
        for p in placements:
            possessions.append(ArtifactPossessionRef(p.identity_id, p.provider_id, p.source_item_id, "storage" if p.kind == "storage" else "local", state=p.state, facts={"placement_id": p.placement_id, "storage_target_id": p.storage_target_id or ""}))
        return LogicalBookArtifacts(book, artifacts, tuple(variants), tuple(placements), tuple(possessions))

    def book(self, book_id: str, *, identity_id: str | None = None) -> LogicalBookArtifacts | None:
        value=str(book_id)
        entries=[
            e for e in self.service.storage.catalog_entries()
            if self._visible(e, identity_id)
            and value in {str(e.get("item_id") or ""),str(e.get("catalog_entry_id") or ""),str(e.get("artifact_id") or "")}
        ]
        if not entries:
            # Resolve a transformed artifact back to its source and logical book.
            variant=next((v for v in self.service.storage.artifact_variants(artifact_id=value)),None)
            if variant:
                return self.for_artifact(value, identity_id=identity_id)
        return self._view(entries, value, identity_id=identity_id)

    def for_artifact(self, artifact_id: str, *, identity_id: str | None = None) -> LogicalBookArtifacts | None:
        value=str(artifact_id)
        source=value
        variant=next((v for v in self.service.storage.artifact_variants(artifact_id=value)),None)
        if variant:
            # A transformed artifact can belong to another identity even
            # while its source hash exists in both libraries. Do not disclose
            # the other identity's variant by resolving it to a shared source.
            if identity_id is not None and not _identity_placements(
                self.service.storage, value, identity_id=identity_id,
                default_identity_id=self._default_identity(),
            ):
                return None
            source=str(variant.get("source_artifact_id") or value)
        entries=[
            e for e in self.service.storage.catalog_entries()
            if str(e.get("artifact_id") or "") == source and self._visible(e, identity_id)
        ]
        logical=next((str(e.get("item_id") or "") for e in entries if e.get("item_id")), source)
        return self._view(entries, logical, identity_id=identity_id)


def _duplicate_placement_scope_safe(storage: Any, row: dict | None, keep: dict | None) -> bool:
    """Same bytes are not authority to delete another identity/right's file.

    Placements do not store credential/account. If multiple recorded owners
    share an identity/provider/edition, Core cannot prove which one owns either
    path and refuses destruction. Repeated at execution after preview.
    """
    if not isinstance(row, dict) or not isinstance(keep, dict):
        return False
    for key in ("placement_id", "artifact_id", "identity_id", "provider_id", "path"):
        if not isinstance(row.get(key), str) or not row[key]:
            return False
        if not isinstance(keep.get(key), str) or not keep[key]:
            return False
    if (row["placement_id"] == keep["placement_id"]
            or row["path"] == keep["path"]
            or row["artifact_id"] != keep["artifact_id"]
            or row["identity_id"] != keep["identity_id"]
            or row["provider_id"] != keep["provider_id"]
            or row.get("source_item_id") != keep.get("source_item_id")
            or row.get("state") != "present"
            or keep.get("state") != "present"):
        return False
    provider, edition = row["provider_id"], row.get("source_item_id")
    # Without an explicit edition/source reference, identical EPUB bytes
    # could come from unrelated imports or acquisitions. There is no
    # canonical, scope-verified keeper: fail closed even with zero rights.
    if type(edition) is not str or not edition:
        return False
    # The placement omits credential/borrower. Check all providers and
    # preserve the unknown provenance of legacy unattributed rights.
    rights = [
        right for right in storage.entitlements(provider_id=provider)
        if right.get("identity_id") == row["identity_id"]
        or right.get("identity_id") is None
    ]
    matching = [right for right in rights if right.get("item_id") == edition]
    # A legacy NULL identity is not proof that the right belongs to this
    # placement. Never silently discard this ambiguous ownership evidence.
    if any(right.get("identity_id") is None for right in matching):
        return False
    owners = {
        (right.get("credential_id"), right.get("account_id"))
        for right in matching
    }
    return len(owners) <= 1


def _variant_deletion_scope_safe(storage: Any, variant: dict[str, Any]) -> bool:
    """Deleting a derived SHA cannot unlink another book sharing its bytes.

    The physical placement table is content-addressed. Only placements
    explicitly materialized by this variant's source are candidates for
    global deletion. A generic/local/provider placement with the same SHA,
    or a derivative now shared across identities, remains independent.
    """
    if type(variant) is not dict:
        return False
    artifact_id = variant.get("artifact_id")
    source_id = variant.get("source_artifact_id")
    if (type(artifact_id) is not str or not artifact_id
            or type(source_id) is not str or not source_id
            or artifact_id == source_id):
        return False
    placements = [
        row for row in storage.placements()
        if row.get("artifact_id") == artifact_id
    ]
    scopes = set()
    for row in placements:
        metadata = row.get("metadata")
        scope = tuple(row.get(key) for key in (
            "identity_id", "provider_id", "source_item_id"
        ))
        if (type(metadata) is not dict
                or metadata.get("variant") is not True
                or metadata.get("source_artifact_id") != source_id
                or any(type(value) is not str or not value for value in scope)
                or row.get("state") != "present"
                or Path(str(row.get("path") or "")).is_symlink()):
            return False
        scopes.add(scope)
        if len(scopes) > 1:
            return False
    return True


@dataclass(slots=True)
class LibraryMaintenanceBrokerImpl:
    service: Any

    def legacy_placement_scope_review(
        self, *, candidate_placement_id: str, keeper_placement_id: str,
    ):
        """Review old provider-bound copies; never authorize scope adoption."""
        from .core.canonical_legacy_scope_review import (
            review_legacy_placement_scope,
        )
        return review_legacy_placement_scope(
            self.service.storage,
            library_root=_canonical_maintenance_root(self.service),
            candidate_placement_id=candidate_placement_id,
            keeper_placement_id=keeper_placement_id,
        )

    def canonical_reference_migration_plan(
        self, *, candidate_placement_id: str, keeper_placement_id: str,
    ):
        """Read-only historical dependency plan, not an unlink authorization."""
        from .core.canonical_dedup_reference_plan import (
            plan_verified_canonical_reference_migration,
        )
        return plan_verified_canonical_reference_migration(
            self.service.storage,
            library_root=_canonical_maintenance_root(self.service),
            candidate_placement_id=candidate_placement_id,
            keeper_placement_id=keeper_placement_id,
        )

    def canonical_placement_inventory(self, *, identity_id: str):
        """SQL-only v14 placement inventory, including books without catalogue."""
        from .core.canonical_placement_inventory import (
            read_canonical_placement_inventory,
        )
        return read_canonical_placement_inventory(
            self.service.storage, identity_id=identity_id,
        )

    def pending_canonical_advisories(self, *, identity_id: str | None = None):
        """Read-only post-publication duplicate inventory, not deletion consent."""
        from .core.canonical_dedup_advisory_review import (
            review_canonical_dedup_advisories,
        )
        return review_canonical_dedup_advisories(
            self.service.storage, identity_id=identity_id,
        )

    def pending_canonical_cleanups(self, *, identity_id: str | None = None):
        """Read-only maintenance discovery; never an unlink authorization."""
        from .core.canonical_dedup_pending_review import (
            review_pending_canonical_cleanups,
        )
        return review_pending_canonical_cleanups(
            self.service.storage, identity_id=identity_id,
        )

    def plan(self, request: LibraryMutationRequest) -> LibraryMutationPlan:
        op=request.operation
        _enforce_ordinary_dedup_hold(request)
        if op == "normalize_filename":
            row=self.service.storage.placement_get(request.placement_id)
            if row is None: raise NotFound("Placement inconnu", code="placement_not_found")
            entries=[e for e in self.service.storage.catalog_entries() if str(e.get("placement_id") or "") == request.placement_id]
            entry=entries[0] if entries else {}
            old=Path(str(row.get("path") or "")); ext=old.suffix.lstrip(".") or str(entry.get("media_format") or "epub")
            new_name=self.service.preview_library_filename(title=str(entry.get("title") or old.stem), author=str(entry.get("author") or ""), extension=ext)
            return LibraryMutationPlan(op, f"Renommer {old.name} en {new_name}", request, changes=(old.name,new_name), facts={"new_filename":new_name})
        if op == "move_placement":
            if not request.target_identity_id: raise ValueError("target_identity_id required")
            row=self.service.storage.placement_get(request.placement_id)
            if row is None: raise NotFound("Placement inconnu", code="placement_not_found")
            return LibraryMutationPlan(op, f"Matérialiser le placement vers l'identité {request.target_identity_id}", request, changes=(request.placement_id, request.target_identity_id))
        if op == "legacy_adopt":
            from .core.canonical_legacy_adoption import adopt_verified_legacy_placement
            action = request.options.get("action", "preview")
            if type(action) is not str or action not in ("preview", "commit"):
                raise _CanonicalDedupConflict(
                    "Action de migration historique invalide",
                    code="legacy_adoption_invalid_action",
                )
            evidence = adopt_verified_legacy_placement(
                self.service.storage,
                library_root=_canonical_maintenance_root(self.service),
                candidate_placement_id=request.placement_id,
                keeper_placement_id=request.keep_placement_id,
            )
            if action == "commit" and evidence.status != "review_required":
                raise _CanonicalDedupConflict(
                    "Migration historique non vérifiée",
                    code="legacy_adoption_unverified",
                )
            return LibraryMutationPlan(
                op,
                ("Contrôler le placement historique" if action == "preview"
                 else "Adopter le placement historique après confirmation"),
                request, destructive=(action == "commit"),
                changes=(request.placement_id,) if action == "commit" else (),
                warnings=(("Écriture SQLite confirmée ; aucun fichier supprimé.",)
                          if action == "commit" else ()),
                facts={"action": action, "status": evidence.status,
                       "reason": evidence.reason,
                       "review_token": evidence.review_token},
            )
        if op == "canonical_dedup":
            from .core.canonical_dedup_supervisor import supervise_canonical_duplicate

            action = _canonical_maintenance_action(request)
            root = _canonical_maintenance_root(self.service)
            candidate = self.service.storage.placement_get(request.placement_id)
            keeper = self.service.storage.placement_get(request.keep_placement_id)
            if candidate is None or keeper is None:
                raise NotFound("Placement canonique inconnu")
            # A preview is always read-only; pending tombstones may be
            # recovered, but must never be re-committed as a fresh deletion.
            if action == "recover":
                if candidate.get("state") != "dedup_cleanup_pending":
                    raise _CanonicalDedupConflict("Aucun nettoyage canonique à reprendre", code="canonical_dedup_no_pending_cleanup")
                summary = "Reprendre le nettoyage canonique interrompu"
                reason = "explicit_recovery_required"
            else:
                evidence = supervise_canonical_duplicate(
                    self.service.storage, library_root=root,
                    candidate_placement_id=request.placement_id,
                    keeper_placement_id=request.keep_placement_id,
                    action="preview",
                )
                reason = evidence.reason
                if action == "commit" and reason not in (
                    "active_references", "verified_no_active_references",
                ):
                    raise _CanonicalDedupConflict("Doublon canonique non vérifié", code="canonical_dedup_pair_unverified")
                summary = ("Contrôler le doublon canonique"
                           if action == "preview" else
                           "Consolider puis supprimer le doublon canonique confirmé")
            destructive = action in ("commit", "recover")
            return LibraryMutationPlan(
                op, summary, request, destructive=destructive,
                changes=(request.placement_id,) if destructive else (),
                warnings=(("La suppression physique nécessite une confirmation explicite.",)
                          if destructive else ()),
                facts={"action": action, "reason": reason},
            )
        if op == "delete_duplicate":
            row=self.service.storage.placement_get(request.placement_id); keep=self.service.storage.placement_get(request.keep_placement_id)
            if row is None or keep is None: raise NotFound("Placement inconnu", code="placement_not_found")
            if _canonical_b_pair(row, keep):
                raise _CanonicalDedupConflict("Utiliser le dédoublonnage canonique supervisé", code="canonical_dedup_supervised_required")
            if not _duplicate_placement_scope_safe(self.service.storage, row, keep):
                raise _DuplicateScopeConflict("Doublon hors du même périmètre vérifié")
            from .core.duplicate_maintenance_guard import candidate_has_active_dependencies
            if candidate_has_active_dependencies(
                self.service.storage, placement_id=row["placement_id"], path=row["path"],
            ):
                raise Conflict("Le fichier est référencé par des données actives")
            return LibraryMutationPlan(op, "Supprimer le doublon physique vérifié", request, destructive=True, changes=(request.placement_id,), warnings=("Le fichier du placement sera supprimé.",))
        if op == "replace_cover":
            return LibraryMutationPlan(op, "Remplacer la couverture canonique", request, changes=(request.book_id, request.cover_artifact_id))
        if op == "delete_variant":
            rows=self.service.storage.artifact_variants(artifact_id=request.artifact_id)
            if not rows: raise NotFound("Variante inconnue", code="artifact_variant_not_found")
            # Content-addressed identity derivatives can exist in historical
            # stores. Never unlink the source artifact's physical placements.
            # More than one variant row sharing the hash is also ambiguous:
            # deleting one cannot authorize deletion of the other's bytes.
            if (len(rows) != 1
                    or not _variant_deletion_scope_safe(self.service.storage, rows[0])):
                raise _VariantScopeConflict("Variante et source non indépendantes")
            return LibraryMutationPlan(op, "Supprimer la variante et ses placements", request, destructive=True, changes=(request.artifact_id,), warnings=("Les matérialisations de cette variante seront supprimées.",))
        if op == "set_preferred_variant":
            rows=self.service.storage.artifact_variants(artifact_id=request.artifact_id)
            if not rows: raise NotFound("Variante inconnue", code="artifact_variant_not_found")
            return LibraryMutationPlan(op, f"Définir la variante préférée pour {request.purpose or 'read'}", request, changes=(request.artifact_id, request.purpose or "read"))
        raise UnsupportedOperation(f"Opération de maintenance non supportée: {op}")

    def run(self, plan: LibraryMutationPlan, control: TaskControl | None = None) -> LibraryMutationResult:
        request=plan.request
        _enforce_ordinary_dedup_hold(request)
        if control is not None: control.check_cancelled()
        if request.operation == "normalize_filename":
            row=self.service.storage.placement_get(request.placement_id)
            if row is None: raise NotFound("Placement inconnu", code="placement_not_found")
            # A recorded placement may have been replaced by a symlink
            # after its last scan. resolve() BEFORE checking the source would
            # silently turn a library rename into a mutation outside the
            # indexed path. Also refuse symlinked ancestors, legacy aliases
            # and special files before creating any hardlink.
            old=Path(str(row.get("path") or ""))
            try:
                if (not old.is_absolute() or ".." in old.parts
                        or old.is_symlink() or old.resolve() != old
                        or not stat.S_ISREG(
                            os.stat(old, follow_symlinks=False).st_mode
                        )):
                    raise _LibraryFilenameConflict("Source non canonique ou symbolique")
            except (OSError, RuntimeError):
                raise _LibraryFilenameConflict("Source locale non vérifiable") from None
            new=old.with_name(str(plan.facts.get("new_filename") or old.name))
            if new != old:
                # A stored artifact_id is not evidence that the bytes on
                # disk are still those of the indexed book. Rehash the actual
                # source under a no-follow fd for this explicit maintenance
                # operation, before creating the destination hardlink or
                # changing SQLite. Size + mtime alone are not a fingerprint.
                evidence = self.service.storage.artifact_get(str(row.get("artifact_id") or ""))
                digest = evidence.get("sha256") if type(evidence) is dict else None
                length = evidence.get("size") if type(evidence) is dict else None
                if (type(digest) is not str or len(digest) != 64
                        or any(c not in "0123456789abcdef" for c in digest)
                        or row.get("artifact_id") != "sha256:" + digest
                        or type(length) is not int or length <= 0
                        or not hasattr(os, "O_NOFOLLOW")):
                    raise _LibraryFilenameConflict("Empreinte source non attestée")
                try:
                    fd = os.open(
                        old, os.O_RDONLY | os.O_NOFOLLOW |
                        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0),
                    )
                    try:
                        before = os.fstat(fd)
                        if not stat.S_ISREG(before.st_mode) or before.st_size != length:
                            raise _LibraryFilenameConflict("Fichier source modifié")
                        hasher = hashlib.sha256()
                        while chunk := os.read(fd, 1024 * 1024):
                            hasher.update(chunk)
                        after = os.fstat(fd)
                        named = os.stat(old, follow_symlinks=False)
                        attrs = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
                        if (any(getattr(before, key) != getattr(after, key) or
                                getattr(before, key) != getattr(named, key)
                                for key in attrs)
                                or hasher.hexdigest() != digest):
                            raise _LibraryFilenameConflict("Fichier source modifié")
                    finally:
                        os.close(fd)
                except OSError:
                    raise _LibraryFilenameConflict("Source locale non vérifiable") from None
                # Never allow a target symlink to redirect the new SQLite
                # pathname to a different inode/root, even at equal SHA.
                if new.is_symlink():
                    raise _LibraryFilenameConflict("Destination symbolique refusée")
                # Never overwrite or merge a path on SHA equality alone:
                # a different identity/provider/edition may share those bytes.
                # Perform this check before os.replace() can change the disk.
                target = self.service.storage.placement_for_path(new)
                if target and str(target.get("placement_id")) != str(row.get("placement_id")):
                    if not all((
                        row.get("identity_id"),
                        row.get("provider_id"),
                        row.get("artifact_id"),
                        row.get("source_item_id"),
                        target.get("identity_id") == row.get("identity_id"),
                        target.get("provider_id") == row.get("provider_id"),
                        target.get("artifact_id") == row.get("artifact_id"),
                        target.get("source_item_id") == row.get("source_item_id"),
                    )):
                        raise _LibraryFilenameConflict("Collision de placement")
                if new.exists():
                    if target is None:
                        raise _LibraryFilenameConflict("Destination existante non attestée")
                    if sha256_file(new) != sha256_file(old):
                        raise _LibraryFilenameConflict("Collision de nom")
                # Keep at least one path named by SQLite at every crash
                # boundary. A hardlink in the same directory creates the
                # destination atomically while the old DB path still exists;
                # then commit SQLite, and only finally unlink the old name.
                # If the destination is already attested with identical bytes,
                # preserve its inode: it already satisfies the new DB path.
                # Never replace an existing unrelated or symlinked target.
                created_target = False
                if not new.exists():
                    try:
                        os.link(old, new, follow_symlinks=False)
                    except FileExistsError:
                        raise _LibraryFilenameConflict("Collision de nom")
                    created_target = True
                expected_old = os.stat(old, follow_symlinks=False)
                expected_new = os.stat(new, follow_symlinks=False)
                try:
                    self.service.storage.relocate_local_path(
                        old, new,
                        identity_id=str(row.get("identity_id") or ""),
                        provider_id=str(row.get("provider_id") or "local"),
                    )
                except Exception:
                    # A failed DB transaction leaves the original path valid.
                    # Remove only OUR new hardlink and only if it still points
                    # at that original inode. Never delete someone else's file.
                    if created_target:
                        try:
                            old_stat = os.stat(old, follow_symlinks=False)
                            new_stat = os.stat(new, follow_symlinks=False)
                            if ((old_stat.st_dev, old_stat.st_ino)
                                    == (new_stat.st_dev, new_stat.st_ino)):
                                new.unlink()
                        except OSError:
                            pass
                    raise
                # A crash at this point leaves a valid, extra old hardlink;
                # SQLite already points at the durable destination. Refuse to
                # unlink a replaced source path or any unexpected new inode.
                cleanup_pending = False
                try:
                    old_stat = os.stat(old, follow_symlinks=False)
                    new_stat = os.stat(new, follow_symlinks=False)
                    # Even an attested existing destination can be replaced
                    # externally during the SQLite transaction. In that case
                    # keep the original file as an inert, recoverable duplicate.
                    new_unchanged = all(
                        getattr(new_stat, attr) == getattr(expected_new, attr)
                        for attr in (
                            'st_dev', 'st_ino', 'st_size',
                            'st_mtime_ns', 'st_ctime_ns',
                        )
                    )
                    same_inode = (
                        (old_stat.st_dev, old_stat.st_ino)
                        == (new_stat.st_dev, new_stat.st_ino)
                    )
                    old_unchanged = all(
                        getattr(old_stat, attr) == getattr(expected_old, attr)
                        for attr in (
                            'st_dev', 'st_ino', 'st_size',
                            'st_mtime_ns', 'st_ctime_ns',
                        )
                    )
                    if (not old_unchanged or not new_unchanged
                            or (created_target and not same_inode)):
                        cleanup_pending = True
                    else:
                        old.unlink()
                except OSError:
                    cleanup_pending = True
            else:
                cleanup_pending = False
            placed=self.service.storage.placement_for_path(new)
            return LibraryMutationResult(
                True, 1 if new != old else 0, plan.summary,
                (_placement_ref(placed),) if placed else (),
                warnings=(("Ancien nom conservé : nettoyage local à vérifier.",)
                          if cleanup_pending else ()),
            )
        if request.operation == "move_placement":
            result=self.service.materialize_to_identity(request.placement_id,str(request.target_identity_id))
            row=self.service.storage.placement_get(str(result.get("placement_id") or ""))
            return LibraryMutationResult(True,1,plan.summary,(_placement_ref(row),) if row else (),facts=result)
        if request.operation == "legacy_adopt":
            from .core.canonical_legacy_adoption import adopt_verified_legacy_placement
            from .locking import InterProcessLock, LockBusyError
            from .paths import acquisition_lock_file

            action = request.options.get("action", "preview")
            if (type(action) is not str or action not in ("preview", "commit")
                    or plan.operation != "legacy_adopt"
                    or plan.destructive is not (action == "commit")
                    or type(plan.facts) is not dict
                    or plan.facts.get("action") != action):
                raise _CanonicalDedupConflict(
                    "Plan de migration historique invalide",
                    code="legacy_adoption_invalid_plan",
                )
            root = _canonical_maintenance_root(self.service)
            kwargs = {
                "library_root": root,
                "candidate_placement_id": request.placement_id,
                "keeper_placement_id": request.keep_placement_id,
            }
            if action == "preview":
                result = adopt_verified_legacy_placement(
                    self.service.storage, **kwargs,
                )
            else:
                if request.options.get("confirmed") is not True:
                    raise _CanonicalDedupConflict(
                        "Confirmation explicite requise",
                        code="legacy_adoption_confirmation_required",
                    )
                try:
                    if control is not None:
                        control.check_cancelled()
                    with InterProcessLock(acquisition_lock_file(), reentrant=True) as lease:
                        if control is not None:
                            control.check_cancelled()
                        result = adopt_verified_legacy_placement(
                            self.service.storage, **kwargs,
                            action="commit", confirmed=True, shared_lock=lease,
                            expected_review_token=plan.facts.get("review_token", ""),
                        )
                except LockBusyError as error:
                    raise _CanonicalDedupConflict(
                        "Bibliothèque occupée : migration historique indisponible",
                        code="legacy_adoption_lock_busy",
                    ) from error
            return LibraryMutationResult(
                result.status in ("review_required", "migrated"),
                int(result.status == "migrated"),
                "Migration historique non destructive : " + result.status,
                facts={
                    "action": action, "status": result.status,
                    "reason": result.reason,
                    "completed_path_receipts": result.completed_path_receipts,
                    "catalogue_references": result.catalogue_references,
                    "backup_references": result.backup_references,
                    "removed_file": False,
                },
            )
        if request.operation == "canonical_dedup":
            from .core.canonical_dedup_supervisor import supervise_canonical_duplicate
            from .locking import InterProcessLock, LockBusyError
            from .paths import acquisition_lock_file

            action = _canonical_maintenance_action(request)
            if (plan.operation != request.operation
                    or plan.destructive is not (action in ("commit", "recover"))
                    or type(plan.facts) is not dict
                    or plan.facts.get("action") != action):
                raise _CanonicalDedupConflict("Plan de dédoublonnage invalide", code="canonical_dedup_invalid_plan")
            root = _canonical_maintenance_root(self.service)
            if action == "preview":
                status = supervise_canonical_duplicate(
                    self.service.storage, library_root=root,
                    candidate_placement_id=request.placement_id,
                    keeper_placement_id=request.keep_placement_id,
                    action="preview",
                )
            else:
                # Explicit confirmation MUST be supplied by the caller after
                # human approval. A legacy delete_duplicate, an old plan or a
                # general maintenance scan never implicitly opts in.
                if request.options.get("confirmed") is not True:
                    raise _CanonicalDedupConflict(
                        "Confirmation explicite requise",
                        code="canonical_dedup_confirmation_required",
                    )
                try:
                    if control is not None: control.check_cancelled()
                    # Confirmed GTK maintenance already holds this exact
                    # acquisition lock across its plan revalidation. Borrow
                    # the genuine same-thread OS lease; never drop the host
                    # lock or accept an untrusted already_locked flag.
                    with InterProcessLock(acquisition_lock_file(), reentrant=True) as lease:
                        if control is not None: control.check_cancelled()
                        status = supervise_canonical_duplicate(
                            self.service.storage, library_root=root,
                            candidate_placement_id=request.placement_id,
                            keeper_placement_id=request.keep_placement_id,
                            action=action, shared_lock=lease, confirmed=True,
                        )
                except LockBusyError as exc:
                    raise _CanonicalDedupConflict(
                        "Bibliothèque occupée : dédoublonnage indisponible",
                        code="canonical_dedup_lock_busy",
                    ) from exc
            # Public maintenance.changed counts *retired placements*, not
            # SQL reference relinks. Report those separately in facts so
            # existing destructive tool progress remains 0/1 per candidate.
            changed = int(status.removed_file)
            return LibraryMutationResult(
                status.status in ("preview_ready", "retired"),
                changed if action != "preview" else 0,
                "Dédoublonnage canonique supervisé : " + status.status,
                warnings=(("Reprise explicite nécessaire.",)
                          if status.status == "cleanup_pending" else ()),
                facts={
                    "action": action, "status": status.status,
                    "reason": status.reason,
                    "catalogue_relinked": status.catalogue_relinked,
                    "backup_relinked": status.backup_relinked,
                    "receipt_relinked": status.receipt_relinked,
                    "removed_file": status.removed_file,
                },
            )
        if request.operation == "delete_duplicate":
            # The destructive operation must use the same non-blocking,
            # interprocess acquisition lock as Sync. Revalidate and hash
            # BOTH paths while it is held; release only after the optional
            # unlink, never queue a stale maintenance plan behind a GET.
            from .locking import InterProcessLock, LockBusyError
            from .paths import acquisition_lock_file
            try:
                with InterProcessLock(acquisition_lock_file()):
                    row=self.service.storage.placement_get(request.placement_id)
                    if row is None: return LibraryMutationResult(True,0,"Placement déjà absent")
                    keep=self.service.storage.placement_get(request.keep_placement_id)
                    if _canonical_b_pair(row, keep):
                        raise _CanonicalDedupConflict("Utiliser le dédoublonnage canonique supervisé", code="canonical_dedup_supervised_required")
                    if not _duplicate_placement_scope_safe(self.service.storage, row, keep):
                        raise _DuplicateScopeConflict("Le doublon n'est plus vérifiable")
                    from .core.duplicate_maintenance_guard import candidate_has_active_dependencies
                    if candidate_has_active_dependencies(
                        self.service.storage, placement_id=row["placement_id"], path=row["path"],
                    ):
                        raise Conflict("Le fichier est référencé par des données actives")
                    # A stale SHA in SQLite does not authorize unlinking an arbitrary
                    # replaced file, even when its placement owner still matches.
                    # Hash BOTH actual files under the user's explicit maintenance
                    # action. Never follow a symbolic link or remove a non-regular path.
                    path = Path(row["path"])
                    kept_path = Path(keep["path"])
                    evidence = self.service.storage.artifact_get(row["artifact_id"])
                    digest = evidence.get("sha256") if type(evidence) is dict else None
                    size = evidence.get("size") if type(evidence) is dict else None
                    if (type(digest) is not str or len(digest) != 64
                            or type(size) is not int or size < 0
                            or path.is_symlink() or kept_path.is_symlink()
                            or not path.is_file() or not kept_path.is_file()):
                        raise Conflict("Octets du doublon non vérifiés")
                    try:
                        deleted_stat = os.stat(path, follow_symlinks=False)
                        kept_stat = os.stat(kept_path, follow_symlinks=False)
                        if (not stat.S_ISREG(deleted_stat.st_mode)
                                or not stat.S_ISREG(kept_stat.st_mode)
                                or deleted_stat.st_size != size or kept_stat.st_size != size
                                or sha256_file(path) != digest
                                or sha256_file(kept_path) != digest):
                            raise Conflict("Octets du doublon non vérifiés")
                        fingerprint = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
                        for selected, expected in ((path, deleted_stat), (kept_path, kept_stat)):
                            now = os.stat(selected, follow_symlinks=False)
                            if any(getattr(now, key) != getattr(expected, key) for key in fingerprint):
                                raise Conflict("Octets du doublon non vérifiés")
                    except OSError:
                        raise Conflict("Octets du doublon non vérifiés") from None
                    # Commit removal of the unreferenced placement FIRST. A failed
                    # SQLite transaction leaves both verified physical paths intact;
                    # a crash afterwards leaves an extra, recoverable duplicate, not
                    # a 'present' SQLite row pointing at an already deleted book.
                    try:
                        self.service.storage.placement_retire_duplicate_atomic(
                            placement_id=str(row["placement_id"]),
                            keep_placement_id=str(keep["placement_id"]),
                            candidate_path=str(path), kept_path=str(kept_path),
                            artifact_id=str(row["artifact_id"]),
                            identity_id=str(row["identity_id"]),
                            provider_id=str(row["provider_id"]),
                            source_item_id=row.get("source_item_id"),
                        )
                    except ValueError as exc:
                        raise _DuplicateScopeConflict("Le doublon n'est plus vérifiable") from exc
                    # The old file is now an optional cleanup target. Never unlink a
                    # name that was replaced or rebound while SQLite committed, and
                    # never destroy the remaining copy after its inode changed.
                    cleanup_pending = True
                    try:
                        if (not candidate_has_active_dependencies(
                            self.service.storage,
                            placement_id=request.placement_id,
                            path=str(path),
                        ) and self.service.storage.placement_for_path(path) is None):
                            current_deleted = os.stat(path, follow_symlinks=False)
                            current_kept = os.stat(kept_path, follow_symlinks=False)
                            if (
                                stat.S_ISREG(current_deleted.st_mode)
                                and stat.S_ISREG(current_kept.st_mode)
                                and all(
                                    getattr(current_deleted, key) == getattr(deleted_stat, key)
                                    and getattr(current_kept, key) == getattr(kept_stat, key)
                                    for key in fingerprint
                                )
                            ):
                                path.unlink()
                                cleanup_pending = False
                    except OSError:
                        pass
                    return LibraryMutationResult(
                        True, 1, plan.summary,
                        warnings=(
                            ("Fichier doublon conservé : nettoyage local à vérifier.",)
                            if cleanup_pending else ()
                        ),
                    )
            except LockBusyError as exc:
                raise Conflict(
                    "Bibliothèque occupée : nettoyage des doublons indisponible"
                ) from exc
        if request.operation == "replace_cover":
            if not request.book_id or not request.cover_artifact_id: raise ValueError("book_id and cover_artifact_id required")
            cover=self.service.storage.artifact_get(request.cover_artifact_id)
            if cover is None: raise NotFound("Artefact de couverture inconnu", code="artifact_not_found")
            changed=0
            for entry in self.service.storage.catalog_entries():
                if str(entry.get("item_id") or entry.get("catalog_entry_id") or "") != request.book_id: continue
                meta=dict(entry.get("metadata") or {}); meta["cover_artifact_id"]=request.cover_artifact_id
                self.service.storage.catalog_entry_update_metadata(str(entry.get("catalog_entry_id")),meta); changed += 1
            return LibraryMutationResult(True,changed,plan.summary,facts={"cover_artifact_id":request.cover_artifact_id})
        if request.operation == "set_preferred_variant":
            row=self.service.storage.artifact_variants(artifact_id=request.artifact_id)[0]
            source_id=str(row.get("source_artifact_id") or "")
            purpose=str(request.purpose or "read")
            self.service.storage.metadata_set(f"artifact.variant.preferred.{source_id}.{purpose}", request.artifact_id)
            return LibraryMutationResult(True,1,plan.summary,facts={"source_artifact_id":source_id,"artifact_id":request.artifact_id,"purpose":purpose})
        if request.operation == "delete_variant":
            rows = self.service.storage.artifact_variants(
                artifact_id=request.artifact_id,
            )
            if not rows:
                return LibraryMutationResult(True, 0, "Variante déjà absente")
            if (len(rows) != 1 or not _variant_deletion_scope_safe(
                    self.service.storage, rows[0])):
                raise _VariantScopeConflict("Variante et source non indépendantes")
            variant = rows[0]
            source_id = str(variant.get("source_artifact_id") or "")
            placements = [
                placement for placement in self.service.storage.placements()
                if str(placement.get("artifact_id") or "") == request.artifact_id
            ]
            # Multi-placement retirement needs one SQLite transaction that
            # revalidates all references. Until that API exists, refuse this
            # destructive action rather than allow a partially applied batch.
            if len(placements) > 1:
                raise _VariantScopeConflict("Variante à placements multiples")
            evidence = self.service.storage.artifact_get(str(request.artifact_id or ""))
            digest = evidence.get("sha256") if type(evidence) is dict else None
            size = evidence.get("size") if type(evidence) is dict else None
            if placements and (
                type(digest) is not str or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
                or str(request.artifact_id) != "sha256:" + digest
                or type(size) is not int or size < 0
                or not hasattr(os, "O_NOFOLLOW")
            ):
                raise _VariantScopeConflict("Empreinte de la variante non attestée")
            from .core.duplicate_maintenance_guard import (
                candidate_has_active_dependencies,
            )
            # A plan is not an authorization to unlink whatever bytes happen
            # to occupy a historical path. Validate ALL placements before
            # touching SQLite; no-follow fd binds the hash to a real inode.
            fingerprint = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            verified = []
            seen_paths = set()
            for placement in placements:
                placement_id = str(placement.get("placement_id") or "")
                path = Path(str(placement.get("path") or ""))
                if (not placement_id or not path.is_absolute()
                        or ".." in path.parts or path in seen_paths
                        or path.resolve() != path
                        or candidate_has_active_dependencies(
                            self.service.storage,
                            placement_id=placement_id, path=str(path),
                            removable_variant={
                                **placement, "source_artifact_id": source_id,
                            },
                        )):
                    raise _VariantScopeConflict("Références de variante non vérifiables")
                seen_paths.add(path)
                try:
                    fd = os.open(
                        path, os.O_RDONLY | os.O_NOFOLLOW |
                        getattr(os, "O_CLOEXEC", 0) |
                        getattr(os, "O_NONBLOCK", 0),
                    )
                    try:
                        before = os.fstat(fd)
                        if not stat.S_ISREG(before.st_mode) or before.st_size != size:
                            raise _VariantScopeConflict("Octets de variante non vérifiés")
                        sha = hashlib.sha256()
                        while chunk := os.read(fd, 1024 * 1024):
                            sha.update(chunk)
                        after = os.fstat(fd)
                        named = os.stat(path, follow_symlinks=False)
                        if (sha.hexdigest() != digest
                                or any(
                                    getattr(before, key) != getattr(after, key)
                                    or getattr(before, key) != getattr(named, key)
                                    for key in fingerprint
                                )):
                            raise _VariantScopeConflict("Octets de variante non vérifiés")
                    finally:
                        os.close(fd)
                except _VariantScopeConflict:
                    # A verified checksum/size mismatch is an integrity
                    # refusal, not an unavailable filesystem inspection.
                    raise
                except (OSError, RuntimeError):
                    raise _VariantScopeConflict("Fichier variante non vérifiable") from None
                verified.append((placement_id, path, named))
            # Commit the SQLite reference changes before any physical unlink.
            # An SQL failure leaves ALL files intact. A crash after the commit
            # may leave an unindexed, recoverable file, never a live placement
            # pointing to already-deleted bytes.
            self.service.storage.artifact_variant_retire_atomic(
                variant_id=str(variant.get("variant_id") or ""),
                artifact_id=request.artifact_id,
                source_artifact_id=source_id,
                placement_id=verified[0][0] if verified else None,
                placement_path=str(verified[0][1]) if verified else None,
            )
            cleanup_pending = False
            for placement_id, path, expected in verified:
                try:
                    if (candidate_has_active_dependencies(
                            self.service.storage,
                            placement_id=placement_id, path=str(path),
                        ) or self.service.storage.placement_for_path(path) is not None):
                        cleanup_pending = True
                        continue
                    current = os.stat(path, follow_symlinks=False)
                    if (not stat.S_ISREG(current.st_mode)
                            or any(getattr(current, key) != getattr(expected, key)
                                   for key in fingerprint)):
                        cleanup_pending = True
                        continue
                    path.unlink()
                except OSError:
                    cleanup_pending = True
            self.service.storage.artifact_delete_if_unreferenced(request.artifact_id)
            return LibraryMutationResult(
                True, 1, plan.summary,
                warnings=(("Fichier variante conservé : nettoyage local à vérifier.",)
                          if cleanup_pending else ()),
                facts={"artifact_id": request.artifact_id},
            )
        raise UnsupportedOperation(f"Opération de maintenance non supportée: {request.operation}")


class _UsbCanonicalExecutionRequired(UnsupportedOperation):
    """The generic artifact route is never an authority to write on USB."""

    code = "usb_core_authorization_required"


class _NoStorageTarget(NotFound):
    """No currently eligible generic target, including excluded USB targets."""

    code = "storage_target_unavailable"


def _is_usb_storage_target(target: object) -> bool:
    """Closed recognition of the USB storage contract, not mount attestation.

    Plugins declaring a USB kind/fingerprint must use the dedicated Core
    executor (#2854); a generic importer cannot provide consent, per-reader
    lock, no-overwrite transaction or a managed USB receipt. Unknown kinds
    must not be positively identified as safe USB by this helper.
    """
    kind = getattr(target, "kind", None)
    if type(kind) is str and kind.casefold() in {
        "usb", "removable", "usb-mass-storage",
    }:
        return True
    metadata = getattr(target, "metadata", None)
    if isinstance(metadata, Mapping):
        transport = metadata.get("transport")
        if type(transport) is str and transport.casefold() in {
            "usb", "removable", "usb-mass-storage",
        }:
            return True
        if "usb_fingerprint" in metadata:
            return True
    return False


@dataclass(slots=True)
class ArtifactRoutingBrokerImpl:
    service: Any
    execution: ArtifactExecutionBrokerImpl

    def targets(self, request: ArtifactRouteRequest) -> tuple[ArtifactRouteTarget, ...]:
        manager=getattr(self.service,"plugin_manager",None)
        if manager is None: return ()
        out=[]
        for resolved in manager.services("storage.target"):
            configured=getattr(resolved.service,"configured_targets",None)
            if not callable(configured): continue
            try: targets=tuple(configured())
            except Exception: continue
            for target in targets:
                # Route discovery must not advertise an unsafe USB write
                # through the generic storage.target importer. The USB
                # sync coordinator owns its own opt-in & receipts.
                if _is_usb_storage_target(target): continue
                if request.target_id and str(getattr(target,"target_id","") or "") != request.target_id: continue
                if not bool(dict(request.options or {}).get("include_existing", False)):
                    already_present = False
                    for placement in self.service.storage.placements(state="present"):
                        if str(placement.get("artifact_id") or "") != request.artifact.artifact_id:
                            continue
                        metadata = dict(placement.get("metadata") or {})
                        if str(metadata.get("storage_target_id") or "") == str(getattr(target,"target_id","") or "") and str(metadata.get("storage_plugin_id") or "") == resolved.plugin_id:
                            already_present = True; break
                    if already_present:
                        continue
                matches=getattr(resolved.service,"matches",None)
                if callable(matches):
                    try:
                        if not matches(target): continue
                    except Exception: continue
                out.append(ArtifactRouteTarget(resolved.plugin_id,target,resolved.effective_priority,resolved.registry_generation,resolved.capabilities))
        out.sort(key=lambda row:(-row.effective_priority,row.plugin_id,row.target.target_id))
        return tuple(out)

    def plan(self, request: ArtifactRouteRequest) -> ArtifactRoutePlan:
        targets=self.targets(request)
        if not targets: raise _NoStorageTarget("Aucune cible de stockage compatible")
        target=targets[0]
        filename=Path(request.filename or request.artifact.filename or (request.artifact.artifact_id.replace(":","-")+"."+request.artifact.format.format)).name
        return ArtifactRoutePlan(request,target,filename,summary=f"Envoyer {filename} vers {target.target.label or target.target.target_id}")

    def transfer(self, plan: ArtifactRoutePlan, control: TaskControl | None = None) -> ArtifactTransferResult:
        recorder = getattr(self.service, "_record_action", None)
        try:
            # A caller can hold a previously constructed (or synthetic) plan
            # even after discovery is filtered. Refuse before requesting a
            # source lease or entering a plugin write method.
            if _is_usb_storage_target(plan.target.target):
                raise _UsbCanonicalExecutionRequired(
                    "Transfert USB non autorisé : utiliser la synchronisation "
                    "USB canonique avec droits et confirmation vérifiés."
                )
            if control is not None: control.check_cancelled(); control.report(current=0,total=1,message=plan.summary)
            manager=getattr(self.service,"plugin_manager",None)
            if manager is None: raise RuntimeError("plugin manager unavailable")
            resolved=next((r for r in manager.services("storage.target") if r.plugin_id == plan.target.plugin_id and r.registry_generation == plan.target.registry_generation),None)
            if resolved is None: raise Conflict("La cible a changé depuis la planification", code="storage_target_generation_changed")
            lease=self.execution.input_lease(plan.request.artifact)
            importer=getattr(resolved.service,"import_artifact",None)
            if not callable(importer): raise UnsupportedOperation("La cible ne supporte pas import_artifact")
            result=importer(ArtifactInput(path=lease.path,artifact_id=plan.request.artifact.artifact_id,title="",format=plan.request.artifact.format.format,sha256=plan.request.artifact.sha256),plan.target.target.target_id,filename=plan.filename)
            destination=Path(result.path)
            if not destination.is_file(): raise NotFound("La cible n'a pas matérialisé le fichier", code="storage_transfer_missing")
            copied_sha=sha256_file(destination)
            if plan.request.artifact.sha256 and copied_sha != plan.request.artifact.sha256: raise Conflict("Checksum cible différent", code="storage_transfer_checksum_mismatch")
            placement_id=self.service.storage.placement_put({"artifact_id":plan.request.artifact.artifact_id,"path":str(destination),"identity_id":plan.request.identity_id,"provider_id":"storage","state":"present","last_seen_at":int(time.time()),"metadata":{"placement_kind":"storage","storage_target_id":plan.target.target.target_id,"storage_plugin_id":plan.target.plugin_id,"relative_path":str(result.relative_path)}})
            row=self.service.storage.placement_get(placement_id); placement=_placement_ref(row) if row else None
            if control is not None: control.report(current=1,total=1,message="Transfert terminé")
            if callable(recorder): recorder(plan.request.identity_id, "transfer", "success", plugin_id=plan.target.plugin_id, facts={"target_id": plan.target.target.target_id, "artifact_id": plan.request.artifact.artifact_id})
            return ArtifactTransferResult(True,plan.request.artifact,plan.target,placement,str(result.relative_path),facts={"sha256":copied_sha})
        except Exception as exc:
            if callable(recorder): recorder(plan.request.identity_id, "transfer", "failed", plugin_id=plan.target.plugin_id, facts={"target_id": plan.target.target.target_id, "error": type(exc).__name__})
            raise
