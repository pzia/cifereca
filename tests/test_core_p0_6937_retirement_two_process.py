"""#6937: two independent processes cannot retire one private stage twice.

Synthetic Core/SQLite/FS only. The production consumer fence remains False;
each child replaces it solely within its disposable fixture interpreter.
"""
import json
from pathlib import Path
import selectors
import subprocess
import sys

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_private_retirement as retirement
from tests.test_core_p0_6937_private_admission import rig
from tests.test_core_p0_6937_private_retirement import (
    isolated_fence, _published, _prepare, _unchanged,
)


_WORKER = r"""
import json
from pathlib import Path
import sys
from time import monotonic, sleep

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_private_retirement as retirement
from cifereca.core import derived_variant_supervised_recovery as recovery
from cifereca.locking import InterProcessLock, LockBusyError
from cifereca.storage import Storage

database, root, lock_file, receipt, binding, intent = sys.argv[1:]
root, lock_file = Path(root), Path(lock_file)
recovery.acquisition_lock_file = lambda create=True: lock_file
# Only the disposable child process trusts this exact test boundary.
retirement._exclusive_consumer_fence_current = (
    lambda storage, r, identity, lock: (
        identity == "id1" and r == root
        and Path(storage.path) == Path(database)
        and lock.strict_current()
    )
)
storage = Storage(Path(database))
try:
    print("ready", flush=True)
    assert sys.stdin.readline().strip() == "retire"
    deadline = monotonic() + 10
    while monotonic() < deadline:
        try:
            with InterProcessLock(lock_file, existing_nofollow=True) as lock:
                try:
                    result = retirement.retire_private_stage(
                        storage, library_root=root, identity_id="id1",
                        receipt_sha256=receipt, approved_binding_sha256=binding,
                        approved_intent_sha256=intent, shared_lock=lock,
                        scope_current=lambda sql: sql.in_transaction,
                        explicitly_requested=True,
                    )
                except journal.DerivedJournalConflict as exc:
                    outcome = ["refused", str(exc)]
                else:
                    outcome = ["retired", result["outcome"]]
            print(json.dumps(outcome), flush=True)
            break
        except LockBusyError:
            sleep(0.01)
    else:
        raise AssertionError("lock not acquired within bounded retry window")
finally:
    storage.close()
"""


def test_one_retirement_wins_and_other_process_cannot_replay(
        rig, isolated_fence):
    storage, original_intent, stage, options = _published(rig)
    approved = _prepare(storage, options)
    receipt = options["receipt_sha256"]
    key = retirement._PREFIX + receipt
    raw_prepared = storage.metadata_get(key)
    quota = journal.summarize_derived_journal(storage)
    final = Path(original_intent.candidate_path)
    original = Path(original_intent.parent_path).read_bytes()
    content = final.read_bytes()
    assert stage.samefile(final) and raw_prepared is not None

    lock = options["shared_lock"]
    lock_file = lock.path
    lock_bytes = lock_file.read_bytes()
    assert lock._handle is not None
    lock.release()  # Transfer the fixture's lease before competing children.
    assert lock._handle is None
    assert not storage._connect().in_transaction

    arguments = (
        str(storage.path), str(options["library_root"]), str(lock_file),
        receipt, options["approved_binding_sha256"], approved["approved_intent_sha256"],
    )
    processes = []
    outcomes = []
    try:
        for _ in range(2):
            child = subprocess.Popen(
                [sys.executable, "-c", _WORKER, *arguments],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            processes.append(child)
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=20), "retirement worker startup timeout"
            assert child.stdout.readline().strip() == "ready", (
                child.stderr.read() if child.poll() is not None else
                "worker did not initialize")
        for child in processes:
            child.stdin.write("retire\n")
            child.stdin.flush()
        for child in processes:
            output, error = child.communicate(timeout=20)
            assert child.returncode == 0, error
            outcomes.append(json.loads(output.strip()))
    finally:
        for child in processes:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)

    assert [status for status, _ in outcomes].count("retired") == 1, outcomes
    assert [status for status, _ in outcomes].count("refused") == 1, outcomes
    assert [reason for status, reason in outcomes if status == "refused"] == [
        "derived_retirement_intent_changed"]
    assert lock_file.read_bytes() == lock_bytes
    assert not stage.exists() and final.read_bytes() == content
    assert final.stat().st_nlink == 1
    assert Path(original_intent.parent_path).read_bytes() == original
    trace = retirement._decode_trace(key, storage.metadata_get(key))
    assert trace["state"] == "retired" and trace["outcome"] == "unlink_confirmed"
    assert journal.summarize_derived_journal(storage) == quota
    _unchanged(storage, original_intent, quota, receipt, content)
