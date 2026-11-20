"""#773: generated real EPUB/PDF bytes through cancelled canonical intake."""
from hashlib import sha256
from pathlib import Path

import pytest

from cifereca.core import convergence_execution as execution
from cifereca.locking import InterProcessLock
from cifereca.storage import Storage
from tests.test_core_p0_3171_convergence_execution import rig
from tests.test_core_p0_3171_atomic_publication_v14 import rows
from tests.test_core_p0_3171_crash_recovery_v14 import journal


@pytest.mark.parametrize("fixture", ["optimizer-valid.epub", "publication-text.pdf"])
@pytest.mark.parametrize("boundary", ["delivery", "receipt", "promotion"])
def test_cancel_then_restart_exact_offer(rig, tmp_path, monkeypatch, fixture, boundary):
    service, account, source, snapshot, root, lock_path = rig
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "simulation"))
    from plugin_scenarios import create

    dataset = tmp_path / "dataset"
    manifest = create(dataset)
    original = dataset / fixture
    payload = original.read_bytes()
    digest = sha256(payload).hexdigest()
    assert manifest["files"][fixture] == digest
    stopped = False
    calls = 0
    stage = root / "A" / ".canonical-delivery" / ("run-" + "a" * 32) / fixture

    def deliver(key, right):
        nonlocal stopped, calls
        calls += 1
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_bytes(payload)
        if boundary == "delivery":
            stopped = True
        return execution.CompletedDelivery(key, right.delivery_offer_id, stage,
                                           digest, "synthetic-completed-receipt")

    def wrap(name):
        original_call = getattr(execution, name)

        def cancel_after(*args, **kwargs):
            nonlocal stopped
            result = original_call(*args, **kwargs)
            stopped = True
            return result

        return original_call, cancel_after

    name = {"receipt": "record_completed_delivery_for_recovery",
            "promotion": "promote_completed_delivery"}.get(boundary)
    if name:
        actual, wrapped = wrap(name)
        monkeypatch.setattr(execution, name, wrapped)

    def run(delivery):
        with InterProcessLock(lock_path) as lease:
            return execution.execute_admitted_convergence(
                service, account, service._sessions[("seller-a", "card-A")],
                snapshot, admitted_epoch=service._source_context_epoch,
                shared_lock=lease, library_root=root,
                persisted_consent=frozenset({source}),
                consent_still_current=lambda key: key == source,
                deliver=delivery, cancelled=lambda: stopped,
            )

    first = run(deliver)
    assert first[0].status == "cancelled"
    expected = {"delivery": "cancelled_after_get", "receipt": "cancelled_after_receipt",
                "promotion": "cancelled_after_promotion"}
    assert first[0].reason == expected[boundary]
    assert rows(service.storage) == (0, 0, 0, 0)
    assert len(journal(service.storage)) == int(boundary != "delivery")
    assert stage.read_bytes() == payload
    assert original.read_bytes() == payload
    if name:
        monkeypatch.setattr(execution, name, actual)
    # Reopen the actual database; do not reuse in-memory publication evidence.
    prior = service.storage
    database = prior.path
    prior.close()
    service.storage = Storage(database)
    try:
        stopped = False

        def redeliver(key, right):
            nonlocal calls
            calls += 1
            return execution.CompletedDelivery(key, right.delivery_offer_id, stage,
                                               digest, "synthetic-completed-receipt")

        second = run(redeliver if boundary == "delivery" else
                     lambda *_: pytest.fail("durable receipt must avoid GET"))
        assert second[0].status == ("downloaded" if boundary == "delivery" else "recovered")
        assert calls == (2 if boundary == "delivery" else 1)
        assert rows(service.storage) == (1, 1, 1, 1)
        placement, = service.storage.placements()
        final = Path(placement["path"])
        assert final.parent == root / "A" / "Acquisitions"
        assert final.read_bytes() == payload == original.read_bytes()
        assert run(lambda *_: pytest.fail("verified origin must avoid GET"))[0].status == "present_attested"
        assert rows(service.storage) == (1, 1, 1, 1)
    finally:
        service.storage.close()
