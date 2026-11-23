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


def _isbn13(value):
    """Normalize an explicitly typed ISBN; reject arbitrary identifier text."""
    if type(value) is not str or len(value) > 64:
        return None
    raw = value.strip()
    if raw.casefold().startswith("urn:isbn:"):
        raw = raw[9:]
    number = raw.replace("-", "").replace(" ", "").upper()
    if (len(number) == 10 and all(c in "0123456789" for c in number[:9])
            and number[-1] in "0123456789X"):
        digits = [int(c) for c in number[:9]] + [10 if number[-1] == "X" else int(number[-1])]
        if sum((10 - i) * digit for i, digit in enumerate(digits)) % 11:
            return None
        prefix = "978" + number[:9]
        check = (-sum((1 if i % 2 == 0 else 3) * int(c)
                      for i, c in enumerate(prefix))) % 10
        return prefix + str(check)
    if (len(number) == 13 and number.startswith(("978", "979"))
            and all(c in "0123456789" for c in number)
            and sum((1 if i % 2 == 0 else 3) * int(c)
                    for i, c in enumerate(number)) % 10 == 0):
        return number
    return None


def _explicit_isbns(package, metadata, opf, dc, *, allow_refinements):
    """EPUB 2 scheme/URN and EPUB 3 reserved ONIX identifier-type.

    Only standard local ID refinements are read. Unknown/custom schemes,
    duplicate IDs or conflicting refinements never create a stronger claim.
    Checksums alone cannot turn an untyped identifier into an ISBN.
    """
    identifiers = metadata.findall(dc + "identifier")
    if len(identifiers) > 32:
        return ()
    # EPUB 3 reserves this scheme prefix. Reject refinements altogether if
    # the package declares it again (checked by the caller).
    refinements = metadata.findall(opf + "meta")
    if len(refinements) > 256:
        return ()
    counts = {}
    for node in package.iter():
        identifier = node.get("id")
        if identifier:
            counts[identifier] = counts.get(identifier, 0) + 1
    isbns = set()
    for node in identifiers:
        raw = (node.text or "").strip()
        if len(node) or len(raw) > 64:
            continue
        scheme = node.get(opf + "scheme", "")
        identifier = node.get("id", "")
        if identifier and counts.get(identifier) != 1:
            continue
        types = [meta for meta in refinements
                 if identifier and meta.get("refines") == "#" + identifier
                 and meta.get("property") == "identifier-type"]
        typed = scheme.casefold() == "isbn" or raw.casefold().startswith("urn:isbn:")
        if scheme and scheme.casefold() != "isbn":
            # An explicit incompatible EPUB 2 scheme is a contradiction.
            continue
        if types:
            if not allow_refinements:
                continue
            if len(types) != 1 or len(types[0]) or types[0].get("scheme") != "onix:codelist5":
                continue
            code = (types[0].text or "").strip()
            length = {"02": 10, "15": 13}.get(code)
            number = raw[9:] if raw.casefold().startswith("urn:isbn:") else raw
            if length is None or len(number.replace("-", "").replace(" ", "")) != length:
                continue
            typed = True
        if typed:
            value = _isbn13(raw)
            if value is not None:
                isbns.add(value)
    return tuple(sorted(isbns))


# Explicit controlled URI claims only; generic Text, genre and filename
# do not distinguish a book, comic or document. Audio is a separate bundle.
_DOCUMENTARY_TYPES = {
    "Book": "Livres",
    "ComicStory": "BD-Manga",
    "ComicIssue": "BD-Manga",
    "DigitalDocument": "Documents",
}


def _documentary_nature(metadata, dc):
    nodes = metadata.findall(dc + "type")
    if not 1 <= len(nodes) <= 16:
        return None
    categories = set()
    for node in nodes:
        value = (node.text or "").strip()
        if len(node) or len(value) > 128:
            return None
        for prefix in ("https://schema.org/", "http://schema.org/"):
            if value.startswith(prefix):
                category = _DOCUMENTARY_TYPES.get(value[len(prefix):])
                break
        else:
            category = None
        if category is None:
            return None
        categories.add(category)
    return next(iter(categories)) if len(categories) == 1 else None


def _source_url_claims(metadata, dc):
    """Read literal OPF claims for comparison, without resolving any URL."""
    nodes = metadata.findall(dc + "source") + metadata.findall(dc + "identifier")
    if len(nodes) > 32:
        return frozenset()
    return frozenset(
        value for node in nodes
        if not len(node) and (value := node.text or "").startswith("https://")
        and len(value) <= 512
    )


def _read_epub_details(stream, *, include_source_claims=False):
    """Pure bounded metadata read; restore caller position, do not close.

    ISBN is a descriptive claim in this file, not verified external origin.
    Preserve multiple valid values instead of choosing an edition.
    """
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
            # Reserved ONIX vocabulary only, never a redefined package
            # prefix (or an EPUB 2 meta that merely resembles a refinement).
            prefix = package.get("prefix", "")
            allow_refinements = (package.get("version", "").startswith("3.")
                                 and "onix:" not in prefix.casefold())
            isbns = _explicit_isbns(package, metadata[0], opf, dc, allow_refinements=allow_refinements)
            details = title, authors, isbns, _documentary_nature(metadata[0], dc)
            if include_source_claims:
                return (*details, _source_url_claims(metadata[0], dc))
            return details
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


def read_epub_bibliography(stream):
    value = _read_epub_details(stream)
    return value[:3] if value is not None else None


def read_epub_description(stream):
    """Compatibility title/creator projection used by the organizer."""
    value = read_epub_bibliography(stream)
    return value[:2] if value is not None else None


def read_verified_epub_details(path: Path, expected_signature, *, include_source_claims=False):
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
            value = _read_epub_details(stream, include_source_claims=include_source_claims)
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


def read_verified_epub_bibliography(path: Path, expected_signature):
    value = read_verified_epub_details(path, expected_signature)
    return value[:3] if value is not None else None


def read_verified_epub_description(path: Path, expected_signature):
    """Preserve the existing title/creator contract for other callers."""
    value = read_verified_epub_bibliography(path, expected_signature)
    return value[:2] if value is not None else None
