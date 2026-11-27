"""Core-only installation key for the encrypted capture vault.

The application's existing local configuration trust boundary applies: this
protects the capture directory in isolation, not a compromised same-UID process
or a backup containing both the configuration and the captures. No key is
exposed by the public broker, no separate user password or unlock is required.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import secrets
import stat

from .capture_vault import VaultError


class CaptureKey:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def load(self, *, create: bool = False) -> bytes:
        fd = None
        try:
            if create:
                self.directory.mkdir(mode=0o700, parents=False, exist_ok=True)
            fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise VaultError("vault_key_unavailable")
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                handle = os.open("key", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=fd)
            except FileNotFoundError:
                if create is not True:
                    raise VaultError("vault_key_unavailable") from None
                return self._create(fd)
            with os.fdopen(handle, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_size != 32):
                    raise VaultError("vault_key_unavailable")
                result = stream.read(33)
                if len(result) != 32:
                    raise VaultError("vault_key_unavailable")
                return result
        except OSError as exc:
            raise VaultError("vault_key_unavailable") from exc
        finally:
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _create(fd: int) -> bytes:
        value = secrets.token_bytes(32)
        name = ".key-" + secrets.token_hex(8)
        handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=fd)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            # Publish exclusively; never replace an existing key.
            os.link(name, "key", src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        finally:
            os.unlink(name, dir_fd=fd)
        os.fsync(fd)
        return value
