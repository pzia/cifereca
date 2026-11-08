from __future__ import annotations

import errno
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cifereca_storage_usb import PLUGIN, _CHUNK_SIZE


def test_transport_appends_exact_ordered_bounded_chunks():
    service = PLUGIN.activate(None)
    payload = b"a" * _CHUNK_SIZE + b"b" * _CHUNK_SIZE + b"tail"
    chunks = []
    assert service.write_usb_stage(payload, chunks.append) is None
    assert list(map(len, chunks)) == [_CHUNK_SIZE, _CHUNK_SIZE, 4]
    assert b"".join(chunks) == payload
    assert not hasattr(service, "configured_targets")
    assert not hasattr(service, "import_artifact")


def test_transport_stops_at_core_sink_failure():
    service = PLUGIN.activate(None)
    calls = []

    def append(chunk):
        calls.append(chunk)
        raise OSError(errno.ENOSPC, "synthetic USB full")

    with pytest.raises(OSError) as error:
        service.write_usb_stage(b"a" * _CHUNK_SIZE + b"tail", append)
    assert error.value.errno == errno.ENOSPC
    assert len(calls) == 1


def test_empty_payload_does_not_call_core_sink():
    service = PLUGIN.activate(None)
    chunks = []
    assert service.write_usb_stage(b"", chunks.append) is None
    assert chunks == []


def test_transport_never_retries_a_failed_chunk_or_publishes_itself():
    service = PLUGIN.activate(None)
    payload = b"a" * _CHUNK_SIZE + b"b" * _CHUNK_SIZE + b"tail"
    observed = []

    def interrupted_sink(chunk):
        observed.append(chunk)
        if len(observed) == 2:
            raise InterruptedError(errno.EINTR, "synthetic unplug")

    with pytest.raises(InterruptedError):
        service.write_usb_stage(payload, interrupted_sink)
    assert observed == [b"a" * _CHUNK_SIZE, b"b" * _CHUNK_SIZE]
    assert not hasattr(service, "commit")
    assert not hasattr(service, "delete")


def test_two_independent_device_stages_never_share_chunk_state():
    service = PLUGIN.activate(None)
    first, second = [], []
    service.write_usb_stage(b"1" * (_CHUNK_SIZE + 7), first.append)
    service.write_usb_stage(b"2" * 3, second.append)
    assert [len(chunk) for chunk in first] == [_CHUNK_SIZE, 7]
    assert second == [b"222"]
    assert b"".join(first) == b"1" * (_CHUNK_SIZE + 7)
