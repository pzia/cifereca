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


def _open_parent_without_symlinks(directory: Path) -> int:
    """Resolve every absolute path component using dirfd + O_NOFOLLOW."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in directory.parts[1:]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY
                | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd,
            )
            os.close(fd)
            fd = child
        return fd
    except OSError:
        os.close(fd)
        raise


def save_chosen_download(download, destination: Path) -> None:
    """Explicitly publish a *complete* download with no overwrite.

    Download.save_as first writes only into an ephemeral mode-0700 directory.
    Bounded copy to a 0600 staging file is fsynced before a hardlink exposes
    the final name. Hardlink fails atomically if the target already exists;
    no partial/empty filename can masquerade as a successful download.
    """
    import secrets

    destination = Path(destination)
    name = destination.name
    if (not destination.is_absolute()
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name) is None
            or ".." in name or ".." in destination.parts):
        raise RecorderError("download_destination_invalid")
    fd = None
    staged = False
    published = False
    temp_name = "." + secrets.token_hex(16) + ".cifereca-download-part"
    try:
        fd = _open_parent_without_symlinks(destination.parent)
        folder = os.fstat(fd)
        if (not stat.S_ISDIR(folder.st_mode)
                or folder.st_uid != os.getuid()
                or stat.S_IMODE(folder.st_mode) & 0o022):
            raise RecorderError("download_destination_invalid")
        with tempfile.TemporaryDirectory(
            prefix="cifereca-explicit-download-"
        ) as staging:
            temporary = Path(staging) / "private-transfer"
            download.save_as(str(temporary))
            if temporary.stat().st_size > MAX_MANUAL_DOWNLOAD_BYTES:
                raise RecorderError("download_limit")
            handle = os.open(
                temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=fd,
            )
            staged = True
            with os.fdopen(handle, "wb") as output, temporary.open("rb") as source:
                total = 0
                while True:
                    data = source.read(64 * 1024)
                    if not data:
                        break
                    total += len(data)
                    if total > MAX_MANUAL_DOWNLOAD_BYTES:
                        raise RecorderError("download_limit")
                    output.write(data)
                output.flush()
                os.fsync(output.fileno())
        os.link(temp_name, name, src_dir_fd=fd, dst_dir_fd=fd,
                follow_symlinks=False)
        published = True
        os.unlink(temp_name, dir_fd=fd)
        staged = False
        os.fsync(fd)
    except RecorderError:
        if published:
            try:
                os.unlink(name, dir_fd=fd)
            except OSError:
                pass
        raise
    except Exception:
        if published:
            try:
                os.unlink(name, dir_fd=fd)
            except OSError:
                pass
        raise RecorderError("download_save_failed") from None
    finally:
        if fd is not None:
            if staged:
                try:
                    os.unlink(temp_name, dir_fd=fd)
                except OSError:
                    pass
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
