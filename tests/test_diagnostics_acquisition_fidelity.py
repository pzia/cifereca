"""#3632: actual journal/broker/export boundaries, synthetic observations only."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import json
import logging
import threading
from types import SimpleNamespace as NS

import pytest

from cifereca import logging_utils
from cifereca.brokers import DiagnosticsBrokerImpl
from cifereca.core.diagnostic_acquisition_failure import failure_payload, protect_text
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.diagnostic_journal import export_raw_journal, journal_text_export, query_journal
from cifereca.core.diagnostic_loan_download import log_message, public_fields, public_log_fields
from cifereca.diagnostic_task_context import diagnostic_task_reference, diagnostic_task_scope
from cifereca.diagnostics import Diagnostics
from cifereca.gui.diagnostics_pseudonyms import DiagnosticsPseudonyms
from cifereca.plugin_api import Cancelled
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService


@pytest.fixture
def journal(tmp_path, monkeypatch):
    logger = logging.getLogger("cifereca")
    old_handlers, old_level, old_propagate = logger.handlers[:], logger.level, logger.propagate
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    path = tmp_path / "cifereca.log"
    monkeypatch.setattr("cifereca.paths.log_file", lambda: path)
    output = io.StringIO()
    handlers = [logging_utils.PrivateRotatingFileHandler(path, encoding="utf-8"), logging.StreamHandler(output)]
    for handler in handlers:
        handler.setFormatter(logging_utils.CanonicalEventFormatter(
            "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"
        ))
        handler.addFilter(logging_utils.RedactionFilter())
    logger.handlers[:] = handlers
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        yield NS(path=path, console=output, logger=logger)
    finally:
        logger.handlers[:] = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate
        for handler in handlers:
            handler.close()


def terminal(**extra):
    return {"schema_version": 1, "provider_id": "paris", "operation_kind": "download_loan",
            "phase": "terminal", "status": "failed", "code": "provider_failure", "duration_ms": 12, **extra}


def service():
    instance = object.__new__(LibraryService)
    instance.diagnostics = Diagnostics()
    instance.registry = NS(maybe_get=lambda name: NS(descriptor=NS(id="paris")) if name == "paris" else None)
    return instance


@pytest.mark.parametrize("value,secrets,retained", [
    ('HTTP 403 loan_id=loan-42 password="p,a;&ss" account_id=account-2', ["p,a;&ss"], ["HTTP 403", "loan_id=loan-42", "account_id=account-2"]),
    ("HTTP 403 token=opaque-secret credential_id=cred-42", ["opaque-secret"], ["credential_id=cred-42"]),
    ("https://books.example/Loan-42.epub?signature=signed-secret&loan_id=42", ["signed-secret"], ["Loan-42.epub", "&loan_id=42"]),
    ("https://books.example/Loan-42?X-Amz-Signature=aws-secret&edition=E42", ["aws-secret"], ["edition=E42"]),
    ("https://alice:secret-userinfo@books.example/Loan-42", ["secret-userinfo"], ["alice", "books.example/Loan-42"]),
    ("HTTP 403\nAuthorization: Bearer bearer-secret\nloan_id=loan-42", ["bearer-secret"], ["HTTP 403", "loan_id=loan-42"]),
    ("HTTP 403\nCookie: sid=cookie-secret; csrf=csrf-secret\nloan_id=loan-42", ["cookie-secret", "csrf-secret"], ["loan_id=loan-42"]),
    ('headers={"Authorization":"Basic basic-secret","Cookie":"sid=json-cookie"} loan_id=42', ["basic-secret", "json-cookie"], ["loan_id=42"]),
    ("ADEPT rejected <adept:privateKey>adobe-secret</adept:privateKey> profile=profile-42", ["adobe-secret"], ["ADEPT rejected", "profile=profile-42"]),
    ("key=-----BEGIN RSA PRIVATE KEY-----\nprivate-key-material\n-----END RSA PRIVATE KEY----- profile=profile-42", ["private-key-material"], ["profile=profile-42"]),
])
def test_secret_values_only_and_idempotence(value, secrets, retained):
    clean = protect_text(value)
    assert protect_text(clean) == clean
    assert all(secret not in clean for secret in secrets)
    assert all(item in clean for item in retained)


def test_real_failure_chain_survives_normal_level_in_one_physical_occurrence(journal):
    instance = service()
    loan = NS(source_provider="paris", id="loan-42")
    error = ProviderConfigurationError("original provider rejection; credential_id=cred-42")
    def fail(_loan):
        try:
            raise OSError("observed response 403 https://books.example/Loan-42?sig=chain-secret&edition=E42 password=local-secret")
        except OSError as inner:
            raise error from inner
    instance._reacquire_digital_loan_impl = fail
    with pytest.raises(ProviderConfigurationError) as raised:
        with diagnostic_task_scope():
            instance.reacquire_digital_loan(loan)
    assert raised.value is error
    rows = query_journal(journal.path, limit=100)["events"]
    (cause,) = [row for row in rows if row.get("operation") == "loan_download_cause"]
    (result,) = [row for row in rows if row.get("operation") == "download_loan"]
    assert cause["task_ref"] == result["task_ref"]
    assert cause["detail"]["loan_id"] == "loan-42"
    assert cause["detail"]["observed_provider_id"] == "paris"
    assert "provider_id" not in cause  # Raw argument is not registry attestation.
    assert result["provider_id"] == "paris" and result["detail"]["code"] == "provider_failure"
    assert cause["detail"]["cause"]["type"] == "ProviderConfigurationError"
    assert "OSError" in cause["detail"]["cause"]["traceback"]
    assert "edition=E42" in cause["detail"]["cause"]["traceback"]
    assert "http_status" not in cause["detail"]["cause"]  # Text 403 is not an HTTP observation.
    raw = journal.path.read_text()
    assert "chain-secret" not in raw and "local-secret" not in raw
    assert raw == journal.console.getvalue()
    assert journal.path.stat().st_mode & 0o777 == 0o600
    assert query_journal(journal.path, search="credential_id=cred-42")["total"] == 1
    assert query_journal(journal.path, provider_id="paris")["total"] == 1
    assert len(query_journal(journal.path, search=cause["task_ref"])["events"]) == 3
    broker = DiagnosticsBrokerImpl(NS(diagnostics=instance.diagnostics))
    assert broker.events(limit=100)["events"] == rows
    assert broker.events(source="all", limit=100)["events"] == rows
    assert json.loads(broker.export_raw_journal())["events"] == rows
    ndjson = journal_text_export(journal.path, version="synthetic")
    assert [json.loads(line) for line in ndjson.splitlines() if line.startswith("{")] == rows
    rendered = DiagnosticsPseudonyms().text(json.dumps(cause, ensure_ascii=False))
    assert "loan-42" in rendered and "original provider rejection" in rendered
    assert "chain-secret" not in rendered


@pytest.mark.parametrize("status,code,phase,severity", [
    ("refused", "provider_disconnected", "admission", "error"),
    ("failed", "provider_failure", "terminal", "error"),
    ("attention_required", "adept_error", "terminal", "warning"),
    ("retryable_error", "provider_failure", "terminal", "warning"),
    ("cancelled", "operation_cancelled", "terminal", "warning"),
    ("success", "completed", "terminal", "info"),
])
def test_typed_fields_roundtrip_consistently_in_journal_and_legacy(journal, status, code, phase, severity):
    diag = Diagnostics()
    event = terminal(status=status, code=code, phase=phase, loan_id="loan-é-42", task_ref="a" * 12)
    if status not in {"success", "cancelled"}:
        event["cause"] = {"type": "HTTPStatusError", "message": "Original 403 token=typed-secret loan_id=loan-é-42", "http_status": 403}
    original = json.loads(json.dumps(event))
    diag.add_core_loan_download(event)
    (row,) = query_journal(journal.path)["events"]
    assert row["severity"] == severity and row["task_ref"] == "a" * 12
    expected = public_fields(event)
    assert row["detail"] == expected == diag.snapshot()[0]["detail"]
    assert row["detail"]["loan_id"] == "loan-é-42"
    assert public_log_fields(log_message(expected)) == expected
    legacy = query_diagnostic_events(diag, log_path=journal.path, source="core_events")["events"]
    assert legacy[0]["task_ref"] == row["task_ref"] and legacy[0]["severity"] == severity
    assert legacy[0]["detail"] == expected
    assert event == original  # No mutation of the observed input or business result.
    assert "typed-secret" not in journal.path.read_text()


def test_absent_attempt_and_missing_cause_never_turn_into_an_observed_get(journal):
    instance = service()
    instance.loan_download_eligibility = lambda _: {"available": False, "code": "provider_disconnected"}
    assert not instance.can_download_loan(NS(medium="digital"))
    assert query_journal(journal.path)["total"] == 0
    journal.logger.warning("TRACE download provider error loan_ref=%s reason=provider_or_artifact_failure dt=%.3fs", "local-ref", 0.1)
    (row,) = query_journal(journal.path)["events"]
    assert row["detail"]["cause_state"] == "not_supplied"
    assert row["status"] == "unknown" and "cause" not in row["detail"]
    assert "provider_id" not in row and "loan_id" not in row["detail"]
    assert "task_ref" not in row and "http_status" not in row["detail"]


def test_real_admission_refusal_cause_and_terminal_share_errors_filter(journal):
    instance = service()
    instance.loan_download_eligibility = lambda _: {"available": False, "code": "provider_disconnected"}
    loan = NS(source_provider="paris", source_credential="cred-42", id="loan-42", medium="digital")
    with pytest.raises(RuntimeError):
        with diagnostic_task_scope():
            instance.reacquire_digital_loan(loan)
    rows = [row for row in query_journal(journal.path, severity="error", limit=100)["events"]
            if row.get("operation") in {"loan_download_cause", "download_loan"}]
    assert len(rows) == 2 and all(row["detail"]["phase"] == "admission" for row in rows)
    cause = next(row for row in rows if row["operation"] == "loan_download_cause")
    assert cause["detail"]["observed_severity"] == "WARNING"
    assert cause["detail"]["code"] == "provider_disconnected"
    assert "http_status" not in cause["detail"]["cause"]


def test_partial_inventory_remains_inventory_and_cancellation_has_no_fabricated_cause(journal):
    diag = Diagnostics()
    diag.add("SYNC", "loan_inventory", "partial", detail={"provider_id": "bnf-pret", "branch": "unavailable"})
    instance = service()
    signal = Cancelled("not emitted by the Core cancellation branch")
    instance._reacquire_digital_loan_impl = lambda _: (_ for _ in ()).throw(signal)
    with pytest.raises(Cancelled) as raised:
        instance.reacquire_digital_loan(NS(source_provider="paris", id="loan-42"))
    assert raised.value is signal
    rows = query_journal(journal.path, limit=100)["events"]
    assert len(rows) == 2
    assert rows[0]["operation"] == "loan_inventory" and rows[0]["status"] == "partial"
    assert rows[1]["status"] == "cancelled" and "cause" not in rows[1]["detail"]
    assert all(row["operation"] != "loan_download_cause" for row in rows)


def test_real_worker_references_do_not_cross_concurrent_or_reused_workers(journal):
    barrier = threading.Barrier(2)
    def worker(number):
        diag = Diagnostics()
        with diagnostic_task_scope():
            barrier.wait(timeout=5)
            try:
                raise RuntimeError(f"original worker {number}")
            except RuntimeError:
                journal.logger.error("TRACE explicit loan recovery unexpected failure provider=%s loan_id=%s", "paris", f"loan-{number}", exc_info=True)
            diag.add_core_loan_download(terminal())
            return diagnostic_task_reference()
    with ThreadPoolExecutor(max_workers=2) as pool:
        refs = list(pool.map(worker, (1, 2)))
        assert list(pool.map(lambda _: diagnostic_task_reference(), (1, 2))) == [None, None]
    assert len(set(refs)) == 2 and diagnostic_task_reference() is None
    rows = query_journal(journal.path, limit=100)["events"]
    for number, ref in zip((1, 2), refs):
        associated = [row for row in rows if row.get("task_ref") == ref]
        # Canonical journal now also projects the *existing* Core worker
        # terminal on this same attested task_ref. It has no business
        # operation/provider, and must not be confused with a third download.
        business = [r for r in associated if "operation" in r]
        assert {r["operation"] for r in business} == {"loan_download_cause", "download_loan"}
        worker_terminal = [r for r in associated if "operation" not in r]
        assert len(worker_terminal) == 1
        assert worker_terminal[0]["status"] == "success"
        assert "provider_id" not in worker_terminal[0]
        assert [r for r in business if r["operation"] == "loan_download_cause"][0]["detail"]["loan_id"] == f"loan-{number}"


def test_hostile_arguments_never_fall_back_to_unsanitized_formatting(journal):
    class Hostile:
        def __str__(self):
            raise AssertionError("must not stringify")
    try:
        raise RuntimeError("observed cause password=hostile-secret")
    except RuntimeError:
        journal.logger.error("TRACE explicit loan recovery unexpected failure provider=%s loan_id=%s", Hostile(), "loan-42", exc_info=True)
    (row,) = query_journal(journal.path)["events"]
    assert row["detail"]["arguments_state"] == "unreadable"
    assert "observed cause" in row["detail"]["cause"]["message"]
    assert "hostile-secret" not in journal.path.read_text()


@pytest.mark.parametrize("extra", [
    {"task_ref": "not-a-worker"}, {"loan_id": object()},
    {"cause": RuntimeError("no arbitrary exception coercion")},
    {"cause": {"type": "Error", "message": "x", "http_status": True}},
    {"cause": {"type": "Error", "message": "x", "secret": "not-a-cause-field"}},
    {"cause": {"type": "Error", "message": "x" * 8193}},
])
def test_extended_terminal_schema_refuses_unattested_or_malformed_fields(extra):
    assert public_fields(terminal(**extra)) == {}


def test_extended_unicode_schema_still_roundtrips_within_log_parser_bound():
    event = terminal(loan_id="🌍" * 4096, cause={"type": "RemoteError", "message": "🌍" * 8192, "traceback": "🌍" * 8192})
    body = log_message(event)
    assert len(body) < 262144
    assert public_log_fields(body) == event


def test_no_worker_reference_is_created_when_nothing_can_be_recorded(journal):
    journal.logger.setLevel(logging.CRITICAL)
    with diagnostic_task_scope():
        Diagnostics(enabled=False).add_core_loan_download(terminal())
    assert query_journal(journal.path)["total"] == 0


@pytest.mark.parametrize("stage", ("declared_lookup", "declared_revalidation", "indexed_candidates", "physical_candidates", "delivery_or_import"))
def test_bnf_evidence_failures_are_not_invented_downloads(journal, stage):
    kind = "acquisition" if stage == "delivery_or_import" else "local evidence"
    try:
        raise OSError("Observed local failure credential_id=cred-42 token=bnf-secret")
    except OSError:
        journal.logger.warning(
            f"TRACE bnf auto {kind} failed stage={stage} provider_id=%s credential_id=%s account_id=%s loan_id=%s",
            "bnf-pret", "cred-42", "account-42", "loan-42", exc_info=True,
        )
    (row,) = query_journal(journal.path)["events"]
    assert row["detail"]["phase"] == stage
    assert row["detail"]["loan_id"] == "loan-42"
    assert row["detail"]["credential_id"] == "cred-42"
    assert row["detail"]["account_id"] == "account-42"
    assert row["operation"] == ("loan_download_cause" if kind == "acquisition" else "local_evidence_cause")
    assert row["status"] == ("failed" if kind == "acquisition" else "unknown")
    assert "provider_id" not in row and "http_status" not in row["detail"]["cause"]
    assert "bnf-secret" not in journal.path.read_text()


def test_automatic_refusal_preserves_real_reference_without_making_it_a_loan_or_worker_id(journal):
    journal.logger.warning("TRACE download loan_auto blocked reason=cross_identity_loan loan_ref=%s", "observed-ref")
    (row,) = query_journal(journal.path)["events"]
    assert row["status"] == "refused" and row["detail"]["phase"] == "admission"
    assert row["detail"]["loan_ref"] == "observed-ref"
    assert row["detail"]["cause_state"] == "not_supplied"
    assert "loan_id" not in row["detail"] and "task_ref" not in row


def test_untrusted_logger_and_success_trace_cannot_claim_failure_adapter():
    for name, level, message in (
        ("cifereca.plugin.fake", logging.ERROR, "TRACE explicit loan recovery unexpected failure provider=%s loan_id=%s"),
        ("cifereca", logging.INFO, "TRACE explicit loan recovery unexpected failure provider=%s loan_id=%s"),
        ("cifereca", logging.ERROR, "TRACE arbitrary failure provider=%s loan_id=%s"),
    ):
        record = logging.LogRecord(name, level, __file__, 1, message, ("paris", "loan-42"), None)
        assert failure_payload(record) is None


def test_cached_traceback_and_stack_preserve_evidence_without_raw_secret_append(journal):
    record = logging.LogRecord("cifereca", logging.ERROR, __file__, 1,
                               "TRACE explicit loan recovery unexpected failure provider=%s loan_id=%s", ("paris", "loan-42"), None)
    record.exc_text = "Cached original rejection password=cached-secret"
    record.stack_info = "Original worker stack token=stack-secret loan_id=loan-42"
    formatted = logging_utils.CanonicalEventFormatter("%(message)s").format(record)
    document = json.loads(formatted.removeprefix("DIAG_JSON "))
    assert document["detail"]["cause_state"] == "formatted_only"
    assert "Cached original rejection" in document["detail"]["cause"]["traceback"]
    assert "Original worker stack" in document["detail"]["stack_info"]
    assert "cached-secret" not in formatted and "stack-secret" not in formatted
    assert record.exc_text.endswith("cached-secret")  # Original record not mutated.
