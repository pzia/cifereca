"""#3252: closed, read-only profile of EPUB encryption markers after a Core veto.

This is diagnostic evidence only. It MUST NOT authorize publication, decide
whether the artifact is protected, or reveal raw XML, paths or keys.
"""
from __future__ import annotations

from pathlib import Path
import zipfile
from xml.etree import ElementTree

_LIMIT = 256 * 1024
_FONT_ALGORITHMS = frozenset((
    "http://www.idpf.org/2008/embedding",
    "http://ns.adobe.com/pdf/enc#RC",
))


def rejected_epub_encryption_profile(path: Path) -> str:
    """Return one closed reason, never a snippet of private encryption.xml."""
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("META-INF/encryption.xml")
            if info.file_size > _LIMIT:
                return "encryption_manifest_oversized"
            with archive.open(info) as stream:
                raw = stream.read(_LIMIT + 1)
            if len(raw) > _LIMIT:
                return "encryption_manifest_oversized"
        root = ElementTree.fromstring(raw)
        markers = raw.decode("utf-8", "ignore").casefold()
        if "adobe.com/adept" in markers:
            return "adobe_adept_marker"
        algorithms = [
            str(value)
            for element in root.iter()
            for key, value in element.attrib.items()
            if str(key).rsplit("}", 1)[-1].casefold() == "algorithm"
        ]
        if any("aes" in algorithm.casefold() for algorithm in algorithms):
            return "aes_algorithm"
        if algorithms and all(
            algorithm in _FONT_ALGORITHMS for algorithm in algorithms
        ):
            return "font_obfuscation_algorithms_only"
        if "aes" in markers:
            return "aes_marker_without_algorithm"
        if "xmlenc" in markers:
            return "generic_xmlenc_marker"
        return "other_encryption_marker"
    except KeyError:
        return "encryption_manifest_missing"
    except ElementTree.ParseError:
        return "encryption_manifest_malformed"
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError,
            ValueError, UnicodeError):
        return "encryption_manifest_unreadable"
