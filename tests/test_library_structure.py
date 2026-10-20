import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cifereca.core.library_structure import inspect_stream_structure


def book(*, opf=b'<package/>', mime=b'application/epub+zip', content=b'chapter'):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('mimetype', mime)
        archive.writestr('META-INF/container.xml', '<container><rootfile full-path="book.opf"/></container>')
        archive.writestr('book.opf', opf)
        archive.writestr('chapter.xhtml', content)
    stream.seek(0)
    return stream


class StructureTests(unittest.TestCase):
    def test_strict_core_validation_and_crc_on_same_stream(self):
        stream = book()
        before = stream.getvalue()
        self.assertEqual(inspect_stream_structure(stream,'epub'), 'epub_structure_crc_ok')
        self.assertFalse(stream.closed)
        self.assertEqual(stream.getvalue(), before)

    def test_missing_or_bad_mimetype_and_non_package_rejected(self):
        self.assertEqual(inspect_stream_structure(book(mime=b'zip'),'epub'), 'epub_structure_invalid')
        self.assertEqual(inspect_stream_structure(book(opf=b'<metadata/>'),'epub'), 'epub_structure_invalid')

    def test_corrupt_unrelated_member_is_not_hidden_by_readable_opf(self):
        stream = book(content=b'PRIVATE_CHAPTER_MARKER')
        data = stream.getvalue().replace(b'PRIVATE_CHAPTER_MARKER', b'CORRUPT_CHAPTER_MARKER')
        self.assertEqual(inspect_stream_structure(io.BytesIO(data),'epub'), 'structure_invalid')

    def test_dtd_and_utf16_entity_never_reach_legacy_core_parser(self):
        for encoding in ('utf-8','utf-16'):
            stream = book(opf='<!DOCTYPE x [<!ENTITY x "PRIVATE">]><package>&x;</package>'.encode(encoding))
            self.assertIn(inspect_stream_structure(stream,'epub'), {'structure_invalid', 'epub_structure_invalid'})

    def test_global_bytes_cancel_time_and_unsafe_ratio_are_closed(self):
        self.assertEqual(inspect_stream_structure(book(),'epub',max_bytes=1), 'structure_byte_or_entry_budget')
        self.assertEqual(inspect_stream_structure(book(),'epub',cancelled=lambda:True), 'structure_cancelled')
        with patch('cifereca.core.library_structure.time.monotonic', side_effect=[0,100]):
            self.assertEqual(inspect_stream_structure(book(),'epub'), 'structure_byte_or_time_budget')
        stream = io.BytesIO()
        with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('bomb',b'0'*2_000_000)
        self.assertEqual(inspect_stream_structure(stream,'epub'), 'structure_unsafe_member')

    def test_pdf_check_explicitly_minimal_not_conformance_or_drm(self):
        self.assertEqual(inspect_stream_structure(io.BytesIO(b'%PDF-1.4\n/Encrypt\n%%EOF'),'pdf'), 'pdf_structure_minimal')
        self.assertEqual(inspect_stream_structure(io.BytesIO(b'%PDF-1.4'),'pdf'), 'pdf_structure_invalid')


if __name__ == '__main__':
    unittest.main()
