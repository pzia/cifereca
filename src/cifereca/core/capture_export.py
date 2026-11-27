"""Bounded local ZIP export shared by technical capture consumers."""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import stat
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from .capture_vault import VaultError


def build_capture_zip(members: dict[str, bytes], *, max_bytes: int) -> bytes:
    """Serialize a bounded set of relative members without filesystem reads."""
    if type(max_bytes) is not int or max_bytes <= 0 or type(members) is not dict:
        raise VaultError("private_zip_invalid")
    if len(members) > 4096:
        raise VaultError("private_zip_limit")
    for name, body in members.items():
        if (type(name) is not str or not name or len(name) > 512
                or "\\" in name or "\x00" in name
                or any(part in ("", ".", "..") for part in name.split("/"))
                or PurePosixPath(name).is_absolute() or ":" in name
                or type(body) is not bytes):
            raise VaultError("private_zip_invalid")
    if sum(map(len, members.values())) > max_bytes * 4:
        raise VaultError("private_zip_limit")
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, body in sorted(members.items()):
            member = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.compress_type = ZIP_DEFLATED
            member.external_attr = 0o600 << 16
            archive.writestr(member, body)
    data = output.getvalue()
    if len(data) > max_bytes:
        raise VaultError("private_zip_limit")
    return data



def write_capture_zip(destination: Path, payload: bytes, *, confirmed=False,
                      max_bytes: int) -> int:
    """Exclusive private local write, only after explicit confirmation."""
    if confirmed is not True:
        raise VaultError("private_zip_confirmation_required")
    destination = Path(destination)
    if not destination.is_absolute() or destination.suffix.lower() != ".zip":
        raise VaultError("private_zip_destination")
    if type(payload) is not bytes or len(payload) > max_bytes:
        raise VaultError("private_zip_limit")
    folder_fd = None
    try:
        folder_fd = os.open(
            destination.parent, os.O_RDONLY | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0),
        )
        info = os.fstat(folder_fd)
        if not stat.S_ISDIR(info.st_mode):
            raise VaultError("private_zip_destination")
        fd = os.open(
            destination.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=folder_fd,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(folder_fd)
        except Exception:
            try:
                os.unlink(destination.name, dir_fd=folder_fd)
            except OSError:
                pass
            raise
        return len(payload)
    except VaultError:
        raise
    except FileExistsError as exc:
        raise VaultError("private_zip_exists") from exc
    except OSError as exc:
        # No path or OS exception text may appear in the UI/diagnostics.
        raise VaultError("private_zip_write_failed") from exc
    finally:
        if folder_fd is not None:
            os.close(folder_fd)

