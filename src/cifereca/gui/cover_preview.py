"""Worker-only, bounded image previews; original cache files are preserved."""
import hashlib
import os
from pathlib import Path
import stat
import tempfile

MAX_PIXELS = 50_000_000
MAX_BYTES = 24*1024*1024
SCHEMA = 'preview-v2'


def _fingerprint(source):
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('preview_source_not_regular')
    return (source.resolve(), info.st_dev, info.st_ino, info.st_ctime_ns,
            info.st_mtime_ns, info.st_size)


def _destination(fingerprint, root, width, height):
    key = f'{fingerprint!r}:{width}:{height}:{SCHEMA}'
    return root / (hashlib.sha256(key.encode()).hexdigest() + '.png')


def preview_path(source, root, width, height):
    # A replacement can preserve size and mtime; it must not reuse the old
    # derivative. The fingerprint is physical cache state, not book identity.
    return _destination(_fingerprint(source), root, width, height)


def create_preview(source, dest, width, height, backend=None):
    if not 1 <= width <= 2048 or not 1 <= height <= 2048:
        raise ValueError('preview_dimensions')
    initial = _fingerprint(source)
    if initial[-1] > MAX_BYTES:
        raise ValueError('preview_bytes_limit')
    # The loader has already computed the destination. If the cache source
    # changed since then, never publish a new image under its old key.
    if dest != _destination(initial, dest.parent, width, height):
        raise ValueError('preview_source_changed')
    if backend is None:
        import gi
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf
        backend = GdkPixbuf.Pixbuf
    info = backend.get_file_info(str(source))
    if not info or not info[0] or not 0 < info[1]*info[2] <= MAX_PIXELS:
        raise ValueError('preview_pixels_limit')
    if _fingerprint(source) != initial:
        raise ValueError('preview_source_changed')
    # Never enlarge an original; caller requests 3x logical dimensions for HiDPI.
    pixbuf = backend.new_from_file_at_scale(
        str(source), min(width, info[1]), min(height, info[2]), True,
    )
    if pixbuf is None:
        raise ValueError('preview_decode_failed')
    # O_EXCL on the same physical directory avoids publishing through a
    # pre-created symlink/FIFO at a guessed worker temp name. The backend
    # requires a pathname; the exclusive leaf remains owned until replace.
    fd, name = tempfile.mkstemp(prefix=dest.stem + ".", suffix=".tmp", dir=dest.parent)
    os.close(fd)
    temp = Path(name)
    try:
        if pixbuf.savev(str(temp), 'png', [], []) is False:
            raise ValueError('preview_save_failed')
        if _fingerprint(source) != initial:
            raise ValueError('preview_source_changed')
        temp.replace(dest)
    finally:
        temp.unlink(missing_ok=True)
    return dest
