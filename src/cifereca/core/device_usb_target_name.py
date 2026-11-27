"""Reader-specific choice of the existing Library name or technical SHA.

Only the readable prefix changes. The full digest and exact format suffix
remain mandatory through reservation, preflight, publication and recovery.
No path from a catalogue/provider is trusted and no format is inferred.
"""
from pathlib import PurePosixPath
from unicodedata import normalize

from ..filename_normalization import sanitize_filename_component
from ..filename_suffixes import split_filename_suffix
from .device_usb_preview import _sha
from .device_usb_variant_review import _SUFFIX


NAMING_MODES = ("sha256", "source")
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL"} |
                      {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)})


def _prefix(stem: str) -> str:
    value = sanitize_filename_component(normalize("NFC", stem), "Livre")
    # Keep the whole name well under FAT's 255-byte component limit even
    # with a 64-digit SHA and the longest existing Kobo suffix.
    if value.split(".")[0].upper() in _RESERVED:
        value = "Livre-" + value
    value = value.encode("utf-8")[:120].decode("utf-8", errors="ignore").strip(" ._")
    return value or "Livre"


def usb_target_name(digest: str, fmt: str, *, mode="sha256", source_path=None) -> str:
    if (not _sha(digest) or type(fmt) is not str or fmt not in _SUFFIX
            or type(mode) is not str or mode not in NAMING_MODES):
        raise ValueError("usb_target_naming_invalid")
    if mode == "sha256":
        return digest + _SUFFIX[fmt]
    if (type(source_path) is not str or not source_path
            or not PurePosixPath(source_path).is_absolute()
            or ".." in PurePosixPath(source_path).parts):
        raise ValueError("usb_target_source_name_unverified")
    stem, _ = split_filename_suffix(PurePosixPath(source_path))
    return _prefix(stem) + "--" + digest + _SUFFIX[fmt]


def usb_target_format(name: object, digest: str) -> str | None:
    if type(name) is not str or not _sha(digest):
        return None
    for fmt, suffix in _SUFFIX.items():
        tail = digest + suffix
        if name == tail:
            return fmt
        if name.endswith("--" + tail):
            prefix = name[:-len(tail)-2]
            if prefix and _prefix(prefix) == prefix:
                return fmt
    return None


def usb_target_name_matches(name: object, digest: str, fmt: str | None = None) -> bool:
    selected = usb_target_format(name, digest)
    return selected is not None and (fmt is None or selected == fmt)
