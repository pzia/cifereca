"""Closed observations survive the real log filter without private context."""
import json
import logging

import pytest

from cifereca import logging_utils
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.diagnostic_log_policy import is_public_core_observation
from cifereca.core.diagnostic_source_sync import log_message, public_log_fields
from cifereca.core.public_provider_diagnostics import public_connection_log_message
from cifereca.diagnostic_task_context import diagnostic_task_scope, diagnostic_task_reference
from cifereca.diagnostics import Diagnostics
from cifereca.plugin_api import Cancelled


def observation():
    from cifereca.core.source_sync_timing import TIMING_FIELDS
    return log_message(dict(
        schema_version=1, provider_id="provider." + "a" * 31 + "." + "b" * 23,
        operation_kind="source_sync", phase="completed", status="failed",
        code="source_sync_failed", duration_ms=86400000, loans=1000000, library=1000000,
        task_ref="a" * 12, **{k: 86400000 for k in TIMING_FIELDS},
    ))


def record(body, *, level=logging.ERROR, logger="cifereca", args=()):
    return logging.LogRecord(logger, level, __file__, 1, body, args, None)


@pytest.mark.parametrize("level", ["normal", "detailed", "verbose", "trace", "errors"])
def test_closed_log_body_survives_filter_without_generic_arg_truncation(monkeypatch, level):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", level)
    body = observation()
    assert len(body) > 400  # full timing/count schema, close to legacy cap
    item = record("%s", args=(body,))
    assert logging_utils.RedactionFilter().filter(item)
    assert item.getMessage() == body
    assert public_log_fields(item.getMessage()) == public_log_fields(body)
    assert logging_utils.RedactionFilter().filter(item)  # console + file
    assert item.getMessage() == body


@pytest.mark.parametrize("body", [
    "TRACE task correlation task_ref=aaaaaaaaaaaa status=failed",
    'TRACE provider connection failure {"code":"provider_connect_failed","provider_id":"provider.paris"}',
    "TRACE kobo backup decision status=error enabled=True connected=1 eligible=1 interval=60",
    'TRACE adept runtime {"code":"adept_error","operation":"status","operation_kind":"kobo_adept","phase":"completed","plugin_id":"runtime.kobo-adept","schema_version":1,"status":"failed"}',
    'TRACE numilog startup_materialization {"attempted":0,"cause_phase":"manifest_fetch","code":"manifest_failed","downloaded":0,"failed":1,"phase":"completed","skipped":0,"status":"failed","task_kind":"source-materialize"}',
])
def test_known_canonical_producers_are_retained_at_normal_level(monkeypatch, body):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    assert is_public_core_observation("cifereca", body)
    assert logging_utils.RedactionFilter().filter(record(body))


@pytest.mark.parametrize("attack", [
    lambda body: body + " email=PRIVATE@example.invalid",
    lambda body: body.replace('"schema_version":1', '"schema_version":1,"password":"PRIVATE"'),
    lambda body: body.replace('"schema_version":1', '"schema_version":2,"schema_version":1'),
    lambda body: body.replace('"provider_id":', '"provider_id": "PRIVATE@example.invalid","other":'),
    lambda body: "TRACE arbitrary PRIVATE",
    lambda body: "TRACE " + "[" * 3000,
])
def test_normal_mode_never_enables_arbitrary_or_extended_trace(monkeypatch, attack):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    body = attack(observation())
    assert not is_public_core_observation("cifereca", body)
    assert not logging_utils.RedactionFilter().filter(record(body))


def test_noncanonical_logger_cannot_publish_typed_trace_in_normal_mode(monkeypatch):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    assert not logging_utils.RedactionFilter().filter(record(observation(), logger="cifereca.plugin"))


def test_safe_body_does_not_attest_attached_private_traceback_or_stack(monkeypatch):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    item = record(observation())
    item.exc_info = (RuntimeError, RuntimeError("PRIVATE"), None)
    item.exc_text = "PRIVATE cached traceback"
    item.stack_info = "PRIVATE stack"
    assert logging_utils.RedactionFilter().filter(item)
    assert "PRIVATE" not in logging.Formatter("%(message)s").format(item)


@pytest.mark.parametrize("outcome", ["success", "failed", "cancelled"])
def test_real_worker_scope_survives_errors_mode_only_for_failures(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "errors")
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    old_level = logger.level
    handler = logging.FileHandler(path)
    handler.addFilter(logging_utils.RedactionFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        try:
            with diagnostic_task_scope():
                reference = diagnostic_task_reference()
                if outcome == "failed":
                    raise RuntimeError("PRIVATE")
                if outcome == "cancelled":
                    raise Cancelled("PRIVATE")
        except (RuntimeError, Cancelled):
            pass
        result = query_diagnostic_events(Diagnostics(), log_path=path, source="app_log")
        if outcome == "success":
            assert result["events"] == []
        else:
            row, = result["events"]
            assert row["task_ref"] == reference
            assert row["severity"] == ("error" if outcome == "failed" else "warning")
            assert f"status={outcome}" in row["message"]
        assert "PRIVATE" not in path.read_text() + json.dumps(result)
        assert diagnostic_task_reference() is None
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()


def test_real_disabled_collector_still_logs_failed_provider_at_normal_level(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    logger = logging.getLogger("cifereca")
    handler = logging.FileHandler(tmp_path / "cifereca.log")
    handler.addFilter(logging_utils.RedactionFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    try:
        diag = Diagnostics(enabled=False)
        diag.add_core_provider_connect_failure("provider.paris")
        rows = query_diagnostic_events(diag, log_path=tmp_path / "cifereca.log")["events"]
        row, = rows
        assert row["detail"] == {"provider_id": "provider.paris", "code": "provider_connect_failed"}
    finally:
        logger.removeHandler(handler)
        handler.close()


@pytest.mark.parametrize("key", [
    "credential", "identity", "profile", "account", "borrower", "source_credential",
    "api_key", "access_token", "refresh_token", "client_secret", "private_key",
    "account_name", "reader_name", "email", "source_item_id",
])
@pytest.mark.parametrize("value", ["PRIVATE", '"PRIVATE with spaces"',
                                    '"PRIVATE' + "x" * 1500 + '"',
                                    '"PRIVATE\\\" with escaped quote"', '"PRIVATE unclosed'])
def test_private_aliases_and_long_quoted_values_are_masked(key, value):
    output = logging_utils._redact_log_identifiers(
        f'{key}={value} provider_id=provider.paris book_id=9782000000000'
    )
    assert "PRIVATE" not in output and "escaped quote" not in output
    if not value.endswith("unclosed"):
        assert "provider_id=provider.paris" in output
        assert "book_id=9782000000000" in output


def test_cached_traceback_and_stack_are_redacted_on_generic_logs(monkeypatch):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    item = record("provider_id=provider.paris error")
    item.exc_text = "credential=PRIVATE api_key=PRIVATE"
    item.stack_info = "identity=PRIVATE /home/private/secret.py"
    assert logging_utils.RedactionFilter().filter(item)
    result = logging.Formatter("%(message)s").format(item)
    assert "PRIVATE" not in result and "/home/private" not in result


def test_normal_mode_preserves_source_phases_and_their_real_worker_correlation(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    old_level = logger.level
    handler = logging.FileHandler(path)
    handler.addFilter(logging_utils.RedactionFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    diag = Diagnostics(enabled=False)
    try:
        with pytest.raises(RuntimeError, match="PRIVATE"):
            with diagnostic_task_scope():
                diag.add_core_source_sync("provider.paris", phase="start", status="running", code="source_sync_started")
                diag.add_core_source_sync("provider.paris", phase="completed", status="failed",
                                          code="source_sync_failed", duration_ms=123)
                raise RuntimeError("PRIVATE@example.invalid")
        rows = query_diagnostic_events(diag, log_path=path, source="app_log")["events"]
        assert len(rows) == 3
        sync = [r for r in rows if r.get("detail", {}).get("operation_kind") == "source_sync"]
        assert {r["detail"]["phase"] for r in sync} == {"start", "completed"}
        worker, = [r for r in rows if r.get("task_ref")]
        assert {r["detail"]["task_ref"] for r in sync} == {worker["task_ref"]}
        assert "status=failed" in worker["message"]
        assert "PRIVATE" not in path.read_text()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()


def test_errors_mode_does_not_promote_successful_info_trace(monkeypatch):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "errors")
    assert not logging_utils.RedactionFilter().filter(record(
        "TRACE task correlation task_ref=aaaaaaaaaaaa status=success", level=logging.INFO,
    ))


@pytest.mark.parametrize("url", [
    "https://reader-PRIVATE.example.invalid/opaque-PRIVATE",
    "https://bibliotheques.paris.fr/opaque-PRIVATE",
    "https://user:PRIVATE@reader-PRIVATE.example.invalid/?token=PRIVATE",
])
def test_legacy_url_text_is_removed_while_explicit_public_context_survives(monkeypatch, url):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    item = record("provider_id=paris site=bibliotheques.paris.fr book_id=9782000000000 " + url)
    item.exc_text = url
    item.stack_info = url
    assert logging_utils.RedactionFilter().filter(item)
    rendered = logging.Formatter("%(message)s").format(item)
    assert "PRIVATE" not in rendered and "https://" not in rendered
    assert "site=bibliotheques.paris.fr" in rendered
    assert "book_id=9782000000000" in rendered
