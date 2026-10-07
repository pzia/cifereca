from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


@dataclass(frozen=True)
class ArtifactInspection:
    media_format: str | None
    valid: bool
    protected: bool | None = None
    reason: str | None = None


def _local_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1].casefold()


def detect_acsm_format(path: Path) -> str | None:
    """Best-effort fulfillment format detection from ACSM metadata.

    Kobo-Adept contract v8 requires the caller to choose the exact destination
    path.  For ACSM inputs that means Paris must not assume EPUB: ADEPT loans can
    materialize either EPUB or PDF.  We intentionally inspect only local ACSM
    metadata and never execute ADEPT here.
    """
    try:
        content = path.read_bytes()
    except OSError:
        return None

    values: list[str] = []
    try:
        root = ElementTree.fromstring(content)
        for element in root.iter():
            name = _local_name(element.tag)
            if name in {"format", "type", "mediatype", "mime", "mimetype"}:
                if element.text:
                    values.append(element.text)
            for key, value in element.attrib.items():
                if _local_name(key) in {"format", "type", "mediatype", "mime", "mimetype", "href", "url"}:
                    values.append(str(value))
    except ElementTree.ParseError:
        # Some ACSM producers are lax XML emitters. A bounded text fallback is
        # safer than silently assuming EPUB when an explicit PDF MIME exists.
        values.append(content[:65536].decode("utf-8", "ignore"))

    joined = "\n".join(values).casefold()
    if "application/pdf" in joined or re.search(r"(?:^|[\s/>=\"'])pdf(?:$|[\s<\"'])", joined):
        return "pdf"
    if "application/epub+zip" in joined or "application/epub" in joined or re.search(r"(?:^|[\s/>=\"'])epub(?:$|[\s<\"'])", joined):
        return "epub"
    return None


def detect_artifact_format(path: Path) -> str | None:
    """Detect PDF/EPUB from file content rather than its extension."""
    try:
        with path.open("rb") as handle:
            head = handle.read(8)
        if head.startswith(b"%PDF-"):
            return "pdf"
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "META-INF/container.xml" in names:
                    try:
                        mime = archive.read("mimetype").strip()
                    except KeyError:
                        mime = b""
                    if not mime or mime == b"application/epub+zip":
                        return "epub"
    except (OSError, zipfile.BadZipFile):
        return None
    return None


def _validate_epub_package(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            try:
                mime = archive.read("mimetype").strip()
            except KeyError:
                return "epub_mimetype_missing"
            if mime != b"application/epub+zip":
                return "epub_mimetype_invalid"
            try:
                container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
            except KeyError:
                return "epub_container_missing"
            except ElementTree.ParseError:
                return "epub_container_invalid"
            roots = [
                node.attrib.get("full-path")
                for node in container.iter()
                if _local_name(node.tag) == "rootfile"
            ]
            if not roots:
                return "epub_rootfile_missing"
            if not all(root and root in names for root in roots):
                return "epub_package_missing"
            if archive.testzip() is not None:
                return "epub_crc_invalid"
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        return "invalid_epub"
    return None




def inspect_artifact_content(
    content: bytes,
    expected_format: str | None = None,
    *,
    strict_structure: bool = False,
) -> ArtifactInspection:
    """Validate in-memory PDF/EPUB/ACSM content without publishing it first."""
    data = bytes(content)
    actual: str | None = None
    acsm_root = None

    if data.startswith(b"%PDF-"):
        actual = "pdf"
    else:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
                if "META-INF/container.xml" in names:
                    try:
                        mime = archive.read("mimetype").strip()
                    except KeyError:
                        mime = b""
                    if not mime or mime == b"application/epub+zip":
                        actual = "epub"
        except (zipfile.BadZipFile, OSError):
            pass

    if actual is None:
        try:
            acsm_root = ElementTree.fromstring(data)
        except ElementTree.ParseError:
            acsm_root = None
        if acsm_root is not None and _local_name(acsm_root.tag) == "fulfillmenttoken":
            actual = "acsm"

    if actual is None:
        return ArtifactInspection(None, False, None, "unknown_or_invalid_format")
    if expected_format and expected_format not in {actual, "kepub" if actual == "epub" else actual}:
        return ArtifactInspection(actual, False, None, f"format_mismatch:{expected_format}->{actual}")

    if actual == "pdf":
        if strict_structure and b"%%EOF" not in data[-2048:]:
            return ArtifactInspection(actual, False, None, "pdf_eof_missing")
        return ArtifactInspection(actual, True, None, None)

    if actual == "acsm":
        return ArtifactInspection(actual, True, None, None)

    protected = False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if strict_structure:
                try:
                    mime = archive.read("mimetype").strip()
                except KeyError:
                    return ArtifactInspection(actual, False, None, "epub_mimetype_missing")
                if mime != b"application/epub+zip":
                    return ArtifactInspection(actual, False, None, "epub_mimetype_invalid")
                try:
                    container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
                except KeyError:
                    return ArtifactInspection(actual, False, None, "epub_container_missing")
                except ElementTree.ParseError:
                    return ArtifactInspection(actual, False, None, "epub_container_invalid")
                roots = [
                    node.attrib.get("full-path")
                    for node in container.iter()
                    if _local_name(node.tag) == "rootfile"
                ]
                if not roots:
                    return ArtifactInspection(actual, False, None, "epub_rootfile_missing")
                if not all(root and root in names for root in roots):
                    return ArtifactInspection(actual, False, None, "epub_package_missing")
                if archive.testzip() is not None:
                    return ArtifactInspection(actual, False, None, "epub_crc_invalid")
            if "META-INF/encryption.xml" in names:
                xml = archive.read("META-INF/encryption.xml").decode("utf-8", "ignore").casefold()
                if "aes" in xml or "xmlenc" in xml or "adobe.com/adept" in xml:
                    protected = True
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        return ArtifactInspection(actual, False, None, "invalid_epub")
    return ArtifactInspection(actual, True, protected, None)

def inspect_artifact(
    path: Path,
    expected_format: str | None = None,
    *,
    strict_structure: bool = False,
) -> ArtifactInspection:
    """Validate a final artifact and detect obvious remaining protection.

    This is deliberately conservative. It does not implement DRM removal; it
    only prevents Paris from recording a clearly malformed/misnamed artifact as
    complete.
    """
    actual = detect_artifact_format(path)
    if actual is None:
        return ArtifactInspection(None, False, None, "unknown_or_invalid_format")
    if expected_format and expected_format not in {actual, "kepub" if actual == "epub" else actual}:
        return ArtifactInspection(actual, False, None, f"format_mismatch:{expected_format}->{actual}")

    protected: bool | None = None
    if actual == "epub":
        if strict_structure:
            invalid_reason = _validate_epub_package(path)
            if invalid_reason:
                return ArtifactInspection(actual, False, None, invalid_reason)
        protected = False
        try:
            with zipfile.ZipFile(path) as archive:
                if "META-INF/encryption.xml" in archive.namelist():
                    xml = archive.read("META-INF/encryption.xml").decode("utf-8", "ignore").casefold()
                    # Font obfuscation may legitimately remain in readable EPUBs;
                    # ADEPT/content encryption uses XML Encryption/AES markers.
                    if "aes" in xml or "xmlenc" in xml or "adobe.com/adept" in xml:
                        protected = True
        except (OSError, zipfile.BadZipFile):
            return ArtifactInspection(actual, False, None, "invalid_epub")
    elif actual == "pdf":
        # Do not infer Adobe/ADEPT protection from the generic PDF /Encrypt
        # dictionary.  /Encrypt is part of the PDF security model and may be
        # present in readable/post-processed PDFs; treating it as proof of
        # remaining ADEPT DRM caused completed loans to be re-downloaded on
        # every sync.  Paris validates the PDF container here and relies on
        # Kobo-Adept success + the persisted post_processed marker for ACSM
        # fulfillment state.
        protected = None
        try:
            with path.open("rb") as handle:
                if not handle.read(8).startswith(b"%PDF-"):
                    return ArtifactInspection(actual, False, None, "invalid_pdf")
        except OSError:
            return ArtifactInspection(actual, False, None, "unreadable_pdf")

    return ArtifactInspection(actual, True, protected, None)
