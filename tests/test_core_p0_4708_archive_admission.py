"""#4708: private pre/post admission proof, synthetic storage only."""
from pathlib import Path

import pytest

from cifereca.core import missing_placement_archive_admission as admission
from cifereca.core import missing_placement_archive_sql as archive
from tests.test_core_p0_4708_archive_sql import rig


def _observe(db, row, args, phase="live", **changes):
    options = {k: args[k] for k in (
        "expected_payload", "expected_digest", "max_records", "max_bytes",
    )}
    options.update(library_root=Path(row[2]).parent.parent,
                   identity_id=row[3], phase=phase)
    options.update(changes)
    return admission._observe_archive_admission(db, **options)


def test_exact_pre_post_witness_matches_without_committing(rig):
    _, db, row, args = rig
    assert _observe(db, row, args) is None
    db.execute("BEGIN IMMEDIATE")
    before = _observe(db, row, args)
    assert before is not None
    assert _observe(db, row, args, "archived") is None
    archive._change(db, **args, restore=False)
    assert _observe(db, row, args, "archived") == before
    assert _observe(db, row, args, "live") is None
    assert db.in_transaction
    db.rollback()
    assert archive._live_row(db, row[0]) == row


@pytest.mark.parametrize("change", ["file", "scope", "dependency", "profile_replaced"])
def test_pre_post_refuses_new_physical_or_sql_evidence(rig, change):
    _, db, row, args = rig
    path = Path(row[2])
    db.execute("BEGIN IMMEDIATE")
    before = _observe(db, row, args)
    assert before is not None
    archive._change(db, **args, restore=False)
    opts = {}
    if change == "file":
        path.write_bytes(b"returned")
    elif change == "scope":
        opts["identity_id"] = "b"
    elif change == "dependency":
        db.execute("INSERT INTO metadata(key,value) VALUES(?,?)", ("checkpoint", row[0]))
    else:
        path.parent.rename(path.parent.with_name("former"))
        path.parent.mkdir()
    assert _observe(db, row, args, "archived", **opts) != before
    db.rollback()
    assert archive._live_row(db, row[0]) == row


def test_other_private_archive_metadata_can_reference_live_candidate(rig):
    _, db, row, args = rig
    other = list(row)
    other[0] = "other"
    other[2] = str(Path(row[2]).with_name("another.epub"))
    other[8] = '{"dependency":"' + row[0] + '"}'
    payload, digest = archive._encode_archive_row(tuple(other))
    db.execute("BEGIN IMMEDIATE")
    db.execute(f"INSERT INTO {archive._TABLE} VALUES(?,?,?,?,?)",
               (other[0], other[3], other[2], digest, payload))
    assert _observe(db, row, args) is None
    assert archive._live_row(db, row[0]) == row
    db.rollback()
