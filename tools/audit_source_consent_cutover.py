#!/usr/bin/env python3
"""#5077: offline, read-only inventory of legacy source-consent metadata.

No source IDs, account IDs, credentials, preference values or database paths
are emitted. This is a diagnostic, NOT an opt-in migration.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

# Exact historical prefixes; never match other metadata families.
_PREFIXES = {
    "numilog_purchase": "sync.source.numilog.materialization.consent.v1.",
    "numilog_epoch": "sync.source.numilog.materialization.epoch.v1.",
    "bnf_loan": "sync.source.bnf_auto.",
    "kobo_purchase": "sync.source.materialization.on_start.",
    "cas_source_policy": "sync.source.acquisition-policy.v1.",
}


def inventory(database: Path) -> dict[str, object]:
    if not database.is_file():
        raise ValueError("database_unavailable")
    # mode=ro refuses to create the DB and forbids writes. immutable=1 would
    # silently ignore an active WAL, so do not use it for live installations.
    connection = sqlite3.connect(
        database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2,
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        rows = connection.execute(
            "SELECT key, value FROM metadata WHERE key IS NOT NULL"
        )
        counts = {name: 0 for name in _PREFIXES}
        # Exact matching is performed inside the SQLite snapshot. The output
        # never contains the suffix, original value or individual row status.
        legacy = {"numilog_purchase": {}, "bnf_loan": {}, "kobo_purchase": {}}
        revisions = {"numilog_purchase": {}, "bnf_loan": {}, "kobo_purchase": {}}
        active = {"numilog_purchase": "on_start:v1",
                  "bnf_loan": "1", "kobo_purchase": "1"}
        scanned = 0
        for key, value in rows:
            scanned += 1
            if scanned > 1_000_000:
                raise ValueError("inventory_too_large")
            if type(key) is not str:
                continue
            for name, prefix in _PREFIXES.items():
                if not key.startswith(prefix):
                    continue
                suffix = key[len(prefix):]
                if not suffix:
                    break
                if name == "numilog_epoch":
                    counts[name] += 1
                    revisions["numilog_purchase"][suffix] = value
                elif name in ("bnf_loan", "kobo_purchase"):
                    if suffix.endswith(".revision"):
                        revisions[name][suffix[:-len(".revision")]] = value
                    else:
                        counts[name] += 1
                        legacy[name][suffix] = value
                else:
                    counts[name] += 1
                    if name == "numilog_purchase":
                        legacy[name][suffix] = value
                break
        classifications = {}
        for name in legacy:
            valid_on = 0
            unversioned_on = 0
            not_on = 0
            for sid, value in legacy[name].items():
                if value != active[name]:
                    not_on += 1
                elif (type(revisions[name].get(sid)) is str
                      and len(revisions[name][sid]) == 32
                      and all(c in "0123456789abcdef"
                              for c in revisions[name][sid])):
                    valid_on += 1
                else:
                    unversioned_on += 1
            classifications[name] = {
                "on_with_valid_revision": valid_on,
                "on_missing_or_invalid_revision": unversioned_on,
                "not_on": not_on,
            }
        return {"schema": "cifereca.source-consent-inventory/1",
                "read_only": True, "counts": counts,
                "legacy_modes": classifications}
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="existing cifereca SQLite database")
    args = parser.parse_args(argv)
    try:
        result = inventory(args.database)
    except (ValueError, sqlite3.Error, OSError):
        print("source_consent_inventory_unavailable", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
