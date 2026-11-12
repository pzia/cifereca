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
            "SELECT key FROM metadata WHERE key IS NOT NULL"
        )
        counts = {name: 0 for name in _PREFIXES}
        # BnF .revision rows share the same prefix: do not count as toggles.
        for (key,) in rows:
            if type(key) is not str:
                continue
            for name, prefix in _PREFIXES.items():
                if key.startswith(prefix):
                    if name == "bnf_loan" and key.endswith(".revision"):
                        continue
                    if name == "kobo_purchase" and key.endswith(".revision"):
                        continue
                    counts[name] += 1
                    break
        return {"schema": "cifereca.source-consent-inventory/1",
                "read_only": True, "counts": counts}
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
