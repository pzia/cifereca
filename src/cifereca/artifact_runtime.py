from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import stat
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


class _LibraryFilenameConflict(Conflict):
    """Closed, catchable collision code; no path or identity in the error."""
    code = "library_filename_conflict"

class _DuplicateScopeConflict(Conflict):
    """A fixed UI-safe reason; never include private path or borrower text."""
    code = "placement_not_duplicate"

class _VariantScopeConflict(Conflict):
    """Deleting a shared SHA is not authority to unlink another book."""
    code = "artifact_variant_scope_conflict"




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
            status = next((s for s in manager.statuses() if s.descriptor.id == plugin_id), None)
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
        # An identity transform has exactly the original content-addressed
        # artifact_id. Persisting a self-variant would make delete_variant
        # select and physically unlink the ORIGINAL book placements. The
        # produced staging file is disposable; preserve the existing source
        # artifact and do not create a false independent derivative.
        if actual_sha == source.sha256:
            path.unlink()
            lease.active = False
            return source
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
            result = transformer.transform(request, source_lease, destination, control)
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

    def plan(self, request: LibraryMutationRequest) -> LibraryMutationPlan:
        op=request.operation
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
        if op == "delete_duplicate":
            row=self.service.storage.placement_get(request.placement_id); keep=self.service.storage.placement_get(request.keep_placement_id)
            if row is None or keep is None: raise NotFound("Placement inconnu", code="placement_not_found")
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
        if not targets: raise NotFound("Aucune cible de stockage compatible", code="storage_target_unavailable")
        target=targets[0]
        filename=Path(request.filename or request.artifact.filename or (request.artifact.artifact_id.replace(":","-")+"."+request.artifact.format.format)).name
        return ArtifactRoutePlan(request,target,filename,summary=f"Envoyer {filename} vers {target.target.label or target.target.target_id}")

    def transfer(self, plan: ArtifactRoutePlan, control: TaskControl | None = None) -> ArtifactTransferResult:
        recorder = getattr(self.service, "_record_action", None)
        try:
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
