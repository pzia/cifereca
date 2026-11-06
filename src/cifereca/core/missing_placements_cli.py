"""#2847: installed, strictly read-only missing Placement inspection CLI.

Run: python -m cifereca.core.missing_placements_cli --identity PROFILE_ID
Only closed, aggregate counters leave Core. No Storage, Config.load, migration,
provider, mkdir, file publication, repair or automatic acquisition is called.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat

from ..config import Config, DEFAULT_CONFIG, _merge
from ..paths import config_file, database_file
from .library_audit_roots import configured_identity_roots
from .library_evidence_scope import scope_evidence
from .library_reconciliation import read_library_snapshot
from .library_stored_paths import inspect_stored_paths

_MAX_CONFIG_BYTES = 1024 * 1024
_STATES = (
    "stored_path_present", "stored_path_missing",
    "stored_path_unavailable", "stored_path_out_of_scope",
)


class _ReadOnlyConfig:
    """Reuse normal identity/root normalization without Config.load's chmod."""

    get = Config.get
    identities = Config.identities

    def __init__(self, data: dict):
        self.data = data


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous_config")
        result[key] = value
    return result


def _read_config(path: Path) -> _ReadOnlyConfig:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("invalid_config_path")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or not 0 < before.st_size <= _MAX_CONFIG_BYTES):
            raise ValueError("untrusted_config")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(_MAX_CONFIG_BYTES + 1)
        after = os.fstat(fd)
        if (len(raw) != before.st_size
                or (before.st_dev, before.st_ino, before.st_mtime_ns,
                    before.st_size) != (
                    after.st_dev, after.st_ino, after.st_mtime_ns,
                    after.st_size)):
            raise ValueError("unstable_config")
    finally:
        os.close(fd)
    payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys)
    if type(payload) is not dict:
        raise ValueError("invalid_config")
    # Only an in-memory view. The normal Config.load() chmods its source
    # and performs compatibility migrations, so it is forbidden here.
    return _ReadOnlyConfig(_merge(DEFAULT_CONFIG, payload))


def inspect_missing_placements(*, config: Path, database: Path,
                               identity_id: str) -> dict:
    """Report one configured identity, without paths, IDs or book metadata.

    'complete' means all selected SQLite placement paths were directly
    classified under all Core-configured identity roots. Zero with an
    incomplete snapshot, root or traversal MUST NOT mean zero missing books.
    """
    if (type(identity_id) is not str or not identity_id
            or len(identity_id) > 1024
            or any(ord(char) < 32 or ord(char) == 127 for char in identity_id)
            or not isinstance(config, Path)
            or not isinstance(database, Path)
            or not database.is_absolute() or ".." in database.parts):
        raise ValueError("invalid_inspection_scope")
    roots = configured_identity_roots(_read_config(config), identity_id)
    snapshot = scope_evidence(
        read_library_snapshot(database, identity_id=identity_id),
        (identity_id,),
    )
    selected = snapshot.tables.get("placements")
    if type(selected) is not list or snapshot.reasons:
        return {
            "schema": 1, "status": "incomplete_scan",
            "read_only": True, "repair_authorized": False,
            "acquisition_authorized": False,
            "scope": "selected_identity", "root_count": len(roots[identity_id]),
            "sqlite_coverage": "incomplete", "path_coverage": "not_checked",
            "counts": None, "reasons": sorted(snapshot.reasons) or [
                "snapshot_schema_incomplete"],
        }
    paths = inspect_stored_paths(snapshot, roots)
    counts = {state: sum(x == state for x in paths.states.values())
              for state in _STATES}
    inspected = sum(counts.values())
    coverage_complete = (
        not paths.reasons and inspected == len(selected)
        and counts["stored_path_unavailable"] == 0
        and counts["stored_path_out_of_scope"] == 0
    )
    reasons = set(paths.reasons)
    if inspected != len(selected):
        reasons.add("stored_path_budget")
    if counts["stored_path_out_of_scope"]:
        reasons.add("stored_path_out_of_scope")
    return {
        "schema": 1,
        "status": "complete" if coverage_complete else "incomplete_scan",
        "read_only": True, "repair_authorized": False,
        "acquisition_authorized": False, "scope": "selected_identity",
        "root_count": len(roots[identity_id]),
        "sqlite_coverage": "complete",
        "path_coverage": "complete" if coverage_complete else "incomplete",
        "counts": {
            "placements": len(selected),
            "verified_present": counts["stored_path_present"],
            "verified_missing": counts["stored_path_missing"],
            "unavailable": counts["stored_path_unavailable"],
            "out_of_scope": counts["stored_path_out_of_scope"],
            "unexamined": max(0, len(selected)-inspected),
        },
        "missing_by_sqlite_state": {
            "present": paths.missing_by_sqlite_state["present"],
            "missing": paths.missing_by_sqlite_state["missing"],
            "other": paths.missing_by_sqlite_state["other"],
        },
        "reasons": sorted(reasons),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compteurs de Placements physiques (lecture seule, expurgés)")
    parser.add_argument("--identity", required=True, metavar="ID")
    parser.add_argument("--config", type=Path, default=config_file(create=False))
    parser.add_argument("--database", type=Path, default=database_file(create=False))
    args = parser.parse_args(argv)
    try:
        result = inspect_missing_placements(
            config=args.config, database=args.database,
            identity_id=args.identity)
    except (OSError, ValueError, TypeError, UnicodeError,
            RecursionError, RuntimeError):
        result = {
            "schema": 1, "status": "incomplete_scan",
            "read_only": True, "repair_authorized": False,
            "acquisition_authorized": False, "scope": "selected_identity",
            "sqlite_coverage": "incomplete", "path_coverage": "not_checked",
            "counts": None, "reasons": ["inspection_unavailable"],
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
