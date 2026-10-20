"""Bounded descriptor-only structural validation, reusing Core EPUB rules.

No cover extraction, DRM operation, file reopen, cache, network or mutation.
EPUB result is structural + member CRC, not an EPUBCheck accessibility/layout
certification. PDF result is explicitly minimal signature/EOF, not conformance.
"""
from __future__ import annotations

import time
import zipfile
import zlib
from xml.etree import ElementTree

from ..artifact_inspection import _validate_epub_archive
from ..epub_metadata import _bounded_directory, _xml


class _Limit(Exception):
    pass


class _GuardedArchive:
    """Adapter for existing strict validation with a global read budget."""
    def __init__(self, archive, max_bytes, deadline, cancelled, count_bytes):
        self.archive = archive
        self.remaining = max_bytes
        self.deadline = deadline
        self.cancelled = cancelled
        self.count_bytes = count_bytes

    def check(self):
        if self.cancelled() or time.monotonic() >= self.deadline:
            raise _Limit()

    def infolist(self):
        return self.archive.infolist()

    def getinfo(self, name):
        return self.archive.getinfo(name)

    def read(self, name):
        self.check()
        info = self.archive.getinfo(name)
        if info.file_size > self.remaining:
            raise _Limit()
        with self.archive.open(info) as stream:
            data = stream.read(min(self.remaining, info.file_size)+1)
        self.remaining -= len(data)
        self.count_bytes(len(data))
        if self.remaining < 0:
            raise _Limit()
        self.check()
        # The strict Core helper parses only container/package XML. Reject
        # DTDs/encoded entities before its historical ElementTree parser.
        if name != 'mimetype':
            _xml(data)
        return data

    def testzip(self):
        # Same CRC validation as ZipFile.testzip, interruptible per chunk and
        # across a global byte budget. No exception embeds a private member.
        for info in self.archive.infolist():
            self.check()
            if info.file_size > self.remaining:
                raise _Limit()
            if info.file_size == 0 and info.CRC != 0:
                raise zipfile.BadZipFile()
            with self.archive.open(info) as stream:
                left = info.file_size
                while left:
                    self.check()
                    data = stream.read(min(1024*1024, left))
                    if not data:
                        raise zipfile.BadZipFile()
                    self.remaining -= len(data)
                    left -= len(data)
                    self.count_bytes(len(data))
        return None


def inspect_stream_structure(stream, media_format: str, *, max_bytes: int = 1024*1024*1024,
                             seconds: float = 10, cancelled=lambda: False, count_bytes=lambda n: None) -> str:
    if (type(max_bytes) is not int or not 1 <= max_bytes <= 1024*1024*1024
            or type(seconds) not in (int, float) or not 0 < seconds <= 30):
        raise ValueError('invalid_structure_limits')
    deadline = time.monotonic()+seconds
    try:
        if cancelled():
            return 'structure_cancelled'
        stream.seek(0)
        if media_format == 'pdf':
            if max_bytes < 2056:
                return 'structure_byte_or_entry_budget'
            head = stream.read(8)
            count_bytes(len(head))
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size-2048))
            tail = stream.read(2048)
            count_bytes(len(tail))
            return 'pdf_structure_minimal' if head.startswith(b'%PDF-') and b'%%EOF' in tail else 'pdf_structure_invalid'
        if media_format != 'epub':
            return 'structure_unsupported'
        _bounded_directory(stream)
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            if len(infos) > 10000 or sum(info.file_size for info in infos) > max_bytes:
                return 'structure_byte_or_entry_budget'
            for info in infos:
                if (info.file_size > 512*1024*1024 or info.file_size/max(info.compress_size,1) > 1000
                        or (info.external_attr >> 16) & 0o170000 == 0o120000):
                    return 'structure_unsafe_member'
            guarded = _GuardedArchive(archive, max_bytes, deadline, cancelled, count_bytes)
            reason = _validate_epub_archive(guarded)
            return 'epub_structure_invalid' if reason else 'epub_structure_crc_ok'
    except _Limit:
        return 'structure_cancelled' if cancelled() else 'structure_byte_or_time_budget'
    except (OSError, ValueError, ElementTree.ParseError, zipfile.BadZipFile, RuntimeError, EOFError, zlib.error):
        return 'structure_invalid'
