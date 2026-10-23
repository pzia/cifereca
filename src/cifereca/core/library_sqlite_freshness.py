"""Private, read-only freshness guard for a long offline reconciliation scan.

The SQLite image reader already verifies coherent bytes when it captures them.
This guard only checks whether the *source* DB or committed WAL/journal changed
between that capture and the end of a potentially 600-second filesystem scan.
It does not claim to establish a current entitlement or a right to a file.

Return values are strictly PRIVATE process-local stat tuples. Never publish,
log, serialize or expose them via a broker. No SQLite connection is opened,
and only directory file descriptors (never the DB file) are opened.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

from .library_inventory import _fingerprint, _open_root


def sqlite_source_stamp(path: Path) -> tuple | None:
    """Fingerprint source database and write-bearing sidecars, fail closed.

    The SHM is intentionally excluded: concurrent *readers* legitimately
    update its lock/index bytes without committing new database evidence.
    Every directory ancestor is opened O_NOFOLLOW; leaf and WAL/journal stat
    operations also refuse symlinks and nonregular filesystem nodes.
    """
    if type(path) is not Path or not path.is_absolute() or '..' in path.parts:
        return None
    directory = check = -1
    try:
        directory = _open_root(path.parent)
        anchored = os.fstat(directory)
        signatures = []
        for suffix in ('', '-wal', '-journal'):
            try:
                source = os.stat(
                    path.name + suffix, dir_fd=directory, follow_symlinks=False,
                )
            except FileNotFoundError:
                if not suffix:
                    return None
                signatures.append(None)
                continue
            if not stat.S_ISREG(source.st_mode):
                return None
            signatures.append(_fingerprint(source))
        check = _open_root(path.parent)
        final_dir = os.fstat(check)
        if ((anchored.st_dev, anchored.st_ino)
                != (final_dir.st_dev, final_dir.st_ino)):
            return None
        return ((anchored.st_dev, anchored.st_ino), *signatures)
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    finally:
        if check >= 0:
            os.close(check)
        if directory >= 0:
            os.close(directory)
