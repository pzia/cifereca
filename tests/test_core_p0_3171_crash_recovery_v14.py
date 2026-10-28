"""#3171: durable receipt handoff after completed GET, before canonical B commit.

Real SQLite v14 + synthetic final PDF bytes and two-provider-capable Core
executor, zero network. Crash is injected after the journal commits but before
the Artifact/Placement/OriginLink transaction.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cifereca.core import convergence_execution
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_convergence_execution import (
    rig, run, fake_delivery,
)
from tests.test_core_p0_3171_atomic_publication_v14 import rows


def journal(storage):
    with storage._lock, storage._connect() as db:
        return db.execute(
            "SELECT status,artifact_id,metadata_json "
            "FROM acquisition_ledger WHERE acquisition_id LIKE 'canonical-intake:%'"
        ).fetchall()


def force_failure_once(monkeypatch):
    original = convergence_execution.publish_completed_acquisition
    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic_sql_crash_after_persisted_receipt")
    monkeypatch.setattr(convergence_execution, "publish_completed_acquisition", fail)
    return original


def test_post_get_sql_failure_recovers_same_receipt_no_second_get(rig, monkeypatch):
    service, account, source, snapshot, root, lock_path = rig
    original = force_failure_once(monkeypatch)
    delivery = fake_delivery(service, root)
    first = run(service, account, snapshot, root, lock_path, {source}, delivery)
    assert first[0].status == "failed"
    assert service.calls == 1
    assert rows(service.storage) == (0, 0, 0, 0)
    stage, = journal(service.storage)
    assert stage[0] == "canonical_receipt_staged" and stage[1] is None
    assert json.loads(stage[2])["source"] == [
        "A", "seller-a", "card-A", "buyer-A",
        "purchases", "edition", "purchase", "purchase",
    ]
    monkeypatch.setattr(
        convergence_execution, "publish_completed_acquisition", original,
    )
    second = run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("a persisted valid completed receipt avoids GET"),
    )
    assert [(x.status, x.reason) for x in second] == [
        ("recovered", "canonical_delivery_recovered")
    ]
    assert service.calls == 1
    assert rows(service.storage) == (1, 1, 1, 1)
    assert journal(service.storage)[0][0:2] == (
        "complete", "sha256:" + json.loads(stage[2])["sha256"],
    )
    third = run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("verified origin avoids GET"),
    )
    assert third[0].status == "present_attested"
    assert rows(service.storage) == (1, 1, 1, 1)


def test_recovery_does_not_override_consent_revocation(rig, monkeypatch):
    service, account, source, snapshot, root, lock_path = rig
    original = force_failure_once(monkeypatch)
    run(service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root))
    monkeypatch.setattr(convergence_execution, "publish_completed_acquisition", original)
    denied = run(
        service, account, snapshot, root, lock_path, set(),
        lambda *_: pytest.fail("revoked consent must prevent GET/recovery"),
    )
    assert denied[0].status == "pending"
    assert rows(service.storage) == (0, 0, 0, 0)
    resumed = run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("consented recovery must not GET"),
    )
    assert resumed[0].status == "recovered"
    assert service.calls == 1
    assert rows(service.storage) == (1, 1, 1, 1)


def test_modified_staged_bytes_require_fresh_delivery(rig, monkeypatch):
    service, account, source, snapshot, root, lock_path = rig
    original = force_failure_once(monkeypatch)
    run(service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root))
    monkeypatch.setattr(convergence_execution, "publish_completed_acquisition", original)
    (root / "A" / "book.pdf").write_bytes(b"%PDF-1.4\nAltered PDF bytes\n")
    after = run(
        service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root),
    )
    assert after[0].status == "downloaded"
    assert service.calls == 2
    assert rows(service.storage) == (1, 1, 1, 1)
    assert journal(service.storage)[0][0] == "complete"


def test_changed_offer_cannot_replay_previous_receipt(rig, monkeypatch):
    service, account, source, snapshot, root, lock_path = rig
    original = force_failure_once(monkeypatch)
    run(service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root))
    monkeypatch.setattr(convergence_execution, "publish_completed_acquisition", original)
    changed = replace(snapshot, rights=(replace(
        snapshot.rights[0], delivery_offer_id="fresh-independent-offer",
    ),))
    after = run(
        service, account, changed, root, lock_path, {source},
        fake_delivery(service, root),
    )
    assert after[0].status == "downloaded"
    assert service.calls == 2
    assert rows(service.storage) == (1, 1, 1, 1)
    assert len(journal(service.storage)) == 2


def test_tampered_journal_context_never_adopts_a_similar_file(rig, monkeypatch):
    service, account, source, snapshot, root, lock_path = rig
    original = force_failure_once(monkeypatch)
    run(service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root))
    monkeypatch.setattr(convergence_execution, "publish_completed_acquisition", original)
    with service.storage._lock, service.storage._connect() as db:
        row = db.execute(
            "SELECT acquisition_id,metadata_json FROM acquisition_ledger "
            "WHERE acquisition_id LIKE 'canonical-intake:%'"
        ).fetchone()
        data = json.loads(row[1])
        data["source"][0] = "B"
        db.execute(
            "UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
            (json.dumps(data), row[0]),
        )
    after = run(
        service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root),
    )
    assert after[0].status == "downloaded"
    assert service.calls == 2
    assert rows(service.storage) == (1, 1, 1, 1)


def test_disabled_connection_rejects_staged_recovery_and_get(rig, monkeypatch):
    service, account, source, snapshot, root, lock_path = rig
    original = force_failure_once(monkeypatch)
    run(service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root))
    monkeypatch.setattr(convergence_execution, "publish_completed_acquisition", original)
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "UPDATE provider_connections SET enabled=0 "
            "WHERE provider_id='seller-a' AND credential_id='card-A'"
        )
    with pytest.raises(ValueError, match="convergence_session_rebound"):
        run(
            service, account, snapshot, root, lock_path, {source},
            lambda *_: pytest.fail("disabled credential cannot GET"),
        )
    assert rows(service.storage) == (0, 0, 0, 0)
    assert service.calls == 1
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "UPDATE provider_connections SET enabled=1 "
            "WHERE provider_id='seller-a' AND credential_id='card-A'"
        )
    assert run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("active scope resumes exact prior delivery"),
    )[0].status == "recovered"
    assert rows(service.storage) == (1, 1, 1, 1)


def test_recovery_never_infers_provenance_from_manual_path(rig, monkeypatch):
    service, account, source, snapshot, root, lock_path = rig
    original = force_failure_once(monkeypatch)
    run(service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root))
    monkeypatch.setattr(convergence_execution, "publish_completed_acquisition", original)
    manual = root / "manual" / "book.pdf"
    manual.parent.mkdir(parents=True)
    manual.write_bytes((root / "A" / "book.pdf").read_bytes())
    with service.storage._lock, service.storage._connect() as db:
        row = db.execute(
            "SELECT acquisition_id,metadata_json FROM acquisition_ledger "
            "WHERE acquisition_id LIKE 'canonical-intake:%'"
        ).fetchone()
        data = json.loads(row[1])
        data["path"] = str(manual)
        db.execute(
            "UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
            (json.dumps(data), row[0]),
        )
    after = run(
        service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root),
    )
    assert after[0].status == "downloaded"
    assert service.calls == 2
    assert manual.exists()
    assert rows(service.storage) == (1, 1, 1, 1)


def test_normal_opening_recovery_never_destroys_completed_delivery_checkpoint(
    rig, monkeypatch,
):
    service, account, source, snapshot, root, lock_path = rig
    original = force_failure_once(monkeypatch)
    run(service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root))
    monkeypatch.setattr(
        convergence_execution, "publish_completed_acquisition", original,
    )
    before = journal(service.storage)
    assert len(before) == 1
    assert before[0][0] == "canonical_receipt_staged"
    # Opening Sync calls this existing generic recovery. It must not
    # classify a final receipt as an unfinished network acquisition.
    assert service.storage.acquisition_recover_interrupted() == 0
    assert journal(service.storage) == before
    recovered = run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("startup must not force a second GET"),
    )
    assert recovered[0].status == "recovered"
    assert service.calls == 1
    assert rows(service.storage) == (1, 1, 1, 1)


def test_inventory_evidence_is_never_a_completed_delivery_receipt(rig):
    service, account, source, snapshot, root, lock_path = rig
    from cifereca.core.convergence_execution import CompletedDelivery
    from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA
    def invalid(key, right):
        service.calls += 1
        path = root / "A" / "book.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BYTES)
        return CompletedDelivery(
            key, right.delivery_offer_id, path, SHA,
            right.evidence_ref,
        )
    first = run(service, account, snapshot, root, lock_path, {source}, invalid)
    assert first[0].status == "failed"
    assert journal(service.storage) == []
    assert rows(service.storage) == (0, 0, 0, 0)
    second = run(
        service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root),
    )
    assert second[0].status == "downloaded"
    assert service.calls == 2
    assert journal(service.storage)[0][0] == "complete"
    assert rows(service.storage) == (1, 1, 1, 1)


def test_failed_optional_journal_status_update_never_uncommits_canonical_B(
    rig, monkeypatch,
):
    service, account, source, snapshot, root, lock_path = rig
    def broken_mark(*_args, **_kwargs):
        raise RuntimeError("synthetic best-effort ledger status write failed")
    monkeypatch.setattr(
        convergence_execution, "mark_completed_delivery_published", broken_mark,
    )
    first = run(
        service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root),
    )
    assert first[0].status == "downloaded"
    assert journal(service.storage)[0][0] == "canonical_receipt_staged"
    assert rows(service.storage) == (1, 1, 1, 1)
    second = run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("canonical OriginLink B, not ledger status, avoids GET"),
    )
    assert second[0].status == "present_attested"
    assert service.calls == 1
