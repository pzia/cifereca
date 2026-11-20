"""#2435 bounded before/after-state benchmark using actual N1/N2/N3.

No live network, existing cache, GTK paint or physical EPUB publication.
Run pytest -s tests/test_core_p0_2435_cover_cache_baseline.py for JSON.
"""
import base64
from dataclasses import replace
import json
import resource
import statistics
from threading import Event
from time import perf_counter_ns, process_time_ns

import httpx
import pytest

from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.covers import candidate
from cifereca.gui import cover_loader
from cifereca.gui.document_cover_service import CoverSourceSnapshot, DocumentCoverService

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


def dist(samples):
    ordered = sorted(samples)
    return {"count": len(ordered), "p50_ms": round(statistics.median(ordered), 4),
            "p95_ms": round(ordered[(len(ordered) * 95 + 99) // 100 - 1], 4),
            "max_ms": round(ordered[-1], 4)}


@pytest.mark.parametrize("size", (16, 256))
def test_documentary_and_media_cache_baseline(tmp_path, monkeypatch, size):
    monkeypatch.setattr(cover_loader, "cache_dir", lambda: tmp_path)
    urls = [f"https://cover.example.test/{i}.png" for i in range(size)]
    allowed = set(urls)
    transport = []

    def response(req):
        assert req.method == "GET" and str(req.url) in allowed
        transport.append(str(req.url))
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    loader = cover_loader.CoverLoader(max_workers=2)
    old = loader.client
    loader.client = httpx.Client(transport=httpx.MockTransport(response), trust_env=False)
    old.close()
    service = DocumentCoverService(loader)
    documents = [CoverDocumentScope(
        kind="public_catalogue", identity_id=None, provider_id="fixture-cover",
        credential_id="__public__", source_key=f"notice-c:exact:{i}",
    ) for i in range(size)]
    snapshots = [CoverSourceSnapshot(
        scope=document, revision=1, snapshot_complete=True,
        candidates=(candidate(url, tier="catalog", source="notice-c",
                              evidence_id=document.source_key),),
    ) for document, url in zip(documents, urls)]
    report = {"documents": size, "workers": 2, "transport": "mock_httpx",
              "media_bytes": len(PNG), "gtk_paint_measured": False,
              "physical_io_measured": False, "phases": {}}
    try:
        for snapshot in snapshots:
            service.register(snapshot)

        def phase(name, *, miss, get):
            before = service.stats()
            media_before = loader.stats()
            before_get = len(transport)
            cpu = process_time_ns()
            wall = perf_counter_ns()
            durations = []
            for document, url in zip(documents, urls):
                ready, values = Event(), []
                def deliver(value):
                    values.append(value)
                    ready.set()
                started = perf_counter_ns()
                handle = service.get_cover(document, "detail", deliver)
                assert ready.wait(5), "bounded scratch media callback timed out"
                durations.append((perf_counter_ns() - started) / 1e6)
                assert handle.source_uri == url
                assert values == [loader._path(url)] and values[0].read_bytes() == PNG
                handle.cancel()
            after = service.stats()
            media = loader.stats()
            assert after["n2_miss"] - before["n2_miss"] == (size if miss else 0)
            assert after["n2_hit"] - before["n2_hit"] == (0 if miss else size)
            assert after["n1_selected_count"] - before["n1_selected_count"] == (size if miss else 0)
            assert len(transport) - before_get == (size if get else 0)
            assert media["download_ok"] - media_before["download_ok"] == (size if get else 0)
            assert media["cache_hit_valid"] - media_before["cache_hit_valid"] == (0 if get else size)
            published = media["n3_url_published_bytes"] - media_before["n3_url_published_bytes"]
            assert published == (size * len(PNG) if get else 0)
            assert media["download_failed"] == 0 and media["ui_decode_count"] == 0
            report["phases"][name] = {
                "callback_latency": dist(durations),
                "wall_ms": round((perf_counter_ns() - wall) / 1e6, 3),
                "process_cpu_ms": round((process_time_ns() - cpu) / 1e6, 3),
                "rss_highwater_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "n1_selected": after["n1_selected_count"] - before["n1_selected_count"],
                "n2_misses": after["n2_miss"] - before["n2_miss"],
                "n2_hits": after["n2_hit"] - before["n2_hit"],
                "n3_gets": len(transport) - before_get,
                "n3_media_hits": media["cache_hit_valid"] - media_before["cache_hit_valid"],
                "n3_logical_published_bytes": published,
                "n3_cache_file_bytes": sum(p.stat().st_size for p in loader.root.glob("*.img")),
            }

        phase("n2_cold_n3_cold", miss=True, get=True)
        phase("n2_warm_n3_warm", miss=False, get=False)
        for url in urls:
            loader.invalidate(loader._path(url))
        phase("n2_warm_n3_cold", miss=False, get=True)
        # Keep exact same selected URI/media; new documentary revision must
        # still recompute N1/N2. N3 must not be refetched merely for that reason.
        for snapshot in snapshots:
            service.register(replace(snapshot, revision=2))
        phase("n2_revision_changed_n3_warm", miss=True, get=False)
        report["n1_n2_recent"] = service.stats()
        report["n3_recent"] = loader.stats()
        assert len(transport) == 2 * size
        print("COVER_CACHE_BASELINE " + json.dumps(report, sort_keys=True))
    finally:
        service.close()
        loader.close()
