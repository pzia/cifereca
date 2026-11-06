"""P1 #4496: bounded 'Tout accepter' selection for safe keeper ADVICE only.

This combines independent CanonicalKeeperReview snapshots into a deterministic
UI preview. It is not a deletion batch, consent, postcheck, receipt, lock,
supervisor plan or a capability to bypass the ordinary deduplication hold.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .canonical_keeper_review import CanonicalKeeperReview

_MAX_ITEMS = 256
_MAX_RETIRE_IDS = 2048
_SHA_ARTIFACT = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{0,95}\Z")


@dataclass(frozen=True, slots=True)
class KeeperAdviceChoice:
    artifact_id: str
    keeper_placement_id: str
    retire_candidate_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KeeperBulkPreview:
    identity_id: str
    eligible: tuple[KeeperAdviceChoice, ...]
    preselected: tuple[KeeperAdviceChoice, ...]
    blocked: tuple[tuple[str, str], ...]  # artifact ID, closed reason
    excluded_copies: int
    # Never a mutation consent, fresh Core snapshot or executable plan.
    executable: bool = False


def _placement(value: object) -> bool:
    return (type(value) is str and 0 < len(value) <= 256
            and value.isprintable() and value == value.strip()
            and "/" not in value and "\\" not in value)


def preview_keeper_bulk(
    reviews: object, *, identity_id: str, accept_all: bool = False,
) -> KeeperBulkPreview:
    """Preselect only individually eligible, unambiguous keeper advice.

    For every artifact, all candidate placements must have passed the
    separate Core physical/dependency reviewer. Blocked entries remain
    visible but cannot be preselected. Any duplicate placement or artifact
    across reviews is an invalid snapshot, never resolved by input order.
    """
    if (type(identity_id) is not str or not 0 < len(identity_id) <= 256
            or not identity_id.isprintable()
            or type(accept_all) is not bool
            or type(reviews) not in (tuple, list)
            or len(reviews) > _MAX_ITEMS):
        raise ValueError("invalid_keeper_bulk_scope")
    seen_artifacts: set[str] = set()
    seen_placements: set[str] = set()
    eligible: list[KeeperAdviceChoice] = []
    blocked: list[tuple[str, str]] = []
    excluded = 0
    total_retired = 0
    for item in reviews:
        if (type(item) is not CanonicalKeeperReview
                or item.identity_id != identity_id
                or type(item.artifact_id) is not str
                or _SHA_ARTIFACT.fullmatch(item.artifact_id) is None
                or item.artifact_id in seen_artifacts
                or item.executable is not False
                or type(item.eligible_copies) is not int
                or type(item.excluded_copies) is not int
                or not 0 <= item.eligible_copies <= 4096
                or not 0 <= item.excluded_copies <= 4096
                or type(item.reason) is not str
                or _REASON.fullmatch(item.reason) is None):
            raise ValueError("invalid_keeper_bulk_review")
        seen_artifacts.add(item.artifact_id)
        excluded += item.excluded_copies
        if item.status == "blocked":
            if (item.keeper_placement_id or item.candidate_placement_ids
                    or type(item.candidate_placement_ids) is not tuple):
                raise ValueError("invalid_keeper_bulk_blocked")
            blocked.append((item.artifact_id, item.reason))
            continue
        if item.status != "review_required" or item.reason != "aesthetic_preference_only":
            raise ValueError("invalid_keeper_bulk_review_state")
        keeper = item.keeper_placement_id
        retired = item.candidate_placement_ids
        if (not _placement(keeper) or type(retired) is not tuple
                or not retired
                or item.eligible_copies != len(retired) + 1
                or any(not _placement(x) for x in retired)
                or len(set(retired)) != len(retired)
                or keeper in retired):
            raise ValueError("invalid_keeper_bulk_choice")
        identifiers = {keeper, *retired}
        if identifiers & seen_placements:
            raise ValueError("overlapping_keeper_bulk_placements")
        seen_placements.update(identifiers)
        total_retired += len(retired)
        if total_retired > _MAX_RETIRE_IDS:
            raise ValueError("keeper_bulk_budget_exceeded")
        eligible.append(KeeperAdviceChoice(
            artifact_id=item.artifact_id,
            keeper_placement_id=keeper,
            retire_candidate_ids=tuple(sorted(retired)),
        ))
    eligible.sort(key=lambda x: x.artifact_id)
    blocked.sort()
    selected = tuple(eligible) if accept_all else ()
    return KeeperBulkPreview(
        identity_id=identity_id, eligible=tuple(eligible),
        preselected=selected, blocked=tuple(blocked),
        excluded_copies=excluded,
    )


__all__ = ["KeeperAdviceChoice", "KeeperBulkPreview", "preview_keeper_bulk"]
