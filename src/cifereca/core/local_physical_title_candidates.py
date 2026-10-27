"""Bounded read-only physical-title hints for unindexed local EPUBs (#3228).

This generic Core primitive uses the existing no-follow inventory. It only
builds *reconciliation candidates*, never provider provenance, loan ownership,
ADEPT license evidence, or authorization to skip/acquire a loan.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import stat

from .library_inventory import InventoryLimits, scan_identity_roots
from .local_candidate_screen import LocalCandidateScreen, _title


# A large/inaccessible/changed library is inconclusive: refuse a new GET
# rather than claiming a complete negative scan. This is a short preflight,
# not a full, unbounded reconciliation audit.
_LIMITS = InventoryLimits(
    entries=4096, files=1024, file_bytes=512 * 1024 * 1024,
    hash_bytes=4 * 1024 * 1024 * 1024, seconds=12,
    depth=20, metadata_bytes=16 * 1024 * 1024,
)


@dataclass(frozen=True, slots=True, repr=False)
class PhysicalTitleEvidence:
    identity_id: str
    normalized_titles: frozenset[str]
    complete: bool

    def screen(self, *, identity_id: str, title: str) -> LocalCandidateScreen:
        if (identity_id != self.identity_id or not self.complete
                or not _title(title)):
            return LocalCandidateScreen('needs_reconciliation', 'local_evidence_unavailable')
        if _title(title) in self.normalized_titles:
            return LocalCandidateScreen('needs_reconciliation', 'local_candidate_found')
        # No title hint in this bounded scan is NOT proof that a file is absent.
        return LocalCandidateScreen('none_observed', 'no_physical_title_hint')


def observe_physical_local_titles(
    *, identity_id: str, roots: tuple[Path, ...],
) -> PhysicalTitleEvidence:
    """Inspect only caller-scoped directories with O_NOFOLLOW at each level.

    A missing local directory is normal (never initialized); an unreadable,
    changed or symlinked one is inconclusive. Only valid EPUB OPF titles are
    considered. No on-disk index, UI publication, HTTP request or mutation.
    """
    incomplete = PhysicalTitleEvidence(identity_id, frozenset(), False)
    if (type(identity_id) is not str or not identity_id
            or type(roots) is not tuple or not roots
            or len(roots) > 16
            or any(not isinstance(root, Path) or not root.is_absolute()
                   or root == Path('/') or '..' in root.parts for root in roots)):
        return incomplete
    selected: list[Path] = []
    for root in roots:
        try:
            mode = root.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError:
            return incomplete
        if not stat.S_ISDIR(mode):
            return incomplete
        selected.append(root)
    if not selected:
        return PhysicalTitleEvidence(identity_id, frozenset(), True)
    try:
        inventory = scan_identity_roots(
            {identity_id: tuple(selected)}, limits=_LIMITS,
        )
    except (OSError, ValueError, RuntimeError):
        return incomplete
    if inventory.reasons or inventory.unavailable_directories:
        return incomplete
    titles = set()
    for item in inventory.files:
        if item.identity_id != identity_id or item.media_format != 'epub':
            continue
        if (item.validation != 'ok'
                or item.structure_validation != 'epub_structure_crc_ok'
                or not isinstance(item.metadata, dict)):
            return incomplete
        title = _title(item.metadata.get('title'))
        if title:
            titles.add(title)
    return PhysicalTitleEvidence(identity_id, frozenset(titles), True)


__all__ = ['PhysicalTitleEvidence', 'observe_physical_local_titles']
