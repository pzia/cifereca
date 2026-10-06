"""Safe readable filenames for Numilog EPUBs."""
from pathlib import Path
import hashlib
import os
import re
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

def _clean(value: str) -> str:
    value = unicodedata.normalize("NFC", str(value or ""))
    value = re.sub(r"[\\/<>:\"|?*\x00-\x1f]", "_", value)
    value = " ".join(value.split()).strip(" ._") or "Livre Numilog"
    return value.encode("utf-8")[:180].decode("utf-8", "ignore").rstrip(" ._")

def epub_author(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            root = container.find(".//{*}rootfile")
            full_path = root.attrib.get("full-path", "") if root is not None else ""
            if not full_path or full_path.startswith("/") or ".." in full_path.split("/"): return None
            metadata = ET.fromstring(archive.read(full_path)).find("{*}metadata")
            if metadata is None: return None
            values = [" ".join(node.itertext()).strip() for node in metadata.findall("{http://purl.org/dc/elements/1.1/}creator")]
            return "; ".join(dict.fromkeys(value for value in values if value))[:300] or None
    except (OSError, KeyError, ValueError, ET.ParseError, zipfile.BadZipFile): return None

def publish(part: Path, directory: Path, *, title: str, author: str | None, book_id: str) -> Path:
    author = author or epub_author(part)
    stem = _clean(str(title) + (" - " + author if author else ""))
    digest = hashlib.sha256(part.read_bytes()).hexdigest(); suffix = hashlib.sha256(str(book_id).encode()).hexdigest()[:8]
    for candidate in (stem, f"{stem} - {suffix}", f"{stem} - {suffix}-{digest[:12]}"):
        target = directory / f"{candidate}.epub"
        try: os.link(part, target); return target
        except FileExistsError:
            if target.is_file() and not target.is_symlink() and hashlib.sha256(target.read_bytes()).hexdigest() == digest: return target
    raise FileExistsError("Numilog : nom de fichier déjà occupé ; fichier existant conservé")
