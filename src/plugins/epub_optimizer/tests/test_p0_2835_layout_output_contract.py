"""#2835: generic optimization preserves layout declarations, not Kobo readiness."""
from hashlib import sha256
from io import BytesIO
from zipfile import ZIP_STORED, ZipFile

import pytest
from PIL import Image

from cifereca_epub_optimizer.builtin_profiles import builtin_profile
from cifereca_epub_optimizer.engine import EpubOptimizationError, optimize_epub
from cifereca_epub_optimizer.profiles import default_output_path


def _document(path, layout):
    declaration = (f'<meta property="rendition:layout">{layout}</meta>'
                   if layout else '')
    opf = (
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata>' + declaration + '</metadata>'
        '<manifest><item id="page" href="page.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="image" href="image.jpg" media-type="image/jpeg"/></manifest>'
        '<spine page-progression-direction="rtl"><itemref idref="page"/></spine></package>'
    ).encode()
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        '<meta name="viewport" content="width=1200,height=1600"/>'
        '</head><body><img src="image.jpg"/></body></html>'
    ).encode()
    image = BytesIO()
    Image.new("RGB", (1200, 1600), "gray").save(image, format="JPEG")
    with ZipFile(path, "w") as book:
        book.writestr("mimetype", b"application/epub+zip", compress_type=ZIP_STORED)
        book.writestr("META-INF/container.xml",
                      '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                      '<rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        book.writestr("OPS/book.opf", opf)
        book.writestr("OPS/page.xhtml", xhtml)
        book.writestr("OPS/image.jpg", image.getvalue())
    return opf, xhtml


@pytest.mark.parametrize("suffix", [".epub", ".kepub.epub", ".fxl.kepub.epub"])
@pytest.mark.parametrize("layout", ["pre-paginated", "reflowable", None])
def test_generic_optimizer_does_not_change_layout_or_claim_kepub(tmp_path, suffix, layout):
    source = tmp_path / ("Manga" + suffix)
    opf, xhtml = _document(source, layout)
    original = source.read_bytes()
    profile = builtin_profile("kobo-clara-bw")
    target = default_output_path(source, profile)
    report = optimize_epub(source, target, profile=profile)
    assert target.name == "Manga [Clara BW].epub"
    assert source.read_bytes() == original
    assert report.output_sha256 == sha256(target.read_bytes()).hexdigest()
    with ZipFile(target) as archive:
        assert archive.read("OPS/book.opf") == opf
        assert archive.read("OPS/page.xhtml") == xhtml


@pytest.mark.parametrize("suffix", [".kepub.epub", ".fxl.kepub.epub", ".FXL.KePub.EPUB"])
def test_compound_target_refused_without_creating_directory_or_overwriting(tmp_path, suffix):
    source = tmp_path / "Manga.epub"
    _document(source, "pre-paginated")
    original = source.read_bytes()
    target = tmp_path / "unused" / ("Output" + suffix)
    profile = builtin_profile("kobo-clara-bw")
    with pytest.raises(EpubOptimizationError, match="KEPUB"):
        optimize_epub(source, target, profile=profile)
    assert not target.parent.exists()
    target.parent.mkdir()
    target.write_bytes(b"unrelated")
    with pytest.raises(EpubOptimizationError, match="KEPUB"):
        optimize_epub(source, target, profile=profile)
    assert target.read_bytes() == b"unrelated"
    assert source.read_bytes() == original


def test_resolved_target_alias_cannot_advertise_kepub(tmp_path):
    source = tmp_path / "Manga.epub"
    _document(source, "reflowable")
    protected = tmp_path / "Existing.kepub.epub"
    protected.write_bytes(b"unrelated")
    alias = tmp_path / "output.epub"
    alias.symlink_to(protected)
    with pytest.raises(EpubOptimizationError, match="KEPUB"):
        optimize_epub(source, alias, profile=builtin_profile("kobo-clara-bw"))
    assert alias.is_symlink()
    assert protected.read_bytes() == b"unrelated"
