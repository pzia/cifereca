"""#4708: archive witness round-trip only; never retire a Placement."""
import hashlib
import json

import pytest

from cifereca.core import missing_placement_archive_envelope as envelope
from cifereca.storage import Storage
from tests.test_core_p0_4708_historical_missing_report import _setup


def test_exact_sqlite_row_roundtrip_preserves_metadata_and_nullable_fields(tmp_path):
    database, _config, root, ids = _setup(tmp_path)
    store = Storage(database)
    try:
        with store._lock, store._connect() as sql:
            pid = ids["unlinked.epub"]
            metadata = '{ "unicode": "é", "list": [1, null], "escaped": "\\u0061" }'
            sql.execute("UPDATE placements SET metadata_json=?, provider_id=NULL, last_seen_at=NULL "
                        "WHERE placement_id=?", (metadata, pid))
            row = sql.execute(
                "SELECT " + ",".join(envelope._COLUMNS) + " FROM placements WHERE placement_id=?",
                (pid,),
            ).fetchone()
        before = store.placements()
        payload, digest = envelope._encode_archive_row(row)
        assert envelope._decode_archive_row(payload, digest) == row
        assert row[8] == metadata and row[4] is None and row[7] is None
        assert store.placements() == before
        assert not (root / "a" / "unlinked.epub").exists()
    finally:
        store.close()


def _row():
    return ("p", "sha256:" + "a" * 64, "/library/a/book.epub", "a",
            None, "", "missing", None, '{"x":1}', 1, 2)


@pytest.mark.parametrize("index,value", [
    (6, "present"), (2, "/a/../book.epub"), (3, None),
    (4, 7), (7, True), (9, 2 ** 63), (8, '{"x":1,"x":2}'),
    (8, "[]"), (8, '{"x":NaN}'), (8, "broken"),
])
def test_ineligible_or_ambiguous_rows_are_refused(index, value):
    row = list(_row())
    row[index] = value
    with pytest.raises(ValueError, match="missing_archive_invalid_envelope"):
        envelope._encode_archive_row(tuple(row))


def test_tamper_and_noncanonical_payload_are_refused():
    payload, digest = envelope._encode_archive_row(_row())
    with pytest.raises(ValueError, match="missing_archive_invalid_envelope"):
        envelope._decode_archive_row(payload + b" ", digest)
    changed = json.loads(payload)
    changed["schema"] = "unknown"
    raw = json.dumps(changed).encode()
    with pytest.raises(ValueError, match="missing_archive_invalid_envelope"):
        envelope._decode_archive_row(raw, hashlib.sha256(raw).hexdigest())
    raw = payload + b" "
    with pytest.raises(ValueError, match="missing_archive_invalid_envelope"):
        envelope._decode_archive_row(raw, hashlib.sha256(raw).hexdigest())


def test_payload_limit_is_not_silent_truncation(monkeypatch):
    monkeypatch.setattr(envelope, "_MAX_BYTES", 128)
    with pytest.raises(ValueError, match="missing_archive_invalid_envelope"):
        envelope._encode_archive_row(_row())
