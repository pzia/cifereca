"""#3909: terminal errors identify each Core post-staging phase privately.

The executor receives only synthetic PDF bytes. The original failure is kept
in the local raw journal, with a closed terminal reason for UI consumers.
"""
from __future__ import annotations

import logging

import pytest

import cifereca.core.convergence_execution as execution
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows
from tests.test_core_p0_3171_convergence_execution import (
    rig, run, fake_delivery,
)

_SECRET = "PRIVATE_SIGNED_URL_account=buyer-A&token=redacted-in-tests"

_PHASES = (
    ("recovery", "recover_completed_delivery", "canonical_recovery_failed"),
    ("delivery", None, "canonical_delivery_failed"),
    ("receipt", "record_completed_delivery_for_recovery",
     "canonical_receipt_failed"),
    ("promotion", "promote_completed_delivery", "canonical_promotion_failed"),
    ("publication", "publish_completed_acquisition",
     "canonical_publication_failed"),
)


def _raise_private(*_args, **_kwargs):
    raise RuntimeError(_SECRET)


@pytest.mark.parametrize("phase,target,code", _PHASES)
def test_exact_failure_keeps_raw_cause_and_closed_terminal_reason(
    rig, caplog, monkeypatch, phase, target, code,
):
    service, account, source, snapshot, root, lock_path = rig
    if target:
        monkeypatch.setattr(execution, target, _raise_private)
    deliver = _raise_private if phase == "delivery" else fake_delivery(
        service, root,
    )
    with caplog.at_level(logging.INFO, logger="cifereca"):
        result = run(
            service, account, snapshot, root, lock_path, {source}, deliver,
        )
    assert len(result) == 1
    assert (result[0].status, result[0].reason) == ("failed", code)
    assert rows(service.storage) == (0, 0, 0, 0)
    assert service.calls == (0 if phase in ("recovery", "delivery") else 1)
    if phase in ("receipt", "promotion", "publication"):
        assert (root / "A" / "book.pdf").read_bytes() == BYTES
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        line.startswith("TRACE canonical_flow ")
        and f"phase={phase} code=failed " in line
        for line in messages
    )
    assert any(
        line.startswith("TRACE canonical_flow ")
        and f"phase=terminal code=failed reason={code} " in line
        for line in messages
    )
    assert any(f"TRACE convergence code=canonical_{phase}_failed" in line
               for line in messages)
    failure = next(r for r in caplog.records
                   if f"TRACE convergence code=canonical_{phase}_failed" in r.getMessage())
    assert failure.exc_info[0] is RuntimeError
    assert str(failure.exc_info[1]) == _SECRET
    for field, value in (
        ("identity_id", source.scope.identity_id),
        ("provider_id", source.scope.provider_id),
        ("credential_id", source.scope.credential_id),
        ("account_id", source.scope.account_id),
        ("edition_id", source.edition_id), ("right_id", source.right_id),
    ):
        assert f"{field}={value}" in failure.getMessage()
    assert _SECRET in caplog.text and "Traceback" in caplog.text
    assert _SECRET not in repr(result)
    # Status events and the raw failure refer to this same exact item/run.
    observed = next(line for line in messages if line.startswith("TRACE canonical_flow "))
    for token in failure.getMessage().split():
        if token.startswith(("run_ref=", "item_ref=")):
            assert token in observed


def test_failure_reason_allowlist_rejects_raw_exception(caplog):
    from cifereca.core.canonical_flow_trace import CanonicalFlowTrace
    from tests.test_core_p0_3171_atomic_publication_v14 import new_source

    source, _ = new_source("A", provider="seller-a", credential="card-A",
                           account="buyer-A")
    with caplog.at_level(logging.INFO, logger="cifereca"):
        CanonicalFlowTrace().emit(
            source, "terminal", "failed", reason=_SECRET,
        )
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 1
    assert "reason=none" in messages[0]
    assert _SECRET not in messages[0]


def test_original_exception_chain_is_retained_without_affecting_result(rig, caplog):
    service, account, source, snapshot, root, lock_path = rig
    def deliver(*_args):
        try:
            raise OSError("synthetic runtime returned no artifact /tmp/book.epub")
        except OSError as cause:
            raise ValueError("canonical_delivery_runtime_result_invalid") from cause
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        outcome = run(service, account, snapshot, root, lock_path, {source}, deliver)
    assert outcome[0].reason == "canonical_delivery_failed"
    assert "canonical_delivery_runtime_result_invalid" in caplog.text
    assert "synthetic runtime returned no artifact /tmp/book.epub" in caplog.text
    assert "direct cause" in caplog.text
    assert "runtime_result_invalid" not in repr(outcome)
    assert rows(service.storage) == (0, 0, 0, 0)


def test_broken_diagnostic_handler_never_retries_or_changes_failed_outcome(rig, monkeypatch):
    from cifereca.core import canonical_flow_trace
    service, account, source, snapshot, root, lock_path = rig
    monkeypatch.setattr(canonical_flow_trace._LOG, "warning",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("log unavailable")))
    calls = []
    def deliver(*_args):
        calls.append("one-attempt")
        raise ValueError("canonical_delivery_runtime_result_invalid")
    outcome = run(service, account, snapshot, root, lock_path, {source}, deliver)
    assert outcome[0].reason == "canonical_delivery_failed"
    assert calls == ["one-attempt"]
    assert rows(service.storage) == (0, 0, 0, 0)
