"""#773: generated simulation documents exercise actual optimizer engines."""
from hashlib import sha256
from pathlib import Path
from threading import Event
from zipfile import ZipFile

import pymupdf
import pytest

from cifereca_epub_optimizer.builtin_profiles import builtin_profile
from cifereca_epub_optimizer.engine import (
    CancelledError, DrmProtectedError, EpubOptimizationError, optimize_epub,
)
from cifereca_epub_optimizer.pdf_engine import (
    PdfOptimizationError, inspect_pdf, optimize_pdf,
)


@pytest.fixture
def documents(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[4] / "simulation"))
    from plugin_scenarios import create
    root = tmp_path / "dataset"
    manifest = create(root)
    return root, manifest


def test_generated_epub_real_optimizer_preserves_source_and_document_metadata(documents, tmp_path):
    root, manifest = documents
    source = root / "optimizer-valid.epub"
    target = tmp_path / "optimized.epub"
    original = source.read_bytes()
    report = optimize_epub(source, target, profile=builtin_profile("generic-eink"))
    assert report.source_sha256 == manifest["files"][source.name]
    assert report.output_sha256 == sha256(target.read_bytes()).hexdigest()
    assert source.read_bytes() == original
    assert report.images_seen >= 1
    with ZipFile(source) as before, ZipFile(target) as after:
        assert after.testzip() is None
        assert set(before.namelist()) == set(after.namelist())
        for name in before.namelist():
            if name.endswith((".opf", ".xhtml", ".xml")):
                assert before.read(name) == after.read(name)


@pytest.mark.parametrize("name,pages,images", [
    ("publication-text.pdf", 2, 0), ("publication-scan.pdf", 1, 1),
])
def test_generated_pdf_real_optimizer_preserves_pages_and_text(documents, tmp_path, name, pages, images):
    root, manifest = documents
    source = root / name
    target = tmp_path / "optimized.pdf"
    original = source.read_bytes()
    profile = builtin_profile("generic-eink")
    inspection = inspect_pdf(source, profile=profile)
    assert (inspection.pages, inspection.images_seen) == (pages, images)
    report = optimize_pdf(source, target, profile=profile)
    assert report.pages == pages
    assert report.source_sha256 == manifest["files"][source.name]
    assert report.output_sha256 == sha256(target.read_bytes()).hexdigest()
    with pymupdf.open(source) as before, pymupdf.open(target) as after:
        assert before.page_count == after.page_count == pages
        for old, new in zip(before, after):
            assert tuple(old.rect) == tuple(new.rect)
            assert old.get_text("text") == new.get_text("text")
        if images:
            assert after[0].get_images()
            assert not after[0].get_text("text").strip()
        else:
            assert "Synthetic page one" in after[0].get_text("text")
    assert source.read_bytes() == original


@pytest.mark.parametrize("name,engine,error", [
    ("optimizer-corrupt.epub", optimize_epub, EpubOptimizationError),
    ("publication-corrupt.pdf", optimize_pdf, PdfOptimizationError),
])
def test_corrupt_fixture_refused_without_output(documents, tmp_path, name, engine, error):
    root, _ = documents
    source = root / name
    target = tmp_path / ("out" + source.suffix)
    before = source.read_bytes()
    with pytest.raises(error):
        engine(source, target, profile=builtin_profile("generic-eink"))
    assert not target.exists() and source.read_bytes() == before


@pytest.mark.parametrize("name,engine", [
    ("optimizer-valid.epub", optimize_epub),
    ("publication-scan.pdf", optimize_pdf),
])
def test_cancellation_keeps_original_and_existing_destination(documents, tmp_path, name, engine):
    root, _ = documents
    source = root / name
    target = tmp_path / ("out" + source.suffix)
    target.write_bytes(b"independent-sentinel")
    before = source.read_bytes()
    cancelled = Event()
    cancelled.set()
    with pytest.raises(CancelledError):
        engine(source, target, profile=builtin_profile("generic-eink"), cancel_event=cancelled)
    assert target.read_bytes() == b"independent-sentinel"
    assert source.read_bytes() == before


def test_synthetic_protection_marker_refused_without_decryption(documents, tmp_path):
    root, manifest = documents
    source = root / "optimizer-protected.epub"
    before = source.read_bytes()
    assert sha256(before).hexdigest() == manifest["files"][source.name]
    # Refusal witness only: the generated archive has no encrypted payload,
    # key, license or decryption.
    with ZipFile(source) as archive:
        assert b"synthetic-unsupported" in archive.read("META-INF/encryption.xml")
    target = tmp_path / "out.epub"
    with pytest.raises(DrmProtectedError):
        optimize_epub(source, target, profile=builtin_profile("generic-eink"))
    assert source.read_bytes() == before and not target.exists()


def test_compact_grayscale_fixture_keeps_compatible_noncompressible_image(documents, tmp_path):
    root, manifest = documents
    source = root / "optimizer-compact.epub"
    original = source.read_bytes()
    target = tmp_path / "compact-out.epub"
    report = optimize_epub(source, target, profile=builtin_profile("generic-eink"))
    assert report.source_sha256 == manifest["files"][source.name]
    assert report.output_sha256 == sha256(target.read_bytes()).hexdigest()
    assert (report.images_seen, report.images_optimized, report.images_kept) == (1, 0, 1)
    with ZipFile(source) as before, ZipFile(target) as after:
        assert after.testzip() is None
        assert before.read("OEBPS/cover.png") == after.read("OEBPS/cover.png")
        assert set(before.namelist()) == set(after.namelist())
        assert all(before.read(name) == after.read(name) for name in before.namelist())
    assert source.read_bytes() == original
