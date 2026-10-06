from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
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


def _artifact_ref(storage: Any, artifact_id: str, *, book_id: str | None = None) -> ArtifactRef | None:
    row = storage.artifact_get(str(artifact_id))
    if row is None:
        return None
    placements = [p for p in storage.placements(state="present") if str(p.get("artifact_id") or "") == str(artifact_id)]
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


def _variant_ref(storage: Any, row: dict[str, Any], *, book_id: str = "") -> ArtifactVariant | None:
    artifact = storage.artifact_get(str(row.get("artifact_id") or ""))
    if artifact is None:
        return None
    placements = [p for p in storage.placements(state="present") if str(p.get("artifact_id") or "") == str(row.get("artifact_id") or "")]
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


@dataclass(slots=True)
class _InputLease:
    _path: Path
    _artifact: ArtifactRef
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
    active: bool = True
    @property
    def path(self) -> Path: return self._path
    def suggested_suffix(self) -> str:
        fmt = str(self.output_format.format or "").strip().lower()
        return ("." + fmt) if fmt else self._path.suffix


@dataclass(slots=True)
class ArtifactExecutionBrokerImpl:
    service: Any

    def _source_placement(self, artifact: ArtifactRef) -> dict[str, Any]:
        rows = [row for row in self.service.storage.placements(state="present") if str(row.get("artifact_id") or "") == artifact.artifact_id]
        for row in rows:
            path = Path(str(row.get("path") or ""))
            if path.is_file():
                return row
        raise NotFound("Artefact local introuvable", code="artifact_missing")

    def input_lease(self, artifact: ArtifactRef) -> _InputLease:
        row = self._source_placement(artifact)
        path = Path(str(row.get("path"))).expanduser().resolve()
        if artifact.sha256 and sha256_file(path) != artifact.sha256:
            raise Conflict("Le contenu local ne correspond plus à l'artefact", code="artifact_checksum_mismatch")
        return _InputLease(path, artifact)

    def output_lease(self, source: ArtifactRef, *, output_format: ArtifactFormat, proposed_filename: str = "", variant_kind: str = "") -> _OutputLease:
        source_row = self._source_placement(source)
        source_path = Path(str(source_row.get("path"))).expanduser().resolve()
        staging = source_path.parent / ".cifereca-staging" / "variants"
        staging.mkdir(parents=True, exist_ok=True)
        suffix = "." + str(output_format.format or source_path.suffix.lstrip(".") or "bin").lower().lstrip(".")
        fd, name = tempfile.mkstemp(prefix="variant-", suffix=suffix, dir=staging)
        os.close(fd)
        path = Path(name)
        path.unlink(missing_ok=True)
        return _OutputLease(path, source, output_format, str(proposed_filename or ""), str(variant_kind or "derived"))

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
            status = next((row for row in manager.statuses() if row.descriptor.id == plugin_id), None)
            return status.descriptor.version if status else ""
        except Exception:
            return ""
t((s for s in manager.statuses() if s.descriptor.id == plugin_id), None)
            return str(status.descriptor.version) if status else ""
        except Exception:
            return ""

    def commit_variant(self, *, source: ArtifactRef, destination: Any, result: TransformResult, transformer_plugin_id: str, profile_plugin_id: str | None = None, options_fingerprint: str = "") -> ArtifactRef:
        lease = self._assert_output_lease(destination)
        if lease.source.artifact_id != source.artifact_id:
            raise Conflict("Le lease de sortie ne correspond pas à la source", code="artifact_lease_source_mismatch")
        path = lease.path
        if not path.is_file():
            raise NotFound("Le transformer n'a produit aucun fichier", code="artifact_output_missing")
        actual_sha = sha256_file(path); actual_size = path.stat().st_size
        if result.sha256 and actual_sha != result.sha256:
            raise Conflict("Checksum de transformation invalide", code="artifact_output_checksum_mismatch")
        if result.size_bytes and actual_size != int(result.size_bytes):
            raise Conflict("Taille de transformation invalide", code="artifact_output_size_mismatch")
        existing = self.service.storage.artifact_variant_find(
            source_sha256=source.sha256, transformer_plugin_id=transformer_plugin_id,
            profile_plugin_id=profile_plugin_id, options_fingerprint=options_fingerprint,
        )
        if existing:
            artifact = _artifact_ref(self.service.storage, str(existing.get("artifact_id") or ""), book_id=source.book_id)
            path.unlink(missing_ok=True); lease.active = False
            if artifact is not None: return artifact
        source_placement = self._source_placement(source)
        source_path = Path(str(source_placement.get("path"))).resolve()
        proposed = Path(str(result.proposed_filename or lease.proposed_filename or "")).name
        if not proposed:
            suffix = "." + str(result.output_format.format or lease.output_format.format or source_path.suffix.lstrip(".")).lstrip(".")
            proposed = f"{source_path.stem} [{lease.variant_kind or 'variant'}]{suffix}"
        suffix = Path(proposed).suffix or ("." + str(result.output_format.format or lease.output_format.format or "bin").lstrip("."))
        stem = Path(proposed).stem or source_path.stem
        final = source_path.parent / (stem + suffix)
        if final.exists() and sha256_file(final) != actual_sha:
            final = source_path.parent / f"{stem}-{actual_sha[:8]}{suffix}"
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, final)
        lease.active = False
        now = int(time.time())
        artifact_id = f"sha256:{actual_sha}"
        self.service.storage.artifact_put({
            "artifact_id": artifact_id, "sha256": actual_sha, "size": actual_size,
            "media_format": result.output_format.format or lease.output_format.format or suffix.lstrip("."),
            "mime_type": result.output_format.media_type or lease.output_format.media_type,
            "metadata": {"variant_kind": lease.variant_kind or "derived", "source_artifact_id": source.artifact_id, "book_id": source.book_id or ""},
        })
        placement_id = self.service.storage.placement_put({
            "artifact_id": artifact_id, "path": str(final), "identity_id": source_placement.get("identity_id"),
            "provider_id": source_placement.get("provider_id"), "source_item_id": source_placement.get("source_item_id"),
            "state": "present", "last_seen_at": now,
            "metadata": {"variant": True, "variant_kind": lease.variant_kind or "derived", "source_artifact_id": source.artifact_id},
        })
        source_entries = [e for e in self.service.storage.catalog_entries() if str(e.get("placement_id") or "") == str(source_placement.get("placement_id") or "")]
        if source_entries:
            src_entry = source_entries[0]
            meta = dict(src_entry.get("metadata") or {}); meta.update({"path": str(final), "variant": True, "source_artifact_id": source.artifact_id})
            self.service.storage.catalog_entry_put({
                "artifact_id": artifact_id, "placement_id": placement_id, "title": src_entry.get("title"), "author": src_entry.get("author"),
                "media_format": result.output_format.format, "identity_id": src_entry.get("identity_id"), "provider_id": src_entry.get("provider_id"),
                "item_id": src_entry.get("item_id"), "local_state": "local", "metadata": meta,
            })
        self.service.storage.artifact_variant_put({
            "artifact_id": artifact_id, "source_artifact_id": source.artifact_id, "source_sha256": source.sha256,
            "variant_kind": lease.variant_kind or "derived", "transformer_plugin_id": transformer_plugin_id,
            "transformer_version": self._version_for_plugin(transformer_plugin_id), "profile_plugin_id": profile_plugin_id,
            "profile_version": self._version_for_plugin(profile_plugin_id), "options_fingerprint": options_fingerprint,
            "metadata": {"book_id": source.book_id or "", "warnings": list(result.warnings), "facts": dict(result.facts)},
        })
        ref = _artifact_ref(self.service.storage, artifact_id, book_id=source.book_id)
        if ref is None: raise RuntimeError("committed artifact disappeared")
        return ref

    def abort_output(self, destination: Any) -> None:
        lease = self._assert_output_lease(destination)
        lease.path.unlink(missing_ok=True); lease.active = False

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
        raise NotFound("Aucun transformer compatible", code="artifact_transformer_unavailable")

    def execute_transform(self, request: TransformRequest, *, transformer_plugin_id: str = "", control: TaskControl | None = None) -> ArtifactRef:
        plugin_id, transformer = self._select_transformer(request, transformer_plugin_id)
        source_lease = self.input_lease(request.source)
        plan = transformer.plan(request, source_lease, control)
        destination = self.output_lease(request.source, output_format=plan.output_format, proposed_filename=plan.proposed_filename, variant_kind=request.capability_id or "derived")
        identity_id = None
        try:
            placement = self._source_placement(request.source)
            identity_id