"""#1120: extracted Numilog acquisition preserves service-level compatibility.

All tests use SQLite, the existing synthetic Numilog provider fixture, and
the original service façade; no signed transport or ADEPT account is used.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import cifereca.services as facade
from cifereca.locking import InterProcessLock, LockBusyError
from cifereca.providers import ProviderConfigurationError
from tests.test_numilog_owned_library_download import case


def test_extracted_numilog_download_still_uses_service_level_lock_seam(
    case, monkeypatch,
):
    events = []
    class InstrumentedLock(InterProcessLock):
        def acquire(self):
            events.append("locked")
            super().acquire()
        def release(self):
            events.append("released")
            super().release()

    monkeypatch.setattr(facade, "InterProcessLock", InstrumentedLock)
    original_download = case.session.download_direct
    def download_with_proof(item, directory):
        assert events == ["locked"]
        events.append("provider")
        return original_download(item, directory)
    case.session.download_direct = download_with_proof

    acquired = case.service.acquire_direct_item(case.item)
    assert acquired.metadata["identity_id"] == case.account.identity_id
    assert acquired.account_id == case.account.unique_id
    assert acquired.source_url is None
    assert Path(acquired.path).is_file()
    assert events == ["locked", "provider", "released"]
    assert case.calls == [case.item.record_id]


def test_extracted_numilog_busy_lock_still_rejects_before_provider_io(
    case, monkeypatch,
):
    events = []
    class BusyLock:
        def __init__(self, path):
            events.append("constructed")
        def __enter__(self):
            events.append("busy")
            raise LockBusyError("PRIVATE_LOCK_PATH")
        def __exit__(self, *_a):
            pytest.fail("lock not acquired")

    monkeypatch.setattr(facade, "InterProcessLock", BusyLock)
    with pytest.raises(
        ProviderConfigurationError,
        match="acquisition locale déjà en cours",
    ) as exc:
        case.service.acquire_direct_item(case.item)
    assert "PRIVATE_LOCK_PATH" not in str(exc.value)
    assert events == ["constructed", "busy"]
    assert case.calls == []
    assert case.service.storage.acquisitions(provider_id="numilog") == []


def test_extracted_numilog_pinned_admission_keeps_same_item_inside_lock(
    case, monkeypatch,
):
    lock_held = []
    class TracedLock(InterProcessLock):
        def acquire(self):
            super().acquire()
            lock_held.append(True)
        def release(self):
            lock_held.pop()
            super().release()

    monkeypatch.setattr(facade, "InterProcessLock", TracedLock)
    admission_calls = []
    def admission():
        assert lock_held == [True]
        admission_calls.append(True)
        return case.item
    acquired = case.service.acquire_direct_item(
        case.item, _admission=admission,
    )
    assert acquired.metadata["numilog_checkpoint_id"].startswith(
        "numilog-materialization:"
    )
    assert len(admission_calls) >= 2
    assert lock_held == []
    assert case.calls == [case.item.record_id]


def test_extracted_numilog_rejects_swapped_admission_before_delivery(
    case, monkeypatch,
):
    from dataclasses import replace

    called = []
    def admission():
        called.append(True)
        return replace(case.item, record_id="different-edition")
    with pytest.raises(ProviderConfigurationError) as exc:
        case.service.acquire_direct_item(
            case.item, _admission=admission,
        )
    assert len(called) >= 1
    assert "different-edition" not in str(exc.value)
    assert case.calls == []
    assert case.service.storage.acquisitions(provider_id="numilog") == []
