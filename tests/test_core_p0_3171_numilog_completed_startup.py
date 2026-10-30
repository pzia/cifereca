"""P0 #3171: a fully indexed Numilog purchase is present, not pending.

Synthetic provider receipt, EPUB and SQLite from existing Core fixtures.
"""
from __future__ import annotations

from time import time

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core import numilog_materialization_startup as startup
from tests.test_core_p0_numilog_auto_materialization import _authorize
from tests.test_numilog_owned_library_download import case


def _completed_purchase(case):
    target = case.service.owned_library_download_target(case.entry)
    assert target is not None
    result = case.service.obtain_owned_library_item(target)
    assert result.path.is_file() and result.indexed["status"] == "indexed"
    broker = SynchronizationBrokerImpl(case.service)
    source_id = broker._source_id(case.account)
    assert broker.preflight_numilog_purchase(
        source_id, case.item.record_id,
    ).facts["status"] == "needs_reconciliation"
    preview = broker.preview_local_materialization(
        source_id, verify_local_files=True,
    )
    assert preview.ok and preview.facts["counts"]["already_local_verified"] == 1
    _authorize(broker, source_id)
    case.session.library = lambda: [case.item]
    case.session.download_direct = lambda *_a, **_k: pytest.fail(
        "a verified existing purchase must never be downloaded again"
    )
    return broker, source_id, result.path


def test_completed_purchase_is_skipped_on_two_independent_startups(case, monkeypatch):
    broker, sid, path = _completed_purchase(case)
    initial = path.read_bytes()
    original_downloads = tuple(case.calls)

    first = startup.execute(broker, case.account.identity_id, "startup")
    assert first.ok and first.facts["status"] == "complete"
    assert first.changed == first.facts["attempted"] == first.facts["blocked"] == 0
    assert first.facts["skipped"] == 1
    assert broker.numilog_materialization_run_status(sid)["status"] == "complete"

    monkeypatch.setattr(startup, "time", lambda: time() + 7200)
    second = startup.execute(broker, case.account.identity_id, "startup")
    assert second.ok and second.facts["status"] == "complete"
    assert second.changed == second.facts["attempted"] == second.facts["blocked"] == 0
    assert second.facts["skipped"] == 1
    assert tuple(case.calls) == original_downloads
    assert path.read_bytes() == initial
    assert len(case.service.storage.acquisitions(provider_id="numilog")) == 1


@pytest.mark.parametrize("damage", [
    "missing_file", "changed_bytes", "foreign_receipt", "second_receipt",
])
def test_completed_purchase_uncertain_evidence_stays_blocked(case, damage):
    broker, sid, path = _completed_purchase(case)
    if damage == "missing_file":
        path.unlink()
    elif damage == "changed_bytes":
        path.write_bytes(b"%PDF-1.7\nWRONG BYTES\n")
    elif damage == "foreign_receipt":
        with case.service.storage._lock, case.service.storage._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET account_id=? WHERE provider_id='numilog'",
                ("foreign-account",),
            )
    else:
        case.service.storage.acquisition_put({
            "acquisition_id": "second-ledger",
            "provider_id": "numilog",
            "identity_id": case.account.identity_id,
            "credential_id": case.account.credential_id,
            "account_id": case.account.unique_id,
            "item_id": case.item.record_id,
            "status": "attention_required",
        })

    previous_downloads = tuple(case.calls)
    requests = []
    if damage in {"missing_file", "changed_bytes"}:
        # Presence is no longer a permanent download veto. A verified right,
        # opted-in source and fresh downloadable offer permit a new GET.
        def unavailable(*_a, **_kw):
            requests.append(True)
            raise RuntimeError("synthetic vendor outage")
        case.session.download_direct = unavailable
    result = startup.execute(broker, case.account.identity_id, "startup")
    assert not result.ok
    if damage in {"missing_file", "changed_bytes"}:
        assert result.facts["status"] == "failed"
        assert result.facts["attempted"] == result.facts["failed"] == 1
        assert result.facts["blocked"] == 0
        assert requests == [True]
    else:
        assert result.facts["status"] == "blocked"
        assert result.facts["blocked"] == 1
        assert result.facts["attempted"] == 0
        assert requests == []
    assert result.changed == 0
    assert tuple(case.calls) == previous_downloads
