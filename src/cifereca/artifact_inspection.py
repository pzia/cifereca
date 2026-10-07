from __future__ import annotations

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


def inspect_artifact(path: Path, expected_format: str | None = None) -> ArtifactInspection:
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
