"""#4030: read documentary title and creators from the verified EPUB inode.

No cover, content page, cache, network, decryption or origin assertion.
This conservative port accepts a single unambiguous OPF package.
"""
from __future__ import annotations

import os
from pathlib import Path
import zipfile
import zlib
from xml.etree import ElementTree

from ..epub_metadata import MAX_XML, _bounded_directory, _read, _xml
from .derived_variant_publication import _file_signature
from .library_inventory import _open_root
from .public_search_projection import _public_bibliographic_text


def read_epub_description(stream):
    """Pure bounded metadata read; restore caller position, do not close."""
    ocf = "{urn:oasis:names:tc:opendocument:xmlns:container}"
    opf = "{http://www.idpf.org/2007/opf}"
    dc = "{http://purl.org/dc/elements/1.1/}"
    position = None
    try:
        position = stream.tell()
        _bounded_directory(stream)
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            if len({item.filename for item in infos}) != len(infos):
                return None
            if _read(archive, "mimetype", 100).strip() != b"application/epub+zip":
                return None
            container = _xml(_read(archive, "META-INF/container.xml", MAX_XML))
            roots = list(container.iter(ocf + "rootfile"))
            if (container.tag != ocf + "container" or len(roots) != 1
                    or container.findall(ocf + "rootfiles/" + ocf + "rootfile") != roots):
                return None
            member = roots[0].get("full-path", "")
            if (not member or member.startswith("/")
                    or any(ch in member for ch in (chr(92), chr(0), ":", "?", "#", "%"))
                    or any(part in ("", ".", "..") for part in member.split("/"))):
                return None
            package = _xml(_read(archive, member, MAX_XML))
            if package.tag != opf + "package":
                return None
            metadata = package.findall(opf + "metadata")
            if len(metadata) != 1:
                return None
            titles = metadata[0].findall(dc + "title")
            creators = metadata[0].findall(dc + "creator")
            if (len(titles) != 1 or len(creators) > 32
                    or any(len(node) for node in titles + creators)):
                return None
            title = (titles[0].text or "").strip()
            authors = tuple((node.text or "").strip() for node in creators)
            if (not 2 <= len(title) <= 160
                    or not _public_bibliographic_text(title, required=True, limit=160)
                    or any(not _public_bibliographic_text(a, required=True, limit=512)
                           for a in authors)):
                return None
            return title, authors
    except (OSError, ValueError, TypeError, AttributeError, EOFError, KeyError,
            RuntimeError, NotImplementedError, zipfile.BadZipFile,
            zlib.error, ElementTree.ParseError):
        return None
    finally:
        if position is not None:
            try:
                stream.seek(position)
            except (OSError, ValueError):
                pass


def read_verified_epub_description(path: Path, expected_signature):
    """Open only the inode already hashed by the outer candidate review.

    The original pinned file and outer name recheck remain live until
    after the C query. This second no-follow FD must match all six fields.
    """
    directory_fd = file_fd = None
    try:
        directory_fd = _open_root(path.parent)
        file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW |
                          os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory_fd)
        if _file_signature(os.fstat(file_fd)) != expected_signature:
            return None
        with os.fdopen(file_fd, "rb") as stream:
            file_fd = None
            value = read_epub_description(stream)
            if _file_signature(os.fstat(stream.fileno())) != expected_signature:
                return None
            return value
    except (OSError, ValueError, RuntimeError):
        return None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)
