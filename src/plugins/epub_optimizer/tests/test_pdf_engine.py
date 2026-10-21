from __future__ import annotations

import io
import tempfile
from pathlib import Path
import threading

import pytest
from PIL import Image
import pymupdf

from cifereca_epub_optimizer.builtin_profiles import builtin_profile
from cifereca_epub_optimizer.engine import CancelledError
from cifereca_epub_optimizer.pdf_engine import _image_inventory, inspect_pdf, optimize_pdf


def jpeg(size=(1800, 2400)) -> bytes:
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(size[1]):
        value = y % 256
        for x in range(size[0]):
            pixels[x, y] = (value, x % 256, (x + y) % 256)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=90)
    return out.getvalue()


def rgba_png(size=(500, 500)) -> bytes:
    image = Image.new("RGBA", size, (255, 0, 0, 64))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def make_pdf(path: Path, image: bytes, *, rect=(50, 100, 550, 700)):
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 50), "Cifereca PDF vector text")
    page.draw_rect(pymupdf.Rect(45, 90, 555, 710), width=2)
    page.insert_image(pymupdf.Rect(*rect), stream=image)
    doc.save(path)
    doc.close()


def test_pdf_preserves_text_and_page_geometry_while_optimizing_raster():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src = root / "book.pdf"
        dst = root / "out.pdf"
        make_pdf(src, jpeg())
        report = optimize_pdf(src, dst, profile=builtin_profile("kobo-clara-bw"))
        assert report.pages == 1
        assert report.images_seen >= 1
        assert report.images_optimized >= 1

        before = pymupdf.open(src)
        after = pymupdf.open(dst)
        try:
            assert before.page_count == after.page_count == 1
            assert before[0].get_text("text") == after[0].get_text("text")
            assert tuple(before[0].rect) == tuple(after[0].rect)
            rows = after[0].get_images(full=True)
            assert rows
            extracted = after.extract_image(rows[0][0])
            image = Image.open(io.BytesIO(extracted["image"]))
            assert image.width <= 1072
            assert image.height <= 1448
        finally:
            before.close()
            after.close()


def test_pdf_transparent_image_is_kept_conservatively():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src = root / "book.pdf"
        dst = root / "out.pdf"
        make_pdf(src, rgba_png())
        report = optimize_pdf(src, dst, profile=builtin_profile("xteink-x3"))
        assert report.images_seen >= 1
        assert report.images_kept >= 1
        assert any("masque" in note or "transparence" in note for note in report.image_notes)


def test_pdf_inspection_is_non_destructive_and_has_real_estimate():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "book.pdf"
        make_pdf(src, jpeg((1200, 1600)))
        before = src.read_bytes()
        info = inspect_pdf(src, profile=builtin_profile("xteink-x3"))
        assert info.pages == 1
        assert info.images_seen >= 1
        assert info.candidate_images >= 1
        assert 0 < info.estimated_size_bytes <= info.source_bytes
        assert src.read_bytes() == before


def test_unsigned_pdf_sigflags_minus_one_is_accepted():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "book.pdf"
        make_pdf(src, jpeg((200, 300)))
        doc = pymupdf.open(src)
        try:
            assert doc.get_sigflags() <= 0
        finally:
            doc.close()
        info = inspect_pdf(src, profile=builtin_profile("generic-eink"))
        assert info.pages == 1


def test_pdf_inspection_honours_preexisting_cancellation():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "book.pdf"
        make_pdf(src, jpeg((200, 300)))
        cancelled = threading.Event()
        cancelled.set()
        with pytest.raises(CancelledError):
            inspect_pdf(
                src,
                profile=builtin_profile("generic-eink"),
                cancel_event=cancelled,
            )


def test_pdf_inventory_exposes_bpc_and_colorspace_name_for_stencil_policy():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "book.pdf"
        make_pdf(src, jpeg((200, 300)))
        doc = pymupdf.open(src)
        try:
            inventory = _image_inventory(doc)
            assert inventory
            item = next(iter(inventory.values()))
            assert "bpc" in item and "colorspace_name" in item
            assert item["bpc"] > 1
            assert isinstance(item["colorspace_name"], str)
            assert item["colorspace_name"]
        finally:
            doc.close()



def test_pdf_optimizer_never_retains_all_extracted_images(monkeypatch, tmp_path):
    """Real PyMuPDF, 6 distinct image streams, no accumulated raster blobs."""
    import weakref
    from cifereca_epub_optimizer import pdf_engine

    source, target = tmp_path / "many.pdf", tmp_path / "optimized.pdf"
    doc = pymupdf.open()
    for index in range(6):
        page = doc.new_page(width=400, height=500)
        image = Image.new("RGB", (160, 220), (index * 31, 80, 120))
        out = io.BytesIO()
        image.save(out, format="JPEG")
        page.insert_image(pymupdf.Rect(20, 20, 300, 380), stream=out.getvalue())
    doc.save(source)
    doc.close()
    original = source.read_bytes()

    real_open = pdf_engine._open_pdf
    wrapped = [False]
    live_refs, held_at_next_extraction = [], []

    class Tracked(dict):
        pass

    class TracingDoc:
        def __init__(self, underlying):
            self.underlying = underlying

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.underlying.close()

        def __getattr__(self, name):
            return getattr(self.underlying, name)

        def __getitem__(self, key):
            return self.underlying[key]

        def extract_image(self, xref):
            held_at_next_extraction.append(
                sum(ref() is not None for ref in live_refs)
            )
            payload = Tracked(self.underlying.extract_image(xref))
            live_refs.append(weakref.ref(payload))
            return payload

    def inspect_once(path, *, max_pages):
        opened = real_open(path, max_pages=max_pages)
        if Path(path) == source and not wrapped[0]:
            wrapped[0] = True
            return TracingDoc(opened)
        return opened

    monkeypatch.setattr(pdf_engine, "_open_pdf", inspect_once)
    progresses = []
    report = optimize_pdf(
        source, target, profile=builtin_profile("generic-eink"),
        progress=lambda percentage, _label: progresses.append(percentage),
    )

    assert report.pages == 6
    assert report.images_seen == 6
    assert report.images_optimized + report.images_kept == 6
    assert not report.image_errors
    assert len(held_at_next_extraction) == 6
    # Historical eager extraction retained 1..5 previous dictionaries,
    # each keeping its own image bytes; streamed mode retains at most one.
    assert max(held_at_next_extraction) <= 1
    assert source.read_bytes() == original
    assert target.is_file()
    assert progresses == sorted(progresses)
    assert progresses[-1] == 100
