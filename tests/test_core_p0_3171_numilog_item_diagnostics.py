"""#3171: privacy-safe per-purchase preflight decisions on real Core/SQLite.

All accounts, rights, books and files are synthetic. No HTTP/ADEPT/acquisition.
"""
from __future__ import annotations

import json
import logging

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.numilog_materialization_startup import execute
from cifereca.core.numilog_startup_diagnostics import public_fields
from cifereca.gui.numilog_materialization_status import describe_run
from cifereca.plugin_api import SyncResult
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_1862_numilog_store_mirror import _row


def test_three_real_preflight_refusal_reasons_are_disclosed_per_manifest_position(
    tmp_path, monkeypatch, caplog,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        rows = [_row(account.credential_id, index) for index in range(3)]
        session.library = lambda: rows
        reasons = {
            rows[0].record_id: "needs_reconciliation",
            rows[1].record_id: "purchase_unproven",
            rows[2].record_id: "source_not_ready",
        }
        monkeypatch.setattr(
            SynchronizationBrokerImpl, "preflight_numilog_purchase",
            lambda _broker, _source, item_id: SyncResult(
                ok=False, facts={"status": reasons[item_id]}
            ),
        )

        with caplog.at_level(logging.INFO, logger="cifereca"):
            result = execute(broker, account.identity_id, "startup")
        assert result.ok is False
        assert result.facts["status"] == "failed"
        assert result.facts["attempted"] == 2
        assert result.facts["downloaded"] == 0
        assert result.facts["failed"] == 2
        assert result.facts["blocked"] == 1
        expected = [
            {"position": 1, "decision": "acquisition_failed"},
            {"position": 2, "decision": "acquisition_failed"},
            {"position": 3, "decision": "source_not_ready"},
        ]
        assert result.facts["source_results"][0]["item_decisions"] == expected
        persisted = broker.numilog_materialization_run_status(sid)
        detail = persisted["diagnostic"]
        assert detail["item_decisions"] == expected
        assert detail["code"] == "acquisition_delivery_failed"
        assert detail["status"] == "failed"
        caption = describe_run("on_start", persisted)
        assert "n°1" in caption
        assert "n°2" in caption
        assert "n°3 : connexion non prête" in caption
        assert "rangs de cet inventaire uniquement" in caption
        for private in (account.credential_id, *[r.record_id for r in rows]):
            assert private not in json.dumps(detail)
            assert private not in caption
        # The UI's bounded rank view remains unchanged. The raw journal must
        # also identify the exact purchase observed in THIS authenticated run,
        # so a later manifest reordering cannot change the forensic meaning.
        item_events = [
            record.getMessage() for record in caplog.records
            if record.getMessage().startswith("TRACE numilog purchase decision")
        ]
        assert len(item_events) == 3
        for position, (row, reason) in enumerate(zip(rows,
                ("acquisition_failed", "acquisition_failed", "source_not_ready")), 1):
            event = item_events[position - 1]
            assert "provider_id=numilog" in event
            assert f"source_id={sid}" in event
            assert f"item_id={row.record_id}" in event
            assert f"position={position}" in event
            assert f"decision={reason}" in event
            assert "run_ref=" in event and "task_ref=" in event
        assert calls == ["FORBIDDEN DOWNLOAD", "FORBIDDEN DOWNLOAD"]
        assert service.storage.acquisitions(provider_id="numilog") == []
    finally:
        service.close()


@pytest.mark.parametrize("invalid", [
    [{"position": 1, "decision": "PRIVATE_SECRET"}],
    [{"position": 0, "decision": "needs_reconciliation"}],
    [{"position": True, "decision": "needs_reconciliation"}],
    [{"position": 2, "decision": "needs_reconciliation"},
     {"position": 1, "decision": "purchase_unproven"}],
    [{"position": 1, "decision": "needs_reconciliation"},
     {"position": 1, "decision": "purchase_unproven"}],
    [{"position": 1, "decision": "needs_reconciliation", "title": "PRIVATE_SECRET"}],
    [{"position": i, "decision": "preflight_failed"} for i in range(1, 10)],
    "PRIVATE_SECRET",
])
def test_item_decision_schema_never_echoes_untrusted_private_data(invalid):
    valid = dict(
        phase="completed", cause_phase="preflight",
        code="preflight_failed", status="blocked",
        task_kind="source-materialize", attempted=0,
        downloaded=0, skipped=0, failed=0,
        item_decisions=invalid,
    )
    assert public_fields(valid) == {}


def test_item_decisions_are_optional_for_old_diagnostic_records():
    valid = dict(
        phase="completed", cause_phase="preflight",
        code="preflight_failed", status="blocked",
        task_kind="source-materialize", attempted=0,
        downloaded=0, skipped=0, failed=0,
    )
    assert public_fields(valid) == valid


def test_truncation_and_empty_item_decisions_cannot_invent_per_book_evidence():
    base = dict(
        phase="completed", cause_phase="preflight",
        code="preflight_failed", status="blocked",
        task_kind="source-materialize", attempted=0,
        downloaded=0, skipped=0, failed=0,
    )
    assert public_fields({**base, "items_truncated": True}) == {}
    assert public_fields({**base, "item_decisions": [], "items_truncated": True}) == {}
    eight = [{"position": i, "decision": "preflight_failed"} for i in range(1, 9)]
    assert public_fields({**base, "item_decisions": eight,
                          "items_truncated": True}) != {}
