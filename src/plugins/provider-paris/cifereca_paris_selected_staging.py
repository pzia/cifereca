"""Private staging for one Core-selected Paris PNB file candidate (#3723).

No acquisition consent, receipt, ADEPT or canonical publication lives here.
The caller holds this directory fd across its authenticated PNB GET.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

from cifereca.plugin_api import ProviderCapabilityError


def save_selected_candidate(target_dir: Path, stage_fd: int,
                            filename: str, content: bytes) -> Path:
    target = Path(target_dir)
    if (type(stage_fd) is not int or type(filename) is not str
            or not filename or filename in (".", "..")
            or Path(filename).name != filename or type(content) is not bytes):
        raise ProviderCapabilityError("paris_candidate_staging_invalid")

    def stage_current() -> bool:
        try:
            pinned = os.fstat(stage_fd)
            current = os.stat(target, follow_symlinks=False)
            return ((current.st_dev, current.st_ino)
                    == (pinned.st_dev, pinned.st_ino))
        except OSError:
            return False

    if not stage_current():
        raise ProviderCapabilityError("paris_candidate_staging_rebound")
    # The reference is never interpreted as a new output permission: Core
    # has pre-created the attempt directory, and this helper can only write
    # into that inode. It cannot replace an existing candidate file.
    temp = None
    linked = False
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=".paris-selected-", suffix=".part",
            dir=Path("/proc/self/fd") / str(stage_fd),
        )
        temp = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if not stage_current():
            raise ProviderCapabilityError("paris_candidate_staging_rebound")
        os.link(temp.name, filename, src_dir_fd=stage_fd,
                dst_dir_fd=stage_fd, follow_symlinks=False)
        linked = True
        if not stage_current():
            raise ProviderCapabilityError("paris_candidate_staging_rebound")
        return target / filename
    except (OSError, ValueError):
        raise ProviderCapabilityError("paris_candidate_staging_occupied") from None
    finally:
        if linked and not stage_current():
            try:
                os.unlink(filename, dir_fd=stage_fd)
            except FileNotFoundError:
                pass
        if temp is not None:
            temp.unlink(missing_ok=True)
