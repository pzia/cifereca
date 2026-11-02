"""P1 #4142: Core source Sync must respect exact BnF rights coverage.

Offline integration: real v14 SQLite + canonical A observation and physical
SHA/OriginLink checks; synthetic provider session and loan coordinates only.
This does not substitute for a private authenticated BnF acceptance run.
"""
from dataclasses import replace

import pytest

from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.models import Account
from cifereca.storage import Storage
from tests.test_core_p0_3171_canonical_sync_stage import Service
from tests.test_core_p0_3171_atomic_publication_v14 import (
    SHA, STAMP, new_source, register, target,
)


@pytest.mark.parametrize(
    ("present_count", "coverage_complete", "expected_status", "expected_pending"),
    [
        (4, True, "ok", 0),
        (4, False, "partial", 0),
        (3, True, "partial", 1),
    ],
)
def test_four_bnf_loans_source_complete_only_when_coverage_and_local_receipts(
    tmp_path, monkeypatch, present_count, coverage_complete,
    expected_status, expected_pending,
):
    db = Storage(tmp_path / "db.sqlite")
    account = Account(
        identity_id="A", provider_id="bnf-pret",
        credential_id="bnf-reader", unique_id="bnf-reader",
    )
    register(db, "A", "bnf-pret", "bnf-reader", account="bnf-reader")
    root = tmp_path / "EBooks"
    service = Service(db, root)
    service.account = account
    service.accounts = [account]
    service.session.provider_id = "bnf-pret"
    service.session.credential_id = "bnf-reader"
    service.session.accounts = [account]
    service._sessions = {service._session_key("bnf-pret", "bnf-reader"): service.session}
    sources = [
        new_source(
            "A", provider="bnf-pret", credential="bnf-reader",
            account="bnf-reader", branch="bnf_html",
            edition=f"edition-{i}", right=f"loan-{i}", kind="loan",
        )
        for i in range(4)
    ]
    service.source = sources[0][0]
    all_rights = tuple(snap.rights[0] for _, snap in sources)
    service.snapshot = replace(
        sources[0][1], rights=all_rights,
        status="success" if coverage_complete else "partial",
        coverage="complete" if coverage_complete else "partial",
        pagination_complete=coverage_complete,
        observed_at=STAMP, snapshot_ref="bnf_html.active_loans",
    )
    for name in (
        "cifereca.core.sync_coordination.acquisition_lock_file",
        "cifereca.core.convergence_execution.acquisition_lock_file",
        "cifereca.core.canonical_recovery.acquisition_lock_file",
    ):
        monkeypatch.setattr(name, lambda: tmp_path / "acquisition.lock")
    try:
        for i, (source, individual) in enumerate(sources[:present_count]):
            path = target(tmp_path, "A", f"loan-{i}.pdf")
            publish_completed_acquisition(
                db, library_root=root, path=path, source=source,
                snapshot=individual, final_sha256=SHA,
                delivery_receipt_ref=f"synthetic-bnf-delivery-{i}",
            )
        before = (db.acquisitions(), db.placements())
        result = synchronize_account_source(service, account)
        assert result["status"] == expected_status
        assert result["canonical_rights"]["status"] == (
            "success" if coverage_complete else "partial"
        )
        assert result["canonical_rights"]["observed"] == 4
        assert result["canonical_rights"]["present_attested"] == present_count
        assert result["canonical_rights"]["pending"] == expected_pending
        assert service.read_calls == 1
        assert service.events[-1] == (
            "A", "synchronization",
            "success" if expected_status == "ok" else "partial",
        )
        assert db.acquisitions() == before[0]  # no ACSM/borrow/re-fulfillment
        assert db.placements() == before[1]    # no file/deletion/move
        assert len(db.placements()) == present_count
    finally:
        db.close()
