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
