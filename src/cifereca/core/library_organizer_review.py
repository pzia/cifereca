"""#3176: one private, paginated organizer review, never an executable plan.

Reuse Core's inventory, naming policy and keeper reviewer. Classification
cannot be inferred from suffix or a folder. Verified OPF type claims are
advice only; the current directory remains unchanged.
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
        # The connection is worker-local. The binding API takes its own
        # non-reentrant lock, so do not hold it across that call.
        # Confirmation calls this guard *before* their own exception boundary:
        # unavailable SQLite/binding must refuse the advice, never escape to UI.
        try:
            return (self.current()
                    and not self.storage._connect().in_transaction
                    and self.storage.exact_profile_directory_binding(self.identity)
                    == (self.directory, True))
        except Exception:
            return False


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
    with scope.storage._lock:
        db = scope.storage._connect()
        if db.in_transaction:
            return None
        with db:
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
    with scope.storage._lock:
        db = scope.storage._connect()
        if db.in_transaction:
            return True
        with db:
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


def _naming(scope, path, signature, description, *, opf=None):
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


def _completed_acsm_layout(scope, path, artifact_id):
    """Read-only eligibility of one finalized technical subdirectory.

    The folder name is never provenance: the same canonical checkpoint
    validator used by the relocation transaction must verify the receipt.
    """
    import json
    from .canonical_recovery import _relocated_completed_checkpoint
    from .duplicate_maintenance_guard import _mentions_exact_path

    owner = scope.root / scope.directory
    relative = path.relative_to(owner)
    if (len(relative.parts) != 3
            or re.fullmatch(r"acsm-[0-9a-f]{64}", relative.parts[1]) is None):
        return None
    provider = relative.parts[0]
    target = owner / provider / path.name
    with scope.storage._lock:
        db = scope.storage._connect()
        if db.in_transaction:
            return None
        with db:
            db.execute("BEGIN")
            placement = db.execute(
                "SELECT provider_id,source_item_id FROM placements "
                "WHERE path=? AND identity_id=? AND artifact_id=? AND state='present'",
                (str(path), scope.identity, artifact_id),
            ).fetchone()
            if placement != (None, None):
                return None
            candidates = db.execute(
                "SELECT acquisition_id,provider_id,credential_id,account_id,item_id,"
                "identity_id,status,artifact_id,metadata_json FROM acquisition_ledger "
                "WHERE identity_id=? AND artifact_id=? LIMIT 33",
                (scope.identity, artifact_id),
            ).fetchall()
            if not candidates or len(candidates) > 32:
                return None
            verified = 0
            for row in candidates:
                raw = row[-1]
                if type(raw) is not str or len(raw) > 65536:
                    return None
                data = json.loads(raw)
                if type(data) is not dict:
                    return None
                if not _mentions_exact_path(data, str(path)):
                    continue
                if row[1] != provider:
                    return None
                _relocated_completed_checkpoint(
                    db, row[0], row[1:], identity_id=scope.identity,
                    artifact_id=artifact_id, old_path=str(path), new_path=str(target))
                verified += 1
            if not verified:
                return None
    return target


def _classified_path(scope, path, name, details, artifact_id):
    """Layout advice, never a move grant or a proof derived from a pathname."""
    owner = scope.root / scope.directory
    relative = path.relative_to(owner)
    if len(relative.parts) == 3 and relative.parts[1].startswith("acsm-"):
        target = _completed_acsm_layout(scope, path, artifact_id)
        if target is None:
            return None, "classified_source_not_admitted"
    else:
        if len(relative.parts) != 2 or relative.parts[0] != "Acquisitions":
            return None, "classified_source_not_admitted"
        if details is None or details[3] not in ("Livres", "BD-Manga", "Documents") or not name:
            return None, "classified_evidence_unavailable"
        authors = details[1]
        if len(authors) != 1:
            return None, "classified_author_unverified"
        author = authors[0]
        if (not _text(author, 255) or len(author.encode("utf-8")) > 255
                or author != author.strip() or author.startswith(".")
                or "/" in author or chr(92) in author
                or not author.isprintable()):
            return None, "classified_author_unverified"
        target = owner / details[3] / author / name
    target_relative = target.relative_to(owner)
    with scope.storage._lock:
        db = scope.storage._connect()
        if db.in_transaction:
            return None, "classified_target_unverified"
        with db:
            if db.execute("SELECT 1 FROM placements WHERE path=? LIMIT 1",
                          (str(target),)).fetchone() is not None:
                return None, "classified_target_occupied"
    fd = None
    try:
        fd = _open_root(owner)
        for component in target_relative.parts[:-1]:
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY |
                                os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except FileNotFoundError:
                return str(target.relative_to(owner)), "classified_directory_missing"
            os.close(fd)
            fd = child
        try:
            os.stat(target.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return str(target.relative_to(owner)), "classified_layout_advice_only"
        return None, "classified_target_occupied"
    except OSError:
        return None, "classified_target_unverified"
    finally:
        if fd is not None:
            os.close(fd)


def review_organizer_file(scope, artifact_id, row, description, *, allow_classified_path=False):
    """One exact local filename observation shared with the read-only audit."""
    pid, raw, state, digest, size, media_format = row
    path = _ordinary_path(scope, raw)

    def result(current, name, reason, verified_path=None, category=None,
               layout=None, layout_reason=None):
        return ({"placement": pid, "current": current, "proposed_name": name,
                 "reason": reason, "category_advice": category,
                 "category_evidence": "verified_opf_type_claim" if category else None,
                 "classified_path_advice": layout,
                 "classified_path_reason": layout_reason}, verified_path)

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
            details = None
            if media_format in ("epub", "kepub"):
                from .local_origin_epub_evidence import read_verified_epub_details
                details = read_verified_epub_details(path, signature)
            name, reason = _naming(scope, path, signature, description,
                                   opf=details[:2] if details is not None else None)
            layout, layout_reason = (None, None)
            if allow_classified_path:
                layout, layout_reason = _classified_path(scope, path, name, details, artifact_id)
            recheck()
        return result(relative, name, reason, path,
                      details[3] if details is not None else None, layout, layout_reason)
    except (OSError, ValueError, TypeError, RuntimeError):
        return result(relative, None, "physical_unverified")


def _single_local_rename(scope, group):
    """Separate local naming observation; never qualify a dedup candidate.

    The shared inventory deliberately vetoes all source-bound placements.
    A single local occurrence with a matching catalogue can still be reviewed
    for filename advice. The existing writer must independently qualify all
    operational references, and the organizer execution fence stays closed.
    """
    if (group.status != "blocked" or group.reason != "legacy_source_scope_unbound"
            or len(group.placements) != 1 or not group.placements[0].catalogued):
        return False
    pid = group.placements[0].placement_id
    with scope.storage._lock:
        db = scope.storage._connect()
        if db.in_transaction:
            return False
        with db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT provider_id, source_item_id FROM placements "
                "WHERE placement_id=? AND identity_id=? AND artifact_id=? AND state='present'",
                (pid, scope.identity, group.artifact_id),
            ).fetchone()
            if row is None or row[0] != "local" or not _text(row[1], 512):
                return False
            entries = db.execute(
                "SELECT provider_id,item_id FROM catalog_entries "
                "WHERE placement_id=? ORDER BY catalog_entry_id LIMIT 9", (pid,),
            ).fetchall()
            return (0 < len(entries) <= 8
                    and all(tuple(entry) == tuple(row) for entry in entries))


def _group(scope, group):
    """Classify one inventory group; blocked groups never become a safe batch."""
    report = {"status": group.status, "reason": group.reason,
              "copies": len(group.placements), "files": (), "keeper": None,
              "documentary_nature": "unverified", "category_advice": "Non classés",
              "category_evidence": None, "relocation_proposed": False}
    local_rename = _single_local_rename(scope, group)
    if ((group.status not in ("unique", "candidate_review") and not local_rename)
            or not 1 <= len(group.placements) <= _MAX_COPIES):
        return report, None
    if local_rename:
        report.update(status="unique", reason="single_registered_local_placement")
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
        file, verified_path = review_organizer_file(
            scope, group.artifact_id, row, descriptions[row[0]],
            # The exact registered local occurrence is admitted above for
            # naming advice; the same unique verified file may be classified.
            # Neither advice grants execution; the writer and fence still veto.
            allow_classified_path=report["status"] == "unique" and len(rows) == 1,
        )
        files.append(file)
        if verified_path is not None:
            named[row[0]] = verified_path
    if _placement_facts(scope, group) != snapshot:
        return {**report, "status": "blocked", "reason": "scope_changed"}, None
    categories = {file["category_advice"] for file in files}
    if len(categories) == 1 and None not in categories and len(named) == len(rows):
        report["category_advice"] = next(iter(categories))
        report["category_evidence"] = "verified_opf_type_claim"
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
                # Private proof: advice selection must refer to the exact
                # scope and SQLite snapshot that were reviewed.
                "review_anchor": OrganizerCursor(scope, stamp, offset),
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
            or not scope.bound() or type(result) is not dict
            or result.get("mutation_authorized") is not False):
        return None
    try:
        anchor = result.get("review_anchor")
        if (type(anchor) is not OrganizerCursor or anchor.scope is not scope
                or sqlite_source_stamp(scope.database) != anchor.stamp):
            return None
        reviews = result["keeper_reviews"]
        if type(reviews) is not tuple or len(reviews) > _PAGE:
            return None
        original = preview_keeper_bulk(reviews, identity_id=scope.identity)
        if original != result.get("keeper_advice"):
            return None
        # The SQLite/WAL stamp cannot attest unchanged filesystem bytes.
        # Reverify every eligible keeper group at the Core selection boundary,
        # including direct consumers that do not use the public broker.
        eligible = {choice.artifact_id for choice in original.eligible}
        for review in reviews:
            if review.artifact_id in eligible:
                if (not scope.bound()
                        or sqlite_source_stamp(scope.database) != anchor.stamp
                        or review_canonical_keeper(
                            scope.storage, library_root=scope.root,
                            identity_id=scope.identity, artifact_id=review.artifact_id,
                        ) != review):
                    return None
        selected = preview_keeper_bulk(reviews, identity_id=scope.identity, accept_all=True)
        return (selected if scope.bound()
                and sqlite_source_stamp(scope.database) == anchor.stamp else None)
    except Exception:
        # Advice selection is read-only. A failed SQLite/physical recheck or
        # a revoked/malformed private preview must never escape to CoreUI.
        return None
