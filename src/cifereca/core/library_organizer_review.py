"""#3176: one private, paginated organizer review, never an executable plan.

Reuse Core's inventory, naming policy and keeper reviewer. Classification
cannot be inferred from suffix or a folder; this first view intentionally
keeps the current directory and identifies unverified documentary nature.
Audio bundles are held as units and never expanded into rename suggestions.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from ..filename_suffixes import split_filename_suffix
from .canonical_placement_inventory import read_canonical_placement_inventory
from .canonical_keeper_review import review_canonical_keeper
from .canonical_keeper_bulk_preview import preview_keeper_bulk
from .canonical_dedup_exclusions import ordinary_dedup_path_excluded
from .derived_variant_publication import _verified_variant_file
from .library_inventory import _open_root
from .library_sqlite_freshness import sqlite_source_stamp
from .local_origin_suggestion_operator import _root, _navigation_epoch
from .source_context import source_context_epoch

_PAGE = 4
_MAX_COPIES = 8
_MAX_HASH_BYTES = 128 * 1024 * 1024
_RECEIPT = re.compile(r"(?:^| - )([0-9a-f]{64})$")
_SUPPORTED_SUFFIX = (".epub", ".kepub.epub", ".fxl.kepub.epub", ".pdf")


def _text(value, limit=4096):
    return (type(value) is str and 0 < len(value) <= limit
            and all(ord(c) >= 32 and ord(c) != 127 for c in value))


@dataclass(frozen=True, slots=True, repr=False)
class OrganizerScope:
    service: object
    owner: object
    identity: str
    directory: str
    root: Path
    storage: object
    database: Path
    config: object
    filename_template: object
    source_epoch: int
    navigation_epoch: int

    def current(self):
        try:
            s = self.service
            return (s.current_identity is self.owner
                    and s.identity_by_id(self.identity) is self.owner
                    and self.owner.identity_id == self.identity
                    and self.owner.directory_name == self.directory
                    and s.config is self.config and _root(s.config) == self.root
                    and s.config.get("library.filename_template", "{author} - {title}")
                    == self.filename_template
                    and s.storage is self.storage and Path(s.storage.path) == self.database
                    and source_context_epoch(s) == self.source_epoch
                    and _navigation_epoch(s) == self.navigation_epoch)
        except Exception:
            return False

    def bound(self):
        return (self.current()
                and self.storage.exact_profile_directory_binding(self.identity)
                == (self.directory, True))


@dataclass(frozen=True, slots=True, repr=False)
class OrganizerCursor:
    scope: OrganizerScope
    stamp: tuple
    offset: int


def admit_organizer_review(service):
    try:
        owner = service.current_identity
        if (not _text(owner.identity_id, 256)
                or not _text(owner.directory_name, 256)
                or owner.directory_name in (".", "..")
                or "/" in owner.directory_name or chr(92) in owner.directory_name):
            return None
        template = service.config.get("library.filename_template", "{author} - {title}")
        if not _text(template, 2048):
            return None
        scope = OrganizerScope(
            service, owner, owner.identity_id, owner.directory_name,
            _root(service.config), service.storage, Path(service.storage.path),
            service.config, template,
            source_context_epoch(service), _navigation_epoch(service),
        )
        return scope if scope.current() else None
    except Exception:
        return None


def _placement_facts(scope, group):
    """Only exact group paths and bounded local descriptions, no metadata dumps."""
    with scope.storage._lock, scope.storage._connect() as db:
        db.execute("BEGIN")
        rows = db.execute(
            "SELECT p.placement_id,p.path,p.state,a.sha256,a.size,a.media_format "
            "FROM placements p JOIN artifacts a ON a.artifact_id=p.artifact_id "
            "WHERE p.identity_id=? AND p.artifact_id=? ORDER BY p.placement_id LIMIT ?",
            (scope.identity, group.artifact_id, _MAX_COPIES+1),
        ).fetchall()
        if tuple(row[0] for row in rows) != tuple(p.placement_id for p in group.placements):
            return None
        descriptions = {}
        for pid, *_ in rows:
            values = db.execute(
                "SELECT artifact_id,identity_id,local_state,"
                "CASE WHEN length(CAST(title AS BLOB))<=2048 THEN title END,"
                "CASE WHEN length(CAST(author AS BLOB))<=2048 THEN author END "
                "FROM catalog_entries WHERE placement_id=? ORDER BY catalog_entry_id LIMIT 9",
                (pid,),
            ).fetchall()
            if (len(values) > 8 or any(v[:3] != (
                    group.artifact_id, scope.identity, "local") for v in values)):
                descriptions[pid] = None
            else:
                choices = {(v[3], v[4]) for v in values}
                descriptions[pid] = next(iter(choices)) if len(choices) == 1 else None
        return tuple(rows), descriptions


def _ordinary_path(scope, raw):
    if not _text(raw):
        return None
    path, owner = Path(raw), scope.root / scope.directory
    if (not path.is_absolute() or str(path) != raw or ".." in path.parts
            or path == owner or not path.is_relative_to(owner)):
        return None
    relative = path.relative_to(owner)
    if (any(part.startswith(".") for part in relative.parts)
            or ordinary_dedup_path_excluded(
                path, library_root=scope.root, identity_id=scope.identity,
                owner_directory=scope.directory)):
        return None
    return path


def _destination_occupied(scope, target):
    # Never treat same bytes as permission to replace another Placement.
    with scope.storage._lock, scope.storage._connect() as db:
        if db.execute("SELECT 1 FROM placements WHERE path=? LIMIT 1",
                      (str(target),)).fetchone() is not None:
            return True
    fd = _open_root(target.parent)
    try:
        try:
            os.stat(target.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True
    finally:
        os.close(fd)


def _naming(scope, path, signature, description):
    """Use the existing policy, not a second naming engine or a new right."""
    stem, suffix = split_filename_suffix(path)
    if suffix.casefold() not in _SUPPORTED_SUFFIX:
        return None, "unsupported_filename_convention"
    if (description is None or not _text(description[0], 512)
            or description[0] == "Sans titre"
            or (description[1] is not None and not _text(description[1], 512))):
        return None, "description_unverified"
    title, author = description
    if suffix.casefold() != ".pdf":
        # Filename/catalogue fallback is not proof of OPF title/author.
        from .local_origin_epub_evidence import read_verified_epub_description
        opf = read_verified_epub_description(path, signature)
        if (opf is None or opf[0] != title
                or (opf[1][0] if len(opf[1]) == 1 else None) != (author or None)
                or len(opf[1]) > 1):
            return None, "description_unverified"
    else:
        # A PDF catalogue title may derive solely from its old filename.
        # No attested PDF metadata reader is consumed by this organizer yet.
        return None, "pdf_description_unverified"
    name = scope.service.preview_library_filename(
        title=title, author=author or "", extension=suffix[1:],
    )
    if (not _text(name, 255) or len(name.encode("utf-8")) > 255
            or Path(name).name != name or chr(92) in name or name.startswith(".")
            or split_filename_suffix(Path(name))[1] != suffix):
        return None, "naming_policy_unverified"
    receipt = _RECEIPT.search(stem)
    if receipt and not split_filename_suffix(Path(name))[0].endswith(receipt[1]):
        return None, "receipt_discriminator_preserve"
    target = path.with_name(name)
    if target == path:
        return name, "name_unchanged"
    if _destination_occupied(scope, target):
        return None, "destination_occupied"
    return name, "rename_advice_only"


def review_organizer_file(scope, artifact_id, row, description):
    """One exact local filename observation shared with the read-only audit."""
    pid, raw, state, digest, size, media_format = row
    path = _ordinary_path(scope, raw)

    def result(current, name, reason, verified_path=None):
        return ({"placement": pid, "current": current, "proposed_name": name,
                 "reason": reason}, verified_path)

    if path is None:
        return result(None, None, "excluded_or_unverified_path")
    relative = str(path.relative_to(scope.root / scope.directory))
    if state == "missing":
        return result(relative, None, "registered_missing")
    if (state != "present" or media_format not in ("epub", "kepub", "pdf")
            or type(digest) is not str or artifact_id != "sha256:" + digest):
        return result(relative, None, "format_or_bundle_held")
    try:
        with _verified_variant_file(path, digest, expected_size=size) as recheck:
            signature = recheck()
            name, reason = _naming(scope, path, signature, description)
            recheck()
        return result(relative, name, reason, path)
    except (OSError, ValueError, TypeError, RuntimeError):
        return result(relative, None, "physical_unverified")


def _group(scope, group):
    """Classify one inventory group; blocked groups never become a safe batch."""
    report = {"status": group.status, "reason": group.reason,
              "copies": len(group.placements), "files": (), "keeper": None,
              "documentary_nature": "unverified", "relocation_proposed": False}
    if (group.status not in ("unique", "candidate_review")
            or not 1 <= len(group.placements) <= _MAX_COPIES):
        return report, None
    snapshot = _placement_facts(scope, group)
    if snapshot is None:
        return {**report, "status": "blocked", "reason": "scope_changed"}, None
    rows, descriptions = snapshot
    # Each keeper review hashes the keeper again for every candidate.
    if (not rows or any(type(row[4]) is not int or row[4] <= 0 for row in rows)
            or sum(row[4] for row in rows) * 3 > _MAX_HASH_BYTES):
        return {**report, "status": "blocked", "reason": "physical_budget"}, None
    files = []
    named = {}
    for row in rows:
        file, verified_path = review_organizer_file(scope, group.artifact_id, row, descriptions[row[0]])
        files.append(file)
        if verified_path is not None:
            named[row[0]] = verified_path
    if _placement_facts(scope, group) != snapshot:
        return {**report, "status": "blocked", "reason": "scope_changed"}, None
    review = None
    if group.status == "candidate_review" and len(named) == len(rows):
        review = review_canonical_keeper(
            scope.storage, library_root=scope.root, identity_id=scope.identity,
            artifact_id=group.artifact_id,
        )
        if review.status == "review_required":
            report["keeper"] = str(named[review.keeper_placement_id].relative_to(
                scope.root / scope.directory))
        else:
            report["reason"] = review.reason
    return {**report, "files": tuple(files)}, review


def run_organizer_review(service, scope, *, cursor=None):
    """At most four groups; retain a DB/WAL stamp across independent pages.

    No provider, scan writer, directory creation, plan.run, unlink, or execute
    path exists. A page is a point-in-time observation, never consent.
    """
    if type(scope) is not OrganizerScope or scope.service is not service:
        return None
    try:
        if not scope.bound():
            return None
        stamp = sqlite_source_stamp(scope.database)
        if stamp is None:
            return None
        offset = 0
        if cursor is not None:
            if (type(cursor) is not OrganizerCursor or cursor.scope is not scope
                    or cursor.stamp != stamp or type(cursor.offset) is not int
                    or not 0 < cursor.offset < 5000 or cursor.offset % _PAGE):
                return None
            offset = cursor.offset
        inventory = read_canonical_placement_inventory(scope.storage, identity_id=scope.identity)
        if inventory.status not in ("reviewed", "partial"):
            return None
        reports, reviews = [], []
        for group in inventory.groups[offset:offset+_PAGE]:
            if not scope.bound():
                return None
            report, review = _group(scope, group)
            reports.append(report)
            if review is not None:
                reviews.append(review)
        advice = preview_keeper_bulk(tuple(reviews), identity_id=scope.identity)
        if not scope.bound() or sqlite_source_stamp(scope.database) != stamp:
            return None
        following = offset + _PAGE
        return {"groups": tuple(reports), "inventory_status": inventory.status,
                "placement_count": inventory.placement_count, "group_count": len(inventory.groups),
                "page": offset//_PAGE+1,
                "next_cursor": OrganizerCursor(scope, stamp, following)
                if following < len(inventory.groups) else None,
                "keeper_advice": advice, "keeper_reviews": tuple(reviews),
                "mutation_authorized": False,
                "classification_attested": False, "inventory_physical_complete": False}
    except Exception:
        return None


def select_organizer_keeper_advice(scope, result, *, confirmed=False):
    """Explicit selection of reviewed ADVICE only; never a mutation plan."""
    if (confirmed is not True or type(scope) is not OrganizerScope
            or not scope.current() or type(result) is not dict
            or result.get("mutation_authorized") is not False):
        return None
    try:
        reviews = result["keeper_reviews"]
        if type(reviews) is not tuple or len(reviews) > _PAGE:
            return None
        original = preview_keeper_bulk(reviews, identity_id=scope.identity)
        if original != result.get("keeper_advice"):
            return None
        selected = preview_keeper_bulk(reviews, identity_id=scope.identity, accept_all=True)
        return selected if scope.current() else None
    except (ValueError, TypeError, KeyError):
        return None
