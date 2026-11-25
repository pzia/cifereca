"""Explicit owner deployment input for the ordinary desktop startup.

A local config entry is the deployment owner's independent digest pin, not
a digest read from installed plugin metadata. No selection or installation
is performed here; the existing durable selection and active-package
checks remain mandatory at execution/read time.
"""
import os
from pathlib import Path
import re
import stat

from .notice_consolidator_selection import _identifier
from .notice_consolidator_trusted_registry import TrustedConsolidatorRegistry


def desktop_age_configuration(value):
    if value is None:
        return None, None
    if (type(value) is not dict or set(value) != {"archive", "sha256", "scope"}
            or type(value["archive"]) is not str
            or not 0 < len(value["archive"]) <= 4096
            or "\x00" in value["archive"]
            or type(value["sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None
            or not _identifier(value["scope"], max_len=160)):
        raise ValueError("notice_descriptive_invalid_desktop_deployment")
    path = Path(value["archive"])
    if not path.is_absolute():
        raise ValueError("notice_descriptive_invalid_desktop_deployment")
    limit = 64 * 1024 * 1024
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
                raise ValueError("notice_descriptive_invalid_desktop_deployment")
            payload = stream.read(limit + 1)
        if len(payload) != info.st_size:
            raise ValueError("notice_descriptive_invalid_desktop_deployment")
        registry = TrustedConsolidatorRegistry(((payload, value["sha256"]),))
        if not registry.public_age_digests():
            raise ValueError("notice_descriptive_invalid_desktop_deployment")
    except (OSError, ValueError, TypeError):
        raise ValueError("notice_descriptive_invalid_desktop_deployment") from None
    return registry, value["scope"]
