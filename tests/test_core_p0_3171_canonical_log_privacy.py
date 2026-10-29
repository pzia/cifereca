"""P0 #3171: canonical Sync failures must not export secrets through Core logs.

This suite uses only synthetic SQLite/providers/bytes and injected failures.
The closed reason code is sufficient for diagnostics; a provider exception,
signed delivery URL, consent token or borrower identifier is never logged.
"""
from __future__ import annotations

import logging

import pytest

from cifereca.core.convergence_execution import CompletedDelivery
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA, rows
from tests.test_core_p0_3171_canonical_sync_stage import rig as source_rig
from tests.test_core_p0_3171_convergence_execution import (
    rig as executor_rig, run, fake_delivery,
)

PRIVATE = "PRIVATE_TOKEN_AND_SIGNED_ACSM_URL_9278"


def _fail(*_args, **_kwargs):
    raise RuntimeError(PRIVATE)


def _logged_code(caplog, code):
    matching = [record for record in caplog.records if code in record.getMessage()]
    assert matching, f"missing closed diagnostic: {code}"
    assert all(record.exc_info is None for record in matching)
    for record in matching:
        text = record.getMessage()
        assert PRIVATE not in text
        assert "credential_id=" not in text
        assert "account_id=" not in text
        assert "right_id=" not in text
    assert PRIVATE not in caplog.text
    assert "Traceback (most recent call last)" not in caplog.text


def test_unbound_positive_source_logs_no_borrower_or_credential(
    source_rig, caplog,
):
    service, db, _root = source_rig
    with db._lock, db._connect() as sql:
        sql.execute(
            "DELETE FROM provider_accounts WHERE identity_id='A' "
            "AND provider_id='seller-a' AND credential_id='card-a'"
        )
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        result = synchronize_account_source(service, service.account)
    assert result["status"] == "partial"
    assert result["canonical_rights"]["status"] == "failed"
    _logged_code(caplog, "canonical_rights_core_binding_changed")
    message = next(r.getMessage() for r in caplog.records
                   if "canonical_rights_core_binding_changed" in r.getMessage())
    assert "card-a" not in message and "buyer-a" not in message
    assert db.placements() == []


def test_initial_consent_backend_failure_remains_pending_and_redacted(
    source_rig, caplog,
):
    service, db, _root = source_rig
    service._canonical_persisted_consent = _fail
    service._canonical_right_delivery = lambda *_: pytest.fail("no GET without consent")
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        result = synchronize_account_source(service, service.account)
    assert result["status"] == "partial"
    assert result["canonical_execution"]["pending"] == 1
    assert result["canonical_execution"]["downloaded"] == 0
    assert db.placements() == []
    _logged_code(caplog, "canonical_consent_read_failed")


def test_consent_revalidation_exception_never_leaks_or_allows_get(
    executor_rig, caplog,
):
    service, account, source, snapshot, root, lock_path = executor_rig
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        result = run(
            service, account, snapshot, root, lock_path, {source},
            lambda *_: pytest.fail("no GET on inaccessible consent"),
            consent_still_current=_fail,
        )
    assert len(result) == 1 and result[0].status == "pending"
    assert rows(service.storage) == (0, 0, 0, 0)
    _logged_code(caplog, "canonical_consent_lookup_failed")


def test_delivery_error_remains_failed_without_private_exception(
    executor_rig, caplog,
):
    service, account, source, snapshot, root, lock_path = executor_rig
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        result = run(
            service, account, snapshot, root, lock_path, {source}, _fail,
        )
    assert len(result) == 1 and result[0].status == "failed"
    assert result[0].reason == "delivery_or_commit_failed"
    assert rows(service.storage) == (0, 0, 0, 0)
    _logged_code(caplog, "canonical_delivery_or_commit_failed")


def test_postcommit_catalog_projection_failure_keeps_origin_and_hides_error(
    executor_rig, caplog, monkeypatch,
):
    from cifereca.catalog import LocalCatalog

    service, account, source, snapshot, root, lock_path = executor_rig
    monkeypatch.setattr(LocalCatalog, "index_path", _fail)
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        result = run(
            service, account, snapshot, root, lock_path, {source},
            fake_delivery(service, root),
        )
    assert result[0].status == "downloaded"
    assert rows(service.storage) == (1, 1, 1, 1)
    assert service.calls == 1
    _logged_code(caplog, "canonical_catalog_index_failed")
    second = run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("published OriginLink must avoid repeated GET"),
    )
    assert second[0].status == "present_attested"


def test_postcommit_recovery_marker_error_preserves_publication_without_leak(
    executor_rig, caplog, monkeypatch,
):
    import cifereca.core.convergence_execution as execution

    service, account, source, snapshot, root, lock_path = executor_rig
    monkeypatch.setattr(execution, "mark_completed_delivery_published", _fail)
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        result = run(
            service, account, snapshot, root, lock_path, {source},
            fake_delivery(service, root),
        )
    assert result[0].status == "downloaded"
    assert rows(service.storage) == (1, 1, 1, 1)
    assert service.calls == 1
    _logged_code(caplog, "canonical_intake_state_update_failed")
    second = run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("no second GET after atomic commit"),
    )
    assert second[0].status == "present_attested"
