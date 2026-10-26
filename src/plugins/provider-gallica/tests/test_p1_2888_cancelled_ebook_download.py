"""#2888: an explicit Gallica EPUB/PDF cancellation is not a failed download.

Only synthetic transport and local files; no public or private HTTP calls.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import Cancelled, ProviderError
from cifereca_gallica import GallicaSession


class Broker:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def fetch(self, source, destination):
        destination = Path(destination)
        self.calls.append((dict(source), destination))
        destination.write_bytes(b"partial simulated transfer")
        if self.error is not None:
            raise self.error
        return destination


def session(broker):
    return GallicaSession(
        credential_id="public", label="Gallica", credentials={},
        options={}, config={}, diagnostics=None, transport=broker,
    )


@pytest.mark.parametrize("fmt,mime,extension", [
    ("EPUB", "epub", ".epub"),
    ("PDF", "pdf", ".pdf"),
])
def test_cancel_keeps_task_signal_and_removes_partial_output(
    tmp_path, fmt, mime, extension,
):
    cancellation = Cancelled("cancelled by user")
    broker = Broker(cancellation)
    obj = session(broker)
    item = NS(
        title="Book", record_id="ark:/12148/synthetic-record",
        format=fmt, download_url="https://gallica.bnf.fr/ark:/12148/synthetic-record",
    )
    try:
        with pytest.raises(Cancelled) as caught:
            obj._download_ebook(item, tmp_path)
        assert caught.value is cancellation
        assert len(broker.calls) == 1
        source, candidate = broker.calls[0]
        assert source["expected_format"] == mime
        assert candidate.suffix == extension
        assert not candidate.exists()
        assert not list(tmp_path.glob("*"))
    finally:
        obj.close()


def test_non_cancelled_transport_failure_remains_failure(tmp_path):
    broker = Broker(RuntimeError("synthetic I/O outage"))
    obj = session(broker)
    item = NS(
        title="Book", record_id="synthetic-record",
        format="EPUB", download_url="https://gallica.bnf.fr/ark:/12148/synthetic-record",
    )
    try:
        with pytest.raises(ProviderError, match="téléchargement numérique indisponible"):
            obj._download_ebook(item, tmp_path)
        assert len(broker.calls) == 1
        assert not list(tmp_path.glob("*"))
    finally:
        obj.close()


def test_successful_broker_delivery_unchanged(tmp_path):
    broker = Broker()
    obj = session(broker)
    item = NS(
        title="Book", record_id="synthetic-record",
        format="EPUB", download_url="https://gallica.bnf.fr/ark:/12148/synthetic-record",
    )
    try:
        path, evidence = obj._download_ebook(item, tmp_path)
        assert path.exists()
        assert evidence == item.download_url
        assert path.read_bytes() == b"partial simulated transfer"
        assert len(broker.calls) == 1
    finally:
        obj.close()
