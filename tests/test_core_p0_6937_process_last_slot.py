"""#6937: independent processes share a single durable reservation budget."""
from dataclasses import replace
from hashlib import sha256
import multiprocessing
from pathlib import Path

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.storage import Storage
from tests.test_core_p0_4520_derived_journal import _prepared_publication, _publish_reserved


def _reserve_last_slot(database, intent, budget, ready, results):
    """Spawned process, its own Storage/connection, no inherited locks."""
    db = None
    try:
        db = Storage(database)
        if budget == "records":
            journal._MAX_RECORDS = 2
        else:
            journal._MAX_RESERVED_OUTPUT_BYTES = 18
        ready.wait(timeout=20)
        try:
            reservation = journal.reserve_derived_publication(db, intent)
        except journal.DerivedJournalConflict as exc:
            results.put(("refused", str(exc)))
        else:
            results.put(("reserved", reservation.key))
    except Exception as exc:
        results.put(("unexpected", type(exc).__name__))
    finally:
        if db is not None:
            db.close()


@pytest.mark.parametrize("budget,refusal", [
    ("records", "derived_journal_capacity_exceeded"),
    ("bytes", "derived_journal_output_capacity_exceeded"),
])
def test_last_slot_two_processes_preserves_committed_history_on_restart(
    tmp_path, budget, refusal,
):
    service, intent, committed = _prepared_publication(tmp_path)
    db = service.storage
    try:
        original_path = Path(intent.parent_path)
        original = original_path.read_bytes()
        placement_id = _publish_reserved(db, intent, committed)
        committed_key = journal._COMMITTED + committed.receipt_sha256
        committed_raw = db.metadata_get(committed_key)
        assert committed_raw is not None
        database = db.path
        attempts = []
        for suffix in ("A", "B"):
            stage = Path(intent.staged_path).with_name("last-" + suffix + ".epub")
            candidate = Path(intent.candidate_path).with_name("last-" + suffix + ".epub")
            contents = ("second-" + suffix + "!").encode("ascii")
            assert len(contents) == intent.output_size == 9
            stage.write_bytes(contents)
            info = stage.stat()
            attempts.append(replace(
                intent, staged_path=str(stage), candidate_path=str(candidate),
                options_fingerprint="last-" + suffix,
                output_sha256=sha256(contents).hexdigest(), output_size=info.st_size,
                output_stamp=(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns),
            ))
        before = journal.summarize_derived_journal(db)
        assert before["committed_count"] == 1 and before["pending_count"] == 0
        assert before["committed_output_bytes"] == 9
    finally:
        db.close()

    context = multiprocessing.get_context("spawn")
    ready = context.Barrier(3)
    results = context.Queue()
    processes = [context.Process(
        target=_reserve_last_slot, args=(database, item, budget, ready, results),
    ) for item in attempts]
    started = []
    try:
        for process in processes:
            process.start()
            started.append(process)
        ready.wait(timeout=20)
        outcomes = [results.get(timeout=20), results.get(timeout=20)]
        for process in started:
            process.join(timeout=20)
            assert process.exitcode == 0
    finally:
        for process in started:
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
        results.close()
        results.join_thread()

    winners = [value for status, value in outcomes if status == "reserved"]
    assert len(winners) == 1, outcomes
    assert [value for status, value in outcomes if status == "refused"] == [refusal]

    db = Storage(database)
    try:
        summary = journal.summarize_derived_journal(db)
        assert (summary["pending_count"], summary["committed_count"]) == (1, 1)
        assert summary["reserved_output_bytes"] == 18
        assert db.metadata_get(committed_key) == committed_raw
        assert db.metadata_get(winners[0]) is not None
        assert len(db.placements()) == 2
        assert db.placement_get(placement_id)["path"] == intent.candidate_path
        assert Path(intent.staged_path).samefile(intent.candidate_path)
        assert Path(intent.candidate_path).read_bytes() == b"new bytes"
        assert original_path.read_bytes() == original
        for item in attempts:
            assert sha256(Path(item.staged_path).read_bytes()).hexdigest() == item.output_sha256
            assert not Path(item.candidate_path).exists()
        with db._lock, db._connect() as sql:
            assert sql.execute(
                "SELECT count(*) FROM origin_links WHERE origin_kind='derived'"
            ).fetchone() == (1,)
        assert not summary["recovery_authorized"]
        assert not summary["destructive_cleanup_allowed"]
    finally:
        db.close()
