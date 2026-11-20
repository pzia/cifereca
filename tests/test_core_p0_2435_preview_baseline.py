"""#2435: preview cost on a bounded synthetic JPEG corpus.

Production scheduling/cache/publication. GdkPixbuf if installed; otherwise
explicit Pillow backend for the same preview contract, NOT GTK/Gdk evidence.
Run pytest -s tests/test_core_p0_2435_preview_baseline.py.
"""
from __future__ import annotations

import json
from pathlib import Path
import resource
from threading import Event
from time import perf_counter_ns, process_time_ns

import httpx
import statistics
from PIL import Image, ImageDraw

from cifereca.gui import cover_loader
from cifereca.gui.cover_preview import create_preview
def dist(samples):
    ordered = sorted(samples)
    return {"count": len(ordered), "p50_ms": round(statistics.median(ordered), 4),
            "p95_ms": round(ordered[(len(ordered) * 95 + 99) // 100 - 1], 4),
            "max_ms": round(ordered[-1], 4)}


class PillowBackend:
    """Measurement adapter only; production continues to use GdkPixbuf."""
    @staticmethod
    def get_file_info(path):
        with Image.open(path) as image:
            return image.format, image.width, image.height

    @staticmethod
    def new_from_file_at_scale(path, width, height, preserve):
        assert preserve is True
        with Image.open(path) as image:
            resized = image.copy()
            resized.thumbnail((width, height), Image.Resampling.BILINEAR)
        return PillowBackend(resized)

    def __init__(self, image):
        self.image = image

    def savev(self, path, kind, keys, values):
        assert kind == "png" and not keys and not values
        self.image.save(path, format="PNG")
        return True


def _process_io():
    # Linux kernel accounting, including test/process/library activity.
    # Neither page-cache eviction nor physical-device telemetry is claimed.
    try:
        values = dict(line.split(": ", 1) for line in
                      Path("/proc/self/io").read_text().splitlines())
        return {key: int(values[key]) for key in
                ("rchar", "wchar", "read_bytes", "write_bytes")}
    except (OSError, KeyError, ValueError):
        return {}


def run_preview_cache_concurrency_baseline(tmp_path, monkeypatch, size, *, require_gdk=False):
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
    except (ImportError, ValueError):
        if require_gdk:
            raise RuntimeError("GdkPixbuf required for native preview baseline") from None
        backend = "pillow_adapter"
        monkeypatch.setattr(cover_loader, "create_preview",
                            lambda source, dest, w, h: create_preview(
                                source, dest, w, h, backend=PillowBackend))
    else:
        assert GdkPixbuf.Pixbuf is not None
        backend = "gdkpixbuf"

    monkeypatch.setattr(cover_loader, "cache_dir", lambda: tmp_path)
    loader = cover_loader.CoverLoader(max_workers=2)
    old = loader.client
    gets = []

    def reject(req):
        gets.append(req.method)
        raise AssertionError("preview baseline must never request network")

    loader.client = httpx.Client(transport=httpx.MockTransport(reject), trust_env=False)
    old.close()
    urls = [f"https://preview.example.test/{i}.jpg" for i in range(size)]
    width, height = 180, 270
    report = {"documents": size, "source_dimensions": [600, 900],
              "preview_dimensions": [width, height], "backend": backend,
              "preview_workers": 2, "physical_device_io_measured": False,
              "gtk_paint_measured": False, "phases": {}}
    try:
        base = Image.linear_gradient("L").resize((600, 900)).convert("RGB")
        for i, url in enumerate(urls):
            image = base.copy()
            ImageDraw.Draw(image).rectangle((10, 10, 220, 220),
                                           fill=(i & 255, 80, 160))
            image.save(loader._path(url), format="JPEG", quality=85)
        original_bytes = {url: loader._path(url).stat().st_size for url in urls}
        report["source_corpus_bytes"] = sum(original_bytes.values())

        def phase(name, *, batch, cold):
            before = loader.stats()
            io_before = _process_io()
            cpu, wall = process_time_ns(), perf_counter_ns()
            durations = []
            targets = []
            for offset in range(0, size, batch):
                entries = []
                for url in urls[offset:offset + batch]:
                    ready, results = Event(), []
                    started = perf_counter_ns()

                    def deliver(path, ready=ready, results=results, started=started):
                        results.append((path, (perf_counter_ns() - started) / 1e6))
                        ready.set()

                    handle = loader.load_preview(url, deliver, width=width, height=height)
                    entries.append((ready, results, handle))
                for ready, results, handle in entries:
                    assert ready.wait(10), "scratch preview callback timeout"
                    assert len(results) == 1
                    target, duration = results[0]
                    assert target is not None and target.parent == loader.preview_root
                    targets.append(target)
                    durations.append(duration)
                    handle.cancel()
            elapsed = (perf_counter_ns() - wall) / 1e6
            cpu_ms = (process_time_ns() - cpu) / 1e6
            io_after = _process_io()
            after = loader.stats()
            assert after["preview_created"] - before["preview_created"] == (size if cold else 0)
            assert after["preview_hit"] - before["preview_hit"] == (0 if cold else size)
            assert after["preview_fallback"] == 0
            assert after["cache_hit_valid"] - before["cache_hit_valid"] == size
            assert after["download_ok"] == after["download_failed"] == 0 and not gets
            assert len(set(targets)) == size
            for target in targets:
                with Image.open(target) as image:
                    assert image.size == (width, height)
                    image.verify()
            assert all(loader._path(url).stat().st_size == original_bytes[url] for url in urls)
            report["phases"][name] = {
                "batch": batch, "callback_latency": dist(durations),
                "wall_ms": round(elapsed, 3), "process_cpu_ms": round(cpu_ms, 3),
                "rss_highwater_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "preview_created": size if cold else 0, "preview_hits": 0 if cold else size,
                "preview_file_bytes": sum(target.stat().st_size for target in targets),
                "process_io_delta": {key: io_after[key] - io_before[key]
                                     for key in io_before.keys() & io_after.keys()},
            }
            return targets

        for batch in (1, 8):
            targets = phase(f"batch{batch}_cold", batch=batch, cold=True)
            assert targets == phase(f"batch{batch}_warm", batch=batch, cold=False)
            # Drop only scratch derivatives, not source bytes or OS page cache.
            for target in targets:
                target.unlink()
        assert loader.stats()["preview_created"] == 2 * size
        assert loader.stats()["preview_hit"] == 2 * size
        assert not gets
        return report
    finally:
        loader.close()


if __name__ == "__main__":
    # The existing GTK venv has real GI/Pillow/httpx but not necessarily
    # pytest. Do not install another runtime or fall back to Pillow here.
    from contextlib import ExitStack
    from tempfile import TemporaryDirectory
    from types import SimpleNamespace
    from unittest.mock import patch

    for size in (16, 256):
        with TemporaryDirectory(prefix="cifereca-preview-baseline-") as tmp, ExitStack() as stack:
            monkeypatch = SimpleNamespace(
                setattr=lambda obj, name, value: stack.enter_context(patch.object(obj, name, value))
            )
            report = run_preview_cache_concurrency_baseline(
                Path(tmp), monkeypatch, size, require_gdk=True)
            assert report["backend"] == "gdkpixbuf"
            print("COVER_PREVIEW_BASELINE " + json.dumps(report, sort_keys=True), flush=True)
else:
    import pytest

    @pytest.mark.parametrize("size", (16, 256))
    def test_preview_cache_concurrency_baseline(tmp_path, monkeypatch, capsys, size):
        report = run_preview_cache_concurrency_baseline(tmp_path, monkeypatch, size)
        with capsys.disabled():
            print("COVER_PREVIEW_BASELINE " + json.dumps(report, sort_keys=True), flush=True)
