from __future__ import annotations

import os
import stat as _stat
import hashlib
import mimetypes
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .acquisition_engine import Artifact, stable_id
from .artifact_inspection import detect_artifact_format
from .epub_metadata import SCHEMA as EPUB_METADATA_SCHEMA, inspect_epub, cached_cover_url
from .plugin_api import BUNDLE_MANIFEST_NAME, BundleError, load_bundle_manifest


# Locally received copies are physical possessions, never evidence that the
# recipient has purchased the source account's book (#3275). A scan only
# refreshes filesystem facts and must not erase the already stored origin.
def _preserved_received_copy(metadata: object, owner: object) -> dict[str, str]:
    if type(metadata) is not dict or type(owner) is not str or not owner:
        return {}
    source = metadata.get("materialized_from")
    origin = metadata.get("file_origin_identity_id")
    kind = metadata.get("transfer_kind")
    if type(source) is not str or not 0 < len(source) <= 512:
        return {}
    if (origin is not None and (type(origin) is not str or not origin
                                or len(origin) > 512 or origin == owner)):
        return {}
    if kind not in (None, "local_copy"):
        return {}
    kept = {"materialized_from": source}
    if type(origin) is str:
        kept["file_origin_identity_id"] = origin
    if kind == "local_copy":
        kept["transfer_kind"] = kind
    return kept


SUPPORTED_EXTENSIONS = {
    ".epub": "epub",
    ".pdf": "pdf",
    ".mp3": "mp3",
    ".m4a": "m4a",
    ".ogg": "ogg",
    ".opus": "opus",
    ".flac": "flac",
    ".wav": "wav",
}


@dataclass(frozen=True)
class CatalogScanResult:
    root: Path
    scanned: int
    cataloged: int
    invalid: int
    unsupported: int
    missing_marked: int
    artifacts_new: int
    placements_new: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "scanned": self.scanned,
            "cataloged": self.cataloged,
            "invalid": self.invalid,
            "unsupported": self.unsupported,
            "missing_marked": self.missing_marked,
            "artifacts_new": self.artifacts_new,
            "placements_new": self.placements_new,
        }


class LocalCatalog:
    """Local-first catalog for the functional library owned by Paris.

    0.8.0 deliberately discovers facts from disk instead of deleting or moving
    user files. Identity/provider path components are interpreted when present;
    legacy flat layouts stay cataloged without inferring origin from a 0.7 backup.
    """

    def __init__(self, storage: Any, root: Path) -> None:
        self.storage = storage
        self.root = root.expanduser()

    @staticmethod
    def sha256(path: Path) -> str:
        # A book can be replaced after the rglob()/is_file() check. Hash via
        # a no-follow descriptor and require the same inode/size/timestamps
        # at both ends. Never hash an external symlink target as local proof.
        if not hasattr(os, "O_NOFOLLOW"):
            raise OSError("catalog_scan_nofollow_unavailable")
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path, flags | getattr(os, "O_NONBLOCK", 0))
        try:
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                before = os.fstat(handle.fileno())
                if not _stat.S_ISREG(before.st_mode):
                    raise OSError("catalog_scan_not_regular")
                digest = hashlib.sha256()
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                after = os.fstat(handle.fileno())
                named = os.stat(path, follow_symlinks=False)
                fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
                if any(
                    getattr(before, key) != getattr(after, key)
                    or getattr(before, key) != getattr(named, key)
                    for key in fields
                ):
                    raise OSError("catalog_scan_file_changed")
                return digest.hexdigest()
        finally:
            if fd >= 0:
                os.close(fd)

    @staticmethod
    def _media_format(path: Path) -> str | None:
        name = path.name.lower()
        if name.endswith(".kepub.epub"):
            return "kepub"
        return SUPPORTED_EXTENSIONS.get(path.suffix.lower())

    @staticmethod
    def _validate(path: Path, media_format: str) -> tuple[bool, str | None]:
        try:
            if path.stat().st_size <= 0:
                return False, "empty_file"
            if media_format in {"epub", "kepub"}:
                if not zipfile.is_zipfile(path):
                    return False, "invalid_epub_zip"
                with zipfile.ZipFile(path) as archive:
                    names = set(archive.namelist())
                    if "META-INF/container.xml" not in names:
                        return False, "missing_epub_container"
                return True, None
            if media_format == "pdf":
                with path.open("rb") as handle:
                    if not handle.read(5).startswith(b"%PDF-"):
                        return False, "invalid_pdf_header"
                return True, None
            return True, None
        except (OSError, zipfile.BadZipFile):
            return False, "unreadable_file"

    def _path_hints(self, path: Path) -> tuple[str | None, str | None]:
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            return None, None
        parts = rel.parts
        # The first directory may be a registered identity even when a book
        # lives directly under it (root/<identity>/book.pdf). Neither that
        # directory nor its children attest any acquisition provider.
        # Legacy flat files still have no inferred identity.
        if len(parts) >= 2:
            directory_name = parts[0]
            identity_id = None
            try:
                match = next((row for row in self.storage.identities() if str(row.get("directory_name") or "") == directory_name), None)
                if match:
                    identity_id = str(match.get("identity_id") or directory_name)
                else:
                    previous = self.storage.placement_for_path(path.resolve())
                    owner = (previous or {}).get("identity_id")
                    registered = {row["identity_id"] for row in self.storage.identities()}
                    if owner and (owner in registered or any(row.get("identity_id") == owner for row in self.storage.acquisitions()) or any(row.get("identity_id") == owner for row in self.storage.entitlements())):
                        identity_id = owner
            except Exception:
                pass
            # The registered directory can identify the local library, but
            # its second component is ONLY a physical folder label. A path
            # like <identity>/manual/, /other/, or even /numilog/ does not
            # attest an acquisition provider. Only an explicit caller scope,
            # exact historical receipt or existing placement can supply that.
            return identity_id, None
        # Likewise a top-level directory is not a supplier declaration.
        return None, None

    def _canonical_stage_visible(self, path: Path) -> bool:
        """Uncommitted candidate bytes are not public library placements.

        After Core's atomic provider OriginLink+Placement publication, the
        same file is eligible for the normal verified catalog scan. This
        read-only fence cannot grant source rights or infer an OriginLink.
        """
        try:
            try:
                relative = path.relative_to(self.root)
            except ValueError:
                relative = path.relative_to(self.root.resolve())
        except ValueError:
            return False
        if ".canonical-delivery" not in relative.parts:
            return True
        # No global catalogue rescan or legacy backup lookup. The exact
        # committed physical path needs an OriginLink in the same profile.
        with self.storage._lock, self.storage._connect() as db:
            row = db.execute(
                "SELECT 1 FROM placements AS p "
                "JOIN origin_links AS o ON o.artifact_id=p.artifact_id "
                "AND o.identity_id=p.identity_id AND o.origin_kind='provider' "
                "WHERE p.path=? AND p.state='present' LIMIT 1",
                (str(path.resolve()),),
            ).fetchone()
        return row is not None

    def _index_bundle(
        self,
        root: Path,
        *,
        title: str | None = None,
        item_id: str | None = None,
        identity_id: str | None = None,
        provider_id: str | None = None,
        preserved_catalogue_ids: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        root = root.expanduser().resolve()
        path_text = str(root)
        existing = self.storage.placement_for_path(Path(path_text))
        placement_id = existing["placement_id"] if existing else stable_id("placement", path_text)
        identity_hint, provider_hint = self._path_hints(root)
        if identity_id is not None and identity_hint and identity_id != identity_hint:
            # An explicit caller cannot register A's physical bundle as B's
            # placement, even when no SQLite placement exists yet.
            return {"status": "invalid", "path": path_text,
                    "reason": "placement_scope_conflict", "artifact_kind": "bundle"}
        identity_hint = identity_id or identity_hint
        provider_hint = provider_id or provider_hint
        if existing and any((
            identity_hint and existing.get("identity_id")
                and identity_hint != existing["identity_id"],
            provider_hint and existing.get("provider_id")
                and provider_hint != existing["provider_id"],
            item_id and existing.get("source_item_id")
                and item_id != existing["source_item_id"],
        )):
            # A bundle rescan (or targeted reindex) cannot silently transfer
            # ownership merely because an unrelated folder/caller says so.
            return {"status": "invalid", "path": path_text,
                    "reason": "placement_scope_conflict", "artifact_kind": "bundle"}
        if existing:
            identity_hint = identity_hint or existing.get("identity_id")
            provider_hint = provider_hint or existing.get("provider_id")
            item_id = item_id or existing.get("source_item_id")
        now = int(time.time())
        try:
            manifest = load_bundle_manifest(root, verify=True)
        except BundleError:
            # A manifest is controlled by a plugin or external archive. Its
            # validation exception may contain filenames, signed references
            # or raw manifest values. Persist only an explicit closed reason.
            reason = "bundle_manifest_invalid"
            self.storage.placement_put({
                "placement_id": placement_id,
                "artifact_id": (existing or {}).get("artifact_id"), "path": path_text,
                "identity_id": identity_hint, "provider_id": provider_hint,
                "source_item_id": item_id, "state": "invalid", "last_seen_at": now,
                "metadata": {**dict((existing or {}).get("metadata") or {}),
                             "invalid_reason": reason, "artifact_kind": "bundle"},
            })
            # Storage.placement_put invalidates all linked catalogue rows
            # within the same SQLite commit; historical ownership is retained.
            return {"status": "invalid", "path": path_text, "reason": reason, "artifact_kind": "bundle"}

        artifact = Artifact.from_bundle(
            manifest, metadata={"discovered_by": "targeted_catalog_index", "bundle_manifest_file": BUNDLE_MANIFEST_NAME}
        )
        artifact_was_new = self.storage.artifact_get(artifact.artifact_id) is None
        placement_was_new = self.storage.placement_get(placement_id) is None
        self.storage.artifact_put(artifact)
        try:
            relative = str(root.relative_to(self.root.resolve()))
        except ValueError:
            relative = root.name
        manifest_path = root / BUNDLE_MANIFEST_NAME
        try:
            stat = manifest_path.stat()
            stat_meta = {"manifest_size": int(stat.st_size), "manifest_mtime_ns": int(stat.st_mtime_ns)}
        except OSError:
            stat_meta = {}
        self.storage.placement_put({
            "placement_id": placement_id, "artifact_id": artifact.artifact_id, "path": path_text,
            "identity_id": identity_hint, "provider_id": provider_hint, "source_item_id": item_id,
            "state": "present", "last_seen_at": now,
            "metadata": {
                **_preserved_received_copy((existing or {}).get("metadata"), identity_hint),
                "relative_path": relative, "artifact_kind": "bundle", **stat_meta,
            },
        })
        member_rows = [member.as_dict() for member in manifest.members]
        metadata = {
            "path": path_text, "artifact_kind": "bundle", "playlist": manifest.playlist,
            "bundle_sha256": manifest.bundle_sha256, "bundle_members": member_rows,
            "bundle_metadata": dict(manifest.metadata),
        }
        # Parent-level descriptive metadata may be supplied by the plugin in the
        # autonomous manifest and remains reconstructable after a database loss.
        metadata.update({k: v for k, v in dict(manifest.metadata).items() if k not in metadata})
        self.storage.catalog_entry_put({
            "catalog_entry_id": (preserved_catalogue_ids or {}).get(placement_id)
                                or stable_id("catalog", placement_id),
            "artifact_id": artifact.artifact_id,
            "placement_id": placement_id, "title": str(title or manifest.title or root.name),
            "author": manifest.author or None, "media_format": manifest.media_format,
            "identity_id": identity_hint, "provider_id": provider_hint, "item_id": item_id,
            "local_state": "local", "metadata": metadata,
        })
        return {
            "status": "indexed", "path": path_text, "media_format": manifest.media_format,
            "artifact_kind": "bundle", "artifact_id": artifact.artifact_id,
            "artifact_new": artifact_was_new, "placement_new": placement_was_new,
            "members": len(manifest.members),
        }

    def index_path(
        self,
        path: Path,
        *,
        title: str | None = None,
        item_id: str | None = None,
        identity_id: str | None = None,
        provider_id: str | None = None,
    ) -> dict[str, Any]:
        """Index one newly materialized artifact without scanning the library."""
        # A targeted index is a claim about *this library's* physical copy.
        # resolve() alone follows a supplied file/directory symlink and may
        # silently register an external PDF/EPUB under a chosen account.
        # The configured library root itself may intentionally be a symlink.
        submitted = Path(os.path.abspath(path.expanduser()))
        root_alias = Path(os.path.abspath(self.root))
        root_resolved = self.root.resolve()
        relative = None
        anchor = None
        for candidate_root in (root_alias, root_resolved):
            try:
                relative = submitted.relative_to(candidate_root)
                anchor = candidate_root
                break
            except ValueError:
                continue
        if relative is None or anchor is None:
            return {"status": "invalid", "path": str(submitted),
                    "reason": "catalog_index_path_not_local"}

        def linked_child() -> bool:
            current = anchor
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    return True
            return False

        if linked_child():
            return {"status": "invalid", "path": str(submitted),
                    "reason": "catalog_index_path_not_local"}
        path = submitted.resolve()
        if not path.is_relative_to(root_resolved) or linked_child():
            return {"status": "invalid", "path": str(submitted),
                    "reason": "catalog_index_path_not_local"}
        if not self._canonical_stage_visible(path):
            return {"status": "invalid", "path": str(path),
                    "reason": "canonical_staging_not_published"}
        path_owner, _ = self._path_hints(path)
        if identity_id is not None and path_owner and path_owner != identity_id:
            # This is a new path, not necessarily an existing SQLite placement.
            # Reject the foreign owner *before* reading/hashing the file.
            return {"status": "invalid", "path": str(path),
                    "reason": "placement_scope_conflict"}
        if path.is_dir():
            return self._index_bundle(
                path, title=title, item_id=item_id, identity_id=identity_id, provider_id=provider_id,
            )
        media_format = self._media_format(path)
        if media_format is None:
            return {"status": "unsupported", "path": str(path)}
        # The validation must apply to the same inode subsequently hashed.
        # A replacement between _validate() and the first hash could otherwise
        # publish a corrupt PDF/EPUB as 'present' under its fresh SHA.
        try:
            validation_stat = os.stat(path, follow_symlinks=False)
            if not _stat.S_ISREG(validation_stat.st_mode):
                raise OSError("catalog_index_not_regular")
        except OSError:
            validation_stat = None
        ok, invalid_reason = self._validate(path, media_format)
        if validation_stat is None:
            if ok:
                raise OSError("catalog_index_file_changed")
        else:
            try:
                checked_stat = os.stat(path, follow_symlinks=False)
                if (not _stat.S_ISREG(checked_stat.st_mode)
                        or any(
                            getattr(validation_stat, key) != getattr(checked_stat, key)
                            for key in ("st_dev", "st_ino", "st_size",
                                        "st_mtime_ns", "st_ctime_ns")
                        )):
                    raise OSError("catalog_index_file_changed")
            except OSError as exc:
                raise OSError("catalog_index_file_changed") from exc
        path_text = str(path)
        existing = self.storage.placement_for_path(Path(path_text))
        placement_id = existing["placement_id"] if existing else stable_id("placement", path_text)
        identity_hint, provider_hint = self._path_hints(path)
        identity_hint = identity_id or identity_hint
        # The compatibility backup_items table may describe a path, but it
        # is not evidence of supplier ownership or an edition. Explicit
        # caller scope and an existing Placement remain authoritative here.
        provider_hint = provider_id or provider_hint
        proposed_item = item_id
        if existing and any((
            identity_hint and existing.get("identity_id")
                and identity_hint != existing["identity_id"],
            provider_hint and existing.get("provider_id")
                and provider_hint != existing["provider_id"],
            proposed_item and existing.get("source_item_id")
                and proposed_item != existing["source_item_id"],
        )):
            # A targeted re-index is not authorization to transfer an
            # existing purchase/loan between owners, providers or editions.
            return {"status": "invalid", "path": path_text,
                    "reason": "placement_scope_conflict"}
        if existing:
            identity_hint = identity_hint or existing.get("identity_id")
            provider_hint = provider_hint or existing.get("provider_id")
            proposed_item = proposed_item or existing.get("source_item_id")
        now = int(time.time())
        try:
            indexed_stat = validation_stat
            if indexed_stat is None:
                raise OSError("catalog_index_not_regular")
            stat_meta = {
                "file_size": int(indexed_stat.st_size),
                "mtime_ns": int(indexed_stat.st_mtime_ns),
            }
        except OSError:
            indexed_stat = None
            stat_meta = {}
        if not ok:
            # Current bytes are unusable, but historic artifact/edition/owner
            # remains evidence for reconciliation. Never advertise an invalid
            # physical placement as an available local catalogue entry.
            self.storage.placement_put({
                "placement_id": placement_id,
                "artifact_id": (existing or {}).get("artifact_id"),
                "path": path_text,
                "identity_id": identity_hint, "provider_id": provider_hint,
                "source_item_id": proposed_item,
                "state": "invalid", "last_seen_at": now,
                "metadata": {**dict((existing or {}).get("metadata") or {}),
                             "invalid_reason": invalid_reason,
                             "media_format": media_format, **stat_meta},
            })
            # Storage.placement_put also invalidates linked catalogue rows
            # transactionally, so no second, racy catalogue write is needed.
            return {"status": "invalid", "path": path_text, "reason": invalid_reason, "media_format": media_format}

        catalog_entry_id = stable_id('catalog', placement_id)
        previous_entry = self.storage.catalog_entry_get(catalog_entry_id)
        digest = self.sha256(path)
        artifact_id = f"sha256:{digest}"
        artifact_was_new = self.storage.artifact_get(artifact_id) is None
        placement_was_new = self.storage.placement_get(placement_id) is None
        mime_type = mimetypes.guess_type(path.name)[0]
        if media_format in {"epub", "kepub"}:
            mime_type = "application/epub+zip"
        artifact = Artifact.from_file(
            path, sha256=digest, media_format=media_format, mime_type=mime_type,
            metadata={**((self.storage.artifact_get(artifact_id) or {}).get("metadata") or {}), "discovered_by": "targeted_catalog_index"},
        )
        # Hashing itself uses a no-follow descriptor, but the pathname can
        # change *after* that hash and before SQLite publishes the placement.
        # Only publish a fresh file if it still names precisely the same
        # regular inode/size/timestamps observed prior to hashing.
        try:
            current_stat = os.stat(path, follow_symlinks=False)
            if (indexed_stat is None
                    or not _stat.S_ISREG(current_stat.st_mode)
                    or any(
                        getattr(indexed_stat, key) != getattr(current_stat, key)
                        for key in ("st_dev", "st_ino", "st_size",
                                    "st_mtime_ns", "st_ctime_ns")
                    )):
                raise OSError("catalog_index_file_changed")
        except OSError as exc:
            raise OSError("catalog_index_file_changed") from exc
        self.storage.artifact_put(artifact)
        try:
            relative = str(path.relative_to(self.root.resolve()))
        except ValueError:
            relative = path.name
        source_item_id = proposed_item
        self.storage.placement_put({
            "placement_id": placement_id, "artifact_id": artifact_id, "path": path_text,
            "identity_id": identity_hint, "provider_id": provider_hint, "source_item_id": source_item_id,
            "state": "present", "last_seen_at": now,
            "metadata": {
                **_preserved_received_copy((existing or {}).get("metadata"), identity_hint),
                "relative_path": relative, "artifact_kind": "file", **stat_meta,
            },
        })
        self.storage.catalog_entry_put({
            "catalog_entry_id": catalog_entry_id, "artifact_id": artifact_id,
            "placement_id": placement_id, "title": str(title or ((previous_entry or {}).get("title") if (previous_entry or {}).get("artifact_id")==artifact_id else None) or path.stem),
            "author": ((previous_entry or {}).get("author") if (previous_entry or {}).get("artifact_id")==artifact_id else None), "media_format": media_format,
            "identity_id": identity_hint, "provider_id": provider_hint, "item_id": source_item_id,
            "local_state": "local", "metadata": {**(dict((previous_entry or {}).get("metadata") or {}) if (previous_entry or {}).get("artifact_id")==artifact_id else {}), "path": path_text, "artifact_kind": "file"},
        })
        entry = self.storage.catalog_entry_get(catalog_entry_id)
        if entry is None:
            raise RuntimeError(f"Entrée catalogue introuvable après indexation: {catalog_entry_id}")
        if title:
            entry['metadata']['title_source']='explicit';self.storage.catalog_entry_put(entry)
        self._enrich_epub_entry(entry)
        return {
            "status": "indexed", "path": path_text, "media_format": media_format,
            "artifact_id": artifact_id, "artifact_new": artifact_was_new, "placement_new": placement_was_new,
        }

    def _enrich_epub_entry(self, entry):
        if entry.get('local_state') != 'local' or entry.get('media_format') not in {'epub','kepub'}:
            return entry
        artifact=self.storage.artifact_get(str(entry.get('artifact_id') or ''))
        if not artifact:return entry
        metadata=dict(entry.get('metadata') or {})
        path=Path(str(metadata.get('path') or ''))
        if not path.is_file():return entry
        cached=dict(artifact.get('metadata') or {})
        force=getattr(self,'_force_covers',False) and entry.get('artifact_id') not in getattr(self,'_cover_refresh_seen',set())
        complete=cached.get('epub_metadata_schema')==EPUB_METADATA_SCHEMA and not force
        if cached.get('epub_cover') and not cached_cover_url(cached):complete=False
        if not complete:
            inspected=inspect_epub(path)
            if hasattr(self,'_cover_refresh_seen'):self._cover_refresh_seen.add(entry.get('artifact_id'))
            # Content-derived facts attach to the Artifact hash, never a filename.
            cached={**cached,**inspected}
            if not inspected.get('epub_cover'):cached.pop('epub_cover',None)
            self.storage.artifact_put({**artifact,'metadata':cached})
        facts={key:value for key,value in cached.items() if key.startswith('epub_')}
        if not metadata.get('epub_metadata_schema'):
            descriptive={key:metadata[key] for key in ['description','publisher','published_at','languages','subjects','identifiers','series','collection'] if metadata.get(key)}
            if descriptive:metadata['catalogue_metadata']=descriptive
        if not facts.get('epub_cover'):metadata.pop('epub_cover',None)
        metadata.update(facts)
        local=facts.get('epub_metadata') or {}
        for key,value in {'description':local.get('description'),'publisher':local.get('publishers'),
                          'languages':local.get('languages'),'subjects':local.get('subjects'),
                          'identifiers':local.get('identifiers'),'series':[row.get('name') for row in local.get('series',[]) if row.get('name')]}.items():
            if value and not metadata.get(key):metadata[key]=value
        if local.get('title') and (entry.get('title') in {None,'',path.stem} or metadata.get('title_source')=='epub'):
            entry={**entry,'title':local['title']};metadata['title_source']='epub'
        if local.get('authors') and not entry.get('author'):entry={**entry,'author':' ; '.join(local['authors'])}
        enriched={**entry,'metadata':metadata}
        if enriched!=entry:self.storage.catalog_entry_put(enriched)
        return enriched

    def scan(self, *, repair_misnamed: bool = False, force_covers: bool = False) -> CatalogScanResult:
        self._force_covers = force_covers
        self._cover_refresh_seen = set()
        # Never fabricate an empty replacement for a temporarily unavailable
        # library (unmounted volume, removed root). Doing so and then marking
        # every previously indexed placement missing would turn an outage
        # into false absence evidence for reconciliation/reacquisition.
        if not self.root.exists():
            root = self.root.resolve()
            if any(
                type(placement.get("path")) is str
                and Path(placement["path"]).is_absolute()
                and Path(placement["path"]).is_relative_to(root)
                for placement in self.storage.placements()
            ):
                raise FileNotFoundError("catalog_scan_root_unavailable")
        self.root.mkdir(parents=True, exist_ok=True)
        # The library root can be unmounted or atomically replaced after
        # the initial existence check. A later os.walk on a vanished root
        # may yield zero entries without calling onerror; such an empty walk
        # must never authorize a mass transition to 'missing'.
        initial_root = self.root.stat()
        root_identity = (initial_root.st_dev, initial_root.st_ino)
        started_at = int(time.time())
        previous_catalog = {row["catalog_entry_id"]: row for row in self.storage.catalog_entries()}
        # A sent copy already has a catalogue row. Its original entry ID may
        # differ from the scan-generated ID; do not create a second book for B.
        received_catalogue_ids: dict[str, str] = {}
        ambiguous_transfers: set[str] = set()
        for entry in previous_catalog.values():
            pid = entry.get("placement_id")
            if (type(pid) is not str or not pid
                    or not _preserved_received_copy(
                        (entry.get("metadata") or {}), entry.get("identity_id"))):
                continue
            old = received_catalogue_ids.setdefault(pid, entry["catalog_entry_id"])
            if old != entry["catalog_entry_id"]:
                ambiguous_transfers.add(pid)
        for pid in ambiguous_transfers:
            received_catalogue_ids.pop(pid, None)
        # Only placements already eligible for a negative observation when
        # this inventory started can become 'missing'. Another acquisition
        # may insert a placement or restore an archived one after our rglob
        # snapshot and immediately before the final SQLite transition.
        negative_candidate_ids = {
            row["placement_id"] for row in self.storage.placements()
            if row.get("state") != "missing"
        }
        seen_paths: set[str] = set()
        scanned = cataloged = invalid = unsupported = artifacts_new = placements_new = 0

        # Never treat a linked file (or a file below a linked directory)
        # as an owned local book. resolve() would otherwise project its
        # external target as a placement, outside the configured library,
        # while the apparent symlink filename supplies misleading provenance.
        # The explicitly configured root itself may be a symlink; paths must
        # stay beneath that resolved root without crossing further links.
        resolved_root = self.root.resolve()

        def is_unlinked_local_file(path: Path) -> bool:
            try:
                relative = path.relative_to(self.root)
                candidate = self.root
                for part in relative.parts:
                    candidate = candidate / part
                    if candidate.is_symlink():
                        return False
                return path.resolve(strict=True).is_relative_to(resolved_root)
            except (OSError, ValueError, RuntimeError):
                return False

        bundle_roots = sorted({p.parent.resolve() for p in self.root.rglob(BUNDLE_MANIFEST_NAME) if p.is_file() and is_unlinked_local_file(p) and not any(part.startswith(".cifereca-migration-") for part in p.relative_to(self.root).parts)})
        bundle_root_set = set(bundle_roots)
        for bundle_root in bundle_roots:
            scanned += 1
            seen_paths.add(str(bundle_root))
            result = self._index_bundle(
                bundle_root, preserved_catalogue_ids=received_catalogue_ids,
            )
            if result.get("status") == "indexed":
                cataloged += 1
                artifacts_new += int(bool(result.get("artifact_new")))
                placements_new += int(bool(result.get("placement_new")))
            else:
                invalid += 1

        def inside_bundle(path: Path) -> bool:
            resolved = path.resolve()
            return any(root in resolved.parents for root in bundle_root_set)

        def same_scanned_file(path: Path, before) -> bool:
            # Compare the actual indexed filename with the stat preceding
            # SHA/cache lookup, including after metadata/Artifact creation.
            # A successful hash of one inode never attests replacement bytes.
            try:
                after = os.stat(path, follow_symlinks=False)
                return (
                    _stat.S_ISREG(after.st_mode)
                    and is_unlinked_local_file(path)
                    and all(
                        getattr(after, key) == getattr(before, key)
                        for key in ("st_dev", "st_ino", "st_size",
                                    "st_mtime_ns", "st_ctime_ns")
                    )
                )
            except (OSError, ValueError, RuntimeError):
                return False

        for path in sorted(p for p in self.root.rglob("*") if p.is_file() and not p.name.endswith(".part") and not inside_bundle(p) and not any(part.startswith(".cifereca-migration-") for part in p.relative_to(self.root).parts) and self._canonical_stage_visible(p)):
            scanned += 1
            if not is_unlinked_local_file(path):
                invalid += 1
                continue
            media_format = self._media_format(path)
            if media_format is None:
                unsupported += 1
                continue
            # Hold the physical snapshot across extension/format validation
            # and hashing: a replaced file cannot inherit an earlier valid
            # PDF/EPUB verdict simply because the hash is fresh.
            validation_stat = os.stat(path, follow_symlinks=False)
            if not _stat.S_ISREG(validation_stat.st_mode):
                raise OSError("catalog_scan_file_changed")
            ok, invalid_reason = self._validate(path, media_format)
            checked_stat = os.stat(path, follow_symlinks=False)
            if (not _stat.S_ISREG(checked_stat.st_mode)
                    or any(
                        getattr(validation_stat, key) != getattr(checked_stat, key)
                        for key in ("st_dev", "st_ino", "st_size",
                                    "st_mtime_ns", "st_ctime_ns")
                    )):
                raise OSError("catalog_scan_file_changed")
            if repair_misnamed and not ok and media_format in {"epub", "pdf"}:
                # Deep Sync is an inventory, never a file-maintenance consent.
                # The historical path.replace() ran before any SQLite commit
                # and silently rewrote only a subset of acquisition metadata,
                # stranding backup/placement/checkpoint provenance. Record the
                # mismatch for explicit repair instead of moving user files.
                actual = detect_artifact_format(path)
                if actual in {"epub", "pdf"} and actual != media_format:
                    invalid_reason = "extension_mismatch"
            path_text = str(path.resolve())
            seen_paths.add(path_text)
            identity_hint, provider_hint = self._path_hints(path)
            # A historic backup_items.local_path is never a current
            # provider/edition attestation, even for a single exact match.
            # Preserve the existing Placement scope, not the legacy hint.
            if not ok:
                invalid += 1
                # Invalid bytes revoke physical presence, not historic source
                # ownership. A deep scan must not replace an indexed artifact,
                # edition or identity with null/inferred provenance when its
                # extension is wrong. Keep the reference for manual review.
                previous = self.storage.placement_for_path(Path(path_text))
                self.storage.placement_put(
                    {
                        "placement_id": (previous or {}).get("placement_id")
                            or stable_id("placement", path_text),
                        "artifact_id": (previous or {}).get("artifact_id"),
                        "path": path_text,
                        "identity_id": (previous or {}).get("identity_id") or identity_hint,
                        "provider_id": (previous or {}).get("provider_id") or provider_hint,
                        "source_item_id": (previous or {}).get("source_item_id")
                            or None,
                        "state": "invalid",
                        "last_seen_at": started_at,
                        "metadata": {**((previous or {}).get("metadata") or {}),
                                     "invalid_reason": invalid_reason,
                                     "media_format": media_format},
                    }
                )
                # The physical placement is no longer trusted; an old
                # catalogue projection must not continue to advertise this
                # file as local/usable while its bytes are invalid.
                if previous:
                    for entry in self.storage.catalog_entries():
                        if (entry.get("placement_id") == previous["placement_id"]
                                and entry.get("local_state") != "invalid"):
                            self.storage.catalog_entry_put({
                                **entry, "local_state": "invalid",
                            })
                continue

            existing = self.storage.placement_for_path(Path(path_text))
            # A folder hint is not a transaction that
            # transfers a file's already recorded owner/provider/edition.
            # Keep existing authority and mark the physical projection
            # untrusted for review rather than silently moving provenance to
            # another identity on an ordinary (including deep) scan.
            proposed_item = None
            if existing and any((
                identity_hint and existing.get("identity_id")
                    and identity_hint != existing["identity_id"],
                provider_hint and existing.get("provider_id")
                    and provider_hint != existing["provider_id"],
                proposed_item and existing.get("source_item_id")
                    and proposed_item != existing["source_item_id"],
            )):
                invalid += 1
                self.storage.placement_put({
                    **existing,
                    "state": "invalid",
                    "last_seen_at": started_at,
                    "metadata": {
                        **dict(existing.get("metadata") or {}),
                        "invalid_reason": "placement_scope_conflict",
                    },
                })
                for entry in self.storage.catalog_entries():
                    if entry.get("placement_id") == existing["placement_id"]:
                        self.storage.catalog_entry_put({
                            **entry, "local_state": "invalid",
                        })
                continue
            # Re-discovery is physical evidence, not a new ownership event.
            # An existing scoped placement can sit in a flat/legacy directory
            # with no identity, provider or edition encoded in its name. Never
            # replace those established coordinates with absent path hints.
            # Genuine contradictory non-empty hints were rejected above.
            if existing:
                identity_hint = identity_hint or existing.get("identity_id")
                provider_hint = provider_hint or existing.get("provider_id")
                proposed_item = proposed_item or existing.get("source_item_id")
            placement_id = existing["placement_id"] if existing else stable_id("placement", path_text)
            previous_placement = self.storage.placement_get(placement_id)
            placement_was_new = previous_placement is None
            stat = validation_stat
            previous_meta = dict((previous_placement or {}).get("metadata") or {})
            artifact_id = str((previous_placement or {}).get("artifact_id") or "")
            cached_unchanged = bool(
                artifact_id
                and int(previous_meta.get("file_size") or -1) == int(stat.st_size)
                and int(previous_meta.get("mtime_ns") or -1) == int(stat.st_mtime_ns)
                # Size and mtime are not a sufficient local presence proof:
                # a restored mtime after an in-place replacement can retain
                # an unrelated file under the stale artifact SHA. Require a
                # full inode/device/ctime fingerprint before reusing bytes.
                # Older metadata simply rehashes on its next scan.
                and int(previous_meta.get("file_inode") or -1) == int(stat.st_ino)
                and int(previous_meta.get("file_device") or -1) == int(stat.st_dev)
                and int(previous_meta.get("file_ctime_ns") or -1) == int(stat.st_ctime_ns)
                and self.storage.artifact_get(artifact_id) is not None
            )
            if cached_unchanged:
                artifact_was_new = False
            else:
                digest = self.sha256(path)
                artifact_id = f"sha256:{digest}"
                artifact_was_new = self.storage.artifact_get(artifact_id) is None
                mime_type = mimetypes.guess_type(path.name)[0]
                if media_format in {"epub", "kepub"}:
                    mime_type = "application/epub+zip"
                artifact = Artifact.from_file(
                    path,
                    sha256=digest,
                    media_format=media_format,
                    mime_type=mime_type,
                    metadata={**((self.storage.artifact_get(artifact_id) or {}).get("metadata") or {}), "discovered_by": "local_catalog_scan"},
                )
                if not same_scanned_file(path, stat):
                    raise OSError("catalog_scan_file_changed")
                self.storage.artifact_put(artifact)
            if not same_scanned_file(path, stat):
                raise OSError("catalog_scan_file_changed")
            self.storage.placement_put(
                {
                    "placement_id": placement_id,
                    "artifact_id": artifact_id,
                    "path": path_text,
                    "identity_id": identity_hint,
                    "provider_id": provider_hint,
                    "source_item_id": proposed_item,
                    "state": "present",
                    "last_seen_at": started_at,
                    "metadata": {
                        "relative_path": str(path.relative_to(self.root)),
                        "file_size": int(stat.st_size),
                        "mtime_ns": int(stat.st_mtime_ns),
                        "file_inode": int(stat.st_ino),
                        "file_device": int(stat.st_dev),
                        "file_ctime_ns": int(stat.st_ctime_ns),
                        "artifact_kind": "file",
                        **_preserved_received_copy(previous_meta, identity_hint),
                    },
                }
            )
            self.storage.catalog_entry_put(
                {
                    "catalog_entry_id": received_catalogue_ids.get(placement_id)
                                        or stable_id("catalog", placement_id),
                    "artifact_id": artifact_id,
                    "placement_id": placement_id,
                    "title": path.stem,
                    "author": None,
                    "media_format": media_format,
                    "identity_id": identity_hint,
                    "provider_id": provider_hint,
                    "item_id": proposed_item,
                    "local_state": "local",
                    "metadata": {"path": path_text, "artifact_kind": "file"},
                }
            )
            cataloged += 1
            artifacts_new += int(artifact_was_new)
            placements_new += int(placement_was_new)

        # A scan updates disk facts. It must preserve descriptive provenance
        # already attached to an unchanged Artifact, including after relocation.
        # Reuse the same catalogue snapshot for EPUB enrichment instead of
        # rescanning the whole table a second time.
        for current in self.storage.catalog_entries():
            effective = current
            previous = previous_catalog.get(current["catalog_entry_id"])
            if previous and previous.get("artifact_id") == current.get("artifact_id") and previous.get("local_state") == "local":
                preserved = {**current, "title": previous["title"],
                             "author": current.get("author") or previous.get("author"),
                             "item_id": current.get("item_id") or previous.get("item_id"),
                             "metadata": {**previous.get("metadata", {}), **current.get("metadata", {})}}
                if preserved != current:
                    self.storage.catalog_entry_put(preserved)
                    effective = preserved
            if str((effective.get('metadata') or {}).get('path') or '') in seen_paths:
                self._enrich_epub_entry(effective)

        # pathlib.rglob can suppress scandir failures on some Python versions.
        # An unreadable child directory is *not* evidence its known files
        # vanished. Require a successful complete directory traversal before
        # applying the negative SQLite transition, even if positive files
        # were indexed earlier in this pass.
        def fail_incomplete(_error):
            raise OSError("catalog_scan_incomplete") from None

        for _ in os.walk(self.root, topdown=True, onerror=fail_incomplete,
                         followlinks=False):
            pass

        # A file can reappear after the earlier rglob snapshot, yet before
        # the negative SQLite transition. A full directory walk alone does
        # not mean this *specific* old path stayed absent throughout. Refuse
        # to mark anything missing if a previously indexed regular file has
        # become observable but was not in the scan's validated set. Symlinks
        # and files outside the configured root are never presence evidence.
        for placement in self.storage.placements(state="present"):
            previous_path = placement.get("path")
            if type(previous_path) is not str or not previous_path:
                continue
            if previous_path in seen_paths:
                continue
            candidate = Path(previous_path)
            if (candidate.is_absolute()
                    and candidate.is_relative_to(self.root)
                    and candidate.is_file()
                    and is_unlinked_local_file(candidate)):
                raise OSError("catalog_scan_changed_during_inventory")

        # Validate the directory *after* traversal and immediately before
        # the negative transition. A missing/replaced mount is not evidence
        # of missing books, even when os.walk reported no error.
        try:
            final_root = self.root.stat()
            if (not _stat.S_ISDIR(final_root.st_mode)
                    or (final_root.st_dev, final_root.st_ino) != root_identity):
                raise OSError("catalog_scan_root_changed")
        except OSError:
            raise OSError("catalog_scan_root_changed") from None
        missing_marked = self.storage.placements_mark_missing(
            self.root, seen_paths, seen_at=started_at,
            candidate_placement_ids=negative_candidate_ids,
        )
        return CatalogScanResult(
            root=self.root,
            scanned=scanned,
            cataloged=cataloged,
            invalid=invalid,
            unsupported=unsupported,
            missing_marked=missing_marked,
            artifacts_new=artifacts_new,
            placements_new=placements_new,
        )

    def entries(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return self.storage.catalog_entries(state=state)
