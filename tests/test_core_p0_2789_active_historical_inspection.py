"""P0 #2789: historic missing SQLite locations are not active broken links.

Aggregate counters are identity-scoped and read-only. No name, path,
artifact ID or account crosses the UI's public diagnostic boundary.
"""
from __future__ import annotations

import sqlite3

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_library_reconciliation import fixture_db, filesystem


def test_273_historical_missing_and_zero_active_do_not_look_like_273_failures(tmp_path):
    root = tmp_path / "reader"
    root.mkdir()
    database = tmp_path / "synthetic.sqlite"
    fixture_db(database)
    with sqlite3.connect(database) as db:
        db.executemany(
            "INSERT INTO placements("
            "placement_id,identity_id,path,provider_id,state,artifact_id"
            ") VALUES(?,?,?,?,?,?)",
            [
                (f"PRIVATE-historical-{i}", "reader",
                 str(root / f"PRIVATE-old-{i}.epub"), "kobo", "missing",
                 f"sha256:PRIVATE-{i}")
                for i in range(273)
            ]
            + [
                ("PRIVATE-present-missing", "reader",
                 str(root / "PRIVATE-active-path.epub"), "numilog",
                 "present", "PRIVATE-artifact-active"),
                ("PRIVATE-other-state", "reader",
                 str(root / "PRIVATE-other-path.epub"), "numilog",
                 "invalid", "PRIVATE-other-artifact"),
                ("PRIVATE-foreign", "other-reader",
                 str(root / "PRIVATE-foreign.epub"), "paris",
                 "present", "PRIVATE-foreign-artifact"),
            ],
        )
    original = filesystem(tmp_path)
    audit = audit_library(database, {"reader": (root,)})
    assert audit.redacted()["status"] == "complete", audit.redacted()["reasons"]
    session = LocalInspection(audit, lambda: True, "reader", roots=(root,))
    overview = session.overview()
    assert overview["paths"] == 276
    assert overview["paths_historical_missing"] == 273
    assert overview["paths_active_missing"] == 1
    assert overview["paths_other_missing"] == 1
    assert session.page(state="stored_path_missing").total == 276
    # Historical path rows are not necessarily missing books. No scanning
    # side-effect, no DB cleanup and no guessed reconnection is permitted.
    assert filesystem(tmp_path) == original
    session.close()
    assert session.overview() == {}
    assert not session._path_missing_by_sqlite_state
