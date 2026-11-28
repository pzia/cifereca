"""Bounded local source snapshot of one installed provider package.

Never import or execute a package. Only Python source and the package
manifest are eligible; no configuration, credentials, caches or log files.
"""
import os
import stat
from pathlib import Path
from hashlib import sha256

_MAX_BYTES = 2_000_000
_MAX_FILES = 128
_MAX_ENTRIES = 4096


def snapshot_provider_source(manager, provider_id):
    """Return a current installed source snapshot, or None if unavailable."""
    if type(provider_id) is not str or not provider_id:
        return None
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY")):
        return None
    try:
        with manager.hold_descriptive_lifecycle():
            record = manager._records.get(provider_id)
            if (record is None or record.descriptor.id != provider_id
                    or record.descriptor.type != "provider.library"):
                return None
            package = record.package
            root = Path(os.path.abspath(package.root))
            files = {}
            total = 0
            entries = 0

            def walk(fd, prefix, depth):
                nonlocal total, entries
                if depth > 12:
                    raise ValueError
                for name in sorted(os.listdir(fd)):
                    entries += 1
                    if entries > _MAX_ENTRIES:
                        raise ValueError
                    if name.startswith(".") or name in {"tests", "__pycache__", "fixtures", "venv", "data", "config", "secrets"}:
                        continue
                    if not name.isascii() or not all(c.isalnum() or c in "._-" for c in name):
                        continue
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    rel = prefix + name
                    if stat.S_ISDIR(info.st_mode):
                        sub = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                        try:
                            walk(sub, rel + "/", depth + 1)
                        finally:
                            os.close(sub)
                    elif name.endswith(".py") or (not prefix and name == "cifereca-plugin.json"):
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise ValueError
                        if info.st_size > _MAX_BYTES - total or len(files) >= _MAX_FILES:
                            raise ValueError
                        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                        with os.fdopen(handle, "rb") as stream:
                            before = os.fstat(stream.fileno())
                            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                                    or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)):
                                raise ValueError
                            data = stream.read(_MAX_BYTES - total + 1)
                            after = os.fstat(stream.fileno())
                            if (before.st_size != len(data) or before.st_mtime_ns != after.st_mtime_ns
                                    or before.st_size != after.st_size):
                                raise ValueError
                        if len(data) > _MAX_BYTES - total or b"\x00" in data:
                            raise ValueError
                        total += len(data)
                        files[rel] = data
            fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for part in root.parts[1:]:
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd)
                    fd = nxt
                walk(fd, "", 0)
            finally:
                os.close(fd)
            if "cifereca-plugin.json" not in files or not any(name.endswith(".py") for name in files):
                return None
            return {"provider_id": provider_id, "version": record.descriptor.version,
                    "package_version": package.manifest.package.version,
                    "package_archive_sha256": package.sha256,
                    "files": files}
    except (OSError, ValueError, AttributeError, TypeError):
        return None
