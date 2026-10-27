"""Read-only, provider-agnostic screen for *possible* older local materializations.

An exact bibliographic title is ONLY a reason to request manual reconciliation.
It is never proof of source, edition, entitlement, local file validity or
acquisition completion. In particular, this module neither scans nor opens
personal books, creates declarations, nor authorizes a supplier download.

The caller supplies explicit identity-owned candidate directories and records
already obtained from Core Storage. No knowledge of any provider identifier.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import unicodedata


_MAX_ROWS = 8192
_MAX_TITLE = 512


@dataclass(frozen=True, slots=True, repr=False)
class LocalCandidateScreen:
    status: str
    reason: str
    read_only: bool = True
    acquisition_authorized: bool = False


def _title(value: object) -> str:
    if type(value) is not str or len(value) > _MAX_TITLE:
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _path_in_roots(value: object, roots: tuple[Path, ...]) -> bool:
    """Lexical hint only: *never* follow a private symlink or stat a file."""
    if type(value) is not str or not 0 < len(value) <= 4096 or "\x00" in value:
        return False
    path = Path(value)
    if (not path.is_absolute() or ".." in path.parts
            or path.suffix.casefold() not in {".epub", ".pdf"}):
        return False
    return any(path != root and path.is_relative_to(root) for root in roots)


def screen_local_candidates(
    *, identity_id: str, title: str, roots: tuple[Path, ...],
    acquisitions: list[dict], catalogue: list[dict],
) -> LocalCandidateScreen:
    """Flag an indexed same-title local candidate, never infer its origin.

    'none_observed' is NOT a negative filesystem inventory or permission to
    acquire. A caller must separately enforce all provider/consent admission.
    Any truncated/untrustworthy evidence fails closed as review required.
    """
    review = lambda code: LocalCandidateScreen("needs_reconciliation", code)
    if (type(identity_id) is not str or not identity_id
            or type(roots) is not tuple or not roots
            or any(not isinstance(root, Path) or not root.is_absolute()
                   or root == Path("/") or ".." in root.parts for root in roots)
            or type(acquisitions) is not list or type(catalogue) is not list
            or len(acquisitions) > _MAX_ROWS or len(catalogue) > _MAX_ROWS):
        return review("local_evidence_unavailable")
    target = _title(title)
    if not target:
        return review("local_title_unavailable")

    for collection in (acquisitions, catalogue):
        for row in collection:
            if type(row) is not dict:
                return review("local_evidence_unavailable")
            if row.get("identity_id") != identity_id:
                continue
            # An acquisition must be complete; an index entry must still be
            # classified local. The caller can pass rows of both kinds.
            if collection is acquisitions and row.get("status") != "complete":
                continue
            if collection is catalogue and row.get("local_state") != "local":
                continue
            if _title(row.get("title")) != target:
                continue
            metadata = row.get("metadata")
            if type(metadata) is not dict:
                return review("local_evidence_unavailable")
            if _path_in_roots(metadata.get("path"), roots):
                return review("local_candidate_found")
    return LocalCandidateScreen("none_observed", "no_indexed_title_hint")


__all__ = ["LocalCandidateScreen", "screen_local_candidates"]
