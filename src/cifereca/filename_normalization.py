from __future__ import annotations

import re

_SAFE_FILENAME_COMPONENT = re.compile(
    r"[^A-Za-z0-9._()\- àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]+"
)


def sanitize_filename_component(value: str, fallback: str = "download") -> str:
    """Return the conservative filename component historically used by providers."""
    cleaned = _SAFE_FILENAME_COMPONENT.sub("_", str(value or fallback)).strip(" ._")
    return cleaned or str(fallback)
