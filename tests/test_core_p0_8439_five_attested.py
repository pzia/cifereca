"""#8439: five verified B presences cannot certify incomplete A coverage.

Offline synthetic source; real Core publication, SQLite, Sync broker and
TaskRunner lifecycle (deterministic GTK delivery double). No provider I/O.
"""
from dataclasses import replace
import hashlib
import logging
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.diagnostic_source_sync import CANONICAL_EXECUTION_STATUSES
from cifereca.core.source_convergence import SourceKey
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, STAMP
from tests.test_core_p0_3171_canonical_sync_stage import rig
from tests.test_core_p0_981_redacted_task_traces import _runner


@pytest.mark.parametrize("a_status", ["partial", "success"])
def test_five_attested_presences_do_not_override_a_status(rig, caplog, a_status):
    service, db, root = rig
    seed = service.snapshot
    rows = tuple(
        replace(seed.rights[0], edition_id=f"edition-{i}", right_id=f"right-{i}",
                evidence_ref=f"synthetic-right-proof-{i}")
        for i in range(5)
    )
    complete = a_status == "success"
    service.snapshot = replace(
        seed, rights=rows, status=a_status,
        coverage="complete" if complete else "partial",
        pagination_complete=complete, observed_at=STAMP,
        snapshot_ref="synthetic-inventory-proof",
    )
    sources = tuple(SourceKey(seed.scope, row.edition_id, row.right_id,
                              row.kind, seed.branch) for row in rows)
    # Seed already-completed synthetic publications, not new acquisitions
    # during the operation being tested. Each B has its own bytes and SHA.
    for i, key in enumerate(sources):
        path = root / "A" / "Acquisitions" / f"prior-{i}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = BYTES + f"\n% synthetic prior {i}\n".encode("ascii")
        path.write_bytes(data)
        publish_completed_acquisition(
            db, library_root=root, path=path, source=key,
            snapshot=service.snapshot,
            final_sha256=hashlib.sha256(data).hexdigest(),
            delivery_receipt_ref=f"synthetic-prior-delivery-{i}",
        )
    prior = db.placements()
    assert len(prior) == 5
    service._canonical_persisted_consent = lambda _account: frozenset(sources)

    def unexpected_delivery(*_args, **_kwargs):
        pytest.fail("verified B must never trigger another delivery")

    service._canonical_right_delivery = unexpected_delivery
    service.session.capabilities = NS(personal_library=True)
    service.accounts_for_identity = lambda identity_id=None: (
        [service.account] if identity_id in (None, "A") else []
    )
    service._synchronize_account_source_impl = lambda *_a, **_kw: {
        "status": "ok", "library": 5,
    }
    core_results = []

    def core_sync(account, **kwargs):
        result = synchronize_account_source(service, account, **kwargs)
        core_results.append(result)
        return result

    service.synchronize_account_source = core_sync
    broker = SynchronizationBrokerImpl(service)
    source_id = broker._source_id(service.account)
    runner, displayed, flush, logger = _runner()
    receipts = []
    errors = []
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Synthetic Sync", lambda: broker.synchronize_source(source_id),
                kind="source-sync", origin="user",
                ok=receipts.append, err=errors.append,
            )
            flush()
        assert not errors and len(core_results) == len(receipts) == 1
        core, receipt = core_results[0], receipts[0]
        counts = dict.fromkeys(CANONICAL_EXECUTION_STATUSES, 0)
        counts["present_attested"] = 5
        assert core["canonical_rights"]["status"] == a_status
        assert core["canonical_rights"]["observed"] == 5
        assert core["canonical_rights"]["present_attested"] == 5
        assert core["canonical_rights"]["pending"] == 0
        assert core["canonical_execution"] == counts
        assert core["status"] == ("ok" if complete else "partial")
        assert receipt.ok is complete
        assert receipt.facts["status"] == ("ok" if complete else "partial")
        assert receipt.facts["canonical_execution"] == counts
        state = broker._state(source_id)
        assert state["last_cycle"]["status"] == ("success" if complete else "partial")
        assert state["last_cycle"]["canonical_execution"] == counts
        assert bool(state.get("last_success")) is complete
        assert bool(state.get("inventory_last_success")) is complete
        assert f"business_status={a_status}" in caplog.text
        finished = [args for event, args in displayed if event == "finished"]
        assert len(finished) == 1 and finished[0][3] is complete
        if not complete:
            assert finished[0][-1] == "partial"
        assert db.placements() == prior
        assert service.read_calls == 1
    finally:
        runner.close()
