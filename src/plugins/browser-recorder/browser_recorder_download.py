"""Manual-only Browser Recorder downloads, separate from HTTP traces and ZIPs.

A Playwright Download is *never* persisted by this module until a human
chooses an absolute destination using the native GTK Save-As dialog.
No access to the download's URL or suggested_filename is needed.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import tempfile

from browser_recorder import RecorderError

MAX_MANUAL_DOWNLOAD_BYTES = 128 * 1024 * 1024
MAX_MANUAL_DOWNLOADS = 8


def save_chosen_download(download, destination: Path) -> None:
    """Explicit save via exclusive 0600 destination and private staging.

    The output directory must be owned by the operator and not group/world
    writable. Never overwrite, follow symlinks, create directories, log
    paths, or place downloaded content in the recorder's archival vault.
    """
    destination = Path(destination)
    name = destination.name
    if (not destination.is_absolute()
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name) is None
            or ".." in name):
        raise RecorderError("download_destination_invalid")
    fd = None
    created = False
    try:
        fd = os.open(
            destination.parent, os.O_RDONLY | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0),
        )
        folder = os.fstat(fd)
        if (not stat.S_ISDIR(folder.st_mode)
                or folder.st_uid != os.getuid()
                or stat.S_IMODE(folder.st_mode) & 0o022):
            raise RecorderError("download_destination_invalid")
        flags = (os.O_CREAT | os.O_EXCL | os.O_WRONLY
                 | getattr(os, "O_NOFOLLOW", 0))
        handle = os.open(name, flags, 0o600, dir_fd=fd)
        created = True
        try:
            with tempfile.TemporaryDirectory(
                prefix="cifereca-explicit-download-"
            ) as staging:
                temporary = Path(staging) / "private-transfer"
                # Download.save_as waits until the *browser* finishes writing
                # its own temporary file. There is no HTTP retry/replay.
                download.save_as(str(temporary))
                size = 0
                with os.fdopen(handle, "wb") as output, temporary.open("rb") as source:
                    handle = None
                    while True:
                        data = source.read(64 * 1024)
                        if not data:
                            break
                        size += len(data)
                        if size > MAX_MANUAL_DOWNLOAD_BYTES:
                            raise RecorderError("download_limit")
                        output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
            os.fsync(fd)
        finally:
            if handle is not None:
                os.close(handle)
    except RecorderError:
        if created:
            try:
                os.unlink(name, dir_fd=fd)
            except OSError:
                pass
        raise
    except Exception:
        if created:
            try:
                os.unlink(name, dir_fd=fd)
            except OSError:
                pass
        raise RecorderError("download_save_failed") from None
    finally:
        if fd is not None:
            os.close(fd)


_BLOCKED_MIME = frozenset({
    "application/vnd.adobe.adept+xml",
    "application/vnd.adobe.adept+zip",
    "application/epub+zip",
    "application/pdf",
    "application/octet-stream",
    "application/x-mobipocket-ebook",
})


def is_download_response(response) -> bool:
    """Observe MIME/disposition transiently; NEVER retain raw values."""
    try:
        headers = response.headers
        if type(headers) is not dict:
            return True
        disposition = headers.get("content-disposition", "")
        if (type(disposition) is str
                and disposition.partition(";")[0].strip().lower()
                == "attachment"):
            return True
        mime = headers.get("content-type", "")
        return type(mime) is str and mime.partition(";")[0].strip().lower() in _BLOCKED_MIME
    except (AttributeError, TypeError, ValueError):
        return True
