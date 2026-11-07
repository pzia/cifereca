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
