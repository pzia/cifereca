"""#6937 optional, bounded PDF reader evidence on the already pinned descriptor.

No plugin import, new runtime dependency, document path, mutation or reader
claim when the optional installed PyMuPDF is absent. A subprocess protects the
Core from unbounded native decoding time/memory; it is not a security sandbox.
"""
import os
import stat
import subprocess
import sys
from time import monotonic

_MAX_BYTES = 64 * 1024 * 1024
_SECONDS = 3
_WORKER = r"""
import math
import os
import resource
import sys

resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
import pymupdf

fd, size = map(int, sys.argv[1:])
data = bytearray()
while len(data) < size:
    block = os.pread(fd, min(1024 * 1024, size - len(data)), len(data))
    if not block:
        sys.exit(1)
    data.extend(block)
if os.pread(fd, 1, size):
    sys.exit(1)
with pymupdf.open(stream=bytes(data), filetype="pdf") as doc:
    del data
    if (not doc.is_pdf or doc.is_repaired or doc.needs_pass or doc.is_encrypted
            or (doc.metadata or {}).get("encryption")
            or doc.get_sigflags() > 0 or not 1 <= doc.page_count <= 128):
        sys.exit(1)
    for page in doc:
        rect = page.rect
        if not all(math.isfinite(n) and 0 < n <= 20000
                   for n in (rect.width, rect.height)):
            sys.exit(1)
        scale = min(1.0, 128 / max(rect.width, rect.height))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                              colorspace=pymupdf.csGRAY, alpha=False, annots=False)
        if not 0 < pix.width <= 130 or not 0 < pix.height <= 130:
            sys.exit(1)
sys.exit(0)
"""


def pdf_reader_observed(fd, size, *, still_current, deadline=None):
    """Return only a revocable local readability observation, never authority."""
    process = None
    try:
        end = min(deadline, monotonic() + _SECONDS) if deadline is not None else monotonic() + _SECONDS

        def live():
            return still_current() is True and monotonic() < end

        def stamp():
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("reader_descriptor")
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

        if type(size) is not int or not 0 < size <= _MAX_BYTES or not live():
            return False
        before = stamp()
        if before[2] != size or not os.pread(fd, 8, 0).startswith(b"%PDF-"):
            return False
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", _WORKER, str(fd), str(size)],
            pass_fds=(fd,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True,
        )
        while live():
            try:
                status = process.wait(timeout=min(0.05, max(0.001, end - monotonic())))
                return status == 0 and live() and stamp() == before
            except subprocess.TimeoutExpired:
                continue
        return False
    except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError):
        return False
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
