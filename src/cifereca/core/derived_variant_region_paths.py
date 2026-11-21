"""#6937: reserved namespace, not an ownership or retention proof.

An exact reserved path component excludes the region from ordinary library
consumers even before registration or after an interrupted provision.
"""
from pathlib import Path

PRIVATE_DERIVED_REGION = ".cifereca-derived-v1"


def private_derived_path(path: Path) -> bool:
    return PRIVATE_DERIVED_REGION in path.parts
