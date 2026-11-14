"""One-way historical configuration repairs, never runtime provider policy.

Only version-gated legacy upgrades live here. The Core Settings path calls
this generic dispatcher; new supplier contracts never become automatic
migrations or a source for implicit grants.
"""
from __future__ import annotations

from typing import Any, Callable


def run_pending_config_migrations(
    *, data: dict[str, Any], identities: list[dict[str, Any]],
    rows: list[dict[str, Any]], default_identity: str,
    used_directories: set[str], directory_slug: Callable[[str, str], str],
) -> bool:
    """Run registered historical upgrades; True only for row modifications."""
    from .kobo_account_cardinality_v1 import migrate_once
    return migrate_once(
        data=data, identities=identities, rows=rows,
        default_identity=default_identity,
        used_directories=used_directories, directory_slug=directory_slug,
    )
