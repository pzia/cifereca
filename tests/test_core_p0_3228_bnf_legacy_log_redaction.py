"""P0 #3228 / #2888: raw BnF cause fidelity without raw account credentials.

The local private journal retains observed loan ID and exception per #2888.
The eight producer templates must not interpolate profile/credential/account.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

from cifereca.core.bnf_auto_materialization import _receipt_local_file_verified
from cifereca.core.diagnostic_acquisition_failure import (
    failure_payload, is_acquisition_failure,
)
from cifereca.models import Account


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "src" / "cifereca" / "core" / "bnf_auto_materialization.py"


def test_all_legacy_bnf_logs_use_only_provider_or_loan_coordinates():
    source = ast.parse(WORKER.read_text(encoding="utf-8"))
    checked = 0
    for node in ast.walk(source):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not isinstance(func.value, ast.Name) or func.value.id != "_TRACE_LOG":
            continue
        assert func.attr in {"warning", "info"}
        text = ast.unparse(node)
        assert "credential_id" not in text
        assert "identity_id" not in text
        assert "unique_id" not in text
        assert "account_id" not in text
        assert "password" not in text
        assert "token" not in text
        assert all(
            isinstance(argument, (ast.Constant, ast.Attribute))
            for argument in node.args
        )
        for arg in node.args[1:]:
            assert isinstance(arg, ast.Attribute)
            assert arg.attr in {"provider_id", "source_provider", "id"}
        if func.attr == "warning":
            # The local receipt-check exception can contain private paths
            # and signed supplier URLs: keep its historical event template
            # for diagnostics, but intentionally omit the raw traceback.
            if "receipt file check failed" in text:
                assert not node.keywords
            else:
                assert any(
                    kw.arg == "exc_info" and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True for kw in node.keywords
                )
        else:
            assert not node.keywords
        checked += 1
    assert checked == 8


def test_receipt_local_error_reports_closed_reason_without_private_cause(
    caplog,
):
    private = "OBSERVED_IO_CAUSE_NOT_A_REAL_TOKEN"
    account = Account(
        identity_id="IDENTITY_PRIVATE", provider_id="bnf-pret",
        credential_id="CREDENTIAL_PRIVATE", unique_id="ACCOUNT_PRIVATE",
    )

    def unavailable(_):
        raise OSError(private)

    service = SimpleNamespace(
        storage=SimpleNamespace(artifact_get=unavailable),
    )
    row = {
        "identity_id": account.identity_id,
        "provider_id": "manual", "credential_id": "manual",
        "artifact_id": "sha256:" + "a" * 64,
    }
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        assert _receipt_local_file_verified(
            service, account, row, Path("/test-missing-file"),
        ) is False
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "TRACE bnf auto receipt file check failed provider_id=bnf-pret"
    ]
    assert private not in caplog.text
    for secret in ("IDENTITY_PRIVATE", "CREDENTIAL_PRIVATE", "ACCOUNT_PRIVATE"):
        assert secret not in caplog.text


def test_new_templates_keep_phase_and_private_observed_exception_in_diagnostic():
    cases = [
        ("TRACE bnf auto receipt file check failed provider_id=%s",
         ("bnf-pret",), None, "unknown", "local_evidence_cause"),
    ]
    for phase in ("declared_lookup", "declared_revalidation",
                  "indexed_candidates", "physical_candidates",
                  "delivery_or_import"):
        kind = "acquisition" if phase == "delivery_or_import" else "local evidence"
        cases.append((
            f"TRACE bnf auto {kind} failed stage={phase} "
            "provider_id=%s loan_id=%s",
            ("bnf-pret", "synthetic-loan-id"), phase,
            "failed" if phase == "delivery_or_import" else "unknown",
            "loan_download_cause" if phase == "delivery_or_import"
            else "local_evidence_cause",
        ))
    for template, args, phase, status, operation in cases:
        record = logging.LogRecord(
            "cifereca", logging.WARNING, __file__, 1, template, args, None,
        )
        assert is_acquisition_failure(record)
        projection = failure_payload(record)
        assert projection["operation"] == operation
        assert projection["status"] == status
        assert projection["detail"].get("phase") == phase
        assert projection["detail"]["observed_provider_id"] == "bnf-pret"
        if len(args) == 2:
            assert projection["detail"]["loan_id"] == "synthetic-loan-id"
        else:
            assert "loan_id" not in projection["detail"]
        for field in ("identity_id", "credential_id", "account_id"):
            assert field not in projection["detail"]
        assert projection["detail"]["cause_state"] == "not_supplied"


def test_private_exception_cause_remains_observed_after_template_migration():
    try:
        raise OSError("synthetic-local-error-without-secrets")
    except OSError:
        import sys
        cause = sys.exc_info()
    template = (
        "TRACE bnf auto local evidence failed stage=physical_candidates "
        "provider_id=%s loan_id=%s"
    )
    record = logging.LogRecord(
        "cifereca", logging.WARNING, __file__, 1, template,
        ("bnf-pret", "loan-ref"), cause,
    )
    row = failure_payload(record)
    assert row["detail"]["cause_state"] == "observed"
    assert row["detail"]["cause"]["type"] == "OSError"
    assert "synthetic-local-error-without-secrets" in row["detail"]["cause"]["message"]
