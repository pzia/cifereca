"""P0 #1143: Core sync's auxiliary trace failures use closed codes only.

Real local Core/SQLite and synthetic providers only. No PNB, ADEPT, Kobo,
Numilog network activity, loan return or signed entitlement is requested.
"""
from __future__ import annotations

import logging

from cifereca.brokers import SynchronizationBrokerImpl
from tests.test_core_p0_1346_opening_source_isolation import _accounts
from tests.test_sync_state_contract import _service


PRIVATE = "https://private.invalid/loan?token=SIGNED_SYNC_AUXILIARY"


class HostileError(RuntimeError):
    def __str__(self):
        raise AssertionError("An untrusted provider exception was stringified")
    def __repr__(self):
        raise AssertionError("An untrusted provider exception was repr'd")


HostileError.__name__ = "SIGNED_SYNC_AUXILIARY"


def _fail(*_args, **_kwargs):
    raise HostileError(PRIVATE)


def test_mirror_failure_reports_closed_reason_and_does_not_block_opening(
    tmp_path, monkeypatch, caplog,
):
    service = _service(tmp_path)
    monkeypatch.setattr(service, "mirror_personal_libraries", _fail)
    monkeypatch.setattr(service, "_loans_for_account", lambda *_args, **_kwargs: [])
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            result = service.opening_sync()
        assert result["catalog_mirror"]["status"] == "failed"
        errors = [line for line in caplog.text.splitlines()
                  if "TRACE catalog mirror opening error" in line]
        assert len(errors) == 1
        assert errors[0].endswith("code=catalog_mirror_failed")
        assert "SIGNED_SYNC_AUXILIARY" not in errors[0]
        assert "private.invalid" not in errors[0]
    finally:
        service.close()


def test_source_projection_failure_does_not_log_provider_or_exception_class(
    tmp_path, monkeypatch, caplog,
):
    service = _service(tmp_path)
    _accounts(service)
    monkeypatch.setattr(service, "_loans_for_account", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(service, "mirror_personal_libraries", lambda: {})
    monkeypatch.setattr(SynchronizationBrokerImpl, "record_opening_state", _fail)
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            result = service.opening_sync()
        assert len(result["identities"]) >= 1
        lines = [line for line in caplog.text.splitlines()
                 if "TRACE sync source projection failed" in line]
        assert lines
        assert all(line.endswith("code=source_state_projection_failed")
                   for line in lines)
        assert all("SIGNED_SYNC_AUXILIARY" not in line for line in lines)
        assert all("private.invalid" not in line for line in lines)
    finally:
        service.close()
