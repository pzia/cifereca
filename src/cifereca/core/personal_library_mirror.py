"""Read-only owned-library mirror results without private provider details.

A dict subtype maintains the legacy per-source numeric return value while
giving opening-sync honest aggregate partial/failed statuses. Counts never
contain a title, credential, signed URL or provider-supplied exception.
"""
from __future__ import annotations


class PersonalLibraryMirrorCounts(dict[str, int]):
    def __init__(self) -> None:
        super().__init__()
        self.failed_sources = 0
        self.blocked_sources = 0
