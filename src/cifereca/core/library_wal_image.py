"""Isolated Linux WAL capture and native in-memory SQLite replay (#2565).

Never open source files from the application process: closing any descriptor
of a file may release that process's POSIX SQLite locks. Only the child opens
the existing DB/WAL/SHM; it never opens an SQLite connection to source paths.
SHM read0 blocks checkpoints, read1 blocks resets, DMS keeps the index alive.
Writers may append; concurrent changes make the capture unavailable.

Only the private byte protocol crosses stdout. Errors are closed codes;
neither paths, SQLite prose nor image bytes belong in logs or diagnostics.
"""
from __future__ import annotations

import os
from pathlib import Path
import selectors
import stat
import struct
import subprocess
import sys
import time
import uuid

from .library_inventory import _fingerprint, _open_root

_ERRORS = frozenset({'snapshot_unavailable', 'snapshot_changed',
                     'snapshot_byte_budget_or_invalid', 'snapshot_time_budget',
                     'snapshot_cancelled', 'snapshot_wal_reader_unavailable'})


class _Unavailable(Exception):
    pass


def _check(deadline):
    if time.monotonic() >= deadline:
        raise _Unavailable('snapshot_time_budget')


def _checksum(data, endian, prior=(0, 0)):
    a, b = prior
    for x, y in struct.iter_unpack(endian+'II', data):
        a = (a+x+b) & 0xffffffff
        b = (b+y+a) & 0xffffffff
    return a, b


def _capture(path, max_bytes, deadline):
    if __name__ != '__main__':
        raise _Unavailable('snapshot_wal_reader_unavailable')
    import fcntl
    parent = -1
    descriptors = []
    try:
        parent = _open_root(path.parent)
        anchor = os.fstat(parent)
        try:
            os.stat(path.name+'-journal', dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise _Unavailable('snapshot_unavailable')
        images = {}
        infos = {}
        for suffix in ('', '-wal', '-shm'):
            fd = os.open(path.name+suffix, os.O_RDONLY | os.O_NOFOLLOW |
                         os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
            descriptors.append(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise _Unavailable('snapshot_unavailable')
            infos[suffix] = info
        db, wal, shm = descriptors
        if infos['-shm'].st_size < 136:
            raise _Unavailable('snapshot_unavailable')
        # Unix SQLite DMS (128) and WAL read0/read1 (123,124). Nonblocking,
        # shared locks only; no writes to SHM, no source SQLite handle.
        fcntl.lockf(shm, fcntl.LOCK_SH | fcntl.LOCK_NB, 1, 128, os.SEEK_SET)
        fcntl.lockf(shm, fcntl.LOCK_SH | fcntl.LOCK_NB, 2, 123, os.SEEK_SET)
        header = os.pread(shm, 96, 0)
        if (len(header) != 96 or header[:48] != header[48:]
                or struct.unpack('=I', header[:4])[0] != 3007000
                or header[12] != 1
                or _checksum(header[:40], '=') != struct.unpack('=II', header[40:48])):
            raise _Unavailable('snapshot_changed')
        frames, pages = struct.unpack('=II', header[16:24])
        total = infos[''].st_size + infos['-wal'].st_size
        if not 100 <= infos[''].st_size or total > max_bytes:
            raise _Unavailable('snapshot_byte_budget_or_invalid')
        for suffix, fd in (('', db), ('-wal', wal)):
            remaining = infos[suffix].st_size
            chunks = []
            while remaining:
                _check(deadline)
                chunk = os.read(fd, min(1024*1024, remaining))
                if not chunk:
                    raise _Unavailable('snapshot_changed')
                chunks.append(chunk)
                remaining -= len(chunk)
            images[suffix] = b''.join(chunks)
        if os.pread(shm, 96, 0) != header:
            raise _Unavailable('snapshot_changed')
        for suffix, fd in zip(('', '-wal', '-shm'), descriptors):
            # SHM stat timestamps may change from unrelated read marks; its
            # identity and duplicated published commit header are what matter.
            current = os.stat(path.name+suffix, dir_fd=parent, follow_symlinks=False)
            if suffix == '-shm':
                prior = infos[suffix]
                if (prior.st_dev, prior.st_ino) != (current.st_dev, current.st_ino):
                    raise _Unavailable('snapshot_changed')
            elif (_fingerprint(infos[suffix]) != _fingerprint(os.fstat(fd))
                  or _fingerprint(infos[suffix]) != _fingerprint(current)):
                raise _Unavailable('snapshot_changed')
        current_parent = _open_root(path.parent)
        try:
            current = os.fstat(current_parent)
            if (anchor.st_dev, anchor.st_ino) != (current.st_dev, current.st_ino):
                raise _Unavailable('snapshot_changed')
        finally:
            os.close(current_parent)
        try:
            os.stat(path.name+'-journal', dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise _Unavailable('snapshot_changed')
        image, log = images[''], images['-wal']
        if image[:16] != b'SQLite format 3\0' or image[18:20] != b'\x02\x02':
            raise _Unavailable('snapshot_unavailable')
        if not frames:
            return image, b''
        if len(log) < 32:
            raise _Unavailable('snapshot_unavailable')
        page_size = int.from_bytes(log[8:12], 'big')
        if page_size < 512 or page_size > 65536 or page_size & (page_size-1):
            raise _Unavailable('snapshot_unavailable')
        end = 32 + frames*(24+page_size)
        if (end > len(log) or log[16:24] != header[32:40]
                or int.from_bytes(log[end-page_size-20:end-page_size-16], 'big') != pages
                or struct.unpack('>II', log[end-page_size-8:end-page_size]) != struct.unpack('=II', header[24:32])):
            raise _Unavailable('snapshot_changed')
        # Exclude frames not yet published by the wal-index commit header.
        return image, log[:end]
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
        if parent >= 0:
            os.close(parent)


def _validate_wal(log, deadline):
    """Verify committed prefix; SQLite recovery must not silently drop a tail.

    Native SQLite still does ALL page replay. This check only rejects a
    corrupt committed transaction rather than reporting older rights as current.
    """
    if not log:
        return
    magic = int.from_bytes(log[:4], 'big')
    if magic not in (0x377f0682, 0x377f0683) or log[4:8] != (3007000).to_bytes(4, 'big'):
        raise _Unavailable('snapshot_unavailable')
    endian = '<' if magic == 0x377f0682 else '>'
    value = _checksum(log[:24], endian)
    if value != struct.unpack('>II', log[24:32]):
        raise _Unavailable('snapshot_unavailable')
    size = int.from_bytes(log[8:12], 'big')
    for offset in range(32, len(log), size+24):
        _check(deadline)
        frame = log[offset:offset+24+size]
        if (len(frame) != size+24 or frame[8:16] != log[16:24]
                or not int.from_bytes(frame[:4], 'big')):
            raise _Unavailable('snapshot_unavailable')
        value = _checksum(frame[24:], endian, _checksum(frame[:8], endian, value))
        if value != struct.unpack('>II', frame[16:24]):
            raise _Unavailable('snapshot_unavailable')


def _replay(image, log, max_bytes, deadline):
    import apsw
    class File:
        def __init__(self, data): self.data = data
        def xRead(self, amount, offset): return self.data[offset:offset+amount]
        def xFileSize(self): return len(self.data)
        def xClose(self): pass
        def xLock(self, level): pass
        def xUnlock(self, level): pass
        def xCheckReservedLock(self): return False
        def xFileControl(self, op, ptr): return False
        def xSectorSize(self): return 4096
        def xDeviceCharacteristics(self): return 0
        def xWrite(self, *args): raise apsw.ReadOnlyError()
        def xTruncate(self, *args): raise apsw.ReadOnlyError()
        def xSync(self, *args): raise apsw.ReadOnlyError()
    class MemoryVFS(apsw.VFS):
        def xFullPathname(self, name): return name
        def xAccess(self, name, flags):
            return name in images and flags != apsw.SQLITE_ACCESS_READWRITE
        def xOpen(self, name, flags):
            name = name.filename() if isinstance(name, apsw.URIFilename) else name
            if name not in images:
                raise apsw.CantOpenError()
            flags[1] = apsw.SQLITE_OPEN_READONLY
            return File(images[name])
        def xDelete(self, *args): raise apsw.ReadOnlyError()
    images = {'/snapshot.sqlite': image}
    if log:
        images['/snapshot.sqlite-wal'] = log
    name = 'audit-memory-'+uuid.uuid4().hex
    vfs = MemoryVFS(name, '', makedefault=False)
    db = None
    try:
        db = apsw.Connection('/snapshot.sqlite', flags=apsw.SQLITE_OPEN_READONLY, vfs=name)
        # EXCLUSIVE applies ONLY to frozen private byte copies. It lets native
        # SQLite rebuild its wal-index in heap memory without an SHM VFS.
        db.execute('PRAGMA locking_mode=EXCLUSIVE')
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA cache_size=-8192')
        db.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        pages = db.pragma('page_count')
        size = db.pragma('page_size')
        if pages*size > max_bytes:
            raise _Unavailable('snapshot_byte_budget_or_invalid')
        _check(deadline)
        result = db.serialize('main')
        _check(deadline)
        return result
    finally:
        if db is not None:
            db.close()
        vfs.unregister()


def read_wal_image(path, *, max_bytes, seconds, cancelled=lambda: False):
    """Return (private bytes or None, closed reason or None), no source opens."""
    if sys.platform != 'linux' or getattr(sys, 'frozen', False):
        return None, 'snapshot_wal_reader_unavailable'
    if max_bytes < 100:
        return None, 'snapshot_byte_budget_or_invalid'
    deadline = time.monotonic()+seconds
    if cancelled():
        return None, 'snapshot_cancelled'
    child = subprocess.Popen(
        [sys.executable, '-B', '-m', __name__, str(path), str(max_bytes), str(seconds)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    payload = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while True:
                if cancelled():
                    return None, 'snapshot_cancelled'
                if time.monotonic() >= deadline:
                    return None, 'snapshot_time_budget'
                if not selector.select(min(0.05, max(0, deadline-time.monotonic()))):
                    continue
                chunk = os.read(child.stdout.fileno(), 65536)
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > max_bytes+128:
                    return None, 'snapshot_byte_budget_or_invalid'
        child.wait(timeout=max(0.001, deadline-time.monotonic()))
        if child.returncode == 0 and payload[:1] == b'O' and 101 <= len(payload) <= max_bytes+1:
            return bytes(payload[1:]), None
        reason = bytes(payload[1:]).decode('ascii', errors='replace') if payload[:1] == b'E' else ''
        return None, reason if reason in _ERRORS else 'snapshot_unavailable'
    except (OSError, subprocess.TimeoutExpired):
        return None, 'snapshot_unavailable'
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
        child.stdout.close()


def _main():
    try:
        if sys.platform != 'linux' or getattr(sys, 'frozen', False):
            raise _Unavailable('snapshot_wal_reader_unavailable')
        path, max_bytes, seconds = Path(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
        if (not path.is_absolute() or '..' in path.parts
                or not 100 <= max_bytes <= 128*1024*1024 or not 0 < seconds <= 30):
            raise _Unavailable('snapshot_unavailable')
        # Import before source locks; missing dependency fails closed.
        import apsw  # noqa: F401
        deadline = time.monotonic()+seconds
        image, log = _capture(path, max_bytes, deadline)
        # All source locks/descriptors are now released, before CPU replay.
        _validate_wal(log, deadline)
        image = _replay(image, log, max_bytes, deadline)
        sys.stdout.buffer.write(b'O'+image)
    except ImportError:
        sys.stdout.buffer.write(b'Esnapshot_wal_reader_unavailable')
    except _Unavailable as exc:
        reason = str(exc)
        sys.stdout.buffer.write(b'E'+(reason if reason in _ERRORS else 'snapshot_unavailable').encode())
    except Exception:
        sys.stdout.buffer.write(b'Esnapshot_unavailable')


if __name__ == '__main__':
    _main()
