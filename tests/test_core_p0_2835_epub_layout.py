"""#2835: explicit package layout is descriptive, never a Kobo engine choice."""
from io import BytesIO
import zipfile

import pytest

from cifereca.artifact_inspection import inspect_epub_declared_layout


OCF = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF = "http://www.idpf.org/2007/opf"
FIXED = '<meta property="rendition:layout">pre-paginated</meta>'
FLOW = '<meta property="rendition:layout">reflowable</meta>'


def _book(metadata=FIXED, *, spine='<itemref idref="p"/>',
          package_attrs='version="3.0"', roots=None, encrypted=False,
          mime=b"application/epub+zip"):
    if roots is None:
        roots = '<rootfile full-path="OPS/content.opf"/>'
    result = BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        archive.writestr("mimetype", mime)
        archive.writestr("META-INF/container.xml",
                         f'<container xmlns="{OCF}"><rootfiles>{roots}</rootfiles></container>')
        archive.writestr("OPS/content.opf",
                         f'<package xmlns="{OPF}" {package_attrs}>'
                         f'<metadata>{metadata}</metadata>'
                         '<manifest><item id="p" href="page.xhtml" media-type="application/xhtml+xml"/></manifest>'
                         f'<spine>{spine}</spine></package>')
        # Reading a page is not necessary for an explicit layout declaration.
        archive.writestr("OPS/page.xhtml", b"not a parsed XHTML page")
        if encrypted:
            archive.writestr("META-INF/encryption.xml", b"not inspected")
    result.seek(3)
    return result


@pytest.mark.parametrize("metadata,spine,expected", [
    (FIXED, '<itemref idref="p"/>', "fixed"),
    (FLOW, '<itemref idref="p"/>', "reflowable"),
    (FIXED, '<itemref idref="p" properties="rendition:layout-pre-paginated"/>', "fixed"),
    (FIXED, '<itemref idref="p" properties="rendition:layout-reflowable"/>', "unknown"),
    (FLOW, '<itemref idref="p" properties="rendition:layout-pre-paginated"/>', "unknown"),
    (FIXED, '<itemref idref="p" properties="rendition:layout-mystery"/>', "unknown"),
    (FIXED, '<itemref idref="p" properties="rendition:layout-pre-paginated rendition:layout-reflowable"/>', "unknown"),
    (FIXED, '', "unknown"),
    (FIXED + FLOW, '<itemref idref="p"/>', "unknown"),
    (FIXED + FIXED, '<itemref idref="p"/>', "unknown"),
    ('<meta property="rendition:layout" refines="#p">pre-paginated</meta>', '<itemref idref="p"/>', "unknown"),
    ('<meta xmlns="urn:other" property="rendition:layout">pre-paginated</meta>', '<itemref idref="p"/>', "unknown"),
    ('<meta property="rendition:layout">fixed</meta>', '<itemref idref="p"/>', "unknown"),
    ('', '<itemref idref="p" properties="rendition:layout-pre-paginated"/>', "unknown"),
])
def test_explicit_layout_requires_unambiguous_package_and_spine(metadata, spine, expected):
    stream = _book(metadata, spine=spine)
    original = stream.getvalue()
    assert inspect_epub_declared_layout(stream) == expected
    assert stream.tell() == 3
    assert stream.getvalue() == original
    assert not stream.closed


@pytest.mark.parametrize("kwargs", [
    {"encrypted": True},
    {"mime": b"application/zip"},
    {"package_attrs": 'version="2.0"'},
    {"package_attrs": 'version="3.0" prefix="rendition: urn:foreign#"'},
    {"package_attrs": 'version="3.0" prefix="r: http://www.idpf.org/vocab/rendition/#"'},
    {"roots": '<rootfile full-path="OPS/content.opf"/><rootfile full-path="OPS/other.opf"/>'},
    {"roots": '<rootfile full-path="../OPS/content.opf"/>'},
])
def test_inaccessible_or_ambiguous_epub_layout_stays_unknown(kwargs):
    assert inspect_epub_declared_layout(_book(**kwargs)) == "unknown"


def test_non_epub_layout_is_unknown():
    assert inspect_epub_declared_layout(BytesIO(b"not a zip")) == "unknown"
