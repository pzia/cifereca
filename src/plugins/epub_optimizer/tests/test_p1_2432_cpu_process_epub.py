"""P1 #2432 — bounded opt-in CPU process backend, no GTK or network.

All ZIPs and raster data are synthetic. Tests run with real spawned
workers for 1/2/4 and a synthetic timeout for cleanup and cancellation.
"""
from __future__ import annotations

import io
import multiprocessing as mp
import threading
import zipfile
from pathlib import Path

import pytest
from PIL import Image

import cifereca_epub_optimizer.engine as engine
from cifereca_epub_optimizer.builtin_profiles import builtin_profile
from tests.test_engine import MIMETYPE, CONTAINER, OPF, XHTML


def _image(seed: int):
    out = io.BytesIO()
    img = Image.new("RGB", (320, 480), (seed * 45, 80, 180))
    img.save(out, format="JPEG", quality=95)
    return out.getvalue()


def _many_pages(path: Path, count=4):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", MIMETYPE, compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("OEBPS/content.opf", OPF)
        zf.writestr("OEBPS/page.xhtml", XHTML)
        for i in range(count):
            zf.writestr(f"OEBPS/images/{i:03}.jpg", _image(i))


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_cpu_modes_preserve_zip_order_content_and_source(tmp_path, workers):
    source = tmp_path / "source.epub"
    target = tmp_path / f"mode-{workers}.epub"
    _many_pages(source)
    source_before = source.read_bytes()
    report = engine.optimize_epub(
        source, target, profile=builtin_profile("xteink-x3"),
        cpu_workers=workers,
    )
    assert report.images_seen == 4
    assert report.images_optimized == 4
    assert report.images_kept == 0
    assert report.source_sha256
    assert report.output_sha256 == engine._sha256(target)
    assert source.read_bytes() == source_before
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target) as result:
        assert original.namelist() == result.namelist()
        assert result.infolist()[0].compress_type == zipfile.ZIP_STORED
        assert result.read("mimetype") == MIMETYPE
        for name in original.namelist():
            if not name.endswith(".jpg"):
                assert original.read(name) == result.read(name)
    baseline = tmp_path / "mode-1.epub"
    if baseline.exists() and workers != 1:
        with zipfile.ZipFile(baseline) as expected, zipfile.ZipFile(target) as actual:
            assert [actual.read(n) for n in actual.namelist()] == [
                expected.read(n) for n in expected.namelist()
            ]


@pytest.mark.parametrize("bad", [0, -1, 3, 5, True, "2", 1.5])
def test_unsupported_cpu_config_denied_before_output(tmp_path, bad):
    source = tmp_path / "book.epub"
    _many_pages(source, 1)
    target = tmp_path / "bad.epub"
    with pytest.raises(engine.EpubOptimizationError, match="cpu_workers"):
        engine.optimize_epub(
            source, target, profile=builtin_profile("xteink-x3"),
            cpu_workers=bad,
        )
    assert not target.exists()


def test_cancellation_terminates_pool_and_removes_temporary_output(
    tmp_path, monkeypatch,
):
    source = tmp_path / "book.epub"
    target = tmp_path / "out.epub"
    _many_pages(source)
    stopped = threading.Event()

    class NeverCompletes:
        def get(self, timeout):
            assert timeout <= 0.2
            stopped.set()
            raise mp.TimeoutError()

    class FakePool:
        def __enter__(self): return self
        def __exit__(self, *_args): self.closed = True
        def apply_async(self, *_args, **_kw): return NeverCompletes()

    pool = FakePool()
    monkeypatch.setattr(
        engine.mp, "get_context",
        lambda start_method: type("Context", (), {
            "Pool": lambda self, **kw: pool,
        })(),
    )
    with pytest.raises(engine.CancelledError):
        engine.optimize_epub(
            source, target, profile=builtin_profile("xteink-x3"),
            cpu_workers=2, cancel_event=stopped,
        )
    assert pool.closed
    assert not target.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_crashed_worker_aborts_atomically_not_silent_success(tmp_path, monkeypatch):
    source = tmp_path / "book.epub"
    target = tmp_path / "out.epub"
    _many_pages(source)
    class Crashed:
        def get(self, timeout): raise BrokenPipeError("private worker payload")
    class Pool:
        def __enter__(self): return self
        def __exit__(self, *_a): self.closed=True
        def apply_async(self,*_a,**_k): return Crashed()
    pool=Pool()
    monkeypatch.setattr(
        engine.mp, "get_context",
        lambda start_method: type("Context", (), {
            "Pool": lambda self, **kw: pool,
        })(),
    )
    with pytest.raises(engine.EpubOptimizationError, match="cpu_image_worker_failed"):
        engine.optimize_epub(
            source, target, profile=builtin_profile("xteink-x3"),
            cpu_workers=2,
        )
    assert pool.closed
    assert not target.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_large_images_do_not_enter_pool(tmp_path, monkeypatch):
    source = tmp_path / "book.epub"
    target = tmp_path / "out.epub"
    _many_pages(source, 1)
    monkeypatch.setattr(engine, "_CPU_BATCH_BYTES", 4)
    class Pool:
        calls = 0
        def __enter__(self): return self
        def __exit__(self, *_a): pass
        def apply_async(self, *_args, **_kwargs):
            self.calls += 1
            pytest.fail("too-large payload submitted to worker")
    pool=Pool()
    monkeypatch.setattr(
        engine.mp, "get_context",
        lambda start_method: type("Context", (), {
            "Pool": lambda self, **kw: pool,
        })(),
    )
    report=engine.optimize_epub(
        source, target, profile=builtin_profile("xteink-x3"),
        cpu_workers=4,
    )
    assert report.images_seen == 1
    assert pool.calls == 0 and target.is_file()



@pytest.mark.parametrize("failure", [OSError, RuntimeError])
def test_pool_start_failure_falls_back_to_sequential_with_explicit_notice(
    tmp_path, monkeypatch, failure,
):
    """An unstarted CPU pool is recoverable; no worker task is retried."""
    source = tmp_path / "source.epub"
    baseline = tmp_path / "sequential.epub"
    target = tmp_path / "fallback.epub"
    _many_pages(source)
    before = source.read_bytes()
    sequential = engine.optimize_epub(
        source, baseline, profile=builtin_profile("xteink-x3"),
        cpu_workers=1,
    )

    def refuse_pool(_method):
        class Context:
            def Pool(self, **_kwargs):
                raise failure("SYNTHETIC_PRIVATE_FAILURE_DETAIL")
        return Context()

    monkeypatch.setattr(engine.mp, "get_context", refuse_pool)
    report = engine.optimize_epub(
        source, target, profile=builtin_profile("xteink-x3"),
        cpu_workers=2,
    )

    assert report.images_optimized == sequential.images_optimized == 4
    assert report.images_kept == sequential.images_kept
    assert report.image_notes == [
        "Processus CPU indisponibles : repli séquentiel sécurisé."
    ]
    assert "SYNTHETIC_PRIVATE_FAILURE_DETAIL" not in repr(report)
    assert source.read_bytes() == before
    assert target.read_bytes() == baseline.read_bytes()
    assert report.output_sha256 == engine._sha256(target)
    assert not list(tmp_path.glob(".*.tmp"))


def test_single_image_epub_never_starts_pool(tmp_path, monkeypatch):
    source, target = tmp_path / "small.epub", tmp_path / "out.epub"
    _many_pages(source, 1)
    monkeypatch.setattr(engine.mp, "get_context", lambda _: pytest.fail("single image spawn overhead"))
    for workers in (1, 2, 4):
        assert engine.optimize_epub(source, target, profile=builtin_profile("xteink-x3"),
                                    cpu_workers=workers).images_optimized == 1


def test_single_cpu_epub_falls_back_explicitly(tmp_path, monkeypatch):
    source, target = tmp_path / "source.epub", tmp_path / "out.epub"
    _many_pages(source)
    monkeypatch.setattr(engine.os, "cpu_count", lambda: 1)
    monkeypatch.setattr(engine.mp, "get_context", lambda _: pytest.fail("single CPU spawn"))
    report = engine.optimize_epub(source, target, profile=builtin_profile("xteink-x3"), cpu_workers=4)
    assert report.images_optimized == 4
    assert report.image_notes == ["Processus CPU limités à 1 (CPU disponibles)."]


def test_interleaved_epub_batches_cross_xhtml_and_keep_identical_order_and_payloads(tmp_path, monkeypatch):
    from tests.test_p1_2432_cpu_process_pdf import RecordingPool, install_pool
    source = tmp_path / "interleaved.epub"
    _many_pages(source)
    with zipfile.ZipFile(source) as archive:
        entries = [(info.filename, archive.read(info)) for info in archive.infolist()]
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data, compress_type=zipfile.ZIP_STORED)
            if name.endswith(".jpg"):
                archive.writestr(name + ".xhtml", b"<html/>")
    baseline = tmp_path / "baseline.epub"
    engine.optimize_epub(source, baseline, profile=builtin_profile("xteink-x3"))
    original = source.read_bytes()
    # Instrument real Pillow transformations; assert >=2 simultaneous image
    # tasks even though XHTML entries separate every pair of raster entries.
    class Pool(RecordingPool):
        def apply_async(self, function, args, kwds):
            assert "layout_hint" in kwds
            self.jobs.append(args)
            self.outstanding += 1
            self.max_outstanding = max(self.max_outstanding, self.outstanding)
            value = function(*args, **kwds)
            pool = self
            class Result:
                def get(self, timeout):
                    pool.outstanding -= 1
                    return value
            return Result()
    for workers in (2, 4):
        pool = Pool()
        install_pool(monkeypatch, pool)  # engine.mp is the same stdlib module
        target = tmp_path / f"out-{workers}.epub"
        report = engine.optimize_epub(source, target, profile=builtin_profile("xteink-x3"), cpu_workers=workers)
        assert pool.max_outstanding == workers and pool.closed
        assert len(pool.jobs) == report.images_optimized == 4
        with zipfile.ZipFile(baseline) as expected, zipfile.ZipFile(target) as result:
            assert result.namelist() == expected.namelist()
            assert [result.read(n) for n in result.namelist()] == [expected.read(n) for n in expected.namelist()]
    assert source.read_bytes() == original


def test_decoded_budget_keeps_large_epub_rasters_out_of_pool(tmp_path, monkeypatch):
    source, target = tmp_path / "source.epub", tmp_path / "out.epub"
    _many_pages(source)
    monkeypatch.setattr(engine, "_CPU_BATCH_WORKING_BYTES", 1)
    monkeypatch.setattr(engine.mp, "get_context", lambda _: pytest.fail("decoded budget exceeded"))
    report = engine.optimize_epub(source, target, profile=builtin_profile("xteink-x3"), cpu_workers=4)
    assert report.images_optimized == 4


@pytest.mark.parametrize("stage", ["waiting", "validation", "publish", "memory"])
def test_epub_failure_or_cancel_closes_pool_and_preserves_previous_target(tmp_path, monkeypatch, stage):
    from tests.test_p1_2432_cpu_process_pdf import RecordingPool, install_pool
    source, target = tmp_path / "source.epub", tmp_path / "out.epub"
    _many_pages(source)
    original = source.read_bytes()
    target.write_bytes(b"PREVIOUS_RESULT")
    cancelled = threading.Event()
    pool = RecordingPool()
    class Result:
        def get(self, timeout):
            if stage == "waiting":
                cancelled.set()
                raise mp.TimeoutError()
            raise MemoryError("synthetic OOM")
    if stage in ("waiting", "memory"):
        pool.apply_async = lambda *args, **kw: Result()
        install_pool(monkeypatch, pool)
    if stage == "publish":
        def refuse(*args, **kw): raise PermissionError("synthetic permission")
        monkeypatch.setattr(engine.os, "replace", refuse)
    def progress(percent, _):
        if stage == "validation" and percent == 95: cancelled.set()
    with pytest.raises(engine.EpubOptimizationError):
        engine.optimize_epub(source, target, profile=builtin_profile("xteink-x3"), cpu_workers=2,
                             cancel_event=cancelled, progress=progress)
    assert target.read_bytes() == b"PREVIOUS_RESULT" and source.read_bytes() == original
    assert not list(tmp_path.glob(".*.tmp"))
    if stage in ("waiting", "memory"): assert pool.closed


@pytest.mark.parametrize("crash", ["exit", "sigkill"])
def test_real_epub_killed_worker_leaves_source_and_target_intact(tmp_path, monkeypatch, crash):
    from tests.test_p1_2432_cpu_process_pdf import _exit_worker, _sigkill_worker, install_pool
    source, target = tmp_path / "source.epub", tmp_path / "out.epub"
    _many_pages(source)
    original = source.read_bytes()
    target.write_bytes(b"PREVIOUS_RESULT")
    pool = mp.get_context("spawn").Pool(processes=2)
    children = tuple(pool._pool)
    submit = pool.apply_async
    fail = _exit_worker if crash == "exit" else _sigkill_worker
    pool.apply_async = lambda _fn, args, kwds: submit(fail, args, kwds)
    install_pool(monkeypatch, pool)
    with pytest.raises(engine.EpubOptimizationError, match="cpu_image_worker_failed"):
        engine.optimize_epub(source, target, profile=builtin_profile("xteink-x3"), cpu_workers=2)
    assert all(not child.is_alive() for child in children)
    assert source.read_bytes() == original and target.read_bytes() == b"PREVIOUS_RESULT"
    assert not list(tmp_path.glob(".*.tmp"))


def test_real_epub_modes_have_identical_checksums_of_every_entry(tmp_path):
    source = tmp_path / "source.epub"
    _many_pages(source)
    original = source.read_bytes()
    outputs, changes = [], []
    for workers in (1, 2, 4):
        target = tmp_path / f"mode-{workers}.epub"
        report = engine.optimize_epub(source, target, profile=builtin_profile("xteink-x3"), cpu_workers=workers)
        with zipfile.ZipFile(target) as archive:
            outputs.append([(info.filename, info.CRC, archive.read(info)) for info in archive.infolist()])
        changes.append(report.changes)
        assert report.source_sha256 == engine._sha256(source)
        assert report.output_sha256 == engine._sha256(target)
        assert source.read_bytes() == original
    assert outputs[0] == outputs[1] == outputs[2]
    assert changes[0] == changes[1] == changes[2]


@pytest.mark.parametrize("budget", ["source", "decoded"])
def test_epub_inflight_aggregate_budget_admits_only_two_images(tmp_path, monkeypatch, budget):
    from tests.test_p1_2432_cpu_process_pdf import install_pool
    source, target = tmp_path / "source.epub", tmp_path / "out.epub"
    _many_pages(source)
    maximum_bytes = max(len(_image(i)) for i in range(4))
    if budget == "source": monkeypatch.setattr(engine, "_CPU_BATCH_BYTES", maximum_bytes * 2)
    if budget == "decoded": monkeypatch.setattr(engine, "_CPU_BATCH_WORKING_BYTES", (320 * 480 * 16 + maximum_bytes * 2) * 2)
    class Pool:
        _pool = ()
        pending = bytes_inflight = max_pending = max_bytes = 0
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def apply_async(self, function, args, kwds):
            self.pending += 1
            self.bytes_inflight += len(args[0])
            self.max_pending = max(self.max_pending, self.pending)
            self.max_bytes = max(self.max_bytes, self.bytes_inflight)
            value = function(*args, **kwds)
            pool = self
            class Result:
                def get(self, timeout):
                    pool.pending -= 1
                    pool.bytes_inflight -= len(args[0])
                    return value
            return Result()
    pool = Pool()
    install_pool(monkeypatch, pool)
    report = engine.optimize_epub(source, target, profile=builtin_profile("xteink-x3"), cpu_workers=4)
    assert pool.max_pending == 2
    assert pool.max_bytes <= engine._CPU_BATCH_BYTES
    assert report.images_optimized == 4 and not report.image_errors
