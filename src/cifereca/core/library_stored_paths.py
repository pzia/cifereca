"""No-follow, identity-scoped checks of explicit SQLite paths (#2565).

A file not present in the bounded inventory is NOT automatically missing.
This separate read-only pass can attest ENOENT under a successfully opened root;
symlinks, foreign paths, special files and permission failures remain unknown.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import errno
import os
from pathlib import Path
import stat
import time

from .library_inventory import _open_root
from .library_reconciliation import EvidenceSnapshot


@dataclass(repr=False)
class StoredPathReport:
    # Private ephemeral SQLite row indices; never expose path/identity values.
    states: dict[int, str] = field(default_factory=dict)
    reasons: set[str] = field(default_factory=set)
    # Filled by the Core audit orchestrator, absent for standalone checks.
    duration_ms: int | None = None

    def redacted(self):
        result = {'schema': 1, 'read_only': True,
                  'status': 'incomplete_scan' if self.reasons else 'complete',
                  'reasons': sorted(self.reasons),
                  'counts': dict(sorted(Counter(self.states.values()).items()))}
        if type(self.duration_ms) is int and 0 <= self.duration_ms <= 30_000:
            result['duration_ms'] = self.duration_ms
        return result


def inspect_stored_paths(snapshot: EvidenceSnapshot, roots: dict[str, tuple[Path, ...]], *,
                         cancelled=lambda: False, max_paths: int = 10_000,
                         seconds: float = 10) -> StoredPathReport:
    """Classify only placements with an explicit owning identity and root.

    Each root is reopened no-follow before accepting the outcome. A missing
    root is unavailable, not proof all its stored books are absent. A path
    failure is never permission for acquisition, repair or deleting DB rows.
    """
    if (type(max_paths) is not int or not 1 <= max_paths <= 10_000
            or type(seconds) not in (float, int) or not 0 < seconds <= 30
            or not isinstance(roots, dict) or not 1 <= len(roots) <= 128):
        raise ValueError('invalid_stored_path_options')
    selected = []
    for identity, paths in roots.items():
        if type(identity) is not str or not identity or type(paths) is not tuple or not paths:
            raise ValueError('invalid_stored_path_roots')
        for path in paths:
            if len(selected) >= 128:
                raise ValueError('invalid_stored_path_roots')
            path = Path(path)
            if not path.is_absolute() or path == Path('/') or '..' in path.parts:
                raise ValueError('invalid_stored_path_roots')
            if any(path.is_relative_to(other) or other.is_relative_to(path) for _, other in selected):
                raise ValueError('overlapping_inventory_roots')
            selected.append((identity, path))
    result = StoredPathReport(reasons=set(snapshot.reasons))
    if not all(hasattr(os, key) for key in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK')):
        result.reasons.add('nofollow_unavailable')
        return result
    deadline = time.monotonic() + seconds
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    for index, row in enumerate(snapshot.tables.get('placements', [])):
        if cancelled():
            result.reasons.add('cancelled')
            break
        if index >= max_paths or time.monotonic() >= deadline:
            result.reasons.add('stored_path_budget')
            break
        identity = row.get('identity_id')
        raw = row.get('path')
        if type(raw) is not str or not raw or '\0' in raw:
            result.states[index] = 'stored_path_unavailable'
            result.reasons.add('stored_path_unavailable')
            continue
        path = Path(raw)
        matches = [(root, path.relative_to(root)) for owner, root in selected
                   if owner == identity and path.is_absolute() and '..' not in path.parts
                   and path != root and path.is_relative_to(root)]
        if len(matches) != 1:
            result.states[index] = 'stored_path_out_of_scope'
            continue
        root, relative = matches[0]
        def probe(anchor):
            parent = os.dup(anchor)
            chain = []
            try:
                for part in relative.parts[:-1]:
                    child = os.open(part, flags, dir_fd=parent)
                    os.close(parent)
                    parent = child
                    info = os.fstat(parent)
                    chain.append((info.st_dev, info.st_ino))
                info = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
                state = 'stored_path_present' if stat.S_ISREG(info.st_mode) else 'stored_path_unavailable'
                return state, chain
            except OSError as exc:
                # ENOTDIR, ELOOP, EACCES, etc. do not attest file absence.
                state = 'stored_path_missing' if exc.errno == errno.ENOENT else 'stored_path_unavailable'
                return state, chain
            finally:
                os.close(parent)

        root_fd = -1
        state = 'stored_path_unavailable'
        try:
            root_fd = _open_root(root)
            first = probe(root_fd)
            current = _open_root(root)
            try:
                a, b = os.fstat(root_fd), os.fstat(current)
                second = probe(current)
                # A replaced ancestor, or missing component created between
                # probes, invalidates the earlier absence/presence evidence.
                if (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino) and first == second:
                    state = first[0]
            finally:
                os.close(current)
        except OSError:
            state = 'stored_path_unavailable'
        finally:
            if root_fd >= 0:
                os.close(root_fd)
        result.states[index] = state
        if state == 'stored_path_unavailable':
            result.reasons.add('stored_path_unavailable')
    return result
