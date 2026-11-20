"""#773: real loopback HTTP, generated EPUB and Core publication/restart.

The provider adapter is a synthetic delivery witness, not a real account.
Cancellation is checked at Core boundaries, not inside HttpTransport.fetch.
"""
from hashlib import sha256
from http.server import ThreadingHTTPServer
import logging
from pathlib import Path
import threading
from types import SimpleNamespace as NS

import pytest

from cifereca.core import convergence_execution as core
from cifereca.locking import InterProcessLock
from cifereca.plugin_api import Cancelled
from cifereca.storage import Storage
from tests.test_core_p0_3171_convergence_execution import rig
from tests.test_core_p0_3171_atomic_publication_v14 import rows
from tests.test_core_p0_3171_crash_recovery_v14 import journal


@pytest.fixture
def loopback(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repo / "simulation"))
    monkeypatch.syspath_prepend(str(repo / "src/plugins/transport-http"))
    from plugin_scenarios import create
    from transport.pseudo_http_failure_service import Handler, TransportFakeApp
    from cifereca_http_transport import HttpTransport

    dataset = tmp_path / "dataset"
    manifest = create(dataset)
    original = dataset / "optimizer-valid.epub"
    payload = original.read_bytes()
    digest = sha256(payload).hexdigest()
    assert digest == manifest["files"][original.name]
    app = TransportFakeApp(tmp_path / "http-cache")
    app.artifact = original
    requests = []

    class CountedHandler(Handler):
        def do_GET(self):
            requests.append(self.path)
            super().do_GET()

    CountedHandler.app = app
    server = ThreadingHTTPServer(("127.0.0.1", 0), CountedHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    transport = HttpTransport(NS(
        config={"timeout_seconds": 2, "max_download_bytes": 1024 * 1024},
        logger=logging.getLogger("test.fixture.transport"),
    ))
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield transport, base, requests, payload, digest
    finally:
        transport.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        assert original.read_bytes() == payload


def _run(rig, deliver, cancelled, *, allowed=True):
    service, account, source, snapshot, root, lock_path = rig
    with InterProcessLock(lock_path) as lease:
        return core.execute_admitted_convergence(
            service, account, service._sessions[(account.provider_id, account.credential_id)],
            snapshot, admitted_epoch=service._source_context_epoch,
            shared_lock=lease, library_root=root,
            persisted_consent=frozenset({source} if allowed else ()),
            consent_still_current=lambda key: allowed and key == source,
            deliver=deliver, cancelled=cancelled.is_set,
        )


def _deliver(rig, loopback, path, *, after=None):
    _service, _account, _source, _snapshot, root, _lock = rig
    transport, base, requests, payload, digest = loopback

    def get(source, observation):
        attempt = root / "A" / ".canonical-delivery" / f"run-{len(requests)+1:032x}"
        stage = attempt / "received.epub"
        transport.fetch({"url": base + path, "expected_format": "epub",
                         "allowed_content_types": ["application/epub+zip"],
                         "timeout_seconds": 1}, stage)
        assert stage.read_bytes() == payload
        if after is not None:
            after.set()
        return core.CompletedDelivery(source, observation.delivery_offer_id,
                                      stage, digest, f"synthetic-http-receipt-{len(requests)}")
    return get


@pytest.mark.parametrize("fault", ["/status/429", "/slow"])
def test_failed_http_never_creates_canonical_authority_and_retry_reads_fresh(rig, loopback, fault):
    service, _, _, _, root, _ = rig
    _, _, requests, payload, digest = loopback
    cancelled = threading.Event()
    result, = _run(rig, _deliver(rig, loopback, fault), cancelled)
    assert result.status == "failed"
    assert requests == [fault]
    assert rows(service.storage) == (0, 0, 0, 0)
    assert journal(service.storage) == []
    assert not list(root.rglob("*.part"))
    assert not list(root.rglob("*.epub"))
    result, = _run(rig, _deliver(rig, loopback, "/file.epub"), cancelled)
    assert result.status == "downloaded"
    assert requests == [fault, "/file.epub"]
    assert rows(service.storage) == (1, 1, 1, 1)
    placement = service.storage.placement_get(result.placement_id)
    assert placement["identity_id"] == "A" and placement["artifact_id"] == "sha256:" + digest
    assert Path(placement["path"]).read_bytes() == payload


@pytest.mark.parametrize("phase", ["before_get", "after_get", "after_receipt", "after_promotion"])
def test_cancel_and_restart_only_reuse_verified_completed_receipt(rig, loopback, monkeypatch, phase):
    service, _, _, _, root, _ = rig
    _, _, requests, payload, digest = loopback
    cancelled = threading.Event()
    before = service.storage
    if phase == "before_get":
        cancelled.set()
        with pytest.raises(Cancelled):
            _run(rig, lambda *_: pytest.fail("cancelled before GET"), cancelled)
        assert requests == [] and journal(before) == []
        assert rows(before) == (0, 0, 0, 0)
    else:
        with monkeypatch.context() as patch:
            if phase == "after_receipt":
                name = "record_completed_delivery_for_recovery"
            elif phase == "after_promotion":
                name = "promote_completed_delivery"
            else:
                name = None
            if name:
                original = getattr(core, name)

                def interrupt(*args, **kwargs):
                    value = original(*args, **kwargs)
                    cancelled.set()
                    return value
                patch.setattr(core, name, interrupt)
            result, = _run(
                rig, _deliver(rig, loopback, "/file.epub",
                              after=cancelled if phase == "after_get" else None), cancelled,
            )
        assert (result.status, result.reason) == ("cancelled", "cancelled_" + phase)
        assert requests == ["/file.epub"]
        assert rows(before) == (0, 0, 0, 0)
        staged = list((root / "A" / ".canonical-delivery").glob("run-*/received.epub"))
        assert len(staged) == 1 and staged[0].read_bytes() == payload
        receipts = journal(before)
        if phase == "after_get":
            assert receipts == []
        else:
            assert len(receipts) == 1 and receipts[0][:2] == ("canonical_receipt_staged", None)

    # Restart storage, not provider delivery. No right inferred from payload.
    database = before.path
    before.close()
    service.storage = Storage(database)
    try:
        cancelled.clear()
        denied, = _run(rig, lambda *_: pytest.fail("no current consent"), cancelled, allowed=False)
        assert denied.status == "pending"
        assert rows(service.storage) == (0, 0, 0, 0)
        resumable = phase in ("after_receipt", "after_promotion")
        deliver = (lambda *_: pytest.fail("durable exact receipt must avoid GET")) if resumable else (
            _deliver(rig, loopback, "/file.epub")
        )
        result, = _run(rig, deliver, cancelled)
        assert result.status == ("recovered" if resumable else "downloaded")
        assert len(requests) == (2 if phase == "after_get" else 1)
        assert rows(service.storage) == (1, 1, 1, 1)
        assert service.storage.entitlements() == []
        placement = service.storage.placement_get(result.placement_id)
        output = Path(placement["path"])
        assert placement["identity_id"] == "A" and output.parent == root / "A" / "Acquisitions"
        assert output.read_bytes() == payload and placement["artifact_id"] == "sha256:" + digest
        replay, = _run(rig, lambda *_: pytest.fail("already published"), cancelled)
        assert replay.status == "present_attested"
        assert len(requests) == (2 if phase == "after_get" else 1)
        assert rows(service.storage) == (1, 1, 1, 1)
    finally:
        service.storage.close()
