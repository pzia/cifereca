from __future__ import annotations

import io
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image
import pytest

from cifereca_epub_optimizer.engine import (
    DrmProtectedError,
    EpubOptimizationError,
    _canonical_entry_name,
    _effective_target_box,
    _unsafe_zip_entry,
    _validate_archive_structure,
    analyze_epub,
    optimize_epub,
)
from cifereca_epub_optimizer.builtin_profiles import builtin_profile


MIMETYPE = b"application/epub+zip"
CONTAINER = b'''<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'''
OPF = b'''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata/><manifest><item id="p1" href="page.xhtml" media-type="application/xhtml+xml"/><item id="img" href="images/page.jpg" media-type="image/jpeg"/></manifest><spine><itemref idref="p1"/></spine></package>'''
XHTML = b'''<html xmlns="http://www.w3.org/1999/xhtml"><body><img src="images/page.jpg"/></body></html>'''


def image_bytes(fmt: str, size=(2000, 3000), mode="RGB") -> bytes:
    image = Image.new(mode, size)
    # Add a gradient-like pattern that compresses realistically.
    pixels = image.load()
    for y in range(0, size[1], 16):
        value = int(255 * y / max(1, size[1] - 1))
        for yy in range(y, min(y + 16, size[1])):
            for x in range(size[0]):
                if mode == "RGB":
                    pixels[x, yy] = (value, 255 - value, (x + y) % 256)
                else:
                    pixels[x, yy] = value
    out = io.BytesIO()
    image.save(out, format=fmt, quality=95)
    return out.getvalue()


def make_epub(path: Path, image_data: bytes, *, image_name="OEBPS/images/page.jpg", encryption: bytes | None = None):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", MIMETYPE, compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER)
        if encryption is not None:
            zf.writestr("META-INF/encryption.xml", encryption)
        zf.writestr("OEBPS/content.opf", OPF)
        zf.writestr("OEBPS/page.xhtml", XHTML)
        zf.writestr(image_name, image_data)


class EngineTests(unittest.TestCase):
    def test_kobo_clara_bw_resizes_and_preserves_non_image_content(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "book.epub"
            dst = Path(td) / "book [Clara BW].epub"
            make_epub(src, image_bytes("JPEG"))
            report = optimize_epub(src, dst, profile=builtin_profile("kobo-clara-bw"))
            self.assertEqual(report.images_seen, 1)
            self.assertEqual(report.images_optimized, 1)
            with zipfile.ZipFile(src) as before, zipfile.ZipFile(dst) as after:
                self.assertEqual(before.read("OEBPS/content.opf"), after.read("OEBPS/content.opf"))
                self.assertEqual(before.read("OEBPS/page.xhtml"), after.read("OEBPS/page.xhtml"))
                img = Image.open(io.BytesIO(after.read("OEBPS/images/page.jpg")))
                self.assertLessEqual(img.width, 1072)
                self.assertLessEqual(img.height, 1448)
                self.assertEqual(img.mode, "L")

    def test_x3_png_is_four_level_grayscale(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "book.epub"
            dst = Path(td) / "out.epub"
            png = image_bytes("PNG", size=(700, 900), mode="L")
            # Build a matching EPUB entry; manifest content is irrelevant to image processing.
            make_epub(src, png, image_name="OEBPS/images/page.png")
            optimize_epub(src, dst, profile=builtin_profile("xteink-x3"))
            with zipfile.ZipFile(dst) as zf:
                img = Image.open(io.BytesIO(zf.read("OEBPS/images/page.png"))).convert("L")
                self.assertLessEqual(img.width, 528)
                self.assertLessEqual(img.height, 792)
                values = set(img.getdata())
                self.assertTrue(values.issubset({0, 85, 170, 255}), values)

    def test_mimetype_is_first_and_stored(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "book.epub"
            dst = Path(td) / "out.epub"
            make_epub(src, image_bytes("JPEG", size=(100, 100)))
            optimize_epub(src, dst, profile=builtin_profile("generic-eink"))
            with zipfile.ZipFile(dst) as zf:
                first = zf.infolist()[0]
                self.assertEqual(first.filename, "mimetype")
                self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(zf.read("mimetype"), MIMETYPE)

    def test_adept_encryption_is_refused(self):
        encryption = b'''<?xml version="1.0"?><encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData><enc:EncryptionMethod Algorithm="http://ns.adobe.com/adept/xmlenc#aes128-cbc"/><enc:CipherData><enc:CipherReference URI="OEBPS/images/page.jpg"/></enc:CipherData></enc:EncryptedData></encryption>'''
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "book.epub"
            dst = Path(td) / "out.epub"
            make_epub(src, image_bytes("JPEG", size=(100, 100)), encryption=encryption)
            with self.assertRaises(DrmProtectedError):
                optimize_epub(src, dst, profile=builtin_profile("xteink-x3"))
            self.assertFalse(dst.exists())


    def test_animated_image_is_preserved_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "book.epub"
            dst = Path(td) / "out.epub"
            frames = [Image.new("RGB", (64, 64), value) for value in ("white", "black")]
            out = io.BytesIO()
            frames[0].save(out, format="WEBP", save_all=True, append_images=frames[1:], duration=100, loop=0)
            animated = out.getvalue()
            make_epub(src, animated, image_name="OEBPS/images/page.webp")
            report = optimize_epub(src, dst, profile=builtin_profile("generic-eink"))
            with zipfile.ZipFile(dst) as zf:
                self.assertEqual(zf.read("OEBPS/images/page.webp"), animated)
            self.assertEqual(report.images_kept, 1)
            self.assertTrue(any("animation" in note for note in report.image_notes))

    def test_transparent_png_is_preserved_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "book.epub"
            dst = Path(td) / "out.epub"
            image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
            out = io.BytesIO()
            image.save(out, format="PNG")
            transparent = out.getvalue()
            make_epub(src, transparent, image_name="OEBPS/images/page.png")
            report = optimize_epub(src, dst, profile=builtin_profile("xteink-x3"))
            with zipfile.ZipFile(dst) as zf:
                self.assertEqual(zf.read("OEBPS/images/page.png"), transparent)
            self.assertTrue(any("transparence" in note for note in report.image_notes))

    def test_pixel_limit_keeps_image_without_decoding_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "book.epub"
            dst = Path(td) / "out.epub"
            payload = image_bytes("JPEG", size=(200, 200))
            make_epub(src, payload)
            report = optimize_epub(
                src, dst, profile=builtin_profile("xteink-x3"), max_image_pixels=10_000
            )
            with zipfile.ZipFile(dst) as zf:
                self.assertEqual(zf.read("OEBPS/images/page.jpg"), payload)
            self.assertTrue(any("limite sécurité" in note for note in report.image_notes))

    def test_unknown_zoom_ignores_layout_hint_below_screen_resolution(self):
        profile = builtin_profile("xteink-x3")
        self.assertIsNone(profile.max_zoom_factor)
        self.assertEqual(_effective_target_box(profile, (2000, 3000), (200, 300)), (528, 792))

    def test_declared_zoom_makes_layout_hint_actionable(self):
        from dataclasses import replace
        profile = replace(builtin_profile("generic-eink"), max_zoom_factor=2.0)
        self.assertEqual(_effective_target_box(profile, (2000, 3000), (300, 400)), (600, 800))

    def test_duplicate_zip_paths_are_refused(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "book.epub"
            dst = Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w") as zf:
                zf.writestr("mimetype", MIMETYPE, compress_type=zipfile.ZIP_STORED)
                zf.writestr("META-INF/container.xml", CONTAINER)
                zf.writestr("OEBPS/content.opf", OPF)
                zf.writestr("OEBPS/page.xhtml", XHTML)
                zf.writestr("OEBPS/images/page.jpg", image_bytes("JPEG", size=(64, 64)))
                zf.writestr("oebps/images/page.jpg", image_bytes("JPEG", size=(64, 64)))
            from cifereca_epub_optimizer.engine import EpubOptimizationError
            with self.assertRaises(EpubOptimizationError):
                optimize_epub(src, dst, profile=builtin_profile("generic-eink"))


    def test_analysis_estimates_output_and_reports_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "book.epub"
            make_epub(src, image_bytes("JPEG", size=(2000, 3000)))
            analysis = analyze_epub(src, profile=builtin_profile("xteink-x3"))
            self.assertEqual(analysis.images_seen, 1)
            self.assertEqual(analysis.images_candidates, 1)
            self.assertEqual(analysis.images_semantic_preserved, 0)
            self.assertGreater(analysis.estimated_size_bytes, 0)
            self.assertLessEqual(analysis.estimated_size_bytes, src.stat().st_size)

if __name__ == "__main__":
    unittest.main()



def test_noncanonical_zip_aliases_are_rejected():
    for name in ("OEBPS//page.xhtml", "OEBPS/./page.xhtml", "./mimetype"):
        info = zipfile.ZipInfo(name)
        assert _canonical_entry_name(info) == ""

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "book.epub"
        with zipfile.ZipFile(src, "w") as zf:
            zf.writestr("mimetype", MIMETYPE, compress_type=zipfile.ZIP_STORED)
            zf.writestr("OEBPS//page.xhtml", XHTML)
        with pytest.raises(EpubOptimizationError, match="non canonique"):
            analyze_epub(src, profile=builtin_profile("generic-eink"))


def test_zip_symlink_and_encrypted_entries_are_rejected_explicitly():
    symlink = zipfile.ZipInfo("OEBPS/link")
    symlink.create_system = 3
    symlink.external_attr = stat.S_IFLNK << 16
    assert _unsafe_zip_entry(symlink) == "lien symbolique ZIP"

    encrypted = zipfile.ZipInfo("OEBPS/page.xhtml")
    encrypted.flag_bits |= 0x1
    assert _unsafe_zip_entry(encrypted) == "entrée ZIP chiffrée"


def test_total_uncompressed_limit_is_checked_before_reading_mimetype():
    mimetype = zipfile.ZipInfo("mimetype")
    mimetype.file_size = 2 * 1024 * 1024

    class Archive:
        def read(self, _info):
            raise AssertionError("mimetype must not be read before global size validation")

    with pytest.raises(EpubOptimizationError, match="décompressé trop volumineux"):
        _validate_archive_structure(
            Archive(),
            [mimetype],
            max_uncompressed_mb=1,
        )


def test_oversized_mimetype_entry_is_rejected_before_payload_read():
    mimetype = zipfile.ZipInfo("mimetype")
    mimetype.file_size = 129

    class Archive:
        def read(self, _info):
            raise AssertionError("oversized mimetype must not be read")

    with pytest.raises(EpubOptimizationError, match="mimetype EPUB invalide"):
        _validate_archive_structure(
            Archive(),
            [mimetype],
            max_uncompressed_mb=1,
        )


def test_valid_archive_structure_still_accepts_normal_epub():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "book.epub"
        make_epub(src, image_bytes("JPEG", size=(64, 64)))
        with zipfile.ZipFile(src, "r") as archive:
            name = _validate_archive_structure(
                archive,
                archive.infolist(),
                max_uncompressed_mb=64,
            )
        assert name == "mimetype"



def test_mimetype_must_be_exact_first_stored_and_without_extra_field():
    profile = builtin_profile("generic-eink")
    cases = []

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        misplaced = root / "misplaced.epub"
        with zipfile.ZipFile(misplaced, "w") as zf:
            zf.writestr("META-INF/container.xml", CONTAINER)
            zf.writestr("mimetype", MIMETYPE, compress_type=zipfile.ZIP_STORED)
        cases.append(misplaced)

        compressed = root / "compressed.epub"
        with zipfile.ZipFile(compressed, "w") as zf:
            zf.writestr("mimetype", MIMETYPE, compress_type=zipfile.ZIP_DEFLATED)
        cases.append(compressed)

        wrong_case = root / "wrong-case.epub"
        with zipfile.ZipFile(wrong_case, "w") as zf:
            zf.writestr("MIMETYPE", MIMETYPE, compress_type=zipfile.ZIP_STORED)
        cases.append(wrong_case)

        whitespace = root / "whitespace.epub"
        with zipfile.ZipFile(whitespace, "w") as zf:
            zf.writestr("mimetype", b"application/epub+zip\n", compress_type=zipfile.ZIP_STORED)
        cases.append(whitespace)

        extra = root / "extra.epub"
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        info.extra = b"\x01\x00\x00\x00"
        with zipfile.ZipFile(extra, "w") as zf:
            zf.writestr(info, MIMETYPE)
        cases.append(extra)

        for path in cases:
            with pytest.raises(EpubOptimizationError):
                analyze_epub(path, profile=profile)


def test_ocf_rejects_non_stored_non_deflate_compression():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "book.epub"
        with zipfile.ZipFile(src, "w") as zf:
            zf.writestr("mimetype", MIMETYPE, compress_type=zipfile.ZIP_STORED)
            zf.writestr(
                "OEBPS/page.xhtml",
                XHTML,
                compress_type=zipfile.ZIP_BZIP2,
            )
        with pytest.raises(EpubOptimizationError, match="compression OCF"):
            analyze_epub(src, profile=builtin_profile("generic-eink"))


def test_exact_mimetype_payload_is_not_trimmed_or_repaired_silently():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "book.epub"
        dst = Path(td) / "out.epub"
        with zipfile.ZipFile(src, "w") as zf:
            zf.writestr(
                "mimetype",
                b" application/epub+zip",
                compress_type=zipfile.ZIP_STORED,
            )
        with pytest.raises(EpubOptimizationError, match="mimetype EPUB invalide"):
            optimize_epub(src, dst, profile=builtin_profile("generic-eink"))
        assert not dst.exists()
