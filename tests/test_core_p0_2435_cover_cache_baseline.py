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
def test_documentary_and_media_cache_baseline(tmp_path, monkeypatch, capsys, size):
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
        with capsys.disabled():
            print("COVER_CACHE_BASELINE " + json.dumps(report, sort_keys=True), flush=True)
    finally:
        service.close()
        loader.close()


def _png(index):
    # A genuinely decodable, distinct 1x1 RGB PNG, no external asset.
    import struct
    import zlib
    def chunk(name, body):
        return (struct.pack(">I", len(body)) + name + body
                + struct.pack(">I", zlib.crc32(name + body) & 0xffffffff))
    head = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head)
            + chunk(b"IDAT", zlib.compress(bytes((0, index >> 8, index & 255, 0))))
            + chunk(b"IEND", b""))


@pytest.mark.parametrize("size", (16, 256))
def test_epub_physical_cache_baseline(tmp_path, monkeypatch, capsys, size):
    import hashlib
    import zipfile
    from cifereca import epub_metadata
    from cifereca.core.epub_cover_materialization import (
        EpubCoverPhysicalCache, ExactEpubCoverResource,
    )
    from tests import test_epub_cover_contract as fixture

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    corpus = []
    for i in range(size):
        book = tmp_path / f"book-{i}.epub"
        image = _png(i)
        monkeypatch.setattr(fixture, "PNG", image)
        fixture._write_epub(book, epub3=True)
        # Distinct synthetic non-cover ZIP body: exercise the full physical
        # digest, not just the miniature. Stored, rather than compressed.
        with zipfile.ZipFile(book, "a", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("OPS/synthetic-padding.bin", b"z" * 65536)
        metadata = epub_metadata.inspect_epub(book)
        assert metadata["epub_cover_status"] == "extracted"
        digest = hashlib.sha256(book.read_bytes()).hexdigest()
        ref = ExactEpubCoverResource(
            scope=CoverDocumentScope(
                kind="placement", identity_id="fixture",
                source_key="artifact:sha256:" + digest,
                artifact_id="sha256:" + digest, placement_id=f"place:{i}",
            ),
            source_revision=1, artifact_sha256=digest,
            member=metadata["epub_cover"]["member"],
            cache_name=metadata["epub_cover"]["cache_name"],
        )
        target = epub_metadata.cover_cache_dir() / ref.cache_name
        assert target.read_bytes() == image
        corpus.append((book, ref, target, image))
    assert len({x[2] for x in corpus}) == size
    loader = EpubCoverPhysicalCache(max_workers=2)
    restores = []
    actual = loader._restore
    def counted(ref, path):
        restores.append(path)
        return actual(ref, path)
    monkeypatch.setattr(loader, "_restore", counted)
    report = {"documents": size, "workers": 2, "phases": {},
              "physical_io_measured": False, "gtk_paint_measured": False,
              "archive_corpus_bytes": sum(book.stat().st_size for book, *_ in corpus)}
    try:
        for _, _, target, _ in corpus:
            target.unlink()

        def phase(name, cold):
            start_wall, start_cpu = perf_counter_ns(), process_time_ns()
            initial = loader.stats()
            initial_reads = len(restores)
            durations = []
            for book, ref, target, image in corpus:
                started = perf_counter_ns()
                assert loader.request(ref, source_path=book).result(timeout=5) == target.resolve().as_uri()
                durations.append((perf_counter_ns() - started) / 1e6)
                assert target.read_bytes() == image
            final = loader.stats()
            assert final["n3_epub_miss"] - initial["n3_epub_miss"] == (size if cold else 0)
            assert final["n3_epub_hit"] - initial["n3_epub_hit"] == (0 if cold else size)
            assert len(restores) - initial_reads == (size if cold else 0)
            assert final["n3_epub_failed"] == final["n3_epub_overflow"] == 0
            report["phases"][name] = {
                "request_latency_until_result": dist(durations),
                "wall_ms": round((perf_counter_ns() - start_wall) / 1e6, 3),
                "process_cpu_ms": round((process_time_ns() - start_cpu) / 1e6, 3),
                "rss_highwater_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "restore_calls": len(restores) - initial_reads,
                "n3_epub_hits": final["n3_epub_hit"] - initial["n3_epub_hit"],
                "logical_archive_bytes_hashed": report["archive_corpus_bytes"] if cold else 0,
                "cache_file_bytes": sum(target.stat().st_size for _, _, target, _ in corpus),
            }

        phase("epub_n3_cold", True)
        phase("epub_n3_warm", False)
        report["n3_epub_recent"] = loader.stats()
        assert report["n3_epub_recent"]["n3_epub_ok_recent_count"] == size
        with capsys.disabled():
            print("COVER_EPUB_BASELINE " + json.dumps(report, sort_keys=True), flush=True)
    finally:
        loader.close()


@pytest.mark.parametrize("size", (16, 256))
def test_shared_n3_independent_n2_concurrency_baseline(tmp_path, monkeypatch, capsys, size):
    """Eight exact documents per URL, deterministic overlap without sleeps."""
    from tests.test_core_p0_2435_preview_baseline import _process_io

    monkeypatch.setattr(cover_loader, "cache_dir", lambda: tmp_path)
    batch = 8
    urls = [f"https://shared.example.test/{i}.png" for i in range(size // batch)]
    release = {url: Event() for url in urls}
    transfers = []

    def response(req):
        url = str(req.url)
        assert req.method == "GET" and url in release
        transfers.append(url)
        assert release[url].wait(10), "bounded mock GET was not released"
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    loader = cover_loader.CoverLoader(max_workers=2)
    old = loader.client
    loader.client = httpx.Client(transport=httpx.MockTransport(response), trust_env=False)
    old.close()
    service = DocumentCoverService(loader)
    documents = [CoverDocumentScope(
        kind="public_catalogue", identity_id=None, provider_id="fixture-cover",
        credential_id="__public__", source_key=f"notice-c:shared:{i}",
    ) for i in range(size)]
    snapshots = [CoverSourceSnapshot(
        scope=doc, revision=1, snapshot_complete=True,
        candidates=(candidate(urls[i // batch], tier="catalog", source="notice-c",
                              evidence_id=doc.source_key),),
    ) for i, doc in enumerate(documents)]
    report = {"documents": size, "unique_media": len(urls), "batch": batch,
              "workers": 2, "transport": "mock_httpx_barrier",
              "gtk_paint_measured": False, "physical_io_measured": False,
              "phases": {}}
    try:
        for snapshot in snapshots:
            service.register(snapshot)

        def phase(name, *, expected_misses, cold):
            before, media_before = service.stats(), loader.stats()
            io_before = _process_io()
            cpu, wall = process_time_ns(), perf_counter_ns()
            durations = []
            for offset in range(0, size, batch):
                url = urls[offset // batch]
                entries = []
                for doc in documents[offset:offset + batch]:
                    ready, values, started = Event(), [], perf_counter_ns()

                    def deliver(value, ready=ready, values=values, started=started):
                        values.append((value, (perf_counter_ns() - started) / 1e6))
                        ready.set()

                    handle = service.get_cover(doc, "detail", deliver)
                    assert handle.source_uri == url
                    entries.append((handle, ready, values))
                # Prevent an early physical HIT from hiding a duplicated
                # in-flight GET. No transport wait on the submitting thread.
                release[url].set()
                for handle, ready, values in entries:
                    assert ready.wait(10)
                    assert len(values) == 1
                    value, duration = values[0]
                    assert value == loader._path(url)
                    durations.append(duration)
                    handle.cancel()
                assert loader._path(url).read_bytes() == PNG
            elapsed = (perf_counter_ns() - wall) / 1e6
            cpu_ms = (process_time_ns() - cpu) / 1e6
            io_after = _process_io()
            after, media = service.stats(), loader.stats()
            unique = len(urls)
            assert after["n2_miss"] - before["n2_miss"] == expected_misses
            assert after["n2_hit"] - before["n2_hit"] == size - expected_misses
            assert after["n1_selected_count"] - before["n1_selected_count"] == expected_misses
            assert media["download_ok"] - media_before["download_ok"] == (unique if cold else 0)
            assert media["deduplicated"] - media_before["deduplicated"] == (size - unique if cold else 0)
            assert media["cache_hit_valid"] - media_before["cache_hit_valid"] == (0 if cold else size)
            written = media["n3_url_published_bytes"] - media_before["n3_url_published_bytes"]
            assert written == (unique * len(PNG) if cold else 0)
            assert media["download_failed"] == 0 and media["ui_decode_count"] == 0
            report["phases"][name] = {
                "callback_latency": dist(durations),
                "wall_ms": round(elapsed, 3), "process_cpu_ms": round(cpu_ms, 3),
                "rss_highwater_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "n2_misses": expected_misses, "n2_hits": size - expected_misses,
                "n3_gets": unique if cold else 0,
                "n3_coalesced": size - unique if cold else 0,
                "n3_logical_published_bytes": written,
                "process_io_delta": {key: io_after[key] - io_before[key]
                                     for key in io_before.keys() & io_after.keys()},
            }

        phase("cold_shared_media", expected_misses=size, cold=True)
        phase("warm_shared_media", expected_misses=0, cold=False)
        # One exact decision per shared URL changes, without changing its
        # physical image or the seven sibling documentary decisions.
        for snapshot in snapshots[::batch]:
            service.register(replace(snapshot, revision=2))
        phase("targeted_revision_warm_media", expected_misses=len(urls), cold=False)
        assert sorted(transfers) == sorted(urls)
        with capsys.disabled():
            print("COVER_SHARED_N3_BASELINE " + json.dumps(report, sort_keys=True), flush=True)
    finally:
        for event in release.values():
            event.set()
        service.close()
        loader.close()
