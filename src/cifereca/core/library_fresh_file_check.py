"""No-write, no-follow freshness check for a *previously scanned* EPUB (#2714).

This is a future transaction prerequisite, NOT an attachment or ownership
capability. The caller supplies the active identity and Core-authorized roots;
no discovery, SQLite, plugin, provider, network or mutation occurs here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from time import monotonic
from typing import Callable

from .library_inventory import FileObservation, _fingerprint, _open_root

_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_CHUNK = 1024 * 1024


@dataclass(frozen=True, repr=False)
class FreshFileCheck:
    verified: bool
    reason: str
    bytes_checked: int = 0
    read_only: bool = True
    attachment_authorized: bool = False


def _stamp(fd: int) -> tuple[int, int]:
    value = os.fstat(fd)
    return (value.st_dev, value.st_ino)


def _open_chain(root: Path, relative: Path) -> tuple[list[int], int]:
    """Open one component at a time; caller owns every returned descriptor."""
    chain: list[int] = []
    try:
        chain.append(_open_root(root))
        for piece in relative.parts[:-1]:
            chain.append(os.open(piece, _DIRECTORY, dir_fd=chain[-1]))
        leaf = os.open(relative.name, _FILE, dir_fd=chain[-1])
        return chain, leaf
    except BaseException:
        for fd in reversed(chain):
            os.close(fd)
        raise


def revalidate_observed_file(
    observation: FileObservation, roots: tuple[Path, ...], *,
    identity_id: str, cancelled: Callable[[], bool] = lambda: False,
    seconds: float = 30, max_bytes: int = 512 * 1024 * 1024,
) -> FreshFileCheck:
    """Reopen by active-root capability, hash exact old bytes, and check races.

    No result is a placement permit. Even successful validation must be
    re-run *under a future storage transaction lock* with fresh account,
    receipt, edition, placement and collision checks.
    """
    unavailable = lambda code: FreshFileCheck(False, code)
    if (type(observation) is not FileObservation
            or type(roots) is not tuple or not 1 <= len(roots) <= 128
            or type(identity_id) is not str or not identity_id
            or identity_id != observation.identity_id
            or type(seconds) not in (int, float) or not 0 < seconds <= 30
            or type(max_bytes) is not int or not 0 < max_bytes <= 512 * 1024 * 1024
            or not callable(cancelled)):
        return unavailable("invalid_scope")
    if (observation.media_format != "epub" or observation.validation != "ok"
            or observation.structure_validation != "epub_structure_crc_ok"
            or type(observation.sha256) is not str
            or len(observation.sha256) != 64
            or any(char not in "0123456789abcdef" for char in observation.sha256)
            or type(observation.size) is not int or observation.size <= 0
            or type(observation.fingerprint) is not tuple
            or len(observation.fingerprint) != 5
            or any(type(value) is not int for value in observation.fingerprint)):
        return unavailable("unverified_observation")
    if type(observation.path) is not str or not 1 <= len(observation.path) <= 4096:
        return unavailable("invalid_scope")
    path = Path(observation.path)
    if (not path.is_absolute() or ".." in path.parts
            or path.suffix.lower() != ".epub"):
        return unavailable("invalid_scope")
    permitted = []
    for item in roots:
        if (not isinstance(item, Path) or not item.is_absolute()
                or item == Path("/") or ".." in item.parts):
            return unavailable("invalid_scope")
        if path != item and path.is_relative_to(item):
            permitted.append(item)
    if len(permitted) != 1:
        return unavailable("invalid_scope")
    if observation.size > max_bytes:
        return unavailable("byte_budget")
    if cancelled():
        return unavailable("cancelled")
    root = permitted[0]
    relative = path.relative_to(root)
    deadline = monotonic() + seconds
    first_chain: list[int] = []
    second_chain: list[int] = []
    first = second = None
    total = 0
    try:
        first_chain, first = _open_chain(root, relative)
        before = os.fstat(first)
        if (not stat.S_ISREG(before.st_mode)
                or _fingerprint(before) != observation.fingerprint):
            return unavailable("file_changed")
        anchors = tuple(_stamp(fd) for fd in first_chain)
        digest = hashlib.sha256()
        while total < observation.size:
            if cancelled():
                return unavailable("cancelled")
            if monotonic() >= deadline:
                return unavailable("time_budget")
            chunk = os.read(first, min(_CHUNK, observation.size - total))
            if not chunk:
                return unavailable("file_changed")
            total += len(chunk)
            digest.update(chunk)
        if (_fingerprint(os.fstat(first)) != observation.fingerprint
                or digest.hexdigest() != observation.sha256):
            return unavailable("file_changed")
        if cancelled() or monotonic() >= deadline:
            return unavailable("cancelled" if cancelled() else "time_budget")
        # Do not trust an old opened inode after its directory was renamed.
        # Reopen every component by the *current* authorized pathname.
        second_chain, second = _open_chain(root, relative)
        if (tuple(_stamp(fd) for fd in second_chain) != anchors
                or _fingerprint(os.fstat(second)) != observation.fingerprint):
            return unavailable("file_changed")
        if cancelled():
            return unavailable("cancelled")
        return FreshFileCheck(True, "unchanged", total)
    except OSError:
        return unavailable("path_unavailable")
    finally:
        for fd in (second, first):
            if fd is not None:
                os.close(fd)
        for chain in (second_chain, first_chain):
            for fd in reversed(chain):
                os.close(fd)
