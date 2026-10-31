"""#3185: bounded read-only inventory of canonical post-placement advisories.

An advisory is never a deletion authorization: only a separate, confirmed
supervisor may rehash files, migrate references and retire a physical copy.
No provider I/O, filesystem walk, SQLite mutation or private path disclosure.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Literal

from .canonical_post_placement import _PENDING_PREFIX, _unique_checkpoint_fields

_SCHEMA = "canonical.post-placement-dedup-pending.v1"
_MAX_ROWS = 250
_MAX_BYTES = 65536
_MAX_IDS = 65


@dataclass(frozen=True, slots=True)
class CanonicalDedupAdvisory:
    checkpoint_key: str
    identity_id: str
    artifact_id: str
    placement_ids: tuple[str, ...]
    status: Literal["pending_preview", "blocked"]
    reason: str


@dataclass(frozen=True, slots=True)
class CanonicalDedupAdvisoryReview:
    status: Literal["reviewed", "partial", "over_budget", "unavailable"]
    entries: tuple[CanonicalDedupAdvisory, ...] = ()

    @property
    def pending_count(self) -> int:
        return sum(row.status == "pending_preview" for row in self.entries)

    @property
    def blocked_count(self) -> int:
        return sum(row.status == "blocked" for row in self.entries)


def _valid_id(value: object) -> bool:
    return (
        type(value) is str and 0 < len(value) <= 256
        and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
    )


def review_canonical_dedup_advisories(
    storage, *, identity_id: str | None = None,
) -> CanonicalDedupAdvisoryReview:
    """Enumerate distinct persisted checkpoint pairs without claiming FS proof.

    When scoped, invalid records that cannot be attributed to the identity
    are omitted. Global invalid records count as blocked, never as clean.
    Unknown/mismatched checkpoints are retained in the database for review.
    """
    if identity_id is not None and not _valid_id(identity_id):
        return CanonicalDedupAdvisoryReview("unavailable")
    try:
        if storage.schema_version() < 14:
            return CanonicalDedupAdvisoryReview("unavailable")
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            records = db.execute(
                "SELECT key,CASE WHEN length(value)>? THEN NULL ELSE value END "
                "FROM metadata WHERE key LIKE ? ORDER BY key LIMIT ?",
                (_MAX_BYTES, _PENDING_PREFIX + "%", _MAX_ROWS + 1),
            ).fetchall()
            if len(records) > _MAX_ROWS:
                return CanonicalDedupAdvisoryReview("over_budget")
            entries = []
            unattributed = 0
            for key, raw in records:
                if type(key) is not str or not key.startswith(_PENDING_PREFIX):
                    continue

                def blocked(reason: str, *, owner: str = "",
                            artifact: str = "",
                            ids: tuple[str, ...] = ()):
                    return CanonicalDedupAdvisory(
                        key, owner, artifact, ids, "blocked", reason,
                    )

                try:
                    data = json.loads(
                        raw, object_pairs_hook=_unique_checkpoint_fields,
                    ) if type(raw) is str else None
                except (ValueError, TypeError, RecursionError):
                    data = None
                owner = data.get("identity_id") if type(data) is dict else None
                if identity_id is not None and owner != identity_id:
                    # A syntactically valid OTHER identity is not sufficient
                    # proof of ownership. The opaque metadata key must match
                    # the claimed owner + Artifact fingerprint. Otherwise a
                    # forged owner could silently hide an A checkpoint from A.
                    artifact_claim = (
                        data.get("artifact_id") if type(data) is dict else None
                    )
                    claim_valid = (
                        _valid_id(owner)
                        and type(artifact_claim) is str
                        and len(artifact_claim) == 71
                        and artifact_claim.startswith("sha256:")
                        and all(ch in "0123456789abcdef"
                                for ch in artifact_claim[7:])
                    )
                    if not claim_valid:
                        unattributed += 1
                    else:
                        claimed_key = _PENDING_PREFIX + hashlib.sha256(json.dumps(
                            [owner, artifact_claim],
                            separators=(",", ":"), ensure_ascii=True,
                        ).encode("ascii")).hexdigest()
                        if claimed_key != key:
                            unattributed += 1
                    continue
                if (type(data) is not dict or set(data) != {
                        "schema", "identity_id", "artifact_id", "placement_ids",
                    } or data["schema"] != _SCHEMA
                        or not _valid_id(owner)
                        or type(data["artifact_id"]) is not str
                        or len(data["artifact_id"]) != 71
                        or not data["artifact_id"].startswith("sha256:")
                        or any(ch not in "0123456789abcdef"
                               for ch in data["artifact_id"][7:])
                        or type(data["placement_ids"]) is not list
                        or not 2 <= len(data["placement_ids"]) <= _MAX_IDS
                        or any(not _valid_id(pid)
                               for pid in data["placement_ids"])
                        or data["placement_ids"] != sorted(set(data["placement_ids"]))):
                    entries.append(blocked("checkpoint_invalid"))
                    continue
                artifact = data["artifact_id"]
                ids = tuple(data["placement_ids"])
                expected_key = _PENDING_PREFIX + hashlib.sha256(json.dumps(
                    [owner, artifact], separators=(",", ":"), ensure_ascii=True,
                ).encode("ascii")).hexdigest()
                if key != expected_key:
                    entries.append(blocked(
                        "checkpoint_scope_mismatch", owner=owner,
                        artifact=artifact, ids=ids,
                    ))
                    continue
                artifact_row = db.execute(
                    "SELECT sha256 FROM artifacts WHERE artifact_id=?",
                    (artifact,),
                ).fetchone()
                if artifact_row != (artifact[7:],):
                    entries.append(blocked(
                        "artifact_unavailable", owner=owner,
                        artifact=artifact, ids=ids,
                    ))
                    continue
                sql = (
                    "SELECT placement_id,identity_id,artifact_id,state,"
                    "provider_id,source_item_id "
                    "FROM placements WHERE placement_id IN ("
                    + ",".join("?" for _ in ids) + ")"
                )
                placements = {
                    row[0]: row[1:] for row in db.execute(sql, ids).fetchall()
                }
                if len(placements) != len(ids) or any(
                    placements.get(pid, ())[:2] != (owner, artifact)
                    for pid in ids
                ):
                    reason = "placement_record_changed"
                elif any(placements[pid][2] == "dedup_cleanup_pending"
                         for pid in ids):
                    reason = "recovery_review_required"
                elif any(placements[pid][2] != "present" for pid in ids):
                    reason = "placement_record_changed"
                elif any(placements[pid][3:] != (None, None) for pid in ids):
                    # Canonical retire accepts only unbound physical B
                    # Placements. Legacy provider/item rows need independent
                    # reconciliation, never a green supervised preview.
                    reason = "legacy_placement_requires_review"
                else:
                    entries.append(CanonicalDedupAdvisory(
                        key, owner, artifact, ids,
                        "pending_preview", "physical_review_required",
                    ))
                    continue
                entries.append(blocked(
                    reason, owner=owner, artifact=artifact, ids=ids,
                ))
            return CanonicalDedupAdvisoryReview(
                "partial" if unattributed or any(
                    e.status == "blocked" for e in entries
                ) else "reviewed",
                tuple(entries),
            )
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError):
        return CanonicalDedupAdvisoryReview("unavailable")
    except Exception:
        # A missing table or unexpected reader error is never a clean queue.
        return CanonicalDedupAdvisoryReview("unavailable")


__all__ = [
    "CanonicalDedupAdvisory",
    "CanonicalDedupAdvisoryReview",
    "review_canonical_dedup_advisories",
]
