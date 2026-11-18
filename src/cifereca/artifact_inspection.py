from __future__ import annotations

import io
import re
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal
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
    """Detect PDF/EPUB/ACSM from file content rather than its extension."""
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
        size = path.stat().st_size
        if 0 < size <= 2 * 1024 * 1024:
            root = ElementTree.parse(path).getroot()
            if _local_name(root.tag) == "fulfillmenttoken":
                return "acsm"
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError):
        return None
    return None


_EPUB_MAX_ENTRIES = 20000
_EPUB_MAX_MIMETYPE_BYTES = 100
_EPUB_MAX_CONTAINER_BYTES = 1024 * 1024
_EPUB_MAX_PACKAGE_BYTES = 5 * 1024 * 1024


def _bounded_zip_read(archive: zipfile.ZipFile, name: str, maximum: int) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > maximum:
        raise ValueError("zip entry too large")
    return archive.read(name)


def _validate_epub_archive(archive: zipfile.ZipFile) -> str | None:
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(infos) > _EPUB_MAX_ENTRIES:
        return "epub_too_many_entries"
    if len(set(names)) != len(names):
        return "epub_duplicate_entries"
    if any(info.flag_bits & 1 for info in infos):
        return "epub_zip_encrypted"

    try:
        mime = _bounded_zip_read(archive, "mimetype", _EPUB_MAX_MIMETYPE_BYTES).strip()
    except KeyError:
        return "epub_mimetype_missing"
    except ValueError:
        return "epub_mimetype_oversized"
    if mime != b"application/epub+zip":
        return "epub_mimetype_invalid"

    try:
        container = ElementTree.fromstring(
            _bounded_zip_read(archive, "META-INF/container.xml", _EPUB_MAX_CONTAINER_BYTES)
        )
    except KeyError:
        return "epub_container_missing"
    except ValueError:
        return "epub_container_oversized"
    except ElementTree.ParseError:
        return "epub_container_invalid"

    roots = [
        node.attrib.get("full-path")
        for node in container.iter()
        if _local_name(node.tag) == "rootfile"
    ]
    if not roots:
        return "epub_rootfile_missing"

    archive_names = set(names)
    for root in roots:
        if not root:
            return "epub_package_missing"
        if root.startswith("/") or ".." in root.split("/"):
            return "epub_package_path_invalid"
        if root not in archive_names:
            return "epub_package_missing"
        try:
            package = ElementTree.fromstring(
                _bounded_zip_read(archive, root, _EPUB_MAX_PACKAGE_BYTES)
            )
        except ValueError:
            return "epub_package_oversized"
        except ElementTree.ParseError:
            return "epub_package_invalid"
        if _local_name(package.tag) != "package":
            return "epub_package_invalid"

    if archive.testzip() is not None:
        return "epub_crc_invalid"
    return None


def _validate_epub_package(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            return _validate_epub_archive(archive)
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        return "invalid_epub"




_FONT_OBFUSCATION_ALGORITHMS = frozenset((
    "http://www.idpf.org/2008/embedding",
    "http://ns.adobe.com/pdf/enc#RC",
))
_EPUB_ENCRYPTION_MANIFEST_MAX = 256 * 1024


def _epub_encryption_protected(archive: zipfile.ZipFile) -> bool:
    """Only exact, standard font obfuscation is non-DRM.

    A namespace containing 'xmlenc' is *not* by itself evidence of
    content DRM. Any unknown/mixed/malformed encryption claim stays
    protected, including absent method and oversized manifests.
    """
    name = "META-INF/encryption.xml"
    # A second manifest can hide a protected declaration behind an innocent
    # last entry: never exempt a duplicated ZIP member in non-strict mode.
    if sum(info.filename == name for info in archive.infolist()) > 1:
        return True
    try:
        info = archive.getinfo(name)
    except KeyError:
        return False
    if info.file_size > _EPUB_ENCRYPTION_MANIFEST_MAX:
        return True
    try:
        with archive.open(info) as stream:
            raw = stream.read(_EPUB_ENCRYPTION_MANIFEST_MAX + 1)
    except (RuntimeError, NotImplementedError, OSError, ValueError,
            zipfile.BadZipFile):
        return True
    if len(raw) > _EPUB_ENCRYPTION_MANIFEST_MAX:
        return True
    text = raw.decode("utf-8", "ignore").casefold()
    if "adobe.com/adept" in text or "aes" in text:
        return True
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return True
    if _local_name(root.tag) != "encryption":
        return True
    # Additional keys, raw cipher values or unknown XML declarations are
    # not proven to be standard font obfuscation. Fail closed.
    allowed_nodes = {
        "encryption", "encrypteddata", "encryptionmethod",
        "cipherdata", "cipherreference", "encryptionproperties",
        "encryptionproperty",
    }
    if any(_local_name(node.tag) not in allowed_nodes
           for node in root.iter()):
        return True
    encrypted_nodes = [
        node for node in root.iter()
        if _local_name(node.tag) == "encrypteddata"
    ]
    if not encrypted_nodes:
        return True
    for node in encrypted_nodes:
        methods = [
            child for child in node
            if _local_name(child.tag) == "encryptionmethod"
        ]
        if len(methods) != 1:
            return True
        method = methods[0]
        if method.attrib.get("Algorithm") not in _FONT_OBFUSCATION_ALGORITHMS:
            return True
    # Do not silently ignore unknown nested encryption declarations.
    for node in root.iter():
        if _local_name(node.tag) == "encryptionmethod" and not any(
            node is child
            for data in encrypted_nodes for child in data
            if _local_name(child.tag) == "encryptionmethod"
        ):
            return True
    return False


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
                invalid_reason = _validate_epub_archive(archive)
                if invalid_reason:
                    return ArtifactInspection(actual, False, None, invalid_reason)
            protected = _epub_encryption_protected(archive)
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
                protected = _epub_encryption_protected(archive)
        except (OSError, zipfile.BadZipFile):
            return ArtifactInspection(actual, False, None, "invalid_epub")
    elif actual == "acsm":
        protected = None
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


def inspect_epub_declared_layout(
    stream: BinaryIO,
) -> Literal["fixed", "reflowable", "unknown"]:
    """Read explicit EPUB3 package layout, not rendering or KEPUB readiness.

    The caller supplies the already-open, owner-validated binary stream.
    No paths are reopened; no page, image or DRM payload is read. The
    original stream position is restored. Missing, mixed, ambiguous or
    inaccessible declarations stay unknown; no suffix-based inference.
    """
    from .epub_metadata import MAX_XML, _bounded_directory, _read, _xml

    ocf = "{urn:oasis:names:tc:opendocument:xmlns:container}"
    opf = "{http://www.idpf.org/2007/opf}"
    position = None
    try:
        position = stream.tell()
        _bounded_directory(stream)
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            names = {info.filename for info in infos}
            if (len(infos) != len(names)
                    or any(info.flag_bits & 1 for info in infos)
                    or "META-INF/encryption.xml" in names):
                # Even benign obfuscation is not classified in this first
                # conservative tranche. Never inspect protected resources.
                return "unknown"
            if _read(archive, "mimetype", 100).strip() != b"application/epub+zip":
                return "unknown"
            container = _xml(_read(archive, "META-INF/container.xml", MAX_XML))
            if container.tag != ocf + "container":
                return "unknown"
            roots = list(container.iter(ocf + "rootfile"))
            if (len(roots) != 1
                    or container.findall(ocf + "rootfiles/" + ocf + "rootfile") != roots):
                return "unknown"
            member = roots[0].get("full-path", "")
            if (not member or member.startswith("/")
                    or any(char in member for char in ("\\", "\x00", ":", "?", "#", "%"))
                    or any(part in ("", ".", "..") for part in member.split("/"))):
                return "unknown"
            package = _xml(_read(archive, member, MAX_XML))
            if (package.tag != opf + "package"
                    or not package.get("version", "").startswith("3.")):
                return "unknown"
            # Use only the reserved EPUB3 rendition vocabulary. An explicit
            # redefinition or alias of that vocabulary is not supported here.
            prefix = package.get("prefix", "")
            if ("rendition:" in prefix or "http://www.idpf.org/vocab/rendition/#" in prefix):
                return "unknown"
            metadata = package.findall(opf + "metadata")
            spines = package.findall(opf + "spine")
            if len(metadata) != 1 or len(spines) != 1:
                return "unknown"
            declarations = [
                node for node in metadata[0].findall(opf + "meta")
                if node.get("property") == "rendition:layout"
            ]
            if (len(declarations) != 1 or "refines" in declarations[0].attrib
                    or len(declarations[0])):
                return "unknown"
            layout = {"pre-paginated": "fixed", "reflowable": "reflowable"}.get(
                (declarations[0].text or "").strip()
            )
            if layout is None:
                return "unknown"
            expected = ("rendition:layout-pre-paginated" if layout == "fixed"
                        else "rendition:layout-reflowable")
            items = spines[0].findall(opf + "itemref")
            if not items or any(not item.get("idref", "").strip() for item in items):
                return "unknown"
            for item in items:
                overrides = [
                    token for token in item.get("properties", "").split()
                    if token.startswith("rendition:layout-")
                ]
                if overrides and overrides != [expected]:
                    # Mixed or unknown page layouts cannot be represented by
                    # one document-wide fixed/reflowable classification.
                    return "unknown"
            return layout
    except (OSError, ValueError, TypeError, AttributeError, EOFError, KeyError,
            RuntimeError, NotImplementedError, zipfile.BadZipFile,
            zlib.error, ElementTree.ParseError):
        return "unknown"
    finally:
        if position is not None:
            try:
                stream.seek(position)
            except (OSError, ValueError):
                pass
