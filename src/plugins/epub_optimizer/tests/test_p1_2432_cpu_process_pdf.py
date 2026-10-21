"""Synthetic PDFs, real spawn workers and injected failures; no accounts/network."""
from __future__ import annotations

import io
import multiprocessing as mp
import os
import threading
from dataclasses import asdict
from types import SimpleNamespace

import pymupdf
import pytest
from PIL import Image

from cifereca_epub_optimizer import pdf_engine as engine
from cifereca_epub_optimizer.builtin_profiles import builtin_profile
from cifereca_epub_optimizer.engine import CancelledError, _optimize_image


def make_pdf(path, count=6, *, alpha=False):
    with pymupdf.open() as doc:
        for index in range(count):
            page = doc.new_page(width=400, height=500)
            page.insert_text((20, 20), f"Synthetic page {index}")
            page.draw_rect(pymupdf.Rect(15, 25, 350, 450))
            image = Image.new("RGBA" if alpha else "RGB", (320, 480),
                              (index * 31, 80, 120, 64) if alpha else (index * 31, 80, 120))
            out = io.BytesIO()
            image.save(out, "PNG" if alpha else "JPEG", quality=95)
            page.insert_image(pymupdf.Rect(20, 30, 300, 420), stream=out.getvalue())
        doc.save(path)


def run(source, target, **kw):
    return engine.optimize_pdf(source, target, profile=builtin_profile("xteink-x3"), **kw)


def contents(path):
    with pymupdf.open(path) as doc:
        return [(tuple(page.rect), page.get_text(), page.get_drawings(),
                 [doc.extract_image(row[0])["image"] for row in page.get_images(full=True)])
                for page in doc]


def test_real_spawn_1_2_4_have_identical_content_changes_and_intact_source(tmp_path):
    source = tmp_path / "source.pdf"
    make_pdf(source)
    original = source.read_bytes()
    results = []
    for workers in (1, 2, 4):
        target = tmp_path / f"mode-{workers}.pdf"
        percentages = []
        report = run(source, target, cpu_workers=workers,
                     progress=lambda percent, _: percentages.append(percent))
        assert report.images_optimized == 6
        assert not report.image_errors
        assert source.read_bytes() == original
        assert report.output_sha256 == engine._sha256(target)
        assert percentages == sorted(percentages) and percentages[-1] == 100
        results.append((contents(target), [asdict(change) for change in report.changes]))
    assert results[0] == results[1] == results[2]


@pytest.mark.parametrize("bad", [0, -1, 3, 5, True, "2", 1.5, None])
def test_cpu_option_rejected_before_creating_any_output(tmp_path, bad):
    with pytest.raises(engine.PdfOptimizationError, match="cpu_workers"):
        run(tmp_path / "missing.pdf", tmp_path / "new" / "out.pdf", cpu_workers=bad)
    assert not (tmp_path / "new").exists()


class Immediate:
    def __init__(self, value): self.value = value
    def get(self, timeout): return self.value


class RecordingPool:
    def __init__(self):
        self.jobs = []
        self._pool = []
        self.closed = False
        self.outstanding = 0
        self.max_outstanding = 0
        self.batch_bytes = 0
        self.max_bytes = 0
    def __enter__(self): return self
    def __exit__(self, *_): self.closed = True
    def apply_async(self, function, args, kwds):
        assert function is _optimize_image
        assert isinstance(args[0], bytes) and isinstance(args[1], str)
        assert set(kwds) == {"target_box", "max_image_pixels"}
        self.jobs.append((args, kwds))
        self.outstanding += 1
        self.max_outstanding = max(self.max_outstanding, self.outstanding)
        self.batch_bytes += len(args[0])
        self.max_bytes = max(self.max_bytes, self.batch_bytes)
        pool = self
        value = function(*args, **kwds)
        class Result:
            def get(self, timeout):
                pool.outstanding -= 1
                if not pool.outstanding: pool.batch_bytes = 0
                return value
        return Result()


def install_pool(monkeypatch, pool):
    def context(method):
        assert method == "spawn"
        return SimpleNamespace(Pool=lambda **kw: pool)
    monkeypatch.setattr(engine.mp, "get_context", context)


@pytest.mark.parametrize("budget", ["source", "decoded", "jobs"])
def test_batches_obey_aggregate_budgets(tmp_path, monkeypatch, budget):
    source, target = tmp_path / "source.pdf", tmp_path / "out.pdf"
    make_pdf(source)
    with pymupdf.open(source) as doc:
        sizes = [len(doc.extract_image(page.get_images()[0][0])["image"]) for page in doc]
    if budget == "source":
        monkeypatch.setattr(engine, "_CPU_BATCH_BYTES", max(sizes) * 2)
    if budget == "decoded":
        monkeypatch.setattr(engine, "_CPU_BATCH_WORKING_BYTES", 320 * 480 * 16 * 2 + max(sizes) * 4)
    pool = RecordingPool()
    install_pool(monkeypatch, pool)
    report = run(source, target, cpu_workers=4)
    assert pool.jobs and pool.closed
    assert pool.max_outstanding <= (4 if budget == "jobs" else 2)
    assert pool.max_bytes <= engine._CPU_BATCH_BYTES
    assert report.images_optimized == 6


@pytest.mark.parametrize("scenario", ["single", "source", "decoded", "alpha"])
def test_small_oversized_or_masked_images_do_not_create_workers(tmp_path, monkeypatch, scenario):
    source, target = tmp_path / "source.pdf", tmp_path / "out.pdf"
    make_pdf(source, 1 if scenario == "single" else 4, alpha=scenario == "alpha")
    if scenario == "source": monkeypatch.setattr(engine, "_CPU_BATCH_BYTES", 4)
    if scenario == "decoded": monkeypatch.setattr(engine, "_CPU_BATCH_WORKING_BYTES", 4)
    monkeypatch.setattr(engine.mp, "get_context", lambda _: pytest.fail("unnecessary worker"))
    report = run(source, target, cpu_workers=4)
    assert report.images_optimized + report.images_kept == report.images_seen


@pytest.mark.parametrize("where", ["context", "constructor"])
@pytest.mark.parametrize("failure", [OSError, RuntimeError])
def test_only_startup_failure_falls_back_with_public_note(tmp_path, monkeypatch, where, failure):
    source, baseline, target = (tmp_path / name for name in ("source.pdf", "base.pdf", "out.pdf"))
    make_pdf(source)
    run(source, baseline)
    def fail(*_, **__): raise failure("PRIVATE_DETAIL")
    monkeypatch.setattr(engine.mp, "get_context", fail if where == "context" else
                        lambda _: SimpleNamespace(Pool=fail))
    report = run(source, target, cpu_workers=2)
    assert contents(target) == contents(baseline)
    assert report.image_notes == ["Processus CPU indisponibles : repli séquentiel sécurisé."]
    assert "PRIVATE_DETAIL" not in repr(report)


@pytest.mark.parametrize("stage", ["before", "waiting", "rewrite", "validation", "validated"])
def test_cancellation_preserves_existing_target_and_removes_temporaries(tmp_path, monkeypatch, stage):
    source, target = tmp_path / "source.pdf", tmp_path / "out.pdf"
    make_pdf(source)
    original = source.read_bytes()
    target.write_bytes(b"PREVIOUS_RESULT")
    stopped = threading.Event()
    pool = RecordingPool()
    if stage == "before": stopped.set()
    if stage == "waiting":
        class Waiting:
            def get(self, timeout):
                assert timeout <= .2
                stopped.set()
                raise mp.TimeoutError()
        pool.apply_async = lambda *_, **__: Waiting()
        install_pool(monkeypatch, pool)
    if stage == "validated":
        validate = engine._validate_output
        def cancel_after(*args, **kw):
            validate(*args, **kw)
            stopped.set()
        monkeypatch.setattr(engine, "_validate_output", cancel_after)
    def progress(pct, _):
        if (stage == "rewrite" and pct == 93) or (stage == "validation" and pct == 97):
            stopped.set()
    with pytest.raises(CancelledError):
        run(source, target, cpu_workers=2, cancel_event=stopped, progress=progress)
    assert source.read_bytes() == original
    assert target.read_bytes() == b"PREVIOUS_RESULT"
    assert not list(tmp_path.glob(".*.tmp.pdf"))
    if stage == "waiting": assert pool.closed


@pytest.mark.parametrize("failure", [BrokenPipeError, EOFError, MemoryError])
def test_worker_failure_aborts_without_retry_or_partial_publication(tmp_path, monkeypatch, failure):
    source, target = tmp_path / "source.pdf", tmp_path / "out.pdf"
    make_pdf(source)
    target.write_bytes(b"PREVIOUS_RESULT")
    pool = RecordingPool()
    class Broken:
        def get(self, timeout): raise failure("PRIVATE_DETAIL")
    pool.apply_async = lambda *_, **__: Broken()
    install_pool(monkeypatch, pool)
    with pytest.raises(engine.PdfOptimizationError, match="^cpu_image_worker_failed$"):
        run(source, target, cpu_workers=2)
    assert pool.closed
    assert target.read_bytes() == b"PREVIOUS_RESULT"
    assert not list(tmp_path.glob(".*.tmp.pdf"))


def test_timeout_is_fatal_and_cleans_pool(tmp_path, monkeypatch):
    source, target = tmp_path / "source.pdf", tmp_path / "out.pdf"
    make_pdf(source)
    monkeypatch.setattr(engine, "_CPU_WAIT_SECONDS", 0)
    pool = RecordingPool()
    install_pool(monkeypatch, pool)
    with pytest.raises(engine.PdfOptimizationError, match="cpu_image_worker_timeout"):
        run(source, target, cpu_workers=2)
    assert pool.closed and not target.exists()


def _exit_worker(*_, **__):
    os._exit(23)


def test_real_killed_worker_detected_and_joined(tmp_path, monkeypatch):
    source, target = tmp_path / "source.pdf", tmp_path / "out.pdf"
    make_pdf(source)
    context = mp.get_context("spawn")
    real_pool = context.Pool(processes=2)
    children = tuple(real_pool._pool)
    original_submit = real_pool.apply_async
    def submit(_function, args, kwds):
        return original_submit(_exit_worker, args, kwds)
    real_pool.apply_async = submit
    install_pool(monkeypatch, real_pool)
    with pytest.raises(engine.PdfOptimizationError, match="cpu_image_worker_failed"):
        run(source, target, cpu_workers=2)
    assert not target.exists()
    assert all(not child.is_alive() for child in children)


def test_transformer_validates_and_forwards_cpu_config():
    from cifereca_pdf_optimizer_transformer import PdfOptimizerTransformer
    transformer = PdfOptimizerTransformer()
    transformer.context = SimpleNamespace(config={"cpu_workers": 4})
    assert transformer._cpu_workers(SimpleNamespace(options={})) == 4
    assert transformer._cpu_workers(SimpleNamespace(options={"cpu_workers": 1})) == 1
    for bad in (True, "2", 0, 1.5):
        with pytest.raises(ValueError, match="cpu_workers"):
            transformer._cpu_workers(SimpleNamespace(options={"cpu_workers": bad}))


def test_single_cpu_clamps_to_sequential_with_notice(tmp_path, monkeypatch):
    source, target = tmp_path / "source.pdf", tmp_path / "out.pdf"
    make_pdf(source)
    monkeypatch.setattr(engine.os, "cpu_count", lambda: 1)
    monkeypatch.setattr(engine.mp, "get_context", lambda _: pytest.fail("single CPU pool"))
    report = run(source, target, cpu_workers=4)
    assert report.images_optimized == 6
    assert report.image_notes == ["Processus CPU limités à 1 (CPU disponibles)."]


def test_transformer_plan_and_execution_use_same_cpu_option(tmp_path, monkeypatch):
    from cifereca_pdf_optimizer_transformer import PdfOptimizerTransformer, CAPABILITY_ID
    source, target = tmp_path / "source.pdf", tmp_path / "out.pdf"
    make_pdf(source)
    transformer = PdfOptimizerTransformer()
    transformer.context = SimpleNamespace(config={"cpu_workers": 4},
        report_diagnostic=lambda *args, **kw: None)
    monkeypatch.setattr(transformer, "_profile", lambda _: ("device.profile.xteink-x3", builtin_profile("xteink-x3")))
    request = SimpleNamespace(capability_id=CAPABILITY_ID, options={"cpu_workers": 2})
    control = SimpleNamespace(check_cancelled=lambda: None, cancelled=lambda: False,
                              report=lambda **kw: None)
    source_ref = SimpleNamespace(path=source)
    plan = transformer.plan(request, source_ref, control)
    result = transformer.transform(request, source_ref, SimpleNamespace(path=target), control)
    assert plan.facts["cpu_workers"] == result.facts["cpu_workers"] == 2
    assert result.facts["images_optimized"] == 6
    assert result.sha256 == engine._sha256(target)


@pytest.mark.parametrize("stage", ["save", "validate", "publish"])
def test_write_or_validation_failure_rolls_back(tmp_path, monkeypatch, stage):
    source, target = tmp_path / "source.pdf", tmp_path / "out.pdf"
    make_pdf(source)
    original = source.read_bytes()
    target.write_bytes(b"PREVIOUS_RESULT")
    def fail(*_, **__): raise OSError("synthetic write refusal")
    if stage == "save": monkeypatch.setattr(pymupdf.Document, "save", fail)
    if stage == "validate": monkeypatch.setattr(engine, "_validate_output", fail)
    if stage == "publish": monkeypatch.setattr(engine.os, "replace", fail)
    with pytest.raises(engine.PdfOptimizationError):
        run(source, target, cpu_workers=2)
    assert source.read_bytes() == original
    assert target.read_bytes() == b"PREVIOUS_RESULT"
    assert not list(tmp_path.glob(".*.tmp.pdf"))


def test_source_path_cannot_be_overwritten(tmp_path):
    source = tmp_path / "source.pdf"
    make_pdf(source)
    original = source.read_bytes()
    with pytest.raises(engine.PdfOptimizationError, match="différente"):
        run(source, source, cpu_workers=4)
    assert source.read_bytes() == original


def test_protected_pdf_refused_before_pool_creation(tmp_path, monkeypatch):
    source, target = tmp_path / "source.pdf", tmp_path / "out.pdf"
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(source, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                 owner_pw="synthetic-owner", user_pw="synthetic-reader")
    monkeypatch.setattr(engine.mp, "get_context", lambda _: pytest.fail("protected PDF worker"))
    with pytest.raises(engine.PdfOptimizationError, match="protégé"):
        run(source, target, cpu_workers=4)
    assert not target.exists()


def _sigkill_worker(*_, **__):
    os.kill(os.getpid(), 9)


def test_real_sigkill_as_oom_kill_surrogate_aborts_atomically(tmp_path, monkeypatch):
    source, target = tmp_path / "source.pdf", tmp_path / "out.pdf"
    make_pdf(source)
    context = mp.get_context("spawn")
    real_pool = context.Pool(processes=2)
    children = tuple(real_pool._pool)
    submit = real_pool.apply_async
    real_pool.apply_async = lambda _fn, args, kwds: submit(_sigkill_worker, args, kwds)
    install_pool(monkeypatch, real_pool)
    with pytest.raises(engine.PdfOptimizationError, match="cpu_image_worker_failed"):
        run(source, target, cpu_workers=2)
    assert not target.exists()
    assert all(not child.is_alive() for child in children)
