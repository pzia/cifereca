"""#3909: terminal errors identify each Core post-staging phase privately.

The executor receives only synthetic PDF bytes. Provider exceptions are
deliberately malicious and must not reach Trace, terminal reason or logs.
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
def test_exact_failure_phase_is_closed_without_source_or_path_leak(
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
    rendered = "\n".join(messages)
    assert _SECRET not in rendered
    assert str(root) not in rendered
    assert "card-A" not in rendered
    assert "buyer-A" not in rendered
    assert "Traceback" not in rendered


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
