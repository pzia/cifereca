"""P1 #4496: bounded, read-only keeper preference over verified canonical copies.

A human-readable filename and placement under a classified folder are only
aesthetic preferences, NEVER bibliographic classification or evidence of a
right. Every candidate for retirement must separately pass the existing Core
physical+dependency preview. This review cannot permit or trigger a write;
ordinary cleanup is still suspended independently in canonical_dedup_supervisor.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3

from .canonical_dedup_exclusions import ordinary_dedup_path_excluded
from .canonical_dedup_preview import preview_canonical_duplicate


_MAX_ROWS = 4096
_MAX_COPIES = 64
_SHA_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HEX_NAME = re.compile(r"(?:sha256[-_])?[0-9a-f]{64}\Z", re.I)
_RECEIPT_SUFFIX = re.compile(r"[0-9a-f]{64}\Z")
_CLASSIFIED = frozenset(("Livres", "BD-Manga", "Livres audio", "Documents"))


@dataclass(frozen=True, slots=True)
class CanonicalKeeperReview:
    status: str
    reason: str
    identity_id: str
    artifact_id: str
    keeper_placement_id: str = ""
    candidate_placement_ids: tuple[str, ...] = ()
    eligible_copies: int = 0
    excluded_copies: int = 0
    # This is NOT a consent token, mutation plan or authority to unlink.
    executable: bool = False


def _rank(path: Path, owner: Path) -> tuple[int, int]:
    """Prefer human-readable names, then an already organized location.

    No title, author, media type, rights or genre is inferred from the path.
    Equal ranks are deliberately *ambiguous*; do not break ties by ID.
    """
    relative = path.relative_to(owner)
    if path.suffix.casefold() not in (".epub", ".pdf"):
        raise ValueError("keeper_format_unranked")
    # Assess the human part of a canonical publication name separately.
    prefix, _, suffix = path.stem.rpartition(" - ")
    has_receipt_suffix = bool(prefix and _RECEIPT_SUFFIX.fullmatch(suffix))
    pieces = (prefix if has_receipt_suffix else path.stem).split(" - ")
    readable = (
        (1 if has_receipt_suffix else 2) <= len(pieces) <= 5
        and all(0 < len(item.strip()) <= 180 and item == item.strip()
                and not _HEX_NAME.fullmatch(item) for item in pieces)
        and len(path.stem) <= 250
    )
    folder = relative.parts[0]
    position = (
        2 if folder in _CLASSIFIED and len(relative.parts) >= 3
        else 1 if folder == "Non classés" and len(relative.parts) >= 2
        else 0
    )
    return int(readable), position


def review_canonical_keeper(
    storage, *, library_root: Path, identity_id: str, artifact_id: str,
) -> CanonicalKeeperReview:
    """Recommend exactly one keeper only when all *other* copies are verified.

    The complete exact-identity/Artifact placement set is read with a bounded
    SQLite snapshot. The reserved Outbox is excluded, never considered a
    keeper or a retirement candidate. A missing or legacy placement, physical
    uncertainty, active/opaque dependency, same-ranked candidates or mutable
    scope fails closed. The result is always advisory and must not be fed
    to commit without a fresh, explicitly confirmed canonical supervisor.
    """
    def blocked(reason: str, *, eligible: int = 0, excluded: int = 0):
        return CanonicalKeeperReview(
            "blocked", reason, identity_id if type(identity_id) is str else "",
            artifact_id if type(artifact_id) is str else "",
            eligible_copies=eligible, excluded_copies=excluded,
        )

    if (type(identity_id) is not str or not 0 < len(identity_id) <= 256
            or type(artifact_id) is not str
            or _SHA_ID.fullmatch(artifact_id) is None
            or not isinstance(library_root, Path)
            or not library_root.is_absolute() or ".." in library_root.parts):
        return blocked("invalid_scope")
    try:
        if storage.schema_version() < 14:
            return blocked("schema_unavailable")

        def snapshot():
            with storage._lock, storage._connect() as db:
                db.execute("BEGIN")
                owner_row = db.execute(
                    "SELECT directory_name FROM identities WHERE identity_id=?",
                    (identity_id,),
                ).fetchone()
                if (owner_row is None or type(owner_row[0]) is not str
                        or not owner_row[0] or owner_row[0] in (".", "..")
                        or "/" in owner_row[0] or "\\" in owner_row[0]):
                    raise ValueError("keeper_profile_unverified")
                owner_name = owner_row[0]
                if db.execute(
                    "SELECT 1 FROM identities WHERE directory_name=? "
                    "AND identity_id<>? LIMIT 1",
                    (owner_name, identity_id),
                ).fetchone() is not None:
                    raise ValueError("keeper_profile_ambiguous")
                rows = db.execute(
                    "SELECT placement_id,path,state,provider_id,source_item_id "
                    "FROM placements WHERE identity_id=? AND artifact_id=? "
                    "ORDER BY placement_id LIMIT ?",
                    (identity_id, artifact_id, _MAX_ROWS + 1),
                ).fetchall()
                return owner_name, rows

        owner_name, original_rows = snapshot()
        if len(original_rows) > _MAX_ROWS:
            return blocked("inventory_budget")
        owner = library_root / owner_name
        eligible: list[tuple[str, Path, tuple[int, int]]] = []
        excluded = 0
        seen_paths: set[str] = set()
        for placement_id, raw, state, provider, source_item in original_rows:
            if (type(placement_id) is not str or not placement_id
                    or type(raw) is not str or not raw or len(raw) > 4096):
                return blocked("placement_unverified")
            path = Path(raw)
            if not path.is_absolute() or ".." in path.parts:
                return blocked("placement_unverified")
            try:
                relative = path.relative_to(owner)
            except ValueError:
                return blocked("placement_wrong_profile")
            if len(relative.parts) < 2 and relative.parts[0:1] != ("Outbox",):
                # Root-level books may be valid, but need at least one name.
                if not relative.parts:
                    return blocked("placement_unverified")
            key = raw.casefold()
            if key in seen_paths:
                return blocked("placement_collision")
            seen_paths.add(key)
            # The reserved Outbox is never a candidate, even if the device
            # is not mounted. Its entries do not consume the copy budget.
            if relative.parts[0] == "Outbox":
                excluded += 1
                continue
            if (state != "present" or provider is not None
                    or source_item is not None):
                return blocked("noncanonical_placement")
            if ordinary_dedup_path_excluded(
                path, library_root=library_root, identity_id=identity_id,
                owner_directory=owner_name,
            ):
                return blocked("excluded_or_unverified_path")
            try:
                rank = _rank(path, owner)
            except ValueError:
                return blocked("format_not_ranked")
            eligible.append((placement_id, path, rank))
            if len(eligible) > _MAX_COPIES:
                return blocked("ordinary_copy_budget", eligible=len(eligible),
                               excluded=excluded)

        if len(eligible) < 2:
            return blocked("insufficient_ordinary_copies",
                           eligible=len(eligible), excluded=excluded)
        highest = max(rank for _id, _path, rank in eligible)
        preferred = [row for row in eligible if row[2] == highest]
        if len(preferred) != 1:
            return blocked("ambiguous_keeper_preference",
                           eligible=len(eligible), excluded=excluded)
        keeper = preferred[0][0]
        candidates = tuple(item[0] for item in eligible if item[0] != keeper)
        for candidate in candidates:
            evidence = preview_canonical_duplicate(
                storage, library_root=library_root,
                candidate_placement_id=candidate,
                keeper_placement_id=keeper,
            )
            if (evidence.status != "unreferenced"
                    or evidence.reason != "verified_no_active_references"
                    or not evidence.provenance_counted):
                return blocked("candidate_not_safe_for_retirement",
                               eligible=len(eligible), excluded=excluded)
        # Defend against an ordinary identity/placement edit during rehash.
        # Even a stable result is only a point-in-time observation, never
        # an authorization: physical ABA requires the supervisor's locks.
        if snapshot() != (owner_name, original_rows):
            return blocked("snapshot_changed", eligible=len(eligible),
                           excluded=excluded)
        return CanonicalKeeperReview(
            "review_required", "aesthetic_preference_only",
            identity_id, artifact_id, keeper,
            candidates, len(eligible), excluded,
        )
    except (OSError, ValueError, TypeError, sqlite3.Error, RecursionError):
        return blocked("inspection_unavailable")
    except Exception:
        return blocked("inspection_unavailable")


__all__ = ["CanonicalKeeperReview", "review_canonical_keeper"]
