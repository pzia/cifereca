"""#6937: independent processes share a single durable reservation budget."""
from dataclasses import asdict, replace
from hashlib import sha256
import json
import selectors
import subprocess
import sys
from pathlib import Path

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.storage import Storage
from tests.test_core_p0_4520_derived_journal import _prepared_publication, _publish_reserved


# Independent interpreter avoids pytest/multiprocessing module import ambiguity.
# Initialize each connection before releasing the two competitors.
_WORKER = r"""
import json
from pathlib import Path
import sys
from cifereca.core import derived_variant_journal as journal
from cifereca.storage import Storage

db = Storage(Path(sys.argv[1]))
try:
    fields = json.loads(sys.argv[2])
    fields["parent_stamp"] = tuple(fields["parent_stamp"])
    fields["output_stamp"] = tuple(fields["output_stamp"])
    intent = journal.DerivedIntent(**fields)
    if sys.argv[3] == "records":
        journal._MAX_RECORDS = 2
    else:
        journal._MAX_RESERVED_OUTPUT_BYTES = 18
    print("ready", flush=True)
    assert sys.stdin.readline().strip() == "reserve"
    try:
        reservation = journal.reserve_derived_publication(db, intent)
    except journal.DerivedJournalConflict as exc:
        result = ["refused", str(exc)]
    else:
        result = ["reserved", reservation.key]
    print(json.dumps(result), flush=True)
finally:
    db.close()
"""


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

    processes = []
    outcomes = []
    try:
        for item in attempts:
            process = subprocess.Popen(
                [sys.executable, "-c", _WORKER, str(database),
                 json.dumps(asdict(item)), budget],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            processes.append(process)
            # Bounded startup; inspect child errors rather than masking them
            # with a broken parent barrier.
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=20), "reservation worker startup timeout"
            assert process.stdout.readline().strip() == "ready", (
                process.stderr.read() if process.poll() is not None else
                "worker did not initialize"
            )
        for process in processes:
            process.stdin.write("reserve\n")
            process.stdin.flush()
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr
            outcomes.append(json.loads(stdout))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)

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
