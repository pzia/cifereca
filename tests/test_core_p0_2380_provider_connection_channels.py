"""P0 #2380: verified connection failure facts survive both local channels.

All connectors are fake, no HTTP, no private credential, Adobe data or real
account. A log line alone is not a task correlation or identity proof.
"""
from __future__ import annotations

import json
import logging

import pytest

from cifereca.core import provider_sessions
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.public_provider_diagnostics import (
    CONNECT_FAILURE_CODE, MISSING_PROVIDER_CODE, UNKNOWN_PROVIDER,
    public_connection_fields, public_connection_log_fields,
    public_connection_log_message,
)
from cifereca.diagnostics import Diagnostics
from tests.test_core_p0_1143_provider_connect_privacy import _rig, SECRET, SIGNED


@pytest.fixture
def application_log(tmp_path):
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    old_level = logger.level
    handler = logging.FileHandler(path)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s",
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield path
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()


def _rows(diagnostics, path, *, source="all"):
    report = query_diagnostic_events(
        diagnostics, log_path=path, source=source, limit=100,
    )
    verified = [
        event for event in report["events"]
        if event.get("detail", {}).get("code") in {
            CONNECT_FAILURE_CODE, MISSING_PROVIDER_CODE,
        }
    ]
    return report, verified


@pytest.mark.parametrize("singleton", [False, True])
def test_registered_connection_failures_agree_without_account_fields(
    application_log, singleton,
):
    service, session, _ = _rig(singleton=singleton)
    result = provider_sessions.connect_configured(
        service, logger=logging.getLogger("cifereca"),
    )
    assert len(result["errors"]) == 1
    report, rows = _rows(service.diagnostics, application_log)
    assert len(rows) == 2
    by_source = {row["source"]: row for row in rows}
    expected = {
        "provider_id": "provider.test", "code": CONNECT_FAILURE_CODE,
    }
    for source in ("core_events", "app_log"):
        event = by_source[source]
        assert event["detail"] == expected
        assert event["message"] == "connexion fournisseur échouée"
        assert event["status"] == "error"
        assert "task_ref" not in event
    assert by_source["app_log"]["severity"] == "error"
    assert by_source["core_events"]["event_id"] != by_source["app_log"]["event_id"]
    evidence = json.dumps(report) + application_log.read_text()
    for secret in (SECRET, "SECRET_TOKEN", "private.invalid",
                   "identity-private", "Private label", "HostileFailure"):
        assert secret not in evidence


def test_missing_provider_publishes_fixed_code_without_configured_slug(
    application_log,
):
    service, _, _ = _rig()
    service.registry.maybe_get = lambda _id: None
    result = provider_sessions.connect_configured(
        service, logger=logging.getLogger("cifereca"),
    )
    assert len(result["errors"]) == 1
    assert result["connection_failures"]["items"] == ({
        "provider_id": UNKNOWN_PROVIDER, "code": MISSING_PROVIDER_CODE,
    },)
    report, rows = _rows(service.diagnostics, application_log)
    assert len(rows) == 2
    assert all(row["detail"] == {
        "provider_id": UNKNOWN_PROVIDER, "code": MISSING_PROVIDER_CODE,
    } for row in rows)
    assert "provider.test" not in json.dumps(rows)


def test_two_accounts_do_not_collapse_into_one_attested_failure(
    application_log,
):
    service, session, _ = _rig()
    service.config.provider_accounts = lambda **kw: [
        {"id": SECRET, "provider": "provider.test",
         "identity_id": "PRIVATE_ID", "credentials": {"password": SECRET}},
        {"id": "PRIVATE_OTHER_ID", "provider": "provider.test",
         "identity_id": "PRIVATE_ID", "credentials": {"password": SECRET}},
    ]
    provider_sessions.connect_configured(
        service, logger=logging.getLogger("cifereca"),
    )
    report, rows = _rows(service.diagnostics, application_log)
    assert len(rows) == 4
    assert [sum(row["source"] == source for row in rows)
            for source in ("core_events", "app_log")] == [2, 2]
    assert len({row["event_id"] for row in rows}) == 4
    blob = json.dumps(report) + application_log.read_text()
    assert "PRIVATE_OTHER_ID" not in blob
    assert "PRIVATE_ID" not in blob
    assert SECRET not in blob


def test_disabled_collector_does_not_claim_missing_log_evidence(
    application_log,
):
    diag = Diagnostics(enabled=False)
    diag.add_core_provider_connect_failure("provider.test")
    report, rows = _rows(diag, application_log)
    assert diag.snapshot() == []
    assert report["status"] == "partial"
    assert report["sources"][0]["status"] == "disabled"
    assert len(rows) == 1 and rows[0]["source"] == "app_log"
    assert rows[0]["detail"]["provider_id"] == "provider.test"


@pytest.mark.parametrize("tampering", [
    {"provider_id": "private.invalid/path", "code": CONNECT_FAILURE_CODE},
    {"provider_id": "provider.test", "code": "PRIVATE"},
    {"provider_id": "provider.test", "code": CONNECT_FAILURE_CODE,
     "credential_id": SECRET},
    {"provider_id": "provider.test", "code": CONNECT_FAILURE_CODE,
     "task_ref": "aaaaaaaaaaaa"},
    {"provider_id": SECRET, "code": CONNECT_FAILURE_CODE},
    {"provider_id": "provider.test", "code": True},
])
def test_hostile_structured_fields_cannot_be_promoted(tampering):
    assert public_connection_fields(tampering) == {}


def test_canonical_log_refuses_private_extensions_duplicates_and_other_loggers(
    application_log,
):
    expected = {"provider_id": "provider.test", "code": CONNECT_FAILURE_CODE}
    valid = public_connection_log_message(expected)
    duplicate = valid.replace(
        '"provider_id":"provider.test"',
        '"provider_id":"provider.test","provider_id":"provider.test"',
    )
    attacks = [
        valid + " token=PRIVATE",
        duplicate,
        valid.replace(CONNECT_FAILURE_CODE, "PRIVATE"),
        valid.replace("provider.test", "PRIVATE@example.invalid"),
        "TRACE provider connection failure " + "X" * 600,
    ]
    assert public_connection_log_fields(valid) == expected
    assert all(public_connection_log_fields(v) == {} for v in attacks)
    application_log.write_text(
        "2026-10-20 11:00:00,001 ERROR cifereca " + valid + "\n"
        "2026-10-20 11:00:01,001 ERROR cifereca.plugin " + valid + "\n"
        + "".join(
            f"2026-10-20 11:00:0{index+2},001 ERROR cifereca {body}\n"
            for index, body in enumerate(attacks)
        ),
    )
    report, rows = _rows(Diagnostics(), application_log, source="app_log")
    assert len(rows) == 1
    assert rows[0]["detail"] == expected
    assert SECRET not in json.dumps(report)
    assert "PRIVATE" not in json.dumps(report)


def test_generic_diagnostics_add_never_attests_provider_failure(tmp_path):
    diag = Diagnostics()
    diag.add("PLUGIN", "provider connect", "error",
             detail={"provider_id": "provider.test", "code": CONNECT_FAILURE_CODE})
    _, rows = _rows(diag, tmp_path / "none", source="core_events")
    assert rows == []


def test_failing_optional_log_handler_does_not_drop_core_observation():
    class Broken(logging.Handler):
        def emit(self, _record):
            raise RuntimeError(SIGNED)
    logger = logging.getLogger("cifereca")
    previous = logger.level
    logger.setLevel(logging.INFO)
    broken = Broken()
    logger.addHandler(broken)
    try:
        diag = Diagnostics()
        diag.add_core_provider_connect_failure("provider.test", code=MISSING_PROVIDER_CODE)
        assert diag.snapshot()[0]["detail"] == {
            "provider_id": "provider.test", "code": MISSING_PROVIDER_CODE,
        }
    finally:
        logger.removeHandler(broken)
        logger.setLevel(previous)
