"""#2835: actual optimizer preserves declared document geometry, not rendered output."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace as NS
from zipfile import ZIP_STORED, ZipFile

from PIL import Image
import pytest

from cifereca_epub_optimizer.builtin_profiles import builtin_profile
from cifereca_epub_optimizer.engine import optimize_epub
from cifereca_epub_optimizer.profiles import default_output_path, EPUB_RENDERING_WARNING
from cifereca_epub_optimizer.ui import OptimizerPage


def _fixture(source, layout):
    image = BytesIO()
    Image.new("RGB", (1600, 2400), "#879eaf").save(image, format="JPEG", quality=95)
    manifest = (
        '<item id="page" href="page.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="img" href="page.jpg" media-type="image/jpeg"/>'
        '<item id="css" href="style.css" media-type="text/css"/>'
    )
    declaration = (
        '<meta property="rendition:layout">' + layout + '</meta>'
        '<meta property="rendition:spread">none</meta>'
    )
    resources = {
        "META-INF/container.xml": (
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            '<rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/>'
            '</rootfiles></container>'
        ).encode(),
        "OPS/book.opf": (
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata>' + declaration + '</metadata><manifest>' + manifest + '</manifest>'
            '<spine page-progression-direction="rtl"><itemref idref="page"/></spine></package>'
        ).encode(),
        "OPS/page.xhtml": (
            '<html xmlns="http://www.w3.org/1999/xhtml" dir="rtl"><head>'
            '<meta name="viewport" content="width=1600,height=2400"/>'
            '<link rel="stylesheet" href="style.css"/></head>'
            '<body><img src="page.jpg" width="1600" height="2400"/></body></html>'
        ).encode(),
        "OPS/style.css": b"html,body{margin:0;width:1600px;height:2400px} img{width:100%;height:100%}",
        "META-INF/com.kobobooks.display-options.xml": (
            b'<display_options><platform name="*"><option name="fixed-layout">true</option>'
            b'</platform></display_options>' if layout == "pre-paginated" else
            b'<display_options><platform name="*"><option name="fixed-layout">false</option>'
            b'</platform></display_options>'
        ),
    }
    with ZipFile(source, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=ZIP_STORED)
        for name, payload in resources.items():
            archive.writestr(name, payload)
        archive.writestr("OPS/page.jpg", image.getvalue())
    return resources, image.getvalue()


@pytest.mark.parametrize("suffix", [".epub", ".kepub.epub", ".fxl.kepub.epub"])
@pytest.mark.parametrize("layout", ["pre-paginated", "reflowable"])
def test_actual_optimization_keeps_document_geometry_and_generic_output(tmp_path, suffix, layout):
    source = tmp_path / ("Manga" + suffix)
    resources, image = _fixture(source, layout)
    before = source.read_bytes()
    profile = builtin_profile("kobo-clara-bw")
    target = default_output_path(source, profile)
    assert target.name == "Manga [Clara BW].epub"
    report = optimize_epub(source, target, profile=profile)
    assert source.read_bytes() == before
    assert report.source_sha256 == sha256(before).hexdigest()
    assert report.output_sha256 == sha256(target.read_bytes()).hexdigest()
    assert report.source_sha256 != report.output_sha256
    assert report.images_optimized == 1
    with ZipFile(target) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {"mimetype", "OPS/page.jpg", *resources}
        for name, payload in resources.items():
            assert archive.read(name) == payload, name
        optimized = archive.read("OPS/page.jpg")
        assert optimized != image
        with Image.open(BytesIO(optimized)) as raster:
            assert raster.width <= profile.width
            assert raster.height <= profile.height
    # Byte preservation of OPF/XHTML/CSS does not certify physical rendering
    # or KEPUB spans/semantics. The output is still generic EPUB.


class _Widget:
    def set_text(self, value):
        self.text = value
    def set_fraction(self, _value):
        pass


def _page():
    page = OptimizerPage.__new__(OptimizerPage)
    page._set_busy = lambda _: None
    page.status = _Widget()
    page.progress = _Widget()
    page.host = NS(toast=lambda _: None)
    page.action_host = None
    return page


@pytest.mark.parametrize("fmt", ["epub", "pdf"])
def test_export_and_library_success_do_not_certify_epub_rendition(fmt):
    page = _page()
    report = NS(source_bytes=100, output_bytes=80, output_path="book." + fmt,
                images_optimized=1, images_seen=1, reduction_percent=20.0,
                image_errors=[], image_notes=[])
    page._success_ui(report)
    assert (EPUB_RENDERING_WARNING in page.status.text) == (fmt == "epub")
    page._artifact_success_ui(NS(filename="book." + fmt, format=NS(format=fmt)))
    assert (EPUB_RENDERING_WARNING in page.status.text) == (fmt == "epub")
